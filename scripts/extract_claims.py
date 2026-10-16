"""M1: claim 全量抽取(228 份)→ claims 表。

生产方式 = M0 spike 验证过的链路(脚本 + OpenAI 兼容 API),默认引擎
deepseek-flash 非思考(A/B 结论见 plans/reports/m0-claim-ab.md)。

- 幂等:按 doc_id 整文档事务替换,已抽取过的自动跳过(--force 重抽)
- 校验:每条 claim 过 lib_claims.validate(quote 原文子串 + 枚举),不过的丢弃并记录
- 容错:单篇失败(网络/JSON 解析)记报告不阻断;网络错误重试 1 次
- 并发:4 线程只并发 API 调用,写库全部在主线程单写者

用法:
  LLM_API_KEY=$DEEPSEEK_API_KEY LLM_BASE_URL=https://api.deepseek.com \
  LLM_MODEL=deepseek-flash SPIKE_EFFORT=none .venv/bin/python scripts/extract_claims.py
  # --limit N / --doc-ids a,b,c / --force
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_claims import resolve_institution, validate  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_llm import llm_config  # noqa: E402
from lib_paths import APP, DB  # noqa: E402
from spike_claims import PROMPT_PATH, extract_claims, load_doc  # noqa: E402

MAX_CHARS = 200_000  # M1 拍板:不截断策略,20 万字符兜底防爆(覆盖全部 228 篇)
WORKERS = 4
REPORT = APP / "plans" / "reports" / "m1-claim-extraction.md"
DROPPED_JSONL = APP / "plans" / "reports" / "m1-claim-dropped.jsonl"


def engine_tag() -> str:
    import os
    cfg = llm_config()
    mode = "thinking" if os.environ.get("LLM_THINKING") == "1" else (
        f"effort={os.environ['SPIKE_EFFORT']}" if os.environ.get("SPIKE_EFFORT") else "thinking-off")
    return f"{cfg['model']}/{mode}"


def extract_one(doc_id: str, db_path: Path) -> dict:
    """单篇抽取(纯 API + 校验,不写库,线程安全)。"""
    conn = connect(db_path)  # 每线程独立连接,只读
    try:
        doc = load_doc(conn, doc_id, max_chars=MAX_CHARS)
    finally:
        conn.close()
    last_err = None
    for attempt in (0, 1):  # 网络错误重试 1 次
        try:
            data, usage = extract_claims(doc)
            claims = data.get("claims", [])
            kept, dropped = [], []
            for i, c in enumerate(claims, 1):
                problems = validate(c, doc["body"])
                (dropped if problems else kept).append({**c, "ord": i, "problems": problems})
            return {"doc": doc, "kept": kept, "dropped": dropped,
                    "usage": {"in": usage.prompt_tokens, "out": usage.completion_tokens},
                    "error": None}
        except Exception as e:  # noqa: BLE001 单篇失败不阻断,记报告
            last_err = f"{type(e).__name__}: {e}"
            if attempt == 0:
                time.sleep(3)
    return {"doc": doc, "kept": [], "dropped": [], "usage": {"in": 0, "out": 0}, "error": last_err}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--doc-ids", help="逗号分隔")
    ap.add_argument("--force", action="store_true", help="重抽已有 claims 的文档")
    args = ap.parse_args()

    conn = connect(DB)
    rows = conn.execute("SELECT doc_id, title, institution, report_date FROM docs ORDER BY report_date, doc_id").fetchall()
    if args.doc_ids:
        wanted = set(args.doc_ids.split(","))
        rows = [r for r in rows if r["doc_id"] in wanted]
    if not args.force:
        done = {r[0] for r in conn.execute("SELECT doc_id FROM claims_extraction_log WHERE extracted_by=?", (engine_tag(),))}
        rows = [r for r in rows if r["doc_id"] not in done]
    if args.limit:
        rows = rows[:args.limit]
    print(f"待抽取 {len(rows)} 篇,引擎 {engine_tag()}", flush=True)

    total = {"in": 0, "out": 0, "kept": 0, "dropped": 0}
    failures, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(extract_one, r["doc_id"], DB): r for r in rows}
        for n, fut in enumerate(as_completed(futures), 1):
            r = futures[fut]
            res = fut.result()
            doc = res["doc"]
            total["in"] += res["usage"]["in"]
            total["out"] += res["usage"]["out"]
            if res["error"]:
                failures.append({"doc_id": r["doc_id"], "title": r["title"], "error": res["error"]})
                print(f"[{n}/{len(rows)}] ❌ {r['title'][:40]}: {res['error'][:80]}", flush=True)
                continue
            inst = resolve_institution(doc["institution"], doc["title"])
            with conn:  # 单事务:整文档替换
                conn.execute("DELETE FROM claims WHERE doc_id=?", (doc["doc_id"],))
                for c in res["kept"]:
                    conn.execute(
                        """INSERT INTO claims(doc_id, institution, report_date, subject, stance,
                           horizon, city, segment, claim_text, evidence_quote, is_forecast,
                           truncated, extracted_by)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (doc["doc_id"], inst, doc["report_date"], c.get("subject"), c.get("stance"),
                         c.get("horizon", ""), c.get("city", ""), c.get("segment", ""),
                         c.get("claim_text"), c.get("evidence_quote"),
                         1 if c.get("is_forecast") else 0, 1 if doc["truncated"] else 0,
                         engine_tag()))
            conn.execute("INSERT OR REPLACE INTO claims_extraction_log(doc_id, extracted_by, n_claims, extracted_at) VALUES(?,?,?,?)",
                         (doc["doc_id"], engine_tag(), len(res["kept"]),
                          time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            total["kept"] += len(res["kept"])
            total["dropped"] += len(res["dropped"])
            if res["dropped"]:
                with open(DROPPED_JSONL, "a", encoding="utf-8") as f:
                    for c in res["dropped"]:
                        f.write(json.dumps({"doc_id": doc["doc_id"], "title": doc["title"], **c},
                                           ensure_ascii=False) + "\n")
            print(f"[{n}/{len(rows)}] ✅ {inst or '未知'} 《{r['title'][:38]}》 "
                  f"抽 {len(res['kept'])} 条" + (f"(丢 {len(res['dropped'])})" if res["dropped"] else ""), flush=True)

    mins = (time.time() - t0) / 60
    n_claims = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    conn.close()
    report = [
        f"# M1 claim 全量抽取报告({time.strftime('%Y-%m-%d %H:%M')})",
        f"",
        f"- 引擎: {engine_tag()} · 并发 {WORKERS} · 耗时 {mins:.1f} 分钟",
        f"- 本轮: {len(rows)} 篇,入库 {total['kept']} 条,校验丢弃 {total['dropped']} 条,失败 {len(failures)} 篇",
        f"- tokens: in={total['in']:,} out={total['out']:,}",
        f"- claims 表现存量: {n_claims} 条",
    ]
    if failures:
        report.append("\n## 失败清单(可 --doc-ids 重跑)\n")
        report += [f"- {f['doc_id']} 《{f['title'][:50]}》: {f['error']}" for f in failures]
    REPORT.write_text("\n".join(report), encoding="utf-8")
    print(f"\n完成: 入库 {total['kept']} 条 / 丢 {total['dropped']} / 失败 {len(failures)} 篇 · "
          f"{mins:.1f} 分钟 · 报告 {REPORT}")


if __name__ == "__main__":
    main()
