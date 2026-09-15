"""对无文本层的 PDF 用 OCR（macOS Vision / ocrmac）重解析，覆盖 raw_md。

用法: python scripts/reparse_ocr.py <md文件名不含扩展名> [...]
"""
from __future__ import annotations

import sys
from pathlib import Path

from lib_paths import BASE, RAW_MD, SKIP_DIRS  # noqa: E402


def find_pdf(stem: str) -> Path | None:
    for p in BASE.rglob(stem + ".pdf"):
        if not any(part in SKIP_DIRS for part in p.parts):
            return p
    return None


def main() -> None:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.datamodel.base_models import InputFormat

    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.do_ocr = True
    from docling.datamodel.pipeline_options import OcrMacOptions
    opts.ocr_options = OcrMacOptions()
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )

    for stem in sys.argv[1:]:
        pdf = find_pdf(stem)
        if not pdf:
            print(f"MISSING PDF for {stem}")
            continue
        print(f">>> OCR: {pdf.name}", flush=True)
        result = converter.convert(str(pdf))
        md = result.document.export_to_markdown()
        (RAW_MD / (stem + ".md")).write_text(md, encoding="utf-8")
        print(f"    {len(md)} chars", flush=True)


if __name__ == "__main__":
    main()
