"""跑 25 题验收集：检索+指标+生成全链路，检查期望关键词是否出现在回答里。"""
from __future__ import annotations

import argparse
import re
import time

import yaml  # noqa: E402
from lib_paths import APP  # noqa: E402
from lib_search import search  # noqa: E402
from lib_metrics_query import format_metrics, query_metrics  # noqa: E402
from lib_llm import chat  # noqa: E402


def _norm(s: str) -> str:
    """期望词归一：去千分位/空格，"下降16%"→"-16%"、"上涨15%"→"+15%"，抑制同义假失败。"""
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    s = re.sub(r"下降|下跌|回落|跌", "-", s)
    s = re.sub(r"上涨|上升|反弹|涨", "+", s)
    return s


def _expect_hit(e: str, answer: str) -> bool:
    return e in answer or _norm(e) in _norm(answer)


def main() -> None:
    ap = argparse.ArgumentParser(description="运行 LLM 生成评测（会产生外部 API 调用）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只评测前 N 题；默认 0 表示完整验收集")
    args = ap.parse_args()
    if args.limit < 0:
        ap.error("--limit 不能为负数")
    questions = yaml.safe_load((APP / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"]
    if args.limit:
        questions = questions[:args.limit]
    passed = failed = skipped = 0
    report = []
    for i, item in enumerate(questions, 1):
        q, expect = item["q"], item.get("expect", [])
        t0 = time.time()
        try:
            answer = chat(q, search(q, k=8), format_metrics(query_metrics(q)))
        except Exception as e:
            answer = f"[ERROR] {e}"
        missing = [e for e in expect if not _expect_hit(e, answer)]
        status = "PASS" if not missing else ("SKIP" if not expect else "FAIL")
        if status == "PASS":
            passed += 1
        elif status == "FAIL":
            failed += 1
        else:
            skipped += 1
        print(f"[{i:02d}] {status} ({time.time()-t0:.0f}s) {q[:36]}  missing={missing}", flush=True)
        report.append({"q": q, "status": status, "missing": missing, "answer": answer})
    out = APP / "eval" / f"report_{time.strftime('%H%M%S')}.json"
    import json

    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n== 通过 {passed} / 失败 {failed} / 无期望词 {skipped} ==  详情 {out.name}")


if __name__ == "__main__":
    main()
