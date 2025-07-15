#!/usr/bin/env python3
"""
Vector Index Pipeline Step 1: Prepare Data for Embedding

Connects to MongoDB, fetches write-ups, and prepares two separate JSONL files
for the batch embedding process: one for summaries and one for detailed chunks.
"""

import os
import json
import logging
from pymongo import MongoClient
import tiktoken
from google.cloud import storage
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "ctf_writeups_db"
COLLECTION_NAME = "writeups"

# GCS Configuration
GCS_BUCKET_NAME = "ctf-rag"
OUTPUT_GCS_FOLDER = "5_prepare_embedding_data"
LOCAL_OUTPUT_DIR = "VectorIndex/output" # For local inspection

# Chunking Configuration
CHUNK_SIZE = 500  # tokens
CHUNK_OVERLAP = 75 # tokens (approx 15% of CHUNK_SIZE)

def setup_logging():
    """Sets up the logging for the script."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Logging setup complete.")

def connect_to_mongodb():
    """Connects to MongoDB and returns the writeups collection."""
    logging.info(f"Connecting to MongoDB at {MONGO_URI}...")
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        # Verify connection by counting documents
        count = collection.count_documents({})
        logging.info(f"Successfully connected to MongoDB. Found {count} documents in '{COLLECTION_NAME}'.")
        return collection
    except Exception as e:
        logging.critical(f"Failed to connect to MongoDB: {e}")
        raise

def prepare_summary_data(collection):
    """Fetches documents and prepares the JSONL data for summaries."""
    logging.info("Preparing summary data...")
    summary_list = []
    for doc in collection.find({}, {"ctftime_id": 1, "rag_summary": 1}):
        if doc.get("rag_summary"):
            summary_list.append({
                "id": str(doc['ctftime_id']),
                "content": doc['rag_summary'] # Batch prediction expects 'content' key
            })
    logging.info(f"Prepared {len(summary_list)} summaries.")
    return summary_list

def prepare_detailed_chunk_data(collection):
    """Fetches docs, chunks text, and prepares JSONL data for detailed chunks."""
    logging.info("Preparing detailed chunk data with overlap...")
    chunk_list = []
    encoding = tiktoken.get_encoding("cl100k_base")

    for doc in collection.find({}, {"ctftime_id": 1, "rewritten_full_text": 1}):
        if not doc.get("rewritten_full_text"):
            continue
        
        doc_id = str(doc['ctftime_id'])
        full_text = doc['rewritten_full_text']
        # The disallowed_special=() argument tells tiktoken to treat all special tokens as normal text.
        tokens = encoding.encode(full_text, disallowed_special=())
        
        # Create overlapping chunks
        for i in range(0, len(tokens), CHUNK_SIZE - CHUNK_OVERLAP):
            chunk_tokens = tokens[i:i + CHUNK_SIZE]
            chunk_text = encoding.decode(chunk_tokens)
            
            chunk_list.append({
                "id": f"{doc_id}_chunk_{i//(CHUNK_SIZE-CHUNK_OVERLAP)}",
                "content": chunk_text # Batch prediction expects 'content' key
            })
            
    logging.info(f"Prepared {len(chunk_list)} detailed chunks.")
    return chunk_list

def save_to_jsonl_and_upload(data, filename_prefix):
    """Saves data to a local JSONL file and uploads it to GCS."""
    if not data:
        logging.warning(f"No data to save for '{filename_prefix}'. Skipping.")
        return None

    logging.info(f"Saving and uploading data for '{filename_prefix}'...")
    
    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
    local_filepath = os.path.join(LOCAL_OUTPUT_DIR, f"{filename_prefix}.jsonl")
    gcs_filepath = f"{OUTPUT_GCS_FOLDER}/{filename_prefix}.jsonl"

    # Save locally
    try:
        with open(local_filepath, 'w', encoding='utf-8') as f:
            for item in data:
                f.write(json.dumps(item) + '\n')
        logging.info(f"Successfully saved data locally to {local_filepath}")
    except IOError as e:
        logging.error(f"Failed to write local file {local_filepath}: {e}")
        raise

    # Upload to GCS
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(gcs_filepath)
        blob.upload_from_filename(local_filepath)
        gcs_uri = f"gs://{GCS_BUCKET_NAME}/{gcs_filepath}"
        logging.info(f"Successfully uploaded to {gcs_uri}")
        return gcs_uri
    except Exception as e:
        logging.error(f"Failed to upload to GCS: {e}")
        raise

def main():
    """Main function to orchestrate the data preparation."""
    setup_logging()
    logging.info(">>> Starting Step 5: Prepare Data for Embedding <<<")

    try:
        collection = connect_to_mongodb()

        # --- Process Summaries ---
        summary_data = prepare_summary_data(collection)
        summary_gcs_uri = save_to_jsonl_and_upload(summary_data, "summaries")
        if summary_gcs_uri:
            logging.info(f"Summary data prepared and uploaded to: {summary_gcs_uri}")

        # --- Process Detailed Chunks ---
        detailed_data = prepare_detailed_chunk_data(collection)
        detailed_gcs_uri = save_to_jsonl_and_upload(detailed_data, "detailed_chunks")
        if detailed_gcs_uri:
            logging.info(f"Detailed chunk data prepared and uploaded to: {detailed_gcs_uri}")
        
        # --- Create a manifest for the next step ---
        manifest = {
            "summary_input_uri": summary_gcs_uri,
            "detailed_input_uri": detailed_gcs_uri
        }
        os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
        manifest_path = os.path.join(LOCAL_OUTPUT_DIR, "embedding_input_manifest.json")
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=4)
        
        logging.info(f"Output manifest saved to {manifest_path}")

    except Exception as e:
        logging.critical(f"An error occurred in the main process: {e}", exc_info=True)
        return

    logging.info(">>> Step 5 complete. Data is ready for batch embedding. <<<")


if __name__ == "__main__":
    main() 