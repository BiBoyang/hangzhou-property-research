"""把 metrics/ 下的 .csv / .jsonl 指标文件载入 SQLite metrics 表。

幂等策略：以 source_file（相对 metrics 根目录的文件名，posix 形式）为稳定身份，
单事务内整文件替换（按 source_file 删旧后插新）。同文件内完全重复行折叠并报告；
跨文件相同内容各自保留（各挂各 source_file）。不再按 doc_id/source_url 删除。

阻断保护（拒绝且零写入；CLI 预检 + load() 事务内复核，直接调 load() 无法绕过）：
旧 schema（缺 source_file 列）、历史未归属行（source_file 为 NULL/空串）、
库中来源文件在盘上缺失（重命名/删除支持另行任务）、文件不存在/为空/解析或校验失败。
显式迁移：--migrate-source-file 只加列，不连带导入、不归属历史数据。

输入契约：JSONL 顶层每条记录必须是对象；value 只接受数字或字符串（拒绝布尔、
对象、数组与纯空白）；is_forecast 只接受确切的 0/1（缺省 None/"" → 0，
兼容布尔与 "0"/"1"，拒绝 0.9 之类会被 int() 截断的值）。

注意：本模块一律使用普通 sqlite3 连接，不调用 lib_db.connect()——
被拒绝前不得间接触发建表/迁移/commit。整文件替换后 metric_id 会重新分配。
源数据行内自带的 source_file 键一律忽略，身份只来自文件路径。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

from lib_db import migrate_add_source_file  # noqa: E402
from lib_paths import DB, METRICS_DIR  # noqa: E402

BUSINESS_COLS = ("doc_id", "institution", "period", "city", "segment", "metric_name",
                 "value", "value_text", "unit", "comparison_type", "comparison_value",
                 "is_forecast", "source_page", "source_quote", "source_url")

_INSERT_SQL = (
    "INSERT INTO metrics(doc_id, institution, period, city, segment, metric_name,"
    " value, value_text, unit, comparison_type, comparison_value, is_forecast,"
    " source_page, source_quote, source_url, source_file)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

# 合法时期：2026-08 / 2026Q3 / 2026-H1 / 2026（月份 01-12、季度 1-4、半年 H1-H2）
_PERIOD_RE = re.compile(r"^20\d{2}(-(0[1-9]|1[0-2])|Q[1-4]|-H[12])?$")

_STR_FIELDS = ("institution", "period", "city", "segment", "metric_name", "unit",
               "comparison_type", "source_page", "source_quote", "doc_id", "source_url")


class LoadError(Exception):
    """导入被拒绝或失败（消息含原因）；抛出前保证未写入或已回滚。"""


def read_rows(path: Path) -> list:
    """逐行解析源文件。JSONL 单行解析失败带行号抛 LoadError。

    返回值中每条记录可能是任意 JSON 值（null/数组/字符串等），
    调用方必须先校验记录是对象，不能直接当 dict 访问字段。
    文件必须是合法 UTF-8（不做编码探测），解码失败带文件名与字节位置。
    """
    try:
        if path.suffix == ".jsonl":
            rows = []
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise LoadError(f"第 {lineno} 行解析失败: {e.msg}")
            return rows
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except UnicodeDecodeError as e:  # UnicodeError ⊂ ValueError，单独捕获以补文件名与位置
        raise LoadError(f"解析失败: {path.name}: 非法 UTF-8 字节（位置 {e.start}）: {e.reason}")


def _coerce_is_forecast(v) -> int:
    """预测标记只接受确切的 0/1：缺省 None/"" → 0；兼容布尔、0.0/1.0、"0"/"1"；
    拒绝 0.9 之类会被 int() 截断的值及复合类型。"""
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        if v in (0, 1):
            return v
    elif isinstance(v, float):
        if math.isfinite(v) and v in (0.0, 1.0):
            return int(v)
    elif isinstance(v, str):
        if v.strip() in ("0", "1"):
            return int(v.strip())
    raise ValueError(f"is_forecast 只能为确切的 0/1: {v!r}")


def convert_row(r: dict) -> dict:
    """源记录 → 15 个业务字段。与历史 loader 逐条同规则，供 Step 2B 归属复用。

    仅供通过 validate_row() 的记录使用：value/is_forecast 的类型收窄由校验保证。
    """
    val = r.get("value")
    value_num: float | None = None
    value_text: str | None = None
    if isinstance(val, str):
        try:
            value_num = float(val)  # "2027" → 2027.0 的既有行为保留
        except ValueError:
            value_text = val  # text 型指标（如 price_bottom_timing "2026Q4"）
    else:
        value_num = float(val)  # 校验已保证为有限 int/float
    return {
        "doc_id": r.get("doc_id") or None,
        "institution": r.get("institution", ""),
        "period": r.get("period", ""),
        "city": r.get("city", ""),
        "segment": r.get("segment", ""),
        "metric_name": r.get("metric_name", ""),
        "value": value_num,
        "value_text": value_text,
        "unit": r.get("unit", ""),
        "comparison_type": r.get("comparison_type") or None,
        "comparison_value": (float(r.get("comparison_value"))
                             if r.get("comparison_value") not in (None, "") else None),
        "is_forecast": _coerce_is_forecast(r.get("is_forecast")),
        "source_page": r.get("source_page", ""),
        "source_quote": r.get("source_quote", ""),
        "source_url": r.get("source_url") or None,
    }


def validate_row(r: dict) -> list[str]:
    errs = []
    for f in _STR_FIELDS:
        v = r.get(f)
        if v is not None and not isinstance(v, str):
            errs.append(f"{f} 必须是字符串: {v!r}")
    for f in ("institution", "period", "metric_name", "unit", "source_quote"):
        v = r.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(f"{f} 为空")
    p = r.get("period")
    if isinstance(p, str) and p and not _PERIOD_RE.match(p):
        errs.append(f"period 不合法: {p!r}（支持 2026-08 / 2026Q3 / 2026-H1 / 2026）")
    v = r.get("value")
    if v is None or (isinstance(v, str) and not v.strip()):
        errs.append("value 缺失或为空白")
    elif isinstance(v, bool):
        errs.append(f"value 不接受布尔值: {v!r}")
    elif isinstance(v, (int, float)):
        if not math.isfinite(v):
            errs.append(f"value 非有限数值: {v!r}")
    elif isinstance(v, str):
        try:
            if not math.isfinite(float(v)):
                errs.append(f"value 非有限数值: {v!r}")
        except ValueError:
            pass  # 合法文本型指标 → value_text
    else:
        errs.append(f"value 只接受数字或字符串，实际为 {type(v).__name__}")
    cv = r.get("comparison_value")
    if cv is not None and cv != "":
        try:
            if not math.isfinite(float(cv)):
                errs.append(f"comparison_value 非有限数值: {cv!r}")
        except (TypeError, ValueError):
            errs.append(f"comparison_value 不是数字: {cv!r}")
    try:
        _coerce_is_forecast(r.get("is_forecast"))
    except ValueError as e:
        errs.append(str(e))
    return errs


def _identity(path: Path, metrics_root: Path) -> str:
    try:
        return path.resolve().relative_to(metrics_root.resolve()).as_posix()
    except ValueError:
        raise LoadError(f"文件不在 metrics 根目录内: {path}")


def _db_guards(conn: sqlite3.Connection, metrics_root: Path) -> None:
    """数据库级阻断条件。在写事务内调用，确保检查与写入之间状态不变。"""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'")}
    if not tables:
        raise LoadError("metrics 表不存在：数据库未初始化")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
    if "source_file" not in cols:
        raise LoadError("metrics 表缺少 source_file 列：先运行显式迁移 --migrate-source-file")
    n = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE source_file IS NULL OR source_file=''").fetchone()[0]
    if n:
        raise LoadError(f"存在 {n} 行未归属历史记录（source_file 为空）：导入阻断，待 Step 2B 完成归属")
    missing = sorted(sf for (sf,) in conn.execute("SELECT DISTINCT source_file FROM metrics")
                     if not (metrics_root / sf).is_file())
    if missing:
        raise LoadError("来源文件缺失: " + ", ".join(missing)
                        + "（重命名/删除来源文件的支持另行任务，本次拒绝导入）")


def _record_errors(raw_rows: list) -> list[str]:
    """逐条校验；顶层非对象记录（null/数组/字符串等）报位置，不进入字段访问。"""
    errors = []
    for i, r in enumerate(raw_rows, 1):
        if not isinstance(r, dict):
            kind = {type(None): "null", list: "数组", str: "字符串"}.get(
                type(r), type(r).__name__)
            errors.append(f"第{i}条记录: 必须是对象，实际为 {kind}")
            continue
        errors.extend(f"第{i}条记录: {err}" for err in validate_row(r))
    return errors


def load(path: Path, *, db_path: Path, metrics_root: Path) -> dict:
    """导入单个文件。成功返回报告 dict；任何拒绝/失败抛 LoadError 且库不变。"""
    path, db_path, metrics_root = Path(path), Path(db_path), Path(metrics_root)
    rel = _identity(path, metrics_root)
    if path.suffix not in (".csv", ".jsonl"):
        raise LoadError(f"不支持的文件类型: {path.name}")
    if not path.is_file():
        raise LoadError(f"文件不存在: {path}")
    try:
        raw_rows = read_rows(path)
    except (ValueError, OSError, csv.Error) as e:  # LoadError 不在其中，直接透传
        raise LoadError(f"解析失败: {path.name}: {e}")
    if not raw_rows:
        raise LoadError("空文件或无有效记录，拒绝导入（不提供空文件清库开关）")
    errors = _record_errors(raw_rows)
    if errors:
        shown = "\n".join(errors[:5])
        raise LoadError(f"校验失败 {len(errors)} 处:\n{shown}"
                        + ("\n…" if len(errors) > 5 else ""))
    rows, seen, folded = [], set(), 0
    for r in raw_rows:  # 同文件内完全重复（15 业务字段精确一致）折叠
        c = convert_row(r)
        key = tuple(c[col] for col in BUSINESS_COLS)
        if key in seen:
            folded += 1
            continue
        seen.add(key)
        rows.append(c)
    if not db_path.is_file():
        raise LoadError(f"数据库不存在: {db_path}（先经正常初始化流程建库）")
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
    except sqlite3.Error as e:
        raise LoadError(f"无法打开数据库: {e}")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _db_guards(conn, metrics_root)
        deleted = conn.execute("DELETE FROM metrics WHERE source_file=?", (rel,)).rowcount
        conn.executemany(_INSERT_SQL,
                         [tuple(r[c] for c in BUSINESS_COLS) + (rel,) for r in rows])
        conn.commit()
    except LoadError:
        conn.rollback()
        raise
    except sqlite3.Error as e:
        conn.rollback()
        raise LoadError(f"写入失败，已回滚: {e}")
    finally:
        conn.close()
    return {"file": rel, "parsed": len(raw_rows), "folded": folded,
            "deleted": deleted, "inserted": len(rows)}


def _migrate(db_path: Path) -> int:
    if not db_path.is_file():
        print(f"数据库不存在: {db_path}（先经正常初始化流程建库）")
        return 1
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        changed = migrate_add_source_file(conn)
        if changed:
            conn.commit()
    except RuntimeError as e:
        print(f"迁移失败: {e}")
        return 1
    finally:
        conn.close()
    if changed:
        print("已执行迁移：metrics 增加 source_file 列")
        print("注意：历史行 source_file 仍为 NULL，导入保持阻断，待 Step 2B 完成归属。")
    else:
        print("source_file 列已存在，无需迁移")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="载入 metrics/ 指标文件（source_file 幂等替换）")
    ap.add_argument("files", nargs="*", type=Path,
                    help="要导入的 .csv/.jsonl；缺省为 metrics 根目录下全部")
    ap.add_argument("--db", type=Path, default=DB, help="目标数据库（默认 data/rag.db）")
    ap.add_argument("--metrics-root", type=Path, default=METRICS_DIR,
                    help="metrics 根目录，source_file 身份相对它（默认 metrics/）")
    ap.add_argument("--migrate-source-file", action="store_true",
                    help="显式迁移：metrics 表补 source_file 列（只迁移，不导入）")
    args = ap.parse_args(argv)
    if args.migrate_source_file:
        if args.files:
            ap.error("--migrate-source-file 与文件参数互斥")
        return _migrate(args.db)
    files = args.files or sorted(args.metrics_root.glob("*.csv")) \
        + sorted(args.metrics_root.glob("*.jsonl"))
    if not files:
        print(f"未找到指标文件（{args.metrics_root}）")
        return 1
    if not args.db.is_file():
        print(f"数据库不存在: {args.db}（先经正常初始化流程建库）")
        return 1
    conn = sqlite3.connect(str(args.db), timeout=30)  # 全局阻断预检；load() 事务内会复核
    try:
        _db_guards(conn, args.metrics_root)
    except LoadError as e:
        print(f"全局阻断: {e}")
        return 1
    finally:
        conn.close()
    ok = fail = 0
    for f in files:
        try:
            r = load(f, db_path=args.db, metrics_root=args.metrics_root)
            print(f"{r['file']}: 记录 {r['parsed']} 条，折叠同文件重复 {r['folded']} 条，"
                  f"替换 {r['deleted']} → {r['inserted']}")
            ok += 1
        except LoadError as e:
            print(f"{f.name}: 拒绝 — {e}")
            fail += 1
    print(f"完成 {ok} 个文件，失败 {fail} 个")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
