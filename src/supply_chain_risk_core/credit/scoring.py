# -*- coding: utf-8 -*-
"""银行评分卡式风险打分引擎（绝对刻度 0-100）。

与 v1（min-max 相对分数）的根本区别：
- 每个子分基于**绝对业务规则**（注册资本分段、实缴率档位、参保条件化等），
  不做数据集内归一化 → 同一家企业在任何数据集里分数相同，阈值跨数据集稳定，
  验证集规模/列缺失不会扭曲输出分布（v1 缺列时"放宽"飙到68%的根因修复）。
- 分数体系对齐银行授信评分卡方法论（主体资质/风险信号/行业/区域四维），
  每一分都可向评审解释业务依据（决赛"可落地性"评分点）。

四个子分与综合分全部保留在输出中，供 decision 层、稳定性评估与决赛报告使用。
"""
import numpy as np
import pandas as pd

from .rules import (ENTITY_SCORE, SIGNAL_SCORE, INDUSTRY_SCORE, REGION_SCORE,
                    SCORE_WEIGHTS, INDUSTRY_POLICY)

# 制造业关键词（参保条件化判断：制造业零参保比贸易/建筑更异常）。
# 国标行业名多为"××制品业/纺织业/造纸…"而不含"制造"字样，须一并覆盖，
# 否则这些行业既不算制造也享不到贸易折扣，语义悬空（2026-07-25 review 修复）。
_MFG_KW = ('制造', '加工', '冶炼', '采选', '开采', '生产', '制品', '纺织',
           '服饰', '制鞋', '造纸', '印刷', '家具')
_TRADE_KW = ('批发', '零售', '建筑', '装饰', '安装', '房地产')


def _num_col(f: pd.DataFrame, column: str, fill=0.0) -> pd.Series:
    """数值列安全读取：列缺失返回等长填充 Series，存在则 to_numeric+fillna。

    `pd.to_numeric(f.get(col))` 在整列缺失时返回 numpy 标量，后续 .fillna 会
    抛 AttributeError——与"列缺失时不触发"的设计承诺矛盾，此处统一守卫。
    """
    if column not in f.columns:
        return pd.Series(fill, index=f.index)
    return pd.to_numeric(f[column], errors='coerce').fillna(fill)


