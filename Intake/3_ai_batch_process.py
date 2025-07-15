import os
import csv
import json
import time
import uuid
import sys
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from pipeline_logging import setup_logging

# --- GCP & Vertex AI Libraries ---
import vertexai
from google.cloud import storage, aiplatform
from vertexai.generative_models import GenerativeModel
from google.cloud.aiplatform_v1.types import job_state as job_state_v1

# --- Configuration ---
# Load the .env file from the project root
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- Constants ---
# GCS Configuration
GCS_BUCKET_NAME = "ctf-rag" # User-provided bucket name

# AI Model Configuration - Using specific model identifiers
REWRITE_MODEL_NAME = "gemini-2.5-pro"
SUMMARY_MODEL_NAME = "gemini-2.5-flash"
KEYWORD_MODEL_NAME = "gemini-2.5-flash"

# --- Main Batch Processing Logic ---

def execute_batch_job(
    job_name_prefix: str,
    model_name: str,
    input_gcs_uri: str,
    gcs_bucket: storage.Bucket,
    pipeline_run_id: str,
) -> str:
    """Creates, runs, and waits for a Vertex AI Batch Prediction job using aiplatform."""
    job_display_name = f"{job_name_prefix}-{pipeline_run_id}"
    output_gcs_uri = f"gs://{gcs_bucket.name}/3_ai_batch_process/{pipeline_run_id}/output/{job_display_name}/"

    logging.info(f"Starting batch job: {job_display_name}")
    logging.debug(f"Input: {input_gcs_uri}")
    logging.debug(f"Output: {output_gcs_uri}")

    # The Batch Prediction API requires the full publisher model path.
    publisher_model_name = f"publishers/google/models/{model_name}"
    logging.debug(f"Using publisher model: {publisher_model_name}")

    # Use the more stable BatchPredictionJob.create()
    batch_job = aiplatform.BatchPredictionJob.create(
        job_display_name=job_display_name,
        model_name=publisher_model_name,
        instances_format="jsonl",
        predictions_format="jsonl",
        gcs_source=input_gcs_uri,
        gcs_destination_prefix=output_gcs_uri,
    )
    
    logging.info(f"Batch job created: {batch_job.name}. Waiting for completion...")
    
    # This is a synchronous call that waits for the job to finish.
    batch_job.wait()
    
    # Check the final state
    if batch_job.state == job_state_v1.JobState.JOB_STATE_SUCCEEDED:
        logging.info(f"✅ Job {job_display_name} succeeded.")
        return output_gcs_uri
    else:
        logging.error(f"❌ Job {job_display_name} failed with state: {batch_job.state}")
        logging.error(f"   Error: {batch_job.error}")
        raise RuntimeError(f"Job {job_display_name} failed.")

def download_and_parse_results(
    gcs_output_uri: str,
    gcs_bucket: storage.Bucket,
    raw_ai_processed_dir: str,
    job_name_prefix: str
) -> dict:
    """Downloads and parses batch prediction results from a BatchPredictionJob."""
    prefix = gcs_output_uri.replace(f"gs://{gcs_bucket.name}/", "")
    logging.info(f"Downloading results from GCS prefix: {prefix}")
    
    results = {}
    blobs = gcs_bucket.list_blobs(prefix=prefix)
    
    for blob in blobs:
        if "predictions" in blob.name and blob.name.endswith(".jsonl"):
            logging.debug(f"Parsing results file: {blob.name}")
            content = blob.download_as_string().decode("utf-8")

            # We will parse and save per-id results locally.

            for line in content.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # The custom ID we passed in the input is at the top level of the output.
                    item_id = data.get("ctftime_id")
                    if not item_id:
                        logging.warning(f"Could not find 'ctftime_id' in result line: {line}")
                        continue

                    # --- New: Save raw per-ID AI response for debugging ---
                    raw_id_dir = os.path.join(raw_ai_processed_dir, job_name_prefix)
                    id_filepath = os.path.join(raw_id_dir, f"{item_id}.json")
                    try:
                        with open(id_filepath, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=4)
                        logging.debug(f"Saved raw AI response for {item_id} to: {id_filepath}")
                    except (IOError, TypeError) as e:
                        logging.error(f"Failed to save raw AI response for {item_id}: {e}")
                    # --- End New ---

                    # The AI's output is nested inside the 'response' key.
                    response_data = data.get("response", {})
                    candidates = response_data.get("candidates", [])
                    if not candidates:
                        logging.warning(f"No candidates found for ID {item_id} in {blob.name}")
                        results[item_id] = "" # Assign empty string if no prediction
                        continue
                    
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        logging.warning(f"No parts found in candidate for ID {item_id} in {blob.name}")
                        results[item_id] = "" # Assign empty string if no prediction
                        continue
                        
                    prediction = parts[0].get("text", "")
                    results[item_id] = prediction
                except (json.JSONDecodeError, KeyError, IndexError) as e:
                    logging.warning(f"Could not parse line in {blob.name}: {e}")
                    logging.debug(f"   Line: {line}")
                    continue
    
    logging.info(f"Successfully parsed {len(results)} results.")
    return results

