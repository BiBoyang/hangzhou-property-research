# Step 3C Agent Handoff：杭州与北上深横向比较

你是 `/Users/boyang/Desktop/房地产研报/rag-app` 的实现 agent。开始前先读取并遵守：

1. `/Users/boyang/WORKFLOW.md`
2. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-city-comparison.md`（若存在）
3. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-query.md`
4. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-coverage.md`
5. `/Users/boyang/Desktop/房地产研报/rag-app/scripts/lib_metrics_query.py`
6. `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_metrics_query.py`

用户已批准 Step 3C 方案 A：在现有时间序列查询层之上增加城市横向比较，并提供只读 CLI。你可以直接实现本 Step；完成后提交 Review Package 并停止，等待 Review。不要进入 Step 3D/3E。

## 目标

增加一个本地、只读、可追溯的城市比较能力，支持杭州与上海、北京、深圳、全国之间的同指标比较。输出各城市原始点、来源、缺失期、共同覆盖期和可比性状态。

本 Step 只描述数据条件和原始差异，不判断哪个城市更强/更弱，不做趋势结论、相关性、领先滞后、价格预测、市场阶段或购房建议。

## 允许修改

- `/Users/boyang/Desktop/房地产研报/rag-app/scripts/lib_metrics_query.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/scripts/coverage_compare.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_metrics_query.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_coverage_compare.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/README.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-city-comparison.md`

需要其他文件时先停止，报告绝对路径、原因和影响。

## 硬边界

- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/data/rag.db`。
- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/metrics/*.jsonl`、`*.csv`、PDF 或 Markdown。
- 不修改 Web 页面或模板。
- 不运行 `ingest.py`、`build_index.py`、`load_external_metrics.py`、`run_eval.py`。
- 不重新解析、切片、抽取、embedding 或重建索引。
- 不调用外部 LLM、embedding、reranker 或付费 API；API 调用必须为 0。
- 所有写入测试使用临时目录；正式库只允许只读查询。
- 不平均、插值、填 0、前值填充、后值填充或跨来源替代。
- 不把预测当实际观测。
- 不把空城市归为任何城市。
- 不把全国数据冒充城市数据。
- 不修改现有 `query_metrics()` 和 `query_metric_series()` 的既有语义。

## 设计路线

采用方案 A：复用 `query_metric_series()`，不要维护第二套 SQL、时期解析、连接或来源分组逻辑。

建议增加结构化核心接口：

```text
compare_metric_across_cities(
    *, cities, metric_name, institution=None, source_file=None,
    segment=None, unit=None, comparison_type=None,
    periods=None, period_start=None, period_end=None,
    include_forecasts=False, db_path=DB
) -> dict
```

可提供很薄的自然语言适配层，但只解析城市、指标和时期，不决定来源可信度，不输出市场判断，不调用 LLM。

## 可比性规则

城市序列只有在以下字段一致时才进入同一严格比较组：

- `metric_name`
- `segment`
- `unit`
- `comparison_type`
- `is_forecast`
- `period_granularity`
- `institution` 和/或明确的来源选择
- `source_file` 和/或明确的来源选择

默认严格来源模式：不同机构或不同来源文件不静默合并。

来源不同但用户需要查看时，可以并列返回并标记：

```text
comparability = "limited"
```

limited 只能表示原始数据并列，不能计算差值、倍数、排名、方向或“谁更强”。

如果严格可比条件不满足，返回结构化状态，例如：

- `comparable`
- `limited`
- `insufficient_common_periods`
- `uncovered_city`
- `unmatched_metric`
- `conflicted`
- `mixed_granularity`

这些状态只表示数据条件，不表示城市优劣。

## 来源与分组

默认按 Step 3A 的 series 语义分组。不能只按城市和指标合并。

至少保留：

- institution
- source_file
- city / city_label
- metric_name
- segment
- unit
- comparison_type
- is_forecast
- period_granularity
- points
- missing_periods
- conflicts

同一城市、同一来源、同一时期的多个值：

- 不取平均；
- 不取第一条；
- 不覆盖；
- 不丢弃；
- 标记 `conflicted`；
- 保留全部冲突点。

## 共同覆盖时期

每个城市先独立计算：

- `observed_periods`
- `missing_periods`
- `points`
- `conflicts`

然后按比较组求交集：

```text
common_periods = city_1_periods ∩ city_2_periods ∩ ...
```

必须区分：

- 每个城市自己的覆盖期；
- 每个城市自己的缺失期；
- 所有城市共同覆盖期；
- 只被部分城市覆盖的时期。

共同覆盖期少于 2 个时：

```text
insufficient_common_periods = true
```

并加入 warning：“共同覆盖期不足以支持时间变化比较”。第一版仍可展示原始点，但不能计算涨跌幅、排名、差值、倍数、相关性或领先滞后。

## 时期粒度

复用 Step 3A 的时期规则：

- 月度、季度、半年、年度严格分开；
- 不自动转换 `2026Q1`、`2026-H1` 和 `2026-03`；
- 混合粒度请求拒绝并 warning；
- 请求时期和数据库时期格式必须一致；
- 缺失期不插值、不填 0、不用其他粒度替代。

支持明确时期列表和同粒度范围。排序使用现有稳定时期排序工具。

## 预测和实际

默认 `include_forecasts=False`。

如果显式开启：

- 实际和预测分成不同比较组；
- 预测只和预测比较；
- 实际只和实际比较；
- 组和点都保留 `is_forecast`；
- warning 明确预测不是实际观测。

## 空城市与不支持城市

- `city="杭州"` 等必须精确匹配；
- `city=""` 展示为“未归一化城市”，不纳入杭州、上海、北京、深圳或全国比较；
- 用户显式传入空城市时，返回 warning 并排除或单独标记；
- 不支持城市返回 `uncovered_city`；
- 不用全国或其他城市代替缺失城市。

## 返回结构

返回值至少包含：

```text
{
  "filters": {...},
  "comparison_groups": [
    {
      "comparison_key": {...},
      "comparability": "comparable|limited",
      "cities": [
        {
          "city": "杭州",
          "city_label": "杭州",
          "institution": "...",
          "source_file": "...",
          "segment": "...",
          "unit": "...",
          "comparison_type": "...",
          "is_forecast": false,
          "period_granularity": "month",
          "points": [...],
          "observed_periods": [...],
          "missing_periods": [...],
          "conflicts": [...]
        }
      ],
      "common_periods": [...],
      "common_period_count": 0,
      "insufficient_common_periods": false,
      "warnings": [...]
    }
  ],
  "summary": {...},
  "warnings": [...]
}
```

字段可以调整，但不能丢失城市、来源、指标、segment、unit、comparison_type、预测标记、粒度、原始点、缺失期、共同覆盖期、可比性和 warning。

## CLI

新建只读 CLI：

`/Users/boyang/Desktop/房地产研报/rag-app/scripts/coverage_compare.py`

建议支持：

```text
coverage_compare.py --cities 杭州,上海,北京,深圳 --metric secondary_volume_units
coverage_compare.py --cities 杭州,上海 --metric avg_price --period-start 2026-02 --period-end 2026-03
coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --actual-only
coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --institution 贝壳
coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --json
coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --report /absolute/path/report.json
```

要求：默认只读、参数化查询、支持项目外 cwd、报告路径不得覆盖数据库或写入 metrics 源目录、参数错误和报告写入失败返回非零、不调用 LLM。

## 数据库连接

复用 Step 3A 的只读连接；不得调用会建表、迁移或 commit 的 `lib_db.connect()`。

查询必须：

- 使用 `mode=ro`；
- 正确关闭连接；
- 使用参数化 SQL；
- 不执行写 SQL；
- 不改变正式库 schema、指标值或 source_file；
- 临时库查询后可删除或重命名。

## 测试最低要求

新建：

`/Users/boyang/Desktop/房地产研报/rag-app/tests/test_coverage_compare.py`

所有数据库测试使用 tempfile。至少覆盖：

1. 杭州、上海、北京、深圳四城市；
2. 杭州 vs 上海；
3. 杭州 vs 全国；
4. 多城市列表；
5. 同指标同单位；
6. segment 不同不合并；
7. unit 不同不合并；
8. comparison_type 不同不合并；
9. actual/forecast 不混合；
10. 同源同期多值冲突全部保留；
11. 共同覆盖期求交集；
12. 各城市 missing_periods；
13. 共同覆盖期少于 2 的 warning；
14. 月度比较；
15. 季度比较；
16. 半年比较；
17. 混合粒度拒绝；
18. 同来源严格比较；
19. 不同来源 limited 并列；
20. source_file 精确筛选；
21. institution 精确筛选；
22. 空城市排除或单独标记；
23. 不支持城市；
24. 不存在指标；
25. SQL 注入；
26. 只读连接；
27. 连接关闭；
28. 报告路径保护；
29. JSON 序列化；
30. 项目外 cwd；
31. 正式库只读冒烟；
32. 正式库和源文件指纹前后不变；
33. Step 3A 原有 156 项测试不回归。

测试不应调用 `run_eval.py` 或任何外部 API。

## 正式库只读验证

临时库测试通过后，可以对正式库只读执行：

1. 杭州、上海、北京、深圳的 `secondary_volume_units`；
2. 杭州、上海、北京、深圳的 `avg_price`；
3. 杭州、上海、北京的 `yoy_change_pct`；
4. 杭州、上海、北京、深圳的 `rent_level`。

只报告原始点、来源、缺失期、共同覆盖期和可比性，不输出城市优劣。

## DoD

- 有结构化城市比较函数；
- 有只读 CLI；
- 复用 Step 3A 查询和只读连接；
- 严格来源模式和 limited 模式语义清楚；
- 共同覆盖期使用交集；
- 各城市缺失期保留；
- 同期冲突不丢失；
- 实际和预测分开；
- 月/季/半年不混用；
- 空城市不归属；
- 不计算差值、排名、相关性或市场结论；
- SQL 参数化、连接关闭；
- 正式库和源文件未修改；
- 原有 156 项测试不回归，新测试全绿；
- 外部 API 调用 0；
- README 和任务记录同步；
- Review Package 包含修改文件、接口、CLI、测试、正式只读证据、指纹、成本和风险；
- 完成后停止，不进入 Step 3D/3E。

## 硬停止条件

遇到正式库写入、原始文件变化、需要外部 API、无法区分来源、无法保留冲突、粒度混淆、需要改清单外文件、测试失败超出范围，立即停止并报告，不自行扩大任务。
