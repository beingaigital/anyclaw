import type { SessionSummary } from "../types";

export default function SessionSidebar({
  sessions,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="logo">🐾 AnyClaw</span>
        <button className="btn btn-primary" onClick={onNew}>
          ＋ 新会话
        </button>
      </div>
      <div className="session-list">
        {sessions.length === 0 && <div className="empty-hint">暂无会话</div>}
        {sessions.map((s) => (
          <div
            key={s.task_id}
            className={`session-item ${s.task_id === activeId ? "active" : ""}`}
            onClick={() => onSelect(s.task_id)}
          >
            <div className="session-desc">{s.description || s.task_id.slice(0, 8)}</div>
            <div className="session-meta">
              <span>{s.task_id.slice(0, 8)}</span>
              <span>{s.message_count} 条</span>
              <button
                className="btn-icon"
                title="删除会话"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm("删除该会话？")) onDelete(s.task_id);
                }}
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}