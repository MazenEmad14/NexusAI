import asyncio
from src.stores.vector.providers.chroma import ChromaDBProvider
import os
from dotenv import load_dotenv

load_dotenv('src/.env')

async def main():
    try:
        vector_db = ChromaDBProvider("test_tenant")
        vector_store = vector_db.get_vector_store()
        
        query = "Egypt Versus Argentina match in world cup 2026"
        print(f"Searching for: {query}")
        
        docs = vector_store.similarity_search(query, k=5)
        print(f"Found {len(docs)} documents")
        
        for i, doc in enumerate(docs):
            print(f"\n--- Doc {i+1} ---")
            print(f"Content: {doc.page_content}")
            print(f"Metadata: {doc.metadata}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
