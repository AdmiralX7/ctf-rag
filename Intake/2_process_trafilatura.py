import os
import trafilatura
import time
import logging
import sys
from pipeline_logging import setup_logging
import json

# --- Configuration ---
# If the extracted text has fewer characters than this, it's considered low-quality.
MIN_CONTENT_LENGTH = 300 

# --- Constants ---
# This is a global log, not run-specific
REJECTED_IDS_LOG = "Intake/rejected_ids.log"

def process_single_file(run_path, ctftime_id, input_html_path, output_dir):
    """
    Reads a single HTML file, extracts content using Trafilatura, checks its quality,
    and saves it to a text file. Returns the relative path on success, None on failure.
    """
    if not os.path.exists(input_html_path):
        return None

    logging.debug(f"Processing: {os.path.relpath(input_html_path, run_path)}")
    try:
        with open(input_html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        extracted_text = trafilatura.extract(
            html_content,
            include_comments=False,
            include_tables=True,
            output_format='markdown'
        )

        if not extracted_text or len(extracted_text) < MIN_CONTENT_LENGTH:
            logging.debug(f"Content for {ctftime_id} in {os.path.basename(input_html_path)} is too short or empty.")
            return "low_quality" # Special return value for low quality

        # Construct the output path
        base_filename = os.path.basename(input_html_path).replace('.html', '.txt')
        output_rel_path = os.path.join(os.path.basename(output_dir), base_filename)
        output_abs_path = os.path.join(output_dir, base_filename)
        
        with open(output_abs_path, 'w', encoding='utf-8') as f:
            f.write(extracted_text)
        
        logging.debug(f"Successfully extracted to {base_filename}")
        return output_rel_path

    except Exception as e:
        logging.error(f"Error processing file {os.path.relpath(input_html_path, run_path)}: {e}")
        return None

def get_existing_rejected_ids():
    """Reads the rejected IDs log and returns a set of integer IDs."""
    if not os.path.exists(REJECTED_IDS_LOG):
        return set()
    try:
        with open(REJECTED_IDS_LOG, 'r') as f:
            return {int(line.strip()) for line in f if line.strip() and not line.startswith('#')}
    except (IOError, ValueError) as e:
        logging.warning(f"Could not read or parse rejected IDs log: {e}")
        return set()

def extract_and_clean():
    """
    Reads the run_manifest, extracts main content from scraped HTML files, 
    performs a quality check, and updates the manifest.
    """
    if len(sys.argv) < 2:
        print("FATAL: Missing run_path argument. This script should be called from Main.py.")
        sys.exit(1)
    run_path = sys.argv[1]

    # Set up a run-specific logger
    setup_logging(run_path)
    
    # Define run-specific input and output directories
    output_dir = os.path.join(run_path, "processed_trafilatura")
    os.makedirs(output_dir, exist_ok=True)
    
    # --- New: Load the manifest ---
    manifest_path = os.path.join(run_path, "run_manifest.json")
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"FATAL: Could not load or parse the run manifest at '{manifest_path}'. Error: {e}")
        return

    # --- New: Access the correct dictionary within the manifest ---
    urls_to_process = manifest.get("processed_urls", {})
    if not urls_to_process:
        logging.info("No new URLs to process in the manifest. Exiting.")
        return

    logging.info("--- Starting Step 2: Main Content Extraction (trafilatura) ---")
    logging.info(f"Loaded manifest with {len(urls_to_process)} unique URLs to process.")

    # Load existing rejected IDs to avoid duplicate logging
    existing_rejected_ids = get_existing_rejected_ids()
    logging.debug(f"Loaded {len(existing_rejected_ids)} existing rejected IDs.")

    start_time = time.time()
    processed_count = 0
    skipped_count = 0

    for url, data in urls_to_process.items():
        if data.get("status") != "scraped":
            logging.debug(f"Skipping URL (status is not 'scraped'): {url}")
            continue

        if not data.get("tasks"):
            logging.warning(f"URL {url} has no tasks associated with it. Skipping.")
            continue
        
        task = data['tasks'][0]
        ctftime_id = task['ctftime_id']
        
        # --- New: Process both primary and summary HTML files ---
        input_dir = os.path.join(run_path, "output")
        primary_html_path = os.path.join(input_dir, f"{ctftime_id}.html")
        summary_html_path = os.path.join(input_dir, f"{ctftime_id}.summary.html")

        cleaned_text_path = process_single_file(run_path, ctftime_id, primary_html_path, output_dir)
        cleaned_summary_path = process_single_file(run_path, ctftime_id, summary_html_path, output_dir)
        
        # --- Quality Check and Manifest Update ---
        # If both are low quality, or if one is low quality and the other doesn't exist, reject the ID.
        if (cleaned_text_path == "low_quality" and cleaned_summary_path in [None, "low_quality"]) or \
           (cleaned_summary_path == "low_quality" and cleaned_text_path in [None, "low_quality"]):
            
            logging.info(f"Rejecting {ctftime_id}: all available content was low quality.")
            skipped_count += 1
            data["status"] = "rejected_low_quality"
            
            # Log the ID as rejected
            if ctftime_id not in existing_rejected_ids:
                try:
                    with open(REJECTED_IDS_LOG, 'a') as f:
                        f.write(f"{ctftime_id}\n")
                    existing_rejected_ids.add(ctftime_id)
                except IOError as e:
                    logging.error(f"Failed to write to rejected_ids.log: {e}")
            continue

        # If at least one file was processed successfully, update the manifest
        if cleaned_text_path and cleaned_text_path != "low_quality" or \
           cleaned_summary_path and cleaned_summary_path != "low_quality":
            
            data["status"] = "cleaned"
            if cleaned_text_path and cleaned_text_path != "low_quality":
                data["cleaned_text_path"] = cleaned_text_path
            if cleaned_summary_path and cleaned_summary_path != "low_quality":
                data["cleaned_summary_path"] = cleaned_summary_path
            
            processed_count += 1
            logging.info(f"Successfully processed content for ID {ctftime_id}.")
        else:
            # This case hits if both process_single_file calls returned None (e.g., file not found, error)
            logging.warning(f"Failed to clean any content for ID {ctftime_id}.")
            data["status"] = "failed_cleaning"
        # --- End New Logic ---

    # --- New: Save the updated manifest ---
    try:
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=4)
        logging.info("Successfully updated the run manifest.")
    except IOError as e:
        logging.error(f"Could not save the updated manifest file: {e}")

    end_time = time.time()
    logging.info(f"Processing complete in {end_time - start_time:.2f} seconds.")
    logging.info(f"  Successfully converted: {processed_count} files.")
    logging.info(f"  Skipped (low quality): {skipped_count} files.")


if __name__ == "__main__":
    extract_and_clean() 