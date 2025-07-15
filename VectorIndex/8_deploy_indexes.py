
#!/usr/bin/env python3
"""
Vector Index Pipeline Step 4: Deploy & Manage Index Endpoints

Deploys or undeploys specified Vector Search indexes to/from public endpoints,
making them available for querying. This script is idempotent and includes
cost-saving measures by deleting endpoints when they are no longer in use.

This script must be run with two command-line arguments:
  --action: The operation to perform. Must be either 'deploy' or 'undeploy'.
  --index:  The index to apply the action to. Must be 'summary', 'detailed', or 'all'.

Usage Examples:
  
  # Deploy both the summary and detailed indexes to their respective endpoints
  python 8_deploy_indexes.py --action deploy --index all

  # Undeploy only the detailed index (and delete the endpoint if it becomes empty)
  python 8_deploy_indexes.py --action undeploy --index detailed

  # Deploy only the summary index
  python 8_deploy_indexes.py --action deploy --index summary
"""

import os
import logging
import argparse
import threading
from datetime import datetime
from google.cloud import aiplatform
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv(override=True)
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = "us-central1"

# --- Index and Endpoint Configuration ---
# These names must be unique within your project and are used to identify
# the resources. The script will create them if they don't exist.
SUMMARY_INDEX_ID = os.getenv("SUMMARY_INDEX_ID")
DETAILED_INDEX_ID = os.getenv("DETAILED_INDEX_ID")

SUMMARY_ENDPOINT_NAME = os.getenv("SUMMARY_ENDPOINT_NAME", "ctf-summary-endpoint")
DETAILED_ENDPOINT_NAME = os.getenv("DETAILED_ENDPOINT_NAME", "ctf-detailed-endpoint")

# Machine type for the endpoint. Adjust based on performance needs.
MACHINE_TYPE = "e2-standard-2"

def setup_logging():
    """Sets up the logging for the script."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Logging setup complete.")

def get_or_create_endpoint(endpoint_display_name: str) -> aiplatform.MatchingEngineIndexEndpoint:
    """Gets an existing endpoint by its display name or creates it if it doesn't exist."""
    endpoints = aiplatform.MatchingEngineIndexEndpoint.list(
        filter=f'display_name="{endpoint_display_name}"',
        project=PROJECT_ID,
        location=LOCATION
    )
    if endpoints:
        logging.info(f"Found existing endpoint: {endpoint_display_name}")
        return endpoints[0]
    
    logging.info(f"Creating new endpoint: {endpoint_display_name}")
    return aiplatform.MatchingEngineIndexEndpoint.create(
        display_name=endpoint_display_name,
        description=f"Endpoint for {endpoint_display_name}",
        public_endpoint_enabled=True,
        project=PROJECT_ID,
        location=LOCATION
    )

