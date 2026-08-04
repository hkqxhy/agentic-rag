import re
import os
import json
import hashlib

from adjustment.embeddings import ModelScopeEmbeddings

from langchain.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate
from langchain.chains import LLMChain

from data import LoadFile

# 如果预处理需要的话，可以使用大语言模型
# from langchain.chat_models import ChatOpenAI
# model = ChatOpenAI(
#     model_name="Qwen",
#     openai_api_base="http://localhost:8001/v1",
#     openai_api_key="EMPTY",
#     streaming=False
# )


# 引入 FAISS 向量库进行文档存储和搜索
from langchain_community.vectorstores.faiss import FAISS

# 自定义的 Embedding 类，用来完成文档的读取和嵌入操作
class Embedding:
    name = "default"
    size, cover = 512, 128
    vec_db, vec_id = None, {str}
    embedding_model = None

    def __init__(self, size, cover, db_name:str="default"):
        self.name, self.size, self.cover = db_name, size, cover
        self.embedding_model = ModelScopeEmbeddings(
            model_id="D:/Data/Models/nlp_gte_sentence-embedding_chinese-base",
            sequence_length=size
            # nlp_gte_sentence-embedding_chinese-base 模型向量维度为 728，可以接收 512 长度以下的文本
            # nlp_gte_sentence-embedding_chinese-large 模型向量维度为 1024，可以接收 1024 长度以下的文本
        )
        if os.path.exists("./faiss_{}".format(db_name)):
            self.vec_db = FAISS.load_local("./faiss_{}".format(db_name), self.embedding_model)
            self.vec_id = {i[1] for i in self.vec_db.index_to_docstore_id.items()}
        else:
            self.vec_db = None
            self.vec_id = {str}

    def load(self, paths:list):
        # 加载文件
        length = len(paths)
        if length == 0:
            return True if self.vec_db is not None else False
        else:
            for i in range(length):
                print("[{}/{}] Loading: {}".format(i+1, length, paths[i]))
                docs = LoadFile(
                    path = paths[i],
                    size = self.size,
                    cover = self.cover,
                    index = True
                )

                new_docs = 0
                for doc in docs:
                    hash_id = hashlib.md5(doc.page_content.encode()).hexdigest()
                    if hash_id in self.vec_id:
                        continue

                    new_docs += 1
                    self.vec_id.update({hash_id})
                    texts, metadatas, ids = [doc.page_content], [doc.metadata], [hash_id]

                    if self.vec_db is None:
                        self.vec_db = FAISS.from_texts(
                            texts = texts,
                            embedding = self.embedding_model,
                            metadatas = metadatas,
                            ids = ids
                        )
                    else:
                        self.vec_db.add_texts(
                            texts = texts,
                            embedding = self.embedding_model,
                            metadatas = metadatas,
                            ids = ids
                        )
                print("Successfully loaded new {} document(s), find {} documents(s).".format(new_docs, len(docs)))
            self.vec_db.save_local("./faiss_{}".format(self.name))
            return True

    def search(self, s:str, distance:float=0.0) -> list:
        """
            根据输入的句子在向量库中搜索相似文档
            s: 输入的句子
            distance: 向量余弦值阈值，越小越相似
        """
        if self.vec_db is not None:
            result = self.vec_db.similarity_search_with_score(
                query = s,
                k = 5, fetch_k = 20
            )
            return [doc for doc, _ in result if _ > distance]
        else:
            return []

if __name__ == "__main__":
    a = Embedding(size=512, cover=64, db_name="QA")
    a.load(['南哪QA.qa'])

    qa = json.load(open('南哪23级本科①群.txt.qa', 'r', encoding='utf-8'))
    for it in range(len(qa)):
        item = qa[it]
        result = a.search(item['Q'])
        print('原问题：{}\n原答案：{}\n'.format(item['Q'], item['A']))
        for i in range(len(result)):
            print('[{}] QA内容：{}'.format(i, result[i].page_content))
        choice = input('\n请输入有关答案编号，以空格分隔：').split(' ')
        if choice[0] == '':
            continue
        else:
            qa[it]['A'] = ''
            for ch in choice:
                qa[it]['A'] += result[int(ch)].page_content.split('$answer$')[1] + '\n'
    print(qa)

    # dir_path = "./data/"
    # name_list = [
    #     "website/【2023级本科生】美育核心课选课通知.md",
    #     "website/【2023级新生】大类内专业意向填报通知.md",
    #     "第二章：需求、供给与价格机制.pdf",
    #     "苏州校区.docx"
    # ]
    # a.load([dir_path + i for i in name_list])
    # result = a.search("美育核心课啥时候选课？")
    # print(f"找到 {len(result)} 篇文档：", [i.metadata for i in result].__str__(), result, sep="\n")
    # result = a.search("能介绍一下专业分流吗？")
    # print(f"找到 {len(result)} 篇文档：", [i.metadata for i in result].__str__(), result, sep="\n")
    # result = a.search("介绍一下苏州校区")
    # print(f"找到 {len(result)} 篇文档：", [i.metadata for i in result].__str__(), result, sep="\n")