def _bin_score(value, bins):
    """分段打分：bins=[(上界, 分值), ...] 升序，取第一个 value < 上界的分值。"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0
    for ub, sc in bins:
        if value < ub:
            return sc
    return bins[-1][1]


def _is_mfg(industry):
    s = str(industry)
    return any(k in s for k in _MFG_KW) and not any(k in s for k in _TRADE_KW)


def _is_trade_or_construction(industry):
    """只有明确贸易/建筑/地产行业适用零参保折扣，未知行业不自动享受减半。"""
    s = str(industry)
    return any(k in s for k in _TRADE_KW)


def entity_score(f: pd.DataFrame) -> pd.Series:
    """主体资质分：规模、资本充实度、年限、用工、治理结构。"""
    P = ENTITY_SCORE
    s = pd.Series(float(P['base']), index=f.index)

    def numeric(column, default=np.nan):
        if column not in f.columns:
            return pd.Series(default, index=f.index, dtype='float64')
        return pd.to_numeric(f[column], errors='coerce')

    reg = numeric('reg_capital_wan')
    reg = reg.where(reg >= 0)
    s += reg.apply(lambda v: _bin_score(v, P['reg_capital_bins']))

    # 资本充实度逐行判断：明确披露才分档，未披露一律中性。
    # 不使用当前批次披露率开关，保证同一企业在单行/子集/全量输入中的分数一致。
    ratio = numeric('paid_in_ratio')
    # 已披露但实缴率无法计算（注册资本为0/非法 → ratio 为 NaN 或上游 -1 哨兵）时
    # 不能落到任何分档：要求 ratio 为 [0,∞) 的有效数值才分档（P2-2；-1 哨兵会被
    # ratio<=0 误判成"零实缴"最差档，2026-07-25 review 修复）。
    disclosed = (numeric('paid_in_disclosed', 0).fillna(0) > 0) & ratio.notna() & (ratio >= 0)
    paid_sc = np.select(
        [ratio <= 0, ratio < 0.5, ratio < 0.95],
        [P['paid_ratio']['zero'], P['paid_ratio']['low'], P['paid_ratio']['mid']],
        default=P['paid_ratio']['full'])
    s += np.where(disclosed, paid_sc, 0)

    age = numeric('company_age_years')
    age = age.where(age >= 0)
    s += age.apply(lambda v: _bin_score(v, P['age_bins']))

    # 参保：制造业全额计分，贸易/建筑类"零参保"惩罚减半（行业用工惯例）
    ins = numeric('insured_n', -1).fillna(-1)
    industry = f.get('industry_node', f.get('industry', pd.Series('', index=f.index)))
    is_mfg = industry.apply(_is_mfg)
    is_trade = industry.apply(_is_trade_or_construction)
    ins_sc = ins.apply(lambda v: _bin_score(v if v >= 0 else np.nan, P['insured_bins_mfg']))
    discount = np.where(is_trade & (ins_sc > 0), P['insured_zero_trade_discount'], 1.0)
    s += ins_sc * discount

    g = P['governance']
    s += (numeric('is_sole_natural_person', 0).fillna(0) * g['sole_natural']
          + numeric('is_corp_sole', 0).fillna(0) * g['corp_sole']
          + numeric('is_share_co', 0).fillna(0) * g['share']
          + numeric('is_foreign', 0).fillna(0) * g['foreign']
          + numeric('is_state_owned', 0).fillna(0) * g['state'])

    s += (numeric('has_website', 0).fillna(0) * P['has_website']
          + numeric('addr_industrial', 0).fillna(0) * P['addr_industrial']
          + numeric('name_tech', 0).fillna(0) * P['name_tech']
          + numeric('name_group', 0).fillna(0) * P['name_group'])
    s += (numeric('addr_office_only', 0).fillna(0) * is_mfg
          * P['addr_office_only_mfg'])
    return s.clip(0, 100)


def signal_score(f: pd.DataFrame) -> pd.Series:
    """风险信号分：失信/异常/到期/处罚/空壳/变更异动。命中即加分，累加封顶。"""
    P = SIGNAL_SCORE
    def z(column):
        if column not in f.columns:
            return pd.Series(0.0, index=f.index)
        return pd.to_numeric(f[column], errors='coerce').fillna(0)
    s = pd.Series(0.0, index=f.index)
    s += z('is_dishonest') * P['dishonest']
    s += z('is_serious_illegal') * P['serious_illegal']
    s += (z('status_risk') >= 1.0) * P['status_abnormal']
    # 注：confirmed_expired 定义上蕴含 status_risk>=1（deep_features 只在状态异常
    # 时确认到期），此时 status_abnormal(100)+biz_term_expired(70) 同时累加并被
    # clip(100) 封顶——有意的同向冗余，不构成额外分数（2026-07-26 review 声明）。
    confirmed_expired = z('biz_term_expired_confirmed') > 0
    conflict_expired = z('biz_term_status_conflict') > 0
    s += confirmed_expired * P['biz_term_expired']
    s += conflict_expired * P.get('biz_term_expired_conflict', 15)
    # 强处罚是一般处罚的子集（features.penalty_features 中 severe⊆penalty）；
    # 一般层只计"非强"处罚，避免单条强处罚被 20(一般)+70(强)=90 重复计分。
    severe_1y = z('severe_penalty_cnt_1y')
    normal_1y = (z('penalty_cnt_1y') - severe_1y).clip(lower=0)
    s += (normal_1y * P['penalty_1y_each']).clip(upper=P['penalty_1y_cap'])
    s += (severe_1y * P['severe_penalty_1y_each']).clip(
        upper=P['severe_penalty_1y_cap'])
    s += (z('enforcement_cnt_1y') * P['enforcement_1y_each']).clip(
        upper=P['enforcement_1y_cap'])
    s += (z('business_abnormal_cnt_1y') * P['business_abnormal_1y_each']).clip(
        upper=P['business_abnormal_1y_cap'])
    s += z('shell_flag') * P['shell_flag']
    s += (z('shell_score') >= 4) * P['shell_score_ge4_extra']
    s += z('annual_addr_missing') * P['annual_addr_missing']
    s += z('addr_mismatch_strong') * P['addr_mismatch_strong']
    s += z('rename_recent_change') * P['rename_recent_change']
    s += z('legalrep_multi_firm_same_city') * P['legalrep_multi_firm_same_city']
    # 纸面资本：大额注册 + 明确披露的零实缴（列缺失时不触发，避免验证集缺列误判）。
    # ratio 显式要求 ==0：-1 哨兵（披露但无法计算）不得当零实缴触发——与
    # entity_score 的 disclosed 判据同口径（2026-07-26 review，同坑第二处）。
    disclosed = z('paid_in_disclosed') > 0
    paper_ratio = _num_col(f, 'paid_in_ratio', 1)
    paper = (_num_col(f, 'reg_capital_wan', 0) >= 1000) & \
            (paper_ratio == 0) & disclosed
    s += paper * P['paper_capital']
    return s.clip(0, 100)


def industry_score(f: pd.DataFrame, include_policy_prior=True, include_external_policy=True) -> pd.Series:
    """行业分：工行行业政策倾向（慢变量）+ 2026H1 大宗/政策/事件冲击（快变量）。"""
    P = INDUSTRY_SCORE
    s = pd.Series(float(P['base']), index=f.index)

    node = f.get('industry_node', f.get('industry', pd.Series(None, index=f.index)))
    tier = node.map(lambda n: INDUSTRY_POLICY.get(str(n), 0))
    if include_policy_prior:
        s += tier.map(P['policy_tier'])

    cost = _num_col(f, 'ind_cost_shock')
    s += cost.apply(lambda v: _bin_score(v, P['cost_shock_bins']))

    if include_external_policy:
        pol = _num_col(f, 'ind_policy_score')
        s += np.where(pol > 0, (pol * P['policy_score_pos_each']).clip(P['policy_score_pos_cap']),
                      (-pol * P['policy_score_neg_each']).clip(upper=P['policy_score_neg_cap']))

    ev = _num_col(f, 'ind_event_score')
    s += np.where(ev < 0, (-ev * P['event_neg_each']).clip(upper=P['event_neg_cap']), 0)

    ex = _num_col(f, 'export_shock')
    s += np.where(ex > 0, P['export_shock_neg'], np.where(ex < 0, P['export_shock_pos'], 0))
    return s.clip(0, 100)


def region_score(f: pd.DataFrame) -> pd.Series:
    """区域分：省级增速 + 城市能级 + 集聚度；广东出口型企业对冲修正。"""
    P = REGION_SCORE
    s = pd.Series(float(P['base']), index=f.index)

    gdp = pd.to_numeric(f['region_gdp_growth'], errors='coerce') if 'region_gdp_growth' in f.columns else pd.Series(float('nan'), index=f.index)
    s += gdp.apply(lambda v: _bin_score(v, P['gdp_bins']) if pd.notna(v) else 0)

    is_gd = f.get('region', pd.Series('', index=f.index)).astype(str).str.contains('广东', na=False)
    is_exp = _num_col(f, 'scope_import_export') > 0
    s += (is_gd & is_exp) * P['guangdong_export_offset']

    ct = _num_col(f, 'city_tier', 4).astype(int)
    s += ct.map(P['city_tier']).fillna(0)

    dc = _num_col(f, 'district_firm_cnt')
    s += (dc >= 200) * P['district_cluster_ge200']
    return s.clip(0, 100)


def composite_score(f: pd.DataFrame, include_policy_prior=True, include_external_policy=True) -> pd.DataFrame:
    """四维子分 + 加权综合分（0-100 绝对刻度）。返回带 score_* 列的副本。"""
    out = f.copy()
    out['score_entity'] = entity_score(f)
    out['score_signal'] = signal_score(f)
    out['score_industry'] = industry_score(
        f, include_policy_prior=include_policy_prior,
        include_external_policy=include_external_policy)
    out['score_region'] = region_score(f)
    W = SCORE_WEIGHTS
    out['risk_score'] = (W['entity'] * out['score_entity']
                         + W['signal'] * out['score_signal']
                         + W['industry'] * out['score_industry']
                         + W['region'] * out['score_region']).clip(0, 100)
    return out
