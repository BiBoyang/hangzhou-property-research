# TASK-agentic-rag:单引擎双配置 Agentic RAG——M5 已完成(收官)

**状态**:M5 已完成(2026-10-16)——全部里程碑关闭。本文件是唯一事实来源。

## 需求规格(用户确认)

- **R1 双能力**:查得准(自动路由文档库/指标库/观点库)+ 查得深(复杂问题自动拆解、多步检索、交叉验证)
- **R2 判断型回答**:每个答案四段式——我的判断 / 哪些机构支持(带证据)/ 哪些机构不同(带证据)/ 我为什么这么选
- **R3 多轮对话**:session 记忆、追问、指代消解
- **R4 主动结构化研判**:模板化研究报告输出(结论/数据支撑/机构观点矩阵/分歧分析/风险)

## 已拍板决策(2026-10-16)

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 架构 | **手写 ReAct 循环**,不上 LangGraph |
| 2 | 每问 LLM 调用上限 | **4 次**(成本质量平衡;深度模式不够用再调) |
| 3 | 双 agent 形态 | **单引擎双配置**(房地产 agent = 同循环 + topic 过滤 + 专用 prompt;全量 agent = 无过滤) |
| 4 | planner 模型 | **已定(2026-10-16):Kimi 按量计费 key 已写入 ~/.zshrc(`LLM_API_KEY`),模型 `kimi-k2.6`(lib_llm 默认即此,零改动;`LLM_MODEL` 可切换)。/models 已验证该 key 可用 |
| 5 | claim 观点层 | 按推荐 **A:开发期抽取**(用户未显式否决;M0 spike 先验证质量,不过关则回炉) |
| 11 | M5 拍板(2026-10-16) | 轻量 eval 纯规则评分(项目内,回归用);**重度 eval 用 YanbaoEval**(裁判模型处理数字等价/幻觉检测);题目 AI 出;baseline 思考关 |
| 10 | M4 拍板(2026-10-16) | **分节多调用**(复用 M2 循环,不搞单次长 prompt);观点矩阵**规则渲染**;输出落 `plans/reports/研判-*.md`;web 界面不在 M4 |
| 9 | M3 拍板(2026-10-16) | 改写用 kimi-k2.6 思考关(~1 分钱/轮);历史上下文**规则拼装**(非 LLM 滚动摘要:零成本无失真);改写失败兜底=原问题直进循环 |
| 8 | M2 拍板(2026-10-16) | 原生 **tools API**(不退化文本 JSON);**首轮必查**(tool_choice=required)+之后自足判断(min_tool_rounds 可调 2 = 固定两轮保底);thinking 默认关;M2 不动 web 界面 |
| 7 | M1 抽取引擎(2026-10-16) | **deepseek-flash 非思考**(SPIKE_EFFORT=none);kimi-k2.6 思考关质量降级出局;回退档:kimi 思考开(~85 元,人审发现系统性极性错误时启用) |
| 6 | M0 执行拍板(2026-10-16) | topic **多标签**(JSON array,宏观涉房文档可带房地产标签);spike **走 API 脚本**(= M1 生产方式;本地模型否掉:极性判定是已知最弱环+全量成本个位数元+隐私无增量+Ollama 已损坏);样本由 AI 自选 |

## 架构

```
数据底座(已完成): 228 文档 / 5116 chunks / FTS5+向量 / 指标层
        │
三个查询层: search_docs(topic/date/机构过滤) · query_metrics · query_claims(新建) · read_doc
        │
agent 运行时: 手写循环[拆解 → 并行检索三层 → 自足判断(可开关) → 再查 → R2 四段式合成],上限 4 次调用
        │
双配置: 房地产 agent(topic=房地产)/ 全量 agent(无过滤)
        │
输出: 带引用答案(单轮+多轮) / 结构化研判报告
```

## 成本明账(token)

| 环节 | 频率 | 量级 |
|---|---|---|
| M0 spike:claim 抽取 3 份 | 一次性 | ≤2 元 |
| M1:claim 全量抽取 228 份 | 一次性+增量 | ~500 次调用,个位数元 |
| 日常问答(深度模式) | 每问 | ≤4 次调用 |
| 结构化研判报告 | 每次 | 10-20 次调用 |
| 评测 | 每轮 | 题数 × 调用数 |

## 分阶段计划

