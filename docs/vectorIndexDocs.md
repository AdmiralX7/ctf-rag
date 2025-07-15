# Vector Index Pipeline Documentation

This document provides a comprehensive overview of the Vector Indexing pipeline, which is designed to create, populate, and manage a hybrid, two-stage vector retrieval system for a sophisticated RAG (Retrieval-Augmented Generation) application.

## 1. Objective & Architecture

The primary goal is to build a dual-index system using **Vertex AI Vector Search** to enhance search capabilities:

1.  **Summary Index (Macro Search):** Contains embeddings of concise `rag_summary` texts from each document. This index is optimized for fast, high-level retrieval to quickly identify relevant documents.
2.  **Detailed Index (Micro Search):** Contains embeddings of overlapping text chunks from the `rewritten_full_text` of each document. This index is used for fine-grained, precise lookups to find specific answers and passages within the most relevant documents.

The pipeline leverages **Vertex AI Batch Prediction** for scalable embedding generation, ensuring it can handle large datasets without running into rate limits. All artifacts are managed within a dedicated Google Cloud Storage bucket.

## 2. Core Libraries

The pipeline is built using the following key Python libraries:

-   **Database:** `pymongo` for connecting to and retrieving data from MongoDB.
-   **Cloud Infrastructure:** `google-cloud-aiplatform` and `google-cloud-storage` for interacting with Vertex AI and GCS.
-   **Text Processing:** `tiktoken` for accurately chunking text based on token counts.
-   **Configuration:** `python-dotenv` for managing environment variables.
-   **Orchestration:** `argparse` and `threading` for creating a configurable, multi-threaded pipeline.

## 3. The Vector Index Pipeline

The pipeline is broken down into five distinct, sequential steps, orchestrated by a master script.

---

### Step 1: Prepare Data for Embedding (`5_prepare_embedding_data.py`)

This script is the starting point of the pipeline. It connects to the source MongoDB database, extracts the necessary text, processes it, and stages it in Google Cloud Storage for the next step.

-   **Input:** Documents stored in the `ctf_writeups_db.writeups` MongoDB collection.
-   **Core Actions:**
    1.  **Fetch Data:** Establishes a connection to MongoDB and retrieves all write-up documents.
    2.  **Process Summaries:** For each document, it extracts the `rag_summary` and formats it into a JSON object: `{"id": "ctftime_id", "content": "rag_summary"}`. The key `content` is required by the Vertex AI batch prediction service.
    3.  **Process Detailed Chunks:** It takes the `rewritten_full_text` and uses the `tiktoken` library to split it into chunks of approximately **500 tokens**. A **15% overlap** (75 tokens) is implemented between chunks to maintain semantic context across them. Each chunk is formatted as: `{"id": "ctftime_id_chunk_N", "content": "chunk_text"}`.
-   **Output:**
    1.  Two JSONL files uploaded to GCS: `gs://ctf-rag/5_prepare_embedding_data/summaries.jsonl` and `gs://ctf-rag/5_prepare_embedding_data/detailed_chunks.jsonl`.
    2.  A local manifest file (`VectorIndex/output/embedding_input_manifest.json`) containing the GCS URIs of the two JSONL files, which serves as the input for the next step.

---

### Step 2: Run Batch Embedding Jobs (`6_run_embedding_jobs.py`)

This script takes the prepared data from GCS and uses Vertex AI's scalable batch processing capabilities to generate vector embeddings for all text.

-   **Input:** The `embedding_input_manifest.json` file created in the previous step.
-   **Core Actions:**
    1.  **Launch Parallel Jobs:** It initiates two parallel Vertex AI `BatchPredictionJob` instances using Python's `ThreadPoolExecutor`. One job processes the summaries, and the other processes the detailed chunks.
    2.  **Model Configuration:** It uses a specified embedding model (e.g., `text-embedding-005`) from the Google Cloud publisher model repository.
    3.  **Monitor Jobs:** The script waits for both batch jobs to complete successfully before proceeding.
-   **Output:**
    1.  Two new GCS directories containing the embedding results in JSONL format.
    2.  A new local manifest file (`VectorIndex/output/embedding_output_manifest.json`) containing the GCS URIs of the two output directories.

---

### Step 3: Populate Vector Indexes (`7_populate_indexes.py`)

With the embeddings generated, this script populates the two Vector Search indexes. It includes a critical transformation step to reformat the batch job output into the structure required by the Vector Search API.

-   **Input:** The `embedding_output_manifest.json` file from the previous step and two environment variables (`SUMMARY_INDEX_ID`, `DETAILED_INDEX_ID`) pointing to pre-created, empty Vector Search indexes.
-   **Core Actions:**
    1.  **Transform Data:** The script downloads the raw prediction output, which has a complex structure. It parses these files and extracts the necessary fields, creating clean JSONL files with the format `{"id": "...", "embedding": [...]}`. These clean files are uploaded to a new GCS location (`gs://ctf-rag/7_populate_indexes/...`).
    2.  **Populate Indexes:** It initiates two parallel `update_embeddings` operations, one for each index, pointing to the corresponding GCS directory of cleaned embeddings.
    3.  **Update Strategy:** The script supports both complete overwrites (`--is_complete_overwrite=True`, the default) and incremental updates (appending) to the indexes, controlled via a command-line flag.
-   **Output:** The two Vector Search indexes are populated with the embeddings. The process runs asynchronously in the cloud, and the script confirms its initiation.

---

### Step 4: Deploy & Manage Index Endpoints (`8_deploy_indexes.py`)

This script makes the populated indexes queryable by deploying them to public-facing endpoints. It is designed to be idempotent and includes cost-management features.

-   **Input:** The populated Vector Search indexes. The script is controlled via command-line arguments: `--action` (`deploy` or `undeploy`) and `--index` (`summary`, `detailed`, or `all`).
-   **Core Actions:**
    1.  **Deploy:**
        -   It checks if an endpoint with the specified name (e.g., `ctf-summary-endpoint`) exists. If not, it creates one.
        -   It deploys the specified index to the endpoint with a unique, timestamped deployment ID to avoid conflicts.
    2.  **Undeploy:**
        -   It finds all deployments of the specified index on its endpoint.
        -   It undeploys each one.
        -   **Cost-Saving:** After undeploying, it checks if the endpoint is empty. If so, the script automatically deletes the endpoint to prevent incurring unnecessary costs.
-   **Output:** Live, queryable `MatchingEngineIndexEndpoint` instances for the specified indexes, or confirmation of their teardown.

---

### Step 5: Test Index Endpoints (`9_test_endpoints.py`)

This final script serves as a simple health check to verify that the deployed endpoints are live and returning results as expected.

-   **Input:** Live, deployed endpoints.
-   **Core Actions:**
    1.  **Get Endpoints:** The script fetches the deployed `summary` and `detailed` endpoints by their display names. It contains logic to handle recent SDK changes, ensuring the endpoint objects are fully initialized.
    2.  **Generate Query Embedding:** It takes a sample query string (e.g., "What is Log4j?"), generates an embedding for it using the same model as the pipeline.
    3.  **Query Endpoints:** It sends a `find_neighbors` request to each endpoint independently.
-   **Output:** The script prints the top 3 results (neighbor IDs and distances) from both the Summary Index and the Detailed Index to the console, confirming that the entire pipeline was successful and the system is ready for use. 