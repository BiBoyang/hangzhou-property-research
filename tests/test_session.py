"""M3 多轮 session:存储 CRUD / 实体抽取 / 上下文拼装 / 指代消解(mock,零 API)。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib_agent import resolve_query  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_session import (append_turn, build_context, extract_entities,  # noqa: E402
                         latest_session, list_turns, new_session)


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_create_append_list(self):
        sid = new_session(self.conn, "房地产")
        self.assertEqual(append_turn(self.conn, sid, "Q1", "Q1", "A1", 2, 3, "/t/1.jsonl"), 1)
        self.assertEqual(append_turn(self.conn, sid, "Q2", "Q2改写", "A2", 4, 5, "/t/2.jsonl"), 2)
        turns = list_turns(self.conn, sid)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[1]["standalone_question"], "Q2改写")
        self.assertEqual(latest_session(self.conn), sid)

    def test_latest_session_empty(self):
        self.assertIsNone(latest_session(self.conn))


class TestEntities(unittest.TestCase):
    def test_extract(self):
        e = extract_entities("高盛和瑞银对上海2026年房价怎么看?UBS 与 Goldman Sachs")
        self.assertIn("Goldman Sachs", e["机构"])
        self.assertIn("UBS", e["机构"])
        self.assertIn("上海", e["城市"])
        self.assertIn("2026年", e["时期"])

    def test_dedup_and_order(self):
        e = extract_entities("杭州 杭州 杭州,深圳")
        self.assertEqual(e["城市"], ["杭州", "深圳"])


class TestBuildContext(unittest.TestCase):
    def _turn(self, i, q, a):
        return {"turn": i, "question": q, "answer": a}

    def test_empty(self):
        self.assertEqual(build_context([]), "")

    def test_recent_two_full_rest_one_line(self):
        turns = [self._turn(i, f"问题{i}关于杭州", "答案" * 200) for i in range(1, 5)]
        ctx = build_context(turns)
        self.assertIn("城市: 杭州", ctx)                       # 实体注册表
        self.assertIn("更早话题", ctx)                          # 1-2 轮折叠成一行
        self.assertIn("第4轮 问: 问题4关于杭州", ctx)           # 最近两轮保留
        self.assertIn("第3轮 问: 问题3关于杭州", ctx)
        self.assertNotIn("第1轮 问:", ctx)                      # 早轮不出全文
        self.assertLess(len(ctx), 2000)                         # 截断生效(4×200字答案没全带)

    def test_no_raw_chunks(self):
        # 上下文不含长原文:答案 5000 字也只带 300
        ctx = build_context([self._turn(1, "q", "x" * 5000)])
        self.assertLess(len(ctx), 1000)


class TestResolveQuery(unittest.TestCase):
    def test_no_context_zero_call(self):
        # 首轮无历史:不调 LLM,原样返回
        self.assertEqual(resolve_query("独立问题", ""), "独立问题")

    def test_rewrite_used(self):
        resp = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="瑞银2026年以来对中国楼市的态度有什么变化?"))],
            usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: resp)))
        out = resolve_query("那瑞银呢?", "【对话历史】涉及实体 — 机构: Goldman Sachs", client=client)
        self.assertEqual(out, "瑞银2026年以来对中国楼市的态度有什么变化?")

    def test_failure_fallback_to_original(self):
        def boom(**kw):
            raise RuntimeError("network down")
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
        self.assertEqual(resolve_query("那瑞银呢?", "【对话历史】...", client=client), "那瑞银呢?")

    def test_empty_rewrite_fallback(self):
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: resp)))
        self.assertEqual(resolve_query("Q", "有历史", client=client), "Q")


if __name__ == "__main__":
    unittest.main()
