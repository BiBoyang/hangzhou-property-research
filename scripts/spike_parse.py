"""Spike: Docling parse 3 representative PDFs, output Markdown for quality review."""
import sys
from pathlib import Path

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.datamodel.base_models import InputFormat

from lib_paths import APP, BASE  # noqa: E402

OUT = APP / "data" / "spike"

SAMPLES = [
    BASE / "Goldman Sachs-CHINA PROPERTY：Positioning ahead of Tier~1 cities turnaround-260409 (1).pdf",
    BASE / "中国房地产指数系统百城价格指数报告（2026年3月）.pdf",
    BASE / "UBS-China Property：How does the Rmb11trn Housing Provident Fund stabilize the market？-260818.pdf",
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.do_ocr = False  # 投行 PDF 是文字版；若发现扫描件再开
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    for pdf in SAMPLES:
        if not pdf.exists():
            print(f"MISSING: {pdf}", flush=True)
            continue
        print(f">>> parsing: {pdf.name}", flush=True)
        result = converter.convert(str(pdf))
        md = result.document.export_to_markdown()
        out_file = OUT / (pdf.stem + ".md")
        out_file.write_text(md, encoding="utf-8")
        n_tables = sum(1 for line in md.splitlines() if line.strip().startswith("|"))
        print(f"    done: {len(md)} chars, {n_tables} table-lines -> {out_file.name}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
