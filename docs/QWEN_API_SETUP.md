# PAIMON Next 接入通义千问接口文档

本文档用于把 PAIMON Next 连接到阿里云百炼/通义千问。你只需要补全 API Key，就可以让项目从“抽取式回答”升级为“千问生成式 RAG 回答”。

## 1. 推荐配置

默认推荐使用阿里云百炼的 OpenAI 兼容接口：

```text
BASE_URL: https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL: qwen-plus
API_KEY: 你的阿里云百炼 API Key
```

模型选择建议：

| 模型 | 适用场景 |
| --- | --- |
| `qwen-plus` | 推荐默认值，质量、速度、成本比较均衡 |
| `qwen-turbo` | 更低成本、更快响应，适合高频简单问答 |
| `qwen3.6-flash` | 更偏低延迟场景，可用于批量问答 |
| `qwen3.7-max` | 更强效果，适合复杂问题或低置信度兜底 |

如果不确定选哪个，先用 `qwen-plus`。

## 2. 获取 API Key

1. 登录阿里云百炼控制台。
2. 开通模型服务。
3. 创建 API Key。
4. 复制 API Key，后续填入 `PAIMON_LLM_API_KEY`。

注意：不要把真实 API Key 提交到 Git、论文附件、公开截图或聊天记录。

## 3. PAIMON Next 一键配置

### 推荐：写入 `.env.local`

项目启动时会自动读取根目录下的 `.env.local`。文件格式如下：

```text
PAIMON_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
PAIMON_LLM_MODEL=qwen-plus
PAIMON_LLM_API_KEY=你的阿里云百炼 API Key
```

配置好后直接启动：

```bash
python PAIMON.py --host 127.0.0.1 --port 8002
```

`.env.local` 已加入 `.gitignore`，不要把真实 API Key 提交到公开仓库。

### 临时环境变量

### Windows PowerShell

把下面的 `sk-xxxx` 替换成你的真实 API Key：

```powershell
$env:PAIMON_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:PAIMON_LLM_MODEL = "qwen-plus"
$env:PAIMON_LLM_API_KEY = "sk-xxxx"

python PAIMON.py --host 127.0.0.1 --port 8002
```

### Linux / macOS

```bash
export PAIMON_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export PAIMON_LLM_MODEL="qwen-plus"
export PAIMON_LLM_API_KEY="sk-xxxx"

python PAIMON.py --host 127.0.0.1 --port 8002
```

启动后看到类似输出即可：

```text
PAIMON Next is running at http://127.0.0.1:8002
Loaded xxx knowledge chunks. LLM enabled: True
```

## 4. 调用 PAIMON 问答接口

PAIMON Next 会先检索本地新生问答资料，再把资料交给千问生成最终答案。

### 推荐接口

```http
POST /ask
Content-Type: application/json
```

请求体：

```json
{
  "question": "统一身份认证密码忘了怎么办？",
  "session_id": "demo",
  "top_k": 5
}
```

PowerShell 测试：

```powershell
$body = @{
  question = "统一身份认证密码忘了怎么办？"
  session_id = "demo"
  top_k = 5
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8002/ask" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

curl 测试：

```bash
curl -X POST "http://127.0.0.1:8002/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"统一身份认证密码忘了怎么办？","session_id":"demo","top_k":5}'
```

### 兼容旧接口

旧项目调用方式也可以继续使用：

```http
POST /RAG/chat
Content-Type: application/json
```

请求体：

```json
{
  "question": "校园卡丢了怎么补办？"
}
```

## 5. 直接测试千问接口

如果 PAIMON 启动后仍显示 `LLM enabled: False`，可以先单独测试千问接口。

### HTTP 接口信息

```text
POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
Authorization: Bearer 你的 API Key
Content-Type: application/json
```

请求体示例：

```json
{
  "model": "qwen-plus",
  "messages": [
    {
      "role": "system",
      "content": "你是一个面向南京大学新生的问答助手。"
    },
    {
      "role": "user",
      "content": "请用一句话介绍你自己。"
    }
  ],
  "temperature": 0.2,
  "max_tokens": 512
}
```

PowerShell 直连测试：

```powershell
$apiKey = "sk-xxxx"
$headers = @{
  Authorization = "Bearer $apiKey"
  "Content-Type" = "application/json"
}
$body = @{
  model = "qwen-plus"
  messages = @(
    @{ role = "system"; content = "你是一个面向南京大学新生的问答助手。" },
    @{ role = "user"; content = "请用一句话介绍你自己。" }
  )
  temperature = 0.2
  max_tokens = 512
} | ConvertTo-Json -Depth 8

Invoke-RestMethod `
  -Uri "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" `
  -Method Post `
  -Headers $headers `
  -Body $body
```

## 6. 推荐生成参数

PAIMON Next 当前默认使用比较稳的生成参数：

| 参数 | 推荐值 | 说明 |
| --- | --- | --- |
| `temperature` | `0.2` | 新生办事问答要稳，不建议太发散 |
| `max_tokens` | `900` | 足够生成步骤化回答 |
| `top_k` | `5` | 检索 5 条本地资料作为上下文 |

对于新生问答场景，建议保持低温度，并要求答案必须基于本地资料和引用来源。

## 7. 常见问题

### 启动后显示 `LLM enabled: False`

说明没有读取到 `PAIMON_LLM_BASE_URL`。请确认：

```powershell
echo $env:PAIMON_LLM_BASE_URL
echo $env:PAIMON_LLM_MODEL
```

### 返回 401 / Unauthorized

通常是 API Key 错误、Key 未开通百炼服务，或复制时多了空格。

### 返回 404 / model not found

通常是模型名不在当前地域可用。先改成：

```text
qwen-plus
```

### 响应慢或成本偏高

可以把模型改成：

```text
qwen-turbo
```

或者：

```text
qwen3.6-flash
```

### 答案没有引用来源

PAIMON Next 的提示词会要求模型基于检索资料回答并标注 `[S1]` 等引用。如果模型没有按要求输出，系统会在答案后补充依据编号。建议不要把 `temperature` 调得太高。

## 8. 生产部署建议

如果后续要上线给真实新生使用，建议不要让前端直接持有千问 API Key。推荐结构：

```text
前端 / QQ 机器人 / 微信机器人
  -> PAIMON Next API
      -> 阿里云百炼千问 API
```

API Key 只放在服务器环境变量中。

## 9. 官方参考

- 阿里云百炼 OpenAI Chat 兼容接口：`https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope`
- 阿里云百炼获取 API Key：`https://help.aliyun.com/zh/model-studio/get-api-key`
- 阿里云百炼模型列表：`https://help.aliyun.com/zh/model-studio/models`
