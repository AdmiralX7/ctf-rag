# RAG-Augmented AI Assistant for CTF Problem-Solving

This project implements a complete, end-to-end data science pipeline to build a Retrieval-Augmented Generation (RAG) assistant specialized for solving Capture-The-Flag (CTF) challenges. The system ingests public CTF write-ups, processes and enriches them with AI, stores them in a sophisticated dual-vector index, and provides a command-line interface for querying the knowledge base.

## Architecture Overview

The system is composed of three main components that work in sequence. The output of each pipeline serves as the input for the next.

1.  **The Intake Pipeline:** This is a fully automated system that finds, cleans, and enriches raw CTF write-ups. It scrapes data from the web, uses AI to rewrite it for clarity and generate summaries, and stores the final structured data in a MongoDB database.

2.  **The Vector Index Pipeline:** This pipeline takes the structured data from MongoDB and prepares it for search. It generates vector embeddings for both high-level summaries and detailed text chunks, then populates a dual-index system in Google Cloud's Vertex AI Vector Search.

3.  **The Query App:** This is the final, user-facing application. It takes a user's question, converts it to a vector, searches the appropriate index for the most relevant context, and then uses that context to generate a precise, evidence-backed answer from a large language model.

## Directory Structure

-   `App/`: Contains the final user-facing application (`ask.py`) for querying the RAG system.
-   `docs/`: Contains detailed documentation for the `Intake` and `VectorIndex` pipelines.
-   `Intake/`: Contains the complete, multi-stage data ingestion and enrichment pipeline, which automates scraping, cleaning, AI processing, and storage.
-   `VectorIndex/`: Contains the scripts for creating, populating, and deploying the dual-vector search indexes on Google Cloud Vertex AI.

## Setup and Installation

### Prerequisites

*   Python 3.12+
*   Docker Desktop (running)
*   Google Cloud SDK (`gcloud` CLI) installed and authenticated.
*   A Google Cloud Project with the Vertex AI API enabled.

### 1. Initial Setup

Clone the repository and set up the Python virtual environment.

```bash
git clone <your-repository-url>
cd <your-repository-name>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start Local Database

Run a local MongoDB instance using Docker. This container will store the processed write-up data.

```bash
docker run -d -p 27017:27017 --name mongo-ctf mongo:latest
```

### 3. Configure Environment Variables

Create a `.env` file in the project root and populate it with your specific configuration.

```env
# GCP Configuration
GCP_PROJECT_ID="your-gcp-project-id"
GCS_BUCKET_NAME="your-gcs-bucket-name" # e.g., "gs://ctf-rag-data"

# Vector Search Index and Endpoint IDs (from your GCP Vertex AI setup)
SUMMARY_INDEX_ID="your-summary-index-id"
DETAILED_INDEX_ID="your-detailed-index-id"
SUMMARY_ENDPOINT_NAME="your-summary-endpoint-display-name" # e.g., "ctf-summary-endpoint"
DETAILED_ENDPOINT_NAME="your-detailed-endpoint-display-name" # e.g., "ctf-detailed-endpoint"

# Optional: MongoDB connection string if not using the local default
# MONGO_URI="mongodb://localhost:27017/"
```

## How to Run the System

The project is run in three distinct stages: data intake, vector indexing, and querying.

### Step 1: Run the Data Intake Pipeline

This process scrapes, cleans, enriches, and stores the CTF write-ups. It is fully automated by a single script.

```bash
python Intake/Main.py
```

This will create a new run directory in `Intake/runs/` containing all the logs and artifacts from the intake process.

### Step 2: Run the Vector Indexing Pipeline

This process takes the data from MongoDB, generates embeddings, and populates the Vertex AI Vector Search indexes. The orchestrator script (`VectorIndex/main.py`) is highly configurable and allows you to run specific steps or the entire pipeline.

**Running the Full Pipeline**

To run all steps from data preparation to endpoint testing in one go, execute:

```bash
python VectorIndex/main.py --steps 5 6 7 8 9
```

**Running Specific Steps and Options**

You can run individual steps and provide additional options for more granular control.

*   **Step 7: Populate Indexes (Append Mode)**
    To append new embeddings to an index without overwriting existing data, use the `--no-overwrite` flag.
    ```bash
    python VectorIndex/main.py --steps 7 --no-overwrite
    ```

*   **Step 8: Deploy or Undeploy Endpoints**
    You can manage index deployments using `--deploy-action` and `--deploy-index`.
    ```bash
    # Undeploy only the detailed index endpoint to save costs
    python VectorIndex/main.py --steps 8 --deploy-action undeploy --deploy-index detailed
    ```

*   **Step 9: Test Endpoints with a Custom Query**
    Use the `--query` argument to test the live endpoints with a specific question.
    ```bash
    python VectorIndex/main.py --steps 9 --query "How does the Log4j vulnerability work?"
    ```

### Step 3: Query the Assistant

Once the pipelines have been run and the index endpoints are live, you can ask questions using `App/ask.py`.

```bash
python App/ask.py "What is the vulnerability in the 'debug-2' pwn challenge?"
```

The script will use the RAG system to find relevant context and generate a response from the language model, citing its sources.

## License

**Private and Confidential**

This software and its source code are the private property of the author. All rights are reserved.

You may not use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, nor permit persons to whom the Software is furnished to do so, for any personal, commercial, or public purpose.

This software is provided "as is", without warranty of any kind, express or implied. 