"""M1 claims 观点层:schema/校验/机构兜底/查询/矩阵测试(临时库,不调 API)。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib_claims import (STANCES, SUBJECTS, query_claims, resolve_institution,  # noqa: E402
                        stance_matrix, validate)
from lib_db import connect  # noqa: E402

GOOD = {"subject": "房价", "stance": "看空", "horizon": "2026年内", "city": "全国",
        "segment": "secondary_home", "claim_text": "房价继续跌", "evidence_quote": "原文句子",
        "is_forecast": True}


class TestValidate(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(validate(GOOD, "这是原文句子。"), [])

    def test_quote_must_be_substring(self):
        self.assertTrue(validate({**GOOD, "evidence_quote": "编造的"}, "这是原文句子。"))

    def test_quote_trailing_punct_tolerated(self):
        self.assertEqual(validate({**GOOD, "evidence_quote": "原文句子。"}, "这是原文句子"), [])

    def test_bad_enums(self):
        self.assertTrue(validate({**GOOD, "stance": "谨慎乐观"}, "原文句子"))
        self.assertTrue(validate({**GOOD, "subject": "市场趋势"}, "原文句子"))

    def test_forecast_without_horizon_allowed(self):
        self.assertEqual(validate({**GOOD, "horizon": ""}, "原文句子"), [])


class TestInstitutionFallback(unittest.TestCase):
    def test_db_value_wins(self):
        self.assertEqual(resolve_institution("UBS", "任何标题"), "UBS")

    def test_from_title(self):
        self.assertEqual(resolve_institution("", "20260518-Morgan_Stanley-China_Property"), "Morgan Stanley")
        self.assertEqual(resolve_institution("", "Nomura-Asia Insights：China"), "Nomura")
        self.assertEqual(resolve_institution("", "中指丨3月上海上涨"), "中指院")

    def test_unknown_empty(self):
        self.assertEqual(resolve_institution("", "居民资产负债表已开始修复"), "")


class TestQueryAndMatrix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "t.db")
        import backfill_topic
        backfill_topic.migrate_add_topic(self.conn)  # topic 列是显式迁移,不在 connect() 自动加
        c = self.conn
        c.execute("INSERT INTO docs(doc_id, file_path, title, topic) VALUES('d1','/a','高盛研报', ?)",
                  (json.dumps(["房地产"], ensure_ascii=False),))
        c.execute("INSERT INTO docs(doc_id, file_path, title, topic) VALUES('d2','/b','宏观研报', ?)",
                  (json.dumps(["宏观政策"], ensure_ascii=False),))
        rows = [
            ("d1", "Goldman Sachs", "2026-04-09", "房价", "看多", "沪深2026年末触底"),
            ("d1", "Goldman Sachs", "2026-04-09", "成交量", "看多", "成交回暖"),
            ("d1", "Goldman Sachs", "2026-05-01", "房价", "中性", "涨幅放缓"),
            ("d2", "Nomura", "2026-04-16", "房价", "看空", "继续下跌"),
        ]
        for doc, inst, date, subj, stance, text in rows:
            c.execute("""INSERT INTO claims(doc_id,institution,report_date,subject,stance,horizon,city,
                         segment,claim_text,evidence_quote,is_forecast,truncated,extracted_by)
                         VALUES(?,?,?,?,?,'','','',?, 'q',0,0,'test')""",
                      (doc, inst, date, subj, stance, text))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_query_filter_subject(self):
        rows = query_claims(self.conn, subject="房价")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["report_date"] for r in rows], ["2026-04-09", "2026-04-16", "2026-05-01"])  # 时间轴序

    def test_query_filter_stance_and_institution(self):
        self.assertEqual(len(query_claims(self.conn, stance="看空")), 1)
        self.assertEqual(len(query_claims(self.conn, institution="Goldman Sachs")), 3)

    def test_query_topic_filter(self):
        self.assertEqual(len(query_claims(self.conn, topic="房地产")), 3)
        self.assertEqual(len(query_claims(self.conn, topic="宏观政策")), 1)
        self.assertEqual(len(query_claims(self.conn, topic="商品外汇")), 0)

    def test_matrix_groups_by_institution_date(self):
        m = stance_matrix(self.conn, subject="房价")
        self.assertEqual(len(m), 3)
        self.assertEqual(m[0]["stances"], {"看多": 1})
        self.assertEqual(m[0]["institution"], "Goldman Sachs")
        # 反转可见:GS 4月看多 → 5月中性,两条都在,无多数投票
        gs = [g for g in m if g["institution"] == "Goldman Sachs"]
        self.assertEqual([g["stances"] for g in gs], [{"看多": 1}, {"中性": 1}])

    def test_enums_frozen(self):
        self.assertIn("中性", STANCES)
        self.assertIn("库存与去化", SUBJECTS)


if __name__ == "__main__":
    unittest.main()
