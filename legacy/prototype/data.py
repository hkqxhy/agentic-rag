import os
import re
import time
import json
from typing import Literal
from langchain_core.documents import Document
from langchain_community.document_loaders.base import BaseLoader
from langchain.document_loaders import TextLoader, BSHTMLLoader, PyPDFLoader, Docx2txtLoader, CSVLoader

"""
    需要安装的包：
    pip install pypdf docx2txt bs4
"""

class ContentBlock:
    size: int = 0
    content: list[(str, int, bool)] = []
    type: Literal["ContentBlock"] = "ContentBlock"

    def __init__(self, content:str="", integrity:bool=False):
        """
            content: 内容
            integrity: 是否允许拆分
        """
        self.size = 0
        self.content = []
        if content != "":
            self.append_content(content, len(content), integrity)

    def __str__(self) -> str:
        return f"[size: {self.size}] "+self.content.__str__()

    def to_string(self) -> str:
        return "".join([i[0] for i in self.content])

    def append_content(
        self,
        content: str,
        size: int,
        integrity: bool=False
    ) -> None:
        self.content.append((content, size, integrity))
        self.size += size

    def merge_block(
        self,
        item: Literal["ContentBlock"]
    ):
        """
            合并两个 ContentBlock 对象，将 item 的内容加入到 self 后面
        """
        self.content += item.content
        self.size += item.size

    def append_block(
        self,
        item: Literal["ContentBlock"],
        block_size: int
    ) -> Literal["ContentBlock"]:
        """
            在内容后追加字符串，限制最大长度为 block_size

            返回：剩余的文本构成的 ContentBlock
            注意：
                如果 item 被完全加入，rest 为 None
                如果恰好分割到的文本块的 integrity 为 True，且长度不够完整加入，将会选择不加入这个文本块
        """
        if self.size + item.size <= block_size:
            self.content += item.content
            self.size += item.size
            return None
        else:
            accept, rest, full = ContentBlock(), ContentBlock(), False
            for it in item.content:
                if full:
                    # 此块已满，不加入
                    rest.append_content(it[0], it[1], it[2])
                else:
                    if self.size + it[1] <= block_size:
                        accept.append_content(it[0], it[1], it[2])
                    else:
                        full = True
                        if it[2]:
                            # 此块不可拆分，故不加入
                            rest.append_content(it[0], it[1], it[2])
                        else:
                            # 此块可拆分，拆分一部分加入
                            split_size = block_size - self.size
                            accept.append_content(it[0][0 : split_size], split_size, False)
                            rest.append_content(it[0][split_size : ], it[1] - split_size, False)
            self.merge_block(accept)
            return rest

    def append_keep_block(
        self,
        item: Literal["ContentBlock"],
        block_size: int
    ):
        '''
            通过删除靠前的内容，保持文本总长度不大于 block_size 大小
        '''
        self.merge_block(item)
        if self.size <= block_size:
            return
        else:
            while self.size - self.content[0][1] > block_size:
                self.size -= self.content[0][1]
                self.content.pop(0)
            if self.content[0][2]:
                # 不可拆分的文本块，直接整块删除
                self.size -= self.content[0][1]
                self.content.pop(0)
            else:
                # 可拆分的文本块，删除前一部分，保持总长度不大于 block_size
                remove_size = self.size - block_size
                self.size = block_size
                self.content[0] = (self.content[0][0][remove_size : ], self.content[0][1] - remove_size, False)

class QAJsonLoader(BaseLoader):
    """
        转换用于 Qwen 微调的 json 格式 QA 对数据
    """

    docs = []
    Q, A = "", ""
    def __init__(self, file_path, encoding):
        self.file_path = file_path
        self.encoding = encoding

    def load(self) -> list[Document]:
        try:
            with open(self.file_path, "r", encoding=self.encoding) as file_input:
                content = file_input.read()
            return Document(page_content=content, metadata={"source": self.file_path})
        except Exception as e:
            raise RuntimeError(f"Error loading {self.file_path}") from e

    def load_and_split(self, block_size, cover_size, text_splitter):
        self.docs = []
        qa_list = json.load(
            open(self.file_path, "r", encoding=self.encoding))

        for page in range(len(qa_list)):
            qa = qa_list[page]
            self.Q = text_splitter.filter(qa["conversations"][0]["value"])
            page_size = block_size - len(self.Q) - 10
            text_splitter.block_size = page_size
            text_splitter.cover_size = cover_size
            self.A = text_splitter.filter(qa["conversations"][1]["value"])
            self.A = text_splitter.content_splitter(
                doc = self.A, metadata = {"source": self.file_path, "page": page+1}
            )
            for doc in self.A:
                doc.page_content = self.Q + '$answer$' + doc.page_content
                self.docs.append(doc)

        return self.docs

