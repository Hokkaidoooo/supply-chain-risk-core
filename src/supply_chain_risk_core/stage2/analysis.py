"""共享企业财务与来源，生成产业链规格及情景结果。"""
from ..evidence import validate_references
from ..scenarios import cost_impact, demand_impact
from ..validation import exact_keys, nonempty_list, text
from .projection import project_chain

CHAIN_FIELDS = {"id", "title", "company_id", "layers", "links", "risk_path", "steps", "mechanism", "event_kind",
                "risk_event", "event_scope", "path_boundary", "company_nodes", "core_node", "core_reason",
                "highest_node", "lowest_node", "high_reason", "low_reason", "impact", "substitutability",
                "consequence", "counter_evidence", "boundary", "target_impact", "source_refs", "scenario"}


def _scenario(scenario, financials, matrix):
    if financials is None:
        raise ValueError("金额情景需要该企业的财务输入")
    for key in ("revenue_yi", "ebit_yi", "interest_expense_yi"):
        if financials[key] is None:
            raise ValueError(f"金额情景缺少 {key}，不能按零处理")
    kind = scenario.get("kind")
    if kind == "cost_push":
        exact_keys(scenario, {"kind", "source", "target", "shock", "pass_through", "mapping_basis"}, "成本情景")
        if scenario["source"] != matrix["price_source"]:
            raise ValueError("成本情景源头与矩阵固定价格源头不同")
        if scenario["target"] not in matrix["source_price_coefficients"]:
            raise ValueError("成本情景目标未登记在矩阵中")
        text(scenario["mapping_basis"], "行业与企业映射依据")
        coefficient = matrix["source_price_coefficients"][scenario["target"]]
        result = cost_impact(financials["revenue_yi"], financials["ebit_yi"], financials["interest_expense_yi"],
                             coefficient, scenario["shock"], scenario["pass_through"])
        return {"kind": kind, "parameters": dict(scenario, coefficient=coefficient), "result": result}
    if kind == "demand_contraction":
        exact_keys(scenario, {"kind", "sales_share", "shock", "contribution_margin"}, "需求情景")
        result = demand_impact(financials["revenue_yi"], financials["ebit_yi"], financials["interest_expense_yi"],
                               scenario["sales_share"], scenario["shock"],
                               contribution_margin=scenario["contribution_margin"])
        return {"kind": kind, "parameters": dict(scenario), "result": result}
    raise ValueError("金额情景 kind 仅支持 cost_push 或 demand_contraction")


def analyze_chains(chains, companies, sources, matrix):
    nonempty_list(chains, "chains")
    by_id = {company["company_id"]: company for company in companies}
    identities, result = set(), []
    for chain in chains:
        exact_keys(chain, CHAIN_FIELDS, "chain")
        chain_id = text(chain["id"], "chain.id")
        if chain_id != chain["id"] or chain_id in identities:
            raise ValueError("链编号重复或包含首尾空白")
        identities.add(chain_id)
        text(chain["title"], "chain.title")
        if chain["company_id"] not in by_id:
            raise ValueError("产业链目标企业未登记在授信输入中")
        if chain["mechanism"] != chain["scenario"].get("kind"):
            raise ValueError("产业链机制与金额情景不一致")
        validate_references(chain["source_refs"], sources, "chain.source_refs")
        company = by_id[chain["company_id"]]
        if not set(company["source_refs"]) <= set(chain["source_refs"]):
            raise ValueError("产业链来源须包含目标企业的财务与工商来源")
        projected = project_chain(chain)
        projected["scenario"] = _scenario(chain["scenario"], company["financials"], matrix)
        projected["company_name"] = company["name"]
        result.append(projected)
    return result
