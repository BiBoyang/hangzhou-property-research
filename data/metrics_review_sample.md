# 指标人审抽样清单（185 条 / 全库 1477，13%）

审法：只看记录行的**那个数值**，去下面摘录行里找同一个数字+说法，找得到就勾 `- [x]`，找不到标 `❌ 哪个字段错`。
判负规则：同一文件错 ≥2 条 → 整组标「待重抽」并跳过；总体错误率 <2% 通过。
表格类摘录缺表头、拿不准列对不对齐时：去 `data/raw_md/` 里 grep 那个数字，表头就在附近（30 秒），确认不了就标「存疑」。

## external_hangzhou.csv

- [ ] **inventory_months** | 杭州 | new_home | 2026-08 | **8.5** months | - | 实际
  > 截至8月末全市新房库存23078套、332.9万㎡，12月口径去化周期8.5个月，仍处近一年高位

- [ ] **land_transaction_value** | 杭州 | land | 2026-08 | **5.5** CNY_100m | - | 实际
  > 8月杭州招拍挂涉宅用地成交2宗，成交总价5.5亿元，综合溢价率10%

## report_cn_inventory_2026.jsonl

- [ ] **new_home_volume_area** | 全国 | new_home | 2026 | **60000.0** 10k_sqm | - | 预测
  > 基于⼆⼿房需求对新房需求的替代、城镇化节奏⼤幅放缓等相对保守的假设，6亿平⽅⽶或是年化新增商品住宅需求的物理底部。

- [ ] **inventory_months** | (空) | new_home | 2028 | **5.2** months | - | 预测
  > | 2026 |                2.9 |         1.1 |          1.9 |                2.1 |      22.1 |
  > | 2027 |                2.1 |         1.1 |          1.9 |                1.3 |      13.6 |
  > | 2028 |                1.3 |         1.1 |          1.9 |                0.5 |       5.2 |

## report_cn_policy_tools_2026.jsonl

- [ ] **rent_level** | 一线 | rental | 2025Q4 | **88.2** CNY_per_sqm_month | - | 实际
  > | 房租 �     | 88.2                               | 29.2     |

- [ ] **avg_price** | 一线 | - | 2026Q1 | **25167.0** CNY_per_sqm | - | 预测
  > | 模型隐含房价   | 25,167                             | 6,308    |

## report_db_rmb_202605.jsonl

- [ ] **price_bottom_timing** | 全国 | macro | 2026-05 | **2027.0** text | - | 预测
  > An industry survey cited during our meetings found over 70% of developers and investors expect no market stabilisation before 2027.

## report_gs_cio_202604.jsonl

- [ ] **price_bottom_timing** | 全国 | macro | 2026-04 | **2026.0** text | - | 预测
  > Other topics included the outlook for the Chinese economy-risks to export growth, the possibility of the property market bottoming this year, an investment rebound-as well as prospects for macro policies such as monetary, fiscal, and consumption-related measures.

## report_gs_divergence_202606.jsonl

- [ ] **rental_yield** | (空) | rental | 2026-05 | **3.0** pct | - | 实际
  > Select Tourismand retirement-oriented projects rose 8% over the past year, underpinned by ~3% rental yields and high transaction volume

- [ ] **price_bottom_timing** | 上海 | secondary_home | 2026-06 | **2026Q4** text | - | 预测
  > For key market's recovery timeline (i.e. Shanghai), the expert expects another 6 months may be needed for price to stabilize under an optimistic case, or 6-12 months under a pessimistic case (vs. our prior discussion on SH/SZ potentially bottoming out in late 2026)

## report_gs_expert_202603.jsonl

- [ ] **inventory_months** | 全国 | new_home | 2026-01 | **35.0** months | - | 实际
  > To reduce inventory to healthy levels (12-18 months), a key goal mentioned by Ms. Qin during the call, we estimate Rmb2.1-2.9tn is needed for our sample of 80 cities (including Rmb1-1.5tn for tier-1/key tier-2 cities), which represents ~25% of 2025 nationwide sales and with median inventory months standing at 35 by the beginning of 2026. We understand that as of end-25, only Rmb25bn local government special bonds (LGSB) had been issued for completed housing units buybacks (accounting for less than 5% of total LGSB issued for property de-stocking), or implied Rmb50bn-60bn overall funding, consi

- [ ] **price_bottom_timing** | 全国 | macro | 2026-03 | **2028-2029** text | - | 预测
  > Ms. Qin expects the property market downward trend to continue in 2026 despite some recent positive development in the secondary market and her expectations of: 1) improving execution on de-stocking by government; 2) HPF (Housing Provident Fund) reforms to kick off this year; 3) accelerating urban renewal projects in the 15th Five-Year Plan (Rmb10tn). If de-stocking execution remains slow, Ms. Qin expects at least another 2-3 years of downturn before the property industry recovers on its own. In addition, she expects the property developer industry to adopt new models including introduction of

## report_gs_hk_lessons_202604.jsonl

- [ ] **yoy_change_pct** | 一线 | secondary_home | 2026Q1 | **-40.0** pct | - | 实际
  > Tier-1 cities in Mainland China remain on a downward trajectory, with cumulative price declines reaching around 40% from peak levels (Exhibit 1).

- [ ] **price_bottom_timing** | 香港 | secondary_home | 2026-04 | **mid-2025** text | - | 实际
  > Hong Kong's residential property prices appeared to have bottomed out in mid-2025 following a multi-year correction, with prices increasing by nearly 10% since 2025Q2 and transaction volume also recovering.

## report_gs_neg_equity_202602.jsonl

- [ ] **avg_price** | 北京 | secondary_home | 2025 | **37300.0** CNY_per_sqm | - | 实际
  > | Beijing   | 21.8                 | -0.1%                           | 92.5                | 4.6%                            | 37.3                     | 16.2x                  | 28.2                  |

- [ ] **avg_price** | 上海 | secondary_home | 2025 | **29000.0** CNY_per_sqm | - | 实际
  > | Shanghai  | 24.8                 | 0.0%                            | 93.1                | 4.8%                            | 29.0                     | 12.5x                  | 37.3                  |

## report_gs_q1gdp_202604.jsonl

- [ ] **yoy_change_pct** | 全国 | new_home | 2026-03 | **-17.4** pct | - | 实际
  > - New home starts: - 17.4% yoy in March, vs. - 23.1% yoy in January-February.

- [ ] **yoy_change_pct** | 全国 | new_home | 2026-02 | **-23.1** pct | - | 实际
  > - New home starts: - 17.4% yoy in March, vs. - 23.1% yoy in January-February.

## report_gs_tier1_202604.jsonl

- [ ] **price_bottom_timing** | 上海 | - | 2026Q4 | **2026Q4** text | - | 预测
  > Following HK's housing market recovery, we believe SH and SZ are likely to see broad recovery by 4Q26 , underpinned by historical lead-lag patterns, structural demand drivers, and potential rotation of high-net-worth buyer allocation back to domestic core property markets (aforementioned).

- [ ] **price_change_forecast_pct** | 上海 | - | 2028 | **15.0** pct | - | 预测
  > We now model 15% property price increase from end-25 to end-28 for both cities (vs. nationwide -3%/-3%/0% yoy), leading to an average 5%, 2% and 2% increase in 2028E core earnings, end-26E NAV, and 12-month NAV based target prices for our DP coverage universe.

## report_gs_tracker_202604.jsonl

