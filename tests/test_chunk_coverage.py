"""切分治本(2026-10-17):短段合并、appendix 解禁、披露仍丢弃、表格原子。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib_chunk import split_markdown  # noqa: E402


class TestChunkCoverage(unittest.TestCase):
    def test_short_sections_merged_not_dropped(self):
        md = "## 第一节\n短内容甲。\n## 第二节\n" + "正文。" * 300 + "\n## 第三节\n短内容乙。"
        chunks = split_markdown(md)
        full = "\n".join(c.text for c in chunks)
        self.assertIn("短内容甲", full)   # 开头短段并入下一段
        self.assertIn("短内容乙", full)   # 结尾短段并入前一段
        self.assertNotEqual(len(chunks), 0)

    def test_appendix_kept(self):
        md = "## 正文\n" + "正文。" * 300 + "\n## Appendix\n| 指标 | 值 |\n| 库存 | 12个月 |"
        full = "\n".join(c.text for c in split_markdown(md))
        self.assertIn("库存", full)  # 附录表格不再被 DROP_HEADINGS 丢弃

    def test_disclaimer_still_dropped(self):
        md = "## 正文\n" + "正文。" * 300 + "\n## Disclaimer\nall rights reserved. analyst certification."
        full = "\n".join(c.text for c in split_markdown(md))
        self.assertNotIn("all rights reserved", full)

    def test_table_atomic(self):
        md = "## 数据\n正文开头。" + "长句。" * 200 + "\n| 列A | 列B |\n| 1 | 2 |\n| 3 | 4 |"
        chunks = split_markdown(md)
        tables = [c for c in chunks if c.is_table]
        self.assertEqual(len(tables), 1)
        self.assertIn("| 3 | 4 |", tables[0].text)  # 表格完整不拆

    def test_lone_short_chunk_survives(self):
        # 通篇全是短段(无长文可并):短段保留
        md = "## 甲\n短。\n## 乙\n也短。"
        full = "\n".join(c.text for c in split_markdown(md))
        self.assertIn("短", full)


    def test_giant_table_splits_with_header_repeat(self):
        # 巨型表格:按行组拆,每块带表头
        rows = "\n".join(f"| 城市{i} | 数值{format(i, '04d')} | {'x'*40} |" for i in range(120))
        md = "## 数据\n" + "正文。" * 100 + "\n| 城市 | 数值 | 备注 |\n|---|---|---|\n" + rows
        tables = [c for c in split_markdown(md) if c.is_table]
        self.assertGreater(len(tables), 1)                       # 拆了
        for t in tables:
            self.assertIn("| 城市 | 数值 | 备注 |", t.text)       # 每块带表头
            self.assertLess(len(t.text), 2000)                    # 每块不大
        full = "\n".join(t.text for t in tables)
        self.assertIn("数值0119", full)                           # 末行没丢


if __name__ == "__main__":
    unittest.main()
