"""M5: agent 小规模真机评测(轻量规则评分,零 LLM judge;重度评测走 YanbaoEval)。

用法:
  .venv/bin/python scripts/run_agent_eval.py                # 全量 13 题
  .venv/bin/python scripts/run_agent_eval.py --domain re    # 只跑房地产域
  .venv/bin/python scripts/run_agent_eval.py --limit 2      # 冒烟
产出: eval/report_agent_<ts>.json + .md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402
from lib_agent import AGENTS, run_agent  # noqa: E402
from lib_paths import APP  # noqa: E402

QUESTIONS = APP / "eval" / "agent_questions.yaml"
SECTIONS_RE = ["【我的判断】", "【支持的机构】", "【不同意见的机构】", "【我为什么这么选】"]
CITE_RE = re.compile(r"\[[^\[\]]+?\s\d{4}-\d{2}-\d{2}\]|【[^】]+】|\[[^\[\]]+?\s\d{4}\]")
UNCOVERED_RE = re.compile(r"未覆盖|未涉及|没有相关|无法回答|语料中?没有|现有资料不|查不到|未找到")
NUMBER_RE = re.compile(r"\d+\.?\d*\s*(元|万|亿|%|套|平方米)")


def score_answer(q: dict, answer: str, llm_calls: int) -> dict:
    """纯规则评分,返回各项通过情况。"""
    checks = {}
    checks["structure"] = all(s in answer for s in SECTIONS_RE)
    checks["calls_le4"] = llm_calls <= 4
    if q.get("must_include_any"):
        checks["must_include_any"] = any(w in answer for w in q["must_include_any"])
    if q.get("must_cite_any"):
        cites = CITE_RE.findall(answer)
        checks["must_cite_any"] = any(any(inst in c for inst in q["must_cite_any"]) for c in cites)
    if q.get("qtype") == "uncovered":
        # 未覆盖题:明说未覆盖即过。编造数字检测规则层做不好(误伤原文引用),交给 YanbaoEval 重度评测的裁判模型
        checks["uncovered_said"] = bool(UNCOVERED_RE.search(answer))
    return checks


def run_eval(domain: str | None = None, limit: int | None = None) -> dict:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    if domain:
        questions = [q for q in questions if q["agent"] == {"re": "property", "all": "all"}.get(domain, domain)]
    if limit:
        questions = questions[:limit]
    results = []
    total_usage = {"in": 0, "out": 0}
    for i, q in enumerate(questions, 1):
        cfg = AGENTS[q["agent"]]
        cfg.trace = False  # 评测不落逐题 trace,防刷屏;要排查时单独跑
        print(f"[{i}/{len(questions)}] {q['id']} {q['question'][:36]}...", flush=True)
        try:
            res = run_agent(q["question"], cfg)
            checks = score_answer(q, res["answer"], res["llm_calls"])
            usage = res["usage"]
        except Exception as e:  # noqa: BLE001 单题失败记 0 分不阻断
            checks = {"error": f"{type(e).__name__}: {e}"}
            res = {"answer": "", "llm_calls": 0, "usage": {"in": 0, "out": 0}}
            usage = res["usage"]
        total_usage["in"] += usage["in"]
        total_usage["out"] += usage["out"]
        passed = all(checks.values()) and "error" not in checks
        results.append({"id": q["id"], "agent": q["agent"], "qtype": q["qtype"],
                        "question": q["question"], "passed": passed, "checks": checks,
                        "llm_calls": res["llm_calls"], "usage": usage, "answer": res["answer"]})
        print(f"  → {'✅' if passed else '❌'} {checks}", flush=True)
    n_pass = sum(r["passed"] for r in results)
    return {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "total": len(results),
            "passed": n_pass, "total_usage": total_usage, "results": results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", choices=["re", "all"])
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    report = run_eval(args.domain, args.limit)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_json = APP / "eval" / f"report_agent_{ts}.json"
    out_md = APP / "eval" / f"report_agent_{ts}.md"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# agent 评测报告({report['ts']})",
             f"通过 {report['passed']}/{report['total']} · tokens in={report['total_usage']['in']:,} "
             f"out={report['total_usage']['out']:,}", ""]
    for r in report["results"]:
        lines.append(f"- {'✅' if r['passed'] else '❌'} [{r['id']}] {r['question']}")
        if not r["passed"]:
            lines.append(f"  未过项: {[k for k, v in r['checks'].items() if v is not True]}")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n通过 {report['passed']}/{report['total']} · {out_md}")


if __name__ == "__main__":
    main()
