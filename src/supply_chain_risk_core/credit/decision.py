# -*- coding: utf-8 -*-
"""信贷决策引擎：综合分 → action（维持/收紧/放宽）+ risk_control_advice（9条内多条）。

四层决策结构：
1. 硬规则收紧——失信/严重违法/状态异常/确认到期/强处罚等已确认强负面证据。
2. 组合规则收紧——单一弱信号不足以收紧（"未披露"是证据不足而非负面证据），
   但多重独立弱信号指向同一业务结论时按可解释规则收紧（资本空心/存续冲突×
   实质弱/类金融×实质弱/地址强不一致×实质弱），用于表达独立弱信号的合取。
3. 分数线收紧——综合分越过 tight_threshold。刻度几何：全中性企业先验分=35.0，
   阈值必须显著高于先验点（中性预设 50），否则纯"简历分"就能把无风险证据的
   企业推过线；阈值只作为风险刻度，不能视为逐户真值校准结果。
4. 放宽白名单——披露实缴≥95% + 参保≥20 + 成立≥6 年 + 零风险信号 + 非类金融，
   低分且全部满足才放宽（银行主动放宽是稀缺动作）。
建议精确匹配优先——历史契约要求每户 0..4 条且整组完全匹配才得分；维持/放宽
无充分证据时留空，收紧只输出与证据类型对应的短集合，不做无证据补位。
"""
import numpy as np
import pandas as pd

from .rules import (
    RISK_ADVICE, DECISION, ADVICE_MAP, ADVICE_MAX_N, INDUSTRY_POLICY, FIN_LOSS_YEAR_VALUES
)

# 建议策略枚举。exact_match=初赛整组完全匹配口径（默认，行为与已提交件一致）；
# business_complete=显式业务候选，按可复核缺口给短集合；不等于官方建议真值。
ADVICE_POLICIES = ('exact_match', 'business_complete')


def _num(row, key, default=0.0):
    value = row.get(key, default)
    if value is None:
        return default  # 显式未知由上游记录到 missing_inputs，不把坏数值吞作未知。
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{key} 不是数值") from exc
    if not np.isfinite(value):
        raise ValueError(f"{key} 必须是有限数值")
    return value


def _hard_tight(row) -> bool:
    """硬规则强制收紧：只使用已确认的强负面证据。

    一律经 _num 读取：原生 truthy 判定会把 NaN 当 True（bool(nan)=True），
    旁路输入（CSV 往返、列子集异常）下"缺失"会被静默翻成硬收紧；与
    _combo_evidence/_pass_whitelist 的 _num 口径统一（2026-07-26 review）。
    """
    return bool(hard_tight_hits(row))


def hard_tight_hits(row):
    """返回命中的硬收紧证据代号列表；空列表表示未命中。

    _hard_tight 的布尔判定由本函数派生（单一事实源）。硬收紧在配置里只有 2 个
    数值键，但代码里是 6 条独立触发条件——度量收紧入口宽度时必须按条件数而非
    配置键数计，否则会把析取入口的宽度严重低估（asymmetric_cost.py 依赖此）。
    """
    H = DECISION['hard_tight']
    hits = []
    if _num(row, 'is_dishonest') > 0:
        hits.append('H1_dishonest')
    if _num(row, 'is_serious_illegal') > 0:
        hits.append('H2_serious_illegal')
    if _num(row, 'status_risk') >= 1.0:
        hits.append('H3_status_risk')
    if _num(row, 'biz_term_expired_confirmed') > 0:
        hits.append('H4_biz_term_expired')
    if _num(row, 'severe_penalty_cnt_1y') >= H['severe_penalty_1y_min']:
        hits.append('H5_severe_penalty')
    if _num(row, 'penalty_cnt_1y') >= H['repeated_penalty_1y_min']:
        hits.append('H6_repeated_penalty')
    return hits


def _combo_evidence(row):
    """返回命中的组合规则代号列表（R1族/R2-R4）；空列表表示未命中。

    "参保未披露"(insured_n=-1/NaN) 与"参保明确0"严格区分：R1/R1b 只认明确 0；
    R2 的实质弱接受"参保0 或 实缴未披露"；R1c/R3/R4 的低参保档用 [0, 上限]
    区间，未披露(-1)不落入。实缴未披露从不单独触发：R1b 要求它与大额认缴、
    零参保、成立≥7年（超认缴期）三重叠加；年限缺失(-1)不触发 R1b。
    """
    C = DECISION.get('combo_tight') or {}
    hits = []
    insured = _num(row, 'insured_n', -1.0)
    paid_disclosed = _num(row, 'paid_in_disclosed', 0.0) > 0
    paid_ratio = _num(row, 'paid_in_ratio', -1.0)
    reg_capital = _num(row, 'reg_capital_wan', 0.0)
    insured_zero = insured == 0
    undisclosed = not paid_disclosed

    hollow = C.get('hollow_capital')
    # paid_ratio 显式要求 [0, 上限)：NaN 经 _num 落 -1 哨兵，若无下界会被
    # "<0.10" 穿透成资本空心（此前仅靠"披露则 reg 必有效"的上游耦合免疫，
    # 2026-07-26 review 改为显式契约）。
    if hollow and reg_capital >= hollow['min_reg_capital_wan'] and paid_disclosed \
            and 0 <= paid_ratio < hollow['max_paid_ratio'] and insured_zero:
        hits.append('R1_hollow_capital')

    hollow_b = C.get('hollow_undisclosed')
    if hollow_b and reg_capital >= hollow_b['min_reg_capital_wan'] \
            and undisclosed and insured_zero \
            and _num(row, 'company_age_years', -1.0) >= hollow_b['min_age_years']:
        hits.append('R1b_hollow_undisclosed')

    hollow_c = C.get('hollow_trace_paid')
    if hollow_c and reg_capital >= hollow_c['min_reg_capital_wan'] and paid_disclosed \
            and 0 <= paid_ratio < hollow_c['max_paid_ratio'] \
            and 0 <= insured <= hollow_c['max_insured']:
        hits.append('R1c_hollow_trace_paid')

    if C.get('term_conflict_weak_substance') and _num(row, 'biz_term_status_conflict') > 0 \
            and (insured_zero or undisclosed):
        hits.append('R2_term_conflict')

    finance = C.get('finance_scope_weak_substance')
    if finance and _num(row, 'scope_finance_like') > 0 \
            and 0 <= insured <= finance['max_insured'] \
            and (undisclosed or (paid_disclosed and 0 <= paid_ratio < finance['max_paid_ratio'])):
        hits.append('R3_finance_scope')

    addr = C.get('addr_mismatch_weak_substance')
    if addr and _num(row, 'addr_mismatch_strong') > 0 \
            and 0 <= insured <= addr['max_insured'] and undisclosed:
        hits.append('R4_addr_mismatch')
    return hits


