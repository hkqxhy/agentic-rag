import type {
  CompletedEvent,
  ConversationDetail,
  ConversationSummary,
  DeltaEvent,
  FailedEvent,
  RunAccepted,
  AuthResponse,
} from "@/lib/types";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

function createIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("当前浏览器不支持生成请求标识，请更换现代浏览器后重试");
  }

  // randomUUID is restricted to secure contexts, while getRandomValues is also
  // available to the allow-listed HTTP staging environment.
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the status-based fallback when the response is not JSON.
    }
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("agentic-rag:unauthorized"));
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<AuthResponse>("/api/v1/auth/me"),
  register: (email: string, username: string, password: string) =>
    request<AuthResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, username, password }),
    }),
  login: (identifier: string, password: string) =>
    request<AuthResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier, password }),
    }),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  listConversations: () => request<ConversationSummary[]>("/api/v1/conversations"),
  createConversation: (title = "新对话") =>
    request<ConversationSummary>("/api/v1/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  getConversation: (id: string) =>
    request<ConversationDetail>(`/api/v1/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    request<ConversationSummary>(`/api/v1/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteConversation: (id: string) =>
    request<void>(`/api/v1/conversations/${id}`, { method: "DELETE" }),
  sendMessage: (id: string, content: string) =>
    request<RunAccepted>(`/api/v1/conversations/${id}/messages`, {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ content }),
    }),
  cancelRun: (runId: string) =>
    request<{ status: "cancelled" }>(`/api/v1/runs/${runId}/cancel`, { method: "POST" }),
};

export interface StreamHandlers {
  onDelta: (event: DeltaEvent) => void;
  onCompleted: (event: CompletedEvent) => void;
  onFailed: (event: FailedEvent) => void;
  onCancelled: () => void;
  onConnectionError: () => void;
}

export function openRunStream(runId: string, handlers: StreamHandlers): EventSource {
  const stream = new EventSource(`${API_BASE_URL}/api/v1/runs/${runId}/events`, {
    withCredentials: true,
  });
  stream.addEventListener("message.delta", (event) => {
    handlers.onDelta(JSON.parse((event as MessageEvent<string>).data) as DeltaEvent);
  });
  stream.addEventListener("message.completed", (event) => {
    handlers.onCompleted(JSON.parse((event as MessageEvent<string>).data) as CompletedEvent);
    stream.close();
  });
  stream.addEventListener("run.failed", (event) => {
    handlers.onFailed(JSON.parse((event as MessageEvent<string>).data) as FailedEvent);
    stream.close();
  });
  stream.addEventListener("run.cancelled", () => {
    handlers.onCancelled();
    stream.close();
  });
  stream.onerror = () => {
    if (stream.readyState === EventSource.CLOSED) handlers.onConnectionError();
  };
  return stream;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
