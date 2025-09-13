# rag_query.py
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.schema import Document
from langchain.chains import RetrievalQA
from langchain.vectorstores.redis import Redis

def build_qa():
    embeddings = OpenAIEmbeddings()
    vectorstore = Redis(
        redis_url="redis://localhost:6379",
        index_name="notes_idx",
        embedding=embeddings
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3})
    llm = ChatOpenAI(model="gpt-4o-mini")
    qa = RetrievalQA.from_chain_type(llm=llm, retriever=retriever)
    return qa

if __name__ == "__main__":
    qa = build_qa()
    query = "Explain Alphabet from my notes"
    print(qa.run(query))