"""混合检索：FTS5 BM25 + sqlite-vec 向量，RRF 融合。"""
from __future__ import annotations

from pathlib import Path

from lib_db import connect
from lib_paths import DB

import jieba  # noqa: E402

MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
RRF_K = 60

# 查询扩展：中文俗称 → 英文机构名（中英跨语言检索短板的对冲）
SYNONYMS = {
    "瑞银": "UBS", "高盛": "Goldman", "摩根大通": "J.P. Morgan", "小摩": "J.P. Morgan",
    "摩根士丹利": "Morgan Stanley", "大摩": "Morgan Stanley", "野村": "Nomura",
    "公积金": "Provident Fund", "去化": "inventory", "挂牌": "listing",
    "北京": "Beijing", "上海": "Shanghai", "深圳": "Shenzhen", "广州": "Guangzhou",
    "杭州": "Hangzhou", "香港": "Hong Kong", "一线": "tier-1", "新政": "easing",
}

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
    return _model


def fts_search(conn, query: str, k: int = 20) -> list[str]:
    seg = " ".join(jieba.lcut(query))
    # FTS5 MATCH：词间 OR，中文短查询召回优先
    terms = [t for t in seg.split() if len(t) > 1 or "一" <= t <= "鿿"]
    for zh, en in SYNONYMS.items():
        if zh in query:
            terms.append(en)
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms[:12])
    rows = conn.execute(
        """SELECT c.chunk_id FROM chunks_fts f
           JOIN chunks c ON c.rowid = f.rowid
           WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
        (match, k),
    ).fetchall()
    return [r["chunk_id"] for r in rows]


def vec_search(conn, query: str, k: int = 20) -> list[str]:
    emb = _get_model().encode([query], normalize_embeddings=True)[0].tobytes()
    rows = conn.execute(
        """SELECT chunk_id FROM chunks_vec
           WHERE embedding MATCH ? ORDER BY distance LIMIT ?""",
        (emb, k),
    ).fetchall()
    return [r["chunk_id"] for r in rows]


def rrf_merge(rankings: list[list[str]], k: int = 10) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)[:k]


DATE_Q = __import__("re").compile(r"(\d{4})?年?(\d{1,2})月")


def doc_boost(conn, query: str, k: int = 10) -> list[str]:
    """文档级先验：标题命中城市/机构同义词、且报告期与问题年月相符的文档，其 chunks 提权。
    按命中维度数给文档打分，只保留最高分档；每文档取前部 chunks 按文档分排序。"""
    scores: dict[str, int] = {}
    for zh, en in SYNONYMS.items():
        if zh in query:
            for r in conn.execute(
                "SELECT doc_id FROM docs WHERE title LIKE ? OR institution LIKE ?",
                (f"%{en}%", f"%{en}%"),
            ):
                scores[r["doc_id"]] = scores.get(r["doc_id"], 0) + 1
    m = DATE_Q.search(query)
    if m:
        year = m.group(1) or "2026"
        for r in conn.execute("SELECT doc_id FROM docs WHERE report_date LIKE ?",
                              (f"{year}-{int(m.group(2)):02d}%",)):
            scores[r["doc_id"]] = scores.get(r["doc_id"], 0) + 1
    if not scores:
        return []
    best = max(scores.values())
    top_docs = [d for d, s in sorted(scores.items(), key=lambda x: -x[1]) if s == best]
    if not top_docs:
        return []
    ph = ",".join("?" * len(top_docs))
    # 每文档只取前 12 个 chunk，按文档得分排序拼成 ranking（首 chunk 得最高分）
    order = {d: i for i, d in enumerate(top_docs)}
    rows = conn.execute(
        f"""SELECT doc_id, chunk_id FROM chunks
            WHERE doc_id IN ({ph}) AND ord < 12""",
        top_docs,
    ).fetchall()
    rows = sorted(rows, key=lambda r: (order[r["doc_id"]], r["chunk_id"]))
    return [r["chunk_id"] for r in rows][: k * 5]


NOISE = ("disclosure statements", "All rights reserved", "analyst certification",
         "com/disclosures", "attributed to a third party", "Global Research through",
         "法律声明", "免责声明", "SAC Registration")


def _is_noise(text: str) -> bool:
    return any(m in text for m in NOISE)


def search(query: str, k: int = 8, db_path: Path = DB) -> list[dict]:
    conn = connect(db_path)
    merged = rrf_merge(
        [fts_search(conn, query), vec_search(conn, query), doc_boost(conn, query)],
        k * 3,
    )
    if not merged:
        return []
    placeholders = ",".join("?" * len(merged))
    rows = conn.execute(
        f"""SELECT c.chunk_id, c.heading, c.text, c.is_table,
                   d.title, d.institution, d.report_date
            FROM chunks c JOIN docs d ON d.doc_id = c.doc_id
            WHERE c.chunk_id IN ({placeholders})""",
        merged,
    ).fetchall()
    by_id = {r["chunk_id"]: dict(r) for r in rows}
    out = [by_id[cid] for cid in merged if cid in by_id and not _is_noise(by_id[cid]["text"])]
    return out[:k]
