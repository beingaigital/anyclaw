export interface KernelEvent {
  type: string;
  session_id: string;
  run_id: string;
  seq: number;
  ts: string;
  payload: Record<string, any>;
}

export interface SessionSummary {
  task_id: string;
  description: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface SessionMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  timestamp?: string;
  tool_calls?: { name: string; args: Record<string, any>; id: string }[];
  tool_name?: string;
  tool_call_id?: string;
}

export interface PendingInterrupt {
  interrupt_id: string;
  tool_call_id: string;
  tool_name: string;
  args: Record<string, any>;
  tier: string;
  reason: string;
}

export type ChatItem =
  | { kind: "user"; id: string; content: string }
  | { kind: "assistant"; id: string; content: string; streaming?: boolean }
  | {
      kind: "tool";
      id: string;
      toolCallId: string;
      name: string;
      args: Record<string, any>;
      result?: string;
      status: "running" | "ok" | "error" | "denied" | "blocked";
      durationMs?: number;
    }
  | { kind: "system"; id: string; content: string };

export interface UsageTotals {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}