"""Eval 运行器：脚本化场景（CI 模式）/ 真实模型（--real）+ 第二 AI 评审（--judge）。

用法：
    python evals/runner.py                 # CI 模式（fake model，无需 API key）
    python evals/runner.py --real          # 真实模型（需 .env）
    python evals/runner.py --judge         # 追加第二 AI 评审打分
    python evals/runner.py --scenario calculator

每个场景 = 脚本化模型轮次 + 权限策略 + 可检查断言（事件序列 / 工具结果 / 审计 / 文件）。
运行在隔离临时根下，不污染真实 memory/sandbox。报告写入 evals/report.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.messages import AIMessage  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402


def _load_scenarios(only: str | None) -> list[dict]:
    scenarios_dir = _PROJECT_ROOT / "evals" / "scenarios"
    result = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as fh:
            sc = yaml.safe_load(fh) or {}
        if only and sc.get("name") != only:
            continue
        sc["_file"] = path.name
        result.append(sc)
    return result


def _build_scripted_model(turns: list[dict]):
    from tests.fake_model import ScriptedChatModel, make_tool_call_message

    responses = []
    for turn in turns:
        if "tool_call" in turn:
            tc = turn["tool_call"]
            responses.append(
                make_tool_call_message(tc["name"], tc.get("args", {}), tc.get("id", "call_1"))
            )
        else:
            responses.append(AIMessage(content=turn.get("text", "")))
    return ScriptedChatModel(responses=responses)


def _load_tools_by_names(names: list[str]) -> list:
    from utils.tool_registry import load_agent_tools

    all_tools = load_agent_tools()
    by_name = {t.name: t for t in all_tools}
    return [by_name[n] for n in names if n in by_name]


async def run_scenario(sc: dict, *, real: bool, judge: bool) -> dict:
    import utils.path as path_mod
    import utils.session_manager as sm_mod
    import core.kernel as ck
    from core.harness import PermissionPolicy
    from core.kernel import AgentKernel

    report = {"name": sc.get("name", sc["_file"]), "checks": [], "passed": True}

    with tempfile.TemporaryDirectory(prefix="anyclaw-eval-") as tmp:
        tmp_root = Path(tmp)
        # 隔离根 + 复制配置（--real/--judge 需要 model.yaml）
        shutil.copytree(_PROJECT_ROOT / "config", tmp_root / "config")
        (tmp_root / "skills").mkdir()
        path_mod._PROJECT_ROOT = tmp_root
        sm_mod._session_manager = None

        try:
            tools = _load_tools_by_names(sc.get("tools", []))
            policy_cfg = sc.get("policy", {})
            policy = PermissionPolicy(
                default_tier=policy_cfg.get("default_tier", "always"),
                tool_tiers=policy_cfg.get("tools", {}),
            )
            if real:
                from model.factory import get_react_model

                model = get_react_model()
            else:
                model = _build_scripted_model(sc.get("turns", []))

            saver_cm = AsyncSqliteSaver.from_conn_string(str(tmp_root / "ckpt.sqlite"))
            saver = await saver_cm.__aenter__()
            await saver.setup()
            kernel = await AgentKernel.create(
                model=model, tools=tools, policy=policy, checkpointer=saver
            )

            session_id = f"eval-{sc.get('name', 'x')}"
            events = [ev async for ev in kernel.astream(session_id, sc.get("input", "测试"))]

            # 权限场景：自动 allow 第一个 pending
            if events and events[-1].payload.get("status") == "awaiting_permission":
                reqs = [e for e in events if e.type.value == "permission_request"]
                if reqs:
                    decision = sc.get("auto_decision", {"action": "allow"})
                    events += [
                        ev
                        async for ev in kernel.resume(
                            session_id, decision, reqs[0].payload["interrupt_id"]
                        )
                    ]

            types = [e.type.value for e in events]
            checks = sc.get("checks", {})

            def check(name: str, ok: bool, detail: str = ""):
                report["checks"].append({"name": name, "passed": bool(ok), "detail": detail})
                if not ok:
                    report["passed"] = False

            for required in checks.get("events_include", []):
                check(f"event:{required}", required in types)
            if "final_status" in checks:
                actual = events[-1].payload.get("status") if events else None
                check("final_status", actual == checks["final_status"], f"actual={actual}")
            if "tool_result_contains" in checks:
                results = [e.payload.get("result", "") for e in events if e.type.value == "tool_result"]
                needle = checks["tool_result_contains"]
                check("tool_result_contains", any(needle in r for r in results))
            if "final_text_contains" in checks:
                final_text = events[-1].payload.get("final_text", "") if events else ""
                check(
                    "final_text_contains",
                    checks["final_text_contains"] in final_text,
                    f"final_text={final_text[:100]}",
                )
            if "file_exists" in checks:
                f = tmp_root / "sandbox" / session_id / checks["file_exists"]
                check("file_exists", f.exists(), str(f))
            if "audit_action" in checks:
                want = checks["audit_action"]
                entries = []
                for p in (tmp_root / "memory" / "audit").glob("*.jsonl"):
                    entries += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
                ok = any(
                    e.get("kind") == "decision" and e.get("action") == want
                    for e in entries
                )
                check("audit_action", ok, f"want={want}")

            # 第二 AI 评审
            if judge and events:
                final_text = events[-1].payload.get("final_text", "")
                rubric = sc.get("judge_rubric", "回答是否正确、完整、有依据？")
                try:
                    from model.factory import get_text_generation_model

                    judge_model = get_text_generation_model()
                    prompt = (
                        f"你是严格的评审。评分标准：{rubric}\n"
                        f"用户问题：{sc.get('input', '')}\n"
                        f"Agent 最终回答：{final_text}\n"
                        f'请只输出 JSON：{{"score": 0-5 的整数, "reason": "一句话理由"}}'
                    )
                    resp = judge_model.invoke([AIMessage(content=prompt)])
                    text = str(resp.content).strip()
                    start = text.find("{")
                    verdict = json.loads(text[start : text.rfind("}") + 1]) if start >= 0 else {}
                    report["judge"] = verdict
                except Exception as exc:
                    report["judge"] = {"error": str(exc)}

            await kernel.close()
            await saver_cm.__aexit__(None, None, None)
        finally:
            path_mod._PROJECT_ROOT = _PROJECT_ROOT
            sm_mod._session_manager = None

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="AnyClaw eval 运行器")
    parser.add_argument("--real", action="store_true", help="使用真实模型（需 .env）")
    parser.add_argument("--judge", action="store_true", help="追加第二 AI 评审")
    parser.add_argument("--scenario", help="只跑指定场景")
    args = parser.parse_args()

    scenarios = _load_scenarios(args.scenario)
    if not scenarios:
        print("未找到场景")
        sys.exit(1)

    reports = []
    for sc in scenarios:
        print(f"▶ {sc.get('name', sc['_file'])} ...", flush=True)
        report = asyncio.run(run_scenario(sc, real=args.real, judge=args.judge))
        reports.append(report)
        mark = "✅" if report["passed"] else "❌"
        print(f"  {mark} " + ", ".join(
            f"{c['name']}{'✓' if c['passed'] else '✗'}" for c in report["checks"]
        ))
        if "judge" in report:
            print(f"  评审: {report['judge']}")

    # 报告
    lines = ["# AnyClaw Eval 报告", "", f"模式: {'real' if args.real else 'ci'}", ""]
    for r in reports:
        lines.append(f"## {'✅' if r['passed'] else '❌'} {r['name']}")
        for c in r["checks"]:
            lines.append(f"- {'✓' if c['passed'] else '✗'} {c['name']} {c.get('detail', '')}")
        if "judge" in r:
            lines.append(f"- 评审: {r['judge']}")
        lines.append("")
    report_path = _PROJECT_ROOT / "evals" / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {report_path}")

    if not all(r["passed"] for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    main()