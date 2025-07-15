#!/usr/bin/env python3
"""
Vector Index Pipeline Step 3: Populate Vector Indexes

Reads the output manifest from the embedding jobs and uses the GCS URIs
to populate the two corresponding Vector Search indexes.
"""

import os
import json
import logging
import threading
import argparse
from google.cloud import aiplatform
from dotenv import load_dotenv

# --- Configuration ---
# By setting override=True, we ensure that the values from the .env file
# are always used, even if the variables are already set in the environment.
load_dotenv(override=True)
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = "us-central1"

# --- IMPORTANT ---
# You must create two Vector Search indexes in the Google Cloud Console
# and provide their numeric IDs here as environment variables.
SUMMARY_INDEX_ID = os.getenv("SUMMARY_INDEX_ID")
DETAILED_INDEX_ID = os.getenv("DETAILED_INDEX_ID")

# Input manifest from the previous step
INPUT_MANIFEST = "VectorIndex/output/embedding_output_manifest.json"

def setup_logging():
    """Sets up the logging for the script."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Logging setup complete.")

def populate_index(index_id: str, embeddings_gcs_uri: str, index_name: str, is_complete_overwrite: bool = True):
    """
    Populates a specific Vector Search index from a GCS directory of embeddings.
    This function now includes a robust transformation step to clean the
    batch prediction output before updating the index.
    """
    if not index_id or not embeddings_gcs_uri:
        logging.warning(f"Missing Index ID or GCS URI for '{index_name}'. Skipping population.")
        return

    from google.cloud import storage
    storage_client = storage.Client(project=PROJECT_ID)
    bucket_name = embeddings_gcs_uri.replace("gs://", "").split("/")[0]
    bucket = storage_client.bucket(bucket_name)

    # 1. Find the correct prediction subdirectory from the batch job output
    prefix = "/".join(embeddings_gcs_uri.replace("gs://", "").split("/")[1:])
    blobs = storage_client.list_blobs(bucket.name, prefix=prefix, delimiter="/")
    prediction_prefix = None
    for page in blobs.pages:
        for sub_dir in page.prefixes:
            # The prediction output is always in a subdirectory starting with 'prediction'
            if 'prediction' in sub_dir:
                prediction_prefix = sub_dir
                logging.info(f"Found prediction output directory for '{index_name}': gs://{bucket.name}/{prediction_prefix}")
                break
        if prediction_prefix:
            break

    if not prediction_prefix:
        raise FileNotFoundError(f"Could not find a 'prediction' subdirectory in '{embeddings_gcs_uri}'. "
                              "Please verify the batch embedding job completed successfully.")

    # 2. Download, Parse, Transform, and Upload
    temp_dir = f"temp_clean_embeddings_{index_name.replace(' ', '_')}"
    os.makedirs(temp_dir, exist_ok=True)
    
    clean_gcs_path_prefix = f"7_populate_indexes/embeddings_clean/{index_name.replace(' ', '_')}"
    
    logging.info(f"Starting transformation process for '{index_name}'.")
    logging.info(f"Temporary local directory: '{temp_dir}'")
    logging.info(f"Clean GCS destination: 'gs://{bucket.name}/{clean_gcs_path_prefix}/'")

    try:
        prediction_blobs = list(storage_client.list_blobs(bucket.name, prefix=prediction_prefix))
        if not prediction_blobs:
            raise FileNotFoundError(f"No embedding files found in '{prediction_prefix}'.")

        total_blobs = len(prediction_blobs)
        logging.info(f"Found {total_blobs} embedding file(s) to process for '{index_name}'.")

        for i, blob in enumerate(prediction_blobs):
            if not blob.name.endswith((".jsonl", ".json")):
                continue

            logging.info(f"  Processing file {i+1}/{total_blobs}: gs://{bucket.name}/{blob.name}")

            # Define local file paths
            local_raw_path = os.path.join(temp_dir, os.path.basename(blob.name))
            local_clean_path = local_raw_path.replace('.jsonl', '_clean.json').replace('.json', '_clean.json')

            # Download raw prediction file
            logging.info(f"  Downloading gs://{bucket.name}/{blob.name} to {local_raw_path}")
            blob.download_to_filename(local_raw_path)

            # Transform the file
            logging.info(f"  Transforming {local_raw_path} to {local_clean_path}")
            record_count = 0
            with open(local_raw_path, 'r') as infile, open(local_clean_path, 'w') as outfile:
                for line_num, line in enumerate(infile):
                    try:
                        raw_record = json.loads(line)
                        # The 'instance' contains the original data sent for prediction.
                        # The 'predictions' key holds a list of prediction results.
                        clean_record = {
                            "id": raw_record['instance']['id'],
                            "embedding": raw_record['predictions'][0]['embeddings']['values']
                        }
                        outfile.write(json.dumps(clean_record) + '\n')
                        record_count += 1
                        if record_count % 1000 == 0:
                            logging.info(f"    ... transformed {record_count} records ...")
                    except (json.JSONDecodeError, KeyError, IndexError) as e:
                        logging.warning(f"    Skipping malformed line {line_num+1} or missing key in {blob.name}: {e}")
            
            logging.info(f"    Successfully transformed {record_count} records from this file.")

            # If no records were transformed, there's no point in continuing.
            if record_count == 0:
                logging.error(f"  No records were successfully transformed from {blob.name}. Aborting for this index.")
                # Clean up the empty local file before returning
                os.remove(local_clean_path)
                return

            # Upload the clean file to the new GCS location
            clean_blob_name = f"{clean_gcs_path_prefix}/{os.path.basename(local_clean_path)}"
            logging.info(f"  Uploading {local_clean_path} to gs://{bucket.name}/{clean_blob_name}")
            clean_blob = bucket.blob(clean_blob_name)
            clean_blob.upload_from_filename(local_clean_path)
    
    finally:
        # 3. Clean up local temporary directory
        import shutil
        shutil.rmtree(temp_dir)
        logging.info(f"Cleaned up temporary directory: '{temp_dir}'")

    # 4. Point the update function to the new, clean GCS directory
    final_gcs_uri_for_update = f"gs://{bucket.name}/{clean_gcs_path_prefix}/"

    if is_complete_overwrite:
        logging.info(f"Populating '{index_name}' index (ID: {index_id}) with a complete overwrite.")
    else:
        logging.info(f"Appending to '{index_name}' index (ID: {index_id}).")
        
    logging.info(f"  Source GCS URI for cleaned data: {final_gcs_uri_for_update}")

    try:
        # Get a reference to the index
        vector_index = aiplatform.MatchingEngineIndex(
            index_name=index_id, project=PROJECT_ID, location=LOCATION
        )

        # Populate the index. This is an upsert operation.
        # `is_complete_overwrite=True` will clear the index before adding new data.
        vector_index.update_embeddings(
            contents_delta_uri=final_gcs_uri_for_update,
            is_complete_overwrite=is_complete_overwrite
        )

        logging.info(f"✅ Successfully initiated population for '{index_name}' index.")
        logging.info("  The process will complete asynchronously in the background. Monitor progress in the Google Cloud Console.")

    except Exception as e:
        logging.error(f"❌ Failed to populate '{index_name}' index.")
        logging.error(f"  Error: {e}", exc_info=True)
        raise

def main():
    """Main function to orchestrate populating the indexes."""
    parser = argparse.ArgumentParser(description="Populate Vertex AI Vector Search indexes from GCS.")
    parser.add_argument(
        '--no-overwrite',
        dest='is_complete_overwrite',
        action='store_false',
        help="Append to the index instead of performing a complete overwrite. Default is to overwrite."
    )
    args = parser.parse_args()

    setup_logging()
    logging.info(">>> Starting Step 7: Populate Vector Indexes <<<")

    # --- Pre-flight checks ---
    if not SUMMARY_INDEX_ID or not DETAILED_INDEX_ID:
        logging.critical("FATAL: Environment variables for SUMMARY_INDEX_ID and/or DETAILED_INDEX_ID are not set.")
        logging.critical("Please create the indexes in the Google Cloud Console and set their IDs in your .env file.")
        return

    try:
        # --- Initialize Vertex AI Client ---
        aiplatform.init(project=PROJECT_ID, location=LOCATION)

        # --- Load Input Manifest ---
        with open(INPUT_MANIFEST, 'r') as f:
            manifest = json.load(f)
        
        summary_uri = manifest.get("summary_embedding_uri")
        detailed_uri = manifest.get("detailed_embedding_uri")

        # --- Populate Indexes Concurrently ---
        logging.info("Starting index population in parallel for 'Summary Index' and 'Detailed Index'.")

        # Create threads to run the populate_index function for each index
        summary_thread = threading.Thread(
            target=populate_index, 
            args=(SUMMARY_INDEX_ID, summary_uri, "Summary Index", args.is_complete_overwrite)
        )
        detailed_thread = threading.Thread(
            target=populate_index, 
            args=(DETAILED_INDEX_ID, detailed_uri, "Detailed Index", args.is_complete_overwrite)
        )

        # Start the threads
        summary_thread.start()
        detailed_thread.start()

        # Wait for both threads to complete before moving on
        summary_thread.join()
        detailed_thread.join()

        logging.info("Both index population tasks have finished their data preparation and initiation.")

    except FileNotFoundError:
        logging.critical(f"FATAL: Input manifest not found at '{INPUT_MANIFEST}'.")
        logging.critical("Please run step 6 (6_run_embedding_jobs.py) first.")
        return
    except Exception as e:
        logging.critical(f"An error occurred in the main process: {e}", exc_info=True)
        return

    logging.info(">>> Step 7 complete. Index population has been initiated. <<<")

if __name__ == "__main__":
    main() 