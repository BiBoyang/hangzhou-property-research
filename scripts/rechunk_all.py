"""切分治本(2026-10-17):lib_chunk 修复后全库重切 + 索引重建。

修复内容:短段合并(原 MIN_CHARS 直接丢弃,172/228 篇覆盖率<80%)+ appendix 解禁。
chunks 全量替换(chunk_id 含 ord,会变)→ text_seg/FTS/向量全量重建。
claims/metrics 不经 chunks,不受影响;docs 行不动(元数据未变)。

用法: .venv/bin/python scripts/rechunk_all.py   # 自动先备份 DB 到 data/backups/
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_chunk import split_markdown  # noqa: E402
from lib_db import connect, ensure_vec_table  # noqa: E402
from lib_paths import APP, DB, RAW_MD  # noqa: E402


def main() -> None:
    backup = APP / "data" / "backups" / f"rag-pre-rechunk-{time.strftime('%Y%m%d-%H%M%S')}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB, backup)
    print(f"DB 已备份: {backup}")

    conn = connect(DB)
    docs = conn.execute("SELECT doc_id, file_path FROM docs").fetchall()
    # 重切
    t0 = time.time()
    total_chunks = 0
    for d in docs:
        md_path = RAW_MD / (Path(d["file_path"]).stem + ".md")
        if not md_path.exists():
            continue
        chunks = split_markdown(md_path.read_text(encoding="utf-8"))
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (d["doc_id"],))
        conn.executemany(
            "INSERT INTO chunks(chunk_id, doc_id, ord, heading, text, text_seg, is_table) VALUES (?,?,?,?,?,?,?)",
            [(f"{d['doc_id']}-{c.ord:04d}", d["doc_id"], c.ord, c.heading, c.text, c.text,
              int(c.is_table)) for c in chunks])
        total_chunks += len(chunks)
    conn.commit()
    print(f"重切完成: {total_chunks} chunks ({time.time()-t0:.0f}s)")

    # text_seg + FTS
    import jieba
    rows = conn.execute("""SELECT c.chunk_id, c.heading, c.text, d.title FROM chunks c
                           JOIN docs d ON d.doc_id=c.doc_id ORDER BY c.chunk_id""").fetchall()
    t0 = time.time()
    for r in rows:
        seg = " ".join(jieba.lcut(f"{r['title']} | {r['heading']} | {r['text']}"))
        conn.execute("UPDATE chunks SET text_seg=? WHERE chunk_id=?", (seg, r["chunk_id"]))
    conn.commit()
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    print(f"FTS 重建 ({time.time()-t0:.0f}s)")

    # 向量全量重建
    from sentence_transformers import SentenceTransformer
    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device=device)
    model.max_seq_length = 1024
    ensure_vec_table(conn, model.get_sentence_embedding_dimension())
    conn.execute("DELETE FROM chunks_vec")
    conn.commit()
    t0 = time.time()
    for i in range(0, len(rows), 32):
        batch = rows[i:i + 32]
        embs = model.encode([f"{r['title']} | {r['heading']} | {r['text']}" for r in batch],
                            batch_size=64, show_progress_bar=False, normalize_embeddings=True)
        conn.executemany("INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
                         [(r["chunk_id"], e.tobytes()) for r, e in zip(batch, embs)])
        conn.commit()
        if i % 1600 == 0:
            print(f"vec {i}/{len(rows)} {time.time()-t0:.0f}s", flush=True)
    conn.close()
    print(f"全部完成,共 {len(rows)} chunks")


if __name__ == "__main__":
    main()
