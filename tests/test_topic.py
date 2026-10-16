"""M0 topic 规则打标:分类规则单测 + backfill 迁移/回填/幂等测试(临时库,不碰正式库)。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib_topic import TOPICS, classify  # noqa: E402
import backfill_topic  # noqa: E402
from lib_db import connect  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_property_en_title(self):
        self.assertEqual(classify("J.P. Morgan-China Property：Developers' sales stayed weak", ""), ["房地产"])

    def test_gold_title(self):
        self.assertEqual(classify("Global Precious Metals Comment：Is the gold rally over?", ""), ["商品外汇"])

    def test_macro_title(self):
        self.assertEqual(classify("Citi China Economics：In-line Targets but Underwhelming Policies", ""), ["宏观政策"])

    def test_ev_title(self):
        self.assertEqual(classify("Nomura-China EV：Weak start for EV considering policy change", ""), ["行业消费"])

    def test_politburo_cn(self):
        self.assertIn("宏观政策", classify("对2026年7月政治局会议通稿的思考", ""))

    def test_no_signal_is_other(self):
        self.assertEqual(classify("Some Random Notes", "nothing relevant here"), ["其他"])

    def test_multi_label_macro_title_property_body(self):
        body = "房地产 房价 二手房 成交 去化 按揭 " * 30
        tags = classify("对2026年7月政治局会议通稿的思考", body)
        self.assertIn("宏观政策", tags)
        self.assertIn("房地产", tags)

    def test_word_boundary_ai_not_triggered_by_substring(self):
        # "training/maintaining" 不含独立 AI 词,不应误标行业消费
        self.assertEqual(classify("", "training and maintaining " * 50), ["其他"])

    def test_body_below_threshold_not_tagged(self):
        # 正文零星提及(阈值保守,宁可漏打)
        tags = classify("", "GDP fiscal policy 房地产 成交 " * 3)
        self.assertNotIn("房地产", tags)

    def test_topics_order_stable(self):
        self.assertEqual(TOPICS[-1], "其他")


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        conn = connect(self.db)
        conn.execute("INSERT INTO docs(doc_id, file_path, title) VALUES('d1','/a.pdf','China Property：Home prices')")
        conn.execute("INSERT INTO docs(doc_id, file_path, title) VALUES('d2','/b.pdf','Gold rally comment')")
        conn.execute("INSERT INTO chunks(chunk_id, doc_id, ord, heading, text) VALUES('d1-0000','d1',0,'','housing mortgage property ' * 30)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_idempotent(self):
        conn = connect(self.db)
        self.assertTrue(backfill_topic.migrate_add_topic(conn))
        conn.commit()
        self.assertFalse(backfill_topic.migrate_add_topic(conn))
        conn.close()

    def test_backfill_writes_tags(self):
        conn = connect(self.db)
        backfill_topic.migrate_add_topic(conn)
        conn.commit()
        for rec in map(backfill_topic.diagnose, backfill_topic.collect_docs(conn)):
            conn.execute("UPDATE docs SET topic=? WHERE doc_id=?",
                         (json.dumps(rec["tags"], ensure_ascii=False), rec["doc_id"]))
        conn.commit()
        rows = {r["doc_id"]: json.loads(r["topic"]) for r in conn.execute("SELECT doc_id, topic FROM docs")}
        conn.close()
        self.assertEqual(rows["d1"], ["房地产"])
        self.assertEqual(rows["d2"], ["商品外汇"])

    def test_collect_docs_without_chunks(self):
        conn = connect(self.db)
        conn.execute("INSERT INTO docs(doc_id, file_path, title) VALUES('d3','/c.pdf','空文档')")
        conn.commit()
        recs = {r["doc_id"]: r for r in backfill_topic.collect_docs(conn)}
        conn.close()
        self.assertEqual(recs["d3"]["body"], "")
        self.assertEqual(recs["d3"]["tags"] if "tags" in recs["d3"] else classify(recs["d3"]["title"], ""), ["其他"])


if __name__ == "__main__":
    unittest.main()
