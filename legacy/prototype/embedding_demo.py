from langchain_community.embeddings import ModelScopeEmbeddings

model_id = "D:/Data/Models/nlp_gte_sentence-embedding_chinese-base"

embeddings = ModelScopeEmbeddings(model_id=model_id)

text = "This is a test document."

query_result = embeddings.embed_query(text)
print(query_result)
doc_results = embeddings.embed_documents(["foo"])
print(doc_results)