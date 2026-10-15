"""规范工商与财务输入经过原特征、评分、授信规则的完整公开流程。"""
from dataclasses import dataclass
import json
import re
import unicodedata

import numpy as np
import pandas as pd

from ..evidence import validate_references
from ..financial_metrics import CANONICAL_EBIT_BASIS
from ..missing_values import is_missing_text
from ..validation import day, exact_keys, finite_number, json_ready, nonempty_list, text
from .deep_features import DEEP_FEATURE_COLS, _parse_capital, _parse_dates, deep_features
from .decision import ADVICE_POLICIES, _combo_evidence, _fin_evidence, decide_all, hard_tight_hits, whitelist_failures
from .financial_features import _LISTED_FIN_COLS, listed_financial_features
from .scoring import composite_score

REGISTRATION_REQUIRED = {"注册资本", "实缴资本", "成立日期", "核准日期", "参保人数", "营业期限", "经营状态"}
REGISTRATION_OPTIONAL = {"企业类型", "曾用名", "注册地址", "最新年报地址", "网址", "经营范围", "所属城市", "所属区县", "法定代表人"}
RISK_FLAGS = {"is_dishonest", "is_serious_illegal"}
RISK_COUNTS = {"penalty_cnt_1y", "severe_penalty_cnt_1y", "enforcement_cnt_1y", "business_abnormal_cnt_1y"}
CONTEXT_FIELDS = {"region_gdp_growth", "ind_cost_shock", "ind_policy_score", "ind_event_score", "export_shock"}
FINANCIAL_NUMBERS = {"revenue_yi", "total_profit_yi", "interest_expense_yi", "ebit_yi", "cash_yi", "short_term_debt_yi",
                     "debt_ratio_pct", "net_profit_yi", "net_profit_prev_yi", "net_profit_prev2_yi",
                     "guarantee_to_equity_pct", "equity_parent_yi"}
FINANCIAL_FLAGS = {"turn_to_loss", "st_flag_in_window", "audit_nonstandard"}
FINANCIAL_FIELDS = FINANCIAL_NUMBERS | FINANCIAL_FLAGS | {
    "source_ref", "ebit_basis", "annual_pdf_sha256", "fiscal_year", "liquidity_adjustments", "loss_years_3y", "st_revoked_date"}
STATUS_RISK = {"开业": 0.0, "存续": 0.0, "在业": 0.0, "正常": 0.0,
               "注销": 1.0, "吊销": 1.0, "撤销": 1.0, "停业": 1.0, "迁出": 0.5}
COMPANY_FIELDS = {"company_id", "name", "industry", "region", "registration", "risk_signals", "context", "financials", "source_refs"}
CAPITAL_TOKEN = re.compile(r"\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:亿|万)?(?:人民币|美元|欧元|香港元|港元|英镑|日元|元)(?:人民币)?")


@dataclass(frozen=True)
class WorkflowOptions:
    review_on_missing: bool = True
    include_policy_prior: bool = False
    advice_policy: str = "business_complete"

    def __post_init__(self):
        if type(self.review_on_missing) is not bool or type(self.include_policy_prior) is not bool:
            raise ValueError("流程开关必须是布尔值")
        if self.advice_policy not in ADVICE_POLICIES:
            raise ValueError("未知建议策略")


def _missing(value):
    return value is None or (isinstance(value, str) and is_missing_text(value))


