import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { ChatItem } from "../types";
import ToolCallCard from "./ToolCallCard";

export default function ChatWindow({
  items,
  running,
  disabled,
  onSend,
}: {
  items: ChatItem[];
  running: boolean;
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  const submit = () => {
    const text = input.trim();
    if (!text || running || disabled) return;
    setInput("");
    onSend(text);
  };

  return (
    <div className="chat">
      <div className="chat-scroll">
        {items.length === 0 && (
          <div className="empty-hint center">
            {disabled ? "选择或新建一个会话开始" : "输入消息开始对话"}
          </div>
        )}
        {items.map((it) => {
          if (it.kind === "user") {
            return (
              <div key={it.id} className="msg msg-user">
                <div className="msg-role">user</div>
                <div className="msg-body">{it.content}</div>
              </div>
            );
          }
          if (it.kind === "assistant") {
            return (
              <div key={it.id} className="msg msg-assistant">
                <div className="msg-role">assistant</div>
                <div className="msg-body markdown">
                  <ReactMarkdown>{it.content || (it.streaming ? "…" : "")}</ReactMarkdown>
                </div>
              </div>
            );
          }
          if (it.kind === "tool") {
            return <ToolCallCard key={it.id} item={it} />;
          }
          return (
            <div key={it.id} className="msg-system">
              {it.content}
            </div>
          );
        })}
        {running && <div className="running-hint">运行中…</div>}
        <div ref={bottomRef} />
      </div>
      <div className="input-bar">
        <textarea
          value={input}
          placeholder={disabled ? "先选择会话" : "输入消息，Enter 发送，Shift+Enter 换行"}
          disabled={disabled || running}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
        />
        <button className="btn btn-primary" onClick={submit} disabled={disabled || running}>
          发送
        </button>
      </div>
    </div>
  );
}