def manage_deployment(action: str, index_name: str, index_id: str, endpoint_name: str):
    """Handles the deployment or undeployment of a single index."""
    logging.info(f"--- Managing: {index_name} ---")
    logging.info(f"  Action: {action.upper()}")
    logging.info(f"  Index ID: {index_id}")
    logging.info(f"  Endpoint Name: {endpoint_name}")

    if not index_id:
        logging.error(f"FATAL: Index ID for '{index_name}' is not set in environment variables. Skipping.")
        return

    index = aiplatform.MatchingEngineIndex(index_name=index_id)
    
    # Generate a unique ID for the deployment to avoid conflicts
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    base_deployed_index_id = f"deployed_{index_name.lower().replace(' ', '_')}"
    deployed_index_id_unique = f"{base_deployed_index_id}_{timestamp}"

    if action == 'deploy':
        endpoint = get_or_create_endpoint(endpoint_name)
        
        # We no longer check for existing deployments of the same name, as each deployment
        # will now have a unique ID. Instead, we can check if *any* version of this
        # index is already deployed, though for simplicity, we will proceed with a
        # new unique deployment each time, as Vertex AI supports multiple versions.
        
        logging.info(f"Deploying '{index_name}' to endpoint '{endpoint_name}' with unique ID '{deployed_index_id_unique}'...")
        endpoint.deploy_index(
            index=index,
            deployed_index_id=deployed_index_id_unique,
            display_name=f"{index_name} Deployment {timestamp}",
            machine_type=MACHINE_TYPE,
            min_replica_count=1,
            max_replica_count=1
        )
        logging.info(f"✅ Successfully initiated deployment for '{index_name}'. This will take 20-30 minutes.")

    elif action == 'undeploy':
        endpoints = aiplatform.MatchingEngineIndexEndpoint.list(filter=f'display_name="{endpoint_name}"')
        if not endpoints:
            logging.warning(f"Endpoint '{endpoint_name}' not found. Cannot undeploy. No action taken.")
            return
        
        endpoint = endpoints[0]
        
        # Find all deployed indexes on this endpoint that match our base ID
        indexes_to_undeploy = [
            deployed.id for deployed in endpoint.deployed_indexes 
            if deployed.id.startswith(base_deployed_index_id)
        ]

        if not indexes_to_undeploy:
            logging.info(f"No deployments matching '{base_deployed_index_id}*' found on endpoint '{endpoint_name}'. No action needed.")
            return

        for deployed_id in indexes_to_undeploy:
            logging.info(f"Undeploying '{index_name}' (deployed ID: {deployed_id}) from endpoint '{endpoint_name}'...")
            try:
                endpoint.undeploy_index(deployed_index_id=deployed_id)
                logging.info(f"✅ Successfully initiated undeployment for '{deployed_id}'.")
            except Exception as e:
                logging.error(f"Failed to undeploy '{deployed_id}': {e}")

        # Cost-saving: check if the endpoint will be empty after our undeployments
        endpoint.wait() # Wait for undeployment to finish before checking
        refreshed_endpoint = aiplatform.MatchingEngineIndexEndpoint(endpoint.name)
        
        if not refreshed_endpoint.deployed_indexes:
            logging.info(f"Endpoint '{endpoint_name}' is now empty. Deleting to save costs...")
            refreshed_endpoint.delete()
            logging.info(f"✅ Endpoint '{endpoint_name}' deleted.")
        else:
            logging.info(f"Endpoint '{endpoint_name}' still has other deployed indexes. It will not be deleted.")

    else:
        logging.error(f"Invalid action '{action}'. Use 'deploy' or 'undeploy'.")

def main():
    """Main function to parse arguments and manage index deployments."""
    parser = argparse.ArgumentParser(description="Deploy or undeploy Vertex AI Vector Search indexes to endpoints.")
    parser.add_argument(
        '--action',
        type=str,
        required=True,
        choices=['deploy', 'undeploy'],
        help="The action to perform: 'deploy' or 'undeploy'."
    )
    parser.add_argument(
        '--index',
        type=str,
        required=True,
        choices=['summary', 'detailed', 'all'],
        help="The index to manage: 'summary', 'detailed', or 'all'."
    )
    args = parser.parse_args()
    
    setup_logging()
    logging.info(f">>> Starting Step 8: Deploy & Manage Index Endpoints ({args.action.upper()}) <<<")
    
    aiplatform.init(project=PROJECT_ID, location=LOCATION)

    indexes_to_manage = []
    if args.index in ['summary', 'all']:
        indexes_to_manage.append(("Summary Index", SUMMARY_INDEX_ID, SUMMARY_ENDPOINT_NAME))
    if args.index in ['detailed', 'all']:
        indexes_to_manage.append(("Detailed Index", DETAILED_INDEX_ID, DETAILED_ENDPOINT_NAME))

    if not indexes_to_manage:
        logging.error("No valid indexes selected for management. Aborting.")
        return

    threads = []
    for index_name, index_id, endpoint_name in indexes_to_manage:
        thread = threading.Thread(
            target=manage_deployment,
            args=(args.action, index_name, index_id, endpoint_name)
        )
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    logging.info(">>> Step 8 script finished. <<<")

if __name__ == "__main__":
    main() 