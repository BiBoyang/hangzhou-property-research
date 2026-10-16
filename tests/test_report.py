"""M4 研判报告编排:planner 解析/兜底、矩阵规则渲染、节失败容错、合成降级(全 mock,零 API)。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lib_report  # noqa: E402
import backfill_topic  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_report import plan_report, render_matrix, run_report  # noqa: E402


def _client_returning(text):
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    return SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kw: resp)))


class TestPlanner(unittest.TestCase):
    def test_parse_ok_and_enum_filter(self):
        c = _client_returning(json.dumps({
            "matrix_subjects": ["房价", "不存在的主题", "成交量"],
            "sections": {"结论": "q1", "风险": "q5"}}, ensure_ascii=False))
        plan = plan_report("主题", client=c)
        self.assertEqual(plan["matrix_subjects"], ["房价", "成交量"])  # 非法枚举被滤
        self.assertEqual(plan["sections"]["结论"], "q1")
        self.assertIn("数据支撑", plan["sections"])  # 缺的节兜底补齐

    def test_failure_fallback(self):
        def boom(**kw):
            raise RuntimeError("down")
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
        plan = plan_report("主题", client=client)
        self.assertEqual(set(plan["sections"]), set(lib_report.SECTIONS))
        self.assertEqual(plan["matrix_subjects"], ["房价", "成交量"])

    def test_bad_json_fallback(self):
        plan = plan_report("主题", client=_client_returning("不是JSON"))
        self.assertEqual(set(plan["sections"]), set(lib_report.SECTIONS))


class TestRenderMatrix(unittest.TestCase):
    def test_rule_render(self):
        tmp = tempfile.TemporaryDirectory()
        conn = connect(Path(tmp.name) / "t.db")
        backfill_topic.migrate_add_topic(conn)  # topic 列是显式迁移
        conn.execute("INSERT INTO docs(doc_id, file_path, title, topic) VALUES('d1','/a','t', '[\"房地产\"]')")
        conn.execute("""INSERT INTO claims(doc_id,institution,report_date,subject,stance,horizon,city,
                        segment,claim_text,evidence_quote,is_forecast,truncated,extracted_by)
                        VALUES('d1','UBS','2026-01-22','房价','看空','2026','','', '26年难见底','q',1,0,'t')""")
        conn.commit()
        md = render_matrix(conn, ["房价"], "房地产")
        conn.close()
        tmp.cleanup()
        self.assertIn("| 2026-01-22 | UBS | 看空 | 26年难见底 | 2026 |", md)
        self.assertIn("**房价**", md)

    def test_empty(self):
        tmp = tempfile.TemporaryDirectory()
        conn = connect(Path(tmp.name) / "t.db")
        self.assertEqual(render_matrix(conn, ["房价"], None), "(观点库无相关记录)")
        conn.close()
        tmp.cleanup()


class TestRunReport(unittest.TestCase):
    def test_orchestration_and_section_failure(self):
        tmp = tempfile.TemporaryDirectory()
        db = Path(tmp.name) / "t.db"
        _c = connect(db); backfill_topic.migrate_add_topic(_c); _c.commit(); _c.close()

        def fake_run_agent(query, config, db_path=None):
            if "分歧" in query:
                raise RuntimeError("section boom")
            return {"answer": f"[{config.system_extra[:6]}]内容", "usage": {"in": 100, "out": 50},
                    "llm_calls": 2, "tool_calls": 1, "trace_path": "", "rounds": []}

        planner = _client_returning(json.dumps({
            "matrix_subjects": ["房价"],
            "sections": {n: f"q-{n}" for n in lib_report.SECTIONS}}, ensure_ascii=False))
        synth = _client_returning("# 合成后报告\n正文")
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: planner.chat.completions.create() if False else None)))
        # planner 与合成共用一个按序返回的 client
        calls = [_client_returning(json.dumps({
            "matrix_subjects": ["房价"],
            "sections": {n: f"q-{n}" for n in lib_report.SECTIONS}}, ensure_ascii=False)).chat.completions.create(),
                 _client_returning("# 合成后报告\n正文").chat.completions.create()]
        seq = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: calls.pop(0))))

        with mock.patch("lib_report.run_agent", side_effect=fake_run_agent):
            res = run_report("测试主题", topic_filter="房地产", db_path=db,
                             client=seq, out_dir=Path(tmp.name))
        self.assertTrue(res["synthesized"])
        text = Path(res["path"]).read_text(encoding="utf-8")
        self.assertIn("# 合成后报告", text)
        self.assertIn("附表:机构观点矩阵", text)
        failed = [s["name"] for s in res["sections"] if s["error"]]
        self.assertEqual(failed, ["分歧分析"])  # 单节失败不阻断
        self.assertEqual(res["llm_calls"], 1 + 4 * 2 + 1)  # planner + 4 成功节×2 + 合成
        tmp.cleanup()

    def test_synth_failure_rule_assembly(self):
        tmp = tempfile.TemporaryDirectory()
        db = Path(tmp.name) / "t.db"
        _c = connect(db); backfill_topic.migrate_add_topic(_c); _c.commit(); _c.close()

        def fake_run_agent(query, config, db_path=None):
            return {"answer": "节内容", "usage": {"in": 10, "out": 5},
                    "llm_calls": 1, "tool_calls": 0, "trace_path": "", "rounds": []}

        planner_resp = json.dumps({"matrix_subjects": ["房价"],
                                   "sections": {n: f"q-{n}" for n in lib_report.SECTIONS}},
                                  ensure_ascii=False)

        def create(**kw):
            if not hasattr(create, "n"):
                create.n = 0
            create.n += 1
            if create.n == 1:
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=planner_resp))],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
            raise RuntimeError("synth down")
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with mock.patch("lib_report.run_agent", side_effect=fake_run_agent):
            res = run_report("主题", topic_filter=None, db_path=db, client=seq_client(client), out_dir=Path(tmp.name))
        self.assertFalse(res["synthesized"])
        text = Path(res["path"]).read_text(encoding="utf-8")
        self.assertIn("## 结论", text)  # 规则拼装兜底
        tmp.cleanup()


def seq_client(c):
    return c


if __name__ == "__main__":
    unittest.main()
