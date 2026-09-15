# TASK-series-coverage：数据覆盖与缺口报告

## Step 3B（2026-09-08，方案 A）：覆盖摘要层 + 只读 CLI

### 范围

在 Step 3A `query_metric_series()` 之上增加数据覆盖与缺口摘要：说明城市、指标、
机构和来源文件覆盖了哪些时期、缺哪些、哪些序列是完整/部分/单点/纯预测/有冲突、
哪些数据不足以支持后续分析。本 Step 只描述数据存在性和完整性，不判断涨跌、
市场阶段、风险等级或购房时机。不进入 3C/3D/3E。

### 修改文件

- `scripts/lib_metrics_query.py`：
  - `query_metric_series()` 的 series 输出补充 `comparison_type` 字段（分组键本
    就含它，仅透出，不改变任何既有行为与分组语义）；
  - 新增 `summarize_series_coverage()`：纯消费 `query_metric_series()` 结果，
    不维护第二套 SQL / 时期解析 / 来源分组逻辑。新增内部工具
    `_observed_periods` / `_internal_gaps` / `_coverage_series_record` /
    `_group_status` / `_city_metric_summary` / `_status_counts` 与常量
    `INSUFFICIENT_STATUSES`。
- `scripts/coverage_report.py`（新增，只读 CLI）：支持 `--city` / `--metric` /
  `--institution` / `--source-file` / `--periods` / `--period-start` + `--period-end` /
  `--actual-only` / `--db` / `--report` / `--json`；项目外 cwd 可运行（路径按
  `__file__` 推导）；`--report` 路径保护：拒绝覆盖数据库本体、拒绝写入
  `metrics/` 源目录。
- `tests/test_coverage_report.py`（新增，21 项）：筛选、五种有窗口状态 + 冲突、
  无窗口内部缺口、粒度隔离、actual-only、空城市、SQL 注入、旧 schema、只读
  连接与连接关闭、CLI（项目外 cwd / 路径保护 / JSON 序列化）、正式库只读指纹。
- `tests/test_metrics_query.py`：未改动，Step 3A 原有 135 项测试原样回归。
- `README.md`：新增"数据覆盖与缺口报告（Step 3B）"一节。
- `plans/TASK-series-coverage.md`：本文件。

### 状态与输出契约

- 序列层每条记录：city/city_label、metric_name、institution、source_file、
  segment、unit、comparison_type、is_forecast、period_granularity、
  first_period/last_period、observed_periods、requested_periods、covered_count、
  missing_periods、coverage_rate、internal_gaps、conflict_periods、status、
  status_flags。
- 有明确窗口：`full_coverage` / `partial_coverage` / `single_point`（仅覆盖 1 期，
  优先于 full）/ `forecast_only`（预测不当实际观测，满窗也不叫 full）/
  `no_observation`（防御性兜底；底层 SQL 过滤空组，序列层正常不可达，全局
  `overall_status` 在零序列时使用）。
- 无窗口：`single_point` / `multi_point_no_internal_gap` /
  `multi_point_with_internal_gaps` / `forecast_only` / `no_observation`；不把已有
  最小/最大期当成完整请求窗口（`coverage_rate=None`、`missing_periods=[]`、绝不
  给 full_coverage）。粒度不可识别时 `internal_gap_check_skipped` 状态位，不谎报
  无缺口。城市-指标汇总层粒度混合时给 `mixed_granularity`。
- 冲突期计为已覆盖、不算缺失，`has_conflicts` 状态位 + warning，所有值保留。
- actual 与 forecast 分开统计；`actual_only=True` 映射到底层
  `include_forecasts=False`（预测在 SQL 层过滤并带 warning）。
- 空城市单独标记"未归一化城市"，全库报告 warning 提示其存在。
- 全局 `least_covered`（最多 5 条）仅表示覆盖不足，不代表风险最大；
  `insufficient_series_count`（single_point/forecast_only/no_observation）表示
  不足以支持时期对比/趋势类分析——仅陈述数据完整性。

### 验证

- 测试：`.venv/bin/python -m unittest discover -s tests`，156 项全绿
  （0 失败 / 0 错误 / 0 跳过）：Step 3A 及之前 135 项原样回归 + 新增 21 项。
- 正式库只读：`summarize_series_coverage(city="杭州")` 与
  `coverage_report.py --city 杭州 --json`（cwd=/tmp）均可运行；杭州二手房成交
  全库报告 6 条 series（1 无内部缺口 / 3 有内部缺口 / 2 单点），粒度混合的
  城市-指标组标 `mixed_granularity`。
- 指纹：data/rag.db size 23244800 / mtime 1788821905.4951766 /
  sha256 1e46e016…094a19fb 前后不变；无 WAL/SHM；metrics/ 61 文件聚合 hash
  89d2b0b4…2607cd 前后不变（与 3A 记录值 b3ed8d31… 不同系 3A 之后 metrics/
  内容已有变化，本轮前后比对一致即可）；正式写入 0；外部 API 调用 0；
  未运行 ingest / build_index / load_external_metrics / run_eval。

### 已知边界（留给后续 Step）

- 覆盖摘要只回答"有什么、缺什么"，不回答"意味着啥"：方向一致/趋势改善/
  市场企稳判断属 3D，购房建议不做。
- 无窗口时 `least_covered` 按内部缺口数排序，单点序列不参与（缺口无从谈起）；
  有窗口时按缺失期数排序。
- 旧 schema 下 source_file 为 None，覆盖粒度相应降一档（机构级分组）。
