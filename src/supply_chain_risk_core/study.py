"""从一个显式输入文档完成授信、传导和阶段二分析。"""
from .credit.workflow import WorkflowOptions, run_credit_workflow
from .evidence import source_summary, validate_references, validate_sources
from .input_output import analyze_input_output
from .stage2.analysis import analyze_chains
from .stage2.quantitative import board_transfer_costs, feed_cost_change, number, retained_cost_per_base_revenue, revenue_overlap_bounds
from .validation import exact_keys, fingerprint, json_ready, nonempty_list, text

SCHEMA_VERSION = 1
ROOT_FIELDS = {"schema_version", "title", "data_kind", "as_of", "sources", "companies", "input_output", "chains", "quantitative_cases"}


def _quantitative_case(case):
    exact_keys(case, {"id", "title", "kind", "inputs", "source_refs"}, "quantitative_case")
    kind, inputs = case["kind"], case["inputs"]
    text(case["id"], "quantitative_case.id")
    text(case["title"], "quantitative_case.title")
    if kind == "copper_retention":
        exact_keys(inputs, {"material_to_revenue", "copper_share", "unhedged", "price_change", "transfer"}, kind)
        result = {"retained_cost_per_100_revenue": retained_cost_per_base_revenue(**inputs), "unit": "元/百元基期收入"}
    elif kind == "feed_mix":
        exact_keys(inputs, {"corn_change", "soymeal_change", "corn_share", "soymeal_share"}, kind)
        result = {"cost_change_pct": feed_cost_change(**inputs), "unit": "%"}
    elif kind == "board_transfer":
        exact_keys(inputs, {"incremental_cost", "board_transfer", "brand_transfer"}, kind)
        result = dict(board_transfer_costs(**inputs), unit="元/同批一板一机")
    elif kind == "revenue_overlap":
        exact_keys(inputs, {"total", "product", "region"}, kind)
        lower, upper = revenue_overlap_bounds(*(number(inputs[key], key) for key in ("total", "product", "region")))
        result = {"lower": lower, "upper": upper, "unit": "与输入收入相同"}
    else:
        raise ValueError(f"未知 quantitative_case.kind：{kind}")
    return dict(case, result=json_ready(result))


def analyze_study(document, options=WorkflowOptions()):
    exact_keys(document, ROOT_FIELDS, "study")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("不支持的研究输入 schema_version")
    title = text(document["title"], "title")
    sources = validate_sources(document["sources"], document["as_of"], document["data_kind"])
    credit = run_credit_workflow(document["companies"], sources, document["as_of"], options)
    matrix = document["input_output"]
    exact_keys(matrix, {"nodes", "direct_coefficients", "price_source", "source_refs"}, "input_output")
    validate_references(matrix["source_refs"], sources, "input_output.source_refs")
    io = analyze_input_output(matrix["nodes"], matrix["direct_coefficients"], matrix["price_source"])
    io["source_refs"] = list(matrix["source_refs"])
    chains = analyze_chains(document["chains"], document["companies"], sources, io)
    cases, ids = [], set()
    nonempty_list(document["quantitative_cases"], "quantitative_cases")
    for case in document["quantitative_cases"]:
        validate_references(case["source_refs"], sources, "quantitative_case.source_refs")
        calculated = _quantitative_case(case)
        if calculated["id"] in ids:
            raise ValueError("quantitative_case.id 重复")
        ids.add(calculated["id"])
        cases.append(calculated)
    return json_ready({
        "schema_version": SCHEMA_VERSION, "title": title, "data_kind": document["data_kind"],
        "as_of": document["as_of"], "input_fingerprint": fingerprint(document),
        "credit": credit, "input_output": io, "chains": chains, "quantitative_cases": cases,
        "sources": source_summary(sources),
        "source_verification": "核对随输入提供的内容指纹、日期及引用；未在线认证来源真实性",
        "interpretation": "分数、路径暴露与情景金额均不等于违约概率；合成案例不表示实际参赛结果或银行决定",
    })