def _registration(company, as_of):
    raw = company["registration"]
    exact_keys(raw, REGISTRATION_REQUIRED, "registration", REGISTRATION_OPTIONAL)
    for field, value in raw.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"registration.{field} 须为原始文字或 null")
    for field in ("注册资本", "实缴资本"):
        value = raw[field]
        if _missing(value):
            continue
        normalized = re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", value))
        if CAPITAL_TOKEN.fullmatch(normalized) is None or pd.isna(_parse_capital(pd.Series([value])).iloc[0]):
            raise ValueError(f"{company['company_id']} 的{field}金额或货币单位不能解析，拒绝当作缺失")
    for field in ("成立日期", "核准日期"):
        value = raw[field]
        if _missing(value):
            continue
        parsed = _parse_dates(pd.Series([value])).iloc[0]
        if pd.isna(parsed) or parsed.date() > day(as_of, "as_of"):
            raise ValueError(f"{company['company_id']} 的{field}无效或晚于基准日")
    insured = raw["参保人数"]
    if not _missing(insured):
        normalized = re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", insured))
        if re.fullmatch(r"\+?\d+(?:\.0+)?人?", normalized) is None:
            raise ValueError("参保人数须为非负整数；无法识别时须显式使用 null")
    status = raw["经营状态"]
    if not _missing(status) and status not in STATUS_RISK:
        raise ValueError(f"经营状态未定义：{status}")
    return dict(raw, 企业名称=company["name"], 所属省份=company["region"])


def _signals(company):
    signals = company["risk_signals"]
    exact_keys(signals, RISK_FLAGS | RISK_COUNTS, "risk_signals")
    result = {}
    for key, value in signals.items():
        if value is None:
            result[key] = 0  # 中性编码，缺失在 missing_inputs 中保留并触发复核标记。
        elif key in RISK_FLAGS:
            if type(value) is not bool:
                raise ValueError(f"{key} 须为布尔值或 null")
            result[key] = int(value)
        else:
            if type(value) is not int or value < 0:
                raise ValueError(f"{key} 须为非负整数或 null")
            result[key] = value
    normal, severe = signals["penalty_cnt_1y"], signals["severe_penalty_cnt_1y"]
    if normal is not None and severe is not None and severe > normal:
        raise ValueError("强处罚次数不能大于全部处罚次数")
    return result


def _context(company):
    context = company["context"]
    exact_keys(context, CONTEXT_FIELDS, "context")
    result = {}
    for key, value in context.items():
        if value is None:
            result[key] = None if key == "region_gdp_growth" else 0.0
        else:
            result[key] = finite_number(value, key)
    return result


def _financial(company, sources, as_of):
    raw = company["financials"]
    if raw is None:
        return None
    exact_keys(raw, FINANCIAL_FIELDS, "financials")
    validate_references([raw["source_ref"]], sources, "financials.source_ref")
    if raw["source_ref"] not in company["source_refs"]:
        raise ValueError("财务来源须包含在该企业 source_refs 中")
    if raw["ebit_basis"] != CANONICAL_EBIT_BASIS:
        raise ValueError("公开流程只接受利润总额加利息费用的 EBIT 口径")
    for key in FINANCIAL_NUMBERS:
        if raw[key] is not None:
            finite_number(raw[key], key)
    for key in ("interest_expense_yi", "cash_yi", "short_term_debt_yi", "debt_ratio_pct", "guarantee_to_equity_pct"):
        if raw[key] is not None and raw[key] < 0:
            raise ValueError(f"{key} 不得为负")
    for key in FINANCIAL_FLAGS:
        if raw[key] is not None and (type(raw[key]) is not int or raw[key] not in (0, 1)):
            raise ValueError(f"{key} 须为 0、1 或 null")
    loss = raw["loss_years_3y"]
    if loss is not None and (type(loss) is not int or not 0 <= loss <= 3):
        raise ValueError("loss_years_3y 须为 0 至 3 的整数或 null")
    year = raw["fiscal_year"]
    if type(year) is not int or not 1900 <= year < day(as_of, "as_of").year:
        raise ValueError("年度财务期末须早于基准日所在年度")
    if raw["st_revoked_date"] is not None:
        day(raw["st_revoked_date"], "st_revoked_date")
        if raw["st_flag_in_window"] != 1:
            raise ValueError("存在撤销日期时，窗口内实施过风险警示须为 1")
    if not isinstance(raw["liquidity_adjustments"], dict):
        raise ValueError("liquidity_adjustments 须为显式披露调整对象")
    result = {key: value for key, value in raw.items() if key != "source_ref"}
    result.update(company_id=company["company_id"], credit_code=company["company_id"])
    result["liquidity_adjustments"] = json.dumps(raw["liquidity_adjustments"], ensure_ascii=False, allow_nan=False)
    return result


