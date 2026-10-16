"""M5 轻量评测:评分规则 + 注册表单测(零 API)。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import yaml  # noqa: E402
from lib_agent import AGENTS  # noqa: E402
from lib_paths import APP  # noqa: E402
from run_agent_eval import score_answer  # noqa: E402

GOOD = ("【我的判断】x\n【支持的机构】高盛认为…[Goldman Sachs 2026-04-09]\n"
        "【不同意见的机构】无\n【我为什么这么选】x")


class TestScore(unittest.TestCase):
    def test_all_pass(self):
        q = {"qtype": "opinion", "must_include_any": ["2026"], "must_cite_any": ["Goldman Sachs"]}
        checks = score_answer(q, GOOD + "2026 年企稳", 3)
        self.assertTrue(all(checks.values()))

    def test_structure_missing(self):
        checks = score_answer({"qtype": "opinion"}, "没有结构", 2)
        self.assertFalse(checks["structure"])

    def test_calls_cap(self):
        self.assertFalse(score_answer({"qtype": "x"}, GOOD, 5)["calls_le4"])
        self.assertTrue(score_answer({"qtype": "x"}, GOOD, 4)["calls_le4"])

    def test_cite_must_be_real_format(self):
        q = {"qtype": "opinion", "must_cite_any": ["Goldman Sachs"]}
        bad = "【我的判断】【支持的机构】【不同意见的机构】【我为什么这么选】高盛说过"
        self.assertFalse(score_answer(q, bad, 2)["must_cite_any"])  # 无 [机构 日期] 格式不算

    def test_uncovered(self):
        q = {"qtype": "uncovered"}
        self.assertTrue(score_answer(q, GOOD + "现有资料未覆盖成都", 2)["uncovered_said"])
        self.assertFalse(score_answer(q, GOOD + "成都均价 1.5 万", 2)["uncovered_said"])


class TestEvalSet(unittest.TestCase):
    def test_yaml_schema(self):
        qs = yaml.safe_load((APP / "eval" / "agent_questions.yaml").read_text(encoding="utf-8"))
        self.assertEqual(len(qs), 13)
        for q in qs:
            self.assertIn(q["agent"], ("property", "all"))
            self.assertIn("question", q)
            if q["qtype"] != "uncovered":
                self.assertTrue(q.get("must_include_any") or q.get("must_cite_any"),
                                f"{q['id']} 无评分锚点")

    def test_agents_registry(self):
        self.assertEqual(AGENTS["property"].topic_filter, "房地产")
        self.assertIsNone(AGENTS["all"].topic_filter)


if __name__ == "__main__":
    unittest.main()
