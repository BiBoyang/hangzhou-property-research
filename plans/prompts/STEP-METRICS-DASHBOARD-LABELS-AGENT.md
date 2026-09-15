# Agent Handoff：指标看板中文化

项目：`/Users/boyang/Desktop/房地产研报/rag-app`

开始前读取：

1. `/Users/boyang/WORKFLOW.md`
2. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-query.md`
3. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-coverage.md`
4. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-overview-dashboard.md`
5. `/Users/boyang/Desktop/房地产研报/rag-app/web/templates/metrics.html`
6. `/Users/boyang/Desktop/房地产研报/rag-app/config/metrics_schema.yaml`

用户已批准采用方案 A：只改指标看板展示层，将程序内部英文 code 翻译成中文名称、单位、市场类型和统计方式。完成后提交 Review Package 并停止。

## 目标

改善 `/metrics` 指标看板可读性，同时保持：

- 数据库字段不变；
- `/api/metrics` API 契约不变；
- 指标查询仍使用原英文 `metric_name`；
- 原有问答、总览、报告库不变；
- 未知 code 安全回退显示原值；
- 不调用 LLM 或外部 API。

页面显示中文，内部查询仍使用英文 code。

## 允许修改

- `/Users/boyang/Desktop/房地产研报/rag-app/web/templates/metrics.html`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_metrics_dashboard_labels.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/README.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-metrics-dashboard-labels.md`

如需修改其他文件，先停止并报告绝对路径、原因和影响。

## 硬边界

- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/data/rag.db`。
- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/metrics/*.jsonl`、`*.csv`、PDF、Markdown。
- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/web/app.py`。
- 不修改 `scripts/lib_metrics_query.py`、`market_phase.py`、`coverage_report.py`、`coverage_compare.py`。
- 不修改 `/api/metrics` 返回结构。
- 不运行 `ingest.py`、`build_index.py`、`load_external_metrics.py`、`run_eval.py`。
- 不重新解析、切片、抽取、embedding 或重建索引。
- 不调用外部 LLM、embedding、reranker 或任何付费 API。
- 不初始化 Git、提交或推送。

## 展示映射

在 `metrics.html` 的展示层增加稳定映射。至少覆盖：

| code | 中文名称 | 说明 |
|---|---|---|
| `secondary_home_price_index` | 二手房价格指数 | 二手住宅价格变化指数 |
| `new_home_price_index` | 新房价格指数 | 新建商品住宅价格变化指数 |
| `avg_price` | 成交均价 | 统计期内平均成交价格 |
| `mom_change_pct` | 环比涨跌幅 | 与上一个统计期相比的变化 |
| `yoy_change_pct` | 同比涨跌幅 | 与去年同期相比的变化 |
| `secondary_volume_units` | 二手房成交套数 | 统计期内二手住宅成交套数 |
| `new_home_volume_units` | 新房成交套数 | 统计期内新建商品住宅成交套数 |
| `new_home_volume_area` | 新房成交面积 | 统计期内新房成交面积 |
| `developer_sales_amount` | 房企销售额 | 房地产开发企业合同销售金额 |
| `inventory_months` | 新房库存去化周期 | 按当前销售速度消化库存所需月份 |
| `listing_units` | 挂牌量 | 正在挂牌出售的房源数量 |
| `rent_level` | 租金水平 | 单位面积或指定口径的月租金 |
| `rental_yield` | 租金回报率 | 租金收入相对房价的比例 |
| `land_transaction_value` | 土地成交金额 | 土地出让或成交金额 |
| `premium_rate_pct` | 土地溢价率 | 土地成交价相对起始价的溢价比例 |
| `mortgage_rate` | 房贷利率 | 商业住房贷款利率 |
| `provident_fund_rate` | 公积金贷款利率 | 住房公积金贷款利率 |
| `price_bottom_timing` | 房价触底时点预测 | 机构对未来价格底部时间的预测 |
| `price_change_forecast_pct` | 房价涨跌幅预测 | 机构对未来价格变化的预测 |

单位映射至少包括：

- `CNY_per_sqm` → `元/平方米`；
- `CNY_100m` → `亿元`；
- `10k_sqm` → `万平方米`；
- `units` → `套`；
- `months` → `个月`；
- `pct` → `%`；
- `index` → `指数`；
- `CNY_per_sqm_month` → `元/平方米·月`；
- `text` → `文本`。

segment 映射：

- `new_home` → `新房`；
- `secondary_home` → `二手房`；
- `land` → `土地`；
- `rental` → `租赁`；
- `macro` → `宏观`；
- `developer` → `房企`。

comparison_type 映射：

- `mom` → `环比`；
- `yoy` → `同比`。

is_forecast 映射：

- `0` → `实际观测`；
- `1` → `预测`。

内部 code 仍要保留在 tooltip、说明文字或 `data-*` 属性中，方便排查，但主界面不能只显示英文 code。

## 页面要求

- 指标下拉框显示中文名称，必要时附英文 code 小字；
- 图表图例使用中文名称/机构/来源；
- 表格的单位、市场类型、环比/同比和实际/预测使用中文；
- 表格中可以显示一列“指标 code”或在 tooltip 中显示，但不能替代中文名称；
- 给当前指标增加一句解释，例如“新房库存去化周期：按当前销售速度消化库存所需月份”；
- 未知指标、单位、segment、comparison_type 使用原值回退，不报错；
- 数据库文本进入 HTML 必须转义；
- 原有缺失点、预测样式和表格行为保持；
- 不把空城市自动写成“全国”，除非页面原有行为已明确如此；如果展示空城市，使用“未归一化城市”。

## 实现建议

可以在模板 `<script>` 中维护：

```text
METRIC_LABELS
METRIC_DESCRIPTIONS
UNIT_LABELS
SEGMENT_LABELS
COMPARISON_LABELS
```

提供小型展示函数：

```text
metricLabel(code)
metricDescription(code)
unitLabel(code)
segmentLabel(code)
comparisonLabel(code)
forecastLabel(value)
```

这些函数必须对未知值安全回退。

不要引入后端 API 字段迁移。不要用 LLM 自动翻译指标名称。

## 测试要求

新建：

`/Users/boyang/Desktop/房地产研报/rag-app/tests/test_metrics_dashboard_labels.py`

测试可以是模板契约/静态脚本测试，不需要启动正式 Web 写数据库。至少覆盖：

1. 所有主要 `metric_name` 都有中文 label；
2. `avg_price`、`secondary_volume_units`、`inventory_months` 等核心指标的中文名称正确；
3. 单位映射正确；
4. segment、comparison_type、is_forecast 映射正确；
5. 未知 code 有原值回退；
6. 指标 code 仍保留在模板的查询/数据结构中；
7. 页面不会把原英文 code 作为唯一显示名称；
8. 指标解释存在；
9. 数据库文本仍经过 `esc()`；
10. `/api/metrics` 没有被修改；
11. 正式数据库和 metrics 源文件指纹不变；
12. 原有测试不回归。

运行：

```text
cd /Users/boyang/Desktop/房地产研报/rag-app
.venv/bin/python -m unittest discover -s tests -v
```

不要把未执行的验证写成通过。

## 真人验收

启动：

```text
cd /Users/boyang/Desktop/房地产研报/rag-app
.venv/bin/python web/app.py
```

浏览器打开：

`http://127.0.0.1:8666/metrics`

通过标准：

- 指标下拉框出现“成交均价”“二手房成交套数”“库存去化周期”等中文名称；
- 选择“二手房成交套数”后图表和表格正常加载；
- 页面不再只显示 `CNY_per_sqm`、`secondary_home`、`mom` 等内部字段；
- 可以在小字、tooltip 或表格辅助列看到原始 code；
- 预测指标显示“预测”，实际数据显示“实际观测”；
- 页面没有错误。

## 成本边界

- 外部模型 API 调用：0；
- 不运行 `run_eval.py`；
- 不下载或调用外部 CDN；
- 不修改正式库或原始文件。

## Review Package

完成后提交：

1. 修改/新增文件绝对路径；
2. 映射表和回退规则；
3. 页面行为变化；
4. 测试命令、总数、通过/失败/跳过；
5. 正式数据库和源文件指纹前后；
6. API 调用次数；
7. 真人验收说明；
8. 已知风险；
9. 明确未修改 API、数据库和分析模块；
10. 完成后停止等待 Review。

## 硬停止条件

需要改 API、数据库、分析模块、原始数据、Web 其他页面、外部 API、或测试失败超出看板展示范围时，立即停止并报告。
