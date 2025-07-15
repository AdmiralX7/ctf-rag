#!/usr/bin/env python3
"""
Vector Index Pipeline Step 2: Run Batch Embedding Jobs

Reads the input manifest, launches two parallel Vertex AI Batch Prediction jobs
to generate embeddings, and saves the output URIs to a new manifest.
"""

import os
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from google.cloud import aiplatform, storage
from google.cloud.aiplatform_v1.types import job_state as job_state_v1
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = "us-central1"

# The full publisher model path will be constructed from this.
EMBEDDING_MODEL_NAME = "text-embedding-005"

GCS_BUCKET_NAME = "ctf-rag"
# Input is read from the manifest created by step 5
INPUT_MANIFEST = "VectorIndex/output/embedding_input_manifest.json"
# Output will be placed in a new folder
OUTPUT_GCS_FOLDER = "6_run_embedding_jobs"
LOCAL_OUTPUT_DIR = "VectorIndex/output"

def setup_logging():
    """Sets up the logging for the script."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Logging setup complete.")

def execute_batch_embedding_job(
    job_name_prefix: str,
    input_gcs_uri: str,
    gcs_bucket: storage.Bucket,
    pipeline_run_id: str
) -> str:
    """Creates, runs, and waits for a Vertex AI Batch Prediction job for embeddings."""
    job_display_name = f"{job_name_prefix}-{pipeline_run_id}"
    # Note: For embeddings, the output format is slightly different.
    # We are providing a directory for Vertex to write its output to.
    output_gcs_uri_prefix = f"gs://{gcs_bucket.name}/{OUTPUT_GCS_FOLDER}/{job_display_name}/"

    logging.info(f"Starting batch embedding job: {job_display_name}")
    logging.info(f"  Input: {input_gcs_uri}")
    logging.info(f"  Output Prefix: {output_gcs_uri_prefix}")

    # The publisher model path for embedding models
    publisher_model = f"publishers/google/models/{EMBEDDING_MODEL_NAME}"
    logging.info(f"  Using publisher model: {publisher_model}")

    # The 'model_parameters' are crucial for embedding jobs.
    model_parameters = {"outputTokenization": False}

    batch_job = aiplatform.BatchPredictionJob.create(
        job_display_name=job_display_name,
        model_name=publisher_model,
        instances_format="jsonl",
        predictions_format="jsonl",
        gcs_source=input_gcs_uri,
        gcs_destination_prefix=output_gcs_uri_prefix,
        model_parameters=model_parameters,
    )

    logging.info(f"Batch job created: {batch_job.name}. Waiting for completion...")
    batch_job.wait()

    if batch_job.state == job_state_v1.JobState.JOB_STATE_SUCCEEDED:
        logging.info(f"✅ Job {job_display_name} succeeded.")
        # The result is a directory, so we return the prefix.
        return output_gcs_uri_prefix
    else:
        logging.error(f"❌ Job {job_display_name} failed with state: {batch_job.state}")
        logging.error(f"   Error: {batch_job.error}")
        raise RuntimeError(f"Job {job_display_name} failed.")

def main():
    """Main function to orchestrate the batch embedding jobs."""
    setup_logging()
    logging.info(">>> Starting Step 6: Run Batch Embedding Jobs <<<")

    try:
        # --- Initialize GCP Services ---
        if not PROJECT_ID:
            raise ValueError("GCP_PROJECT_ID environment variable not set.")
        
        aiplatform.init(project=PROJECT_ID, location=LOCATION)
        storage_client = storage.Client(project=PROJECT_ID)
        gcs_bucket = storage_client.bucket(GCS_BUCKET_NAME)
        gcs_bucket.reload() # Verify bucket exists

        # --- Load Input Manifest ---
        with open(INPUT_MANIFEST, 'r') as f:
            input_manifest = json.load(f)
        
        summary_input_uri = input_manifest["summary_input_uri"]
        detailed_input_uri = input_manifest["detailed_input_uri"]

        pipeline_run_id = f"embed-{int(time.time())}"

        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit summary embedding job
            future_summary = executor.submit(
                execute_batch_embedding_job,
                "summary-embeddings",
                summary_input_uri,
                gcs_bucket,
                pipeline_run_id
            )
            # Submit detailed chunk embedding job
            future_detailed = executor.submit(
                execute_batch_embedding_job,
                "detailed-embeddings",
                detailed_input_uri,
                gcs_bucket,
                pipeline_run_id
            )
            
            summary_output_uri = future_summary.result()
            detailed_output_uri = future_detailed.result()

        logging.info("✅ Both embedding jobs completed successfully.")
        
        # --- Create Output Manifest ---
        output_manifest = {
            "summary_embedding_uri": summary_output_uri,
            "detailed_embedding_uri": detailed_output_uri
        }
        os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
        manifest_path = os.path.join(LOCAL_OUTPUT_DIR, "embedding_output_manifest.json")
        with open(manifest_path, 'w') as f:
            json.dump(output_manifest, f, indent=4)
        
        logging.info(f"Output manifest saved to {manifest_path}")

    except Exception as e:
        logging.critical(f"An error occurred in the main process: {e}", exc_info=True)
        return

    logging.info(">>> Step 6 complete. Embeddings are generated and stored in GCS. <<<")

if __name__ == "__main__":
    main() 