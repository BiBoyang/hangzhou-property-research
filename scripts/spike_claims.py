"""M0 spike: 3 份研报 claim 抽取,验证 prompt 质量(人工 gate 前置)。

走"脚本 + Kimi API"路线——这就是 M1 全量 228 份的生产方式,spike 顺带探通链路。
产出: plans/reports/m0-claim-spike.jsonl(逐 claim 带校验标记)
      plans/reports/m0-claim-spike-review.md(人审对照稿)

用法:
  .venv/bin/python scripts/spike_claims.py                # 默认 3 份样本
  .venv/bin/python scripts/spike_claims.py --doc-ids a,b,c
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_claims import validate  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_llm import llm_config  # noqa: E402
from lib_paths import APP, DB, RAW_MD  # noqa: E402

# 1 中文数据型(考验"不硬凑") + 2 英文观点型(考验极性/时间轴)
SAMPLE_DOC_IDS = ["64848fc50c25", "ad36f824b71c", "990ca305d4a7"]
MAX_CHARS = 80_000  # 超长文档截断(MS 那份 42.8 万字符),截断事实写入报告

PROMPT_PATH = APP / "config" / "claim_extraction_prompt.md"
OUT_JSONL = APP / "plans" / "reports" / "m0-claim-spike.jsonl"
OUT_REVIEW = APP / "plans" / "reports" / "m0-claim-spike-review.md"


def load_doc(conn, doc_id: str, max_chars: int = MAX_CHARS) -> dict:
    d = conn.execute("SELECT doc_id, title, institution, report_date, file_path FROM docs WHERE doc_id=?",
                     (doc_id,)).fetchone()
    if not d:
        raise SystemExit(f"doc_id 不存在: {doc_id}")
    md = RAW_MD / (Path(d["file_path"]).stem + ".md")
    body = md.read_text(encoding="utf-8") if md.exists() else "\n\n".join(
        r["text"] for r in conn.execute("SELECT text FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,)))
    truncated = len(body) > max_chars
    return {**dict(d), "body": body[:max_chars], "truncated": truncated, "full_chars": len(body)}


def extract_claims(doc: dict) -> tuple[dict, object]:
    """调 LLM 抽取,返回 (解析后的 JSON, usage)。"""
    from openai import OpenAI

    cfg = llm_config()
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    user = (f"机构: {doc['institution'] or '未知'}\n报告日期: {doc['report_date'] or '未知'}\n"
            f"标题: {doc['title']}\n\n研报全文:\n{doc['body']}")
    # thinking 控制(成本大头是思考 token,按输出价计费):
    #   LLM_THINKING=1      → 思考开(基线同款,不附加参数)
    #   SPIKE_EFFORT=low/…  → reasoning_effort(DeepSeek V4 风格)
    #   默认                → thinking disabled(Kimi 风格,同 lib_llm)
    import os
    extra: dict = {}
    if os.environ.get("LLM_THINKING") == "1":
        pass
    elif os.environ.get("SPIKE_EFFORT"):
        extra["reasoning_effort"] = os.environ["SPIKE_EFFORT"]
    else:
        extra["extra_body"] = {"thinking": {"type": "disabled"}}
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=[{"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
                  {"role": "user", "content": user}],
        **extra,
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)  # 去代码围栏
    try:
        return json.loads(text), resp.usage
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0)), resp.usage
        raise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-ids", help="逗号分隔,默认内置 3 份样本")
    ap.add_argument("--tag", help="对比实验标签,产出文件名带后缀,不覆盖基线")
    args = ap.parse_args()
    global OUT_JSONL, OUT_REVIEW
    if args.tag:
        OUT_JSONL = OUT_JSONL.with_name(f"m0-claim-spike-{args.tag}.jsonl")
        OUT_REVIEW = OUT_REVIEW.with_name(f"m0-claim-spike-review-{args.tag}.md")
    doc_ids = args.doc_ids.split(",") if args.doc_ids else SAMPLE_DOC_IDS

    conn = connect(DB)
    all_claims, review_parts, total_usage = [], [], {"prompt_tokens": 0, "completion_tokens": 0}
    for doc_id in doc_ids:
        doc = load_doc(conn, doc_id)
        print(f"抽取 [{doc_id}] {doc['title'][:50]} ({doc['full_chars']} chars"
              f"{'，截断至 8 万' if doc['truncated'] else ''}) ...", flush=True)
        data, usage = extract_claims(doc)
        total_usage["prompt_tokens"] += usage.prompt_tokens
        total_usage["completion_tokens"] += usage.completion_tokens
        claims = data.get("claims", [])
        n_bad = 0
        lines = [f"\n## {doc['institution'] or '未知'} {doc['report_date'] or ''} 《{doc['title']}》",
                 f"doc_id={doc_id} · 抽取 {len(claims)} 条\n"]
        for i, c in enumerate(claims, 1):
            problems = validate(c, doc["body"])
            n_bad += bool(problems)
            all_claims.append({"doc_id": doc_id, "ord": i, **c, "problems": problems})
            flag = "⚠️ " + ";".join(problems) if problems else "✅"
            lines.append(f"### {i}. [{c.get('stance')}/{c.get('subject')}] {c.get('claim_text')}")
            lines.append(f"- horizon={c.get('horizon') or '—'} city={c.get('city') or '—'} "
                         f"segment={c.get('segment') or '—'} forecast={c.get('is_forecast')} · 校验 {flag}")
            lines.append(f"> {c.get('evidence_quote', '')}\n")
        print(f"  → {len(claims)} 条,校验异常 {n_bad} 条;tokens in={usage.prompt_tokens} out={usage.completion_tokens}")
        review_parts.extend(lines)
    conn.close()

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for c in all_claims:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    review = [f"# M0 claim spike 人审稿({datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M} 生成)",
              f"tokens 合计: in={total_usage['prompt_tokens']} out={total_usage['completion_tokens']}",
              "人审要点: ① stance 与 quote 方向一致? ② quote 是否原文(⚠️ 项已程序断言) ③ 有无硬凑/漏抽核心观点"]
    OUT_REVIEW.write_text("\n".join(review + review_parts), encoding="utf-8")
    cfg = llm_config()
    print(f"模型: {cfg['model']} @ {cfg['base_url']}")
    n_ok = sum(1 for c in all_claims if not c["problems"])
    print(f"\n完成: {len(all_claims)} 条 claim,程序校验通过 {n_ok}/{len(all_claims)}")
    print(f"tokens: in={total_usage['prompt_tokens']} out={total_usage['completion_tokens']}")
    print(f"产出: {OUT_JSONL}\n      {OUT_REVIEW}")


if __name__ == "__main__":
    main()