- **M0**(✅ 2026-10-16):docs.topic 多标签规则回填完成(228 篇:房地产 71/宏观 90/消费 55/商品外汇 19/其他 33;`scripts/lib_topic.py`+`backfill_topic.py`,13 项测试);claim 抽取 prompt(`config/claim_extraction_prompt.md`)+ 3 份 spike 完成(28 条,程序校验 28/28,tokens in 5.5w/out 2.8w,粗估<1 元)→ **等人工 gate:`plans/reports/m0-claim-spike.md` + 人审稿**
- **M1**(✅ 2026-10-16):三方 A/B 定引擎(`plans/reports/m0-claim-ab.md`:kimi 思考关出局——枚举违规+硬凑看多;**ds-flash 非思考中选**,校验 0 异常、保守偏中性、最快最便宜)→ 全量 228 篇 4.9 分钟抽完,**2126 条入库 / 丢 ~200 / 失败 0**,tokens in 2.82M out 0.26M,**闲时价 ~4 元**(原估个位数元达成)。claims 表 + claims_extraction_log(幂等增量)+ `lib_claims.py`(query_claims/stance_matrix,时间轴不投票)+ `extract_claims.py`(4 并发/校验丢弃/失败不阻断)。丢弃主因=9 篇网页采集稿 PUA 字体混淆(数字是乱码,模型"读对"了但 quote 对不上原文,按完整性原则丢弃)——2 篇因此 0 claim,后续应在入库层修(重采集/字体映射)。→ **等人工 gate:`plans/reports/m1-claim-sample-review.md`(10 份分层抽样)**
- **M2**(✅ 2026-10-16):`lib_agent.py` 手写循环(原生 tools API,4 工具:search_docs/query_metrics/query_claims/read_doc;首轮 tool_choice=required 必查,末轮强制无工具合成保证 ≤4 次调用;同轮 tool_calls 并行;trace 落 `data/traces/`)+ `agent_query.py` CLI + `tests/test_agent.py` 7 项 mock 剧本单测(机制零成本验证)。真机验证(kimi-k2.6 思考关,3 题):tool_choice=required 兼容 ✓;观点类(GS 见底判断)/数据类(杭州 3 月 9356 套,自动并列大摩 8728 口径分歧)/演化类(UBS 看空→中性偏多全程时间轴,7 次工具调用)全过;每题 tokens in ~6-12k out ~0.5-1.1k ≈ 5 分钱/题
- **M3**(✅ 2026-10-16):`lib_session.py`(agent_sessions/agent_turns 两表 + 规则实体注册表[机构/城市/时期] + 最近 2 轮截断 + 早轮一行摘要,零额外 LLM 调用)+ `lib_agent.resolve_query`(改写 1 次调用不计入 4 次上限,失败退化单轮不阻断)+ CLI `--session/--last`。11 项单测;真机 2 轮验证:"那瑞银呢?"→正确展开为"瑞银(UBS)对一线城市房价何时见底的看法",answer 还抓出 bottoming≠bottomed 的措辞分寸
- **M4**(✅ 2026-10-16):`lib_report.py` 编排器(planner 拆题 1 次 → 五节并行跑 M2 循环[每节 ≤3 次调用,system_extra 注入节指令] → 合成 1 次;实测 17 次调用/份,tokens in 7.6w out 1.4w ≈ 0.9 元)+ `make_report.py` CLI;观点矩阵 stance_matrix **规则渲染**(数字不经模型手);planner/节/合成三级失败各自降级(模板兜底/占位标注/规则拼装)。坑:线程内懒加载 openai→httpx 部分初始化竞态(Python 3.13 无全局 import 锁),改顶部 eager import 修复。首份真机报告 `plans/reports/研判-20261016-上海房价是否已见底.md`;已知瑕疵:合成器自拟标题时间框偏旧("2024-2025"),内容本身引 2026 数据无妨
- **M5**(✅ 2026-10-16):`AGENTS` 双配置注册表(property/all,CLI `--agent`)+ `eval/agent_questions.yaml` 13 题(房地产 8 + 全量 5,含 2 道未覆盖题验"明说未覆盖"纪律)+ `run_agent_eval.py` 纯规则评分(四段式结构/期望词/引用格式/≤4 调用,零 LLM judge)。真机 13/13 通过(re-5 锚点勘误后),kimi 思考关,~3 元。**重度评测分工:YanbaoEval(裁判模型/Wilson 置信区间/错题归因/引用核对),集成需 agent HTTP 适配层(暴露 answer+citations+contexts)+ `yanbaoeval gen` 出题,另立任务**

## 主要风险(已记录)

1. 观点极性判定(看多/看空/中性)LLM 易错 → 枚举约束 + 强制证据摘录 + 抽样人审
2. 机构观点随时间反转 → 观点矩阵按时间轴呈现,禁止简单多数投票
3. 多轮+深度模式叠加延迟 → M2 循环内检索必须并行化
4. 自足性判断(grader)易自信过度 → 做成可关闭开关,固定 2 轮检索保底
5. **(M1 新增)** 9 篇网页采集稿数字被 PUA 字体混淆,quote 无法验源 → 相关 claim 已按完整性原则丢弃(2 篇 0 claim);根治需在入库层修采集,已记 TODO

## 关联

- 语料扩容过程存档:`../../技术笔记-研报批量入库/笔记.md`
- 既有系统:README.md(双层架构、指标层、评测集)
