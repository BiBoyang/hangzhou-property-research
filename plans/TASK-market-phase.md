# TASK-market-phase：市场阶段与风险信号

## Step 3D（2026-09-08）：规则型市场阶段判断层

### 范围

在 Step 3A `query_metric_series()`、3B `summarize_series_coverage()`、3C
`compare_metric_across_cities()` 之上，新增独立规则模块 `market_phase.py`：
对"当前实际观测"做透明、可追溯的市场阶段整理——输出阶段、支持/反向证据、
置信度、风险信号、下一步观察指标、规则版本与免责声明。阶段是规则对证据的
整理，不是事实本身，更不是投资/购房建议；不进入 3E（看板）。

### 修改文件

- `scripts/market_phase.py`（新增，约 1200 行）：`assess_market_phase()` 核心
  接口 + 只读 CLI（`--city` / `--as-of` / `--period-start`+`--period-end` /
  `--include-forecasts` / `--db` / `--json`，项目外 cwd 可运行，无任何写入
  子命令）。零外部 API（不 import requests/urllib/http/socket，有 AST 与
  socket 拦截双测试）；不维护第二套 SQL/时期解析/来源分组，全部复用
  `lib_metrics_query` 只读查询。
- `tests/test_market_phase.py`（新增，34 项）：临时库场景 + CLI + 正式库只读
  指纹。
- `README.md`：新增"市场阶段与风险信号（Step 3D）"一节。
- `plans/TASK-market-phase.md`：本文件。

### 规则版本与常量（模块常量区，单一出处）

- `RULE_VERSION = "market-phase-rules/0.1.0 (2026-09-08)"`；
- `MIN_PERIODS_FOR_CHANGE = 2`（至少 2 期才谈"变化"）；
- `MIN_PERIODS_FOR_RUN = 3`（至少 3 期才谈"连续"）；
- `NARROWING_MIN_PERIODS = 3`（三期连续收窄门槛）；
- `NON_WORSENING_MIN_PERIODS = 2`（"至少两个时期不再恶化"）；
- `RISING_RUN_FOR_RECOVERY = 3`（修复扩散要求成交连续上升期数）；
- `ADVERSE_RUN_STRONG = 3`（库存/挂牌连续上升该期数视为强反向）；
- `CORE_CITIES = 上海/北京/深圳/全国`；比较指标优先级
  `secondary_volume_units > avg_price > yoy_change_pct`；
- 阶段门评估顺序 `PHASE_GATE_ORDER`（从证据要求最严到最保守）：
  `recovery_broadening → initial_stabilization → decline_narrowing →
  bottom_fluctuation → continued_decline → data_insufficient`。
  chosen = 顺序上第一个全条件通过的门；该顺序即"条件不充分选更保守阶段"。
  六个门全部完整评估并留痕（`phase.rule_evaluation`），未满足条件逐条可见。

### 阶段门条件（全部为显式布尔条件，无权重无打分）

- `recovery_broadening`：价格改善且 ≥3 期（全部转正或保守口径连续收窄 ≥3 期）、
  成交连续上升 ≥3 期、库存或挂牌改善、背景（土地/租金）不拖后腿、核心城市
  严格可比组同向支持、价格与成交无来源冲突、无分化信号。
- `initial_stabilization`：价格 ≥2 期不再恶化（非下降链）、成交 ≥2 期不下降、
  至少一个价格子市场不恶化、≥2 个指标/来源支持（improving 或 stable）、
  库存/挂牌无连续 3 期强恶化、结构性不依赖预测（阶段仅由实际观测计算）。
- `decline_narrowing`：价格最新仍为负、连续收窄 ≥3 期、成交未连续回升
  （<3 期）、存在反向信号（成交/库存/挂牌恶化或家族 mixed 或分化）。
- `bottom_fluctuation`：价格出现改善迹象（收窄 ≥2 期或正负读数并存的局部
  转正）、信号互相拉扯（同上反向集合）、价格或成交至少一侧 ≥3 期。
- `continued_decline`：价格持续为负且无三期收窄（或仅水平序列连续下跌）、
  成交未改善、库存/挂牌无缓解、价格或成交至少一侧 ≥3 期连续实际观测。
- `data_insufficient`：兜底恒过；返回时说明缺价格/成交第二期观测、完全缺失
  的核心指标清单、冲突序列数。

### 信号设计

- 家族 × 子市场：`price_secondary_home` / `price_new_home` / `volume_secondary` /
  `volume_new_home` / `inventory_months` / `listing_units` / `land_market` /
  `rent_market` / `core_city_linkage` /（可选）`forecast_outlook`。
- 每个信号：`code/direction(improving|deteriorating|stable|mixed)/strength/
  status(available|unavailable|conflicted|insufficient)/metric_name/segment/
  periods/evidence(原始点: period/value/institution/source_file/source_quote)/
  observations/other_granularity_observations/conflict_series/warnings`。
