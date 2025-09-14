import os
from dotenv import load_dotenv
from embeddings import chunk_and_embed
from langchain.vectorstores import Chroma
from sentence_transformers import SentenceTransformer
from langchain.text_splitter import RecursiveCharacterTextSplitter

load_dotenv()

def ingest_text(file_path: str):
    model = SentenceTransformer('all-MiniLM-L6-v2')
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    with open(file_path, "r") as f:
        text = f.read()
    chunks = splitter.split_text(text)
    embeddings = model.encode(chunks)
    db = Chroma(
        collection_name="my_collection",
        persist_directory="chroma_storage"
    )
    db.add_texts(chunks, embeddings=embeddings)
    db.persist()
    print(f"Ingested {len(chunks)} chunks into ChromaDB")

if __name__ == "__main__":
    ingest_text("data/sample_note.txt")  # after you OCR → save text here