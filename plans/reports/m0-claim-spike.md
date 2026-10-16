# M0 claim 抽取 spike 报告(2026-10-16)

**结论:建议通过人工 gate 候选**——程序校验 28/28 通过,抽审肉眼质量良好,详见人审稿逐条核对。

## 执行事实

- 样本 3 份:GS&Citi《Positioning ahead of Tier-1 cities turnaround》(en,观点型) / MS《An Inflection or Another False Start》(en,判断型) / 中指院《百城价格指数 2026年3月》(zh,数据型)
- 模型 kimi-k2.6(thinking 默认),单次调用/份;tokens 合计 in=55,175 / out=28,276,按刊例价粗估不足 1 元,预算 ≤2 元内
- 产出:`plans/reports/m0-claim-spike.jsonl`(28 条)+ `m0-claim-spike-review.md`(人审稿)

## 程序校验(复用 metrics 人审兜底思路)

- evidence_quote 原文子串断言:28/28 通过(归一化=去空白+剥句尾标点)
- stance/subject/segment 枚举断言:28/28 通过,无自创标签
- claim_text 非空:28/28

## 观察(好)

1. **极性判定质量高于预期**:MS 那份 15 条 stance 与原文方向全对(含"re-rating 不可持续→看空""L 型复苏→中性"这类细区分)
2. **"不硬凑"指令生效**:中指院数据型报告只抽 2 条,均为真正的判断句,没有把数据陈述包装成观点
3. **horizon 提取准确**:"by 4Q26""2026E-28E"均正确转为"2026年四季度""2026E-2028E"
4. 评级类观点(Buy/Neutral/Sell)正确映射看多/中性/看空

## 观察(需记录,M1 设计输入)

1. **预测缺 horizon 是常态**(9/28):评级类/条件类观点原文本无时期——prompt 已允许空串,观点矩阵按时间轴呈现时用 report_date 兜底排序即可
2. **模型会给 quote 补句尾标点**(1/28):校验器已兼容(剥尾标点),M1 沿用
3. **长文档截断**:MS 原文 44.6 万字符截到 8 万,核心观点集中在摘要/首章,实测未漏主观点;M1 全量时策略=前 8 万字符(投行研报观点前置,结论在摘要),并在 claims 表记录 truncated 标记备查
4. **机构元数据缺失**:MS 那份 docs.institution 为空,prompt 收到"未知"——M1 抽取时应从标题正则兜底机构名(file naming 惯例 `日期-机构-标题`)

## 人工 gate 清单(请你过目 `m0-claim-spike-review.md`)

- [ ] stance 与 quote 方向一致(重点抽 MS #4/#13、GS #8/#9)
- [ ] 无硬凑(中指院只抽 2 条,你认可这个克制程度吗)
- [ ] 无漏抽核心观点(对照原报告标题观点)
- [ ] 极性三枚举够用,还是需要"谨慎乐观/悲观"细分(推荐:不加,保持枚举刚性)

gate 通过 → 进 M1(claims 表结构按 spike schema 落地 + 全量 228 份);不过关 → 回炉改 prompt(few-shot 或两步法)。