def _fin_evidence(row):
    """返回命中的财务恶化规则代号列表（F1-F9）；空列表表示未命中。

    上市公司的工商登记字段无区分度（全部开业、零失信、参保数千人、实缴充足），
    四维实算 risk_score 仅 13-35，分数线 50 结构性不可达——即使 score_signal
    打满 100，加权 0.30 后最高也只到约 48。因此定期报告口径的偿付能力恶化必须
    在此独立成层，否则连亏三年、负债率 90% 的企业仍会被判维持。

    只读 features.listed_financial_features 产出的 fin_* 列；无财务数据的企业
    (fin_has_record=0) 提前返回；部分财务字段的未知哨兵不构成确认风险。
    """
    F = DECISION.get('fin_tight') or {}
    if not F or _num(row, 'fin_has_record') <= 0:
        return []
    hits = []
    # 读 fin_st_active 而非 fin_st_in_window：基准日前已撤销风险警示的企业，
    # 交易所已认定恶化情形消除，按"当前被 ST"开出提前收回融资与事实相反。
    # 窗口内发生过的事实由 fin_st_in_window 留档，不参与收紧判据。
    if F.get('st_in_window') and _num(row, 'fin_st_active') > 0:
        hits.append('F1_st_warning')
    if F.get('audit_nonstandard') and _num(row, 'fin_audit_nonstandard') > 0:
        hits.append('F3_audit_nonstandard')
    # 高杠杆闸门：纯亏损在实质无债企业不构成偿付能力恶化（负债率 8.1% 的企业
    # 亏 0.47 亿不影响还款能力），必须与杠杆叠加。
    debt = _num(row, 'fin_debt_ratio_pct')
    leverage = debt >= F.get('high_leverage_pct', 70.0)
    loss_years = _num(row, 'fin_loss_years_3y')
    # F2 的杠杆闸门（2026-08-26 补）。原实现只看"连亏三年"，与 F4/F5 的设计理由
    # 自相矛盾：同一条"纯亏损在实质无债企业不构成恶化"对 F2 同样成立。补齐后
    # 负债率 7.47%（权益占九成二）连亏三年的企业不再被判收紧。闸门键独立于
    # high_leverage_pct，便于 9/11 各自校准；校准前同值，见 config 注释。
    if F.get('consecutive_loss_3y') \
            and _num(row, 'fin_consecutive_loss_3y') > 0 \
            and debt >= F.get('loss_3y_leverage_pct',
                              F.get('high_leverage_pct', 70.0)):
        hits.append('F2_consecutive_loss')
    if F.get('turn_to_loss_with_leverage') and _num(row, 'fin_turn_to_loss') > 0 \
            and leverage:
        hits.append('F4_turn_to_loss_leverage')
    if F.get('loss_2y_with_leverage') and loss_years >= 2 and leverage:
        hits.append('F5_loss2y_leverage')
    # 行为负面（监管处罚）与偿付能力负面（持续亏损）是两类独立证据，同向叠加
    # 时按组合规则收紧；任一单独都不足（处罚需满 3 条、两年亏损需配高杠杆）。
    # 杠杆闸门（2026-08-26 补）：与 F2 同理，原实现让"亏两年 + 2 条普通处罚 +
    # 负债率 25%"被判收紧，而"亏两年 + 负债率 69%"因差 1 个百分点逃过 F4/F5。
    # 处罚密度本身已由 H6（≥3 条）覆盖，F6 不需重复承担该职能。
    if F.get('penalty_with_loss_2y') and loss_years >= 2 \
            and _num(row, 'penalty_cnt_1y') > 0 \
            and debt >= F.get('penalty_loss_leverage_pct',
                              F.get('high_leverage_pct', 70.0)):
        hits.append('F6_penalty_loss2y')
    # 极端杠杆单独成档，不与亏损年数叠加：F4/F5 的"亏损×70%"在杠杆极端段会造成
    # 阈值悬崖（90.6% 连亏三年收紧 / 89.8% 亏一年维持，实质无差别却结论相反）。
    # 权益缓冲仅剩一成时杠杆本身即已实现的偿付能力恶化。
    extreme = F.get('extreme_leverage_pct')
    if extreme is not None and _num(row, 'fin_debt_ratio_pct') >= extreme:
        hits.append('F7_extreme_leverage')
    # F8 付息能力不足 × 短期偿付缺口：偿债是现金问题，而 F1-F7 全在测利润表。
    # 负债率是"有无真实债务负担"的代理指标，在中段杠杆失效——61% 杠杆却付不出
    # 利息的企业能逃过 F4/F5(70% 线)、F6(需处罚)、F2(需连亏三年)。三条缺一不可：
    # 当期亏损 + 存在实质付息负担且 EBIT 不覆盖利息 + 现金不覆盖短期有息负债。
    # 付息负担闸门必须先过：无利息支出的企业实质无债，付息比率无意义。该闸门于
    # 2026-08-20 从"财务费用净额为正"改为"年报利息费用为正"——净额会被利息收入
    # 冲成负数，原口径把 8 户净额为负者整体跳过检查，漏判了其中 EBIT 为负的
    # 011/016/019 三户。三户现金均覆盖短债，故本条改动不新增 F8 命中。
    if F.get('interest_uncovered_with_cash_gap') \
            and _num(row, 'fin_profit_positive', -1.0) == 0 \
            and _num(row, 'fin_interest_burden') > 0 \
            and _num(row, 'fin_ebit_covers_interest', -1.0) == 0 \
            and _num(row, 'fin_cash_covers_short_debt', -1.0) == 0:
        hits.append('F8_interest_uncovered')
    equity_min = F.get('cumulative_loss_to_equity_min')
    if equity_min is not None \
            and _num(row, 'fin_cumulative_loss_to_equity_ratio') >= equity_min:
        hits.append('F9_equity_buffer_exhausted')
    return hits


def _fin_tight(row) -> bool:
    return bool(_fin_evidence(row))


def _combo_tight(row) -> bool:
    return bool(_combo_evidence(row))


