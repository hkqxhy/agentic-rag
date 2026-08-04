# adjustment
## 说明
这里是一些对 LangChain 原有库的改动，为了适应本项目的需求
## 改动库的名称和目的
### langchain_community.embeddings 中的 ModelScopeEmbeddings
- 构造函数：使得对象的 sequence_length 可以根据需要进行调整

### langchain.chat_models 中的 ChatOpenAI
- `get_num_tokens_from_messages`函数：使其能被正常调用。暂时先用字符串长度代替，后续可能通过分词算法进行估计。