def _missing_inputs(company):
    missing = []
    for section in ("registration", "risk_signals", "context", "financials"):
        block = company[section]
        if block is None:
            missing.append(section)
            continue
        missing.extend(f"{section}.{key}" for key, value in block.items()
                       if _missing(value) and key != "st_revoked_date")
    return sorted(missing)


def _input_rows(companies, sources, as_of):
    nonempty_list(companies, "companies")
    rows, financials, identities = [], [], set()
    for company in companies:
        exact_keys(company, COMPANY_FIELDS, "company")
        for key in ("company_id", "name", "industry", "region"):
            text(company[key], key)
        cid = company["company_id"]
        if cid != cid.strip() or cid in identities:
            raise ValueError(f"企业编号重复或包含首尾空白：{cid}")
        identities.add(cid)
        validate_references(company["source_refs"], sources, f"{cid}.source_refs")
        registration = _registration(company, as_of)
        row = dict(registration, company_id=cid, credit_code=cid, name=company["name"],
                   industry=company["industry"], industry_node=company["industry"], region=company["region"])
        row.update(_signals(company))
        row.update(_context(company))
        row["status_risk"] = STATUS_RISK.get(registration["经营状态"], 0.0)
        rows.append(row)
        financial = _financial(company, sources, as_of)
        if financial is not None:
            financials.append(financial)
    return pd.DataFrame(rows, dtype=object), pd.DataFrame(financials, dtype=object)


def run_credit_workflow(companies, sources, as_of, options=WorkflowOptions()):
    """返回模型决策及其证据状态；不生成银行执行指令或比赛计分件。"""
    day(as_of, "as_of")
    raw, financials = _input_rows(companies, sources, as_of)
    features = deep_features(raw, as_of)
    expired = features["biz_term_expired"] > 0
    features["biz_term_expired_confirmed"] = (expired & (features["status_risk"] >= 1)).astype(int)
    features["biz_term_status_conflict"] = (expired & (features["status_risk"] < 1)).astype(int)
    features = listed_financial_features(features, financials, as_of)
    scored = composite_score(features, include_policy_prior=options.include_policy_prior,
                             include_external_policy=True)
    decisions = decide_all(scored, advice_policy=options.advice_policy)
    result = []
    feature_names = list(dict.fromkeys(DEEP_FEATURE_COLS + list(_LISTED_FIN_COLS)))
    for index, company in enumerate(companies):
        row = scored.iloc[index]
        decision = decisions.iloc[index].to_dict()
        missing = _missing_inputs(company)
        decision.pop("chain_position", None)
        decision.update({
            "name": company["name"], "missing_inputs": missing,
            "review_required": bool(missing) and options.review_on_missing,
            "scores": {key: float(row[key]) for key in ("score_entity", "score_signal", "score_industry", "score_region")},
            "features": {key: float(row[key]) for key in feature_names},
            "hard_rule_hits": hard_tight_hits(row), "combination_hits": _combo_evidence(row),
            "financial_rule_hits": _fin_evidence(row), "loosen_gate_failures": whitelist_failures(row),
            "source_refs": list(company["source_refs"]),
        })
        result.append(json_ready(decision))
    return {"companies": result, "as_of": as_of,
            "options": {"review_on_missing": options.review_on_missing,
                        "include_policy_prior": options.include_policy_prior, "advice_policy": options.advice_policy},
            "feature_missing_policy": "明确未知使用原特征哨兵或中性编码，missing_inputs 保留未知，不代表无风险"}
