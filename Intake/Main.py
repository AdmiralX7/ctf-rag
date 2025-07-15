import subprocess
import sys
import os
import logging
import time
from pipeline_logging import setup_logging

# --- List of scripts to run in order ---
# These paths are relative to the project root where Main.py is expected to be run from.
SCRIPTS_TO_RUN = [
    "Intake/1_scrapper.py",
    "Intake/2_process_trafilatura.py",
    "Intake/3_ai_batch_process.py",
    "Intake/4_store_in_db.py",
]

def run_script(script_name, run_path):
    """Runs a Python script, passing the run_path as an argument."""
    logging.info(f"--- Running {script_name} ---")
    try:
        # Pass the run_path to the script as a command-line argument.
        result = subprocess.run(
            [sys.executable, script_name, run_path],
            check=True,
            text=True,
            encoding='utf-8'
        )
        logging.info(f"--- {script_name} finished successfully ---\n")
        return True
            
    except FileNotFoundError:
        logging.critical(f"Fatal Error: Could not find the script '{script_name}'.")
        return False
    except subprocess.CalledProcessError as e:
        logging.critical(f"--- {script_name} failed with return code {e.returncode} ---")
        return False
    except Exception as e:
        logging.critical(f"An unexpected error occurred while running {script_name}: {e}")
        return False

def main():
    """Main orchestrator for the project."""
    
    # --- 1. Setup Run-Specific Environment ---
    # Generate a human-readable, sortable timestamp for the run ID
    run_id = time.strftime("%Y-%m-%d_%H-%M-%S") + "_run"
    run_path = os.path.join("Intake", "runs", run_id)
    
    # Create all necessary directories for the run
    # These paths are now relative to the project root
    os.makedirs(os.path.join(run_path, "output"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "processed_trafilatura"), exist_ok=True)
    os.makedirs(os.path.join(run_path, "ai_processed"), exist_ok=True)

    # --- 2. Initialize Logging for this Run ---
    # The logger will now create its log file inside the run_path
    setup_logging(run_path)
    
    logging.info(f">>> Starting CTF Processing Pipeline. RUN_ID: {run_id} <<<")
    
    # --- 3. Execute Pipeline Scripts ---
    for script in SCRIPTS_TO_RUN:
        success = run_script(script, run_path)
        if not success:
            logging.critical(">>> Pipeline halted due to a critical error in a sub-script. <<<")
            sys.exit(1)

    logging.info(f">>> All pipeline scripts for RUN_ID: {run_id} executed successfully! <<<")

if __name__ == "__main__":
    main() 