- 极性映射透明：价格/成交/土地涨=improving；库存/挂牌涨=deteriorating；
  租金涨=improving（租赁需求）。土地/租金信号附"不等价于房价上涨/稳定"警示。
- 变化类序列（mom/yoy 值或 comparison_value）直接按值符号与收窄链判断；
  水平序列按尾部连续上升/下降链判断；单序列事实函数 `_series_facts` 是
  所有"连续/收窄"结论的唯一出处。
- 同子市场内主序列=变化类优先 → 观测期最多 → source_key 稳定排序；其余
  来源作 observations 并列，方向冲突 → 家族 conflicted。
- 多粒度并存：只分析数据最多的粒度，其余粒度并列展示 + `granularity_mismatch`
  风险，绝不跨粒度拼接。
- 核心城市联动：复用 `compare_metric_across_cities()`，仅严格可比组
  （同机构/同来源/同口径/同粒度，共同覆盖 ≥2 期）参与方向判断；杭州+至少
  一个核心城市同向 → 联动方向；limited 组只并列 + `city_comparison_limited`。
- 预测：`include_forecasts=True` 时单独成 `forecast_outlook` 信号（family=
  forecast），带"机构预测非市场事实"警示；绝不进入阶段计算与支持/反向证据。

### 置信度（证据充分程度，非判断正确概率；只有 low/medium/high）

- high：价格+成交各 ≥2 期且各 ≥3 期、跨价格+成交 ≥2 家机构、库存或挂牌
  available、核心城市严格可比 available、零冲突。
- medium：核心条件满足且（双机构宽覆盖 或 库存/核心城市补充齐全）、冲突 ≤1。
- low：其余（含单点、预测主导、来源冲突等）。

### 风险信号（与阶段分离，无分数，不映射为购房建议）

`data_gap` / `source_conflict`（同期多值，双值全保留）/ `low_sample_size` /
`forecast_dominant` / `price_volume_divergence` / `inventory_pressure` /
`listing_pressure` / `new_secondary_divergence` / `city_comparison_limited` /
`granularity_mismatch` / `source_coverage_asymmetry`（价格与成交来源数不对称）。
每条带 trigger（指标/时期/原始值/来源）与 warning。

### 验证（2026-09-08 实测）

- 测试：`.venv/bin/python -m unittest discover -s tests`，217 项全绿
  （0 失败/0 错误/0 跳过）= Step 3C 及之前 183 项原样回归 + 新增 34 项
  （覆盖派发清单 25 类场景 + CLI 5 类 + 契约/无建议/零网络 4 项）。
- 正式库只读（cwd=/tmp）：`assess_market_phase(city="杭州")` 与 CLI 均可运行。
  实际输出：`bottom_fluctuation（底部震荡）/ confidence=low / as_of=2026-08`。
  依据可解释：价格信号 J.P. Morgan 同比（2 期）恶化 vs Morgan Stanley 同比
  （5 期）收窄 → 来源方向冲突（mixed）；二手房成交 8 期走弱；大摩同口径
  四城联动 deteriorating；土地信号存在同期多值冲突。风险：source_conflict、
  granularity_mismatch。
- 指纹：data/rag.db size 23244800 / mtime 1788821905 /
  sha256 1e46e016…094a19fb 前后不变；无 WAL/SHM；metrics/ 61 文件聚合 hash
  89d2b0b4…2607cd 前后不变；正式写入 0；外部 API 调用 0（socket 拦截 +
  AST import 扫描双测试）；未运行 ingest / build_index /
  load_external_metrics / run_eval。

### 已知边界（留给后续 Step）

- 规则 v0.1.0 阈值为首版拍板值，未做敏感性标定；调整需升版本号并同步本文件。
- 价格"持续负向"在仅有水平序列（无 mom/yoy）时以连续下跌链近似，粒度较粗。
- 库存/挂牌压力只用方向判断，未接入绝对水平（如去化 18 个月 vs 6 个月）；
  绝对阈值需先解决口径归一（各机构库存定义不同），留给后续。
- `bottom_fluctuation` 的"改善迹象"允许正负读数并存（局部转正），比严格
  "连续收窄"宽松，属有意保守设计（避免把冲突数据判成 data_insufficient）。
- 3E 看板未做。

### Review 修复（2026-09-08）

- 修复 `as_of` 历史快照边界：coverage、预测前瞻和核心城市联动均使用同一截止期，
  `filters.effective_period_end` 记录实际生效的截止期；请求窗口晚于 `as_of` 时按快照
  截断，窗口早于截止期时不使用未来数据。
- 核心城市联动在截断后的城市点上重新计算 `common_periods`，证据保留原始
  `source_quote`；预测 evidence 和 periods 同样受 `as_of` 约束。
- 新增 `as_of` 端到端回归测试；未修改正式数据库、原始文件或后续 Step。
