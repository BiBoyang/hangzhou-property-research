"""M2 agent 循环:mock LLM 剧本单测(零 API 成本验证机制)。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lib_agent  # noqa: E402
from lib_agent import AgentConfig, run_agent  # noqa: E402


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _tc(i, name, args):
    return SimpleNamespace(id=f"call_{i}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)),
                           model_dump=lambda: {"id": f"call_{i}", "type": "function",
                                               "function": {"name": name, "arguments": json.dumps(args)}})


def _resp(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=_msg(content, tool_calls))],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))


class FakeClient:
    """预置剧本的 chat.completions.create;记录每次调用的 kwargs。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    class chat:  # noqa: D106
        pass

    def __post_init__(self):
        pass


def make_client(script):
    class C:
        def __init__(self):
            self.calls = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            self.calls.append(kwargs)
            return script.pop(0)
    return C()


class TestAgentLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        lib_agent.TRACES_DIR = Path(self.tmp.name)  # trace 落临时目录
        self.cfg = AgentConfig(trace=True)
        from lib_db import connect
        self.db = Path(self.tmp.name) / "t.db"
        connect(self.db).close()  # 空库,工具查询走临时 DB,不依赖正式库内容

    def test_first_round_forces_tools(self):
        client = make_client([_resp(tool_calls=[_tc(0, "query_claims", {"subject": "房价"})]),
                              _resp(content="终答")])
        run_agent("测试问题", self.cfg, client=client, db_path=self.db)
        self.assertEqual(client.calls[0].get("tool_choice"), "required")  # 首轮必查
        self.assertIn("tools", client.calls[1])          # 第 2 轮(非末轮)仍带工具
        self.assertNotIn("tool_choice", client.calls[1])  # 但不再强制

    def test_loop_executes_tool_and_finishes(self):
        client = make_client([_resp(tool_calls=[_tc(0, "query_claims", {"subject": "房价"})]),
                              _resp(content="【我的判断】...")])
        res = run_agent("房价怎么看", self.cfg, client=client, db_path=self.db)
        self.assertEqual(res["answer"], "【我的判断】...")
        self.assertEqual(res["llm_calls"], 2)
        self.assertEqual(res["tool_calls"], 1)
        self.assertTrue(Path(res["trace_path"]).exists())
        msgs = client.calls[1]["messages"]
        self.assertEqual(msgs[-1]["role"], "tool")  # 工具结果已塞回

    def test_parallel_tool_calls(self):
        client = make_client([_resp(tool_calls=[_tc(0, "query_claims", {}),
                                                _tc(1, "query_metrics", {"query": "杭州成交"}),
                                                _tc(2, "query_claims", {"stance": "看多"})]),
                              _resp(content="done")])
        res = run_agent("q", self.cfg, client=client, db_path=self.db)
        self.assertEqual(res["tool_calls"], 3)
        tool_msgs = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 3)
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["call_0", "call_1", "call_2"])  # 顺序保持

    def test_max_calls_forces_synthesis_without_tools(self):
        client = make_client([_resp(tool_calls=[_tc(0, "query_claims", {})]),
                              _resp(tool_calls=[_tc(1, "query_claims", {})]),
                              _resp(tool_calls=[_tc(2, "query_claims", {})]),
                              _resp(content="被迫终答")])
        res = run_agent("q", AgentConfig(max_llm_calls=4, trace=False), client=client, db_path=self.db)
        self.assertEqual(res["llm_calls"], 4)
        self.assertEqual(res["answer"], "被迫终答")
        self.assertNotIn("tools", client.calls[3])  # 第 4 轮(末轮)不带工具,保证 ≤ 上限

    def test_tool_error_fed_back_loop_continues(self):
        client = make_client([_resp(tool_calls=[_tc(0, "nonexistent_tool", {})]),
                              _resp(content="恢复")])
        res = run_agent("q", self.cfg, client=client, db_path=self.db)
        tool_msg = client.calls[1]["messages"][-1]
        self.assertIn("未知工具", tool_msg["content"])  # 错误反馈给模型而非炸循环
        self.assertEqual(res["answer"], "恢复")

    def test_compat_fallback_on_tool_choice_required(self):
        class RejectClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._c))
                self.n = 0

            def _c(self, **kwargs):
                if kwargs.get("tool_choice") == "required":
                    raise RuntimeError("unsupported")
                self.n += 1
                return _resp(content="ok")
        res = run_agent("q", AgentConfig(trace=False), client=RejectClient(), db_path=self.db)
        self.assertEqual(res["answer"], "ok")
        self.assertTrue(any(t["kind"] == "compat_fallback" for t in res["rounds"]))

    def test_min_tool_rounds_two(self):
        client = make_client([_resp(tool_calls=[_tc(0, "query_claims", {})]),
                              _resp(tool_calls=[_tc(1, "query_claims", {})]),
                              _resp(content="两轮后终答")])
        res = run_agent("q", AgentConfig(min_tool_rounds=2, trace=False), client=client, db_path=self.db)
        self.assertEqual(client.calls[0].get("tool_choice"), "required")
        self.assertEqual(client.calls[1].get("tool_choice"), "required")  # 第二轮仍强制
        self.assertEqual(res["answer"], "两轮后终答")


if __name__ == "__main__":
    unittest.main()
