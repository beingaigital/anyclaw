import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

type Tab = "tools" | "models" | "profile" | "skills";

export default function StatusPanels({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("tools");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(
    async (t: Tab, reload = false) => {
      setLoading(true);
      try {
        if (t === "tools") setData(reload ? await api.reloadTools() : await api.tools());
        if (t === "models") setData(await api.models());
        if (t === "profile") setData(reload ? await api.reloadProfile() : await api.profile());
        if (t === "skills") setData(reload ? await api.reloadSkills() : await api.skills());
      } catch (e: any) {
        setData({ error: String(e?.message || e) });
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    load(tab);
  }, [tab, load]);

  return (
    <aside className="panels">
      <div className="panels-header">
        <div className="tabs">
          {(["tools", "models", "profile", "skills"] as Tab[]).map((t) => (
            <button
              key={t}
              className={`tab ${tab === t ? "active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "tools" ? "工具" : t === "models" ? "模型" : t === "profile" ? "Profile" : "Skills"}
            </button>
          ))}
        </div>
        <div>
          {tab !== "models" && (
            <button className="btn" onClick={() => load(tab, true)} disabled={loading}>
              重载
            </button>
          )}
          <button className="btn-icon" onClick={onClose}>✕</button>
        </div>
      </div>
      <div className="panels-body">
        {loading && <div className="empty-hint">加载中…</div>}
        {!loading && data && tab === "tools" && <ToolsView data={data} />}
        {!loading && data && tab === "models" && <ModelsView data={data} />}
        {!loading && data && tab === "profile" && <ProfileView data={data} />}
        {!loading && data && tab === "skills" && <SkillsView data={data} />}
      </div>
    </aside>
  );
}

function ToolsView({ data }: { data: any }) {
  if (data.error) return <div className="error-banner">{data.error}</div>;
  return (
    <div>
      {(data.enabled || []).map((t: any) => (
        <div key={t.name} className="panel-item">
          <div className="panel-item-title">{t.name}</div>
          <div className="panel-item-sub">{t.description}</div>
        </div>
      ))}
      {Object.keys(data.mcp_status || {}).length > 0 && (
        <>
          <div className="panel-section">MCP servers</div>
          {Object.entries(data.mcp_status).map(([name, status]) => (
            <div key={name} className="panel-item">
              <div className="panel-item-title">{name}</div>
              <div className="panel-item-sub">{String(status)}</div>
            </div>
          ))}
        </>
      )}
      {(data.disabled || []).length > 0 && (
        <>
          <div className="panel-section">已禁用</div>
          {data.disabled.map((t: any, i: number) => (
            <div key={i} className="panel-item">
              <div className="panel-item-title">{t.name}</div>
              <div className="panel-item-sub">{t.reason}</div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function ModelsView({ data }: { data: any }) {
  const models = data.models || {};
  return (
    <div>
      {Object.entries(models).map(([role, cfg]: [string, any]) => (
        <div key={role} className="panel-item">
          <div className="panel-item-title">{role}</div>
          <div className="panel-item-sub">
            {cfg?.provider} / {cfg?.model}
          </div>
        </div>
      ))}
    </div>
  );
}

function ProfileView({ data }: { data: any }) {
  return (
    <div>
      {(data.sections || []).map((s: any) => (
        <div key={s.key} className="panel-item">
          <div className="panel-item-title">
            {s.key} {s.exists ? "✓" : "✗"}
          </div>
          <div className="panel-item-sub">
            {s.file} · {s.chars} 字符{s.truncated ? "（已截断）" : ""}
            {s.error ? ` · ${s.error}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}

function SkillsView({ data }: { data: any }) {
  return (
    <div>
      {(data.skills || []).map((s: any) => (
        <div key={s.skill_id} className="panel-item">
          <div className="panel-item-title">
            {s.skill_id} {!s.enabled && "（禁用）"}
          </div>
          <div className="panel-item-sub">
            {s.summary}
            {s.keywords?.length ? ` · [${s.keywords.join(", ")}]` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}