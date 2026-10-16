"""docs.topic 列迁移 + 规则回填(M0)。

默认 dry-run(只读,打印分布与边界样本,可 --export 导出 CSV 供人扫);
--write 才执行 ALTER TABLE + 回填。幂等,可重复执行。

用法:
  .venv/bin/python scripts/backfill_topic.py                # dry-run
  .venv/bin/python scripts/backfill_topic.py --export PATH  # dry-run + 导出 CSV
  .venv/bin/python scripts/backfill_topic.py --write        # 迁移 + 回填
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_db import connect  # noqa: E402
from lib_paths import DB  # noqa: E402
from lib_topic import BODY_MIN_DENSITY, BODY_MIN_HITS, TOPICS, classify, count_hits  # noqa: E402


def migrate_add_topic(conn: sqlite3.Connection) -> bool:
    """显式迁移:docs 表补 topic 列。幂等;仿 migrate_add_source_file 先例,
    不放进 connect() 自动执行。返回是否执行了 ALTER。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(docs)")}
    if "topic" in cols:
        return False
    conn.execute("ALTER TABLE docs ADD COLUMN topic TEXT")
    return True


def collect_docs(conn: sqlite3.Connection) -> list[dict]:
    """读全量 docs + 拼接正文(chunks 按 ord),返回待分类记录。"""
    rows = conn.execute("""
        SELECT d.doc_id, d.title, d.institution, d.report_date,
               IFNULL(GROUP_CONCAT(ch.text, '\n\n'), '') AS body
        FROM docs d LEFT JOIN chunks ch ON ch.doc_id = d.doc_id
        GROUP BY d.doc_id
    """).fetchall()
    return [dict(r) for r in rows]


def diagnose(rec: dict) -> dict:
    """分类 + 诊断信息(各主题正文命中数/密度,供边界审查)。"""
    body = rec["body"]
    tags = classify(rec["title"], body)
    wan = max(len(body) / 10000.0, 0.1)
    hits = {t: count_hits(t, body) for t in TOPICS[:-1]}
    return {**rec, "tags": tags, "hits": hits,
            "borderline": any(
                BODY_MIN_HITS // 2 <= h < BODY_MIN_HITS and h / wan >= BODY_MIN_DENSITY / 2
                for h in hits.values())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="执行迁移+回填(默认 dry-run)")
    ap.add_argument("--export", metavar="CSV", help="导出 doc_id/标题/标签/命中数 CSV")
    ap.add_argument("--db", type=Path, default=DB, help=argparse.SUPPRESS)  # 测试用
    args = ap.parse_args()

    conn = connect(args.db)
    recs = [diagnose(r) for r in collect_docs(conn)]

    # 分布
    dist = Counter()
    combos = Counter()
    for r in recs:
        combos["+".join(r["tags"])] += 1
        for t in r["tags"]:
            dist[t] += 1
    print(f"共 {len(recs)} 篇。标签分布:")
    for t in TOPICS:
        print(f"  {t}: {dist.get(t, 0)}")
    print("组合 top10:")
    for combo, n in combos.most_common(10):
        print(f"  {n:4d}  {combo}")
    border = [r for r in recs if r["borderline"]]
    print(f"边界样本(命中数接近门槛,建议人工扫一眼): {len(border)} 篇")
    for r in border[:15]:
        print(f"  [{ '+'.join(r['tags']) }] {r['title'][:70]}  hits={r['hits']}")

    if args.export:
        with open(args.export, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["doc_id", "institution", "report_date", "title", "tags",
                        *[f"hits_{t}" for t in TOPICS[:-1]]])
            for r in recs:
                w.writerow([r["doc_id"], r["institution"], r["report_date"], r["title"],
                            "+".join(r["tags"]), *[r["hits"][t] for t in TOPICS[:-1]]])
        print(f"已导出 {args.export}")

    if not args.write:
        print("dry-run 结束(未写库)。确认分布后加 --write 执行。")
        conn.close()
        return

    migrated = migrate_add_topic(conn)
    print("迁移: docs.topic 列", "已新增" if migrated else "已存在,跳过")
    with conn:  # 单事务回填
        for r in recs:
            conn.execute("UPDATE docs SET topic=? WHERE doc_id=?",
                         (json.dumps(r["tags"], ensure_ascii=False), r["doc_id"]))
    # 回读校验:行数不变、JSON 可解、标题强信号一致性
    rows = conn.execute("SELECT doc_id, title, topic FROM docs").fetchall()
    assert len(rows) == len(recs), "回填后行数变了!"
    from lib_topic import TITLE_SIGNALS
    import re as _re
    bad = 0
    for row in rows:
        tags = json.loads(row["topic"])
        assert isinstance(tags, list) and tags, f"{row['doc_id']} topic 为空"
        for topic, pats in TITLE_SIGNALS.items():
            if any(_re.search(p, row["title"] or "", _re.I) for p in pats) and topic not in tags:
                bad += 1
                print(f"  [校验失败] 标题信号 {topic} 但标签缺失: {row['title'][:70]}")
    assert bad == 0, f"{bad} 篇标题信号与标签不一致"
    conn.close()
    print(f"回填完成: {len(rows)} 篇,校验通过。")


if __name__ == "__main__":
    main()
