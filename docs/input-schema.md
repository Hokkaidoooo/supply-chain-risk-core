# 公开研究输入

使用 `python -m supply_chain_risk_core.cli example --output my-input.json` 导出完整样例。JSON 重复字段、非有限数、缺少必需键和未登记来源均会报错。

## 顶层结构

| 字段 | 含义 |
| --- | --- |
| schema_version | 当前为整数 1 |
| title | 报告标题 |
| data_kind | `synthetic_demonstration` 或 `public_evidence` |
| as_of | YYYY-MM-DD 格式的评估基准日 |
| sources | 来源 ID 到内容、指纹、日期和链接的映射 |
| companies | 企业工商、风险查询、背景和财务输入 |
| input_output | 非负直接消耗矩阵、行业名称、价格源头及来源引用 |
| chains | 稳定节点、业务连接、风险路径、依据及金额情景 |
| quantitative_cases | 铜价、采购组合、成本分配或交叉收入区间案例 |

## 来源记录

每个来源包含 `title`、`url`、`published_at`、`valid_from`、`valid_to`、`content`、`sha256`、`kind`。`sha256` 是 `content` 原样 UTF-8 字节的 SHA-256。`valid_to: null` 表示未给出结束日；其他日期必须真实有效。

数据性质必须与顶层相同，公开日不能晚于基准日，基准日必须处于适用区间。来源链接仅接受无用户名和口令的 HTTP(S) URL。检查的是随输入提交的内容与日期，不包含自动网页取证。

## 企业与金额

企业顶层有唯一 `company_id`，以及 `name`、`industry`、`region`、`registration`、`risk_signals`、`context`、`financials`、`source_refs`。主体身份由顶层维护，不接受靠重复编号合并两家企业。

`registration` 使用样例中的中文字段名。资本必须带明确单位，例如 `2,000万元`；接受元、万、亿与人民币、美元、欧元、港元、英镑、日元等已登记货币文字，不按人民币猜测未知货币。参保人数为整数文本；无法取得的值使用 `null`。非法金额和晚于基准日的成立/核准日期会失败。

风险查询中的布尔值与次数需要显式给出；未查清使用 `null`，不能写成 `false` 或 0。行业背景和区域增速也必须区分未知与已观测零值。

财务金额统一为亿元；`debt_ratio_pct` 等带 `_pct` 的字段使用百分数，例如 40 表示 40%。其他情景份额使用 0 至 1，例如 0.3 表示 30%。`ebit_basis` 固定为 `total_profit_plus_interest_expense`，程序核对 EBIT 是否等于利润总额加利息费用。

流动性沿用严格财务接口，必须显式给出受限、专用资金与短期融资债务调整。演示中的来源网址、指纹和页码只用于合成案例；实际使用须绑定真实披露文件。没有财务资料时将整个 `financials` 写为 `null`，不拼造金额。

## 链与情景

产业链用稳定节点 ID 表达，不用图上的位置索引代替身份。图、比较表和路径文字均由同一节点规格派生；输入不接受 `risk_values`、`edges` 等手写派生结果。

成本情景只填写源头、目标、涨幅、转嫁率及映射依据；系数来自本次矩阵求解，企业财务来自同一个 `companies` 输入。需求情景给出销售份额、降幅与贡献率，贡献率可以明确为 `null`。

所有份额、节点分数、传导方向、业务连接和引用都有独立边界检查。改变输入后必须写入新的输出目录，并执行 `verify` 重新计算。
