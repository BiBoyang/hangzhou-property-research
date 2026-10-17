"""agent 深研页(/agent + /api/agent/ask)测试。

mock run_agent/resolve_query(零 LLM、零网络);session 写临时库(RAG_AGENT_DB);
正式库指纹保护;TestClient 不进 lifespan。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))
sys.path.insert(0, str(APP / "web"))

import app as web_app  # noqa: E402
import lib_agent  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_paths import DB as FORMAL_DB  # noqa: E402

FAKE_RES = {"answer": "【我的判断】测试答案", "llm_calls": 2, "tool_calls": 3,
            "usage": {"in": 10, "out": 5}, "trace_path": "", "rounds": [], "contexts": []}


def _fingerprint(path: Path):
    st = path.stat()
    return (st.st_size, hashlib.sha256(path.read_bytes()).hexdigest())


class TestAgentWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tmpdb = Path(cls.tmp.name) / "t.db"
        connect(cls.tmpdb).close()  # 建空 schema
        cls.env_patcher = mock.patch.dict(os.environ, {"RAG_AGENT_DB": str(cls.tmpdb)})
        cls.env_patcher.start()
        from fastapi.testclient import TestClient
        cls.client = TestClient(web_app.app)
        cls.formal_fp = _fingerprint(FORMAL_DB)

    @classmethod
    def tearDownClass(cls):
        cls.env_patcher.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.p_run = mock.patch.object(lib_agent, "run_agent", return_value=dict(FAKE_RES))
        self.p_resolve = mock.patch.object(lib_agent, "resolve_query", side_effect=lambda q, c: q)
        self.p_run.start()
        self.p_resolve.start()

    def tearDown(self):
        self.p_run.stop()
        self.p_resolve.stop()

    def _ask(self, q="测试问题", agent="property", session=""):
        resp = self.client.post(f"/api/agent/ask?q={q}&agent={agent}&session={session}")
        assert resp.status_code == 200
        return [json.loads(l) for l in resp.text.splitlines() if l.strip()]

    def test_page_200_and_nav(self):
        r = self.client.get("/agent")
        self.assertEqual(r.status_code, 200)
        self.assertIn("深研", r.text)
        for page in ["/", "/overview", "/metrics", "/docs"]:
            self.assertIn("/agent", self.client.get(page).text, f"{page} 导航缺深研")

    def test_invalid_agent_422(self):
        r = self.client.post("/api/agent/ask?q=测试问题&agent=bogus")
        self.assertEqual(r.status_code, 422)

    def test_ask_flow_and_session_written(self):
        events = self._ask()
        done = [e for e in events if e.get("done")]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["answer"], "【我的判断】测试答案")
        sid = done[0]["session"]
        conn = connect(self.tmpdb)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM agent_turns WHERE session_id=?",
                                      (sid,)).fetchone()[0], 1)
        conn.close()

    def test_multi_turn_continues(self):
        sid = [e for e in self._ask() if e.get("done")][0]["session"]
        events = self._ask(q="追问呢", session=sid)
        done = [e for e in events if e.get("done")][0]
        self.assertEqual(done["session"], sid)
        conn = connect(self.tmpdb)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM agent_turns WHERE session_id=?",
                                      (sid,)).fetchone()[0], 2)
        conn.close()

    def test_unknown_session_error_event(self):
        events = self._ask(session="不存在的session")
        self.assertTrue(any("session 不存在" in e.get("error", "") for e in events))

    def test_progress_events(self):
        events = self._ask()
        self.assertTrue(any("理解问题中" in e.get("stage", "") for e in events))

    def test_xss_question_not_raw_in_page(self):
        r = self.client.get("/agent")
        self.assertNotIn("<script>alert", r.text)  # 模板无注入点
        events = self._ask(q="x<script>alert(1)</script>")  # JSON 流原样传输,前端 DOMPurify 兜底
        self.assertTrue(all(isinstance(e, dict) for e in events))

    def test_formal_db_untouched(self):
        self._ask()
        self.assertEqual(_fingerprint(FORMAL_DB), self.formal_fp)


if __name__ == "__main__":
    unittest.main()
