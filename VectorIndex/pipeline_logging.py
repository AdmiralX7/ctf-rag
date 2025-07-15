import logging
import sys
import os

def setup_logging():
    """
    Sets up a centralized logger that outputs to both console and a log file
    for the Vector Index pipeline.
    """
    log_file_path = os.path.join(os.path.dirname(__file__), 'vector_index_pipeline.log')

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

    logging.info(f"Logging initialized. Detailed logs will be written to {os.path.abspath(log_file_path)}") 