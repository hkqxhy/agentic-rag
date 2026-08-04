from langchain.agents import AgentType, initialize_agent, load_tools
from adjustment.openai import ChatOpenAI

llm = ChatOpenAI(
    model_name='Qwen',
    openai_api_base='http://localhost:8002/v1',
    openai_api_key='EMPTY',
    streaming=True
)
print(llm.predict("这里有一句话，请你总结出其中的一些重要的关键词，并以[词a，词b，词c...]的方式给出。例如，若我给你“南京大学有多少学生？”，你应该返回[南京大学，学生，数量]。接下来请分解：“想问一下我们学校计算机科学与技术系有多少专业课要学习？”"))