- [ ] **yoy_change_pct** | 全国 | new_home | 2026Q1 | **-7.0** pct | - | 实际
  > Blended ASP in Mar was +1% mom and -6% yoy (vs. -6% from Dec-25 and -9% yoy n in 2M26) and 1Q26 ASP was -7% yoy.

- [ ] **yoy_change_pct** | 一线 | secondary_home | 2026Q1 | **-3.0** pct | - | 实际
  > Secondary transaction volumes in 15 major cities were +112% mom and -8% yoy in n Mar, and were -8% yoy in 1Q26 (vs. -9% yoy in 2M26 and above the high-teens % declines in prior GSe); Tier-1 cities recorded +2% yoy in Mar and -3% yoy in1QM26 (vs. -8% yoy in 2M26).

## report_gs_wrap13_202603.jsonl

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2026-03 | **-14.0** pct | - | 实际
  > March: Primary GFA sold on median was +68% mom and was -16% yoy; Secondary GFA sold on median was +74% mom n and -14% yoy.

- [ ] **inventory_months** | 全国 | new_home | 2026-03 | **29.3** months | mom | 实际
  > Inventory balance was -0.5% wow, with inventory months at 29.3 (vs. average 30.0 in Feb-26).

## report_gs_wrap32_202608.jsonl

- [ ] **yoy_change_pct** | 全国 | new_home | 2026-08 | **-1.0** pct | - | 实际
  > New homes sales volume was -22% wow and - 1% yoy, and new home search activities also declined by 0.9% wow.

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2026-08 | **1.0** pct | - | 实际
  > YTD: Primary GFA sold on average was - 11% yoy and was - 17%/-38% vs. the 2024/2023 level; secondary GFA sold on average was +1% yoy and was + 14%/+13% vs. the 2024/2023 level.

## report_jpm_bj_202608.jsonl

- [ ] **mortgage_rate** | 北京 | macro | 2026-08 | **3.05** pct | - | 实际
  > Commercial 商业房贷 | 1st home mortgage rate mortgage | 3.05% (LPR-45bps) | 3.05% (LPR-45bps)

- [ ] **rental_yield** | 北京 | rental | 2026-08 | **2.0** pct | - | 实际
  > Although Beijing's overall rental yield is still &lt;2%, rental yield in some small units (especially those in core districts) could hit &gt;3%.

## report_jpm_chartbook_202606.jsonl

- [ ] **mom_change_pct** | 上海 | new_home | 2026-03 | **0.3** pct | - | 实际
  > | Shanghai                     | 0.7%     | 0.4%     | 0.3%     | 0.4%     | 0.3%     | 0.3%     | 0.1%     | 0.2%     | 0.0%     | 0.2%     | 0.3%     | 0.4%     |

- [ ] **mom_change_pct** | 广州 | new_home | 2026-02 | **0.0** pct | - | 实际
  > | Guangzhou                    | -0.8%    | -0.5%    | -0.3%    | -0.2%    | -0.6%    | -0.8%    | -0.5%    | -0.6%    | -0.6%    | 0.0%     | 0.3%     | 0.1%     |

- [ ] **mom_change_pct** | 一线 | new_home | 2026-03 | **0.2** pct | - | 实际
  > | Tier-1 Primary (NBS)         | - 0.2%   | -0.2%    | -0.2%    | -0.2%    | -0.3%    | -0.3%    | -0.5%    | -0.3%    | -0.3%    | 0.0%     | 0.2%     | 0.1%     |

- [ ] **mom_change_pct** | 上海 | secondary_home | 2026-01 | **-0.4** pct | - | 实际
  > | Shanghai                     | -0.7%    | -0.7%    | -0.9%    | -1.0%    | - 1.0%   | -0.9%    | -0.8%    | -0.6%    | -0.4%    | 0.2%     | 0.4%     | 0.7%     |

- [ ] **mom_change_pct** | 广州 | secondary_home | 2026-03 | **0.2** pct | - | 实际
  > | Guangzhou                    | -0.8%    | -0.7%    | -1.0%    | -0.9%    | -0.8%    | -0.9%    | - 1.2%   | - 1.0%   | -0.7%    | -0.5%    | 0.2%     | 0.2%     |

- [ ] **mom_change_pct** | 广州 | secondary_home | 2026-04 | **0.2** pct | - | 实际
  > | Guangzhou                    | -0.8%    | -0.7%    | -1.0%    | -0.9%    | -0.8%    | -0.9%    | - 1.2%   | - 1.0%   | -0.7%    | -0.5%    | 0.2%     | 0.2%     |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2026-01 | **-0.6** pct | - | 实际
  > | Shenzhen                     | -0.5%    | -0.5%    | -0.9%    | -0.8%    | -1.0%    | -0.9%    | -1.0%    | -0.6%    | -0.6%    | -0.4%    | 0.4%     | 0.3%     |

- [ ] **mom_change_pct** | 上海 | secondary_home | 2026-03 | **1.0** pct | - | 实际
  > | Shanghai                     | - 1.4%   | - 1.4%   | -1.7%    | -1.7%    | - 1.5%   | -2.2%    | -2.0%    | -2.8%    | -0.5%    | 0.9%     | 1.0%     | 1.6%     |

- [ ] **secondary_volume_units** | 深圳 | secondary_home | 2026-05 | **1391.0** units | yoy | 实际
  > | 31-May-26   | 3,799          | 5,488                   | 2,694          | 1,391          | 5,408 2,694 1,391 13,3/2 30,110 13,372 | 30,110         | 19%   | 11%   | 35%   | 29%   | 19%   | 22%           | 16%                 | 18%                 | 16% 18% 26% 25% 20% 19% 26% | 25%                 | 20%                 | 19%                 |

- [ ] **listing_units** | 广州 | secondary_home | 2026-06 | **137000.0** units | - | 实际
  > | 28-Jun-26   | 120                                     | 82                                      | 137                                     | 86                                      | 425                                     | 1,395                                   | 0.2%  | -0.3% | 0.2%  | -2.9% | -0.6%     | 0.1%  |

## report_jpm_feb_202603.jsonl

- [ ] **developer_sales_amount** | 全国 | developer | 2026-02 | **930.0** CNY_100m | yoy | 实际
  > February  2026  even  recorded  the  lowest monthly sales by absolute value (Rmb93 bn) since the liquidity crisis began in 2021

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **150.0** CNY_100m | yoy | 实际
  > | 3      | 3         | 0   | CR Land                    | 华润置地   | Y    | 15                    | -13%   | 7                     | -22%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **20.0** CNY_100m | yoy | 实际
  > | 10     | 15        | 5   | Greenland Holdings         | 绿地控股   | N    | 5                     | -15%   | 2                     | -45%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **40.0** CNY_100m | yoy | 实际
  > | 13     | 9         | -4  | China Railway Construction | 中国铁建   | Y    | 4                     | -54%   | 1                     | -72%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **40.0** CNY_100m | yoy | 实际
  > | 14     | 46        | 32  | China Construction Yipin   | 中建壹品   | Y    | 4                     | 143%   | 2                     | 180%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **20.0** CNY_100m | yoy | 实际
  > | 15     | 13        | -2  | Binjiang                   | 滨江集团   | N    | 4                     | -36%   | 2                     | -36%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **40.0** CNY_100m | yoy | 实际
  > | 16     | 16        | 0   | Poly Property              | 保利置业   | Y    | 4                     | -25%   | 1                     | -29%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **10.0** CNY_100m | yoy | 实际
  > | 21     | 7         | -14 | Huafa Industrial           | 华发股份   | Y    | 3                     | -75%   | 1                     | -77%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **20.0** CNY_100m | yoy | 实际
  > | 26     | 30        | 4   | Xiamen ITG Holding         | 国贸地产   | Y    | 2                     | -24%   | 1                     | -14%       |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **100.5** CNY_100m | yoy | 实际
  > | China Resources Land          | 1109 HK   | 21,700              | 10,050                           | -14%               | -26%     | -15%                  | 2%                    | -14%              |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **129.3** CNY_100m | yoy | 实际
  > | Jinmao                        | 817 HK    | 12,930              | 5,327                            | 16%                | 21%      | -49%                  | -52%                  | -30%              |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **0.8** CNY_100m | yoy | 实际
  > | Shimao                        | 813 HK    | 1,590               | 80                               | -61%               | -96%     | -93%                  | -99%                  | -95%              |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **4.3** CNY_100m | yoy | 实际
  > | Powerlong                     | 1238 HK   | 900                 | 430                              | -31%               | -29%     | -88%                  | -88%                  | -9%               |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **2.6** CNY_100m | yoy | 实际
  > | Ronshine                      | 3301 HK   | 260                 | 42                               | -59%               | -85%     | -98%                  | -99%                  | -81%              |

