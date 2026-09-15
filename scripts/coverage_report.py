"""Step 3B：数据覆盖与缺口报告 CLI（只读）。

在 query_metric_series() 之上输出三层覆盖摘要（序列层 / 城市-指标汇总层 /
全局汇总层）：覆盖了哪些时期、缺哪些、单点/纯预测/冲突标记。只描述数据
存在性与完整性，不判断市场方向、阶段、风险或购房时机。

只读保证：全程 mode=ro 连接（lib_metrics_query._connect_readonly），不建表、
不迁移、不 commit，不调用任何外部 API。--report 写入路径受保护：不得指向
数据库文件本体，不得写入 metrics/ 源数据目录。

用法：
  .venv/bin/python scripts/coverage_report.py                     # 全库文本摘要
  .venv/bin/python scripts/coverage_report.py --city 杭州 --metric secondary_volume_units
  .venv/bin/python scripts/coverage_report.py --period-start 2026-03 --period-end 2026-08
  .venv/bin/python scripts/coverage_report.py --periods 2026-03,2026-04,2026-05
  .venv/bin/python scripts/coverage_report.py --actual-only --json
  .venv/bin/python scripts/coverage_report.py --report plans/reports/coverage.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_metrics_query as mq  # noqa: E402
from lib_paths import DB, METRICS_DIR  # noqa: E402

STATUS_CN = {
    "full_coverage": "完整覆盖",
    "partial_coverage": "部分覆盖",
    "single_point": "单点",
    "forecast_only": "仅预测",
    "no_observation": "无观测",
    "multi_point_no_internal_gap": "多点无内部缺口",
    "multi_point_with_internal_gaps": "多点有内部缺口",
    "mixed_granularity": "粒度混合",
    "ok": "正常",
}


def _resolve_report_path(raw: str, db_path: Path) -> Path:
    """报告路径保护：拒绝覆盖数据库、拒绝写入 metrics/ 源数据目录。"""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    for protected in {Path(db_path).resolve(), DB.resolve()}:
        if p == protected or (p.exists() and protected.exists() and os.path.samefile(p, protected)):
            raise ValueError(f"报告路径不得覆盖数据库文件: {protected}")
    metrics_dir = METRICS_DIR.resolve()
    if p == metrics_dir or metrics_dir in p.parents:
        raise ValueError(f"报告路径不得写入 metrics 源数据目录: {metrics_dir}")
    if p.exists() and p.is_dir():
        raise ValueError(f"报告路径是目录而非文件: {p}")
    return p


def _fmt_status(status: str) -> str:
    return f"{STATUS_CN.get(status, status)}({status})"


def _render_text(rep: dict) -> str:
    s = rep["summary"]
    lines = [
        "数据覆盖与缺口报告（只读；仅描述数据存在性/完整性，不含市场判断）",
        f"筛选: {json.dumps(rep['filters'], ensure_ascii=False)}",
        ("请求窗口: " + f"{rep['requested_periods'][0]} ~ {rep['requested_periods'][-1]}"
         f"（{len(rep['requested_periods'])} 期）") if rep["has_explicit_window"] else "请求窗口: 无（不推断完整窗口）",
        f"总体状态: {_fmt_status(s['overall_status'])}；序列 {s['series_count']} 条"
        f"（实际观测 {s['actual_series_count']} / 预测 {s['forecast_series_count']}），"
        f"城市-指标组合 {s['city_metric_count']} 个",
    ]
    if s["status_counts"]:
        lines.append("状态分布: " + "，".join(
            f"{_fmt_status(k)} × {v}" for k, v in sorted(s["status_counts"].items())))
    if s["series_with_conflicts"]:
        lines.append(f"冲突: {s['series_with_conflicts']} 条序列存在同期多值"
                     f"（共 {s['conflict_period_count']} 个冲突期；冲突期计为已覆盖，未丢弃任何值）")
    if s["empty_city_series_count"]:
        lines.append(f"未归一化城市: {s['empty_city_series_count']} 条序列 city 为空，"
                     "已单独标记，未归入任何城市")
    if s["insufficient_series_count"]:
        lines.append(f"数据不足以支持时期对比/趋势类分析的序列: {s['insufficient_series_count']} 条"
                     "（single_point / forecast_only / no_observation）")
    if s["least_covered"]:
        lines.append("缺失最多（仅表示覆盖不足，不代表风险最大）:")
        for r in s["least_covered"]:
            lack = (f"缺失 {r['missing_count']} 期" if r["missing_count"] is not None
                    else f"内部缺口 {r['internal_gap_count']} 期")
            lines.append(f"  - {r['city_label']} {r['metric_name']} [{r['institution']}] "
                         f"{_fmt_status(r['status'])}，{lack}")
    lines.append("")
    lines.append("城市-指标汇总:")
    for g in rep["city_metric_summary"]:
        line = (f"  - {g['city_label']} {g['metric_name']}: {_fmt_status(g['status'])}，"
                f"序列 {g['series_count']} 条（实际 {g['actual_series_count']} / "
                f"预测 {g['forecast_series_count']}），"
                f"观测 {g['first_period']} ~ {g['last_period']}")
        if g["union_coverage_rate"] is not None:
            line += f"，覆盖率 {g['union_coverage_rate']:.0%}"
            if g["union_missing_periods"]:
                line += f"，缺 {len(g['union_missing_periods'])} 期"
        if g["status_flags"]:
            line += f"，标记: {','.join(g['status_flags'])}"
        lines.append(line)
    lines.append("")
    lines.append("序列明细:")
    for r in rep["series"]:
        line = (f"  - {r['city_label']} {r['metric_name']} [{r['institution']}"
                f"{('/' + r['source_file']) if r['source_file'] else ''}] "
                f"{_fmt_status(r['status'])}，观测 {r['first_period']} ~ {r['last_period']}"
                f"（{r['covered_count']} 期，{r['period_granularity']}）")
        if r["coverage_rate"] is not None:
            line += f"，覆盖率 {r['coverage_rate']:.0%}"
        if r["missing_periods"]:
            line += f"，缺失: {','.join(r['missing_periods'])}"
        if r["internal_gaps"]:
            line += f"，内部缺口: {','.join(r['internal_gaps'])}"
        if r["conflict_periods"]:
            line += f"，冲突期: {','.join(r['conflict_periods'])}"
        if r["status_flags"]:
            line += f"，标记: {','.join(r['status_flags'])}"
        lines.append(line)
    if rep["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines += [f"  - {w}" for w in rep["warnings"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="数据覆盖与缺口报告（只读）：描述数据存在性/完整性，不做市场判断")
    ap.add_argument("--city", help="城市精确匹配（如 杭州）；缺省全库")
    ap.add_argument("--metric", dest="metric_name", help="指标名精确匹配（如 secondary_volume_units）")
    ap.add_argument("--institution", help="机构精确匹配（如 贝壳）")
    ap.add_argument("--source-file", help="来源文件精确匹配（如 beike.csv）")
    ap.add_argument("--periods", help="时期列表，逗号分隔（如 2026-03,2026Q1,2026-H1 需同粒度）")
    ap.add_argument("--period-start", help="时期范围起点（需与 --period-end 同粒度）")
    ap.add_argument("--period-end", help="时期范围终点")
    ap.add_argument("--actual-only", action="store_true", help="只统计实际观测，过滤预测")
    ap.add_argument("--db", default=str(DB), help="数据库路径（默认 data/rag.db，只读打开）")
    ap.add_argument("--report", help="把 JSON 报告写入该路径（不得覆盖数据库或写入 metrics/）")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 而非文本摘要")
    args = ap.parse_args(argv)

    periods = None
    if args.periods:
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]
        if not periods:
            ap.error("--periods 解析后为空")

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.exists():
        ap.error(f"数据库不存在: {db_path}")

    report_path = None
    if args.report:
        try:
            report_path = _resolve_report_path(args.report, db_path)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2

    rep = mq.summarize_series_coverage(
        city=args.city, metric_name=args.metric_name, institution=args.institution,
        source_file=args.source_file, periods=periods,
        period_start=args.period_start, period_end=args.period_end,
        actual_only=args.actual_only, db_path=db_path,
    )

    payload = json.dumps(rep, ensure_ascii=False, indent=2)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload + "\n", encoding="utf-8")
        print(f"报告已写入: {report_path}")
    if args.json or not report_path:
        print(payload if args.json else _render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
