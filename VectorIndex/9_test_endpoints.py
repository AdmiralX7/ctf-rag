
#!/usr/bin/env python3
"""
Vector Index Pipeline Step 5: Test Index Endpoints

This script provides a simple health check for the deployed Vector Search endpoints.
It sends a hardcoded query to both the summary and detailed indexes to ensure they
are live, responsive, and can return results.
"""

import os
import logging
import argparse
from google.cloud import aiplatform
import vertexai
from vertexai.language_models import TextEmbeddingModel
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv(override=True)
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = "us-central1"

# --- Endpoint Configuration ---
SUMMARY_ENDPOINT_NAME = os.getenv("SUMMARY_ENDPOINT_NAME", "ctf-summary-endpoint")
DETAILED_ENDPOINT_NAME = os.getenv("DETAILED_ENDPOINT_NAME", "ctf-detailed-endpoint")

# The embedding model is needed to generate a query vector.
# This must match the model used to create the index embeddings.
EMBEDDING_MODEL = "text-embedding-005"

def setup_logging():
    """Sets up the logging for the script."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Logging setup complete.")

def get_endpoint(endpoint_display_name: str) -> aiplatform.MatchingEngineIndexEndpoint:
    """Gets a reference to a deployed endpoint by its display name."""
    endpoints = aiplatform.MatchingEngineIndexEndpoint.list(
        filter=f'display_name="{endpoint_display_name}"',
        project=PROJECT_ID,
        location=LOCATION
    )
    if not endpoints:
        raise RuntimeError(f"Endpoint with display name '{endpoint_display_name}' not found.")

    endpoint_resource_name = endpoints[0].resource_name
    logging.info(f"Found endpoint: '{endpoint_display_name}' (ID: {endpoint_resource_name.split('/')[-1]})")
    
    # Instantiate the endpoint with the full resource name to ensure it's fully initialized
    return aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=endpoint_resource_name)

def test_endpoint(endpoint: aiplatform.MatchingEngineIndexEndpoint, query_text: str):
    """Generates an embedding for the query and tests a given endpoint."""
    if not endpoint.deployed_indexes:
        logging.warning(f"Endpoint '{endpoint.display_name}' has no deployed indexes. Cannot test.")
        return

    # Use the first deployed index on the endpoint for the test
    deployed_index_id = endpoint.deployed_indexes[0].id
    logging.info(f"Testing endpoint '{endpoint.display_name}' with deployed index '{deployed_index_id}'...")
    
    # Generate the embedding for the query text
    model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)
    query_embedding = model.get_embeddings([query_text])[0].values
    
    logging.info(f"  Generated embedding for query: '{query_text}'")

    # Perform the search
    response = endpoint.find_neighbors(
        deployed_index_id=deployed_index_id,
        queries=[query_embedding],
        num_neighbors=3
    )
    
    # Process and print results
    if not response or not response[0]:
        logging.warning("  No neighbors found for the query.")
        return

    logging.info("  ✅ Found neighbors:")
    for i, neighbor in enumerate(response[0]):
        logging.info(f"    {i+1}. ID: {neighbor.id}, Distance: {neighbor.distance:.4f}")

def main(query: str = None):
    """Main function to run tests on the deployed endpoints."""
    # If a query is not passed directly, parse it from command line arguments
    if query is None:
        parser = argparse.ArgumentParser(description="Test deployed Vertex AI Vector Search endpoints.")
        parser.add_argument(
            '--query',
            type=str,
            default="What is the vulnerability in vsftpd 2.3.4?",
            help="The sample query text to test the endpoints with."
        )
        args = parser.parse_args()
        query_text = args.query
    else:
        query_text = query

    setup_logging()
    logging.info(">>> Starting Step 9: Test Index Endpoints <<<")

    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    vertexai.init(project=PROJECT_ID, location=LOCATION)

    try:
        # --- Test Summary Endpoint ---
        logging.info("\n--- Testing Summary Index ---")
        summary_endpoint = get_endpoint(SUMMARY_ENDPOINT_NAME)
        test_endpoint(summary_endpoint, query_text)

        # --- Test Detailed Endpoint ---
        logging.info("\n--- Testing Detailed Index ---")
        detailed_endpoint = get_endpoint(DETAILED_ENDPOINT_NAME)
        test_endpoint(detailed_endpoint, query_text)

    except Exception as e:
        logging.error(f"❌ An error occurred during endpoint testing: {e}", exc_info=True)

    logging.info("\n>>> Step 9 script finished. <<<")

if __name__ == "__main__":
    main() 