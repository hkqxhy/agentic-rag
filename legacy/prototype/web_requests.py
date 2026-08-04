import requests
import json
import time

class QA:
    host = "http://127.0.0.1:8002"
    headers = {'Content-type': 'application/json'}

    def clear(self):
        url = self.host+"/clear"
        requests.get(url)

    def chat(self, message):
        start_time = time.time()
        url = self.host+"/model/chat"
        data = {"question": message}
        response = requests.post(url, headers=self.headers, json=data)
        # print(response.text)
        response = response.json()

        print("耗时：{:.2f}s".format(time.time() - start_time))
        print("问题：", response["question"])
        print("回答：", response["answer"])

    def ask(self, message):
        start_time = time.time()

        url = self.host+"/RAG/chat"
        data = {"question": message}
        response = requests.post(url, headers=self.headers, json=data)
        # print(response.text)
        response = response.json()

        print("耗时：{:.2f}s".format(time.time() - start_time))
        print("问题：", response["question"])
        print("回答：", response["answer"])
        print("参考文档：")
        print("\n".join(["{}".format(i["metadata"]) for i in response["reference"]]))

if __name__ == "__main__":
    a = QA()
    # ask("美育核心课啥时候选课？")
    # ask("能介绍一下专业分流吗？")
    # ask("介绍一下苏州校区")
    while True:
        q = input(">> ")
        if q == "exit":
            break
        if q == "clear":
            a.clear()
        else:
            a.ask(q)
            # a.chat(q)