import requests
from bs4 import BeautifulSoup
import time
import os
import csv
import json
import logging
import sys
import random
from pymongo import MongoClient
from urllib.parse import urlparse, urlunparse
from pipeline_logging import setup_logging

# --- Configuration ---
MAX_PAGES_TO_SCRAPE = -1      # Set to -1 to disable page limit
MAX_WRITEUPS_TO_SCRAPE = 500   # Set to -1 to disable write-up limit

# --- Constants ---
BASE_URL = "https://ctftime.org"
WRITEUPS_URL = f"{BASE_URL}/writeups"
# Global log, not run-specific
REJECTED_IDS_LOG = "Intake/rejected_ids.log"
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "ctf_writeups_db"
COLLECTION_NAME = "writeups"

def get_existing_ids_from_db():
    """Fetches a set of all ctftime_ids from the MongoDB collection."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        # The ismaster command is cheap and does not require auth.
        client.admin.command('ismaster')
        logging.info("Successfully connected to MongoDB to fetch existing IDs.")
        # Fetch all documents and extract the 'ctftime_id' field
        ids = {doc['ctftime_id'] for doc in collection.find({}, {'ctftime_id': 1})}
        client.close()
        return ids
    except Exception as e:
        logging.warning(f"Could not connect to MongoDB to fetch existing IDs. Proceeding without them. Error: {e}")
        return set()

def get_rejected_ids():
    """Reads the rejected IDs log and returns a set of IDs."""
    if not os.path.exists(REJECTED_IDS_LOG):
        return set()
    try:
        with open(REJECTED_IDS_LOG, 'r') as f:
            ids = set()
            for line in f:
                # Remove comments and then strip whitespace
                line_content = line.split('#', 1)[0].strip()
                if line_content:
                    try:
                        ids.add(int(line_content))
                    except ValueError:
                        logging.warning(f"Could not parse integer from line: '{line.strip()}'")
            return ids
    except IOError as e:
        logging.error(f"Error reading rejected IDs log: {e}")
        return set()

# --- Main Scraper Logic ---

def get_soup(url):
    """Makes a request to a URL and returns a BeautifulSoup object."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return None

def scrape_summary_page(url):
    """
    Scrapes a CTFtime write-up summary page for details, including the
    external URL and any embedded content.
    """
    logging.debug(f"Scraping summary page: {url}")
    soup = get_soup(url)
    if not soup:
        return None

    # --- Metadata Extraction ---
    breadcrumb = soup.find('ul', class_='breadcrumb')
    if breadcrumb:
        event_name_tag = breadcrumb.find('a', href=lambda href: href and href.startswith('/event/'))
        event_name = event_name_tag.text.strip() if event_name_tag else "Unknown Event"
        task_name_tag = breadcrumb.find('a', href=lambda href: href and href.startswith('/task/'))
        task_name = task_name_tag.text.strip() if task_name_tag else "Unknown Task"
    else:
        event_name = "Unknown Event"
        task_name = "Unknown Task"

    # --- Source Content Extraction ---
    # Find the link to the original writeup (might not exist)
    original_writeup_tag = soup.find('a', string=lambda text: text and 'Original writeup' in text)
    original_url = original_writeup_tag['href'] if original_writeup_tag and original_writeup_tag.has_attr('href') else None

    # Find embedded content on the page (might not exist)
    embedded_content_div = soup.find('div', class_='well')
    embedded_html = str(embedded_content_div) if embedded_content_div else None

    # --- Validation ---
    if not original_url and not embedded_html:
        logging.warning("Could not find an external link or embedded content. Skipping.")
        return None
        
    # Scrape Tags
    tags_div = soup.find('div', class_='tags')
    tags = [a.text for a in tags_div.find_all('a')] if tags_div else []
    
    # Scrape Rating
    rating_div = soup.find('div', class_='rating')
    rating = float(rating_div.text.strip()) if rating_div and rating_div.text.strip() else None

    logging.debug(f"Found Original URL: {original_url}")
    logging.debug(f"Found Embedded Content: {'Yes' if embedded_html else 'No'}")
    logging.debug(f"Found Tags: {tags}")
    logging.debug(f"Found Rating: {rating}")
    logging.debug(f"Found Event: {event_name}")
    logging.debug(f"Found Task: {task_name}")

    return {
        "original_url": original_url,
        "embedded_html": embedded_html,
        "tags": tags,
        "rating": rating,
        "event_name": event_name,
        "task_name": task_name
    }

def fetch_original_content(url):
    """Fetches the raw HTML content from the original writeup URL."""
    logging.debug(f"Fetching final content from: {url}")
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        # We don't parse, just return the raw text
        return response.text
    except requests.exceptions.RequestException as e:
        logging.error(f"Could not fetch final content: {e}")
        return None