## report_jpm_goldenweek_202605.jsonl

- [ ] **mom_change_pct** | 北京 | new_home | 2025-06 | **-0.3** pct | - | 实际
  > | Beijing                      | 0.1%     | -0.4%    | -0.3%    | 0.0%     | -0.4%    | 0.2%     | -0.1%    | -0.5%    | -0.4%    | -0.3%    | 0.2%     | 0.0%     |

- [ ] **mom_change_pct** | 北京 | new_home | 2025-07 | **0.0** pct | - | 实际
  > | Beijing                      | 0.1%     | -0.4%    | -0.3%    | 0.0%     | -0.4%    | 0.2%     | -0.1%    | -0.5%    | -0.4%    | -0.3%    | 0.2%     | 0.0%     |

- [ ] **mom_change_pct** | 北京 | new_home | 2026-02 | **0.2** pct | - | 实际
  > | Beijing                      | 0.1%     | -0.4%    | -0.3%    | 0.0%     | -0.4%    | 0.2%     | -0.1%    | -0.5%    | -0.4%    | -0.3%    | 0.2%     | 0.0%     |

- [ ] **mom_change_pct** | 上海 | new_home | 2026-01 | **0.0** pct | - | 实际
  > | Shanghai                     | 0.5%     | 0.7%     | 0.4%     | 0.3%     | 0.4%     | 0.3%     | 0.3%     | 0.1%     | 0.2%     | 0.0%     | 0.2%     | 0.3%     |

- [ ] **mom_change_pct** | 广州 | new_home | 2025-05 | **-0.8** pct | - | 实际
  > | Guangzhou                    | -0.2%    | -0.8%    | -0.5%    | -0.3%    | -0.2%    | -0.6%    | -0.8%    | -0.5%    | -0.6%    | -0.6%    | 0.0%     | 0.3%     |

- [ ] **mom_change_pct** | 广州 | new_home | 2026-02 | **0.0** pct | - | 实际
  > | Guangzhou                    | -0.2%    | -0.8%    | -0.5%    | -0.3%    | -0.2%    | -0.6%    | -0.8%    | -0.5%    | -0.6%    | -0.6%    | 0.0%     | 0.3%     |

- [ ] **mom_change_pct** | 深圳 | new_home | 2026-01 | **-0.4** pct | - | 实际
  > | Shenzhen                     | -0.1%    | -0.4%    | -0.6%    | -0.6%    | -0.4%    | -1.0%    | -0.7%    | -0.9%    | -0.5%    | -0.4%    | -0.3%    | 0.2%     |

- [ ] **mom_change_pct** | 深圳 | new_home | 2026-03 | **0.2** pct | - | 实际
  > | Shenzhen                     | -0.1%    | -0.4%    | -0.6%    | -0.6%    | -0.4%    | -1.0%    | -0.7%    | -0.9%    | -0.5%    | -0.4%    | -0.3%    | 0.2%     |

- [ ] **mom_change_pct** | 一线 | new_home | 2025-06 | **-0.2** pct | - | 实际
  > | Tier-1 Primary (NBS)         | 0.1%     | -0.2%    | -0.2%    | -0.2%    | -0.2%    | -0.3%    | -0.3%    | -0.5%    | -0.3%    | -0.3%    | 0.0%     | 0.2%     |

- [ ] **mom_change_pct** | 北京 | secondary_home | 2025-10 | **-1.1** pct | - | 实际
  > | Beijing                      | -0.6%    | -0.8%    | -1.0%    | -1.1%    | -1.2%    | -0.9%    | -1.1%    | -1.3%    | -1.3%    | -0.2%    | 0.3%     | 0.6%     |

- [ ] **mom_change_pct** | 上海 | secondary_home | 2025-05 | **-0.7** pct | - | 实际
  > | Shanghai                     | 0.1%     | -0.7%    | -0.7%    | -0.9%    | -1.0%    | -1.0%    | -0.9%    | -0.8%    | -0.6%    | -0.4%    | 0.2%     | 0.4%     |

- [ ] **mom_change_pct** | 广州 | secondary_home | 2025-10 | **-0.9** pct | - | 实际
  > | Guangzhou                    | 0.0%     | -0.8%    | -0.7%    | -1.0%    | -0.9%    | -0.8%    | -0.9%    | -1.2%    | -1.0%    | -0.7%    | -0.5%    | 0.2%     |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2025-05 | **-0.5** pct | - | 实际
  > | Shenzhen                     | -0.3%    | -0.5%    | -0.5%    | -0.9%    | -0.8%    | -1.0%    | -0.9%    | -1.0%    | -0.6%    | -0.6%    | -0.4%    | 0.4%     |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2025-08 | **-0.8** pct | - | 实际
  > | Shenzhen                     | -0.3%    | -0.5%    | -0.5%    | -0.9%    | -0.8%    | -1.0%    | -0.9%    | -1.0%    | -0.6%    | -0.6%    | -0.4%    | 0.4%     |

- [ ] **mom_change_pct** | 北京 | secondary_home | 2025-06 | **-1.6** pct | - | 实际
  > | Beijing                      | -0.5%    | -0.9%    | -1.6%    | -1.5%    | -1.8%    | -2.1%    | -1.9%    | -1.9%    | -1.9%    | -0.5%    | 1.2%     | 1.5%     |

- [ ] **mom_change_pct** | 北京 | secondary_home | 2025-10 | **-1.9** pct | - | 实际
  > | Beijing                      | -0.5%    | -0.9%    | -1.6%    | -1.5%    | -1.8%    | -2.1%    | -1.9%    | -1.9%    | -1.9%    | -0.5%    | 1.2%     | 1.5%     |

- [ ] **mom_change_pct** | 上海 | secondary_home | 2025-09 | **-1.5** pct | - | 实际
  > | Shanghai                     | 0.2%     | -1.4%    | -1.4%    | -1.7%    | -1.7%    | -1.5%    | -2.2%    | -2.0%    | -2.8%    | -0.5%    | 0.9%     | 1.0%     |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2026-01 | **-1.3** pct | - | 实际
  > | Shenzhen                     | -0.9%    | -1.1%    | -0.5%    | -1.1%    | -1.0%    | -1.5%    | -0.5%    | -1.0%    | -1.6%    | -1.3%    | 0.9%     | 0.1%     |

