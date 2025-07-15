import logging
import sys
import os

def setup_logging(run_path=None):
    """
    Sets up a centralized logger that outputs to both console and a log file.
    If a run_path is provided, the log file will be created inside that directory.
    Otherwise, it defaults to the project root.
    """
    if run_path:
        log_file_path = os.path.join(run_path, 'pipeline.log')
    else:
        # Fallback for scripts run standalone for debugging
        log_file_path = 'pipeline.log'

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Set the lowest level to capture everything

    # Prevent the logger from being configured multiple times by clearing old handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # --- File Handler ---
    # Captures everything, including DEBUG messages
    file_handler = logging.FileHandler(log_file_path, mode='a') # Append mode
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # --- Console Handler ---
    # Captures only INFO and higher level messages
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logging.info(f"Logging initialized. Detailed logs for this run will be written to {os.path.abspath(log_file_path)}") 