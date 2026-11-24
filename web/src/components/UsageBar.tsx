import type { UsageTotals } from "../types";

export default function UsageBar({ usage }: { usage: UsageTotals }) {
  return (
    <div className="usage-bar" title="本会话累计 token 消耗">
      <span>↑ {usage.prompt_tokens}</span>
      <span>↓ {usage.completion_tokens}</span>
      <span className="usage-total">Σ {usage.total_tokens}</span>
    </div>
  );
}