# NAME
#     VectorIndex/main.py - Vector Index Pipeline Orchestrator
#
# SYNOPSIS
#     python VectorIndex/main.py --steps <step_list> [OPTIONS]
#
# DESCRIPTION
#     This script serves as the master controller for the entire Vector Index pipeline.
#     It orchestrates the execution of different stages, from data preparation to
#     index deployment and testing. Use the --steps argument to specify which
#     parts of the pipeline to run.
#
# ARGUMENTS
#     --steps <step_list>
#         (Required) A space-separated list of integer step numbers to run.
#         The available steps are:
#         5: Prepare embedding data (summaries and detailed chunks) from MongoDB.
#         6: Run Vertex AI batch embedding jobs on the prepared data.
#         7: Populate the Vector Search indexes with the generated embeddings.
#         8: Deploy or undeploy indexes to a public endpoint.
#         9: Test the live endpoints with a sample query.
#
# OPTIONS
#     --no-overwrite
#         (Optional) When included with Step 7, this flag causes the script to
#         append embeddings to the existing index rather than performing a
#         complete overwrite. If omitted, the index will be wiped and replaced.
#
#     --deploy-action <'deploy'|'undeploy'>
#         (Optional) Used with Step 8. Specifies whether to deploy the index
#         to an endpoint or undeploy it. Defaults to 'deploy'.
#
#     --deploy-index <'summary'|'detailed'|'all'>
#         (Optional) Used with Step 8. Specifies which index to apply the
#         action to. Defaults to 'all'.
#
#     --query <query_text>
#         (Optional) Used with Step 9. The sample query string to test the
#         endpoints with. Defaults to a pre-set question about vsftpd.
#
# USAGE EXAMPLES
#     # Run the full pipeline from data prep to testing
#     python VectorIndex/main.py --steps 5 6 7 8 9
#
#     # Only populate the indexes, appending new data without overwriting
#     python VectorIndex/main.py --steps 7 --no-overwrite
#
#     # Undeploy the detailed index endpoint
#     python VectorIndex/main.py --steps 8 --deploy-action undeploy --deploy-index detailed
#
#     # Test the endpoints with a custom query
#     python VectorIndex/main.py --steps 9 --query "How to exploit the Heartbleed vulnerability?"
#

import argparse
import logging
import sys
import os
import importlib

# Ensure the script can find modules in the VectorIndex directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipeline_logging import setup_logging

# Import pipeline steps dynamically since their names start with numbers
p5 = importlib.import_module("5_prepare_embedding_data")
p6 = importlib.import_module("6_run_embedding_jobs")
p7 = importlib.import_module("7_populate_indexes")
p8 = importlib.import_module("8_deploy_indexes")
p9 = importlib.import_module("9_test_endpoints")

def main():
    """
    Main entry point for the Vector Index pipeline.
    Orchestrates the execution of pipeline steps based on command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Vector Index Pipeline Orchestrator")
    parser.add_argument(
        '--steps',
        nargs='+',
        type=int,
        help="A list of step numbers to run (e.g., --steps 5 7 8)",
        required=True
    )
    parser.add_argument(
        '--no-overwrite',
        action='store_false',
        dest='is_complete_overwrite',
        help="Flag to append to indexes instead of overwriting. Used by Step 7."
    )
    parser.add_argument(
        '--deploy-action',
        type=str,
        choices=['deploy', 'undeploy'],
        default='deploy',
        help="Action for Step 8: 'deploy' or 'undeploy' endpoints."
    )
    parser.add_argument(
        '--deploy-index',
        type=str,
        choices=['summary', 'detailed', 'all'],
        default='all',
        help="Index for Step 8: 'summary', 'detailed', or 'all'."
    )
    parser.add_argument(
        '--query',
        type=str,
        help="Sample query for Step 9 to test endpoints."
    )

    args = parser.parse_args()
    setup_logging()

    logging.info(f"Starting Vector Index pipeline for steps: {args.steps}")

    # A dictionary to map step numbers to their corresponding functions
    pipeline_steps = {
        5: lambda: p5.main(),
        6: lambda: p6.main(),
        7: lambda: p7.main(is_complete_overwrite=args.is_complete_overwrite),
        8: lambda: p8.main(action=args.deploy_action, index_name=args.deploy_index),
        9: lambda: p9.main(query=args.query)
    }

    for step in sorted(args.steps):
        if step in pipeline_steps:
            try:
                logging.info(f"--- Running Step {step} ---")
                pipeline_steps[step]()
                logging.info(f"--- Step {step} completed successfully ---")
            except Exception as e:
                logging.error(f"--- Step {step} failed ---", exc_info=True)
                sys.exit(1) # Exit if a step fails
        else:
            logging.warning(f"Step {step} is not a valid pipeline step. Skipping.")
    
    logging.info("Vector Index pipeline finished.")

if __name__ == "__main__":
    main() 