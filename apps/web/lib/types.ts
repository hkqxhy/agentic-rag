export type MessageRole = "user" | "assistant" | "system";
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[];
}

export interface RunAccepted {
  run_id: string;
  conversation_id: string;
  input_message: Message;
  status: RunStatus;
}

export interface CompletedEvent {
  message: Message;
}

export interface DeltaEvent {
  text: string;
}

export interface FailedEvent {
  code: string;
  message: string;
}
