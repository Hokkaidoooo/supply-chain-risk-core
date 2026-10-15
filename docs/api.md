# 0.3.0 API 与迁移

## 完整研究流程

`from supply_chain_risk_core.study import analyze_study` 接收[输入契约](input-schema.md)中的字典，返回独立的分析结果，不写文件。`from supply_chain_risk_core.credit.workflow import WorkflowOptions` 配置复核标记、历史行业先验和建议策略。

`from supply_chain_risk_core.input_output import analyze_input_output` 接收节点名称、直接消耗矩阵和固定价格源头，返回总需求与价格影响。默认最多 256 个节点，条件数上限为 `1e12`；分别传入 `max_nodes=None`、`condition_limit=None` 可以关闭对应计算预算或数值敏感性检查。非负性与谱半径仍须满足模型定义。

报告目录由 `supply_chain_risk_core.cli.run_to_directory` 生成，`verify_directory` 核验成员与字节并重新计算。输出目录和成员均独占创建，不覆盖已有结果；清单最后落地。命令行详见 [README](../README.md) 与 [复现说明](reproducibility.md)。

两入口默认只允许矩阵条件数出现不超过 32 ULP 的有限正浮点微差，并返回 `recompute_policy` 和 `numerical_differences`；其他结果与全部保存内容指纹严格比较。传入 `exact_recompute=True` 可要求复算逐位一致。比较策略由源码绑定，清单不能放宽它；原始计算值和矩阵病态判断不受影响。

路径与财务核心的参数见下文。


Python >=3.10，NetworkX >=3.4.2,<4。运行依赖以 `pyproject.toml` 为唯一配置源。

```python
from supply_chain_risk_core import (
    canonicalize_earnings, liquidity_metrics, compute_ebit, interest_coverage,
    upstream_of, downstream_of, exposure_report, transmission_paths,
    credit_transmission_paths, TraversalBudgetExceeded,
)
```

提交契约工具从 `supply_chain_risk_core.submission_contract` 导入。旧的裸模块导入需迁移到上述包路径；仓库根 `demo.py` 只是安装包示例的命令入口。

## 财务

`canonicalize_earnings(record)` 默认要求完整规范口径。`allow_legacy=True` 仅用于已知历史净财务费用代理：利润总额缺失时，以旧 EBIT 减净财务费用推得利润，再加实际利息费用。结果保留旧代理数值及迁移规则；金额巧合不用于猜版本。`allow_missing=True` 显式保留未知为 NaN，默认关闭。

`liquidity_metrics(record, as_of=...)` 默认拒绝无调整依据的账面口径及不完整后续披露。`allow_legacy=True` 明确标记账面现金可用性未审查；`allow_annual_fallback=True` 明确选择完整历史年报口径并保留后续时点与哈希。两开关互相独立，默认均为 False。零利息费用的保障倍数返回 None；缺失、布尔和非有限数默认报错。

## 路径预算

上下游查询默认两跳；路径报告默认三跳，最多八跳。三者默认 `max_expansions=100000`，候选边即使因成环或强度条件被拒绝也消耗预算。到达精确预算且没有剩余候选时成功；存在下一候选时抛出 `TraversalBudgetExceeded`，异常携带 `examined_edges`、`emitted_paths` 和 `limit`。输入图不变。需要关闭预算时显式传入 None。

`exposure_report` 返回 `exposures`、检查边数、产生路径数和耗时；`direction` 为 `upstream` 或 `downstream`。暴露仍取最强简单路径，零权路径保留；端点集合阻止从该节点继续扩展。`transmission_paths` 的 top_k 只限制输出内存和数量，所有预算内候选仍需检查，不能把中途结果称为完整结果。

## 企业事实的最小输入

下面仅为合成示例，example.org 不是业务证据来源。

```python
edge = {
    'source': 'synthetic-debtor', 'target': 'synthetic-guarantor',
    'relation': 'guarantee', 'direction': 'risk_source_to_affected',
    'valid_from': '2026-01-01', 'valid_to': '2026-12-31',
    'published_at': '2026-01-02',
    'source_url': 'https://example.org/synthetic-guarantee',
    'evidence_id': 'synthetic-evidence-1', 'estimated': False,
}
paths = credit_transmission_paths('synthetic-guarantor', [edge], as_of='2026-06-30')
```

`guarantee` 的风险方向是被担保债务人到担保人；`receivable` 是付款义务人到收款债权人。只支持直接关系，返回完整输入证据的副本，不推断强度或多跳路径。有效期两端包含，公开日不得晚于查询日；整批输入中任一边无来源、重复、过期、尚未生效或方向不明都会失败。调用方应按查询目的提供有效材料，不能把失败当作无风险。
