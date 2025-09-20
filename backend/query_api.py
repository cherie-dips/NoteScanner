from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
import os
import chromadb
from sentence_transformers import SentenceTransformer
from langchain_groq import ChatGroq

app = FastAPI()
CHROMA_DIR = "backend/chroma_storage"

@app.post("/query_folder")
def query_folder(subject: str = Form(...), query: str = Form(...)):
    """Query documents in a subject folder using RAG"""
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
    print("🚀 Starting Query API server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)