## report_jpm_iceberg_202604.jsonl

- [ ] **yoy_change_pct** | 一线 | secondary_home | 2025 | **-20.0** pct | - | 实际
  > Tier-1 cities saw more severe total price index declines in 2025, nearly 20%, as homes in tier-1 cities have stronger financial attributes.

- [ ] **mom_change_pct** | 上海 | secondary_home | 2026-02 | **0.0** pct | - | 实际
  > | Total | 100.0% | 3.0% | 0.0% | -2.4% | -8.4% | -13.8% | -35.0% | -16.9% | 10.4% | Note: As of 28 February 2026

- [ ] **yoy_change_pct** | 上海 | secondary_home | 2026-02 | **-13.8** pct | - | 实际
  > | Total | 100.0% | 3.0% | 0.0% | -2.4% | -8.4% | -13.8% | -35.0% | -16.9% | 10.4% | Note: As of 28 February 2026

## report_jpm_iceberg_202607.jsonl

- [ ] **price_change_forecast_pct** | 全国 | secondary_home | 2026-H2 | **-9.0** pct | - | 预测
  > While the correction may continue, the expert expects &lt;9% downside (i.e., unlikely to fall below the level in 2014/15).

- [ ] **price_bottom_timing** | 北京 | secondary_home | 2026-07 | **2026-H2** text | - | 预测
  > Shenzhen (2026 forecast: down 3-4%), Beijing/Guangzhou (2026 forecast: down 4-5%) and most core cities (2026 forecast: down 5-6%) may potentially bottom in late 2026 and stabilize in 2027.

- [ ] **mom_change_pct** | 上海 | secondary_home | 2026-07 | **0.4** pct | - | 实际
  > | 7-10mn                    | 0.1%       | 0.4%       | 0.1%       | -0.6%      | -6.9%      | -23.3%     |

- [ ] **yoy_change_pct** | 上海 | secondary_home | 2026-07 | **-5.2** pct | - | 实际
  > | 15-30mn                   | 0.0%       | -0.1%      | 0.3%       | -0.3%      | -5.2%      | -26.4%     |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2026-07 | **-0.4** pct | - | 实际
  > | 5-7mn                     | -0.1%      | -0.4%      | -0.3%      | -0.6%      | -6.0%      | -18.0%     |

## report_jpm_jan_202602.jsonl

- [ ] **mom_change_pct** | 一线 | secondary_home | 2025-12 | **-1.26** pct | - | 实际
  > Per  CREIS, the 100-city secondary home  price  M/M  decline  narrowed  from  -0.97%  in  December  to  -0.85%  in  January  (tier-1  cities  only:  narrowed  from  -1.26%  in  December  to  -1.13%  in  January)  (Figure  5).

- [ ] **yoy_change_pct** | 全国 | developer | 2026-01 | **-21.0** pct | - | 实际
  > Top  100  developers'  sales  in  January also  remained  soft,  with  a  21%  Y/Y  decline  (report).

## report_jpm_kshape_202606.jsonl

- [ ] **yoy_change_pct** | 深圳 | new_home | 2026-05 | **-21.0** pct | yoy | 实际
  > Secondary transactions have outpaced primary home sales (38K/15K units in 2025/5M26, -21%/-21% yoy).

- [ ] **price_bottom_timing** | 深圳 | secondary_home | 2026-06 | **2026-01** text | - | 预测
  > However, it has recovered by &gt;2% since Jan-26, indicating a price bottom.

## report_jpm_summit_202605.jsonl

- [ ] **price_change_forecast_pct** | 全国 | secondary_home | 2026 | **-9.0** pct | - | 预测
  > The home price index may still see a 9% downside, if we benchmark to the bottom in 2015.

- [ ] **rental_yield** | 杭州 | rental | 2026-05 | **2.2** pct | - | 实际
  > The city average is 2.2%

## report_jpm_tier1_202604.jsonl

- [ ] **mom_change_pct** | 广州 | secondary_home | 2026-03 | **0.2** pct | - | 实际
  > | Guangzhou                    | 0.0%     | -0.8%    | -0.7%    | -1.0%    | -0.9%    | -0.8%    | -0.9%    | -1.2%    | -1.0%    | -0.7%    | -0.5%    | 0.2%     |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2026-03 | **0.4** pct | - | 实际
  > | Shenzhen                     | -0.3%    | -0.5%    | -0.5%    | -0.9%    | -0.8%    | -1.0%    | -0.9%    | -1.0%    | -0.6%    | -0.6%    | -0.4%    | 0.4%     |

- [ ] **mom_change_pct** | 北京 | secondary_home | 2026-03 | **1.5** pct | - | 实际
  > | Beijing                      | -0.5%    | -0.9%    | -1.6%    | -1.5%    | -1.8%    | -2.1%    | -1.9%    | -1.9%    | -1.9%    | -0.5%    | 1.2%     | 1.5%     |

- [ ] **yoy_change_pct** | 一线 | new_home | 2026Q1 | **-0.1** pct | yoy | 实际
  > | Tier-1 average        | 4.6%   | 2.6%   | -0.2%  | -4.0%  | -1.7%   | -0.1%      | -7.0%      |

- [ ] **yoy_change_pct** | 一线 | secondary_home | 2026Q1 | **-0.2** pct | yoy | 实际
  > | Tier-1 average        | 5.4%   | 0.5%   | -3.4%  | -6.8%  | -6.9%   | -0.2%      | -17.7%     |

- [ ] **price_change_forecast_pct** | 全国 | macro | 2026-03 | **-5.0** pct | - | 预测
  > we expect most other key market indicators to remain on a downtrend in FY26E (home prices in non-tier 1 cities: down 5%; primary sales value: down 7%; new starts: down 10%)

## report_jpm_weakness_202606.jsonl

- [ ] **mom_change_pct** | 一线 | secondary_home | 2026-05 | **0.3** pct | - | 实际
  > | Tier-1 Secondary (NBS)       | -0.7% | -0.7%                  | -1.0%  | -1.0% -0.9% -0.9% |          |             | -1.1%    | -0.9%  | -0.5%       | -0.1%     | 0.4% | 0.4%  | 0.3%                                        |

- [ ] **yoy_change_pct** | (空) | secondary_home | 2026-06 | **1.0** pct | - | 实际
  > As of 21 June, 9-city (excluding Shanghai as data is not yet available as of the time of writing) real-time weekly secondary sales marginally rose 1% Y/Y (down from +12% Y/Y)

## report_ms_inflection_202605.jsonl

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2026-02 | **1.0** pct | - | 实际
  > Secondary home sales volume unexpectedly rebounded 17% y-y in March (range from - 25% to +90%)   in terms of real-time transactions in the 25 major cities we track (vs. +1% yy in 2M26). The rebound continued into April, with   faster growth of 30% y-y (range from - 15% to +74%) on a low base, pushing YTD growth to 14% y-y in 4M26 (range from -22% to +61%). This strong performance has led to mild m-m home price increases in Shanghai and Beijing (cumulatively up 1-2% in March to April), and a softer m-m home price decline in a number of Tier 2 cities.

- [ ] **yoy_change_pct** | 全国 | new_home | 2026-03 | **-13.1** pct | - | 实际
  > On the other hand, we expect primary home sales volume to remain negative y-y in the coming quarters due to reduced saleable resources at developers, although a favorable base may narrow the YTD decline to high-single-digit %, versus -13.1% y-y in 3M26. Adding primary and secondary sales together, full-year home sales volume could be largely flat yy in volume terms, which is 3-4 ppt better than our previous forecast.

