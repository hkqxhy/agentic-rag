import random
from http import HTTPStatus
import dashscope
import json
import logging
from langchain_core.documents import Document

class PAIMON:
    # model = "qwen1.5-14b-chat"
    model = "qwen-turbo"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "PAIMON",
                "description": "Get any information you need to aswer the user's question.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keywords": {
                            "type": "string",
                            "description": "The keywords you want to search for. If there are multiple keywords, separate them with commas.",
                        }
                    },
                    "required": [
                        "keywords"
                    ]
                }
            }
        }
    ]
    messages = []

    def retriever(self, keywords:list=[]) -> list[Document]:
        return [Document(page_content=" ".join(keywords))]

    def search(self, message, max_retry=1) -> None | Document:
        def validator(call):
            if call.finish_reason != "tool_calls":
                return None
            call = call.message
            if "tool_calls" not in call:
                return None
            call = call["tool_calls"][0]
            if call["function"]["name"] != "PAIMON":
                return None
            if "arguments" not in call["function"]:
                return None
            args = json.loads(call["function"]["arguments"])
            if "keywords" not in args:
                return None
            args = args["keywords"].split(", ")
            return args

        try:
            for i in range(max_retry):
                self.messages.append({'role': 'user', 'content': message})
                response = dashscope.Generation.call(
                    model = self.model,
                    messages = self.messages,
                    tools = self.tools,
                    seed = random.randint(1, 10000),
                    result_format = "message"
                )
                if response.status_code == HTTPStatus.OK:
                    documents = []
                    call = response.output.choices[0]
                    keys = validator(call)
                    if keys is None:
                        logging.warning("函数调用存在问题，将跳过该函数调用。"
                                        f"内容：{call.__str__()}")
                        continue
                    documents += self.retriever(keys)
                    self.messages.append(call.message)
                    return documents
                else:
                    logging.warning("函数调用尝试失败，将重新尝试。"
                                    f"返回内容：{response.message}")
                self.messages.pop()
            raise TimeoutError("尝试次数过多，无法获取到结果！")
        except Exception as e:
            logging.error(e)
            return None

    def clear(self):
        self.messages = []

    def chat(self, message):
        documents = self.search(message)
        if documents is None:
            return "对不起，我无法回答您的问题。"
        if len(documents) == 0:
            documents = [Document(page_content="暂无资料。")]
        self.messages.append({
            "content": "\n".join([i.page_content for i in documents]),
            "name": "PAIMON",
            "role": "tool"
        })
        response = dashscope.Generation.call(
            model = self.model,
            messages = self.messages,
            tools = self.tools,
            seed=random.randint(1, 10000),
            result_format='message'
        )
        if response.status_code == HTTPStatus.OK:
            return response
        else:
            logging.error('Request id: %s, Status code: %s, error code: %s, error message: %s' % (
                response.request_id, response.status_code,
                response.code, response.message
            ))
            return ""

if __name__ == '__main__':
    a = PAIMON()
    # print(a.chat("介绍一下南京大学"))
    while True:
        message = input(">> ")
        print(a.chat(message))