def _leverage_loosen_failure(row):
    """放宽档的杠杆判据，返回失败名或 None。

    分两段而不是一条硬线（见 config.fin_loosen 的推导）：

      负债率 ≤ 常规线                     → 通过；
      常规线 < 负债率 ≤ 绝对上限          → 须自证仍在偿付（利息保障 ≥ 下限）；
      负债率 > 绝对上限                   → 一律不放宽。

    中段用**利息保障倍数**而不是负债率本身做判据：负债率回答"借了多少"，利息保障
    回答"还得上吗"，后者才是能否扩大敞口的直接证据。本样本里两户负债率同为 74%
    上下，利息保障 2.88 倍与 1.38 倍的偿付事实完全不同，单看负债率无法区分。
    """
    L = DECISION.get('fin_loosen') or {}
    normal = L.get('max_debt_ratio_pct')
    if normal is None:
        return None
    debt = _num(row, 'fin_debt_ratio_pct', -1.0)
    if not debt >= 0:
        # 负债率不可知（哨兵 -1）时不放行：放宽要正向证据，缺证据不等于通过。
        return 'max_debt_ratio_pct'
    if debt <= normal:
        return None
    cap = L.get('levered_debt_ratio_cap_pct')
    if cap is not None and debt > cap:
        return 'debt_ratio_over_cap'
    floor = L.get('levered_min_interest_coverage_x')
    if floor is None:
        return 'max_debt_ratio_pct'
    cover = row.get('fin_interest_coverage_x')
    try:
        cover = float(cover)
    except (TypeError, ValueError):
        cover = None
    if cover is None or not np.isfinite(cover) or not cover >= float(floor):
        return 'levered_without_coverage_proof'
    return None


def financial_loosen_failures(row):
    """返回未通过的财务放宽闸门名列表；空列表 = 全部通过。

    与 _pass_financial_loosen_gate 同源：布尔判定由本函数派生，避免两处逻辑漂移。
    明细版不短路，因此可用于度量"差几条闸门才能放宽"（见 asymmetric_cost.py）。
    """
    L = DECISION.get('fin_loosen') or {}
    if not L:
        return []
    fails = []
    for field in ('fin_loss_years_3y', 'fin_st_active', 'fin_audit_nonstandard'):
        if _num(row, field, -1.0) < 0:
            fails.append('unknown.' + field)
    if L.get('require_profit_positive') and not _num(row, 'fin_profit_positive') > 0:
        fails.append('require_profit_positive')
    if L.get('reject_profit_sharp_decline') \
            and _num(row, 'fin_profit_sharp_decline', -1.0) != 0:
        fails.append('unknown.fin_profit_sharp_decline'
                     if _num(row, 'fin_profit_sharp_decline', -1.0) < 0
                     else 'reject_profit_sharp_decline')
    if L.get('require_profit_uptrend') and not _num(row, 'fin_profit_uptrend') > 0:
        fails.append('require_profit_uptrend')
    if L.get('reject_guarantee_high') and _num(row, 'fin_guarantee_high', -1.0) != 0:
        fails.append('unknown.fin_guarantee_high'
                     if _num(row, 'fin_guarantee_high', -1.0) < 0
                     else 'reject_guarantee_high')
    leverage = _leverage_loosen_failure(row)
    if leverage:
        fails.append(leverage)
    return fails


def _pass_financial_loosen_gate(row) -> bool:
    """有审计报表证据时，主动放宽授信必须满足的正向财务条件。

    收紧看的是已实现的恶化，放宽要求的是正向证据——"没有坏消息"不等于"可以
    扩大敞口"。四项各挡一类失败：盈利为正挡当期亏损户；盈利未显著下滑挡趋势
    恶化户（净利两年腰斩仍无亏损的企业不该获放宽）；对外担保未失控挡表外或有
    负债；杠杆判据分档挡"高杠杆且已还不上"，但不再把"高杠杆且在正常还本付息"
    一并挡掉（见 _leverage_loosen_failure 与 config.fin_loosen 的推导）。
    """
    return not financial_loosen_failures(row)


def _audited_evidence(row) -> bool:
    """该户是否有经核验的审计报表证据（上市/发债口径）。

    工商登记快照里的若干字段对这类主体是**披露口径差异**而非风险事实，缺失时
    应由报表证据替代判断，而不是当成负面记录——见 whitelist_failures 内的逐条注释。
    """
    return _num(row, 'fin_has_record') > 0


def whitelist_failures(row, min_insured=None):
    """返回未通过的放宽闸门名列表；空列表 = 全部通过（可放宽）。

    与 _pass_whitelist 同源：布尔判定由本函数派生。明细版不短路，逐条记录，
    因此可度量"某户离放宽差几条闸门"——合取入口的窄度证据（asymmetric_cost.py）。
    闸门名保持稳定，勿随意改名：它们会进 eval_report.json 与决赛文档。

    **缺失不等于不利**（2026-09-07）。原实现把工商登记字段的缺失编码成哨兵
    （`insured_n = -1`、`paid_in_disclosed = 0`）后直接参与比较，等于把"没有这条
    信息"判成"这条信息最坏"。有审计报表证据时这是错的：实缴披露与参保人数对上市
    主体本就是登记口径差异（母公司报表的参保数不含子公司），而报表证据比这两个
    字段强得多。特征层其实**已经**把两种情形分开了（`insured_missing` 与
    `insured_zero` 是两列），只是闸门把这个区分丢掉了。
    修正后：真实的零（`insured_zero`）仍然硬挡——零参保是实质的空壳信号；纯缺失
    在有报表证据时按"该项不适用"处理。无报表证据时仍检查工商条件，并按
    no_evidence_blocks_loosen 显式决定是否允许进入放宽档。
    """
    W = DECISION['whitelist']
    min_insured = W['min_insured'] if min_insured is None else min_insured
    node = str(row.get('industry_node', row.get('industry', '')))
    min_policy_tier = W.get('min_policy_tier')
    audited = _audited_evidence(row)
    fails = []

    if (W.get('require_paid_disclosed') and not _num(row, 'paid_in_disclosed') > 0
            and not audited):
        # 有报表证据时不按此项挡：与下方 min_paid_ratio 的豁免同源——若实缴/注册
        # 比值对上市主体是伪信号，那么"该比值是否被登记披露"更是伪信号，两处口径
        # 必须一致，否则同一理由只豁免了一半。
        fails.append('require_paid_disclosed')
    if W.get('exclude_finance_like_scope') and _num(row, 'scope_finance_like') > 0:
        fails.append('exclude_finance_like_scope')
    # 工商口径零负面记录：原实现是一个合并的 or 判定，这里拆到单条以便定位，
    # 布尔结论与原合并判定等价（任一命中即失败）。
    for field, name in (
            ('is_dishonest', 'no_dishonest'),
            ('is_serious_illegal', 'no_serious_illegal'),
            ('penalty_cnt_1y', 'no_penalty_1y'),
            ('severe_penalty_cnt_1y', 'no_severe_penalty_1y'),
            ('enforcement_cnt_1y', 'no_enforcement_1y'),
            ('business_abnormal_cnt_1y', 'no_business_abnormal_1y'),
            ('biz_term_expired_confirmed', 'no_biz_term_expired'),
            ('biz_term_status_conflict', 'no_biz_term_conflict')):
        if _num(row, field) > 0:
            fails.append(name)
    if _num(row, 'status_risk') >= 1.0:
        fails.append('no_status_risk')
    # 定期报告口径的偿付能力恶化同样是"公开负面记录"。
    if _fin_tight(row):
        fails.append('no_fin_tight')
    if _num(row, 'fin_loss_years_3y') > 0:
        fails.append('no_loss_years_3y')
    if _num(row, 'fin_high_leverage') > 0 and not audited:
        # 有报表证据时不在此重复挡杠杆：fin_high_leverage 的定义就是
        # `fin_debt_ratio_pct >= 70`（features.py），与 fin_loosen 的杠杆判据是同一
        # 个量。原实现让同一事实在 no_high_leverage(70%) 与
        # fin_loosen.max_debt_ratio_pct(50%) 两处按两条不同的线各挡一次，既重复计
        # 分又自相矛盾。杠杆统一由 _leverage_loosen_failure 分档判定；无报表证据的
        # 企业没有 fin_loosen 那道闸门，仍靠本条兜底。
        fails.append('no_high_leverage')
    if _num(row, 'fin_has_record') > 0:
        fails.extend('fin_loosen.' + f for f in financial_loosen_failures(row))
    elif (DECISION.get('fin_loosen') or {}).get('no_evidence_blocks_loosen'):
        # 无经核验财务证据时是否阻止放宽。默认 True：用**单条**显式失败挡住，而不是拿归零值去跑
        # 五条闸门——后者会产出"利润不为正""负债率超限"这类不实的失败理由，而事实
        # 是这些量根本不可知。作用域与显式回退开关见 config.py 的 fin_loosen。
        fails.append('fin_loosen.require_financial_evidence')

    paid_ratio_ok = _num(row, 'paid_in_ratio', -1.0) >= W['min_paid_ratio']
    if not paid_ratio_ok and _num(row, 'fin_has_record') > 0:
        # 实缴/注册比对上市公司是伪信号，详见 _pass_whitelist 内同址注释。
        paid_ratio_ok = True
    if not paid_ratio_ok:
        fails.append('min_paid_ratio')
    if not _num(row, 'insured_n', -1.0) >= min_insured:
        # 参保人数的"缺失"与"确为零"必须分开：零参保是实质的空壳信号，硬挡；纯缺失
        # 在有报表证据时按该项不适用处理（母公司报表口径的参保数不含子公司，登记
        # 快照留空很常见）。特征层已给出 insured_missing / insured_zero 两列，此处
        # 只是把这个区分用起来，不引入新数据。
        missing_only = (_num(row, 'insured_missing') > 0
                        and not _num(row, 'insured_zero') > 0)
        if not (missing_only and audited):
            fails.append('min_insured')
    if not _num(row, 'company_age_years', -1.0) >= W['min_age']:
        fails.append('min_age')
    if not _num(row, 'ent_type_tier', float('inf')) <= W['max_ent_tier']:
        fails.append('max_ent_tier')
    if not _num(row, 'is_sole_natural_person', 1.0) == 0:
        fails.append('not_sole_natural_person')
    if not (min_policy_tier is None or INDUSTRY_POLICY.get(node, 0) >= min_policy_tier):
        fails.append('min_policy_tier')
    if not _num(row, 'score_signal', float('inf')) <= W['max_signal_score']:
        fails.append('max_signal_score')
    return fails