- [ ] **yoy_change_pct** | 全国 | rental | 2025 | **-3.8** pct | - | 实际
  > Bingshan data show that housing rental rates in 76 major cities have cumulatively dropped 15% since July 2021 amid the housing downtrend, including -3.1% y-y in 2022, -1.8% in 2023, -4.9% in 2024, and -3.8% in 2025. Compared with low-tier cities, which have minimal rental demand, high-tier cities have experienced an even deeper pullback, including -25% in Hangzhou and Nanjing, -23% in Zhengzhou, -20% in Wuhan, -17% in Chengdu, and -16% in Shanghai and Shenzhen. Given that home prices have retreated much faster over the period, with a cumulative decline of &gt;40%, the average rental yield in t

- [ ] **rental_yield** | 全国 | rental | 2026-04 | **2.7** pct | - | 实际
  > Bingshan data show that housing rental rates in 76 major cities have cumulatively dropped 15% since July 2021 amid the housing downtrend, including -3.1% y-y in 2022, -1.8% in 2023, -4.9% in 2024, and -3.8% in 2025. Compared with low-tier cities, which have minimal rental demand, high-tier cities have experienced an even deeper pullback, including -25% in Hangzhou and Nanjing, -23% in Zhengzhou, -20% in Wuhan, -17% in Chengdu, and -16% in Shanghai and Shenzhen. Given that home prices have retreated much faster over the period, with a cumulative decline of &gt;40%, the average rental yield in t

- [ ] **mom_change_pct** | 北京 | rental | 2026-02 | **1.9** pct | - | 实际
  > While the YTD decline softened to -0.2% in 4M26, and select Tier 1 cities recorded positive growth partially driven by post-CNY seasonality, including +1.7% in Beijing and Shanghai and +0.3% in Shenzhen, we note that cities in an uptrend have been softening quickly, while cities in a downtrend have been re-accelerating since March. For example, rental rates in Beijing grew 0.1% m-m in April, versus +1.9% in February and +0.7% in March; grew 0.3% m-m in Shanghai, versus +1.0% in both February and March; fell 1.2% mm in Shenzhen, versus +0.3% in February and +1.0% in March; and fell 0.4% m-m in

- [ ] **mom_change_pct** | 深圳 | rental | 2026-02 | **0.3** pct | - | 实际
  > While the YTD decline softened to -0.2% in 4M26, and select Tier 1 cities recorded positive growth partially driven by post-CNY seasonality, including +1.7% in Beijing and Shanghai and +0.3% in Shenzhen, we note that cities in an uptrend have been softening quickly, while cities in a downtrend have been re-accelerating since March. For example, rental rates in Beijing grew 0.1% m-m in April, versus +1.9% in February and +0.7% in March; grew 0.3% m-m in Shanghai, versus +1.0% in both February and March; fell 1.2% mm in Shenzhen, versus +0.3% in February and +1.0% in March; and fell 0.4% m-m in

- [ ] **inventory_months** | 一线 | new_home | 2026-03 | **23.0** months | - | 实际
  > CRIC data show that primary inventory in 70 major cities reached record-high levels across all city tiers at 32 months in March 2026, including 23 months in Tier 1 cities, 30 months in Tier 2 cities, and 42 months in Tier 3 cities, versus historical averages of 14, 15, and 18 months, respectively. This is consistent with our estimate that the national primary inventory level was ~40 months in 2025, given that low-tier cities tend to have more challenging oversupply issues.

- [ ] **inventory_months** | 全国 | new_home | 2025-H1 | **35.3** months | - | 实际
  > | Inventory month (completed + under-constructions)              | (month)  | 36.3   | 35.3   | 38.7   |

- [ ] **yoy_change_pct** | 全国 | macro | 2026 | **0.0** pct | - | 预测
  > Sales durability is a concern: While the recent strong secondary sales rebound may suggest the worst has passed in select high-tier cities,   limited improvement in macro and housing indicators, divergent trends between new and secondary homes, and still-fragile home-buyer sentiment lead us to doubt the sustainability of the rebound, which will hinge on replacement demand from home sellers. Cities with recent easing may stay robust in 2Q, but growth in other cities has shown signs of rapid deceleration in the past few weeks. We lift our full-year total sales volume forecast by 3-4ppt to flat y

- [ ] **price_change_forecast_pct** | 一线 | macro | 2026 | **-3.0** pct | - | 预测
  > Adequate credit and policy achieves a normalized property market:  The central government aims to build a stable property market to support GDP growth but avoid a further property price rally. Recently, some local governments relaxed HPR, downpayment ratios and mortgage rates. Our base case factors in -3%, -3% and -5% yy growth in property prices from the end2025 level in tier 1, 2 and lower-tier cities, respectively.

- [ ] **yoy_change_pct** | 上海 | secondary_home | 2026-01 | **64.0** pct | - | 实际
  > Jan-26  66%  64%  78%  66%  37%  37%  78%  103%  123%  93%  43%  79%  90%  83%  31%  79%  88%  68%  47%  119%  106%  97%  102%  68%

- [ ] **yoy_change_pct** | 上海 | secondary_home | 2026-03 | **15.0** pct | - | 实际
  > Mar-26  13%  15%  -6%  2%  8%  13%  30%  25%  17%  32%  5%  26%  15%  31%  -25%  28%  39%  19%  10%  52%  24%  45%  35%  17%

- [ ] **yoy_change_pct** | 深圳 | secondary_home | 2026-03 | **-6.0** pct | - | 实际
  > Mar-26  13%  15%  -6%  2%  8%  13%  30%  25%  17%  32%  5%  26%  15%  31%  -25%  28%  39%  19%  10%  52%  24%  45%  35%  17%

- [ ] **yoy_change_pct** | 杭州 | secondary_home | 2026-04 | **-15.0** pct | - | 实际
  > Apr-26  30%  25%  22%  22%  15%  27%  44%  54%  74%  45%  13%  39%  33%  46%  -15%  60%  57%  39%  26%  70%  41%  47%  26%  30%

- [ ] **developer_sales_amount** | 全国 | developer | 2026-02 | **780.0** CNY_100m | - | 实际
  > | Aggregate              |                |                                  |         |         | 216     |         | 146     |         | 165     |         |         |         | 78      | 173     | 169     | 3%       | -2%     | 623     | 521              | -16% |          |

- [ ] **developer_sales_amount** | 全国 | developer | 2026-04 | **1590.0** CNY_100m | yoy | 实际
  > | Top 30 (attributable)  |                |                                  | 155     | 170     | 195     | 114     | 119     | 140     | 156     | 140     | 209     | 96      | 50      | 181     | 159     | 2% -12%  |         | 580     | 486              | -16% |          |

- [ ] **mom_change_pct** | 香港 | secondary_home | 2023-09 | **-2.6** pct | - | 实际
  > | Sep-23        | -33%      | -1%        | -22%                 | -33% -26%            | -14%             | -2.6%   | 6.7%       | 2.8%               | 4.13%                             | 2.8%                              | 3%                                | 0.7%                          | 0.44%                         | 67.7%                         | 50%              | 59.9%            |

- [ ] **yoy_change_pct** | 香港 | secondary_home | 2023-12 | **288.0** pct | - | 实际
  > | Dec-23        | 288%      | -11%       | -13%                 | -10% 17%             | -11%             | -2.5%   | 8.5%       | 4.0%               | 4.13%                             | 2.9%                              | -14%                              | 0.7%                          | 0.44%                         | 67.7%                         | 50%              | 56.0%            |