class TextSplitter:
    index = False
    block_size = 512
    cover_size = 128

    def __init__(self, block_size, cover_size, index=False):
        self.index = index
        self.block_size = block_size
        self.cover_size = cover_size

    def invisible_filter(self, content:str) -> str:
        content = re.sub(r"[\t\f\v\r \xa0]+", " ", content)
        content = re.sub(r"[\n]+", "\n", content)
        return content

    def tag_filter(self, content:str) -> str:
        return content

    def filter(self, content:str) -> str:
        """
            对内容进行过滤，去除不可见字符、HTML标签等
        """
        content = content.strip()
        content = self.invisible_filter(content)
        content = self.tag_filter(content)
        return content

    def url_splitter(self, doc:str) -> list[ContentBlock]:
        """
            以链接划分字符串
        """
        url_pattern = r"(http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+)"
        doc = re.sub(url_pattern, r"$url$\1$url$", doc)
        docs = [i.strip() for i in doc.split("$url$")]
        docs = [ContentBlock(i, i[0:4] == "http") for i in docs]
        return docs

    def content_splitter(self, doc:str, metadata:dict) -> list[Document]:
        """
            对内容划分，遵循 block_size 和 cover_size
            期望最大程度上保持链接完整

            返回：划分后的 Document 列表
        """
        docs, index_count = [], 0
        blocks = self.url_splitter(doc)
        last, this = ContentBlock(), ContentBlock()
        for block in blocks:
            result = this.append_block(block, self.block_size-self.cover_size)
            while result is not None:
                if self.index:
                    index_count += 1
                    metadata.update({"index": index_count})
                docs.append(Document(
                    page_content = last.to_string()+this.to_string(),
                    metadata = dict(metadata)
                ))
                last.append_keep_block(this, self.cover_size)
                this = ContentBlock()
                result = this.append_block(result, self.block_size-self.cover_size)
        if this is not None and this.size > 0:
            if self.index:
                index_count += 1
                metadata.update({"index": index_count})
            docs.append(Document(
                page_content = last.to_string()+this.to_string(),
                metadata = dict(metadata)
            ))
        return docs

    def split_documents(self, docs:list[Document]) -> list[Document]:
        """
            对文档列表进行划分
        """
        temp = []
        for doc in docs:
            temp += self.content_splitter(
                doc = self.filter(doc.page_content),
                metadata = doc.metadata
            )
        return temp

supported_types = ["txt", "html", "htm", "md",
                   "pdf", "doc", "docx",
                   "xls", "xlsx", "csv",
                   "qa" # 转换用于 Qwen 微调 json 格式的 QA 对
                   ]
def LoadFile(path, size=512, cover=128, type="auto", index=False, encoding="utf-8") -> list[Document]:
    """
        path: 文件路径
        type: 文件类型
        encoding: 文件编码
        index: 是否对某个文档的若干划分进行编号，
            编号从 1 开始，会在 metadata 中添加 index 字段

        为给定文件分配对应的文件加载器，并加载、划分文档
        返回：加载并划分完成，生成的文档列表
    """

    if type == "auto":
        suffix = os.path.splitext(path)[1].lower().replace(".", "")
        type = suffix if suffix in supported_types else "txt"

    if type == "txt":
        return TextLoader(file_path=path, encoding=encoding). \
            load_and_split(text_splitter=TextSplitter(size, cover, index))
    elif type == "html" or type == "htm":
        return BSHTMLLoader(file_path=path, open_encoding=encoding). \
            load_and_split(text_splitter=TextSplitter(size, cover, index))
    elif type == "md":
        return TextLoader(file_path=path, encoding="utf-8"). \
            load_and_split(text_splitter=TextSplitter(size, cover, index))
    elif type == "pdf":
        return PyPDFLoader(file_path=path). \
            load_and_split(text_splitter=TextSplitter(size, cover, index))
    elif type == "doc" or type == "docx":
        return Docx2txtLoader(file_path=path). \
            load_and_split(text_splitter=TextSplitter(size, cover, index))
    elif type == "qa":
        return QAJsonLoader(file_path=path, encoding=encoding). \
            load_and_split(block_size=size, cover_size=cover, \
                           text_splitter=TextSplitter(size, cover, index))
    elif type == "csv":
        return CSVLoader(file_path=path, encoding=encoding).load()
    else:
        raise ValueError("Unsupported file type: {}".format(type))

if __name__ == "__main__":
    loader = LoadFile("./data/example.json", 512, 128, "QA")
    # loader = LoadFile("./data/选课.csv", 512, 128, "auto", True)
    # print(loader)
    # loader = LoadFile("./data/website/【2023级本科生】美育核心课选课通知.md", 512, 128, "auto", True)
    # loader = LoadFile("./data/website/【2023级新生】大类内专业意向填报通知.md", 512, 128, "auto", True)
    # print(loader)

#     a = TextSplitter(128, 32)
#     print(a.content_splitter("""
#         你好，这是一段测试文本，包含了一些链接：
#         https://www.example.com 和 http://sub.example.com/path?query=string
#         如果你需要访问百度，请访问 https://www.baidu.com，或者我可以提供一些帮助给你
#         这是一段故事，讲述了一只兔子撞到了树桩上，然后被农民抓起来吃掉的故事。
#         这只兔子。
# """, {"source": "test"}))

    # with open("output.txt", "w", encoding="utf-8") as f_out:
    #     f_out.write(loader.__str__())
    # a = TextSplitter(512)
    # print(a.url_filter("欢迎访问 https://www.example.com 和 http://sub.example.com/path?query=string 了解更多内容。也可以通过 FTP 协议访问 ftp://example.org ，或者查看邮件地址 mailto:support@example.net 。"))