"use client";

import {
  AlertDialog,
  Button,
  IconButton,
  TextField,
  Tooltip,
} from "@radix-ui/themes";
import {
  ChatCenteredText,
  Check,
  List,
  MagnifyingGlass,
  Moon,
  NotePencil,
  PaperPlaneRight,
  PencilSimple,
  Plus,
  SidebarSimple,
  Square,
  Sun,
  Trash,
  X,
} from "@phosphor-icons/react";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, openRunStream } from "@/lib/api";
import type { ConversationSummary, Message as ChatMessage } from "@/lib/types";
import { useAppearance } from "@/components/theme-provider";

const SUGGESTIONS = [
  "统一身份认证密码忘了怎么办？",
  "新生报到需要准备哪些材料？",
  "校园卡丢失后怎么补办？",
  "仙林校区宿舍有哪些注意事项？",
];

export function ChatShell() {
  const { appearance, toggleAppearance } = useAppearance();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [statusText, setStatusText] = useState("准备就绪");
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const streamRef = useRef<EventSource | null>(null);
  const runIdRef = useRef<string | null>(null);
  const temporaryMessageIdRef = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);

  const refreshConversations = useCallback(async () => {
    const items = await api.listConversations();
    setConversations(items);
    return items;
  }, []);

  const openConversation = useCallback(async (id: string) => {
    setLoadingConversation(true);
    setError(null);
    try {
      const conversation = await api.getConversation(id);
      setActiveConversationId(id);
      setMessages(conversation.messages);
      setSidebarOpen(false);
    } catch (requestError) {
      setError(toErrorMessage(requestError));
    } finally {
      setLoadingConversation(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void api
      .listConversations()
      .then(async (items) => {
        if (!active) return;
        setConversations(items);
        if (!items[0]) return;

        setLoadingConversation(true);
        const conversation = await api.getConversation(items[0].id);
        if (!active) return;
        setActiveConversationId(conversation.id);
        setMessages(conversation.messages);
      })
      .catch((requestError) => {
        if (active) setError(toErrorMessage(requestError));
      })
      .finally(() => {
        if (active) {
          setLoadingHistory(false);
          setLoadingConversation(false);
        }
      });
    return () => {
      active = false;
      streamRef.current?.close();
    };
  }, []);

  const filteredConversations = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    if (!query) return conversations;
    return conversations.filter((item) => item.title.toLocaleLowerCase("zh-CN").includes(query));
  }, [conversations, search]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || messages.length === 0) return;
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior: streaming ? "smooth" : "auto",
    });
  }, [messages, streaming]);

  async function createNewConversation() {
    setError(null);
    try {
      const conversation = await api.createConversation();
      setConversations((current) => [conversation, ...current]);
      setActiveConversationId(conversation.id);
      setMessages([]);
      setSidebarOpen(false);
      requestAnimationFrame(() => textareaRef.current?.focus());
    } catch (requestError) {
      setError(toErrorMessage(requestError));
    }
  }

  async function submitRename(event: FormEvent, id: string) {
    event.preventDefault();
    const title = editingTitle.trim();
    if (!title) return;
    try {
      const updated = await api.renameConversation(id, title);
      setConversations((current) => current.map((item) => (item.id === id ? updated : item)));
      setEditingId(null);
    } catch (requestError) {
      setError(toErrorMessage(requestError));
    }
  }

  async function deleteConversation(id: string) {
    try {
      await api.deleteConversation(id);
      const remaining = conversations.filter((item) => item.id !== id);
      setConversations(remaining);
      if (activeConversationId === id) {
        if (remaining[0]) await openConversation(remaining[0].id);
        else {
          setActiveConversationId(null);
          setMessages([]);
        }
      }
    } catch (requestError) {
      setError(toErrorMessage(requestError));
    }
  }

  async function sendMessage(contentOverride?: string) {
    const content = (contentOverride ?? input).trim();
    if (!content || streaming) return;
    setError(null);
    setStatusText("正在创建任务");

    try {
      let conversationId = activeConversationId;
      if (!conversationId) {
        const conversation = await api.createConversation();
        conversationId = conversation.id;
        setActiveConversationId(conversation.id);
        setConversations((current) => [conversation, ...current]);
      }
      const accepted = await api.sendMessage(conversationId, content);
      const temporaryMessageId = `streaming-${accepted.run_id}`;
      temporaryMessageIdRef.current = temporaryMessageId;
      runIdRef.current = accepted.run_id;
      setMessages((current) => [
        ...current,
        accepted.input_message,
        {
          id: temporaryMessageId,
          conversation_id: conversationId,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
        },
      ]);
      setInput("");
      setStreaming(true);
      setStatusText("正在处理");

      streamRef.current = openRunStream(accepted.run_id, {
        onDelta: ({ text }) => {
          setStatusText("正在生成");
          setMessages((current) =>
            current.map((message) =>
              message.id === temporaryMessageId
                ? { ...message, content: message.content + text }
                : message,
            ),
          );
        },
        onCompleted: ({ message }) => {
          setMessages((current) =>
            current.map((item) => (item.id === temporaryMessageId ? message : item)),
          );
          finishStream("回答完成");
          void refreshConversations();
        },
        onFailed: ({ message }) => {
          setMessages((current) => current.filter((item) => item.id !== temporaryMessageId));
          setError(message || "本次任务未能完成");
          finishStream("任务失败");
        },
        onCancelled: () => {
          setMessages((current) => current.filter((item) => item.id !== temporaryMessageId));
          finishStream("已停止生成");
        },
        onConnectionError: () => {
          setError("流式连接已断开，请稍后重试");
          finishStream("连接已断开");
        },
      });
    } catch (requestError) {
      setError(toErrorMessage(requestError));
      finishStream("发送失败");
    }
  }

  function finishStream(nextStatus: string) {
    streamRef.current?.close();
    streamRef.current = null;
    runIdRef.current = null;
    temporaryMessageIdRef.current = null;
    setStreaming(false);
    setStatusText(nextStatus);
  }

  async function stopStreaming() {
    const runId = runIdRef.current;
    streamRef.current?.close();
    if (temporaryMessageIdRef.current) {
      const temporaryId = temporaryMessageIdRef.current;
      setMessages((current) => current.filter((item) => item.id !== temporaryId));
    }
    finishStream("正在停止");
    if (!runId) return;
    try {
      await api.cancelRun(runId);
      setStatusText("已停止生成");
    } catch (requestError) {
      setError(toErrorMessage(requestError));
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  const sidebarClassName = [
    "sidebar",
    sidebarOpen ? "sidebar-open" : "",
    sidebarCollapsed ? "sidebar-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <main className="app-shell">
      {sidebarOpen && (
        <button className="sidebar-scrim" aria-label="关闭侧栏" onClick={() => setSidebarOpen(false)} />
      )}

      <aside className={sidebarClassName} aria-label="对话历史">
        <div className="sidebar-header">
          <button className="brand" onClick={() => void createNewConversation()} aria-label="创建新对话">
            <span className="brand-mark">AR</span>
            <span className="brand-name">Agentic RAG</span>
          </button>
          <Tooltip content={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}>
            <IconButton
              className="desktop-collapse"
              variant="ghost"
              color="gray"
              aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
              onClick={() => setSidebarCollapsed((current) => !current)}
            >
              <SidebarSimple size={19} weight="regular" />
            </IconButton>
          </Tooltip>
          <IconButton
            className="mobile-close"
            variant="ghost"
            color="gray"
            aria-label="关闭侧栏"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={19} />
          </IconButton>
        </div>

        <Button className="new-chat-button" variant="soft" color="gray" onClick={() => void createNewConversation()}>
          <Plus size={18} weight="bold" />
          <span>新对话</span>
        </Button>

        <div className="history-search">
          <TextField.Root
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索对话"
            aria-label="搜索对话"
            size="2"
          >
            <TextField.Slot>
              <MagnifyingGlass size={16} />
            </TextField.Slot>
          </TextField.Root>
        </div>

        <div className="history-list" aria-live="polite">
          <p className="history-label">最近对话</p>
          {loadingHistory ? (
            <HistorySkeleton />
          ) : filteredConversations.length === 0 ? (
            <div className="history-empty">{search ? "没有匹配的对话" : "还没有对话记录"}</div>
          ) : (
            filteredConversations.map((conversation) => (
              <div
                className={`history-row ${activeConversationId === conversation.id ? "history-row-active" : ""}`}
                key={conversation.id}
              >
                {editingId === conversation.id ? (
                  <form className="rename-form" onSubmit={(event) => void submitRename(event, conversation.id)}>
                    <input
                      autoFocus
                      value={editingTitle}
                      onChange={(event) => setEditingTitle(event.target.value)}
                      aria-label="对话标题"
                      maxLength={120}
                    />
                    <IconButton type="submit" size="1" variant="ghost" aria-label="保存标题">
                      <Check size={15} weight="bold" />
                    </IconButton>
                  </form>
                ) : (
                  <>
                    <button className="history-main" onClick={() => void openConversation(conversation.id)}>
                      <ChatCenteredText size={17} />
                      <span>{conversation.title}</span>
                    </button>
                    <div className="history-actions">
                      <Tooltip content="重命名">
                        <IconButton
                          size="1"
                          variant="ghost"
                          color="gray"
                          aria-label="重命名对话"
                          onClick={() => {
                            setEditingId(conversation.id);
                            setEditingTitle(conversation.title);
                          }}
                        >
                          <PencilSimple size={14} />
                        </IconButton>
                      </Tooltip>
                      <AlertDialog.Root>
                        <Tooltip content="删除">
                          <AlertDialog.Trigger>
                            <IconButton size="1" variant="ghost" color="red" aria-label="删除对话">
                              <Trash size={14} />
                            </IconButton>
                          </AlertDialog.Trigger>
                        </Tooltip>
                        <AlertDialog.Content maxWidth="420px">
                          <AlertDialog.Title>删除这条对话？</AlertDialog.Title>
                          <AlertDialog.Description size="2">
                            删除后不会再出现在历史记录中。当前阶段采用软删除，便于审计和恢复。
                          </AlertDialog.Description>
                          <div className="dialog-actions">
                            <AlertDialog.Cancel>
                              <Button variant="soft" color="gray">取消</Button>
                            </AlertDialog.Cancel>
                            <AlertDialog.Action>
                              <Button color="red" onClick={() => void deleteConversation(conversation.id)}>
                                删除
                              </Button>
                            </AlertDialog.Action>
                          </div>
                        </AlertDialog.Content>
                      </AlertDialog.Root>
                    </div>
                  </>
                )}
              </div>
            ))
          )}
        </div>

        <div className="sidebar-footer">
          <span>工程联调模式</span>
          <span>知识库更新接口待接入</span>
        </div>
      </aside>

      <section className={`chat-panel ${sidebarCollapsed ? "chat-panel-wide" : ""}`}>
        <header className="chat-header">
          <IconButton
            className="mobile-menu"
            variant="ghost"
            color="gray"
            aria-label="打开侧栏"
            onClick={() => setSidebarOpen(true)}
          >
            <List size={20} />
          </IconButton>
          <div className="chat-title">
            <span>{conversations.find((item) => item.id === activeConversationId)?.title ?? "新对话"}</span>
            <small>{statusText}</small>
          </div>
          <Tooltip content={appearance === "light" ? "切换到深色" : "切换到浅色"}>
            <IconButton variant="ghost" color="gray" aria-label="切换主题" onClick={toggleAppearance}>
              {appearance === "light" ? <Moon size={19} /> : <Sun size={19} />}
            </IconButton>
          </Tooltip>
        </header>

        <div className="conversation-viewport" ref={viewportRef}>
          <div className="conversation-content">
            {error && (
              <div className="error-banner" role="alert">
                <span>{error}</span>
                <button onClick={() => setError(null)} aria-label="关闭错误提示">
                  <X size={16} />
                </button>
              </div>
            )}

            {loadingConversation ? (
              <MessageSkeleton />
            ) : messages.length === 0 ? (
              <EmptyConversation onSuggestion={(suggestion) => void sendMessage(suggestion)} />
            ) : (
              <div className="message-list" aria-live="polite">
                {messages.map((message) => (
                  <article className={`message message-${message.role}`} key={message.id}>
                    <div className="message-author">
                      {message.role === "user" ? "你" : "Agentic RAG"}
                    </div>
                    <div className="message-content">
                      {message.content || (streaming && message.role === "assistant" ? "正在生成" : "")}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="composer-wrap">
          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              void sendMessage();
            }}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="向 Agentic RAG 提问"
              aria-label="消息内容"
              rows={1}
              maxLength={8_000}
              disabled={streaming}
            />
            <div className="composer-actions">
              <span>Enter 发送，Shift + Enter 换行</span>
              {streaming ? (
                <IconButton type="button" className="send-button" aria-label="停止生成" onClick={() => void stopStreaming()}>
                  <Square size={16} weight="fill" />
                </IconButton>
              ) : (
                <IconButton
                  type="submit"
                  className="send-button"
                  aria-label="发送消息"
                  disabled={!input.trim()}
                >
                  <PaperPlaneRight size={18} weight="fill" />
                </IconButton>
              )}
            </div>
          </form>
          <p className="composer-note">重要时间、费用和办理材料请以最新学校官方通知为准。</p>
        </div>
      </section>
    </main>
  );
}

function EmptyConversation({ onSuggestion }: { onSuggestion: (suggestion: string) => void }) {
  return (
    <div className="empty-conversation">
      <span className="empty-mark">
        <NotePencil size={25} weight="regular" />
      </span>
      <h1>今天想了解什么？</h1>
      <p>从报到到校园生活，回答会保留来源与时效提示。</p>
      <div className="suggestion-grid">
        {SUGGESTIONS.map((suggestion) => (
          <button key={suggestion} onClick={() => onSuggestion(suggestion)}>
            <span>{suggestion}</span>
            <PaperPlaneRight size={16} />
          </button>
        ))}
      </div>
    </div>
  );
}

function HistorySkeleton() {
  return (
    <div className="history-skeleton" aria-label="正在加载对话历史">
      <span />
      <span />
      <span />
    </div>
  );
}

function MessageSkeleton() {
  return (
    <div className="message-skeleton" aria-label="正在加载对话">
      <span />
      <span />
      <span />
    </div>
  );
}

function toErrorMessage(error: unknown): string {
  if (error instanceof TypeError && error.message.toLowerCase().includes("fetch")) {
    return "无法连接到后端服务，请确认 API 已启动";
  }
  return error instanceof Error ? error.message : "发生未知错误，请稍后重试";
}
