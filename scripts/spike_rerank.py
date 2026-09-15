"""Spike：RRF 候选 → CrossEncoder(bge-reranker-base) 重排，对比现有 top-8。

用法（首次下载模型约 1GB）：
  env -u http_proxy -u https_proxy -u all_proxy HF_ENDPOINT=https://hf-mirror.com \\
      .venv/bin/python scripts/spike_rerank.py
再次运行可 HF_HUB_OFFLINE=1 离线。生产若上线建议评估 bge-reranker-v2-m3（多语言更强）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_db import connect  # noqa: E402
from lib_paths import DB  # noqa: E402
from lib_search import (  # noqa: E402
    _is_noise, doc_boost, fts_search, rrf_merge, search, vec_search,
)

QUERIES = [
    "瑞银公积金报告认为11万亿公积金如何稳定市场？",
    "高盛2026年4月16日地产月报对一线城市房价环比的判断是什么？",
    "2026年一季度中国GDP增速，野村/高盛/瑞银的点评口径分别是什么？",
    "各机构对上海房价底部时点的判断分别是什么？",
    "2026年8月北京楼市新政的主要内容是什么？各机构怎么评价？",
    "公积金贷款利率后续还有下调空间吗？瑞银怎么说？",
]

MODEL = "BAAI/bge-reranker-base"


def candidates(conn, q: str, pool: int = 24) -> list[dict]:
    merged = rrf_merge(
        [fts_search(conn, q), vec_search(conn, q), doc_boost(conn, q)], pool)
    if not merged:
        return []
    ph = ",".join("?" * len(merged))
    rows = conn.execute(
        f"""SELECT c.chunk_id, c.heading, c.text, d.title, d.institution, d.report_date
            FROM chunks c JOIN docs d ON d.doc_id = c.doc_id
            WHERE c.chunk_id IN ({ph})""", merged).fetchall()
    by = {r["chunk_id"]: dict(r) for r in rows}
    return [by[c] for c in merged if c in by and not _is_noise(by[c]["text"])]


def label(c: dict) -> str:
    return f"{c['institution'][:12]} {c['report_date']} {c['title'][:28]}|{c['heading'][:16]}"


def main() -> None:
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(MODEL, max_length=512)
    conn = connect(DB)
    for q in QUERIES:
        base = search(q, k=8, db_path=DB)
        cands = candidates(conn, q)
        scores = model.predict([(q, c["text"][:1200]) for c in cands])
        order = sorted(range(len(cands)), key=lambda i: -scores[i])
        reranked = [cands[i] for i in order[:8]]
        base_ids = {c["chunk_id"] for c in base}
        new_ids = {c["chunk_id"] for c in reranked}
        print(f"\n=== {q}")
        print("  现有 top-8:")
        for c in base:
            mark = "  " if c["chunk_id"] in new_ids else "▼"
            print(f"   {mark} {label(c)}")
        print("  rerank top-8:")
        for c in reranked:
            mark = "▲" if c["chunk_id"] not in base_ids else "  "
            print(f"   {mark} {label(c)}")
        print(f"  变动：{len(new_ids - base_ids)} 进 / {len(base_ids - new_ids)} 出")


if __name__ == "__main__":
    main()
