import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamSSE } from "./api";
import type {
  ChatItem,
  KernelEvent,
  PendingInterrupt,
  SessionSummary,
  UsageTotals,
} from "./types";
import SessionSidebar from "./components/SessionSidebar";
import ChatWindow from "./components/ChatWindow";
import PermissionModal from "./components/PermissionModal";
import StatusPanels from "./components/StatusPanels";
import UsageBar from "./components/UsageBar";

let idCounter = 0;
const nextId = () => `item-${++idCounter}`;

const ZERO_USAGE: UsageTotals = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState<PendingInterrupt | null>(null);
  const [usage, setUsage] = useState<UsageTotals>(ZERO_USAGE);
  const [showPanels, setShowPanels] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const { sessions } = await api.listSessions();
      setSessions(sessions);
    } catch {
      // 服务未就绪时静默
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const loadHistory = useCallback(async (id: string) => {
    const { messages, token_usage } = await api.getMessages(id);
    const loaded: ChatItem[] = [];
    for (const m of messages) {
      if (m.role === "user") {
        loaded.push({ kind: "user", id: nextId(), content: m.content });
      } else if (m.role === "assistant") {
        if (m.content) loaded.push({ kind: "assistant", id: nextId(), content: m.content });
      } else if (m.role === "tool") {
        loaded.push({
          kind: "tool",
          id: nextId(),
          toolCallId: m.tool_call_id || "",
          name: m.tool_name || "tool",
          args: {},
          result: m.content,
          status: "ok",
        });
      }
    }
    setItems(loaded);
    setUsage({
      prompt_tokens: token_usage?.prompt_tokens || 0,
      completion_tokens: token_usage?.completion_tokens || 0,
      total_tokens: token_usage?.total_tokens || 0,
    });
    const detail = await api.getSession(id);
    setPending(detail.pending_interrupt);
  }, []);

  const selectSession = useCallback(
    async (id: string) => {
      setActiveId(id);
      setError(null);
      setPending(null);
      try {
        await loadHistory(id);
      } catch (e: any) {
        setError(String(e?.message || e));
      }
    },
    [loadHistory]
  );

  const handleEvent = useCallback(
    (ev: KernelEvent) => {
      const p = ev.payload || {};
      switch (ev.type) {
        case "token_delta":
          setItems((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "assistant" && last.streaming) {
              return [
                ...prev.slice(0, -1),
                { ...last, content: last.content + (p.delta || "") },
              ];
            }
            return [
              ...prev,
              { kind: "assistant", id: nextId(), content: p.delta || "", streaming: true },
            ];
          });
          break;
        case "message_complete":
          setItems((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.kind === "assistant" && last.streaming) {
              const content = p.content || last.content;
              if (!content) return prev.slice(0, -1);
              return [...prev.slice(0, -1), { ...last, content, streaming: false }];
            }
            if (p.content) {
              return [...prev, { kind: "assistant", id: nextId(), content: p.content }];
            }
            return prev;
          });
          break;
        case "tool_call_started":
          setItems((prev) => [
            ...prev,
            {
              kind: "tool",
              id: nextId(),
              toolCallId: p.tool_call_id,
              name: p.tool_name,
              args: p.args || {},
              status: "running",
            },
          ]);
          break;
        case "tool_result":
          setItems((prev) =>
            prev.map((it) =>
              it.kind === "tool" && it.toolCallId === p.tool_call_id
                ? { ...it, result: p.result, status: p.status || "ok", durationMs: p.duration_ms }
                : it
            )
          );
          break;
        case "permission_request":
          setPending({
            interrupt_id: p.interrupt_id,
            tool_call_id: p.tool_call_id,
            tool_name: p.tool_name,
            args: p.args || {},
            tier: p.tier,
            reason: p.reason,
          });
          break;
        case "compression":
          setItems((prev) => [
            ...prev,
            { kind: "system", id: nextId(), content: `对话历史已压缩：${p.summary || ""}` },
          ]);
          break;
        case "usage":
          setUsage((prev) => ({
            prompt_tokens: prev.prompt_tokens + (p.prompt_tokens || 0),
            completion_tokens: prev.completion_tokens + (p.completion_tokens || 0),
            total_tokens: prev.total_tokens + (p.total_tokens || 0),
          }));
          break;
        case "error":
          setError(p.message || "未知错误");
          break;
        case "run_finished":
          setRunning(false);
          if (p.status !== "awaiting_permission") setPending(null);
          refreshSessions();
          break;
      }
    },
    [refreshSessions]
  );

  const send = useCallback(
    async (text: string) => {
      if (!activeId || running) return;
      setError(null);
      setItems((prev) => [...prev, { kind: "user", id: nextId(), content: text }]);
      setRunning(true);
      try {
        await streamSSE(`/api/sessions/${activeId}/chat`, { message: text }, handleEvent);
      } catch (e: any) {
        setError(String(e?.message || e));
        setRunning(false);
      }
    },
    [activeId, running, handleEvent]
  );

  const decide = useCallback(
    async (action: "allow" | "deny", remember: boolean) => {
      if (!activeId || !pending) return;
      const interruptId = pending.interrupt_id;
      setPending(null);
      setRunning(true);
      try {
        await streamSSE(
          `/api/sessions/${activeId}/permission`,
          { action, remember, interrupt_id: interruptId },
          handleEvent
        );
      } catch (e: any) {
        setError(String(e?.message || e));
        setRunning(false);
      }
    },
    [activeId, pending, handleEvent]
  );

  const newSession = useCallback(async () => {
    const { task_id } = await api.createSession();
    await refreshSessions();
    await selectSession(task_id);
  }, [refreshSessions, selectSession]);

  const deleteSession = useCallback(
    async (id: string) => {
      await api.deleteSession(id);
      if (id === activeId) {
        setActiveId(null);
        setItems([]);
        setPending(null);
      }
      await refreshSessions();
    },
    [activeId, refreshSessions]
  );

  const activeSession = sessions.find((s) => s.task_id === activeId);

  return (
    <div className="app">
      <SessionSidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={selectSession}
        onNew={newSession}
        onDelete={deleteSession}
      />
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">
            {activeSession ? activeSession.description : "AnyClaw 控制台"}
          </div>
          <UsageBar usage={usage} />
          <button className="btn" onClick={() => setShowPanels((v) => !v)}>
            {showPanels ? "关闭面板" : "工具 / 模型 / 配置"}
          </button>
        </header>
        {error && (
          <div className="error-banner" onClick={() => setError(null)}>
            {error}（点击关闭）
          </div>
        )}
        <ChatWindow items={items} running={running} disabled={!activeId} onSend={send} />
      </main>
      {showPanels && <StatusPanels onClose={() => setShowPanels(false)} />}
      {pending && <PermissionModal pending={pending} onDecide={decide} />}
    </div>
  );
}