- [ ] **yoy_change_pct** | 香港 | secondary_home | 2024-10 | **353.0** pct | - | 实际
  > | Oct-24        | 353%      | 20%        | 75%                  | 16% 121%             | 19%              | 0.7%    | 5.9%       | 8.6%               | 3.88%                             | 3.1%                              | 19%                               | -0.4%                         | 0.49%                         | 67.0%                         | 51%              | 52.3%            |

- [ ] **mortgage_rate** | 香港 | macro | 2024-11 | **3.63** pct | - | 实际
  > | Nov-24        | 356%      | 24%        | 90%                  | 43% 147%             | 28%              | 1.2%    | 5.9%       | 7.3%               | 3.63%                             | 3.1%                              | 14%                               | -0.4%                         | 0.49%                         | 67.0%                         | 51%              | 51.1%            |

- [ ] **mom_change_pct** | 香港 | secondary_home | 2024-12 | **-0.7** pct | - | 实际
  > | Dec-24        | -8%       | -23%       | 63%                  | -43% 40%             | -28%             | -0.7%   | 5.9%       | 7.3%               | 3.50%                             | 3.1%                              | 18%                               | -0.4%                         | 0.49%                         | 67.0%                         | 51%              | 50.3%            |

- [ ] **mortgage_rate** | 香港 | macro | 2025-01 | **3.5** pct | - | 实际
  > | Jan-25        | -23%      | 2%         | 16%                  | -28% 4%              | -3%              | 0.6%    | 4.4%       | 9.6%               | 3.50%                             | 3.1%                              | 31%                               | -0.3%                         | 0.49%                         | 67.0%                         | 51%              | 49.4%            |

- [ ] **mom_change_pct** | 香港 | secondary_home | 2025-04 | **-0.1** pct | - | 实际
  > | Apr-25        | -56%      | 72%        | -17%                 | -8% -33%             | 63%              | -0.1%   | 4.0%       | 9.3%               | 3.50%                             | 3.4%                              | 25%                               | -0.3%                         | 0.49%                         | 67.0%                         | 51%              | 48.2%            |

- [ ] **yoy_change_pct** | 香港 | secondary_home | 2025-06 | **119.0** pct | - | 实际
  > | Jun-25        | 119%      | -13%       | 32%                  | -18% 54%             | -14%             | 1.0%    | 4.0%       | 11.5%              | 2.03%                             | 3.5%                              | 36%                               | -0.3%                         | 0.49%                         | 67.0%                         | 51%              | 42.1%            |

- [ ] **mortgage_rate** | 香港 | macro | 2026-02 | **3.25** pct | - | 实际
  > |               | 185%      |            |                      | 70% 108%             | 10%              |         |            | 9.8%               | 3.25%                             | 3.8%                              | 16%                               |                               |                               | 67.0%                         |                  |                  |

## report_ms_june_202606.jsonl

- [ ] **yoy_change_pct** | (空) | developer | 2026-06 | **-13.0** pct | yoy | 实际
  > CRIC data show the sales decline for the top 100 developers widened to 13% y-y in June. Home sales may stay weak in coming months, potentially leading to faster m-m home price drops. We suggest waiting for better entry points and sticking with quality alpha plays.

- [ ] **yoy_change_pct** | (空) | developer | 2026-H1 | **-14.0** pct | yoy | 实际
  > Major developers' sales weakened, as we expected, falling 11% and 13% y-y in June for top 50 and top 100 developers on attributable basis (vs. -2% each in May). This narrowed the YTD sales declines slightly to 14% for the top 50 and 16% y-y for the top 100. For the 25 major developers we track, the sales drop narrowed to 19% y-y (vs. -26% in May), bringing the YTD sales decrease to 25% y-y.

## report_ms_mar_202604.jsonl

- [ ] **mom_change_pct** | 一线 | secondary_home | 2026-02 | **-0.1** pct | - | 实际
  > In particular, Tier-1 cities saw a rebound of +0.2% in primary and +0.4% in secondary   (vs. flat and -0.1% in February) amid strong secondary sales.

- [ ] **yoy_change_pct** | 全国 | macro | 2026-03 | **-11.3** pct | - | 实际
  > Combined with the reduced construction scale, the decline of real estate investment widened slightly to -11.3% y-y in March (vs. -11.1% in 2M26).

## report_ms_weakening_202606.jsonl

- [ ] **yoy_change_pct** | (空) | secondary_home | 2026-06 | **9.2** pct | yoy | 实际
  > 25-city secondary real-time home sales softened to 9.2% y-y MTD as of June 10 (vs. 30% in April, 26% in May) despite a lower base from Dragon Boat holiday.

- [ ] **yoy_change_pct** | (空) | secondary_home | 2026-05 | **26.0** pct | yoy | 实际
  > 25-city secondary real-time home sales softened to 9.2% y-y MTD as of June 10 (vs. 30% in April, 26% in May) despite a lower base from Dragon Boat holiday.

## report_nomura_apr_202604.jsonl

- [ ] **yoy_change_pct** | (空) | new_home | 2026-04 | **4.7** pct | - | 实际
  > On city-level data, growth in new home sales by floor space in 20 major cities improved to 4.7% y-o-y in April from -5.0% in March, while growth in existing home sales volume in a sample of 18 major cities rose to 13.9% y-o-y in April from -2.9% in March.

- [ ] **yoy_change_pct** | (空) | secondary_home | 2026-04 | **13.9** pct | - | 实际
  > On city-level data, growth in new home sales by floor space in 20 major cities improved to 4.7% y-o-y in April from -5.0% in March, while growth in existing home sales volume in a sample of 18 major cities rose to 13.9% y-o-y in April from -2.9% in March.

## report_nomura_feb_202603.jsonl

- [ ] **yoy_change_pct** | 全国 | developer | 2026-02 | **-29.9** pct | - | 实际
  > Sales growth remained deeply negative in January-February 2026, at -29.9% y-o-y and -30.5%, respectively, in volume and value terms, it barely improved from - 33.6% and -35.2% in Q4.

- [ ] **yoy_change_pct** | (空) | new_home | 2026-02 | **-24.5** pct | - | 实际
  > According to data from local housing authorities, growth of new home sales volumes in the Wind survey of 20 major cities was -24.5% y-o-y in JanuaryFebruary, little changed from -26.7% in December.

## report_nomura_gdp_202604.jsonl

- [ ] **mom_change_pct** | 北京 | secondary_home | 2026-03 | **0.6** pct | - | 实际
  > All tier-1 cities posted price gains in March: 0.6% m-o-m in Beijing, 0.4% in Shanghai, 0.2% in Guangzhou and 0.4% in Shenzhen, compared with their changes of 0.3%, 0.2%, -0.5% and -0.4% in February.

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2026-03 | **0.4** pct | - | 实际
  > All tier-1 cities posted price gains in March: 0.6% m-o-m in Beijing, 0.4% in Shanghai, 0.2% in Guangzhou and 0.4% in Shenzhen, compared with their changes of 0.3%, 0.2%, -0.5% and -0.4% in February.

## report_ubs_ai_202605.jsonl

- [ ] **price_change_forecast_pct** | 二线 | - | 2026 | **-5.0** pct | - | 预测
  > In 2026, we expect property prices in tier 1 cities to stabilize   (previously -10%) and in tier 2 cities to still decline by 5% (previously -10%).

