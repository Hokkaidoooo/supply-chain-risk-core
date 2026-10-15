"""模型标识到报告用语的映射，不参与评分或决策。"""

RULE_LABELS = {
    "H1_dishonest": "已确认失信", "H2_serious_illegal": "已确认严重违法", "H3_status_risk": "经营状态异常",
    "H4_biz_term_expired": "营业期限到期且状态异常", "H5_severe_penalty": "近一年强处罚", "H6_repeated_penalty": "近一年重复处罚",
    "R1_hollow_capital": "低实缴叠加零参保", "R1b_hollow_undisclosed": "长期大额认缴且缺少实缴与用工证据",
    "R1c_hollow_trace_paid": "极低实缴叠加低参保", "R2_term_conflict": "存续冲突叠加实质经营偏弱",
    "R3_finance_scope": "类金融业务叠加实质经营偏弱", "R4_addr_mismatch": "地址不一致叠加实质经营偏弱",
    "F1_st_warning": "风险警示仍处于有效状态", "F2_consecutive_loss": "连续三年亏损",
    "F3_audit_nonstandard": "非标准审计意见", "F4_turn_to_loss_leverage": "转亏叠加高杠杆",
    "F5_loss2y_leverage": "多期亏损叠加高杠杆", "F6_penalty_loss2y": "处罚与持续亏损并存",
    "F7_extreme_leverage": "极端杠杆", "F8_interest_uncovered": "付息不足与短债缺口并存",
    "F9_equity_buffer_exhausted": "累计亏损耗尽权益缓冲",
    "require_paid_disclosed": "缺少实缴披露", "min_paid_ratio": "实缴比例未满足条件", "min_insured": "用工证据未满足条件",
    "min_age": "经营年限不足", "max_ent_tier": "企业类型未满足条件", "not_sole_natural_person": "自然人独资条件不符",
    "exclude_finance_like_scope": "包含类金融经营范围", "max_signal_score": "存在风险信号", "min_policy_tier": "行业政策档位未满足条件",
    "no_dishonest": "存在失信记录", "no_serious_illegal": "存在严重违法记录", "no_penalty_1y": "存在近一年处罚",
    "no_severe_penalty_1y": "存在近一年强处罚", "no_enforcement_1y": "存在近一年被执行记录",
    "no_business_abnormal_1y": "存在近一年经营异常", "no_biz_term_expired": "已确认营业期限到期",
    "no_biz_term_conflict": "营业期限与状态存在冲突", "no_status_risk": "经营状态异常",
    "no_fin_tight": "存在财务收紧信号", "no_loss_years_3y": "近三年存在亏损", "no_high_leverage": "高杠杆条件未满足",
    "require_financial_evidence": "缺少可核验财务资料", "require_profit_positive": "当期盈利条件未满足",
    "require_profit_uptrend": "盈利改善条件未满足", "reject_profit_sharp_decline": "盈利出现明显下降",
    "reject_guarantee_high": "对外担保偏高", "max_debt_ratio_pct": "杠杆证据未满足条件",
    "debt_ratio_over_cap": "杠杆超过放宽上限", "levered_without_coverage_proof": "高杠杆下缺少充分付息覆盖",
}
FINANCIAL_LABELS = {"fin_loss_years_3y": "近三年亏损年数", "fin_st_active": "当前风险警示状态",
                    "fin_audit_nonstandard": "审计意见", "fin_profit_sharp_decline": "盈利下降幅度", "fin_guarantee_high": "担保水平"}
INPUT_LABELS = {"financials": "财务资料", "export_shock": "出口冲击", "ind_cost_shock": "行业成本冲击",
                "ind_event_score": "行业事件", "ind_policy_score": "行业政策", "region_gdp_growth": "区域增速",
                "is_dishonest": "失信查询", "is_serious_illegal": "严重违法查询", "penalty_cnt_1y": "近一年处罚次数",
                "severe_penalty_cnt_1y": "近一年强处罚次数", "enforcement_cnt_1y": "近一年被执行次数",
                "business_abnormal_cnt_1y": "近一年经营异常次数"}
STATUS_LABELS = {"loosen_no_incremental_measure": "未支持新增风险措施", "insufficient_evidence": "证据不足",
                 "public_signal_prediction": "基于公开信号提出研究性措施", "policy_capped": "措施数量受策略约束",
                 "risk_signal_unmapped": "存在尚未映射到措施的风险信号", "public_financial_proposals_disabled": "公开财务信号建议已关闭",
                 "no_incremental_measure_supported": "没有足够依据支持新增措施", "action_evidence_conflict": "模型方向与证据信号存在冲突"}


def rule_label(code):
    key = code.removeprefix("fin_loosen.")
    if key.startswith("unknown."):
        field = key.removeprefix("unknown.")
        return "未知" + FINANCIAL_LABELS[field]
    return RULE_LABELS[key]


def input_label(code):
    section, _, field = code.partition(".")
    if section == "registration":
        return field
    if code == "financials":
        return INPUT_LABELS[code]
    if field in INPUT_LABELS:
        return INPUT_LABELS[field]
    # 财务输入的字段名仍保留以便精确定位，前缀改为读者可理解的类别。
    if section == "financials":
        return "财务字段 " + field
    raise ValueError(f"报告缺少输入字段说明：{code}")