def main():
    """Main function to orchestrate the scraping process."""
    if len(sys.argv) < 2:
        print("FATAL: Missing run_path argument. This script should be called from Main.py.")
        sys.exit(1)
    run_path = sys.argv[1]
    
    # Use the run_path to set up a run-specific logger
    setup_logging(run_path)
    
    # This script now writes to a subdirectory within the unique run folder
    output_dir = os.path.join(run_path, "output")
    os.makedirs(output_dir, exist_ok=True)


    logging.info("--- Starting Step 1: Scraper ---")

    # --- Get IDs to Skip (from global sources) ---
    db_ids = get_existing_ids_from_db()
    rejected_ids = get_rejected_ids()
    skip_ids = db_ids.union(rejected_ids)
    logging.info(f"Found {len(db_ids)} existing IDs in the database.")
    logging.info(f"Found {len(rejected_ids)} rejected IDs in the log.")
    logging.info(f"Total IDs to skip: {len(skip_ids)}")
    
    # --- New: Manifest-driven workflow ---
    # This dictionary will group tasks by their unique source URL
    url_to_tasks_map = {}

    # --- Start Scraping ---
    page = 1
    scraped_count = 0
    stop_scraping = False

    while True:
        # Check if we should stop due to page limit
        if MAX_PAGES_TO_SCRAPE != -1 and page > MAX_PAGES_TO_SCRAPE:
            logging.info(f"Reached page limit of {MAX_PAGES_TO_SCRAPE}. Stopping.")
            break

        logging.info(f"--- Scraping Page {page} ---")
        main_page_url = f"{WRITEUPS_URL}?page={page}"
        soup = get_soup(main_page_url)
        if not soup:
            break

        # Find the main write-ups table
        table = soup.find('table', class_='table')
        if not table:
            logging.warning("Could not find the write-ups table. Ending.")
            break

        rows = table.find('tbody').find_all('tr')
        if not rows:
            logging.info("No more write-ups found on this page. Ending.")
            break

        # Extract the link to the summary page from each row
        for row in rows:
            # Check if we should stop due to write-up limit
            if MAX_WRITEUPS_TO_SCRAPE != -1 and scraped_count >= MAX_WRITEUPS_TO_SCRAPE:
                logging.info(f"Reached write-up limit of {MAX_WRITEUPS_TO_SCRAPE}. Stopping.")
                stop_scraping = True
                break

            # Add a base delay plus a random fraction to avoid hammering the server
            # and to mimic more human-like browsing patterns.
            delay = 1 + (random.random() * 0.25)
            logging.debug(f"Waiting for {delay:.2f} seconds before next request...")
            time.sleep(delay)

            # The link is in the 5th column (index 4)
            cells = row.find_all('td')
            if len(cells) < 5:
                continue

            action_cell = cells[4]
            summary_link_tag = action_cell.find('a')
            if not summary_link_tag or not summary_link_tag.has_attr('href'):
                continue

            # Step 1: Get summary URL and ID
            summary_path = summary_link_tag['href']
            ctftime_id = int(summary_path.split('/')[-1])
            
            # --- Main Skip Logic ---
            if ctftime_id in skip_ids:
                logging.debug(f"ID {ctftime_id} is in skip list. Skipping.")
                continue

            summary_url = BASE_URL + summary_path

            # Step 2: Scrape the summary page to get details
            details = scrape_summary_page(summary_url)
            if not details:
                logging.warning(f"Could not get details for ID {ctftime_id} (no sources found), skipping.")
                continue

            # --- New: Determine the primary source URL for grouping ---
            # If an external link exists, use it. Otherwise, use the CTFtime summary page URL.
            # This becomes the key for our url_to_tasks_map.
            primary_source_url = details.get("original_url") or summary_url
            
            # Deconstruct the URL to remove the fragment for grouping
            parsed_url = urlparse(primary_source_url)
            # Use the URL without the fragment as the group key.
            # Keep the original full URL inside the task info.
            base_url_group_key = urlunparse(parsed_url._replace(fragment=""))

            task_info = {
                "ctftime_id": ctftime_id,
                "event_name": details["event_name"],
                "task_name": details["task_name"],
                # Store both sources. The main loop will decide what to do with them.
                "original_url": details.get("original_url"),
                "embedded_html": details.get("embedded_html"),
            }

            if base_url_group_key not in url_to_tasks_map:
                url_to_tasks_map[base_url_group_key] = {"tasks": []}
            url_to_tasks_map[base_url_group_key]["tasks"].append(task_info)

            # Keep track of the total number of valid writeups found
            scraped_count += 1
            logging.info(f"Found writeup for ID {ctftime_id} ({details['task_name']}). Total found: {scraped_count}")

        # Break the outer loop if the inner loop was stopped
        if stop_scraping:
            break
        page += 1
        # Add a delay between pages to be polite
        time.sleep(1)

    logging.info(f"--- Finished gathering tasks. Found {len(url_to_tasks_map)} unique source URLs. ---")

    # --- New: Filter out multi-task URLs for manual review ---
    manifest_urls = {}
    manual_review_log_path = os.path.join(run_path, "manual_review.log")
    
    # Use a set to track logged URLs to avoid duplicate log entries on re-runs
    logged_urls = set()

    with open(manual_review_log_path, 'a') as f_manual:
        # Check if the file is new to write a header
        is_new_file = f_manual.tell() == 0
        if is_new_file:
            f_manual.write(f"--- Log for run starting at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n\n")
        
        for url, data in url_to_tasks_map.items():
            tasks = data['tasks']
            # If a base URL has more than one task, log it for manual review.
            if len(tasks) > 1:
                if url not in logged_urls:
                    logging.warning(f"URL '{url}' has {len(tasks)} tasks. Logging for manual review and skipping.")
                    f_manual.write(f"URL: {url}\n")
                    for task in tasks:
                        f_manual.write(f"  - ID: {task['ctftime_id']}, Task: '{task['task_name']}', Original URL: {task['original_url']}\n")
                    f_manual.write("\n")
                    logged_urls.add(url)
            else:
                # This URL is good to go for automated processing.
                manifest_urls[url] = data

    logging.info(f"Filtered out {len(logged_urls)} URLs for manual review. See '{manual_review_log_path}'.")
    logging.info(f"{len(manifest_urls)} URLs will be processed.")

    # --- Scrape and save content for the filtered URLs ---
    run_manifest = {
        "run_id": os.path.basename(run_path),
        "start_time": time.strftime('%Y-%m-%d %H:%M:%S'),
        "processed_urls": {},
        "failed_urls": {}
    }

    # Use the filtered 'manifest_urls' dictionary for processing
    for url, data in manifest_urls.items():
        logging.info(f"Processing source: {url}")
        
        # Since we filtered for len(tasks) == 1, we can get the first task.
        task = data['tasks'][0]
        ctftime_id = task['ctftime_id']
        
        # --- New: Fetch/Save logic for multiple sources ---
        has_content = False
        saved_sources = [] # To track what we saved

        # Try to get external content
        external_url = task.get("original_url")
        if external_url:
            html_content = fetch_original_content(external_url)
            if html_content:
                html_filename = os.path.join(output_dir, f"{ctftime_id}.html")
                with open(html_filename, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                saved_sources.append("external")
                has_content = True
            else:
                logging.warning(f"Failed to fetch external content from {external_url} for ID {ctftime_id}.")

        # Try to get embedded content
        embedded_html = task.get("embedded_html")
        if embedded_html:
            summary_filename = os.path.join(output_dir, f"{ctftime_id}.summary.html")
            with open(summary_filename, 'w', encoding='utf-8') as f:
                f.write(embedded_html)
            saved_sources.append("embedded")
            has_content = True
        
        # --- End New Fetch/Save Logic ---

        if has_content:
            # Save metadata if we successfully got at least one piece of content
            meta_filename = os.path.join(output_dir, f"{ctftime_id}.meta.json")
            # We must remove the large HTML content before saving the metadata
            task.pop("embedded_html", None) 
            with open(meta_filename, 'w', encoding='utf-8') as f:
                json.dump(task, f, indent=4)
            
            # Update manifest for this successful URL
            run_manifest["processed_urls"][url] = {
                "status": "scraped",
                "tasks": data['tasks']
            }
            
            # New, more descriptive log message
            log_message = f"Successfully processed ID {ctftime_id}."
            if "external" in saved_sources and "embedded" in saved_sources:
                log_message += " Saved both external and embedded content."
            elif "external" in saved_sources:
                log_message += " Saved external content."
            elif "embedded" in saved_sources:
                log_message += " Saved embedded content."
            logging.info(log_message)
        else:
            # Update manifest for this failed URL
            run_manifest["failed_urls"][url] = {
                "status": "failed_scrape",
                "tasks": data['tasks']
            }
            logging.warning(f"Failed to scrape any content for ID {ctftime_id} from source {url}.")
        
        # Be polite to the server
        time.sleep(1)

    # --- Save the run manifest ---
    manifest_path = os.path.join(run_path, "run_manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump(run_manifest, f, indent=4)

    logging.info(f"--- Step 1: Scraper Finished. Manifest saved to {manifest_path} ---")

if __name__ == "__main__":
    main() 