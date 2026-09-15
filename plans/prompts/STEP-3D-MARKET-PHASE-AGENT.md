# Step 3D Agent Handoff：市场阶段与风险信号

项目：`/Users/boyang/Desktop/房地产研报/rag-app`

开始前读取并遵守：

1. `/Users/boyang/WORKFLOW.md`
2. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-query.md`
3. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-coverage.md`
4. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-city-comparison.md`
5. `/Users/boyang/Desktop/房地产研报/rag-app/scripts/lib_metrics_query.py`
6. `/Users/boyang/Desktop/房地产研报/rag-app/scripts/coverage_report.py`
7. `/Users/boyang/Desktop/房地产研报/rag-app/scripts/coverage_compare.py`

用户已批准 Step 3D：采用独立规则模块，基于已有时间序列、覆盖摘要和城市比较生成市场阶段与风险信号。完成后提交 Review Package 并停止，不进入 Step 3E。

## 目标

新增本地、可解释、可追溯的市场阶段判断层，默认分析实际观测数据，输出：

- 当前阶段；
- 支持信号；
- 反向信号；
- 数据覆盖与冲突；
- 置信度（low/medium/high）；
- 风险信号；
- 下一步观察指标；
- 规则版本和免责声明。

建议阶段代码：`data_insufficient`、`continued_decline`、`decline_narrowing`、`bottom_fluctuation`、`initial_stabilization`、`recovery_broadening`。

阶段是透明规则对当前证据的整理，不是事实本身、投资建议或购房建议。

## 允许修改

- `/Users/boyang/Desktop/房地产研报/rag-app/scripts/market_phase.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_market_phase.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/README.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-market-phase.md`

如需修改其他文件，先停止并报告绝对路径、原因、影响。

## 硬边界

- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/data/rag.db`。
- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/metrics/*.jsonl`、`*.csv`、PDF、Markdown。
- 不修改 Web 页面或模板。
- 不修改 `lib_metrics_query.py`、`coverage_report.py` 或 `coverage_compare.py`，除非先报告底层接口缺口并获准。
- 不运行 `ingest.py`、`build_index.py`、`load_external_metrics.py`、`run_eval.py`。
- 不重新解析、切片、抽取、embedding 或重建索引。
- 不调用外部 LLM、embedding、reranker 或任何付费 API；API 调用必须为 0。
- 所有写入测试使用 tempfile；正式库只允许只读查询。
- 不使用黑箱模型或精确总分；不输出“风险 78 分”。
- 不让预测单独决定实际市场阶段。
- 不把单点数据包装成趋势或企稳。
- 不把一个机构观点当作市场事实。
- 不输出“应该买/不买”“杭州一定涨/跌”等购房结论。

## 数据来源与调用

优先复用：

- `query_metric_series()`；
- `summarize_series_coverage()`；
- `compare_metric_across_cities()`。

不要复制第二套 SQL、时期解析、来源分组或只读连接逻辑。默认 `include_forecasts=False`；预测可以作为独立前瞻信号展示，但不得混入实际阶段计算。

核心模块建议：

`/Users/boyang/Desktop/房地产研报/rag-app/scripts/market_phase.py`

建议接口：

```text
assess_market_phase(
    *, city="杭州", as_of=None,
    period_start=None, period_end=None,
    include_forecasts=False, db_path=DB
) -> dict
```

可提供只读 CLI，但 CLI 不是硬性要求；若提供，必须支持城市、时期范围、`--include-forecasts`、`--json`、`--db`，并支持项目外 cwd。

## 信号设计

每个信号必须包含：

- `code`；
- `direction`；
- `strength`；
- `status`（available/unavailable/conflicted/insufficient）；
- `metric_name`；
- `segment`；
- `periods`；
- 原始 evidence（period/value/institution/source_file/source_quote）；
- warnings。

支持的信号类别：

1. 价格：环比/同比仍为负、跌幅收窄、转正、新房与二手房分化；
2. 成交量：连续下降、连续上升、先升后降、来源方向冲突；
3. 库存与挂牌：去化周期上升/下降、挂牌量上升/下降；
4. 土地：成交金额、溢价率变化；只能说土地信号，不等价于房价上涨；
5. 租金：租金稳定/下降、租售比变化；不能直接等价于房价稳定；
6. 核心城市：上海、北京、深圳、全国与杭州的严格可比方向或数据不足。来源不同只能 limited 并列。

规则要求：至少 2 个时期才能谈变化；至少 3 个时期才能谈连续收窄或连续上升/下降；单点只能报告数据不足。不同来源不求平均，不静默合并。

## 阶段规则

第一版采用证据优先、保守映射：

- `data_insufficient`：核心价格/成交缺少至少两个实际时期，或关键指标大多单点、预测、冲突或 limited；
- `continued_decline`：价格持续负向，成交未恢复或继续下降，库存/挂牌没有明显缓解，至少一个核心序列有连续实际观测；
- `decline_narrowing`：价格仍负但跌幅至少三个时期连续收窄，成交尚未明确连续回升，仍有库存/挂牌/成交反向信号；
- `bottom_fluctuation`：跌幅收窄或局部转正，但成交、库存、挂牌或来源之间信号互相拉扯；
- `initial_stabilization`：价格至少两个时期不再恶化，成交至少两个时期改善或维持，新房/二手房至少一类不再恶化，至少两个指标或来源支持，不能主要依赖预测；
- `recovery_broadening`：价格、成交、库存/挂牌和背景指标均改善，杭州自身及至少一个核心城市支持，多时期持续，实际覆盖充分，冲突有限。

如果条件不充分，选择更保守阶段。不要为了覆盖所有测试而放宽证据门槛。

规则阈值、优先级和版本必须写在任务记录与模块常量中，不能隐藏在零散条件里。

## 置信度

只使用：`low`、`medium`、`high`。

依据：

- 实际可用序列数量；
- 覆盖率和时期数；
- 来源数量；
- 冲突数量；
- 核心指标是否齐全；
- 共同覆盖期数量；
- 是否依赖预测。

置信度表示证据充分程度，不是阶段判断正确概率。

## 风险信号

阶段与风险分开输出。可使用：

- `data_gap`；
- `source_conflict`；
- `low_sample_size`；
- `forecast_dominant`；
- `price_volume_divergence`；
- `inventory_pressure`；
- `listing_pressure`；
- `new_secondary_divergence`；
- `city_comparison_limited`；
- `granularity_mismatch`；
- `source_coverage_asymmetry`。

风险信号必须带触发指标、时期、原始值、来源和 warning。风险信号不转换成分数，不直接映射为买房建议。

## 返回结构

至少包含：

```text
{
  "city": "杭州",
  "as_of": "...",
  "phase": {
    "code": "decline_narrowing",
    "label": "跌幅收窄",
    "confidence": "medium",
    "basis": "rule_based",
    "rule_version": "..."
  },
  "signals": [...],
  "supporting_evidence": [...],
  "counter_evidence": [...],
  "coverage": {...},
  "risk_signals": [...],
  "watch_next": [...],
  "warnings": [...],
  "disclaimer": "阶段判断只整理当前数据证据，不替用户做购房决策"
}
```

必须保留来源、时期、数值和原文摘录。返回“数据不足”时，也要说明缺哪些关键指标和缺口在哪里。

## 测试要求

新建：

`/Users/boyang/Desktop/房地产研报/rag-app/tests/test_market_phase.py`

所有数据库测试使用 tempfile。至少覆盖：

1. 数据不足；
2. 持续下行；
3. 跌幅收窄；
4. 底部震荡；
5. 初步企稳；
6. 修复扩散；
7. 单点不生成趋势；
8. 两期不足以判断连续收窄；
9. 三期连续收窄；
10. 成交量连续下降；
11. 成交量连续上升；
12. 价格与成交量背离；
13. 库存压力；
14. 挂牌压力；
15. 新房/二手房分化；
16. 来源冲突；
17. 跨城市 limited 不进入强结论；
18. 月度/季度/半年粒度隔离；
19. 预测不主导实际阶段；
20. 置信度 low/medium/high；
21. 缺失指标风险；
22. 正式库只读；
23. 数据库指纹不变；
24. 原有 183 项测试不回归；
25. 外部 API 调用 0。

如果实现 CLI，还要测试：

- 项目外 cwd；
- JSON 序列化；
- 参数错误；
- 正式库只读；
- 不存在写入子命令。

## 验证命令

使用：

`/Users/boyang/Desktop/房地产研报/rag-app/.venv/bin/python`

运行：

```text
cd /Users/boyang/Desktop/房地产研报/rag-app
.venv/bin/python -m unittest discover -s tests -v
```

禁止运行 `ingest.py`、`build_index.py`、`load_external_metrics.py`、`run_eval.py`。

## DoD

- 独立市场阶段模块完成；
- 规则版本和阶段映射可读、可测试；
- 支持证据和反向证据均可追溯；
- 风险信号与阶段分开；
- 预测不混入实际阶段；
- 单点和缺失数据返回数据不足或低置信度；
- 不使用黑箱评分；
- 不输出买房结论；
- 正式库只读且指纹不变；
- 原有 183 项测试全部回归，新测试全绿；
- 外部 API 调用 0；
- README 和任务记录同步；
- 提交 Review Package 后停止。

## Review Package

完成后提交：

1. 修改文件绝对路径和职责；
2. 阶段规则、阈值、优先级和版本；
3. 信号和风险返回结构；
4. 实际测试命令与输出；
5. 原有 183 项是否回归；
6. 临时数据库场景与核算；
7. 正式库只读查询和 size/mtime/sha256 前后值；
8. 原始文件 hash 前后值；
9. API 调用次数；
10. 已知风险；
11. 人工验收项；
12. 明确没有进入 Step 3E。

不要把未执行的验证写成通过。完成后停止等待 Review。

## 硬停止条件

需要修改清单外文件、正式库写入、原始文件变化、外部 API、重新抽取/解析/切片/索引、来源无法区分、测试失败超出范围、或发现规则无法透明解释时，立即停止并报告。