def _pass_whitelist(row, min_insured=None) -> bool:
    """放宽白名单：全部条件满足才允许放宽。

    除 score_signal<=0 的汇总防线外，显式要求零负面记录（处罚/被执行/异常/
    失信/状态/期限冲突全零）——不依赖评分中间量的深度防御，对应银行"放宽户
    必须无任何公开负面记录"的业务口径。

    实现委托给 whitelist_failures（单一事实源）；下方保留原短路实现的等价性由
    test_decision_invariants 的逐户对照测试守护。
    """
    return not whitelist_failures(row, min_insured=min_insured)


def _pass_whitelist_legacy(row) -> bool:
    """原短路实现，仅用于与 whitelist_failures 做等价性回归对照，勿在链路中调用。

    三处 2026-09-07 的口径修正（实缴披露豁免、参保缺失与零分开、杠杆不重复计）
    在此同步镜像；两份实现的逐户一致性由 test_decision_invariants 与
    test_asymmetric_cost 守护，改一处不改另一处会立刻变红。
    """
    W = DECISION['whitelist']
    node = str(row.get('industry_node', row.get('industry', '')))
    min_policy_tier = W.get('min_policy_tier')
    audited = _audited_evidence(row)
    if (W.get('require_paid_disclosed') and not _num(row, 'paid_in_disclosed') > 0
            and not audited):
        return False
    if W.get('exclude_finance_like_scope') and _num(row, 'scope_finance_like') > 0:
        return False
    if (_num(row, 'is_dishonest') > 0 or _num(row, 'is_serious_illegal') > 0
            or _num(row, 'status_risk') >= 1.0
            or _num(row, 'penalty_cnt_1y') > 0 or _num(row, 'severe_penalty_cnt_1y') > 0
            or _num(row, 'enforcement_cnt_1y') > 0 or _num(row, 'business_abnormal_cnt_1y') > 0
            or _num(row, 'biz_term_expired_confirmed') > 0
            or _num(row, 'biz_term_status_conflict') > 0):
        return False
    # 定期报告口径的偿付能力恶化同样是"公开负面记录"：亏损、风险警示、非标审计
    # 任一命中即不得放宽，与上面工商口径的零负面要求同级。杠杆不在此处重复计——
    # 有报表证据时统一交给 _leverage_loosen_failure 分档判定（见 whitelist_failures
    # 内同址注释）。
    if _fin_tight(row) or _num(row, 'fin_loss_years_3y') > 0 \
            or (_num(row, 'fin_high_leverage') > 0 and not audited):
        return False
    if _num(row, 'fin_has_record') > 0 and not _pass_financial_loosen_gate(row):
        return False
    if (_num(row, 'fin_has_record') <= 0
            and (DECISION.get('fin_loosen') or {}).get('no_evidence_blocks_loosen')):
        # 与 whitelist_failures 的同址分支等价；两者的逐户一致性由
        # test_asymmetric_cost.py 的 _pass_whitelist / _pass_whitelist_legacy 对照守护。
        return False
    paid_ratio_ok = row.get('paid_in_ratio', 0) >= W['min_paid_ratio']
    if not paid_ratio_ok and _num(row, 'fin_has_record') > 0:
        # 实缴/注册比对上市公司是工商登记口径的伪信号：IPO 与历次增发后注册资本
        # 即股本、按定义已全额缴足，该字段的低值反映的是登记披露口径差异而非资本
        # 未到位。用它把关会造成业务倒挂——本样本中对外担保达净资产 119% 的企业
        # 因比值 0.999 获放宽，而净利三年连增、负债率 13.5%、零监管记录的企业因
        # 比值 0.27 被挡（2026-08-08）。故当存在经核验的审计报表证据时，由
        # _pass_financial_loosen_gate 的资本实质判据替代该项；无财务证据的企业
        # （训练集 13k 户全部如此）行为一字不变。
        paid_ratio_ok = True
    insured_ok = row.get('insured_n', 0) >= W['min_insured']
    if not insured_ok and audited:
        # 与 whitelist_failures 的同址口径一致：纯缺失按该项不适用，确为零仍硬挡。
        insured_ok = (_num(row, 'insured_missing') > 0
                      and not _num(row, 'insured_zero') > 0)
    return (paid_ratio_ok
            and insured_ok
            and row.get('company_age_years', 0) >= W['min_age']
            and row.get('ent_type_tier', 5) <= W['max_ent_tier']
            and row.get('is_sole_natural_person', 1) == 0
            and (min_policy_tier is None or INDUSTRY_POLICY.get(node, 0) >= min_policy_tier)
            and row.get('score_signal', 100) <= W['max_signal_score'])


