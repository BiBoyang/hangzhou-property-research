"""查询规范化(检索方差对策)单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib_search import normalize_query  # noqa: E402


class TestNormalizeQuery(unittest.TestCase):
    def test_strip_question_scaffolding(self):
        self.assertEqual(normalize_query("杭州2026年3月二手房成交多少套?"), "杭州2026年3月二手房成交 套")
        self.assertEqual(normalize_query("高盛对一线城市房价何时见底?"), "高盛对一线城市房价 见底")

    def test_english_stopwords(self):
        out = normalize_query("What was the inventory months figure in Exhibit 3?")
        self.assertNotIn("What", out)
        self.assertIn("inventory", out)
        self.assertIn("Exhibit 3", out)

    def test_already_compact_unchanged(self):
        self.assertEqual(normalize_query("瑞银态度变化"), "瑞银态度变化")

    def test_empty_fallback_original(self):
        self.assertEqual(normalize_query("吗?"), "吗?")  # 剥光则回退原文

    def test_entities_preserved(self):
        out = normalize_query("UBS 对上海的判断是什么")
        self.assertIn("UBS", out)
        self.assertIn("上海", out)


if __name__ == "__main__":
    unittest.main()
