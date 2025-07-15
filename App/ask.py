import argparse
import os
from dotenv import load_dotenv
import vertexai
from vertexai.language_models import TextEmbeddingModel
from google.cloud import aiplatform
from pymongo import MongoClient
import vertexai.generative_models as generative_models

# --- Configuration ---
load_dotenv()
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = "us-central1"
EMBEDDING_MODEL_NAME = "text-embedding-005"
DETAILED_ENDPOINT_NAME = os.getenv("DETAILED_ENDPOINT_NAME", "ctf-detailed-endpoint")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "ctf_writeups_db"
COLLECTION_NAME = "writeups"
GEMINI_MODEL_NAME = "gemini-2.5-pro"


def get_embedding(text: str) -> list[float]:
    """
    Generates a numerical vector embedding for a given text string.

    Args:
        text: The text to embed.

    Returns:
        A list of floats representing the embedding.
    """
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL_NAME)
    embeddings = model.get_embeddings([text])
    return embeddings[0].values

def get_vector_search_neighbors(embedding: list[float], num_neighbors: int = 5) -> list[str]:
    """
    Performs a vector search to find the most similar document chunks.

    Args:
        embedding: The vector embedding of the user's question.
        num_neighbors: The number of neighbors to retrieve.

    Returns:
        A list of matching chunk IDs.
    """
    endpoints = aiplatform.MatchingEngineIndexEndpoint.list(
        filter=f'display_name="{DETAILED_ENDPOINT_NAME}"',
        project=PROJECT_ID,
        location=LOCATION
    )
    if not endpoints:
        raise RuntimeError(f"Endpoint with display name '{DETAILED_ENDPOINT_NAME}' not found.")
    
    endpoint_resource_name = endpoints[0].resource_name
    endpoint = aiplatform.MatchingEngineIndexEndpoint(index_endpoint_name=endpoint_resource_name)
    
    deployed_index_id = endpoint.deployed_indexes[0].id
    
    print(f"Querying endpoint '{endpoint.display_name}' with deployed index '{deployed_index_id}'...")

    response = endpoint.find_neighbors(
        deployed_index_id=deployed_index_id,
        queries=[embedding],
        num_neighbors=num_neighbors
    )
    
    if not response or not response[0]:
        print("No neighbors found.")
        return []
    
    neighbor_ids = [neighbor.id for neighbor in response[0]]
    print(f"Found {len(neighbor_ids)} neighbors.")
    return neighbor_ids

def parse_and_deduplicate_ids(neighbor_ids: list[str]) -> list[str]:
    """
    Parses ctftime_id from chunk IDs and removes duplicates.

    Args:
        neighbor_ids: A list of chunk IDs like '40338_chunk_1'.

    Returns:
        A list of unique string ctftime_ids.
    """
    unique_ids = set()
    for neighbor_id in neighbor_ids:
        # Assuming format 'ctftime_id_chunk_index'
        base_id = neighbor_id.split('_')[0]
        unique_ids.add(base_id)
    return list(unique_ids)

def fetch_documents_from_mongodb(doc_ids: list[str]) -> str:
    """
    Fetches documents from MongoDB and concatenates their text fields.

    Args:
        doc_ids: A list of unique ctftime_ids to fetch.

    Returns:
        A single string containing the concatenated context.
    """
    if not doc_ids:
        return ""

    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        
        # Query for all documents with matching ctftime_id
        documents = collection.find({"ctftime_id": {"$in": doc_ids}})
        
        context = []
        for doc in documents:
            # Reconstruct the context from the original fields
            context_text = f"Title: {doc.get('title', 'N/A')}\n"
            context_text += f"Source: {doc.get('url', 'N/A')}\n"
            context_text += f"Summary: {doc.get('rag_summary', 'N/A')}\n\n"
            context_text += f"Full Write-up:\n{doc.get('rewritten_full_text', 'N/A')}\n---\n"
            context.append(context_text)
            
        print(f"Fetched {len(context)} documents from MongoDB.")
        return "".join(context)
        
    except Exception as e:
        print(f"Error fetching from MongoDB: {e}")
        return ""

def get_document_sources(doc_ids: list[str]) -> list[str]:
    """
    Fetches the source URLs for a given list of document IDs.
    """
    if not doc_ids:
        return []
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        documents = collection.find(
            {"ctftime_id": {"$in": doc_ids}},
            {"url": 1, "title": 1, "_id": 0}
        )
        sources = [doc.get('url') or doc.get('title', 'Unknown Source') for doc in documents]
        return sources
    except Exception as e:
        print(f"Error fetching sources from MongoDB: {e}")
        return []

def get_final_answer(context: str, question: str) -> str:
    """
    Constructs a final prompt and calls the Gemini model to get an answer.
    """
    prompt = f"""
Based on the following context from cybersecurity write-ups, provide a concise answer to the user's question.

Context:
---
{context}
---

Question: {question}
"""
    
    model = generative_models.GenerativeModel(GEMINI_MODEL_NAME)
    
    try:
        response = model.generate_content(
            prompt,
            generation_config=generative_models.GenerationConfig(
                temperature=0.1,
                top_p=0.9,
            )
        )
        return response.text
    except Exception as e:
        print(f"Error generating answer from Gemini: {e}")
        return "Sorry, I was unable to generate an answer."

def main():
    """
    Main function to parse command-line arguments and execute the RAG pipeline.
    """
    parser = argparse.ArgumentParser(description="Query the RAG system with a question.")
    parser.add_argument("question", type=str, help="The question you want to ask.")
    
    args = parser.parse_args()
    
    question = args.question
    
    print(f"Your question is: {question}")
    
    # Generate the embedding for the question
    question_embedding = get_embedding(question)
    print(f"Embedding generated with {len(question_embedding)} dimensions.")
    
    # Find the most relevant document chunks
    neighbor_ids = get_vector_search_neighbors(question_embedding)
    print("Relevant chunk IDs:", neighbor_ids)
    
    # Get the unique document IDs and fetch the full context
    unique_doc_ids = parse_and_deduplicate_ids(neighbor_ids)
    context = fetch_documents_from_mongodb(unique_doc_ids)
    
    if not context:
        print("Could not retrieve context from the database. Aborting.")
        return
        
    # Get the final answer from the LLM
    final_answer = get_final_answer(context, question)
    
    print("\n--- Answer ---")
    print(final_answer)
    print("----------------\n")
    
    # Get and display sources
    sources = get_document_sources(unique_doc_ids)
    if sources:
        print("Sources:")
        for source in sources:
            print(f"- {source}")

if __name__ == "__main__":
    main() 