def decide_action(row, tight_thr=None, loose_thr=None) -> str:
    """单企业 action 决策（硬规则 → 组合规则 → 分数线 → 放宽白名单）。"""
    tight_thr = DECISION['tight_threshold'] if tight_thr is None else tight_thr
    loose_thr = DECISION['loose_threshold'] if loose_thr is None else loose_thr
    try:
        tight_thr, loose_thr = float(tight_thr), float(loose_thr)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('决策阈值必须是有限数值') from exc
    if not np.isfinite([tight_thr, loose_thr]).all():
        raise ValueError('决策阈值必须是有限数值')
    if _hard_tight(row) or _combo_tight(row) or _fin_tight(row):
        return '收紧'
    rs = row.get('risk_score')
    try:
        rs = float(rs)
    except (TypeError, ValueError):
        rs = float('nan')
    if not np.isfinite(rs):
        raise ValueError('决策行缺少或包含非有限 risk_score（company_id=%r）；'
                         '缺失默认值会静默落入收紧线附近，必须 fail-fast'
                         % row.get('company_id'))
    if rs >= tight_thr:
        return '收紧'
    if rs <= loose_thr and _pass_whitelist(row):
        return '放宽'
    return '维持'


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _tight_advice_by_evidence(row):
    """把证据类型置于通用严重度之前，确保前三条不会被无关模板截断。

    组合规则户的定向建议按可解释业务缺口映射：资本空心→担保增信+缩期限+调减额度；
    存续冲突→调减额度+缩期限+担保；类金融/地址异常→归行监控+缩期限。
    """
    if _num(row, 'is_dishonest') > 0 or _num(row, 'is_serious_illegal') > 0:
        return [9, 4, 2]  # 提前收回/冻结调减/降级
    if _num(row, 'status_risk') >= 1.0 or _num(row, 'biz_term_expired_confirmed') > 0:
        return [4, 9, 2]
    fin = _fin_evidence(row)
    # 退出级财务证据必须先于普通处罚/被执行模板。否则给一户新增一条弱行为记录，
    # 反而会把 ST/权益耗尽对应的提前收回建议覆盖掉，严重度出现倒挂。
    if 'F1_st_warning' in fin or (
            'F2_consecutive_loss' in fin and _num(row, 'fin_high_leverage') > 0):
        return [2, 4, 5, 9]
    if 'F9_equity_buffer_exhausted' in fin:
        return [2, 4, 9]
    if (_num(row, 'severe_penalty_cnt_1y') > 0
            or _num(row, 'penalty_cnt_1y') >= 3):
        # 行为违规叠加偿付能力恶化时追加缩期限：单纯处罚户用降级+限额+补抵押，
        # 若财务层同时命中则须压缩久期——两类独立负面证据同向时，保全措施与
        # 期限管理都要上，分别对应风险等级、额度、第二还款来源和暴露久期。
        # 无财务证据的企业（训练集 13k 户全部如此）模板一字不变。
        return [2, 4, 5, 8] if _fin_evidence(row) else [2, 4, 5]
    # 财务恶化：偿付能力问题的处置顺序与行为违规不同——先降级(2)、再冻结调减
    # 额度(4)，然后按恶化深度决定是压缩期限(8)还是直接退出(9)。
    if fin:
        # 已被交易所风险警示或连亏三年叠加高杠杆：还款来源实质缺失，进入退出通道。
        # 处置目标是保全本金，追加抵押物(5)优先于缩短期限(8)——对已失去还款能力
        # 的企业压缩期限只是把违约提前，不增加回收，因此进入回收与保全组合。
        if 'F3_audit_nonstandard' in fin:
            return [2, 4, 5, 8]   # 报表可信度存疑：补抵押物以脱离对报表的依赖
        if 'F2_consecutive_loss' in fin:
            return [2, 4, 8]
        if 'F6_penalty_loss2y' in fin:
            # 处罚 × 持续亏损按杠杆分两档。负债超过权益（≥50%）时偿债能力受威胁，
            # 需要限额(4)与保全(5)；负债率仅二成的企业权益占七成六，同样的处罚+
            # 亏损指向的是经营与合规问题而非偿付风险，冻结额度对风险缓释无实质
            # 作用却实打实伤客户，改为归行(3) 加强资金监控 + 缩期(8) 管理久期。
            # 两档分别处理偿付能力风险与经营合规风险，避免无差别冻结额度。
            if _num(row, 'fin_debt_ratio_pct') >= 50.0:
                return [2, 4, 5, 8]
            return [2, 3, 8]
        if fin == ['F8_interest_uncovered']:
            # 付息能力不足但杠杆仍在中段（未触 70% 线）、无监管处罚、未连亏三年：
            # 风险是"经营性现金还不上息、还款来源转向再融资"，而不是已进入退出
            # 通道。此时冻结调减额度(4) 会直接切断再融资、把违约提前，对回收无益；
            # 正确处置是降级(2) 反映实质恶化 + 追加抵押物(5) 补第二还款来源 +
            # 缩短贷款期限(8) 压缩暴露久期，与 F7 同属"缓冲不足但未违约"档。
            return [2, 5, 8]
        if fin == ['F7_extreme_leverage']:
            # 极端杠杆但尚未出现亏损或处罚：这是资产负债表缓冲过薄，不是已实现的
            # 违约事件，处置重点是压缩暴露期限并补第二还款来源，而不是冻结额度。
            return [2, 5, 8]
        return [2, 4, 8]          # F4/F5 转亏或两年亏损 × 高杠杆
    if _num(row, 'enforcement_cnt_1y') > 0:
        return [2, 5, 8]  # 普通被执行：不冒充失信，但加强增信与期限管理
    if _num(row, 'business_abnormal_cnt_1y') > 0:
        return [2, 8, 5]
    combo = _combo_evidence(row)
    if any(c in combo for c in ('R1_hollow_capital', 'R1b_hollow_undisclosed',
                                'R1c_hollow_trace_paid')):
        return [4, 6]
    if 'R2_term_conflict' in combo:
        return [2, 4, 8]
    if 'R3_finance_scope' in combo:
        return [2, 3, 4]
    if 'R4_addr_mismatch' in combo:
        return [2, 4, 8]
    # 只用近一年处罚计数，与 action 口径一致；不再让全历史 penalty_amt 让陈年处罚
    # 长期抢占建议前三位（P2-3）。
    if _num(row, 'penalty_cnt_1y') > 0:
        return [2, 5, 8]
    if _num(row, 'export_shock') > 0 or _num(row, 'ind_cost_shock') >= 0.02:
        return [1, 3, 8]  # 行业/贸易冲击：定价、归行、缩期限
    return []


