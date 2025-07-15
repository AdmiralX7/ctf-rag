# Data Intake Pipeline Documentation

This document provides a comprehensive overview of the data intake pipeline, which is designed to scrape, clean, enrich, and store CTF (Capture The Flag) write-ups for a RAG (Retrieval-Augmented Generation) application.

## 1. Objective & Architecture

The primary goal is to build a robust, multi-stage pipeline that systematically gathers raw write-ups from the web and transforms them into high-quality, structured data suitable for AI-driven analysis and retrieval. The pipeline is orchestrated by a main script (`Main.py`) that executes each step in sequence, ensuring that each stage's output becomes the input for the next.

A key architectural feature is the **manifest-driven workflow**. The pipeline generates a `run_manifest.json` file at the start, which tracks the state of each unique write-up URL through the various processing stages. This prevents redundant work, handles complex cases where multiple write-ups share a single source URL, and makes the entire process more resilient and auditable. Each pipeline execution is isolated within a unique, timestamped run directory inside `Intake/runs/`.

## 2. Core Libraries

-   **Web Scraping:** `requests`, `beautifulsoup4`
-   **Content Extraction:** `trafilatura`
-   **AI & Batch Processing:** `google-cloud-aiplatform` (Vertex AI)
-   **Database:** `pymongo`
-   **Orchestration & Logging:** `subprocess`, `argparse`, `logging`

## 3. The Data Intake Pipeline

The pipeline is orchestrated by `Intake/Main.py`, which executes the following four scripts in order for each run.

---

### Step 1: Scrape Data & Generate Manifest (`1_scrapper.py`)

This script initiates the pipeline by scanning `ctftime.org` for new write-ups, gathering metadata, and creating a master plan for the run.

-   **Input:** The starting URL for CTFtime write-ups.
-   **Core Actions:**
    1.  **Check Existing Data:** Connects to the MongoDB database and reads a local log (`rejected_ids.log`) to compile a set of IDs that should be skipped, preventing re-processing.
    2.  **Scrape Metadata:** Iterates through `ctftime.org` summary pages to gather metadata for each write-up, including its `ctftime_id`, `event_name`, `task_name`, and the link to the original source.
    3.  **Group by Source URL:** Instead of processing each write-up individually, it groups all tasks by their unique source URL. This is crucial for efficiently handling cases where a single blog post contains write-ups for multiple challenges.
    4.  **Fetch Content:** For each unique source URL, it downloads the raw HTML content **once**. It also downloads the HTML from the CTFtime summary page itself in case the original link is dead.
    5.  **Generate Manifest:** The script's primary output is the `run_manifest.json` file. This file contains a dictionary where each key is a unique source URL, and the value contains the path to the scraped HTML and a list of all associated CTF tasks.
-   **Output:**
    -   Raw HTML files stored in a run-specific directory (e.g., `Intake/runs/RUN_ID/output/`).
    -   The `run_manifest.json` file that will orchestrate the rest of the pipeline.

---

### Step 2: Clean and Filter Content (`2_process_trafilatura.py`)

This script processes the raw HTML files, extracting the core article content and filtering out low-quality pages.

-   **Input:** The `run_manifest.json` file and the raw HTML files from Step 1.
-   **Core Actions:**
    1.  **Read Manifest:** The script is driven entirely by the manifest, processing only URLs with the status `"scraped"`.
    2.  **Extract Content:** It uses the `trafilatura` library to extract the main article content from the HTML, converting it to clean Markdown. This effectively removes ads, navigation bars, and other boilerplate.
    3.  **Quality Check:** It performs a quality check on the extracted text. If the content length is less than a minimum threshold (300 characters), it's flagged as `"low_quality"`.
    4.  **Update Manifest:** After processing, it updates the status of each URL in the manifest to `"cleaned"` or `"rejected_low_quality"` and adds a path to the new cleaned text file.
-   **Output:**
    -   Cleaned text files stored in `Intake/runs/RUN_ID/processed_trafilatura/`.
    -   An updated `run_manifest.json` with the new status and file paths.

---

### Step 3: AI Batch Processing (`3_ai_batch_process.py`)

This is the core AI enrichment step, where the cleaned text is sent to Google's Generative Models via Vertex AI Batch Prediction for transformation.

-   **Input:** The updated `run_manifest.json` and the cleaned text files from Step 2.
-   **Core Actions:**
    1.  **Prepare AI Requests:** The script reads the manifest and, for each task, prepares three distinct, context-aware AI prompts. It injects the specific `event_name` and `task_name` into the prompts to help the AI focus on the correct part of the text.
    2.  **Execute 3-Stage Batch Jobs:** It launches three parallel Vertex AI `BatchPredictionJob` instances:
        -   **Job 1: Rewrite (`gemini-2.5-pro`):** Rewrites the full text for clarity, structure, and readability.
        -   **Job 2: Summarize (`gemini-2.5-flash`):** Creates a concise, RAG-optimized summary of the rewritten text.
        -   **Job 3: Generate Keywords (`gemini-2.5-flash`):** Analyzes the rewritten text to produce a structured JSON array of relevant technical keywords.
    3.  **Process Results:** After the batch jobs complete, the script downloads the results, parses the AI-generated content, and merges it with the original metadata for each `ctftime_id`.
-   **Output:**
    -   Structured JSON files (one per `ctftime_id`) containing all original and AI-generated data, stored in `Intake/runs/RUN_ID/ai_processed/`.
    -   Detailed logs and raw request/response data for debugging, stored in `Intake/runs/RUN_ID/raw_requests/` and `raw_ai_processed/`.

---

### Step 4: Store in Database (`4_store_in_db.py`)

This final script takes the fully processed, structured JSON data and loads it into the central MongoDB database.

-   **Input:** The structured JSON files from the `ai_processed` directory.
-   **Core Actions:**
    1.  **Connect to DB:** Establishes a connection to the local MongoDB instance.
    2.  **Upsert Documents:** It iterates through each JSON file and performs an "upsert" operation into the `writeups` collection. Using the `ctftime_id` as the unique key, it updates the document if it already exists or inserts it if it's new. This makes the operation idempotent and safe to re-run.
-   **Output:** The final, enriched data is securely stored in the `ctf_writeups_db.writeups` collection, ready for the Vector Index pipeline. 