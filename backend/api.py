from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
import chromadb
from sentence_transformers import SentenceTransformer
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the exact frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NOTES_ROOT = "user_notes"
CHROMA_DIR = "backend/chroma_storage"
os.makedirs(NOTES_ROOT, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)

# Mount static files so images/pdfs can be served
app.mount("/user_notes", StaticFiles(directory=NOTES_ROOT), name="user_notes")

@app.post("/create_folder")
def create_folder(path: str = Form(""), name: str = Form(...)):
    """Create a folder inside the given path."""
    folder_path = os.path.join(NOTES_ROOT, path, name) if path else os.path.join(NOTES_ROOT, name)
    os.makedirs(folder_path, exist_ok=True)
    return JSONResponse({"message": f"Folder '{name}' created.", "path": folder_path})

@app.post("/upload_note")
def upload_note(path: str = Form(...), file: UploadFile = File(...)):
    """Upload a file to a specific folder path and automatically process it."""
    folder_path = os.path.join(NOTES_ROOT, path)
    if not os.path.exists(folder_path):
        return JSONResponse({"error": f"Folder '{path}' does not exist."}, status_code=400)
    
    # Save file
    file_path = os.path.join(folder_path, file.filename)
    with open(file_path, "wb") as f:
        f.write(file.file.read())
    
    # Extract text from the uploaded file
    try:
        from backend.extract_api import extract_text
        from backend.ingest_api import ingest_folder
        
        # Extract text
        subject = path.split('/')[0] if '/' in path else path
        extract_result = extract_text(subject)
        
        if extract_result.get("processed_files"):
            # Ingest the extracted text
            ingest_result = ingest_folder(subject)
            
            return JSONResponse({
                "message": f"File '{file.filename}' uploaded and processed successfully.",
                "path": file_path,
                "extraction": extract_result,
                "ingestion": ingest_result
            })
        else:
            return JSONResponse({
                "message": f"File '{file.filename}' uploaded but no text could be extracted.",
                "path": file_path
            })
    except Exception as e:
        return JSONResponse({
            "message": f"File '{file.filename}' uploaded but processing failed: {str(e)}",
            "path": file_path
        })

@app.get("/list_tree")
def list_tree():
    """Recursively list folders and files as a tree structure."""
    def build_tree(folder):
        children = []
        for item in os.listdir(folder):
            full_path = os.path.join(folder, item)
            rel_path = os.path.relpath(full_path, NOTES_ROOT)
            if os.path.isdir(full_path):
                children.append({
                    "type": "folder",
                    "name": item,
                    "path": rel_path,
                    "children": build_tree(full_path)
                })
            else:
                children.append({
                    "type": "file",
                    "name": item,
                    "path": rel_path
                })
        return children

    return {"tree": build_tree(NOTES_ROOT)}

@app.post("/query_folder")
def query_notes(subject: str = Form(...), query: str = Form(...)):
    """Query notes using RAG"""
    try:
        # Initialize ChromaDB client
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        
        # Get the collection
        try:
            collection = client.get_collection(name=subject)
        except:
            return JSONResponse({
                "error": f"Collection '{subject}' not found. Please ingest documents first.",
                "query": query,
                "answer": "",
                "source_documents": []
            })
        
        # Check if collection has documents
        doc_count = collection.count()
        print(f"[DEBUG] Collection '{subject}' has {doc_count} documents")
        
        if doc_count == 0:
            return JSONResponse({
                "error": f"No documents found for subject '{subject}'. Please upload and process files first.",
                "query": query,
                "answer": "",
                "source_documents": []
            })
        
        # Generate query embedding
        model = SentenceTransformer('all-MiniLM-L6-v2')
        query_embedding = model.encode([query]).tolist()[0]
        
        # Query the collection
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=4
        )
        
        # Convert results to document format
        retrieved_docs = []
        for i, doc_text in enumerate(results['documents'][0]):
            retrieved_docs.append({
                "content": doc_text,
                "metadata": {"id": results['ids'][0][i], "distance": results['distances'][0][i]}
            })
        
        print(f"[DEBUG] Retrieved {len(retrieved_docs)} relevant documents")
        
        # Check for GROQ API key
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            return JSONResponse({
                "error": "GROQ_API_KEY not found in environment variables",
                "query": query,
                "answer": "",
                "source_documents": retrieved_docs
            })
        
        # Create context from retrieved documents
        context = "\n\n".join([doc["content"] for doc in retrieved_docs])
        
        # Initialize LLM
        llm = ChatGroq(
            api_key=groq_api_key,
            model="llama-3.1-8b-instant"
        )
        
        # Create prompt
        prompt = f"""Based on the following context, answer the question. If the answer cannot be found in the context, say so.

Context:
{context}

Question: {query}

Answer:"""
        
        # Get response from LLM
        response = llm.invoke(prompt)
        answer = response.content if hasattr(response, 'content') else str(response)
        
        print(f"[DEBUG] LLM answer: {answer}")
        
        return JSONResponse({
            "query": query,
            "answer": answer,
            "source_documents": retrieved_docs
        })
        
    except Exception as e:
        print(f"[DEBUG] Query processing failed: {str(e)}")
        return JSONResponse({
            "error": f"Query processing failed: {str(e)}",
            "query": query,
            "answer": "",
            "source_documents": []
        })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)