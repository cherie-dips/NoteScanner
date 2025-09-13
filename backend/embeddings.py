# embeddings.py
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings

def chunk_and_embed(text: str):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(text)

    embeddings = OpenAIEmbeddings()
    vectors = [embeddings.embed_query(chunk) for chunk in chunks]

    return list(zip(chunks, vectors))