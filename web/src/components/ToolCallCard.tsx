import type { ChatItem } from "../types";

const STATUS_LABEL: Record<string, string> = {
  running: "执行中",
  ok: "成功",
  error: "错误",
  denied: "已拒绝",
  blocked: "已拦截",
};

export default function ToolCallCard({ item }: { item: Extract<ChatItem, { kind: "tool" }> }) {
  return (
    <details className={`tool-card status-${item.status}`} open={item.status === "running"}>
      <summary>
        <span className="tool-icon">🔧</span>
        <span className="tool-name">{item.name}</span>
        <span className={`tool-status status-${item.status}`}>
          {STATUS_LABEL[item.status] || item.status}
        </span>
        {item.durationMs != null && <span className="tool-duration">{item.durationMs}ms</span>}
      </summary>
      <div className="tool-detail">
        {Object.keys(item.args || {}).length > 0 && (
          <>
            <div className="tool-label">参数</div>
            <pre>{JSON.stringify(item.args, null, 2)}</pre>
          </>
        )}
        {item.result != null && (
          <>
            <div className="tool-label">结果</div>
            <pre>{item.result}</pre>
          </>
        )}
      </div>
    </details>
  );
}