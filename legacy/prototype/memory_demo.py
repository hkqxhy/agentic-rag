from adjustment.openai import ChatOpenAI
from langchain.chains import ConversationChain

llm = ChatOpenAI(
    model_name='Qwen',
    openai_api_base='http://localhost:8001/v1',
    openai_api_key='EMPTY',
    streaming=False,
)

def conversation_buffer_memory():
    from langchain.memory import ConversationBufferMemory
    memory = ConversationBufferMemory(return_messages=True)
    chain = ConversationChain(
        llm = llm,
        memory = memory,
        verbose = True
    )

    while True:
        msg = input(">> ")
        print(chain.predict(input=msg))

def entity_memory():
    from langchain.memory import ConversationEntityMemory
    from langchain.memory.prompt import ENTITY_MEMORY_CONVERSATION_TEMPLATE

    memory = ConversationEntityMemory(llm=llm, return_messages=True)
    chain = ConversationChain(
        llm = llm,
        prompt = ENTITY_MEMORY_CONVERSATION_TEMPLATE,
        memory = memory,
        verbose = True
    )

    while True:
        msg = input(">> ")
        print(chain.predict(input=msg))

def knowledge_graph_memory():
    from langchain.prompts import PromptTemplate
    from langchain.memory import ConversationKGMemory

    prompt = PromptTemplate.from_template("""
The following is a friendly conversation between a human and an AI. The AI is talkative and provides lots of specific details from its context.
If the AI does not know the answer to a question, it truthfully says it does not know. The AI ONLY uses information contained in the "Relevant Information" section and does not hallucinate.

Relevant Information:

{history}

Conversation:
Human: {input}
AI:
""")
    memory = ConversationKGMemory(llm=llm, return_messages=True)
    chain = ConversationChain(
        llm = llm,
        prompt = prompt,
        memory = memory,
        verbose = True
    )

    while True:
        msg = input(">> ")
        print(chain.predict(input=msg))

def summary_memory():
    from langchain.memory import ConversationSummaryMemory
    memory = ConversationSummaryMemory(llm=llm, return_messages=True)
    chain = ConversationChain(
        llm = llm,
        memory = memory,
        verbose = True
    )

    while True:
        msg = input(">> ")
        print(chain.predict(input=msg))

def conversation_summary_memory():
    from langchain.memory import ConversationSummaryBufferMemory
    memory = ConversationSummaryBufferMemory(
        llm = llm,
        max_token_limit = 512,
        return_messages = True
    )
    chain = ConversationChain(
        llm = llm,
        memory = memory,
        verbose = True
    )

    while True:
        msg = input(">> ")
        print(chain.predict(input=msg))

def rag_memory():
    from adjustment.embeddings import ModelScopeEmbeddings
    from langchain_community.vectorstores.faiss import FAISS
    from langchain.memory import VectorStoreRetrieverMemory

    # nlp_gte_sentence-embedding_chinese-base 模型向量维度为 728，可以接收 512 长度以下的文本
    embedding_model = ModelScopeEmbeddings(
        model_id="D:/Data/Models/nlp_gte_sentence-embedding_chinese-base",
        sequence_length=512
    )

    vectorstore = FAISS.from_texts(
        texts = [""],
        embedding = embedding_model,
        metadatas = [{}],
        ids = [0]
    )
    retriever = vectorstore.as_retriever(search_kwargs=dict(k=5, fetch_k=20))
    memory = VectorStoreRetrieverMemory(retriever=retriever, return_messages=True)

    chain = ConversationChain(
        llm = llm,
        memory = memory,
        verbose = True
    )

    while True:
        msg = input(">> ")
        print(chain.predict(input=msg))

if __name__ == '__main__':
    # conversation_buffer_memory()
    # entity_memory()
    # knowledge_graph_memory()
    # summary_memory()
    # conversation_summary_memory()
    rag_memory()