from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
import os
import chromadb
from sentence_transformers import SentenceTransformer
from langchain.text_splitter import RecursiveCharacterTextSplitter

app = FastAPI()

NOTES_ROOT = "user_notes"
CHROMA_DIR = "backend/chroma_storage"

def ingest_folder(subject: str):
    """Ingest all text files from a subject folder into ChromaDB"""
    folder_path = os.path.join(NOTES_ROOT, subject)
    if not os.path.exists(folder_path):
        return {"error": "Folder does not exist.", "chunks_created": 0}
    
    # Find all .txt files in folder
    extracted_texts = []
    txt_files = []
    for fname in os.listdir(folder_path):
        if fname.endswith('.txt'):
            txt_path = os.path.join(folder_path, fname)
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    text = f.read()
                    if text.strip():  # Only add non-empty text
                        extracted_texts.append(text)
                        txt_files.append(fname)
                        print(f"📄 Loaded text from {fname} ({len(text)} characters)")
            except Exception as e:
                print(f"❌ Error reading {fname}: {str(e)}")
                continue
    
    if not extracted_texts:
        return {"error": "No extracted text found in folder.", "chunks_created": 0}
    
    print(f"🔄 Processing {len(extracted_texts)} text files for subject '{subject}'")
    
    # Chunk the text
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = []
    for text in extracted_texts:
        chunks.extend(splitter.split_text(text))
    
    print(f"📝 Created {len(chunks)} chunks")
    
    # Initialize ChromaDB client
    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    
    # Delete existing collection if it exists
    try:
        client.delete_collection(name=subject)
        print(f"🗑️ Deleted existing collection '{subject}'")
    except:
        print(f"ℹ️ No existing collection to delete for '{subject}'")
    
    # Create new collection
    collection = client.create_collection(name=subject)
    print(f"📁 Created new collection '{subject}'")
    
    # Initialize embedding model
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # Add documents in batches
    batch_size = 50
    total_added = 0
    
    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i:i+batch_size]
        batch_embeddings = model.encode(batch_chunks).tolist()
        batch_ids = [f"{subject}_{i+j}" for j in range(len(batch_chunks))]
        
        print(f"📤 Adding batch {i//batch_size + 1} ({len(batch_chunks)} documents)...")
        collection.add(
            documents=batch_chunks,
            embeddings=batch_embeddings,
            ids=batch_ids
        )
        total_added += len(batch_chunks)
    
    # Verify the documents were added
    final_count = collection.count()
    print(f"📊 Final document count: {final_count}")
    
    return {
        "message": f"Ingested {len(chunks)} chunks for subject '{subject}' into ChromaDB.",
        "chunks_created": len(chunks),
        "files_processed": txt_files,
        "final_count": final_count
    }

@app.post("/ingest_folder")
def ingest_folder_endpoint(subject: str = Form(...)):
    """API endpoint to ingest a folder"""
    return ingest_folder(subject)

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Ingest API server...")
    uvicorn.run(app, host="0.0.0.0", port=8001)