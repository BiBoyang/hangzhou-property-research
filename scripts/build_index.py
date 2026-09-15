"""构建检索索引：jieba 分词灌 FTS5 + Qwen3-Embedding-0.6B 向量灌 sqlite-vec。

用法: python scripts/build_index.py [--limit N]
"""
from __future__ import annotations

import argparse
import time

import jieba  # noqa: E402
from lib_db import connect, ensure_vec_table  # noqa: E402
from lib_paths import DB  # noqa: E402

MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
BATCH = 32


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = connect(DB)
    # 检索文本 = 报告标题 + 章节标题 + 正文（标题承载强信号，修复"公积金报告"类查询沉底）
    rows = conn.execute(
        """SELECT c.chunk_id, c.heading, c.text, d.title
           FROM chunks c JOIN docs d ON d.doc_id = c.doc_id ORDER BY c.chunk_id"""
        + (f" LIMIT {args.limit}" if args.limit else "")
    ).fetchall()
    print(f"chunks: {len(rows)}")

    # 1) FTS：jieba 分词写入 text_seg 并重建 FTS
    t0 = time.time()
    for r in rows:
        seg = " ".join(jieba.lcut(f"{r['title']} | {r['heading']} | {r['text']}"))
        conn.execute("UPDATE chunks SET text_seg=? WHERE chunk_id=?", (seg, r["chunk_id"]))
    conn.commit()
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    print(f"FTS done {time.time()-t0:.1f}s")

    # 2) 向量：sentence-transformers 本地加载（HF 缓存已有，离线可用）
    from sentence_transformers import SentenceTransformer

    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL_NAME, device=device)
    model.max_seq_length = 1024  # chunk 本身 ≤1500 字符，1024 tokens 足够且提速明显
    print(f"device={device}")
    dim = model.get_sentence_embedding_dimension()
    ensure_vec_table(conn, dim)
    conn.execute("DELETE FROM chunks_vec")

    t0 = time.time()
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        embs = model.encode(
            [f"{r['title']} | {r['heading']} | {r['text']}" for r in batch],
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        conn.executemany(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            [(r["chunk_id"], e.tobytes()) for r, e in zip(batch, embs)],
        )
        conn.commit()
        print(f"vec {min(i+BATCH, len(rows))}/{len(rows)} {time.time()-t0:.0f}s", flush=True)
    print("index done")


if __name__ == "__main__":
    main()
