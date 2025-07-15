import os
import json
import logging
import sys
from pymongo import MongoClient
from pipeline_logging import setup_logging

# --- Configuration ---
# Database connection details
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "ctf_writeups_db"
COLLECTION_NAME = "writeups"

def get_mongo_client(uri):
    """Establishes a connection to MongoDB and returns the client object."""
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        # The ismaster command is cheap and does not require auth.
        client.admin.command('ismaster')
        logging.info("Successfully connected to MongoDB.")
        return client
    except Exception as e:
        logging.error(f"Failed to connect to MongoDB: {e}")
        return None

def store_data_in_mongodb():
    """
    Reads structured JSON files from the input directory and upserts them into MongoDB.
    """
    if len(sys.argv) < 2:
        print("FATAL: Missing run_path argument. This script should be called from Main.py.")
        sys.exit(1)
    run_path = sys.argv[1]

    # Set up a run-specific logger
    setup_logging(run_path)
    
    # Define the run-specific input directory
    input_dir = os.path.join(run_path, "ai_processed")

    logging.info("--- Starting Step 4: Storing data in MongoDB ---")

    client = get_mongo_client(MONGO_URI)
    if not client:
        return

    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    # Ensure the input directory exists
    os.makedirs(input_dir, exist_ok=True)

    # Get a list of all JSON files in the directory
    try:
        json_files = [f for f in os.listdir(input_dir) if f.endswith('.json')]
        if not json_files:
            logging.warning(f"No JSON files found in {input_dir}. Nothing to process. (This is expected if the AI step was skipped).")
            return
    except FileNotFoundError:
        # This case should ideally not be hit due to makedirs, but is here for robustness.
        logging.error(f"Input directory not found: {input_dir}")
        return

    logging.info(f"Found {len(json_files)} JSON files to process.")
    upserted_count = 0

    for filename in json_files:
        file_path = os.path.join(input_dir, filename)
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            # Use ctftime_id as the unique identifier for the document
            if 'ctftime_id' not in data:
                logging.warning(f"Skipping {filename}: does not contain 'ctftime_id'.")
                continue
            
            ctftime_id = data['ctftime_id']
            
            # Upsert the document: update if it exists, insert if it doesn't.
            # This makes the script safe to re-run.
            result = collection.update_one(
                {'ctftime_id': ctftime_id},
                {'$set': data},
                upsert=True
            )
            
            if result.upserted_id:
                logging.info(f"Inserted new document with ctftime_id: {ctftime_id}")
            else:
                logging.info(f"Updated existing document with ctftime_id: {ctftime_id}")

            upserted_count += 1

        except json.JSONDecodeError:
            logging.error(f"Error decoding JSON from {filename}. Skipping.")
        except Exception as e:
            logging.error(f"An error occurred while processing {filename}: {e}")

    logging.info(f"Database storage process completed. Processed {upserted_count} documents.")
    client.close()


if __name__ == "__main__":
    store_data_in_mongodb() 