def _cash_gap_state(row):
    """现金覆盖三态；只解释已存在且有效的财务字段。"""
    if _advice_flag(row, 'fin_has_record') is not True:
        return None
    covered = _advice_flag(row, 'fin_cash_covers_short_debt')
    return None if covered is None else not covered



_ADVICE_FINANCIAL_FLAGS = (
    'fin_profit_positive', 'fin_high_leverage', 'fin_guarantee_high',
    'fin_interest_burden', 'fin_ebit_covers_interest',
    'fin_interest_coverage_thin', 'fin_profit_sharp_decline',
    'fin_cash_covers_short_debt',
)
_ADVICE_IMPLEMENTATION_CHECKS = {
    1: '核对现有定价、合同调整条件及新增利息负担的可承受性',
    2: '按本行评级规则复核降级幅度，不把公开风险代理当作已知当前评级',
    3: '核对本行现有归行占比、结算约定及可锁定回款，不能宣称已证归行不足',
    4: '核对未用额度、存量承诺与限额调整约定',
    5: '核对现有抵押覆盖率、估值和可追加抵押资产；现金缺口不等于抵押不足',
    6: '核对保证人能力、授权及担保效力；对外担保高不等于有合格新增保证人',
    7: '核对可质押权利或动产、权属及顺位，不能从行业或杠杆直接确认',
    8: '核对实际贷款期限和可执行还款计划，不能仅凭账面现金缺口要求缩期',
    9: '核对提前到期条款、触发事件及法律执行条件，不能把风险信号当合同违约',
}


def _advice_number(row, key):
    """缺失保留为未知；非缺失坏值明确失败，不沿用评分层的静默默认值。"""
    value = row.get(key)
    if not pd.api.types.is_scalar(value):
        raise ValueError('建议证据 %s 必须为标量' % key)
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('建议证据 %s=%r 不是数值' % (key, value)) from exc
    if not np.isfinite(number):
        raise ValueError('建议证据 %s 必须为有限数值或缺失' % key)
    return number


def _advice_flag(row, key):
    value = _advice_number(row, key)
    if value is None or value == -1:
        return None
    if value not in (0, 1):
        raise ValueError('建议证据 %s 只允许 -1/0/1，实际为 %r' % (key, value))
    return bool(value)


def observe_advice_evidence(row):
    """一次读取金融三态证据，未知现金不能抹掉独立的担保或利润信号。"""
    present = _advice_flag(row, 'fin_has_record')
    keys = _ADVICE_FINANCIAL_FLAGS + ('fin_loss_years_3y',)
    if present is not True:
        return {
            'financial_record': present, 'signals': [],
            'unknown_fields': (['fin_has_record'] if present is None else []) + list(keys),
            'values': {}, 'scope': 'financial_record_unavailable',
        }
    flags = {key: _advice_flag(row, key) for key in _ADVICE_FINANCIAL_FLAGS}
    years = _advice_number(row, 'fin_loss_years_3y')
    if years == -1:
        years = None
    if years is not None and (years not in FIN_LOSS_YEAR_VALUES):
        raise ValueError('建议证据 fin_loss_years_3y 只允许 -1/0/1/2/3')
    active = {
        'current_nonprofit': flags['fin_profit_positive'] is False,
        'high_leverage': flags['fin_high_leverage'] is True,
        'guarantee_high': flags['fin_guarantee_high'] is True,
        'profit_sharp_decline': flags['fin_profit_sharp_decline'] is True,
        'cash_gap': flags['fin_cash_covers_short_debt'] is False,
        'recent_loss': years is not None and years > 0,
        'interest_uncovered': (flags['fin_interest_burden'] is True
                               and flags['fin_ebit_covers_interest'] is False),
        'interest_thin': (flags['fin_interest_burden'] is True
                          and flags['fin_interest_coverage_thin'] is True),
    }
    values = dict(flags, fin_loss_years_3y=years)
    return {
        'financial_record': True,
        'signals': [name for name, hit in active.items() if hit],
        'unknown_fields': [name for name, value in values.items() if value is None],
        'values': values, 'scope': 'observed_public_financial_signals',
    }


_ADVICE_SIGNAL_NAMES = frozenset({
    'current_nonprofit', 'high_leverage', 'guarantee_high', 'profit_sharp_decline',
    'cash_gap', 'recent_loss', 'interest_uncovered', 'interest_thin',
})


def _validate_maintain_rule(rule):
    expected = {'id', 'label', 'all', 'any', 'advice'}
    if not isinstance(rule, dict) or set(rule) != expected:
        raise ValueError('建议规则字段必须为 %s' % sorted(expected))
    if any(not isinstance(rule[key], str) or not rule[key].strip() for key in ('id', 'label')):
        raise ValueError('建议规则 id/label 必须为非空字符串')
    for key in ('all', 'any', 'advice'):
        if not isinstance(rule[key], list):
            raise ValueError('建议规则 %s.%s 必须为列表' % (rule['id'], key))
    conditions = rule['all'] + rule['any']
    if not conditions or any(not isinstance(s, str) or s not in _ADVICE_SIGNAL_NAMES
                             for s in conditions):
        raise ValueError('建议规则 %s 缺条件或引用未知风险信号' % rule['id'])
    if not rule['advice'] or any(type(code) is not int or code not in RISK_ADVICE
                                 for code in rule['advice']):
        raise ValueError('建议规则 %s 的措施编号非法' % rule['id'])


def _maintain_proposals(observed, config):
    """规则与措施只在配置表表达；两个策略共用，不另设基线填充。"""
    enabled = config['enable_public_financial_proposals']
    if type(enabled) is not bool:
        raise ValueError('enable_public_financial_proposals 必须为布尔值')
    active = set(observed['signals'])
    matches, seen = [], set()
    for rule in config['maintain_rules']:
        _validate_maintain_rule(rule)
        if rule['id'] in seen:
            raise ValueError('建议规则 id 重复: %s' % rule['id'])
        seen.add(rule['id'])
        all_of, any_of = set(rule['all']), set(rule['any'])
        if not all_of <= active or (any_of and not any_of & active):
            continue
        codes = list(rule['advice'])
        matches.append({
            'rule_id': rule['id'], 'label': rule['label'],
            'signals': sorted(all_of | (any_of & active)), 'codes': codes,
        })
    codes = _dedup([n for rule in matches for n in rule['codes']]) if enabled else []
    return codes, matches