- [ ] **price_bottom_timing** | 二线 | - | 2026 | **2026-H2** text | - | 预测
  > Among cities, we expect tier 1 cities have stabilized already, and tier 2 cities property price may stabilize in 2H26.

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2025 | **9.0** pct | - | 实际
  > | 2025  | 6,700                   | -7%                     | 786                   | 9%                    | 8,522           | -12%            | 7,333                 | -13%                  | 733                 | -9%                 | 10,006                | -4%                   | 2.3                  | 5%                   | 48%                                                                 |

- [ ] **inventory_months** | 一线 | new_home | 2026-03 | **25.2** months | - | 实际
  > Figure 21: Tier 1 cities' inventory reached 25.2 months

## report_ubs_downcycle_202603.jsonl

- [ ] **rental_yield** | 一线 | rental | 2026-03 | **2.6** pct | - | 预测
  > This implies a fair rental yield for residential property in tier 1 cities is 2.6%, vs tier 1 cities' average yield of 1.8%.

- [ ] **mom_change_pct** | 一线 | rental | 2026-01 | **-0.7** pct | - | 实际
  > Figure 2: Tier 1 city secondary rental price decrease 2.3% YoY and 0.7% MoM, as of Jan 2026

## report_ubs_gdp_202604.jsonl

- [ ] **yoy_change_pct** | 全国 | developer | 2026Q1 | **-11.2** pct | - | 实际
  > | # Real estate development FAI     | -29.4           | -11.2           | -11.1           | -11.3           |                           |                           |

- [ ] **yoy_change_pct** | 全国 | developer | 2026-02 | **-11.1** pct | - | 实际
  > | # Real estate development FAI     | -29.4           | -11.2           | -11.1           | -11.3           |                           |                           |

## report_ubs_hpf_202608.jsonl

- [ ] **yoy_change_pct** | 一线 | rental | 2026-06 | **-3.1** pct | - | 实际
  > Figure 9: Tier 1 cities rental price still declined at 3.1% YoY as of June 2026, while Shenzhen rental price was +0.5% YoY

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2026-08 | **1.4** pct | - | 实际
  > 50 cities secondary listing (for sale) growth is slowing to +1.4% YoY in Aug 2026, vs +10% in Dec 2025.

## report_ubs_rally_202601.jsonl

- [ ] **yoy_change_pct** | 一线 | rental | 2025-12 | **-2.0** pct | - | 实际
  > In the four tier 1 cities in mainland China, rental prices have been declining since June 2023 and declined by 2% YoY in Dec 2025.

## report_ubs_survey_202601.jsonl

- [ ] **mortgage_rate** | 一线 | macro | 2026-01 | **3.1** pct | - | 实际
  > 2) wide gap of 130bps between average rental yield (1.8%) and mortgage rate (3.1%) in tier 1 cities, which hinders home buyers.

- [ ] **price_change_forecast_pct** | 全国 | secondary_home | 2026 | **-10.0** pct | yoy | 预测
  > we expect two years of downcycle in 2026-27, and forecast a 10%/5% YoY decline for secondary property price and new home sales in 2026/27, respectively.

## report_ubs_survey_202606.jsonl

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2026-06 | **2.3** pct | - | 实际
  > To cross-check, secondary listings across 50 cities grew by 2.3% YoY as of June 2026, vs 10% YoY growth in Jan 2026.

- [ ] **yoy_change_pct** | 一线 | secondary_home | 2026-06 | **-9.3** pct | - | 实际
  > In particular, secondary listing in tier-1 cities fell by 9.3% YoY, vs flattish YoY in Jan 2026.

## report_ubs_top100_202603.jsonl

- [ ] **inventory_months** | 全国 | secondary_home | 2026-01 | **32.0** months | - | 实际
  > Figure 12: ... while inventory months across 80 cities remained largely flattish at around 32 months

- [ ] **yoy_change_pct** | (空) | secondary_home | 2026-01 | **-20.9** pct | - | 实际
  > Figure 13: Secondary housing index in six major cities declined by 20.9% YoY as of Jan 2026 and dropped 43% from peak

- [ ] **yoy_change_pct** | 全国 | new_home | 2026-02 | **-20.0** pct | - | 实际
  > Figure 7: 30-city primary residential sales declined by 20% YoY in Feb 2026

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **78.0** CNY_100m | yoy | 实际
  > | CMSK                          | 7.8              | -25%             | 2%               | 15.5     | 21 %      |

- [ ] **mom_change_pct** | (空) | developer | 2026-02 | **-42.0** pct | - | 实际
  > | Poly Property                 | 2.2              | -37%             | -42%             |          |           |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **7.0** CNY_100m | yoy | 实际
  > | Hopson                        | 0.7              | 76%              | 85%              |          |           |

- [ ] **mom_change_pct** | (空) | developer | 2026-02 | **-36.0** pct | - | 实际
  > | Grandjoy                      | 0.7              | - 19%            | -36%             |          |           |

- [ ] **developer_sales_amount** | (空) | developer | 2026-02 | **5.0** CNY_100m | yoy | 实际
  > | SCE                           | 0.5              | -36%             | 13% 1            |          | %         |

## report_zhongzhi_100cities_202602.jsonl

- [ ] **mom_change_pct** | 全国 | new_home | 2026-02 | **-0.04** pct | - | 实际
  > 2 ⽉， 全国 100 个城市新建住宅平均价格为 17107 元 / 平⽅⽶， 环⽐下跌 0.04% ， 同⽐上涨 2.37% 。

- [ ] **yoy_change_pct** | 一线 | secondary_home | 2026-02 | **-7.85** pct | - | 实际
  > ⼀ 线 城市⼆⼿住宅价格环⽐下跌 0.42% ，同⽐下跌 7.85% ；⼆ 线 城市环⽐下跌 0.57% ，同⽐下跌 9.44% ； 三 四 线代表 城市环⽐下跌 0.54% ，同⽐下跌 8.51% 。

- [ ] **mom_change_pct** | 杭州 | new_home | 2026-02 | **0.57** pct | - | 实际
  > | 杭州         | 0.57%  | 9.11%  |              33477 |               31000 |

- [ ] **mom_change_pct** | 北京 | secondary_home | 2026-02 | **-0.66** pct | - | 实际
  > | 北京         | -0.66% | -8.97%  |              62731 |               56369 |

- [ ] **mom_change_pct** | 广州 | secondary_home | 2026-02 | **-0.41** pct | - | 实际
  > | ⼴州         | -0.41% | -9.06%  |              33283 |               29508 |

- [ ] **mom_change_pct** | 深圳 | secondary_home | 2026-02 | **-0.18** pct | - | 实际
  > | 深圳         | -0.18% | -6.05%  |              63578 |               56584 |

## report_zhongzhi_annual_2026.jsonl

- [ ] **mortgage_rate** | 全国 | macro | 2025-12 | **3.1** pct | - | 实际
  > In December 2025, the weighted average interest rates of new corporate loans and mortgages both stood at about 3.1 percent, down 2.5 and 2.6 percentage points respectively from the second half of 2018.

- [ ] **price_bottom_timing** | 全国 | - | 2026-02 | **entered a bottoming phase** text | - | 预测
  > After more than three years of deep adjustment, the real estate market has entered a bottoming phase.

## report_zhongzhi_mar_202603.jsonl

- [ ] **yoy_change_pct** | 二线 | secondary_home | 2026-03 | **-9.17** pct | - | 实际
  > 分梯队看，二手住宅方面， 各线城市二手房价格环比跌幅均有所收 窄，3 月一线、二线及三四线城市二手住宅价格环比分别下跌 0.16%、 0.42%、 0.34%， 环比跌幅较上月分别收窄 0.26、 0.15、 0.20 个百分点， 同比分别下跌7.72%、9.17%、8.27%；从涨跌城市个数看，7 个城市

