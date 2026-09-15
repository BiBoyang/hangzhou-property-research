# Step 3B Agent Handoff：数据覆盖与缺口报告

你是本项目的实现 agent。请先读取并遵守：

1. `/Users/boyang/WORKFLOW.md`
2. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-coverage.md`
3. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-query.md`

用户已批准 Step 3B 方案 A，并授权你直接实现本 Step。完成后提交 Review Package 并停止，等待 Review；不要进入 Step 3C/3D/3E。

## 目标

在现有 `query_metric_series()` 之上增加数据覆盖与缺口摘要，让系统说明：

- 城市、指标、机构和来源文件覆盖了哪些时期；
- 哪些时期缺失；
- 哪些序列是完整、部分覆盖、单点、只有预测或存在冲突；
- 当前哪些数据不足以支持后续分析。

本 Step 只描述数据存在性和完整性，不判断上涨/下跌、市场阶段、风险等级或购房时机。

## 允许修改的文件

- `/Users/boyang/Desktop/房地产研报/rag-app/scripts/lib_metrics_query.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/scripts/coverage_report.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_metrics_query.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_coverage_report.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/README.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-coverage.md`

如需修改其他文件，先停止并报告绝对路径、原因和影响。

## 硬边界

- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/data/rag.db`。
- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/metrics/*.jsonl` 或 `*.csv`。
- 不修改 PDF、Markdown、Web 页面或模板。
- 不运行 `ingest.py`、`build_index.py`、`load_external_metrics.py`、`run_eval.py`。
- 不重新解析、切片、抽取、embedding 或重建索引。
- 不调用外部 LLM、embedding、reranker 或任何付费 API；本轮 API 调用必须为 0。
- 所有数据库写入测试使用临时目录；正式库只允许只读查询。
- 不做插值、前值填充、后值填充或填 0。
- 不合并不同来源，不求平均，不选择“真实来源”。
- 不把预测当作实际观测。
- 不把空城市归为杭州或全国。
- 不修改 `query_metrics()` 的既有行为。

## 实现要求

1. 增加覆盖摘要函数，优先在 `lib_metrics_query.py` 中实现，并直接复用 `query_metric_series()`；不要维护第二套 SQL、时期解析或来源分组逻辑。
2. 增加只读 CLI `/Users/boyang/Desktop/房地产研报/rag-app/scripts/coverage_report.py`，支持全库、`--city`、`--metric`、`--institution`、`--source-file`、时期列表/范围、`--actual-only`、`--db` 和 `--report` 等必要参数。
3. 使用 Step 3A 的只读连接，不调用会建表、迁移或 commit 的 `lib_db.connect()`。
4. SQL 必须参数化；查询结束必须关闭连接；报告路径不能覆盖数据库或写入 metrics 源目录。
5. 每条覆盖序列保留：city/city_label、metric_name、institution、source_file、segment、unit、comparison_type、is_forecast、period_granularity、观测起止期、观测期列表、请求期、覆盖数、缺失期、覆盖率、内部缺口、冲突期、状态和 flags。
6. 有明确请求窗口时支持：`full_coverage`、`partial_coverage`、`single_point`、`forecast_only`、`no_observation`；冲突使用 `has_conflicts`/`status_flags` 标记，冲突期不算缺失。
7. 没有明确窗口时支持：`single_point`、`multi_point_no_internal_gap`、`multi_point_with_internal_gaps`、`forecast_only`、`no_observation`；不能把已有最小/最大期自动当成完整请求窗口。
8. 输出序列层、城市-指标汇总层、全局汇总层；“缺失最多”只表示覆盖不足，不代表风险最大。
9. actual 与 forecast 分开统计；只有预测的序列标记 `forecast_only`。
10. 空城市单独显示为“未归一化城市”，全库报告需要提示其存在。
11. 同一序列同一期多值不得丢弃，必须保留冲突信息并给 warning。

## 测试最低要求

使用 `/Users/boyang/Desktop/房地产研报/rag-app/.venv/bin/python`，临时数据库覆盖：

- 全库、杭州和指标筛选；
- institution/source_file 筛选；
- full/partial/single/forecast_only/no_observation/conflicted；
- 明确窗口缺失期和无窗口内部缺口；
- 月/季/半年粒度隔离；
- actual-only；
- 空城市；
- SQL 注入；
- 旧 schema 无 source_file；
- 只读连接和连接关闭；
- CLI 参数、项目外 cwd、报告路径保护和 JSON 序列化；
- 正式库只读查询及指纹不变；
- Step 3A 原有 135 项测试不回归。

运行：

```text
cd /Users/boyang/Desktop/房地产研报/rag-app
.venv/bin/python -m unittest discover -s tests -v
```

不要把未执行的检查写成通过。提交 Review Package 时必须列出：修改文件、接口与状态定义、CLI 用法、测试实际结果、正式库只读证据、源文件指纹、API 调用次数、已知风险和人工验收项。完成后停止，不进入后续 Step。
