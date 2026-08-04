from langchain.chat_models import ChatOpenAI
model = ChatOpenAI(
    model_name='Qwen',
    openai_api_base='http://localhost:8001/v1',
    openai_api_key='EMPTY',
    streaming=False
)

def start_up():
    from langchain.schema import HumanMessage
    text = "制造多彩袜子的公司的好名字是什么？"
    messages = [HumanMessage(content=text)]
    print(model.predict_messages(messages))

def try_lcel():
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_template("tell me a short joke about {topic}")
    output_parser = StrOutputParser()

    chain = prompt | model | output_parser

    print(chain.invoke({"topic": "ice cream"}))

def try_prompt():
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_template("tell me a short joke about {topic}")

    value = prompt.invoke({"topic": "ice cream"})
    print(value)
    print(value.to_messages())
    print(value.to_string())

def try_fewshot_template():
    from langchain.chains import LLMChain
    from langchain.prompts import ChatPromptTemplate, AIMessagePromptTemplate, HumanMessagePromptTemplate, SystemMessagePromptTemplate

    # 样本集
    examples = [{
        "input": "今天晚饭去哪吃？",
        "answer": "Yes"
    }, {
        "input": "今天是疯狂星期四耶。",
        "answer": "No"
    }, {
        "input": "广州路还是珠江路",
        "answer": "Yes"
    }, {
        "input": "广州路吧",
        "answer": "No"
    }]

    # 创建样例对话列表
    # example_messages = [
    #     SystemMessagePromptTemplate.from_template("请判断下面给出的语句是否为问题。"),
    #     HumanMessagePromptTemplate.from_template("请判断下面给出的语句是否为问题。"),
    #     AIMessagePromptTemplate.from_template("好的，请给出您的语句。")
    # ]
    # for example in examples:
    #     human_example = HumanMessagePromptTemplate.from_template(example["input"])
    #     AI_example = AIMessagePromptTemplate.from_template(example["answer"])
    #     example_messages.append(human_example)
    #     example_messages.append(AI_example)
    # 加入真正要处理的问题
    human_message_prompt = HumanMessagePromptTemplate.from_template("{text}")
    # example_messages.append(human_message_prompt)

    messages = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template("你将扮演一个语言学家，你的任务是判断下面给出的语句是否为问题。你需要先对给出的语句进行分析，然后以 Yes 或 No 给出你的判断。加油！下面请开始你的工作！"),
        human_message_prompt
    ])

    chain = LLMChain(llm=model, prompt=messages)
    print(chain.invoke({"text": "我错过了初选，我该怎么办？"}))


if __name__ == '__main__':
    # start_up()
    # try_lcel()
    # try_prompt()
    try_fewshot_template()