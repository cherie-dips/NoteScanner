# rag_query.py

import os
from dotenv import load_dotenv
from langchain.chains import RetrievalQA
from langchain_community.vectorstores import Chroma
from langchain_groq import ChatGroq
from embeddings import chunk_and_embed 

load_dotenv()

class CustomEmbedding:
    """Wraps your chunk_and_embed function into an Embeddings-like interface for LangChain."""
    def embed_documents(self, texts):
        # texts is a list of strings
        all_embeddings = []
        for text in texts:
            chunked = chunk_and_embed(text)
            # average all chunk embeddings into one per document
            doc_embedding = sum([emb for _, emb in chunked]) / len(chunked)
            all_embeddings.append(doc_embedding.tolist())  # must be list, not numpy
        return all_embeddings
    
    def embed_query(self, text):
        # For query, just take the first chunk embedding
        chunked = chunk_and_embed(text)
        return chunked[0][1].tolist()

def build_qa():
    # Connect to existing persistent Chroma collection with your embeddings
    db = Chroma(
        collection_name="my_collection",
        persist_directory="chroma_storage",
        embedding_function=CustomEmbedding()  # plug in custom embedding
    )
    retriever = db.as_retriever()

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        raise ValueError("GROQ_API_KEY not found in environment variables")

    llm = ChatGroq(
        api_key=groq_api_key,
        model="llama-3.1-8b-instant"
    )

    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=retriever,
        return_source_documents=True
    )
    return qa_chain

if __name__ == "__main__":
    qa_chain = build_qa()
    query = "Explain Strings and the cardinality of a String over an Alphabet of 0 and 1."
    result = qa_chain({"query": query})
    print("Query:", query)
    print("Answer:", result["result"])
    print("Source Documents:", result["source_documents"])