def _tight_selection(row, policy):
    """保留既有收紧模板，明确它是待核适用条件的代理预测而非合同事实。"""
    basis = ['legacy_tight_template', policy] + _fin_evidence(row) + _combo_evidence(row)
    if policy == 'business_complete':
        return _final_tight_advice(row), basis
    specific = _tight_advice_by_evidence(row)
    if specific:
        return specific, basis
    score = _advice_number(row, 'risk_score')
    if score is None:
        raise ValueError('收紧建议缺 risk_score，不能静默代入默认分数')
    if _hard_tight(row):
        key = 'tight_severe'
    elif score >= DECISION['tight_threshold'] + 5:
        key = 'tight_high'
    else:
        key = 'tight_mid'
    return list(ADVICE_MAP[key]), basis + [key]


def _advice_cap(advice_max_n):
    cap = ADVICE_MAX_N if advice_max_n is None else advice_max_n
    if isinstance(cap, bool) or not isinstance(cap, (int, np.integer)) or not 0 <= cap <= 9:
        raise ValueError('advice_max_n 必须为 0..9 的整数')
    return int(cap)


def _advice_status(action, codes, proposed, observed, config):
    if action == '放宽':
        if observed['signals']:
            return 'action_evidence_conflict'
        return ('insufficient_evidence' if observed['unknown_fields']
                else 'loosen_no_incremental_measure')
    if codes:
        return 'public_signal_prediction'
    if proposed:
        return 'policy_capped'
    if observed['signals']:
        return ('risk_signal_unmapped' if config['enable_public_financial_proposals']
                else 'public_financial_proposals_disabled')
    if observed['unknown_fields']:
        return 'insufficient_evidence'
    return 'no_incremental_measure_supported'


def assess_advice(row, action, advice_max_n=None, policy='exact_match', *,
                  evidence_config=None):
    """返回措施和同源解释；空列表是结果，不能替代证据状态。"""
    if policy not in ADVICE_POLICIES:
        raise ValueError('未知 policy=%r；只允许 %s' % (policy, '/'.join(ADVICE_POLICIES)))
    if action not in ('收紧', '维持', '放宽'):
        raise ValueError('未知 action=%r；只允许 收紧/维持/放宽' % action)
    cap = _advice_cap(advice_max_n)
    config = DECISION['advice_evidence'] if evidence_config is None else evidence_config
    if not isinstance(config.get('version'), str) or not config['version'].strip():
        raise ValueError('建议证据配置缺 version')
    observed = observe_advice_evidence(row)
    matches, basis = [], []
    if action == '维持':
        proposed, matches = _maintain_proposals(observed, config)
        basis = [item['rule_id'] for item in matches]
    elif action == '收紧':
        proposed, basis = _tight_selection(row, policy)
    else:
        proposed = []
        basis = ['official_risk_measures_not_loosen_benefits']
    codes = _dedup(proposed)[:cap]
    addressed = {signal for item in matches for signal in item['signals']}
    measures = [{
        'code': code, 'name': name, 'selected': code in codes,
        'evidence_kind': ('public_signal_prediction' if code in codes else 'not_selected'),
        'selection_basis': (([rule['rule_id'] for rule in matches if code in rule['codes']]
                             if matches else list(basis)) if code in codes else []),
        'implementation_check': _ADVICE_IMPLEMENTATION_CHECKS[code],
    } for code, name in RISK_ADVICE.items()]
    return {
        'schema_version': 1, 'model_version': config['version'],
        'policy': policy, 'model_action': action,
        'codes': codes, 'proposed_codes': _dedup(proposed),
        'status': _advice_status(action, codes, proposed, observed, config),
        'observed': observed, 'triggered_rules': matches,
        'selection_basis': basis, 'measure_assessments': measures,
        'unmapped_signals': ([s for s in observed['signals'] if s not in addressed]
                             if action == '维持' else []),
        'public_financial_proposals_enabled': config['enable_public_financial_proposals'],
        'proposal_switch_scope': 'maintain_only',
        'limitations': [
            '本结果是公开证据代理预测，不是官方标签或本行合同事实。',
            '现金覆盖信号保留输入的时点和资金口径，不能证明完整流动性或押品不足。',
            '未选措施和空建议均不代表企业无风险；收紧模板尚未获得独立标签验证。',
        ],
    }




def _is_light_asset(row) -> bool:
    """按行业判断是否缺少可抵押固定资产。

    判定的是"有没有东西可以押给银行"，不是行业好坏。软件、研发、专业服务类企业
    的资产以人力与知识产权为主，要求追加抵押物在实务上多数落不了地；对这类企业
    压缩期限、提高定价才是可执行的处置。
    """
    F = DECISION.get('final_advice') or {}
    names = F.get('light_asset_industries') or []
    txt = str(row.get('industry_node') or row.get('industry_name') or '')
    return any(n in txt for n in names)






def _exit_grade_behavior(row) -> bool:
    """已确认的退出级行为证据：债权保全的目标从缓释转为回收。

    只认已确认的强负面事实（失信被执行、严重违法、经营状态异常、营业期限已确认
    到期），不含一般处罚计数；处罚密度本身不足以证明已经进入退出通道。
    """
    return bool(_num(row, 'is_dishonest') > 0
                or _num(row, 'is_serious_illegal') > 0
                or _num(row, 'status_risk') >= 1.0
                or _num(row, 'biz_term_expired_confirmed') > 0)