def prepare_and_upload_input(
    input_data: list, # Changed from dict to list of tasks
    prompt_template: str,
    gcs_bucket: storage.Bucket,
    job_name: str,
    pipeline_run_id: str,
    raw_requests_dir: str
) -> str:
    """
    Formats data into JSONL for BatchPredictionJob, uploads to GCS, 
    and returns the GCS URI.
    """
    jsonl_content = []
    for task_data in input_data:
        text_content = task_data["text_content"]
        ctftime_id = task_data["ctftime_id"]
        event_name = task_data["event_name"]
        task_name = task_data["task_name"]

        # Inject the context into the prompt template
        final_prompt = prompt_template.replace('$event_name', event_name).replace('$task_name', task_name)

        # Using a multi-part prompt is the most robust way to handle complex
        # content. We pass the template and the content as separate parts and
        # let the Vertex AI backend safely assemble them, avoiding all local
        # string manipulation bugs.
        template_parts = final_prompt.split('$writeup')
        
        # The API request structure. We also pass our own metadata (`ctftime_id`)
        # to be returned in the output, which helps with matching.
        instance = {
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": template_parts[0]},
                            {"text": text_content},
                            # Handle cases where $writeup is at the end of the prompt
                            {"text": template_parts[1] if len(template_parts) > 1 else ""}
                        ]
                    }
                ],
                "generation_config": {
                    "temperature": 0.2
                }
            },
            "ctftime_id": ctftime_id
        }
        
        # --- New: Save raw per-ID request locally for debugging ---
        raw_id_dir = os.path.join(raw_requests_dir, job_name)
        id_filepath = os.path.join(raw_id_dir, f"{ctftime_id}.json")
        try:
            with open(id_filepath, 'w', encoding='utf-8') as f:
                json.dump(instance, f, indent=4)
            logging.debug(f"Saved raw request for {ctftime_id} to: {id_filepath}")
        except (IOError, TypeError) as e:
            logging.error(f"Failed to save raw request for {ctftime_id}: {e}")
        # --- End New ---
        
        jsonl_content.append(json.dumps(instance))

    jsonl_string = '\n'.join(jsonl_content)

    # We no longer save the combined JSONL file locally for debugging.

    # Upload to GCS
    blob_name = f"3_ai_batch_process/{pipeline_run_id}/input/{job_name}.jsonl"
    blob = gcs_bucket.blob(blob_name)
    # Each JSON object must be on its own line.
    blob.upload_from_string(jsonl_string, content_type="application/jsonl")
    
    gcs_uri = f"gs://{gcs_bucket.name}/{blob_name}"
    logging.info(f"Uploaded batch input to: {gcs_uri}")
    return gcs_uri

