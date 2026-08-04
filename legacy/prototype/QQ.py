import re
import json
import time
from modelscope import AutoModel, AutoModelForCausalLM, AutoTokenizer
from modelscope import GenerationConfig

from langchain.chat_models import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import ChatPromptTemplate, AIMessagePromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate

class QQ_Message_Handler:
    messages = []
    user_map = {}
    model = None

    def log(self, msg):
        print('[{}] {}'.format(time.strftime(r'%Y-%m-%d %H:%M:%S', time.localtime()), msg))

    def handle(self, file_path, output_path='./output.txt'):
        '''调用此函数一键处理消息文件'''
        self.read_message(file_path)
        self.filter_message()
        self.hide_user()
        # self.add_tag('Qwen-1_8B-Chat')
        self.add_tag('chatglm3-6b')
        # json.dump(self.messages, open(output_path, 'w', encoding='utf-8'), ensure_ascii=False)
        print(self.messages)
        with open(output_path, 'w', encoding='utf-8') as f:
            for m in self.messages:
                f.write('[{}] {} {}\n'.format(m['type'], m['time'], m['user']))
                f.write('{}\n'.format(m['message']))

    def read_message(self, file_path):
        '''读取消息文件'''
        message_list = open(file_path, 'r', encoding='utf-8').readlines()
        self.messages = []
        for line in message_list:
            line = line.strip().strip('\uFEFF')
            if re.search(r'^\d{4}-\d{1,2}-\d{1,2}\s\d{1,2}:\d{1,2}:\d{1,2}', line) is not None:
                user_data = re.search(r'(.*)[(<](.*?)[)>]$', line[20:].strip())
                self.messages.append({
                    'time': line[:19].strip(),
                    'user': user_data.group(1).strip() \
                        if user_data is not None else line[20:].strip(),
                    'message': '', 'type': None, 'tag': []
                })
            elif not line == '':
                self.messages[-1]['message'] += line.strip()+'\n'

    def filter_message(self):
        '''消息清洗'''
        messages_tmp = []
        for m in self.messages:
            if m['message'] == '': continue
            if m['user'] == '系统消息': continue

            messages_tmp.append(m)

        self.messages = messages_tmp

    def hide_user(self):
        '''隐藏用户信息'''
        self.user_map = {}
        user_count, max_length = 0, 0

        # 为所有用户建立映射
        for m in self.messages:
            if m['user'] not in self.user_map:
                user_count += 1
                self.user_map[m['user']] = 'user_{:04d}'.format(user_count)

                if len(m['user']) > max_length:
                    max_length = len(m['user'])

        # 查找并替代用户名
        for i in range(len(self.messages)):
            self.messages[i]['user'] = self.user_map[self.messages[i]['user']]
            content, content_len = self.messages[i]['message'], len(self.messages[i]['message'])

            for j in range(content_len):
                if content[j] == '@':
                    user_name, find_user = '', 'unfind_user'
                    for k in range(j+1, min(j+1+max_length, content_len)):
                        user_name += content[k]

                        if self.user_map.get(user_name) is not None:
                            find_user = user_name

                    if not find_user == 'unfind_user':
                        j += len(find_user)
                        self.messages[i]['message'] = self.messages[i]['message'].replace(
                            find_user, self.user_map[find_user])

    def load_model(self, model_name):
        self.log('正在加载大语言模型：{}'.format(model_name))
        self.model = ChatOpenAI(
            model_name='Qwen',
            openai_api_base='http://localhost:8001/v1',
            openai_api_key='EMPTY',
            streaming=False
        )
        self.log('模型加载成功！')

    def add_tag(self, model_name='Qwen-1_8B-Chat'):
        '''使用 LLM 为消息添加标签: Q(提问)/A(回答)/O(其他)'''

        block_size = 1
        messages_length = len(self.messages)
        self.load_model(model_name)

        self.log('开始进行标记，数据大小：{}，分块大小：{}'.format(messages_length, block_size))
        i, block_time = 0, time.time()
        while i < messages_length:
            human_message_prompt = HumanMessagePromptTemplate.from_template("{text}")
            messages = ChatPromptTemplate.from_messages([
                SystemMessagePromptTemplate.from_template("你将扮演一个语言学家，你的任务是判断下面给出的若干行语句是否为对话中的问题。你需要先将输入的语句按照换行符（\\n）进行划分；然后对每一行的语句进行分析，注意可能需要结合上下文语境，判断这个语句在整段对话中是否为提问；最后针对每一行语句输出一个 Yes 或 No 作为你的判断，同样以换行符（\\n）进行分隔。加油！下面请开始你的工作！"),
                human_message_prompt
            ])
            block_list = [i['message'] for i in self.messages[i:i+block_size]]

            try:
                chain = LLMChain(llm=self.model, prompt=messages)
                response = chain.invoke({"text": ''.join(block_list)})

                this_block_size = len(block_list)
                response = response['text'].strip().split('\n')
                for j in range(this_block_size):
                    self.messages[i+j]['type'] = response[j]
                self.log('标记进度 {}/{}(分块大小 {} 条/块)，本分块耗时 {:.2f} 秒' \
                        .format(i+1, messages_length, block_size, time.time()-block_time))
                block_time = time.time()
            except Exception as e:
                self.log('标记出错：{}！'.format(e))
                i -= block_size
            finally:
                i += block_size


if __name__ == '__main__':
    h = QQ_Message_Handler()
    # h.handle('./data/QQ/南哪23级本科①群.txt')
    h.handle('./data/QQ/new.txt', './new.txt')