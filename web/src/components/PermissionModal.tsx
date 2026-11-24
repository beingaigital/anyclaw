import type { PendingInterrupt } from "../types";

export default function PermissionModal({
  pending,
  onDecide,
}: {
  pending: PendingInterrupt;
  onDecide: (action: "allow" | "deny", remember: boolean) => void;
}) {
  return (
    <div className="modal-overlay">
      <div className="modal">
        <div className="modal-title">🛡️ 权限确认</div>
        <div className="modal-tool">{pending.tool_name}</div>
        <div className="modal-reason">{pending.reason}</div>
        <pre className="modal-args">{JSON.stringify(pending.args, null, 2)}</pre>
        <div className="modal-actions">
          <button className="btn" onClick={() => onDecide("deny", false)}>
            拒绝
          </button>
          <button className="btn btn-primary" onClick={() => onDecide("allow", false)}>
            允许
          </button>
          <button className="btn" onClick={() => onDecide("allow", true)}>
            本会话始终允许
          </button>
        </div>
      </div>
    </div>
  );
}