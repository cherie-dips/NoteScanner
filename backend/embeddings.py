# embeddings.py

from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import chromadb

# Load model once (not inside function → avoids reloading every call)
_model = SentenceTransformer('all-MiniLM-L6-v2')

# Initialize persistent Chroma client (data will be saved in "chroma_storage" folder)
_client = chromadb.PersistentClient(path="chroma_storage")

# Create or get collection (avoids errors if it already exists)
_collection = _client.get_or_create_collection(name="my_collection")

def chunk_and_embed(text: str):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(text)
    embeddings = _model.encode(chunks)

    # Generate unique IDs for chunks (to avoid collisions, use len(collection) + i)
    existing_count = _collection.count()
    ids = [f"doc_{existing_count + i}" for i in range(len(chunks))]

    # Store in ChromaDB
    _collection.add(
        documents=chunks,
        embeddings=embeddings.tolist(),  # convert numpy array → list
        ids=ids
    )

    return list(zip(chunks, embeddings))