- [ ] **secondary_volume_units** | 上海 | secondary_home | 2026-03 | **30000.0** units | - | 实际
  > 核心城市中，上海、合肥二手房挂牌均价环比出现上涨。 3 月 上海 二手房价格环比上涨0.08%，自2023 年 6 月以来首次上涨， ' 沪七条 ' 新政实施一个月以来，上海成为今年 ' 小阳春 ' 行情中活跃度最高的城市， 3 月二手商品房成交超3万套，创近五年新高，3月二手房量价齐升。 合肥 3 月二手房挂牌均价环比上涨0.03%， 自2023年5月以来首次上 涨。此外， 北京 3 月二手房价格环比下跌0.31%，跌幅收窄0.35 个百 分点，二手住宅成交量截 至 30 日 已超1.8 万套，市场有所升 温 。3 月核 心城市二手房'小阳春'行情显现。

- [ ] **mom_change_pct** | 深圳 | rental | 2026-03 | **0.41** pct | - | 实际
  > | 深圳   | 0.41%↑  |            82.20 |              61.71 | 杭州      | -0.03%↓ |            48.42 |              46.38 |

- [ ] **rent_level** | 上海 | rental | 2026-03 | **81.61** CNY_per_sqm_month | mom | 实际
  > | 上海   | 0.37%↑  |            81.61 |              73.78 | 常州      | -0.09%↓ |            22.79 |              20.38 |

## report_中指院百城_202603.csv

- [ ] **avg_price** | 全国 | secondary_home | 2026-03 | **12792.0** CNY_per_sqm | yoy | 实际
  > 全国100个城市二手住宅平均价格同比下跌8.55%

- [ ] **mom_change_pct** | 一线 | new_home | 2026-03 | **0.24** pct | - | 实际
  > 一线城市新建住宅价格环比上涨0.24%，同比上涨6.19%

## web_cih_index_202608.jsonl

- [ ] **mom_change_pct** | 全国 | new_home | 2026-08 | **-7.0** pct | - | 实际
  > 根据中指初步统计，8月重点100城新建商品住宅成交面积约1500万平米，环比下降约7%，同比下降约7%

- [ ] **secondary_volume_units** | 上海 | secondary_home | 2026-08 | **23000.0** units | yoy | 实际
  > 其中，8月北京二手住宅、上海二手商品房分别成交1.4万、2.3万套，高基数下同比仍分别增长3.9%、17.9%

- [ ] **mom_change_pct** | 二线 | new_home | 2026-08 | **0.09** pct | - | 实际
  > 二线城市环比上涨0.09%，同比上涨1.74%

## web_cric_bj_2026h1.jsonl

- [ ] **land_transaction_value** | 北京 | land | 2026-06 | **140.5** CNY_100m | - | 实际
  > 1月无成交，2月起逐步放量，4月与6月为成交高峰，成交总价分别达149.70亿元和140.50亿元。

- [ ] **new_home_volume_area** | 北京 | new_home | 2026-H1 | **235.04** 10k_sqm | - | 实际
  > 2026年上半年，北京新房市场（商品住宅口径）累计供应13624套/158.15万㎡、成交19061套/235.04万㎡，成交均价57442元/㎡，供求比0.67。

## web_cric_dev_2026h1.jsonl

- [ ] **yoy_change_pct** | 全国 | secondary_home | 2026-H1 | **12.0** pct | - | 实际
  > 20城上半年累计成交面积约9316.5万㎡，环比上涨22%、同比上涨12%，无论单季度还是半年度维度，二手房成交规模均实现环比、同比双增长，市场修复趋势明确，整体上半年年实现近四年半年度成交规模新高。

- [ ] **premium_rate_pct** | 全国 | land | 2026-06 | **13.7** pct | - | 实际
  > 得益于地方因城施策积极出台一系列可感可及的稳市场政策，二季度以来核心城市优质板块的土拍热度逐步攀升，6月份平均溢价率达到13.7%，近一年以来首次达到10%以上。

## web_cric_hz_202605.jsonl

- [ ] **new_home_volume_area** | 杭州 | new_home | 2026-05 | **48.5** 10k_sqm | yoy | 实际
  > 成交48.50万㎡（环比-16.98%、同比-4.92%）

- [ ] **mom_change_pct** | 杭州 | new_home | 2026-05 | **-16.98** pct | - | 实际
  > 成交48.50万㎡（环比-16.98%、同比-4.92%）

## web_cric_hz_202607.jsonl

- [ ] **new_home_volume_area** | 杭州 | new_home | 2026-07 | **28.09** 10k_sqm | yoy | 实际
  > 7月成交面积28.09万㎡为近期低点，较6月（54.18万㎡）明显回落

- [ ] **avg_price** | 杭州 | new_home | 2026-07 | **48563.0** CNY_per_sqm | mom | 实际
  > 成交均价48563元/㎡创近13个月新高

## web_cric_hz_2026h1.jsonl

- [ ] **premium_rate_pct** | 杭州 | land | 2026-H1 | **79.03** pct | - | 实际
  > 滨江、上城、萧山、余杭等核心区域溢价率居前，其中滨江永久河单元地块经过243轮竞价，楼面价高达5.16万元/㎡，溢价率79.03%，刷新板块纪录。

- [ ] **developer_sales_amount** | 杭州 | developer | 2026-H1 | **133.23** CNY_100m | - | 实际
  > 本土民企兴耀房产集团以133.23亿元位居第三，展现出本土民企的强劲实力与逆势崛起的韧性。

## web_cric_sh_2026h1.jsonl

- [ ] **premium_rate_pct** | 上海 | land | 2024-H1 | **7.0** pct | - | 实际
  > 2024年同期（成交17幅、416亿元、溢价率7%）对比可见，成交金额、幅数较2025年同期回落，但显著高于2024年同期，整体溢价率处于近三年中等水平。

- [ ] **secondary_volume_units** | 上海 | secondary_home | 2026-06 | **25158.0** units | yoy | 实际
  > 其中6月成交25,158套，同比去年6月上涨21.1%，且连续7个月（剔除春节二月）稳居2.2万套之上

## web_cric_sz_2026h1.jsonl

- [ ] **premium_rate_pct** | 深圳 | land | 2025-H1 | **35.74** pct | - | 实际
  > 平均溢价率60.56%，较2025H1的35.74%、2025p的32.03%显著攀升。

- [ ] **avg_price** | 深圳 | new_home | 2026-H1 | **66032.0** CNY_per_sqm | yoy | 实际
  > 成交均价66032元/㎡。同比2025H1（成交238.41万㎡/24879套/均价59844元），成交面积小幅回落，成交均价同比+10.3%

- [ ] **secondary_volume_units** | 深圳 | secondary_home | 2026-H1 | **37378.0** units | mom | 实际
  > 2026年上半年深圳二手房总成交37378套，同比+6.7%，环比+11.5%；

- [ ] **developer_sales_amount** | 深圳 | developer | 2026-H1 | **107.8** CNY_100m | - | 实际
  > 2026年上半年，金额榜由中海地产（133.10亿）、华润置地（118.61亿）、招商蛇口（115.66亿）三大央国企领衔，凭借核心区高货值项目支撑；本地龙头鸿荣源（107.80亿）、宏发集团、京基集团紧随。
