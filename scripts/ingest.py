"""增量入库：扫描研报目录 → Docling 解析(新增/变更的) → 元数据 → 切分 → SQLite + FTS。

用法: python scripts/ingest.py [--rebuild]
- 默认增量：file_path 已在库内的跳过
- --rebuild：清空 docs/chunks 重建（向量索引需随后单独重建）
- 网页采集研报：BASE/web研报/*.md 直接读取（front matter 提供元数据），不走 Docling
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from lib_meta import build_meta, parse_front_matter  # noqa: E402
from lib_chunk import split_markdown  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_paths import BASE, DB, RAW_MD, SKIP_DIRS, WEB_DIR  # noqa: E402


def iter_pdfs() -> list[Path]:
    return sorted(
        p for p in BASE.rglob("*.pdf")
        if not any(part in SKIP_DIRS for part in p.parts) and p.is_file()
    )


def iter_web_docs() -> list[Path]:
    if not WEB_DIR.is_dir():
        return []
    return sorted(p for p in WEB_DIR.glob("*.md") if p.is_file())


def parse_pdf(converter, pdf: Path) -> str:
    result = converter.convert(str(pdf))
    return result.document.export_to_markdown()


def store_doc(conn, meta, md: str) -> int:
    conn.execute(
        "INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?)",
        (meta.doc_id, meta.file_path, meta.title, meta.institution,
         meta.report_date, meta.lang, json.dumps(meta.cities, ensure_ascii=False)),
    )
    chunks = split_markdown(md)
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (meta.doc_id,))
    conn.executemany(
        "INSERT INTO chunks(chunk_id, doc_id, ord, heading, text, text_seg, is_table) VALUES (?,?,?,?,?,?,?)",
        [
            (f"{meta.doc_id}-{c.ord:04d}", meta.doc_id, c.ord, c.heading,
             c.text, c.text, int(c.is_table))  # text_seg 由 index 构建脚本做 jieba 切词
            for c in chunks
        ],
    )
    conn.commit()
    return len(chunks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    RAW_MD.mkdir(parents=True, exist_ok=True)
    conn = connect(DB)

    if args.rebuild:
        conn.executescript("DELETE FROM chunks_fts; DELETE FROM chunks; DELETE FROM docs;")

    known = {
        row["file_path"]: row["doc_id"]
        for row in conn.execute("SELECT doc_id, file_path FROM docs")
    }
    pdfs = iter_pdfs()
    web_docs = iter_web_docs()
    todo_pdfs = [p for p in pdfs if str(p) not in known]
    todo_web = [p for p in web_docs if str(p) not in known]
    if not todo_pdfs and not todo_web:
        print(f"无新增（库内 {len(known)} 份 / 磁盘 PDF {len(pdfs)} 份 + 网页研报 {len(web_docs)} 份）")
        return

    converter = None
    if todo_pdfs:
        # 延迟初始化 Docling（重依赖，有 PDF 新增才加载）
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.datamodel.base_models import InputFormat

        opts = PdfPipelineOptions()
        opts.do_table_structure = True
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
        opts.do_ocr = False
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    total_new = len(todo_pdfs) + len(todo_web)
    for i, pdf in enumerate(todo_pdfs, 1):
        t0 = time.time()
        md_path = RAW_MD / (pdf.stem + ".md")
        if md_path.exists():
            md = md_path.read_text(encoding="utf-8")
        else:
            md = parse_pdf(converter, pdf)
            md_path.write_text(md, encoding="utf-8")
        meta = build_meta(pdf, md)
        n = store_doc(conn, meta, md)
        print(f"[{i}/{total_new}] {pdf.name}: {n} chunks, {time.time()-t0:.1f}s", flush=True)

    for i, src in enumerate(todo_web, len(todo_pdfs) + 1):
        t0 = time.time()
        fields, body = parse_front_matter(src.read_text(encoding="utf-8"))
        md_path = RAW_MD / (src.stem + ".md")
        md_path.write_text(body, encoding="utf-8")
        meta = build_meta(src, body, front=fields)
        n = store_doc(conn, meta, body)
        print(f"[{i}/{total_new}] {src.name}: {n} chunks, {time.time()-t0:.1f}s", flush=True)

    # 重建 FTS
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    print(f"完成：库内文档 {len(known)+total_new} 份，chunks 共 {total}")


if __name__ == "__main__":
    main()
