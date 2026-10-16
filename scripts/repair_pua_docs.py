"""修复 PUA 字体混淆文档(TASK 风险 5)。

侦察结论(2026-10-16,plans/TASK-agentic-rag.md):
- 5 篇:数字被映射到 U+F356..F35F(渲染验证:F356+d = 数字 d)→ 确定性映射表修复
- 2 篇(《对两类增量政策》《政治局会议通稿思考》):文字层 cmap 全错(大面积错字)
  → pypdfium2 渲染 + macOS Vision OCR 整页重识别(视觉渲染是对的,只是文字层烂)
- 2 篇(AI宏观/UBS Outlook):仅 F0xx 区 Wingdings 符号,与数字无关 → 不动

修复链:备份 raw_md → 修文本 → store_doc 重切块(doc_id=路径哈希,稳定)
→ 定向重建索引(jieba text_seg + FTS rebuild + 定向 vec) → --claims 时重抽观点

用法:
  .venv/bin/python scripts/repair_pua_docs.py            # 修文本 + 重切块 + 重建索引
  .venv/bin/python scripts/repair_pua_docs.py --claims   # 追加重抽这 7 篇的 claims(ds-flash)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest import build_meta, store_doc  # noqa: E402
from lib_db import connect, ensure_vec_table  # noqa: E402
from lib_paths import APP, DB, RAW_MD  # noqa: E402

PUA_RE = re.compile(r'[-]')
DIGIT_MAP = {chr(0xF356 + i): str(i) for i in range(10)}  # F356..F35F → 0..9
BACKUP_DIR = APP / "data" / "backups" / f"pua-{time.strftime('%Y%m%d')}"


def find_affected(conn) -> dict[str, list[dict]]:
    """扫描全库,分三组:map(纯数字映射)/ ocr(文字层烂)/ skip(F0xx 符号,不动)。"""
    groups = {"map": [], "ocr": [], "skip": []}
    for r in conn.execute("SELECT doc_id, title, file_path FROM docs"):
        md = RAW_MD / (Path(r["file_path"]).stem + ".md")
        if not md.exists():
            continue
        codes = {ord(c) for c in PUA_RE.findall(md.read_text(encoding="utf-8"))}
        if not codes:
            continue
        if codes <= set(range(0xF356, 0xF360)):
            groups["map"].append(dict(r))
        elif all(0xF000 <= c <= 0xF0FF for c in codes):
            groups["skip"].append(dict(r))
        else:
            groups["ocr"].append(dict(r))
    return groups


def _md_path(doc: dict) -> Path:
    return RAW_MD / (Path(doc["file_path"]).stem + ".md")


def fix_by_map(doc: dict) -> int:
    md_path = _md_path(doc)
    text = md_path.read_text(encoding="utf-8")
    fixed = text.translate(str.maketrans(DIGIT_MAP))
    n = len(PUA_RE.findall(text))
    md_path.write_text(fixed, encoding="utf-8")
    return n


def fix_by_ocr(doc: dict) -> int:
    """整页渲染 + Vision OCR,覆盖 raw_md。返回识别字符数。"""
    import pypdfium2 as pdfium
    from ocrmac import ocrmac

    pdf = pdfium.PdfDocument(doc["file_path"])
    pages = []
    tmp = Path("/tmp") / f"pua_ocr_{doc['doc_id']}"
    tmp.mkdir(exist_ok=True)
    for i in range(len(pdf)):
        img = tmp / f"p{i:03d}.png"
        pdf[i].render(scale=2.5).to_pil().save(img)
        results = ocrmac.OCR(str(img), recognition_level="accurate",
                             language_preference=["zh-Hans", "en-US"]).recognize()
        pages.append("\n\n".join(t for t, _conf, _box in results))
        print(f"    page {i+1}/{len(pdf)}: {len(results)} blocks", flush=True)
    text = "\n\n".join(pages)
    _md_path(doc).write_text(text, encoding="utf-8")
    return len(text)


def reindex_docs(conn, doc_ids: list[str]) -> None:
    """定向重建:jieba text_seg + FTS 全局 rebuild + 这 7 篇的向量重算。"""
    import jieba
    docs_ph = ",".join("?" * len(doc_ids))
    rows = conn.execute(
        f"""SELECT c.chunk_id, c.heading, c.text, d.title FROM chunks c
            JOIN docs d ON d.doc_id = c.doc_id WHERE c.doc_id IN ({docs_ph}) ORDER BY c.chunk_id""",
        doc_ids).fetchall()
    print(f"重切 text_seg: {len(rows)} chunks")
    for r in rows:
        seg = " ".join(jieba.lcut(f"{r['title']} | {r['heading']} | {r['text']}"))
        conn.execute("UPDATE chunks SET text_seg=? WHERE chunk_id=?", (seg, r["chunk_id"]))
    conn.commit()
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    # 向量:先删这 7 篇旧行,再只 embed 新 chunks
    from sentence_transformers import SentenceTransformer
    import torch
    conn.execute(f"DELETE FROM chunks_vec WHERE chunk_id IN "
                 f"(SELECT chunk_id FROM chunks WHERE doc_id IN ({docs_ph}))", doc_ids)
    conn.commit()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device=device)
    model.max_seq_length = 1024
    ensure_vec_table(conn, model.get_sentence_embedding_dimension())
    t0 = time.time()
    for i in range(0, len(rows), 32):
        batch = rows[i:i + 32]
        embs = model.encode([f"{r['title']} | {r['heading']} | {r['text']}" for r in batch],
                            batch_size=64, show_progress_bar=False, normalize_embeddings=True)
        conn.executemany("INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
                         [(r["chunk_id"], e.tobytes()) for r, e in zip(batch, embs)])
        conn.commit()
    print(f"vec 重建 {len(rows)} chunks,{time.time()-t0:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", action="store_true", help="修完追加重抽 claims(ds-flash)")
    ap.add_argument("--doc-ids", help="跳过文本修复,只对指定 doc 重切块+重建索引")
    args = ap.parse_args()

    conn = connect(DB)
    if args.doc_ids:
        ids = args.doc_ids.split(",")
        docs = [dict(r) for r in conn.execute(
            f"SELECT doc_id, title, file_path FROM docs WHERE doc_id IN ({','.join('?'*len(ids))})", ids)]
        for doc in docs:
            md = _md_path(doc).read_text(encoding="utf-8")
            meta = build_meta(Path(doc["file_path"]), md)
            n = store_doc(conn, meta, md)
            print(f"  重切块 {doc['title'][:36]}: {n} chunks")
        reindex_docs(conn, ids)
        conn.close()
        if args.claims:
            _run_claims(ids)
        return
    groups = find_affected(conn)
    print(f"分组: 映射 {len(groups['map'])} 篇 / OCR {len(groups['ocr'])} 篇 / 跳过(符号) {len(groups['skip'])} 篇")
    assert len(groups["ocr"]) == 2, f"OCR 组应为 2 篇,实际 {len(groups['ocr'])}:请人工核对新出现的乱码文档"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    fixed_ids = []
    for doc in groups["map"]:
        shutil.copy2(_md_path(doc), BACKUP_DIR / _md_path(doc).name)
        n = fix_by_map(doc)
        fixed_ids.append(doc["doc_id"])
        print(f"  映射修复 {doc['title'][:36]}: {n} 个 PUA 数字还原")
    for doc in groups["ocr"]:
        shutil.copy2(_md_path(doc), BACKUP_DIR / _md_path(doc).name)
        print(f"  OCR 重识别 {doc['title'][:36]} ...", flush=True)
        n = fix_by_ocr(doc)
        fixed_ids.append(doc["doc_id"])
        print(f"    → {n} chars")

    # 重切块(doc_id 由路径哈希决定,稳定;store_doc 幂等:OR REPLACE + 删旧 chunks)
    for doc in groups["map"] + groups["ocr"]:
        md = _md_path(doc).read_text(encoding="utf-8")
        meta = build_meta(Path(doc["file_path"]), md)
        n = store_doc(conn, meta, md)
        print(f"  重切块 {doc['title'][:36]}: {n} chunks")

    # 残留检查
    for doc in groups["map"] + groups["ocr"]:
        left = PUA_RE.findall(_md_path(doc).read_text(encoding="utf-8"))
        print(f"  残留 PUA {doc['title'][:36]}: {len(left)}")

    reindex_docs(conn, fixed_ids)
    conn.close()
    print(f"备份在 {BACKUP_DIR}")

    if args.claims:
        _run_claims(fixed_ids)


def _run_claims(doc_ids: list[str]) -> None:
    env = dict(os.environ)
    env.update({"LLM_API_KEY": os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", "")),
                "LLM_BASE_URL": "https://api.deepseek.com",
                "LLM_MODEL": "deepseek-flash", "SPIKE_EFFORT": "none"})
    subprocess.run([sys.executable, str(APP / "scripts" / "extract_claims.py"),
                    "--force", "--doc-ids", ",".join(doc_ids)], env=env, check=True)


if __name__ == "__main__":
    main()
