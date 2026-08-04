from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import RAGConfig
from .service import NewStudentAssistant


class PAIMONRequestHandler(BaseHTTPRequestHandler):
    assistant: NewStudentAssistant

    server_version = "PAIMONNext/1.0"

    def do_OPTIONS(self) -> None:
        self._send_json({"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/docs"}:
            self._send_html(self._home_page())
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT.value)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/health":
            graph = getattr(self.assistant.retriever, "graph", None)
            self._send_json(
                {
                    "status": "ok",
                    "chunks": len(self.assistant.chunks),
                    "llm_enabled": self.assistant.llm.enabled,
                    "rag_mode": "graph_rag" if graph is not None else "advanced_rag",
                    "graph_terms": len(graph.terms) if graph is not None else 0,
                    "graph_communities": len(graph.communities) if graph is not None else 0,
                }
            )
            return
        if parsed.path == "/clear":
            query = parse_qs(parsed.query)
            session_id = query.get("session_id", ["default"])[0]
            self.assistant.clear(session_id)
            self._send_json({"status": "ok", "session_id": session_id})
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        data = self._read_json()
        if data is None:
            return

        if parsed.path in {"/ask", "/RAG/chat"}:
            question = str(data.get("question", ""))
            session_id = str(data.get("session_id", "default"))
            top_k = data.get("top_k")
            result = self.assistant.ask(
                question=question,
                session_id=session_id,
                top_k=int(top_k) if top_k else None,
            )
            self._send_json(result.to_dict())
            return

        if parsed.path == "/ask/stream":
            question = str(data.get("question", ""))
            session_id = str(data.get("session_id", "default"))
            top_k = data.get("top_k")
            self._send_sse_start()
            try:
                for event in self.assistant.ask_stream(
                    question=question,
                    session_id=session_id,
                    top_k=int(top_k) if top_k else None,
                ):
                    self._send_sse_event(event["type"], event)
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        if parsed.path == "/model/chat":
            question = str(data.get("question", ""))
            session_id = str(data.get("session_id", "default"))
            result = self.assistant.chat(question=question, session_id=session_id)
            self._send_json(result.to_dict())
            return

        if parsed.path == "/reindex":
            self._send_json({"status": "ok", **self.assistant.reindex()})
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        print("%s - - %s" % (self.address_string(), format % args))

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "invalid json"}, status=HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(data, dict):
            self._send_json({"error": "json body must be an object"}, status=HTTPStatus.BAD_REQUEST)
            return None
        return data

    def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_start(self) -> None:
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _send_sse_event(self, event_name: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        message = f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")
        self.wfile.write(message)
        self.wfile.flush()

    def _home_page(self) -> str:
        graph = getattr(self.assistant.retriever, "graph", None)
        return _render_modern_home_page(
            chunk_count=len(self.assistant.chunks),
            llm_enabled=self.assistant.llm.enabled,
            rag_mode="GraphRAG" if graph is not None else "Advanced RAG",
            graph_terms=len(graph.terms) if graph is not None else 0,
            graph_communities=len(graph.communities) if graph is not None else 0,
        )
        llm_status = "已启用" if self.assistant.llm.enabled else "未启用"
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PAIMON Next</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f6f8fb; color: #172033; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 40px; }}
    header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    p {{ margin: 0; line-height: 1.65; }}
    code {{ background: #eef2f6; border-radius: 6px; padding: 2px 5px; }}
    .status {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .pill {{ border: 1px solid #d6dde7; background: #fff; border-radius: 999px; padding: 6px 10px; font-size: 13px; }}
    .layout {{ display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 16px; align-items: start; }}
    .panel {{ background: #fff; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; }}
    label {{ display: block; font-weight: 700; margin-bottom: 8px; }}
    textarea {{ width: 100%; min-height: 118px; resize: vertical; border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px; font: inherit; line-height: 1.55; }}
    .controls {{ display: flex; gap: 10px; align-items: center; margin-top: 10px; }}
    button {{ border: 0; border-radius: 8px; padding: 10px 16px; font: inherit; font-weight: 700; color: #fff; background: #2454d6; cursor: pointer; }}
    button:disabled {{ background: #95a3b8; cursor: not-allowed; }}
    .hint {{ color: #64748b; font-size: 13px; }}
    .answer {{ margin-top: 16px; min-height: 260px; white-space: pre-wrap; line-height: 1.75; }}
    .empty {{ color: #7a869a; }}
    .sources {{ display: grid; gap: 10px; margin-top: 10px; }}
    .source {{ border: 1px solid #e0e6ef; border-radius: 8px; padding: 10px; background: #fbfcfe; }}
    .source strong {{ display: block; font-size: 14px; margin-bottom: 4px; }}
    .source small {{ color: #64748b; overflow-wrap: anywhere; }}
    @media (max-width: 820px) {{ header, .layout {{ display: block; }} .status {{ justify-content: flex-start; margin-top: 12px; }} .panel {{ margin-bottom: 14px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PAIMON Next 新生问答助手</h1>
        <p>输入新生办事问题，系统会检索本地资料并通过流式接口生成回答。</p>
      </div>
      <div class="status">
        <span class="pill">本机地址 127.0.0.1</span>
        <span class="pill">知识块 {len(self.assistant.chunks)}</span>
        <span class="pill">LLM {llm_status}</span>
      </div>
    </header>

    <div class="layout">
      <section class="panel">
        <label for="question">问题</label>
        <textarea id="question" placeholder="例如：统一身份认证密码忘了怎么办？">校园卡丢了怎么补办？</textarea>
        <div class="controls">
          <button id="ask">流式提问</button>
          <button id="clear" type="button">清空</button>
          <span id="state" class="hint">准备就绪</span>
        </div>
        <div id="answer" class="answer empty">答案会在这里流式显示。</div>
      </section>

      <aside class="panel">
        <strong>来源</strong>
        <p class="hint">回答完成后显示检索引用和置信度。</p>
        <div id="meta" class="hint"></div>
        <div id="sources" class="sources"></div>
      </aside>
    </div>
  </main>

  <script>
    const questionEl = document.getElementById("question");
    const askBtn = document.getElementById("ask");
    const clearBtn = document.getElementById("clear");
    const answerEl = document.getElementById("answer");
    const sourcesEl = document.getElementById("sources");
    const metaEl = document.getElementById("meta");
    const stateEl = document.getElementById("state");
    const sessionId = "web-" + Math.random().toString(16).slice(2);

    askBtn.addEventListener("click", askStream);
    clearBtn.addEventListener("click", () => {{
      answerEl.textContent = "答案会在这里流式显示。";
      answerEl.classList.add("empty");
      const diag = result.diagnostics || {{}};
      const meta = [
        "confidence: " + result.confidence,
        "intent: " + result.intent
      ];
      if (diag.mode) meta.push("rag: " + diag.mode);
      if (diag.quality !== undefined) meta.push("quality: " + diag.quality);
      if (diag.corrective_pass) meta.push("corrective retrieval");
      metaEl.textContent = meta.join(" | ");
      sourcesEl.innerHTML = "";
      metaEl.textContent = "";
      stateEl.textContent = "已清空";
    }});
    questionEl.addEventListener("keydown", (event) => {{
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") askStream();
    }});

    async function askStream() {{
      const question = questionEl.value.trim();
      if (!question) {{
        stateEl.textContent = "请先输入问题";
        return;
      }}
      askBtn.disabled = true;
      answerEl.textContent = "";
      answerEl.classList.remove("empty");
      sourcesEl.innerHTML = "";
      metaEl.textContent = "";
      stateEl.textContent = "正在连接流式接口...";

      try {{
        const response = await fetch("/ask/stream", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ question, session_id: sessionId, top_k: 5 }})
        }});
        if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
        stateEl.textContent = "正在生成...";
        await readSSE(response.body);
      }} catch (error) {{
        stateEl.textContent = "请求失败：" + error.message;
      }} finally {{
        askBtn.disabled = false;
      }}
    }}

    async function readSSE(body) {{
      const reader = body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {{
        const {{ value, done }} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {{ stream: true }});
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const block of events) handleSSEBlock(block);
      }}
      if (buffer.trim()) handleSSEBlock(buffer);
    }}

    function handleSSEBlock(block) {{
      const lines = block.split("\n");
      let eventName = "message";
      const dataLines = [];
      for (const line of lines) {{
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }}
      if (!dataLines.length) return;
      const payload = JSON.parse(dataLines.join("\\n"));
      if (eventName === "delta") {{
        answerEl.textContent += payload.delta || "";
      }}
      if (eventName === "final") {{
        renderFinal(payload.result);
      }}
    }}

    function renderFinal(result) {{
      stateEl.textContent = "完成";
      metaEl.textContent = "置信度：" + result.confidence + "，意图：" + result.intent;
      sourcesEl.innerHTML = "";
      for (const source of (result.sources || []).slice(0, 5)) {{
        const item = document.createElement("div");
        item.className = "source";
        item.innerHTML = "<strong>[" + escapeHtml(source.id) + "] " + escapeHtml(source.title || "来源") + "</strong>" +
          "<small>" + escapeHtml(source.source || "") + "</small>";
        sourcesEl.appendChild(item);
      }}
    }}

    function escapeHtml(value) {{
      return String(value).replace(/[&<>"']/g, (ch) => ({{
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }}[ch]));
    }}
  </script>
</body>
</html>"""


def _render_modern_home_page(
    chunk_count: int,
    llm_enabled: bool,
    rag_mode: str,
    graph_terms: int,
    graph_communities: int,
) -> str:
    return _render_taste_home_page(
        chunk_count=chunk_count,
        llm_enabled=llm_enabled,
        rag_mode=rag_mode,
        graph_terms=graph_terms,
        graph_communities=graph_communities,
    )

    llm_status = "已启用" if llm_enabled else "未启用"
    graph_status = (
        f"{graph_terms} 主题 / {graph_communities} 社区"
        if graph_terms
        else "未启用"
    )
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PAIMON 新生问答助手</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --panel-soft: #f1f4f8;
      --text: #15171c;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #2457c5;
      --accent-strong: #1743a3;
      --assistant: #ffffff;
      --user: #e8f0ff;
      --shadow: 0 18px 48px rgba(21, 23, 28, 0.08);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      min-height: 100%;
      background:
        radial-gradient(circle at 18% -8%, rgba(36, 87, 197, 0.13), transparent 30%),
        linear-gradient(180deg, #fbfbf8 0%, var(--bg) 100%);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    button, textarea { font: inherit; }
    button { cursor: pointer; }
    .app {
      height: 100dvh;
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 320px;
      gap: 14px;
      padding: 14px;
    }
    .sidebar, .sources-panel {
      background: rgba(255, 255, 255, 0.84);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-height: 0;
    }
    .sidebar {
      display: flex;
      flex-direction: column;
      padding: 16px;
      gap: 18px;
    }
    .brand {
      display: flex;
      gap: 12px;
      align-items: center;
      min-width: 0;
    }
    .logo {
      position: relative;
      width: 44px;
      height: 44px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      color: #fff;
      font-weight: 800;
      letter-spacing: 0;
      background: linear-gradient(145deg, #1743a3, #2d6cdf);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.22);
      flex: 0 0 auto;
    }
    .logo::after {
      content: "";
      position: absolute;
      width: 30px;
      height: 13px;
      border: 1.5px solid rgba(255,255,255,0.76);
      border-left-color: transparent;
      border-right-color: transparent;
      border-radius: 50%;
      transform: rotate(-24deg);
    }
    .brand h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.05;
      letter-spacing: 0;
    }
    .brand p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 13px;
    }
    .side-actions {
      display: grid;
      gap: 8px;
    }
    .new-chat {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 8px;
      padding: 10px 12px;
      font-weight: 700;
      text-align: left;
    }
    .new-chat:hover { border-color: #b8c3d5; background: #f7f9fc; }
    .status-stack {
      display: grid;
      gap: 8px;
    }
    .metric {
      border: 1px solid var(--line);
      background: #fbfcfe;
      border-radius: 8px;
      padding: 10px;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }
    .metric strong {
      font-size: 14px;
      overflow-wrap: anywhere;
    }
    .examples {
      margin-top: auto;
      display: grid;
      gap: 8px;
    }
    .examples strong {
      font-size: 13px;
    }
    .example {
      border: 1px solid transparent;
      background: #eef3fb;
      color: #25324a;
      border-radius: 8px;
      padding: 9px 10px;
      text-align: left;
      line-height: 1.35;
      font-size: 13px;
    }
    .example:hover { border-color: #b8c8e6; background: #e7eefb; }
    .chat-shell {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      background: rgba(255,255,255,0.66);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .chat-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.76);
    }
    .chat-title strong { display: block; font-size: 15px; }
    .chat-title span { color: var(--muted); font-size: 13px; }
    .state {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    .messages {
      overflow-y: auto;
      padding: 22px 18px 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .message {
      display: grid;
      grid-template-columns: 34px minmax(0, 760px);
      gap: 10px;
      align-items: flex-start;
    }
    .message.user {
      grid-template-columns: minmax(0, 760px) 34px;
      justify-content: end;
    }
    .avatar {
      width: 34px;
      height: 34px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      font-weight: 800;
      font-size: 13px;
      background: #e8edf6;
      color: #25324a;
      user-select: none;
    }
    .assistant .avatar {
      color: #fff;
      background: linear-gradient(145deg, #1743a3, #2d6cdf);
    }
    .user .avatar {
      grid-column: 2;
      background: #111827;
      color: #fff;
    }
    .bubble {
      border: 1px solid var(--line);
      background: var(--assistant);
      border-radius: 8px;
      padding: 12px 14px;
      line-height: 1.7;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .user .bubble {
      grid-column: 1;
      grid-row: 1;
      background: var(--user);
      border-color: #c7d8fb;
    }
    .message-text:empty::after {
      content: "正在检索知识库...";
      color: var(--muted);
    }
    .composer {
      padding: 14px 18px 18px;
      border-top: 1px solid var(--line);
      background: rgba(255,255,255,0.86);
    }
    .composer-box {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: end;
      gap: 10px;
      border: 1px solid #c8d0dc;
      background: var(--panel);
      border-radius: 8px;
      padding: 10px;
    }
    textarea {
      width: 100%;
      min-height: 42px;
      max-height: 160px;
      resize: none;
      border: 0;
      outline: 0;
      padding: 8px 6px;
      line-height: 1.55;
      color: var(--text);
      background: transparent;
    }
    .send {
      min-width: 74px;
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      font-weight: 800;
    }
    .send:hover { background: var(--accent-strong); }
    .send:disabled { background: #aab5c6; cursor: not-allowed; }
    .composer-hint {
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      gap: 12px;
    }
    .sources-panel {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      overflow: hidden;
    }
    .sources-head {
      padding: 16px 16px 10px;
      border-bottom: 1px solid var(--line);
    }
    .sources-head strong { display: block; font-size: 15px; }
    .sources-head span { color: var(--muted); font-size: 13px; }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
    }
    .tag {
      border: 1px solid var(--line);
      background: #fbfcfe;
      border-radius: 999px;
      padding: 5px 8px;
      font-size: 12px;
      color: #3d4758;
    }
    .sources {
      overflow-y: auto;
      padding: 12px 16px 16px;
      display: grid;
      align-content: start;
      gap: 10px;
    }
    .source {
      border: 1px solid var(--line);
      background: #fbfcfe;
      border-radius: 8px;
      padding: 10px;
    }
    .source strong {
      display: block;
      font-size: 13px;
      line-height: 1.45;
      margin-bottom: 4px;
    }
    .source small {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .empty-panel {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }
    @media (max-width: 1080px) {
      .app { grid-template-columns: 230px minmax(0, 1fr); }
      .sources-panel { display: none; }
    }
    @media (max-width: 760px) {
      .app {
        height: 100dvh;
        grid-template-columns: 1fr;
        padding: 0;
        gap: 0;
      }
      .sidebar {
        border-radius: 0;
        box-shadow: none;
        border-width: 0 0 1px;
        padding: 12px 14px;
        gap: 10px;
      }
      .status-stack, .examples { display: none; }
      .chat-shell {
        border-radius: 0;
        border-width: 0;
        box-shadow: none;
      }
      .chat-top { padding: 12px 14px; }
      .messages { padding: 16px 12px; }
      .message, .message.user {
        grid-template-columns: 30px minmax(0, 1fr);
      }
      .message.user {
        grid-template-columns: minmax(0, 1fr) 30px;
      }
      .avatar { width: 30px; height: 30px; }
      .composer { padding: 10px 12px 12px; }
      .composer-hint { display: none; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">P</div>
        <div>
          <h1>PAIMON</h1>
          <p>南京大学新生问答助手</p>
        </div>
      </div>
      <div class="side-actions">
        <button class="new-chat" id="clearBtn" type="button">新建对话</button>
      </div>
      <div class="status-stack">
        <div class="metric"><span>知识库</span><strong>__CHUNK_COUNT__ 个知识块</strong></div>
        <div class="metric"><span>检索模式</span><strong>__RAG_MODE__</strong></div>
        <div class="metric"><span>图谱</span><strong>__GRAPH_STATUS__</strong></div>
        <div class="metric"><span>LLM</span><strong>__LLM_STATUS__</strong></div>
      </div>
      <div class="examples">
        <strong>试试这些问题</strong>
        <button class="example" data-question="校园卡丢了怎么补办？" type="button">校园卡丢了怎么补办？</button>
        <button class="example" data-question="南大有哪些社团可以参加？" type="button">南大有哪些社团可以参加？</button>
        <button class="example" data-question="宿舍校园网和路由器怎么用？" type="button">宿舍校园网和路由器怎么用？</button>
      </div>
    </aside>

    <main class="chat-shell">
      <header class="chat-top">
        <div class="chat-title">
          <strong>新生问答</strong>
          <span>基于本地知识库、GraphRAG 与流式输出</span>
        </div>
        <div class="state" id="state">准备就绪</div>
      </header>

      <section class="messages" id="messages" aria-live="polite"></section>

      <form class="composer" id="composer">
        <div class="composer-box">
          <textarea id="question" rows="1" placeholder="向 PAIMON 提问，例如：统一身份认证密码忘了怎么办？"></textarea>
          <button class="send" id="sendBtn" type="submit">发送</button>
        </div>
        <div class="composer-hint">
          <span>Enter 发送，Shift + Enter 换行</span>
          <span>回答会附带引用来源和检索诊断</span>
        </div>
      </form>
    </main>

    <aside class="sources-panel">
      <div class="sources-head">
        <strong>引用与诊断</strong>
        <span>完成回答后显示来源、置信度和图谱信息</span>
      </div>
      <div class="meta" id="meta"></div>
      <div class="sources" id="sources">
        <div class="empty-panel">还没有引用来源。发送一个问题后，PAIMON 会在这里列出检索到的证据。</div>
      </div>
    </aside>
  </div>

  <script>
    const messagesEl = document.getElementById("messages");
    const questionEl = document.getElementById("question");
    const composerEl = document.getElementById("composer");
    const sendBtn = document.getElementById("sendBtn");
    const clearBtn = document.getElementById("clearBtn");
    const stateEl = document.getElementById("state");
    const sourcesEl = document.getElementById("sources");
    const metaEl = document.getElementById("meta");
    const sessionId = "web-" + Math.random().toString(16).slice(2);
    let isSending = false;

    const greeting = "你好，我是 PAIMON。你可以问我报到、校园卡、宿舍、医保、选课、社团、校园网等新生问题。我会尽量基于知识库回答，并给出引用来源。";

    resetConversation();

    composerEl.addEventListener("submit", (event) => {
      event.preventDefault();
      askStream();
    });

    questionEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        askStream();
      }
    });

    questionEl.addEventListener("input", () => {
      questionEl.style.height = "auto";
      questionEl.style.height = Math.min(questionEl.scrollHeight, 160) + "px";
    });

    clearBtn.addEventListener("click", async () => {
      try {
        await fetch("/clear?session_id=" + encodeURIComponent(sessionId));
      } catch (error) {
        // Clearing local UI still works when the network request fails.
      }
      resetConversation();
      questionEl.focus();
    });

    document.querySelectorAll("[data-question]").forEach((button) => {
      button.addEventListener("click", () => {
        questionEl.value = button.dataset.question || "";
        questionEl.dispatchEvent(new Event("input"));
        questionEl.focus();
      });
    });

    async function askStream() {
      const question = questionEl.value.trim();
      if (!question || isSending) return;
      isSending = true;
      sendBtn.disabled = true;
      stateEl.textContent = "正在检索";
      clearSources();

      appendMessage("user", question);
      const assistantMessage = appendMessage("assistant", "");
      questionEl.value = "";
      questionEl.style.height = "auto";

      let answerText = "";
      try {
        const response = await fetch("/ask/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, session_id: sessionId, top_k: 5 })
        });
        if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
        stateEl.textContent = "正在生成";
        await readSSE(response.body, (eventName, payload) => {
          if (eventName === "delta") {
            answerText += payload.delta || "";
            setMessageText(assistantMessage, answerText);
          }
          if (eventName === "final") {
            renderFinal(payload.result, assistantMessage, answerText);
          }
        });
      } catch (error) {
        setMessageText(assistantMessage, "请求失败：" + error.message);
        stateEl.textContent = "请求失败";
      } finally {
        isSending = false;
        sendBtn.disabled = false;
        questionEl.focus();
      }
    }

    async function readSSE(body, onEvent) {
      const reader = body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const eventSeparator = String.fromCharCode(10) + String.fromCharCode(10);
        const events = buffer.split(eventSeparator);
        buffer = events.pop() || "";
        for (const block of events) parseSSEBlock(block, onEvent);
      }
      if (buffer.trim()) parseSSEBlock(buffer, onEvent);
    }

    function parseSSEBlock(block, onEvent) {
      const lines = block.split(String.fromCharCode(10));
      let eventName = "message";
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) return;
      onEvent(eventName, JSON.parse(dataLines.join("\n")));
    }

    function appendMessage(role, text) {
      const article = document.createElement("article");
      article.className = "message " + role;
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = role === "assistant" ? "P" : "你";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      const messageText = document.createElement("div");
      messageText.className = "message-text";
      messageText.textContent = text;
      bubble.appendChild(messageText);
      article.appendChild(avatar);
      article.appendChild(bubble);
      messagesEl.appendChild(article);
      scrollToBottom();
      return article;
    }

    function setMessageText(article, text) {
      article.querySelector(".message-text").textContent = text;
      scrollToBottom();
    }

    function renderFinal(result, assistantMessage, streamedText) {
      if (!streamedText && result.answer) setMessageText(assistantMessage, result.answer);
      stateEl.textContent = "完成";
      renderMeta(result);
      renderSources(result.sources || []);
    }

    function renderMeta(result) {
      const diagnostics = result.diagnostics || {};
      const graph = diagnostics.graph || {};
      metaEl.innerHTML = "";
      addTag("置信度 " + result.confidence);
      if (result.intent) addTag("意图 " + result.intent);
      if (diagnostics.mode) addTag(diagnostics.mode);
      if (diagnostics.quality !== undefined) addTag("质量 " + diagnostics.quality);
      if (graph.matched_communities && graph.matched_communities.length) {
        addTag("图谱 " + graph.matched_communities.slice(0, 2).join(" / "));
      }
    }

    function addTag(text) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = text;
      metaEl.appendChild(tag);
    }

    function renderSources(sources) {
      sourcesEl.innerHTML = "";
      if (!sources.length) {
        const empty = document.createElement("div");
        empty.className = "empty-panel";
        empty.textContent = "这次回答没有返回引用来源。";
        sourcesEl.appendChild(empty);
        return;
      }
      for (const source of sources.slice(0, 5)) {
        const item = document.createElement("div");
        item.className = "source";
        const title = document.createElement("strong");
        title.textContent = "[" + source.id + "] " + (source.title || "来源");
        const detail = document.createElement("small");
        detail.textContent = (source.source || "") + " · score " + source.score;
        item.appendChild(title);
        item.appendChild(detail);
        sourcesEl.appendChild(item);
      }
    }

    function clearSources() {
      metaEl.innerHTML = "";
      sourcesEl.innerHTML = "";
      const empty = document.createElement("div");
      empty.className = "empty-panel";
      empty.textContent = "正在等待本轮回答的引用来源。";
      sourcesEl.appendChild(empty);
    }

    function resetConversation() {
      messagesEl.innerHTML = "";
      appendMessage("assistant", greeting);
      metaEl.innerHTML = "";
      sourcesEl.innerHTML = '<div class="empty-panel">还没有引用来源。发送一个问题后，PAIMON 会在这里列出检索到的证据。</div>';
      stateEl.textContent = "准备就绪";
    }

    function scrollToBottom() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  </script>
</body>
</html>"""
    return (
        html.replace("__CHUNK_COUNT__", str(chunk_count))
        .replace("__RAG_MODE__", rag_mode)
        .replace("__GRAPH_STATUS__", graph_status)
        .replace("__LLM_STATUS__", llm_status)
    )


def _render_chatnju_home_page(
    chunk_count: int,
    llm_enabled: bool,
    rag_mode: str,
    graph_terms: int,
    graph_communities: int,
) -> str:
    llm_status = "已启用" if llm_enabled else "未启用"
    graph_status = (
        f"{graph_terms} 主题 / {graph_communities} 社区"
        if graph_terms
        else "未启用"
    )
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ChatNJU</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #ffffff;
      --surface: #f7f7f8;
      --surface-strong: #ededf0;
      --text: #26272b;
      --muted: #72757d;
      --soft: #a3a6ad;
      --line: #ececef;
      --purple: #7b2f87;
      --purple-deep: #5d2267;
      --ink: #2c2d31;
      --shadow: 0 20px 55px rgba(15, 18, 25, 0.10);
      --content: 1030px;
      --answer: 850px;
      --composer: 1120px;
    }
    * { box-sizing: border-box; }
    html { min-height: 100%; background: var(--bg); }
    body {
      min-height: 100dvh;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      font-size: 16px;
      letter-spacing: 0;
    }
    button, textarea { font: inherit; }
    button {
      border: 0;
      color: inherit;
      background: transparent;
      cursor: pointer;
    }
    a {
      color: #202124;
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
    }
    .topbar {
      position: fixed;
      z-index: 20;
      top: 0;
      left: 0;
      right: 0;
      height: 54px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 18px;
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(16px);
    }
    .nav-group {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .brand-button {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      min-width: 0;
      height: 36px;
      padding: 0 2px;
      color: #55575c;
      font-weight: 700;
      font-size: 17px;
    }
    .brand-button span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .icon-button {
      width: 34px;
      height: 34px;
      display: inline-grid;
      place-items: center;
      border-radius: 8px;
      color: #696b70;
    }
    .icon-button:hover,
    .brand-button:hover {
      background: #f4f4f5;
      color: #2f3035;
    }
    .icon-button svg,
    .brand-button svg {
      width: 19px;
      height: 19px;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      fill: none;
    }
    .avatar-dot {
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background:
        radial-gradient(circle at 34% 32%, #ffffff 0 15%, transparent 16%),
        linear-gradient(145deg, #d9d9dc, #bfc1c7);
      box-shadow: inset 0 -4px 8px rgba(0, 0, 0, 0.08);
    }
    .state {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
    }
    .conversation {
      width: min(100%, var(--content));
      min-height: 100dvh;
      margin: 0 auto;
      padding: 82px 24px 150px;
    }
    .messages {
      display: flex;
      flex-direction: column;
      gap: 28px;
      min-height: calc(100dvh - 232px);
    }
    .message {
      max-width: var(--answer);
      min-width: 0;
    }
    .message.assistant {
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr);
      gap: 12px;
      align-items: flex-start;
    }
    .message.user {
      align-self: flex-end;
      max-width: min(570px, 78%);
    }
    .assistant-mark {
      width: 28px;
      height: 31px;
      margin-top: 2px;
      display: grid;
      place-items: center;
      color: #fff;
      font-size: 13px;
      font-weight: 800;
      line-height: 1;
      background: linear-gradient(165deg, var(--purple), var(--purple-deep));
      clip-path: polygon(50% 0, 92% 12%, 84% 74%, 50% 100%, 16% 74%, 8% 12%);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.28);
      user-select: none;
    }
    .assistant-mark::before {
      content: "南";
      transform: translateY(-1px);
    }
    .assistant-name {
      margin-bottom: 4px;
      color: #3b3d43;
      font-size: 16px;
      font-weight: 800;
    }
    .message-text {
      color: #24262b;
      line-height: 1.72;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .message-text:empty::after {
      content: "正在检索知识库...";
      color: var(--muted);
    }
    .message-text strong { font-weight: 800; }
    .message-text code {
      padding: 1px 5px;
      border-radius: 6px;
      background: #f1f1f3;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
    }
    .user-bubble {
      width: fit-content;
      max-width: 100%;
      margin-left: auto;
      padding: 12px 20px;
      border-radius: 999px;
      background: #f7f7f8;
      color: #4a4d54;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    .message-sources {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    .message-sources[hidden] { display: none; }
    .source-chip {
      max-width: min(100%, 360px);
      padding: 7px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfbfc;
      color: #5f636b;
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .composer {
      position: fixed;
      z-index: 18;
      left: 50%;
      bottom: 12px;
      width: min(calc(100% - 48px), var(--composer));
      transform: translateX(-50%);
    }
    .composer-shell {
      display: grid;
      grid-template-columns: 38px minmax(0, 1fr) 42px;
      align-items: end;
      gap: 6px;
      min-height: 54px;
      padding: 7px;
      border: 1px solid transparent;
      border-radius: 999px;
      background: #f7f7f8;
      box-shadow: 0 1px 0 rgba(0,0,0,0.02);
      transition: border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
    }
    .composer-shell:focus-within {
      border-color: #e3e3e6;
      background: #f9f9fa;
      box-shadow: 0 16px 45px rgba(20, 22, 30, 0.08);
    }
    .composer textarea {
      width: 100%;
      min-height: 38px;
      max-height: 150px;
      resize: none;
      border: 0;
      outline: 0;
      padding: 9px 4px;
      color: #303238;
      background: transparent;
      line-height: 1.45;
    }
    .composer textarea::placeholder { color: #9a9da5; }
    .composer-action {
      width: 38px;
      height: 38px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      color: #33353a;
    }
    .composer-action:hover { background: #ededf0; }
    .send-button {
      width: 34px;
      height: 34px;
      align-self: center;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: #2f3035;
      color: #fff;
    }
    .send-button:hover { background: #17181b; }
    .send-button svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
      stroke-width: 2.2;
      stroke-linecap: round;
      stroke-linejoin: round;
      fill: none;
    }
    .stop-square {
      display: none;
      width: 12px;
      height: 12px;
      border-radius: 3px;
      background: currentColor;
    }
    .is-sending .send-arrow { display: none; }
    .is-sending .stop-square { display: block; }
    .disclaimer {
      width: min(100%, 760px);
      max-width: calc(100% - 24px);
      margin: 7px auto 0;
      color: #a0a2a9;
      text-align: center;
      font-size: 12px;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }
    .details-panel {
      position: fixed;
      z-index: 30;
      top: 62px;
      right: 16px;
      width: min(376px, calc(100vw - 32px));
      max-height: calc(100dvh - 82px);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.96);
      box-shadow: var(--shadow);
      opacity: 0;
      pointer-events: none;
      transform: translateY(-8px);
      transition: opacity 160ms ease, transform 160ms ease;
    }
    .details-open .details-panel {
      opacity: 1;
      pointer-events: auto;
      transform: translateY(0);
    }
    .details-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    .details-head strong { font-size: 14px; }
    .details-body {
      min-height: 0;
      overflow: auto;
      padding: 12px 14px 14px;
      display: grid;
      align-content: start;
      gap: 14px;
    }
    .details-section h2 {
      margin: 0 0 8px;
      color: #3d4047;
      font-size: 13px;
    }
    .summary-line {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .meta-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }
    .tag {
      padding: 5px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fbfbfc;
      color: #555962;
      font-size: 12px;
    }
    .sources {
      display: grid;
      gap: 9px;
    }
    .source-card {
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfbfc;
    }
    .source-card strong {
      display: block;
      margin-bottom: 4px;
      color: #383b42;
      font-size: 13px;
      line-height: 1.45;
    }
    .source-card small {
      display: block;
      color: var(--muted);
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .empty-panel {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }
    @media (max-width: 760px) {
      .topbar {
        height: 50px;
        padding: 0 10px;
      }
      .brand-button { font-size: 16px; }
      .nav-group { gap: 4px; }
      .icon-button { width: 32px; height: 32px; }
      .topbar .nav-group:last-child .icon-button:not(#detailsBtn),
      .avatar-dot {
        display: none;
      }
      .conversation {
        padding: 72px 16px 142px;
      }
      .messages { gap: 24px; }
      .message.assistant {
        grid-template-columns: 30px minmax(0, 1fr);
        gap: 10px;
      }
      .assistant-mark {
        width: 26px;
        height: 29px;
      }
      .assistant-name,
      .message-text {
        font-size: 15px;
      }
      .message.user {
        max-width: 88%;
      }
      .user-bubble {
        padding: 10px 15px;
      }
      .composer {
        bottom: 8px;
        width: calc(100% - 20px);
      }
      .disclaimer {
        width: min(100%, 300px);
        max-width: 300px;
        font-size: 11px;
        word-break: break-all;
      }
      .composer-shell {
        grid-template-columns: 36px minmax(0, 1fr) 38px;
      }
      .details-panel {
        top: 56px;
        right: 10px;
        width: calc(100vw - 20px);
      }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="nav-group">
      <button class="icon-button" id="menuBtn" type="button" title="状态与来源" aria-label="状态与来源">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      </button>
      <button class="brand-button" id="brandBtn" type="button" title="新建对话">
        <span>ChatNJU</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      <button class="icon-button" id="newTopBtn" type="button" title="新建对话" aria-label="新建对话">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
      </button>
    </div>
    <div class="nav-group">
      <span class="state" id="state" aria-live="polite">就绪</span>
      <button class="icon-button" type="button" title="更多" aria-label="更多">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h.01M12 12h.01M19 12h.01"/></svg>
      </button>
      <button class="icon-button" id="detailsBtn" type="button" title="状态与来源" aria-label="状态与来源">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><path d="M13 4v4M7 10v4M15 16v4"/></svg>
      </button>
      <button class="icon-button" id="composeBtn" type="button" title="新建对话" aria-label="新建对话">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
      </button>
      <span class="avatar-dot" aria-hidden="true"></span>
    </div>
  </header>

  <main class="conversation">
    <section class="messages" id="messages" aria-live="polite"></section>
  </main>

  <form class="composer" id="composer">
    <div class="composer-shell">
      <button class="composer-action" id="clearBtn" type="button" title="新建对话" aria-label="新建对话">
        <svg viewBox="0 0 24 24" aria-hidden="true" width="20" height="20"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>
      </button>
      <textarea id="question" rows="1" placeholder="输入消息"></textarea>
      <button class="send-button" id="sendBtn" type="submit" aria-label="发送">
        <svg class="send-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>
        <span class="stop-square" aria-hidden="true"></span>
      </button>
    </div>
    <p class="disclaimer">大语言模型可能会生成误导性错误信息，请对关键信息加以验证。</p>
  </form>

  <aside class="details-panel" id="detailsPanel" aria-label="状态与来源">
    <div class="details-head">
      <strong>状态与来源</strong>
      <button class="icon-button" id="closeDetailsBtn" type="button" title="关闭" aria-label="关闭">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>
    <div class="details-body">
      <section class="details-section">
        <h2>系统状态</h2>
        <p class="summary-line">__SYSTEM_SUMMARY__</p>
        <p class="summary-line">图谱：__GRAPH_STATUS__</p>
      </section>
      <section class="details-section">
        <h2>本轮诊断</h2>
        <div class="meta-grid" id="meta">
          <span class="tag">等待提问</span>
        </div>
      </section>
      <section class="details-section">
        <h2>引用来源</h2>
        <div class="sources" id="sources">
          <div class="empty-panel">暂无来源</div>
        </div>
      </section>
    </div>
  </aside>

  <script>
    const messagesEl = document.getElementById("messages");
    const questionEl = document.getElementById("question");
    const composerEl = document.getElementById("composer");
    const sendBtn = document.getElementById("sendBtn");
    const clearBtn = document.getElementById("clearBtn");
    const stateEl = document.getElementById("state");
    const sourcesEl = document.getElementById("sources");
    const metaEl = document.getElementById("meta");
    const detailsPanel = document.getElementById("detailsPanel");
    const detailsBtn = document.getElementById("detailsBtn");
    const menuBtn = document.getElementById("menuBtn");
    const closeDetailsBtn = document.getElementById("closeDetailsBtn");
    const sessionId = "web-" + Math.random().toString(16).slice(2);
    let isSending = false;
    let abortController = null;

    resetConversation();

    composerEl.addEventListener("submit", (event) => {
      event.preventDefault();
      if (isSending) {
        stopStream();
        return;
      }
      askStream();
    });

    questionEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (isSending) return;
        askStream();
      }
    });

    questionEl.addEventListener("input", resizeQuestion);

    [clearBtn, document.getElementById("brandBtn"), document.getElementById("newTopBtn"), document.getElementById("composeBtn")]
      .forEach((button) => button.addEventListener("click", clearConversation));

    [detailsBtn, menuBtn].forEach((button) => {
      button.addEventListener("click", () => toggleDetails());
    });
    closeDetailsBtn.addEventListener("click", () => toggleDetails(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") toggleDetails(false);
    });

    async function askStream() {
      const question = questionEl.value.trim();
      if (!question || isSending) return;

      setBusy(true, "正在检索");
      clearSources();
      appendMessage("user", question);
      const assistantMessage = appendMessage("assistant", "");
      questionEl.value = "";
      resizeQuestion();

      let answerText = "";
      abortController = new AbortController();

      try {
        const response = await fetch("/ask/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, session_id: sessionId, top_k: 5 }),
          signal: abortController.signal
        });
        if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
        stateEl.textContent = "正在生成";
        await readSSE(response.body, (eventName, payload) => {
          if (eventName === "delta") {
            answerText += payload.delta || "";
            setMessageText(assistantMessage, answerText);
          }
          if (eventName === "final") {
            renderFinal(payload.result || {}, assistantMessage, answerText);
          }
        });
      } catch (error) {
        if (error.name === "AbortError") {
          if (!answerText) setMessageText(assistantMessage, "已停止生成。");
          stateEl.textContent = "已停止";
        } else {
          setMessageText(assistantMessage, "请求失败：" + error.message);
          stateEl.textContent = "请求失败";
        }
      } finally {
        abortController = null;
        setBusy(false, stateEl.textContent === "已停止" ? "已停止" : "就绪");
        questionEl.focus();
      }
    }

    function stopStream() {
      if (abortController) abortController.abort();
    }

    async function readSSE(body, onEvent) {
      const reader = body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const eventSeparator = String.fromCharCode(10) + String.fromCharCode(10);
        const events = buffer.split(eventSeparator);
        buffer = events.pop() || "";
        for (const block of events) parseSSEBlock(block, onEvent);
      }
      if (buffer.trim()) parseSSEBlock(buffer, onEvent);
    }

    function parseSSEBlock(block, onEvent) {
      const lines = block.split(String.fromCharCode(10));
      let eventName = "message";
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) return;
      onEvent(eventName, JSON.parse(dataLines.join(String.fromCharCode(10))));
    }

    function appendMessage(role, text) {
      const article = document.createElement("article");
      article.className = "message " + role;

      if (role === "user") {
        const bubble = document.createElement("div");
        bubble.className = "user-bubble";
        bubble.textContent = text;
        article.appendChild(bubble);
      } else {
        const mark = document.createElement("div");
        mark.className = "assistant-mark";
        const body = document.createElement("div");
        body.className = "assistant-body";
        const name = document.createElement("div");
        name.className = "assistant-name";
        name.textContent = "ChatNJU";
        const messageText = document.createElement("div");
        messageText.className = "message-text";
        messageText.textContent = text;
        const inlineSources = document.createElement("div");
        inlineSources.className = "message-sources";
        inlineSources.hidden = true;
        body.appendChild(name);
        body.appendChild(messageText);
        body.appendChild(inlineSources);
        article.appendChild(mark);
        article.appendChild(body);
      }

      messagesEl.appendChild(article);
      scrollToBottom();
      return article;
    }

    function setMessageText(article, text) {
      article.querySelector(".message-text").textContent = text;
      scrollToBottom();
    }

    function setMessageHtml(article, text) {
      article.querySelector(".message-text").innerHTML = formatAnswer(text);
      scrollToBottom();
    }

    function renderFinal(result, assistantMessage, streamedText) {
      const answer = result.answer || streamedText;
      if (answer) setMessageHtml(assistantMessage, answer);
      stateEl.textContent = "完成";
      renderMeta(result);
      renderSources(result.sources || [], assistantMessage);
    }

    function renderMeta(result) {
      const diagnostics = result.diagnostics || {};
      const graph = diagnostics.graph || {};
      metaEl.innerHTML = "";
      addTag("置信度 " + (result.confidence ?? "未知"));
      if (result.intent) addTag("意图 " + result.intent);
      if (diagnostics.mode) addTag(diagnostics.mode);
      if (diagnostics.quality !== undefined) addTag("质量 " + diagnostics.quality);
      if (graph.matched_communities && graph.matched_communities.length) {
        addTag("图谱 " + graph.matched_communities.slice(0, 2).join(" / "));
      }
      if (!metaEl.children.length) addTag("无诊断信息");
    }

    function addTag(text) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = text;
      metaEl.appendChild(tag);
    }

    function renderSources(sources, assistantMessage) {
      sourcesEl.innerHTML = "";
      const inlineSources = assistantMessage.querySelector(".message-sources");
      inlineSources.innerHTML = "";

      if (!sources.length) {
        const empty = document.createElement("div");
        empty.className = "empty-panel";
        empty.textContent = "本轮回答没有返回引用来源。";
        sourcesEl.appendChild(empty);
        inlineSources.hidden = true;
        return;
      }

      for (const source of sources.slice(0, 5)) {
        const card = document.createElement("div");
        card.className = "source-card";
        const title = document.createElement("strong");
        title.textContent = "[" + source.id + "] " + (source.title || "来源");
        const detail = document.createElement("small");
        detail.textContent = source.score === undefined
          ? (source.source || "")
          : (source.source || "") + " · score " + source.score;
        card.appendChild(title);
        card.appendChild(detail);
        sourcesEl.appendChild(card);

        const chip = document.createElement("div");
        chip.className = "source-chip";
        chip.textContent = "[" + source.id + "] " + (source.title || source.source || "来源");
        inlineSources.appendChild(chip);
      }
      inlineSources.hidden = false;
    }

    function clearSources() {
      metaEl.innerHTML = '<span class="tag">正在检索</span>';
      sourcesEl.innerHTML = '<div class="empty-panel">等待本轮引用来源。</div>';
    }

    async function clearConversation() {
      if (isSending) stopStream();
      try {
        await fetch("/clear?session_id=" + encodeURIComponent(sessionId));
      } catch (error) {
        // Local reset is still useful if the clear request fails.
      }
      resetConversation();
      questionEl.focus();
    }

    function resetConversation() {
      messagesEl.innerHTML = "";
      appendMessage("assistant", "你好，我是 ChatNJU。");
      metaEl.innerHTML = '<span class="tag">等待提问</span>';
      sourcesEl.innerHTML = '<div class="empty-panel">暂无来源</div>';
      stateEl.textContent = "就绪";
      toggleDetails(false);
    }

    function setBusy(busy, label) {
      isSending = busy;
      document.body.classList.toggle("is-sending", busy);
      sendBtn.setAttribute("aria-label", busy ? "停止生成" : "发送");
      stateEl.textContent = label;
    }

    function resizeQuestion() {
      questionEl.style.height = "auto";
      questionEl.style.height = Math.min(questionEl.scrollHeight, 150) + "px";
    }

    function toggleDetails(force) {
      const open = force === undefined ? !document.body.classList.contains("details-open") : force;
      document.body.classList.toggle("details-open", open);
      detailsPanel.setAttribute("aria-hidden", String(!open));
    }

    function scrollToBottom() {
      requestAnimationFrame(() => {
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
      });
    }

    function formatAnswer(value) {
      let html = escapeHtml(value || "");
      html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      html = html.replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
      html = html.replace(/(https?:\\/\\/[^\\s<]+)/g, '<a href="$1" target="_blank" rel="noreferrer">$1</a>');
      return html.replace(/\\n/g, "<br>");
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch]));
    }
  </script>
</body>
</html>"""
    return (
        html.replace("__CHUNK_COUNT__", str(chunk_count))
        .replace("__RAG_MODE__", rag_mode)
        .replace("__GRAPH_STATUS__", graph_status)
        .replace("__LLM_STATUS__", llm_status)
        .replace("__SYSTEM_SUMMARY__", f"{rag_mode} · {chunk_count} 知识块 · LLM {llm_status}")
    )


def _render_taste_home_page(
    chunk_count: int,
    llm_enabled: bool,
    rag_mode: str,
    graph_terms: int,
    graph_communities: int,
) -> str:
    llm_status = "已启用" if llm_enabled else "未启用"
    graph_status = (
        f"{graph_terms} 个主题 / {graph_communities} 个社区"
        if graph_terms
        else "未启用"
    )
    html = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PAIMON 新生问答助手</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      color-scheme: dark;
      --bg: #070806;
      --ink: #f5f0e8;
      --muted: #aba79d;
      --soft: #d8cdbd;
      --line: rgba(245, 240, 232, 0.16);
      --line-strong: rgba(245, 240, 232, 0.28);
      --panel: rgba(18, 20, 17, 0.78);
      --field: rgba(255, 255, 255, 0.07);
      --gold: #c9a86a;
      --shadow: 0 28px 90px rgba(0, 0, 0, 0.42);
      --max: 1200px;
    }
    * { box-sizing: border-box; }
    html { min-height: 100%; scroll-behavior: smooth; background: var(--bg); }
    body {
      min-height: 100%;
      margin: 0;
      background:
        radial-gradient(circle at 15% 4%, rgba(201, 168, 106, 0.26), transparent 27rem),
        radial-gradient(circle at 88% 16%, rgba(142, 167, 200, 0.20), transparent 25rem),
        linear-gradient(180deg, #090a08 0%, #11120f 44%, #070806 100%);
      color: var(--ink);
      font-family: Outfit, "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
      letter-spacing: 0;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 0;
      opacity: 0.18;
      background-image:
        linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px);
      background-size: 72px 72px;
      mask-image: linear-gradient(180deg, black, transparent 88%);
    }
    button, textarea { font: inherit; }
    button { border: 0; cursor: pointer; }
    a { color: inherit; }
    main {
      position: relative;
      z-index: 1;
      width: 100%;
      max-width: 100%;
      overflow-x: hidden;
    }
    .section {
      width: min(calc(100% - 40px), var(--max));
      margin: 0 auto;
      padding: 130px 0;
    }
    .nav {
      position: fixed;
      z-index: 40;
      top: 18px;
      left: 50%;
      width: min(calc(100% - 32px), 1040px);
      transform: translateX(-50%);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 12px 10px 16px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(7, 8, 6, 0.72);
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.34);
      backdrop-filter: blur(20px);
    }
    .brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
      font-weight: 800;
      white-space: nowrap;
    }
    .brand-mark {
      width: 32px;
      height: 32px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--ink), var(--gold));
      color: #10120f;
      font-size: 15px;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.34);
    }
    .nav-links {
      display: flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .nav-links a,
    .nav-links button {
      padding: 8px 12px;
      border-radius: 999px;
      color: inherit;
      text-decoration: none;
      background: transparent;
    }
    .nav-links a:hover,
    .nav-links button:hover { color: var(--ink); background: rgba(255,255,255,0.08); }
    .nav-cta {
      padding: 10px 15px;
      border-radius: 999px;
      color: #12130f;
      background: var(--ink);
      font-weight: 800;
      white-space: nowrap;
    }
    .hero {
      min-height: 100svh;
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
      align-items: center;
      gap: 36px;
      padding-top: 128px;
      padding-bottom: 84px;
    }
    .hero-copy { max-width: 720px; }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 22px;
      color: var(--soft);
      font-size: 13px;
      font-weight: 700;
    }
    .eyebrow::before {
      content: "";
      width: 42px;
      height: 1px;
      background: var(--gold);
    }
    .hero h1 {
      max-width: 72rem;
      margin: 0;
      font-size: clamp(3.05rem, 6vw, 6.25rem);
      line-height: 0.93;
      letter-spacing: 0;
      text-wrap: balance;
    }
    .inline-image {
      display: inline-block;
      width: clamp(76px, 10vw, 132px);
      height: clamp(34px, 5vw, 54px);
      margin: 0 10px;
      border: 1px solid rgba(255,255,255,0.35);
      border-radius: 999px;
      vertical-align: middle;
      background-image:
        linear-gradient(rgba(0,0,0,0.14), rgba(0,0,0,0.18)),
        url("https://picsum.photos/seed/nju-stone-path/640/320");
      background-size: cover;
      background-position: center;
      filter: grayscale(0.18) contrast(1.18);
    }
    .hero-copy p {
      max-width: 640px;
      margin: 26px 0 0;
      color: var(--muted);
      font-size: clamp(1.02rem, 1.5vw, 1.25rem);
      line-height: 1.72;
    }
    .hero-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 34px;
    }
    .button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 9px;
      min-height: 48px;
      padding: 0 18px;
      border-radius: 999px;
      font-weight: 800;
      text-decoration: none;
    }
    .button-primary { color: #11120f; background: var(--ink); }
    .button-secondary { color: var(--ink); background: rgba(255,255,255,0.09); border: 1px solid var(--line-strong); }
    .button:hover { transform: translateY(-1px); }
    .hero-visual { position: relative; min-height: 560px; }
    .hero-photo {
      position: absolute;
      inset: 58px 0 0 64px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow);
      background:
        linear-gradient(180deg, rgba(7,8,6,0.16), rgba(7,8,6,0.58)),
        url("https://picsum.photos/seed/nanjing-university-night/1100/1300");
      background-size: cover;
      background-position: center;
      filter: grayscale(0.2) contrast(1.18) saturate(0.82);
    }
    .chat-preview {
      position: absolute;
      left: 0;
      right: 48px;
      bottom: 0;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(13, 15, 12, 0.88);
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px);
    }
    .preview-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
    }
    .preview-body { display: grid; gap: 10px; padding: 16px; }
    .preview-line {
      width: fit-content;
      max-width: 92%;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--soft);
      background: rgba(255,255,255,0.06);
      line-height: 1.55;
    }
    .preview-line.answer {
      margin-left: auto;
      color: #11120f;
      background: var(--ink);
    }
    .marquee {
      border-block: 1px solid var(--line);
      overflow: hidden;
      background: rgba(255,255,255,0.035);
    }
    .marquee-track {
      display: flex;
      width: max-content;
      animation: marquee 28s linear infinite;
    }
    .marquee span {
      padding: 18px 30px;
      color: var(--muted);
      font-size: clamp(1rem, 2vw, 1.45rem);
      font-weight: 800;
      white-space: nowrap;
    }
    @keyframes marquee {
      from { transform: translateX(0); }
      to { transform: translateX(-50%); }
    }
    .section-head {
      display: grid;
      grid-template-columns: minmax(0, 0.95fr) minmax(260px, 0.55fr);
      gap: 40px;
      align-items: end;
      margin-bottom: 36px;
    }
    .section-head h2 {
      max-width: 820px;
      margin: 0;
      font-size: clamp(2.2rem, 4.6vw, 4.7rem);
      line-height: 0.98;
      letter-spacing: 0;
    }
    .section-head p {
      margin: 0;
      color: var(--muted);
      line-height: 1.72;
    }
    .bento {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      grid-auto-rows: minmax(220px, auto);
      grid-auto-flow: dense;
      gap: 12px;
    }
    .bento-card {
      position: relative;
      min-height: 220px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      transition: transform 700ms ease, border-color 700ms ease, background 700ms ease;
    }
    .bento-card:hover {
      transform: translateY(-4px);
      border-color: var(--line-strong);
      background: rgba(26, 28, 24, 0.88);
    }
    .bento-card.primary { grid-column: span 5; grid-row: span 2; }
    .bento-card.wide { grid-column: span 4; }
    .bento-card.compact { grid-column: span 3; }
    .bento-card.long { grid-column: span 7; }
    .card-media {
      position: absolute;
      inset: 0;
      background-size: cover;
      background-position: center;
      filter: grayscale(0.34) contrast(1.2);
      transform: scale(1);
      transition: transform 700ms ease, opacity 700ms ease;
      opacity: 0.45;
    }
    .bento-card:hover .card-media { transform: scale(1.05); opacity: 0.62; }
    .card-content {
      position: relative;
      z-index: 1;
      min-height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 24px;
      padding: 24px;
    }
    .card-content h3 {
      max-width: 520px;
      margin: 0;
      font-size: clamp(1.45rem, 2vw, 2.25rem);
      line-height: 1.04;
    }
    .card-content p {
      margin: 0;
      color: var(--muted);
      line-height: 1.65;
    }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .mini-metric {
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .mini-metric strong {
      display: block;
      font-size: clamp(1.4rem, 2.5vw, 2.5rem);
      line-height: 1;
    }
    .mini-metric span {
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .ask-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 14px;
      align-items: stretch;
    }
    .chat-surface,
    .sources-dock {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(11, 13, 10, 0.82);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .chat-toolbar,
    .dock-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
    }
    .chat-toolbar strong,
    .dock-head strong { font-size: 15px; }
    .state {
      min-width: 86px;
      color: var(--muted);
      font-size: 13px;
      text-align: right;
    }
    .messages {
      height: min(52vh, 520px);
      min-height: 360px;
      overflow-y: auto;
      padding: 22px 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      scrollbar-width: thin;
    }
    .message {
      display: grid;
      grid-template-columns: 34px minmax(0, 760px);
      gap: 10px;
      align-items: start;
    }
    .message.user {
      grid-template-columns: minmax(0, 760px) 34px;
      justify-content: end;
    }
    .avatar {
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 50%;
      color: var(--ink);
      background: rgba(255,255,255,0.07);
      font-size: 13px;
      font-weight: 800;
      user-select: none;
    }
    .assistant .avatar {
      color: #11120f;
      background: var(--ink);
    }
    .user .avatar {
      grid-column: 2;
      color: #11120f;
      background: var(--gold);
    }
    .bubble {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px 15px;
      background: rgba(255,255,255,0.055);
      color: var(--soft);
      line-height: 1.72;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .user .bubble {
      grid-column: 1;
      grid-row: 1;
      color: #11120f;
      background: #eadfcf;
      border-color: rgba(255,255,255,0.34);
    }
    .message-text:empty::after {
      content: "正在检索知识库...";
      color: var(--muted);
    }
    .message-text code {
      padding: 1px 5px;
      border-radius: 5px;
      background: rgba(255,255,255,0.12);
      font-family: Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
    }
    .composer {
      border-top: 1px solid var(--line);
      padding: 16px 18px 18px;
      background: rgba(0,0,0,0.12);
    }
    .composer-box {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      align-items: end;
      gap: 10px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      padding: 10px;
      background: var(--field);
    }
    textarea {
      width: 100%;
      min-height: 46px;
      max-height: 150px;
      resize: none;
      border: 0;
      outline: 0;
      padding: 10px 8px;
      color: var(--ink);
      background: transparent;
      line-height: 1.55;
    }
    textarea::placeholder { color: rgba(245,240,232,0.44); }
    .send,
    .ghost-button {
      min-height: 42px;
      padding: 0 15px;
      border-radius: 999px;
      font-weight: 800;
      white-space: nowrap;
    }
    .send {
      color: #11120f;
      background: var(--ink);
    }
    .send.is-stop {
      color: var(--ink);
      background: #8d3e3e;
    }
    .ghost-button {
      color: var(--ink);
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.055);
    }
    .send:disabled { opacity: 0.56; cursor: not-allowed; }
    .composer-hint {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .sources-dock {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      min-height: 100%;
    }
    .dock-head span {
      color: var(--muted);
      font-size: 12px;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .tag {
      padding: 6px 9px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--soft);
      background: rgba(255,255,255,0.055);
      font-size: 12px;
    }
    .sources {
      overflow-y: auto;
      display: grid;
      align-content: start;
      gap: 10px;
      padding: 14px 16px 16px;
    }
    .source {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: rgba(255,255,255,0.045);
      transition: transform 700ms ease, background 700ms ease;
    }
    .source:hover {
      transform: translateY(-2px);
      background: rgba(255,255,255,0.075);
    }
    .source strong {
      display: block;
      margin-bottom: 5px;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.45;
    }
    .source small {
      display: block;
      color: var(--muted);
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .empty-panel {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.65;
    }
    .accordion {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      min-height: 420px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .accordion-item {
      position: relative;
      overflow: hidden;
      min-width: 0;
      border-right: 1px solid var(--line);
      background-size: cover;
      background-position: center;
      filter: grayscale(0.18) contrast(1.16);
      transition: flex 700ms ease, filter 700ms ease;
    }
    .accordion-item:last-child { border-right: 0; }
    .accordion-item::before {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(7,8,6,0.10), rgba(7,8,6,0.72));
    }
    .accordion-inner {
      position: absolute;
      inset: auto 18px 18px;
      z-index: 1;
    }
    .accordion-inner h3 {
      margin: 0 0 8px;
      font-size: clamp(1.2rem, 2vw, 2.1rem);
      line-height: 1.05;
    }
    .accordion-inner p {
      max-width: 360px;
      margin: 0;
      color: var(--soft);
      line-height: 1.62;
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 500ms ease, transform 500ms ease;
    }
    .accordion:hover { grid-template-columns: 0.85fr 1.35fr 0.9fr 0.9fr; }
    .accordion-item:hover { filter: grayscale(0) contrast(1.12); }
    .accordion-item:hover p { opacity: 1; transform: translateY(0); }
    .desire {
      display: grid;
      grid-template-columns: minmax(280px, 0.85fr) minmax(0, 1fr);
      gap: 54px;
      align-items: start;
    }
    .sticky-copy {
      position: sticky;
      top: 120px;
    }
    .sticky-copy h2 {
      margin: 0;
      font-size: clamp(2.4rem, 4.8vw, 5rem);
      line-height: 0.96;
    }
    .reveal-copy {
      margin: 24px 0 0;
      color: var(--muted);
      font-size: clamp(1.05rem, 1.8vw, 1.35rem);
      line-height: 1.78;
    }
    .reveal-word { opacity: 0.18; }
    .media-stack {
      display: grid;
      gap: 16px;
    }
    .media-panel {
      min-height: 430px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background-size: cover;
      background-position: center;
      filter: grayscale(0.16) contrast(1.18);
      transform: scale(0.92);
      opacity: 0.88;
    }
    .footer-cta {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 24px;
      align-items: end;
      padding: 48px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background:
        linear-gradient(135deg, rgba(234,223,207,0.12), rgba(142,167,200,0.08)),
        rgba(255,255,255,0.045);
    }
    .footer-cta h2 {
      max-width: 820px;
      margin: 0;
      font-size: clamp(2.35rem, 5vw, 5.4rem);
      line-height: 0.94;
    }
    .footer-cta p {
      max-width: 620px;
      margin: 20px 0 0;
      color: var(--muted);
      line-height: 1.7;
    }
    .footer-line {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-top: 28px;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 980px) {
      .section { padding: 96px 0; }
      .hero,
      .section-head,
      .ask-grid,
      .desire,
      .footer-cta { grid-template-columns: 1fr; }
      .hero { padding-top: 112px; }
      .hero-visual { min-height: 480px; }
      .hero-photo { inset: 32px 0 0 24px; }
      .chat-preview { right: 20px; }
      .bento { grid-template-columns: repeat(6, minmax(0, 1fr)); }
      .bento-card.primary,
      .bento-card.wide,
      .bento-card.compact,
      .bento-card.long { grid-column: span 6; grid-row: span 1; }
      .accordion { grid-template-columns: 1fr; }
      .accordion:hover { grid-template-columns: 1fr; }
      .accordion-item {
        min-height: 260px;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .accordion-item:last-child { border-bottom: 0; }
      .accordion-inner p { opacity: 1; transform: none; }
    }
    @media (max-width: 700px) {
      .nav { top: 10px; width: calc(100% - 20px); }
      .nav-links a { display: none; }
      .nav-cta { padding-inline: 12px; }
      .section { width: min(calc(100% - 24px), var(--max)); padding: 72px 0; }
      .hero { min-height: auto; padding-top: 104px; }
      .hero h1 { font-size: clamp(2.55rem, 14vw, 4.4rem); }
      .hero-actions { display: grid; }
      .hero-visual { min-height: 410px; }
      .hero-photo { inset: 0; }
      .chat-preview { left: 12px; right: 12px; }
      .marquee span { padding-inline: 20px; }
      .messages { height: 58vh; min-height: 320px; padding: 16px 12px; }
      .message,
      .message.user { grid-template-columns: 30px minmax(0, 1fr); }
      .message.user { grid-template-columns: minmax(0, 1fr) 30px; }
      .avatar { width: 30px; height: 30px; }
      .composer-box { grid-template-columns: minmax(0, 1fr); }
      .send,
      .ghost-button { width: 100%; }
      .composer-hint { display: none; }
      .sources-dock { min-height: 360px; }
      .footer-cta { padding: 28px; }
      .footer-line { display: grid; }
    }
  </style>
</head>
<body>
  <nav class="nav" aria-label="主导航">
    <div class="brand"><span class="brand-mark">P</span><span>PAIMON</span></div>
    <div class="nav-links">
      <a href="#knowledge">知识结构</a>
      <a href="#ask">开始提问</a>
      <a href="#evidence">证据链</a>
      <button id="navClearBtn" type="button">新会话</button>
    </div>
    <a class="nav-cta" href="#ask">提问</a>
  </nav>

  <main>
    <section class="section hero" id="top">
      <div class="hero-copy">
        <div class="eyebrow">南京大学新生问答 RAG</div>
        <h1>把入学问题<span class="inline-image" aria-hidden="true"></span>变成可引用的答案。</h1>
        <p>PAIMON 读取本地知识库、群聊问答和文档资料，用流式输出回答报到、校园卡、宿舍、医保、选课、社团和校园网等高频问题，并把证据来源放在同一屏里。</p>
        <div class="hero-actions">
          <a class="button button-primary" href="#ask">立即提问</a>
          <a class="button button-secondary" href="#knowledge">查看知识结构</a>
        </div>
      </div>
      <div class="hero-visual" aria-hidden="true">
        <div class="hero-photo"></div>
        <div class="chat-preview">
          <div class="preview-head"><span>实时问答</span><span>GraphRAG + 引用</span></div>
          <div class="preview-body">
            <div class="preview-line">校园卡丢了怎么办？</div>
            <div class="preview-line answer">先挂失，再按校区流程补办；我会附上对应来源。</div>
          </div>
        </div>
      </div>
    </section>

    <div class="marquee" aria-hidden="true">
      <div class="marquee-track">
        <span>报到流程</span><span>校园卡</span><span>宿舍网络</span><span>医保就诊</span><span>选课指导</span><span>社团活动</span><span>校史资料</span><span>引用溯源</span>
        <span>报到流程</span><span>校园卡</span><span>宿舍网络</span><span>医保就诊</span><span>选课指导</span><span>社团活动</span><span>校史资料</span><span>引用溯源</span>
      </div>
    </div>

    <section class="section" id="knowledge">
      <div class="section-head">
        <h2>不只是聊天框，是一套可检查的知识检索台。</h2>
        <p>页面把知识规模、检索模式、图谱状态和 LLM 状态前置，回答过程里同步展示置信度、意图、质量和命中的来源，让用户知道答案从哪里来。</p>
      </div>
      <div class="bento">
        <article class="bento-card primary">
          <div class="card-media" style="background-image:url('https://picsum.photos/seed/nju-library-index/900/1100')"></div>
          <div class="card-content">
            <h3>本地资料优先，回答默认带证据。</h3>
            <p>知识库覆盖 QA、文档、群聊资料和校园专题，适合处理新生入学的具体流程问题。</p>
            <div class="metric-row">
              <div class="mini-metric"><strong>__CHUNK_COUNT__</strong><span>知识块</span></div>
              <div class="mini-metric"><strong>__RAG_MODE__</strong><span>检索模式</span></div>
            </div>
          </div>
        </article>
        <article class="bento-card wide">
          <div class="card-content">
            <h3>来源不会躲在答案背后。</h3>
            <p>每次回答后，右侧证据栏会列出来源标题、路径和分数，便于继续核对。</p>
          </div>
        </article>
        <article class="bento-card compact">
          <div class="card-content">
            <h3>LLM __LLM_STATUS__</h3>
            <p>未配置模型时仍可降级为抽取式回答，核心功能不被外部服务卡住。</p>
          </div>
        </article>
        <article class="bento-card long">
          <div class="card-content">
            <h3>图谱状态：__GRAPH_STATUS__</h3>
            <p>当图谱启用时，命中的社区会作为诊断标签展示，帮助理解答案路径。</p>
          </div>
        </article>
      </div>
    </section>

    <section class="section" id="ask">
      <div class="section-head">
        <h2>现在问，边生成边看来源。</h2>
        <p>Enter 发送，Shift + Enter 换行。生成时可停止；新会话会同时清空服务端会话与本地界面。</p>
      </div>
      <div class="ask-grid">
        <section class="chat-surface" aria-label="PAIMON 问答">
          <div class="chat-toolbar">
            <strong>PAIMON 对话</strong>
            <span class="state" id="state" aria-live="polite">准备就绪</span>
          </div>
          <section class="messages" id="messages" aria-live="polite"></section>
          <form class="composer" id="composer">
            <div class="composer-box">
              <textarea id="question" rows="1" placeholder="例如：统一身份认证密码忘了怎么办？"></textarea>
              <button class="ghost-button" id="clearBtn" type="button">新会话</button>
              <button class="send" id="sendBtn" type="submit">发送</button>
            </div>
            <div class="composer-hint">
              <span>试试下面的问题，或直接输入自己的问题。</span>
              <span>回答会附带引用来源和诊断标签。</span>
            </div>
          </form>
        </section>
        <aside class="sources-dock" id="sourcesDock" aria-label="引用与诊断">
          <div class="dock-head">
            <div>
              <strong>引用与诊断</strong><br>
              <span>本轮答案的检索证据</span>
            </div>
          </div>
          <div class="meta" id="meta"></div>
          <div class="sources" id="sources"></div>
        </aside>
      </div>
      <div class="hero-actions" style="margin-top:14px">
        <button class="button button-secondary example" data-question="校园卡丢了怎么补办？" type="button">校园卡补办</button>
        <button class="button button-secondary example" data-question="南大有哪些社团可以参加？" type="button">社团推荐</button>
        <button class="button button-secondary example" data-question="宿舍校园网和路由器怎么用？" type="button">宿舍网络</button>
      </div>
    </section>

    <section class="section" id="evidence">
      <div class="desire">
        <div class="sticky-copy">
          <h2>答案要好看，也要经得起追问。</h2>
          <p class="reveal-copy" id="revealCopy">PAIMON 会把检索诊断、来源标题、置信度和图谱命中放在可见区域。用户不需要猜答案是否来自资料库，也不需要在滚动里找证据。</p>
        </div>
        <div class="media-stack">
          <div class="media-panel" style="background-image:url('https://picsum.photos/seed/nju-archive-paper/1000/760')"></div>
          <div class="media-panel" style="background-image:url('https://picsum.photos/seed/nju-campus-map/1000/760')"></div>
          <div class="media-panel" style="background-image:url('https://picsum.photos/seed/nju-study-hall/1000/760')"></div>
        </div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>从问题到证据，压成四个清晰动作。</h2>
        <p>这组横向展开的流程面板用于解释系统的工作方式，也给用户一个更稳的心理预期。</p>
      </div>
      <div class="accordion">
        <article class="accordion-item" style="background-image:url('https://picsum.photos/seed/nju-question/900/900')">
          <div class="accordion-inner"><h3>输入问题</h3><p>支持自然语言提问，适合报到、生活、教学、校园服务等场景。</p></div>
        </article>
        <article class="accordion-item" style="background-image:url('https://picsum.photos/seed/nju-search/900/900')">
          <div class="accordion-inner"><h3>混合检索</h3><p>结合关键词、相似度、重排和图谱线索，先找出可靠候选资料。</p></div>
        </article>
        <article class="accordion-item" style="background-image:url('https://picsum.photos/seed/nju-answer/900/900')">
          <div class="accordion-inner"><h3>流式回答</h3><p>答案逐字输出，生成期间可以停止，减少等待时的不确定感。</p></div>
        </article>
        <article class="accordion-item" style="background-image:url('https://picsum.photos/seed/nju-citation/900/900')">
          <div class="accordion-inner"><h3>引用核对</h3><p>来源和诊断单独呈现，方便用户继续判断、复核或补充提问。</p></div>
        </article>
      </div>
    </section>

    <section class="section">
      <div class="footer-cta">
        <div>
          <h2>把下一条新生问题交给 PAIMON。</h2>
          <p>页面已经连接当前本地服务。点击按钮会回到输入区并聚焦，继续使用流式问答。</p>
        </div>
        <a class="button button-primary" href="#ask" id="footerAsk">开始</a>
      </div>
      <div class="footer-line">
        <span>PAIMON Next</span>
        <span>本地知识库优先，关键信息仍建议以学校最新官方通知为准。</span>
      </div>
    </section>
  </main>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js"></script>
  <script>
    const messagesEl = document.getElementById("messages");
    const questionEl = document.getElementById("question");
    const composerEl = document.getElementById("composer");
    const sendBtn = document.getElementById("sendBtn");
    const clearBtn = document.getElementById("clearBtn");
    const navClearBtn = document.getElementById("navClearBtn");
    const stateEl = document.getElementById("state");
    const sourcesEl = document.getElementById("sources");
    const metaEl = document.getElementById("meta");
    const sessionId = "web-" + Math.random().toString(16).slice(2);
    let isSending = false;
    let abortController = null;

    const greeting = "你好，我是 PAIMON。你可以问我报到、校园卡、宿舍、医保、选课、社团、校园网等新生问题。我会尽量基于知识库回答，并给出引用来源。";

    resetConversation();
    initMotion();

    composerEl.addEventListener("submit", (event) => {
      event.preventDefault();
      if (isSending) {
        stopStream();
        return;
      }
      askStream();
    });

    questionEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        if (!isSending) askStream();
      }
    });

    questionEl.addEventListener("input", resizeQuestion);

    [clearBtn, navClearBtn].forEach((button) => {
      button.addEventListener("click", clearConversation);
    });

    document.querySelectorAll("[href='#ask'], #footerAsk").forEach((link) => {
      link.addEventListener("click", () => setTimeout(() => questionEl.focus(), 450));
    });

    document.querySelectorAll("[data-question]").forEach((button) => {
      button.addEventListener("click", () => {
        questionEl.value = button.dataset.question || "";
        resizeQuestion();
        document.getElementById("ask").scrollIntoView({ behavior: "smooth", block: "start" });
        setTimeout(() => questionEl.focus(), 420);
      });
    });

    async function askStream() {
      const question = questionEl.value.trim();
      if (!question || isSending) return;

      setBusy(true, "正在检索");
      clearSources();
      appendMessage("user", question);
      const assistantMessage = appendMessage("assistant", "");
      questionEl.value = "";
      resizeQuestion();

      let answerText = "";
      abortController = new AbortController();

      try {
        const response = await fetch("/ask/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, session_id: sessionId, top_k: 5 }),
          signal: abortController.signal
        });
        if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
        stateEl.textContent = "正在生成";
        await readSSE(response.body, (eventName, payload) => {
          if (eventName === "delta") {
            answerText += payload.delta || "";
            setMessageText(assistantMessage, answerText);
          }
          if (eventName === "final") {
            renderFinal(payload.result || {}, assistantMessage, answerText);
          }
        });
      } catch (error) {
        if (error.name === "AbortError") {
          if (!answerText) setMessageText(assistantMessage, "已停止生成。");
          stateEl.textContent = "已停止";
        } else {
          setMessageText(assistantMessage, "请求失败：" + error.message);
          stateEl.textContent = "请求失败";
        }
      } finally {
        abortController = null;
        setBusy(false, stateEl.textContent === "已停止" ? "已停止" : "准备就绪");
        questionEl.focus();
      }
    }

    function stopStream() {
      if (abortController) abortController.abort();
    }

    async function readSSE(body, onEvent) {
      const reader = body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const eventSeparator = String.fromCharCode(10) + String.fromCharCode(10);
        const events = buffer.split(eventSeparator);
        buffer = events.pop() || "";
        for (const block of events) parseSSEBlock(block, onEvent);
      }
      if (buffer.trim()) parseSSEBlock(buffer, onEvent);
    }

    function parseSSEBlock(block, onEvent) {
      const lines = block.split(String.fromCharCode(10));
      let eventName = "message";
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) return;
      onEvent(eventName, JSON.parse(dataLines.join(String.fromCharCode(10))));
    }

    function appendMessage(role, text) {
      const article = document.createElement("article");
      article.className = "message " + role;
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = role === "assistant" ? "P" : "你";
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      const messageText = document.createElement("div");
      messageText.className = "message-text";
      messageText.textContent = text;
      bubble.appendChild(messageText);
      article.appendChild(avatar);
      article.appendChild(bubble);
      messagesEl.appendChild(article);
      scrollToBottom();
      return article;
    }

    function setMessageText(article, text) {
      article.querySelector(".message-text").textContent = text;
      scrollToBottom();
    }

    function setMessageHtml(article, text) {
      article.querySelector(".message-text").innerHTML = formatAnswer(text);
      scrollToBottom();
    }

    function renderFinal(result, assistantMessage, streamedText) {
      const answer = result.answer || streamedText;
      if (answer) setMessageHtml(assistantMessage, answer);
      stateEl.textContent = "完成";
      renderMeta(result);
      renderSources(result.sources || []);
    }

    function renderMeta(result) {
      const diagnostics = result.diagnostics || {};
      const graph = diagnostics.graph || {};
      metaEl.innerHTML = "";
      addTag("置信度 " + (result.confidence ?? "未知"));
      if (result.intent) addTag("意图 " + result.intent);
      if (diagnostics.mode) addTag(diagnostics.mode);
      if (diagnostics.quality !== undefined) addTag("质量 " + diagnostics.quality);
      if (graph.matched_communities && graph.matched_communities.length) {
        addTag("图谱 " + graph.matched_communities.slice(0, 2).join(" / "));
      }
      if (!metaEl.children.length) addTag("暂无诊断");
    }

    function addTag(text) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = text;
      metaEl.appendChild(tag);
    }

    function renderSources(sources) {
      sourcesEl.innerHTML = "";
      if (!sources.length) {
        const empty = document.createElement("div");
        empty.className = "empty-panel";
        empty.textContent = "本轮回答没有返回引用来源。";
        sourcesEl.appendChild(empty);
        return;
      }
      for (const source of sources.slice(0, 6)) {
        const item = document.createElement("div");
        item.className = "source";
        const title = document.createElement("strong");
        title.textContent = "[" + source.id + "] " + (source.title || "来源");
        const detail = document.createElement("small");
        detail.textContent = source.score === undefined
          ? (source.source || "")
          : (source.source || "") + " · score " + source.score;
        item.appendChild(title);
        item.appendChild(detail);
        sourcesEl.appendChild(item);
      }
    }

    function clearSources() {
      metaEl.innerHTML = '<span class="tag">正在检索</span>';
      sourcesEl.innerHTML = '<div class="empty-panel">等待本轮回答的引用来源。</div>';
    }

    async function clearConversation() {
      if (isSending) stopStream();
      try {
        await fetch("/clear?session_id=" + encodeURIComponent(sessionId));
      } catch (error) {
        // Local reset still keeps the interface usable if the clear request fails.
      }
      resetConversation();
      document.getElementById("ask").scrollIntoView({ behavior: "smooth", block: "start" });
      setTimeout(() => questionEl.focus(), 360);
    }

    function resetConversation() {
      messagesEl.innerHTML = "";
      appendMessage("assistant", greeting);
      metaEl.innerHTML = '<span class="tag">等待提问</span>';
      sourcesEl.innerHTML = '<div class="empty-panel">还没有引用来源。发送问题后，PAIMON 会在这里列出检索到的证据。</div>';
      stateEl.textContent = "准备就绪";
    }

    function setBusy(busy, label) {
      isSending = busy;
      sendBtn.classList.toggle("is-stop", busy);
      sendBtn.textContent = busy ? "停止" : "发送";
      stateEl.textContent = label;
    }

    function resizeQuestion() {
      questionEl.style.height = "auto";
      questionEl.style.height = Math.min(questionEl.scrollHeight, 150) + "px";
    }

    function scrollToBottom() {
      requestAnimationFrame(() => {
        messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: "smooth" });
      });
    }

    function formatAnswer(value) {
      let html = escapeHtml(value || "");
      html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noreferrer">$1</a>');
      return html.replace(/\n/g, "<br>");
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[ch]));
    }

    function initMotion() {
      if (!window.gsap || !window.ScrollTrigger) return;
      gsap.registerPlugin(ScrollTrigger);
      gsap.from(".hero-copy > *", {
        y: 26,
        opacity: 0,
        duration: 0.9,
        stagger: 0.08,
        ease: "power3.out"
      });
      gsap.from(".hero-photo, .chat-preview", {
        scale: 0.92,
        opacity: 0,
        duration: 1.1,
        stagger: 0.12,
        ease: "power3.out"
      });
      document.querySelectorAll(".media-panel").forEach((panel) => {
        gsap.fromTo(panel,
          { scale: 0.82, opacity: 0.42 },
          {
            scale: 1,
            opacity: 1,
            ease: "none",
            scrollTrigger: {
              trigger: panel,
              start: "top 86%",
              end: "bottom 24%",
              scrub: true
            }
          }
        );
        gsap.to(panel, {
          opacity: 0.22,
          ease: "none",
          scrollTrigger: {
            trigger: panel,
            start: "bottom 42%",
            end: "bottom 12%",
            scrub: true
          }
        });
      });
      const copy = document.getElementById("revealCopy");
      const words = copy.textContent.trim().split(/\s+/);
      copy.innerHTML = words.map((word) => '<span class="reveal-word">' + word + '</span>').join(" ");
      gsap.to(".reveal-word", {
        opacity: 1,
        stagger: 0.08,
        ease: "none",
        scrollTrigger: {
          trigger: copy,
          start: "top 72%",
          end: "bottom 34%",
          scrub: true
        }
      });
    }
  </script>
</body>
</html>"""
    return (
        html.replace("__CHUNK_COUNT__", str(chunk_count))
        .replace("__RAG_MODE__", rag_mode)
        .replace("__GRAPH_STATUS__", graph_status)
        .replace("__LLM_STATUS__", llm_status)
    )


def run_server(
    host: str = "127.0.0.1",
    port: int = 8002,
    root: str | Path | None = None,
) -> None:
    config = RAGConfig.from_env(root or Path.cwd())
    assistant = NewStudentAssistant(config)

    class Handler(PAIMONRequestHandler):
        pass

    Handler.assistant = assistant
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PAIMON Next is running at http://{host}:{port}")
    print(f"Loaded {len(assistant.chunks)} knowledge chunks. LLM enabled: {assistant.llm.enabled}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down PAIMON Next.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PAIMON Next HTTP API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8002, type=int)
    parser.add_argument("--root", default=str(Path.cwd()))
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, root=args.root)


if __name__ == "__main__":
    main()