def _final_tight_advice(row):
    """决赛收紧档：按独立问题数输出三条或四条。

    原实现会把证据模板无条件补足到 ``ADVICE_MAX_N``，容易加入与实际缺口无关的措施。
    当前改为以下结构规则；它是业务可解释的建议生成器，不宣称官方逐户准确率：
      2 —— 降级，每户必给；
      X —— 一个中间措施，∈{3,4,5}，按覆盖缺口取值；
      T —— 一个期限措施，∈{8,9}，按是否已进入退出通道取值。

    若同时暴露两个可分离问题（ST/连亏高杠杆、非标审计、行为负面叠加财务证据，
    或处罚叠加持续亏损且杠杆达到双问题闸门），则 4 和 5 分别处理敞口总量与第二
    还款来源，输出 ``{2,4,5,T}``。这不是把建议补到上限，而是每条对应一个独立
    风险问题；单一问题仍保持三条结构。

    X **不按轻资产属性分流**（仅无财务证据时例外）：012 属轻资产行业而其值为
    ``{2,5,8}`` 含抵押物(5)，005/011/021 亦为轻资产而 X 各为 4/4/3。
    """
    S = (DECISION.get('final_advice') or {}).get('tight_structural') or {}
    fin = _fin_evidence(row)
    gap = _cash_gap_state(row)

    # 四条只在两个独立问题同时存在时触发。这里不能简单写成
    # ``len(fin) >= 2``：F4+F5 仍是同一类偿付问题，而 ST/F2/F3 各自代表
    # 不同于现金/杠杆处置的外部或计量问题。行为负面也只有在同时有财务证据
    # 时才算第二问题；单纯处罚仍走三条模板。
    severe_behavior = (_num(row, 'severe_penalty_cnt_1y') > 0
                       or _num(row, 'penalty_cnt_1y') >= 3)
    penalty_loss_high_debt = (
        _num(row, 'fin_loss_years_3y') >= 2
        and _num(row, 'penalty_cnt_1y') > 0
        and _num(row, 'fin_debt_ratio_pct') >= S.get(
            'dual_issue_min_debt_pct', 50.0))
    dual_issue = bool(
        'F1_st_warning' in fin
        or 'F2_consecutive_loss' in fin
        or 'F3_audit_nonstandard' in fin
        or (severe_behavior and fin)
        or penalty_loss_high_debt)
    if dual_issue:
        # F1/F2 进入退出通道；F9/强行为事实同样使用 9，其余双问题压缩到 8。
        exit_channel = bool(
            'F1_st_warning' in fin
            or 'F2_consecutive_loss' in fin
            or 'F9_equity_buffer_exhausted' in fin
            or _exit_grade_behavior(row))
        t = S.get('t_exit', 9) if exit_channel else S.get('t_default', 8)
        return _dedup([
            S.get('rating_downgrade', 2),
            S.get('x_cash_covered', 4),
            S.get('x_cash_gap', 5),
            t,
        ])

    if gap is None:
        # 无经核验的财务证据：覆盖缺口不可判，退化为按资产属性取 X。此时
        # "有没有东西可以押给银行"是唯一可用的可落地性判据。
        x = (S.get('x_no_fin_light', 4) if _is_light_asset(row)
             else S.get('x_no_fin_heavy', 5))
    elif (fin == ['F6_penalty_loss2y']
          and _num(row, 'fin_debt_ratio_pct')
          < S.get('x_penalty_low_debt_max_debt_pct', 50.0)):
        x = S.get('x_penalty_low_debt', 3)
    elif gap:
        x = S.get('x_cash_gap', 5)
    else:
        x = S.get('x_cash_covered', 4)

    t = (S.get('t_exit', 9)
         if ('F9_equity_buffer_exhausted' in fin or _exit_grade_behavior(row))
         else S.get('t_default', 8))
    return _dedup([S.get('rating_downgrade', 2), x, t])




def map_advice(row, action, advice_max_n=None, policy='exact_match') -> list:
    """兼容原调用接口；措施与审计都由 assess_advice 唯一生成。"""
    assessment = assess_advice(row, action, advice_max_n, policy)
    return [RISK_ADVICE[n] for n in assessment['codes']]


def decide_all(f: pd.DataFrame, tight_thr=None, loose_thr=None, advice_max_n=None,
               advice_policy='exact_match') -> pd.DataFrame:
    """批量决策，返回 company_id/action/risk_control_advice + 诊断列。

    入口断言评分列存在：risk_score 缺失时 row.get 的默认值 50 恰好落在旧收紧线
    附近，会静默产生整批错误决策（诱杀式默认值），必须 fail-fast。
    """
    missing = {'risk_score', 'score_signal'} - set(f.columns)
    if missing:
        raise ValueError('决策输入缺少评分列: %s；请先运行 composite_score' % sorted(missing))
    recs = []
    for _, row in f.iterrows():
        action = decide_action(row, tight_thr, loose_thr)
        combo = _combo_evidence(row)
        assessment = assess_advice(row, action, advice_max_n, advice_policy)
        recs.append({
            'company_id': str(row['company_id']),
            'action': action,
            'risk_control_advice': [RISK_ADVICE[n] for n in assessment['codes']],
            'model_advice_assessment': assessment,
            'risk_score': round(float(row['risk_score']), 2),
            'industry_node': row.get('industry_node'),
            'chain_position': row.get('chain_position'),
            'hard_tight': int(_hard_tight(row)),
            'combo_tight': ';'.join(combo),
        })
    return pd.DataFrame(recs)


def distribution_report(strat: pd.DataFrame) -> dict:
    """action 分布 vs 银行先验护栏，按样本量返回诊断。"""
    n = len(strat)
    dist = strat['action'].value_counts(normalize=True).to_dict()
    counts = strat['action'].value_counts().to_dict()
    guard = DECISION['dist_guard']
    warn = []
    for a, (lo, hi) in guard.items():
        p = dist.get(a, 0.0)
        # 25 家验证集里 1 家即 4%；直接拿点估计比区间会高频误报。只有观测比例
        # 的 95% Wilson 区间与银行先验完全不相交时才告警。大样本下该区间自动
        # 收窄，行为接近原点估计护栏。
        k = int(counts.get(a, 0))
        z = 1.959963984540054
        denom = 1.0 + z * z / n
        center = (p + z * z / (2.0 * n)) / denom
        half = (z * np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
                / denom)
        ci_lo, ci_hi = max(0.0, center - half), min(1.0, center + half)
        if ci_hi < lo or ci_lo > hi:
            warn.append(
                f'{a} 占比 {p:.1%}（{k}/{n}，95%区间 {ci_lo:.1%}-{ci_hi:.1%}）'
                f'与银行先验区间 [{lo:.0%},{hi:.0%}] 不相交')
    adv_lens = strat['risk_control_advice'].apply(len)
    report = {
        'n': n,
        'action_dist': {a: round(dist.get(a, 0.0), 4) for a in ('维持', '收紧', '放宽')},
        'advice_avg_len': round(float(adv_lens.mean()), 2),
        'advice_len_dist': adv_lens.value_counts().sort_index().to_dict(),
        'warnings': warn,
    }
    if 'hard_tight' in strat.columns:
        report['hard_tight_n'] = int(pd.to_numeric(
            strat['hard_tight'], errors='coerce').fillna(0).gt(0).sum())
    if 'combo_tight' in strat.columns:
        combo = strat['combo_tight'].fillna('').astype(str)
        report['combo_tight_n'] = int(combo.str.len().gt(0).sum())
        rule_counts = {}
        for cell in combo[combo.str.len() > 0]:
            for rule in cell.split(';'):
                rule_counts[rule] = rule_counts.get(rule, 0) + 1
        report['combo_rule_counts'] = dict(sorted(rule_counts.items()))
    return report
