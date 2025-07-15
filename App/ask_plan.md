# Development Plan for `ask.py`

This plan outlines the steps to build the RAG query pipeline script, `ask.py`. The primary goal is to create a single, sequential script to expedite development, leaving modularization as a potential future improvement.

---

## Phase 1: Setup & Command-Line Interface (~30 minutes)

**Goal:** Create the basic script file and implement a simple command-line interface to accept a user's question.

**Key Tasks:**
1.  Create a new file named `ask.py` inside the `App/` directory.
2.  Import the necessary standard libraries, starting with `argparse` for command-line argument parsing.
3.  Initialize the argument parser.
4.  Define a single positional argument named `question` that will capture the user's query from the command line.
5.  Add a descriptive help message for the script and the `question` argument.
6.  Parse the incoming arguments and store the user's question in a variable.

---

## Phase 2: Integrate Embedding & Vector Search Logic (~1.5 hours)

**Goal:** Convert the user's question into a vector embedding and use it to query the Vertex AI Vector Search index to find the most relevant document chunks.

**Key Tasks:**
1.  **Integrate Embedding Generation:**
    *   **Reference File:** `VectorIndex/6_run_embedding_jobs.py`
    *   Examine this file to understand how to initialize the Vertex AI client and call the `text-embedding-005` model.
    *   Write a function within `ask.py` that takes the question string as input and returns its numerical vector embedding.

2.  **Integrate Vector Search Querying:**
    *   **Reference File:** `VectorIndex/9_test_endpoints.py`
    *   Use this script as a guide for how to connect to your deployed Vector Search Index Endpoint.
    *   Write the logic to take the question's vector embedding and use it in a `find_neighbors` (or equivalent) API call.
    *   Configure the query to return the top 5 most similar results.
    *   The result will be a list of matching chunk IDs. Store these IDs in a variable for the next phase.

---

## Phase 3: Integrate MongoDB Fetch Logic (~1 hour)

**Goal:** Use the chunk IDs returned from Vector Search to retrieve the full, original text content from your MongoDB database.

**Key Tasks:**
1.  **Parse Chunk IDs:**
    *   The chunk IDs from Vector Search are likely formatted like `[mongo_document_id]_[chunk_index]`.
    *   Create a helper function to iterate through the list of returned chunk IDs and parse them to extract the unique MongoDB document IDs. Handle potential duplicates.

2.  **Integrate MongoDB Connection & Retrieval:**
    *   **Reference File:** `VectorIndex/5_prepare_embedding_data.py`
    *   Adapt the MongoDB connection logic from this script to connect to your database within `ask.py`.
    *   Using the unique list of MongoDB document IDs, query your `ctf_writeups` collection to fetch the full documents.
    *   Extract the relevant text fields (e.g., `question`, `steps_taken`, `resolution`, `source_url`) from the retrieved documents.
    *   Concatenate this text into a single string that will serve as the context for the final prompt.

---

## Phase 4: Integrate Final LLM Call & Polish (~1.5 hours)

**Goal:** Construct the final prompt, send it to the Gemini model to get a synthesized answer, and present the result to the user.

**Key Tasks:**
1.  **Construct the Final Prompt:**
    *   Create a prompt template string. It should clearly instruct the model on its task, providing the context first, followed by the user's question.
    *   **Example Structure:** "Based on the following context from cybersecurity write-ups, provide a concise answer to the user's question. \n\nContext:\n---\n{retrieved_context}\n---\n\nQuestion: {user_question}"
    *   Format the final prompt by inserting the context string from Phase 3 and the original question from Phase 1.

2.  **Integrate Gemini API Call:**
    *   **Reference File:** `Intake/3_ai_batch_process.py`
    *   Look at how this file makes calls to the Gemini model.
    *   Adapt this logic to send your newly constructed prompt to the Gemini Flash model.

3.  **Display Final Output:**
    *   Receive the response from the model.
    *   Print the generated text answer cleanly to the console.

4.  **Add Citations & Polish:**
    *   After printing the answer, add a "Sources:" section.
    *   List the `title` or `source_url` from the documents retrieved in Phase 3 to give credit and allow for further reading.
    *   This completes the core functionality.

5.  **Perform End-to-End Testing:**
    *   Run the script from your command line with a test question.
    *   Trace the execution flow from question to answer.
    *   Debug any connection issues, data parsing errors, or API credential problems. 