def main():
    """
    Orchestrates the 3-stage batch AI processing pipeline based on a run manifest.
    1. Reads the manifest to find cleaned text.
    2. For each text, creates context-aware AI requests for all associated tasks.
    3. Executes batch jobs for rewriting, summarizing, and generating keywords.
    4. Merges all results and saves them locally.
    """
    if len(sys.argv) < 2:
        print("FATAL: Missing run_path argument. This script should be called from Main.py.")
        sys.exit(1)
    run_path = sys.argv[1]

    # Set up a run-specific logger
    setup_logging(run_path)
    
    # Define run-specific output directories
    ai_output_dir = os.path.join(run_path, "ai_processed")
    meta_dir = os.path.join(run_path, "output") # Used for loading metadata
    os.makedirs(ai_output_dir, exist_ok=True)

    # --- New: Create directories for raw data dumps ---
    raw_requests_dir = os.path.join(run_path, "raw_requests")
    raw_ai_processed_dir = os.path.join(run_path, "raw_ai_processed")
    for subdir_name in ["1_rewrite", "2_summarize", "3_keywords"]:
        os.makedirs(os.path.join(raw_requests_dir, subdir_name), exist_ok=True)
        os.makedirs(os.path.join(raw_ai_processed_dir, subdir_name), exist_ok=True)
    # --- End New ---

    # --- New: Load the manifest ---
    manifest_path = os.path.join(run_path, "run_manifest.json")
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"FATAL: Could not load or parse the run manifest at '{manifest_path}'. Error: {e}")
        return

    logging.info(">>> Starting Step 3: AI Batch Processing Pipeline <<<")

    # --- Initialize GCP Services ---
    try:
        project_id = os.getenv("GCP_PROJECT_ID")
        if not project_id:
            raise ValueError("GCP_PROJECT_ID environment variable not set.")
        
        # Initialize Vertex AI
        vertexai.init(project=project_id, location="us-central1")
        
        # Initialize GCS client
        storage_client = storage.Client(project=project_id)
        gcs_bucket = storage_client.bucket(GCS_BUCKET_NAME)
        
        # This will raise an exception if the bucket doesn't exist or isn't accessible
        gcs_bucket.reload() 

        logging.info(f"Successfully initialized Vertex AI and GCS client for project '{project_id}'.")
        logging.info(f"Using GCS Bucket: gs://{GCS_BUCKET_NAME}")
    
    except Exception as e:
        logging.critical(f"FATAL: Failed to initialize Google Cloud services. Error: {e}")
        sys.exit(1)

    # A unique ID for this pipeline execution to avoid GCS collisions
    pipeline_run_id = f"ctf-pipeline-{int(time.time())}"
    logging.info(f"Pipeline Run ID: {pipeline_run_id}")

    # --- New: Prepare AI requests from the manifest ---
    logging.info("Preparing AI requests based on the run manifest...")
    all_tasks_for_ai = []
    ctftime_id_to_original_text = {} # This will store the original text for final output
    
    urls_to_process = manifest.get("processed_urls", {})

    for url, data in urls_to_process.items():
        if data.get("status") != "cleaned":
            logging.debug(f"Skipping URL (status is not 'cleaned'): {url}")
            continue

        # --- New: Read both cleaned text and summary files ---
        primary_text_path = data.get("cleaned_text_path")
        summary_text_path = data.get("cleaned_summary_path")

        text_parts = []
        
        # Read the summary text if it exists
        if summary_text_path:
            full_summary_path = os.path.join(run_path, summary_text_path)
            try:
                with open(full_summary_path, 'r', encoding='utf-8') as f:
                    summary_content = f.read()
                text_parts.append("--- CTFTIME SUMMARY ---\n" + summary_content)
            except (IOError, FileNotFoundError) as e:
                logging.warning(f"Could not read summary text file {full_summary_path}: {e}")

        # Read the primary text if it exists
        if primary_text_path:
            full_primary_path = os.path.join(run_path, primary_text_path)
            try:
                with open(full_primary_path, 'r', encoding='utf-8') as f:
                    primary_content = f.read()
                text_parts.append("--- ORIGINAL WRITEUP ---\n" + primary_content)
            except (IOError, FileNotFoundError) as e:
                logging.warning(f"Could not read primary text file {full_primary_path}: {e}")
        
        # If we couldn't read any content, skip this entry
        if not text_parts:
            logging.warning(f"No readable content found for URL {url}. Skipping.")
            continue
        
        # Combine the parts into a single text block
        text_content = "\n\n".join(text_parts)
        # --- End New Reading Logic ---

        # For this text content, create a job for each associated task
        for task in data.get("tasks", []):
            task_info = {
                "ctftime_id": task["ctftime_id"],
                "event_name": task["event_name"],
                "task_name": task["task_name"],
                "text_content": text_content, # Use the combined content
            }
            all_tasks_for_ai.append(task_info)
            # Store the original text for this ID for the final assembly step
            ctftime_id_to_original_text[str(task["ctftime_id"])] = text_content
    
    if not all_tasks_for_ai:
        logging.info("No tasks found with 'cleaned' status to process. Exiting.")
        return

    logging.info(f"Prepared {len(all_tasks_for_ai)} total AI tasks from {len(urls_to_process)} source documents.")

    # --- STAGE 1: Rewrite ---
    logging.info("--- Stage 1: Rewriting ---")
    rewrite_prompt = get_rewrite_prompt()
    rewrite_input_uri = prepare_and_upload_input(
            input_data=all_tasks_for_ai,
            prompt_template=rewrite_prompt,
            gcs_bucket=gcs_bucket,
            job_name="1_rewrite",
            pipeline_run_id=pipeline_run_id,
            raw_requests_dir=raw_requests_dir,
        )
    rewrite_output_gcs_uri = execute_batch_job(
            job_name_prefix="1_rewrite",
            model_name=REWRITE_MODEL_NAME,
            input_gcs_uri=rewrite_input_uri,
            gcs_bucket=gcs_bucket,
            pipeline_run_id=pipeline_run_id,
        )
    rewritten_texts = download_and_parse_results(
            gcs_output_uri=rewrite_output_gcs_uri,
            gcs_bucket=gcs_bucket,
            raw_ai_processed_dir=raw_ai_processed_dir,
            job_name_prefix="1_rewrite"
        )

    if not rewritten_texts:
        logging.critical("Stage 1 produced no results. Halting pipeline.")
        return

    logging.info(f"✅ Stage 1 Complete. Successfully processed {len(rewritten_texts)} articles.")


    # --- Stages 2 & 3: Summarize and Generate Keywords (in parallel) ---
    logging.info("--- STAGES 2 & 3: Starting Summary and Keyword Jobs ---")

    # The next stages need to build inputs based on the REWRITTEN text.
    # We also need the original metadata (event name, task name) for context.
    summary_input_tasks = []
    keyword_input_tasks = []
    
    # We need a way to look up the original task metadata using the ctftime_id 
    # that comes back from the first job's results.
    id_to_task_meta = {str(task['ctftime_id']): task for task in all_tasks_for_ai}

    for ctftime_id, rewritten_content in rewritten_texts.items():
        # Find the original task metadata using the ID.
        # Ensure we use a string key for lookup, matching how the map was created.
        original_task = id_to_task_meta.get(str(ctftime_id))
        if not original_task:
            logging.warning(f"Could not find original task metadata for ID {ctftime_id}. Skipping for summary/keywords.")
            continue
        
        # --- Prepare input for Summary job ---
        summary_input_tasks.append({
            "ctftime_id": ctftime_id,
            "event_name": original_task["event_name"],
            "task_name": original_task["task_name"],
            "text_content": rewritten_content # Use the rewritten text
        })

        # --- Prepare input for Keyword job ---
        keyword_input_tasks.append({
            "ctftime_id": ctftime_id,
            "event_name": original_task["event_name"],
            "task_name": original_task["task_name"],
            "text_content": rewritten_content # Use the rewritten text
        })

    # Use a ThreadPoolExecutor to run the jobs in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit the summarize job
        summarize_prompt = get_summarize_prompt()
        summarize_input_uri = prepare_and_upload_input(
            input_data=summary_input_tasks,
            prompt_template=summarize_prompt,
            gcs_bucket=gcs_bucket,
            job_name="2_summarize",
            pipeline_run_id=pipeline_run_id,
            raw_requests_dir=raw_requests_dir,
        )
        future_summarize = executor.submit(
            execute_batch_job,
            job_name_prefix="2_summarize",
            model_name=SUMMARY_MODEL_NAME,
            input_gcs_uri=summarize_input_uri,
            gcs_bucket=gcs_bucket,
            pipeline_run_id=pipeline_run_id,
        )

        # Submit the keyword job
        keyword_prompt = get_keyword_prompt()
        keyword_input_uri = prepare_and_upload_input(
            input_data=keyword_input_tasks,
            prompt_template=keyword_prompt,
            gcs_bucket=gcs_bucket,
            job_name="3_keywords",
            pipeline_run_id=pipeline_run_id,
            raw_requests_dir=raw_requests_dir,
        )
        future_keywords = executor.submit(
            execute_batch_job,
            job_name_prefix="3_keywords",
            model_name=KEYWORD_MODEL_NAME,
            input_gcs_uri=keyword_input_uri,
            gcs_bucket=gcs_bucket,
            pipeline_run_id=pipeline_run_id,
        )
        
        # Wait for jobs to complete and get results
        try:
            summary_output_uri = future_summarize.result()
            keyword_output_uri = future_keywords.result()

            summaries = download_and_parse_results(
                gcs_output_uri=summary_output_uri, 
                gcs_bucket=gcs_bucket,
                raw_ai_processed_dir=raw_ai_processed_dir,
                job_name_prefix="2_summarize"
            )
            keywords_raw = download_and_parse_results(
                gcs_output_uri=keyword_output_uri, 
                gcs_bucket=gcs_bucket,
                raw_ai_processed_dir=raw_ai_processed_dir,
                job_name_prefix="3_keywords"
            )
            
            # Clean keyword results to be valid JSON
            keywords = {}
            for item_id, raw_json in keywords_raw.items():
                if not isinstance(raw_json, str):
                    logging.warning(f"Keyword result for ID {item_id} is not a string. Defaulting to empty list.")
                    keywords[item_id] = []
                    continue
                try:
                    # Clean up the response to ensure it's valid JSON
                    json_str = raw_json.strip().replace("```json", "").replace("```", "")
                    keywords[item_id] = json.loads(json_str)
                except json.JSONDecodeError:
                    logging.warning(f"Could not parse keyword JSON for ID {item_id}. Defaulting to empty list.")
                    keywords[item_id] = []

        except Exception as e:
            logging.critical(f"Stages 2/3 failed. Halting pipeline. Error: {e}")
            return

    logging.info(f"✅ Stages 2 & 3 Complete.")
    logging.info(f"  Processed {len(summaries)} summaries.")
    logging.info(f"  Processed {len(keywords)} keyword sets.")

    # --- Stage 4: Final Assembly & Save ---
    logging.info("--- STAGE 4: Merging all results and saving locally ---")
    final_results_count = 0

    # The keys for all result dictionaries are the ctftime_ids
    all_processed_ids = set(rewritten_texts.keys()) | set(summaries.keys()) | set(keywords.keys())

    for ctftime_id in all_processed_ids:
        # --- New: Load Metadata ---
        event_name = "Unknown Event"
        task_name = "Unknown Task"
        original_url = ""
        meta_path = os.path.join(meta_dir, f"{ctftime_id}.meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r') as f:
                    meta_data = json.load(f)
                    event_name = meta_data.get("event_name", event_name)
                    task_name = meta_data.get("task_name", task_name)
                    original_url = meta_data.get("original_writeup_url", "")
            except (json.JSONDecodeError, IOError) as e:
                logging.warning(f"Could not read or parse metadata for {ctftime_id}: {e}")
        # --- End New Metadata ---

        # Safely get the results for each stage
        rewritten = rewritten_texts.get(ctftime_id, "")
        summary = summaries.get(ctftime_id, "")
        keywords_list = keywords.get(ctftime_id, [])

        # Assemble the final, structured data object
        final_data = {
            "ctftime_id": ctftime_id,
            "original_writeup_url": original_url,
            "event_name": event_name,
            "task_name": task_name,
            "ai_tags": keywords_list,
            "rag_summary": summary,
            "rewritten_full_text": rewritten,
            "full_text": ctftime_id_to_original_text.get(str(ctftime_id), "")
        }

        # Write the final JSON to a file
        output_filename = os.path.join(ai_output_dir, f"{ctftime_id}.json")
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(final_data, f, indent=4)
            final_results_count += 1
        except IOError as e:
            logging.error(f"Error writing JSON file for ID {ctftime_id}: {e}")
            
    logging.info(f"✅ Stage 4 Complete. Successfully saved {final_results_count} final JSON files.")

    logging.info(">>> AI Batch Processing Pipeline Finished! <<<")


# --- Helper Functions ---

def run_job_and_get_results(job_name_prefix, model_name, input_uri, gcs_bucket, pipeline_run_id):
    """Helper function to run a single batch job and return its results."""
    output_uri = execute_batch_job(
        job_name_prefix=job_name_prefix,
        model_name=model_name,
        input_gcs_uri=input_uri,
        gcs_bucket=gcs_bucket,
        pipeline_run_id=pipeline_run_id,
    )
    return download_and_parse_results(output_uri, gcs_bucket)

def load_summary_data():
    # This function is now obsolete as metadata is loaded directly in Stage 4.
    # It's kept here to avoid breaking the script if called, but should be removed in a future refactor.
    logging.warning("Call to obsolete function 'load_summary_data'. Returning empty dict.")
    return {}

def load_raw_texts(processed_dir: str) -> dict:
    """This function is obsolete and no longer used in the manifest-driven workflow."""
    logging.warning("load_raw_texts is an obsolete function and should not be called.")
    return {}

def get_rewrite_prompt() -> str:
    """
    Returns the prompt for rewriting an article, now with placeholders for context.
    """
    return """
The following is a cybersecurity CTF (Capture The Flag) write-up that may be part of a larger text document.
Your task is to find the specific section for the challenge named '$task_name' from the event '$event_name'.
Once you have located the correct section, rewrite ONLY that part to improve clarity, fix grammatical errors, and improve the overall structure.

The goal is to make the technical explanation as clear and easy to understand as possible for a downstream AI model.
Do not add any new information or summaries. Focus only on rewriting the existing content for clarity.

Original Text:
---
$writeup
---

Rewritten Text:
"""

def get_summarize_prompt() -> str:
    """
    Returns the prompt for summarizing an article, now with placeholders for context.
    """
    return """
The following text is a rewritten CTF (Capture The Flag) write-up that may be part of a larger document.
Your task is to find the specific section for the challenge named '$task_name' from the event '$event_name'.
Once located, create a RAG-optimized summary of that section of approximately 350 tokens.

The summary should focus on the technical explanation of the challenge and its solution.
It must be concise, clear, and structured for easy parsing by a downstream AI model.

Rewritten Text:
---
$writeup
---

RAG Summary:
"""

def get_keyword_prompt() -> str:
    """
    Returns the prompt for generating keywords, now with placeholders for context.
    """
    return """
The following text is a rewritten CTF (Capture The Flag) write-up that may be part of a larger document.
Your task is to find the specific section for the challenge named '$task_name' from the event '$event_name'.
After locating the correct section, analyze its content and generate a list of all relevant technical keywords.

The keywords should cover vulnerabilities, tools, protocols, and techniques mentioned.
Return the keywords as a single, valid JSON array of strings. Do not include any other text or explanation.

Example:
["sql injection", "nmap", "buffer overflow", "SSTI"]

Rewritten Text:
---
$writeup
---

Keywords:
"""

if __name__ == "__main__":
    main() 