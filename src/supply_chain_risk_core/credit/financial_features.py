"""定期报告特征沿用参赛算法；所有字段先由公开输入契约校验。"""
import numpy as np
import pandas as pd

from ..financial_metrics import canonicalize_earnings, canonicalize_loss_history, interest_coverage, liquidity_metrics

def _as_of_date(value):
    """统一自然日边界；当日任意时刻均视为截至当日可用。"""
    if value is None:
        raise ValueError('as_of 不能为空')
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('as_of 不能为 NaT')
    if stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)
    return stamp.normalize()

def _normalized_key(series, upper=False):
    """生成可关联键；空串/占位符不参与匹配，避免 ``None`` 同值误连。"""
    out = pd.Series(series, copy=False).astype('string').str.strip()
    missing = out.isna() | out.str.lower().isin(['', '-', 'nan', 'none', 'nat', 'null'])
    out = out.mask(missing)
    return out.str.upper() if upper else out

_LISTED_FIN_COLS = ('fin_loss_years_3y', 'fin_consecutive_loss_3y', 'fin_turn_to_loss',
                    'fin_debt_ratio_pct', 'fin_high_leverage', 'fin_st_in_window',
                    'fin_st_active', 'fin_audit_nonstandard', 'fin_has_record',
                    'fin_profit_positive', 'fin_profit_sharp_decline',
                     'fin_profit_uptrend', 'fin_guarantee_high',
                     'fin_interest_burden', 'fin_ebit_covers_interest',
                     'fin_interest_coverage_x', 'fin_interest_coverage_thin',
                     'fin_cash_covers_short_debt',
                     'fin_cumulative_loss_to_equity_ratio')
_FIN_UNKNOWN_DEFAULTS = dict.fromkeys(_LISTED_FIN_COLS, -1.0)
_FIN_UNKNOWN_DEFAULTS.update(fin_has_record=0.0)
# 资产负债率高杠杆线：银行贷后普遍以 70% 为制造业/服务业预警位，本样本 25 家中
# 有 8 家在此线以上，既不空档也不过半，作为二元档位阈值可用（2026-08-08）。
FIN_HIGH_LEVERAGE_PCT = 70.0
# 三年净利累计降幅超此比例视为盈利显著下滑；主动放宽授信需要盈利至少企稳。
FIN_SHARP_DECLINE_PCT = 20.0
# 对外担保总额/归母净资产达此线，交易所要求强制风险提示，属重大或有负债。
FIN_GUARANTEE_HIGH_PCT = 100.0
# 货币资金/短期有息负债低于此倍数视为短期偿付缺口。取 1.0（自有现金不足以覆盖
# 一年内到期有息负债）而非银行常用的 0.5 预警位：本层只作为"付息能力不足"的
# 叠加条件，不单独收紧，故用最宽松的自洽定义，把区分力交给付息判据。
FIN_CASH_SHORT_DEBT_MIN_X = 1.0
# 利息保障倍数审慎线。**刻意不按本样本标定**：25 户全为 A 股上市公司，任何按其
# 分布取的分位点都会在决赛新验证集的企业画像下失效（初赛的 is_core 就是这样用
# 训练集 P90 标定后在验证集上退化成 25/25 全命中的）。这里取银行业通用的
# 2.0x —— 1.5x 为常见警戒位、3.0x 为安全位，2.0x 是审慎中位。本样本下命中
# 13 户（含 015 的 1.59x、020 的 1.38x），既不空档也不过半。
# 该列不参与 F8 判据，只做风险刻画与区分力自检，不改变既有 action。
FIN_INTEREST_COVERAGE_MIN_X = 2.0
# 保障倍数的截断绝对值，同时充当"无付息负担"的填充值（语义：无限覆盖）。
# 取 999 远高于本样本实际最大值 209x，两种含义在风险方向上一致，不会误判。
FIN_COVERAGE_CLIP = 999.0

def listed_financial_features(firms: pd.DataFrame, listed_fin, as_of=None):
    """把上市公司定期报告口径的偿付能力恶化并入特征矩阵。

    工商登记字段对上市公司无区分度（全部"开业"、无失信、无经营异常），真实信用
    差异只出现在定期报告：连亏年数、当年转亏、资产负债率、窗口内风险警示、
    非标审计意见。这些与处罚记录语义不同——处罚是行为负面，财务恶化是偿付能力
    负面，因此单列一组 ``fin_*`` 列，不与 penalty_cnt 系列混用。

    匹配优先 company_id，其次统一社会信用代码；调用前须通过同批主体校验。
    未知财务值用-1；利息保障倍数可真实为负，须结合 fin_ebit_covers_interest
    的三态值判断是否已观测。fin_has_record 只表示记录在场，不代表字段齐全。
    """
    f = firms.copy()
    for column, default in _FIN_UNKNOWN_DEFAULTS.items():
        f[column] = default
    if listed_fin is None or len(listed_fin) == 0:
        return f

    records = [canonicalize_loss_history(canonicalize_earnings(row, allow_missing=True))
               for row in listed_fin.to_dict('records')]
    fin = pd.DataFrame([dict(row, **liquidity_metrics(row, as_of=as_of))
                        for row in records], index=listed_fin.index)
    fin['_id'] = fin['company_id'].astype(str).str.strip()
    fin['_code'] = _normalized_key(fin.get('credit_code'), upper=True)
    by_id = fin.dropna(subset=['_id']).drop_duplicates('_id').set_index('_id')
    by_code = fin.dropna(subset=['_code']).drop_duplicates('_code').set_index('_code')

    firm_id = f['company_id'].astype(str).str.strip()
    firm_code = _normalized_key(
        f.get('credit_code', pd.Series(index=f.index, dtype='object')), upper=True)

    def pick(column):
        hit = pd.to_numeric(firm_id.map(by_id[column]), errors='coerce')
        if column in by_code:
            hit = hit.fillna(pd.to_numeric(firm_code.map(by_code[column]), errors='coerce'))
        return hit.where(np.isfinite(hit))

    def flag(known, condition):
        return pd.Series(np.where(known, condition.astype(int), -1), index=f.index)

    def _pick_date(column):
        """日期型证据列；列缺失（老版本文件）时整列 NaT，语义为"未发生"。"""
        if column not in by_id.columns and column not in by_code.columns:
            return pd.Series(pd.NaT, index=f.index)
        hit = pd.to_datetime(firm_id.map(by_id[column]), errors='coerce') \
            if column in by_id.columns else pd.Series(pd.NaT, index=f.index)
        if column in by_code.columns:
            hit = hit.fillna(
                pd.to_datetime(firm_code.map(by_code[column]), errors='coerce'))
        return hit

    matched = firm_id.isin(by_id.index) | firm_code.isin(by_code.index)
    f['fin_has_record'] = matched.astype(int)
    loss_years = pick('loss_years_3y')
    f['fin_loss_years_3y'] = loss_years.fillna(-1.0)
    turn_to_loss = pick('turn_to_loss')
    f['fin_turn_to_loss'] = flag(turn_to_loss.isin([0, 1]), turn_to_loss > 0)
    # ST 是可撤销状态：窗口内被实施过风险警示，与基准日仍处警示中不是一回事。
    # 已在 as_of 前撤销的企业刚通过交易所核查摘帽（恶化情形已消除），若仍按
    # "当前被 ST"处置会开出提前收回融资，与事实相反。故拆成两列：
    # fin_st_in_window 保留"是否发生过"的事实，fin_st_active 表示"此刻是否仍在"。
    st_flag = pick('st_flag_in_window')
    st_known = st_flag.isin([0, 1])
    st_ever = st_flag > 0
    revoked = _pick_date('st_revoked_date')
    # as_of 缺省时无法判断"撤销是否已发生"，此时退回窗口事实（不因缺基准日而擅自
    # 放松）；撤销日晚于 as_of 同样按仍在警示中处理，避免使用未来信息。
    if as_of is None:
        still_st = st_ever
    else:
        still_st = st_ever & ~(revoked.notna() & (revoked <= _as_of_date(as_of)))
    f['fin_st_in_window'] = flag(st_known, st_ever)
    f['fin_st_active'] = flag(st_known, still_st)
    audit = pick('audit_nonstandard')
    f['fin_audit_nonstandard'] = flag(audit.isin([0, 1]), audit > 0)
    debt = pick('debt_ratio_pct')
    debt = debt.where(debt >= 0)
    f['fin_debt_ratio_pct'] = debt.clip(0, 200).fillna(-1.0)
    f['fin_consecutive_loss_3y'] = flag(loss_years.notna(), loss_years >= 3)
    f['fin_high_leverage'] = flag(debt.notna(), debt >= FIN_HIGH_LEVERAGE_PCT)
    # 盈利趋势与或有负债：只服务"放宽"档的正向证据判据，不参与收紧。收紧看的是
    # 亏损/杠杆/风险警示这类已实现的恶化；能否主动扩大授信则要看盈利是否至少
    # 企稳、担保是否失控——两者证据方向不同，不可互相替代。
    profit = pick('net_profit_yi')
    prev2 = pick('net_profit_prev2_yi')
    f['fin_profit_positive'] = flag(profit.notna(), profit > 0)
    # 起点非正时"降幅"无定义（-1 亿→-0.5 亿不是下滑 50%），该档只在起点为正时判定。
    base_ok = prev2.notna() & (prev2 > 0)
    decline = pd.Series(0.0, index=f.index)
    decline[base_ok] = (prev2[base_ok] - profit[base_ok]) / prev2[base_ok] * 100.0
    f['fin_profit_sharp_decline'] = flag(
        profit.notna() & prev2.notna(), base_ok & (decline >= FIN_SHARP_DECLINE_PCT))
    # 盈利上升趋势：当期高于两年前且未从上一期回落。这里只生成特征，是否作为
    # 放宽前提由 config.fin_loosen.require_profit_uptrend 明确控制。
    prev = pick('net_profit_prev_yi')
    f['fin_profit_uptrend'] = flag(
        profit.notna() & prev2.notna() & prev.notna(),
        (profit > 0) & (prev > 0) & (prev2 > 0) & (profit > prev2) & (profit >= prev))
    guarantee = pick('guarantee_to_equity_pct')
    guarantee = guarantee.where(guarantee >= 0)
    f['fin_guarantee_high'] = flag(guarantee.notna(), guarantee >= FIN_GUARANTEE_HIGH_PCT)
    # 付息能力与短期偿付缺口。F1–F7 全部测"利润表已实现的恶化"（亏损年数/ST/
    # 非标审计/负债率），没有一条问"自身盈利够不够付息"，后果是负债率 61.34%、
    # EBIT 已经付不出利息的企业逃过全部判据（F4/F5 卡 70% 杠杆线、F6 要处罚、
    # F2 要连亏三年）。这里补上该维度，三列语义各自独立、都不单独成档：
    #   fin_interest_burden        存在实质付息负担 = 年报利息费用 > 0。
    #   fin_ebit_covers_interest   息税前利润能否覆盖利息费用（保障倍数 ≥ 1x）。
    #   fin_interest_coverage_x    利息保障倍数本身，连续值，供风险刻画与区分力自检。
    #   fin_interest_coverage_thin 保障倍数低于审慎线，不单独成档，见下方阈值说明。
    #   fin_cash_covers_short_debt 货币资金能否覆盖一年内到期有息负债。
    # 未知不触发负面规则，也不能作为放宽或已覆盖的正向依据。
    #
    # 两支路只消费统一入口的规范 EBIT；旧净财务费用代理已在入口显式迁移。
    ebit = pick('ebit_yi')
    interest_expense = pick('interest_expense_yi')
    cash = pick('cash_for_debt_service_yi')
    short_debt = pick('short_financing_debt_yi')
    interest_expense = interest_expense.where(interest_expense >= 0)
    cash = cash.where(cash >= 0)
    short_debt = short_debt.where(short_debt >= 0)
    burden = interest_expense.notna() & (interest_expense > 0)
    no_interest = interest_expense.eq(0)
    f['fin_interest_burden'] = flag(interest_expense.notna(), burden)
    covers = pd.Series(-1, index=f.index)
    covers[no_interest] = 1
    known = burden & np.isfinite(ebit)
    covers[known] = (ebit[known] >= interest_expense[known]).astype(int)
    f['fin_ebit_covers_interest'] = covers.astype(int)
    # 利息保障倍数。无付息负担时该比率不存在，留 0 会与"倍数极低"混淆，故填
    # +FIN_COVERAGE_CLIP（语义为"无限覆盖"，无债即无付息压力）。消费方判断
    # 付息风险必须先看 fin_interest_burden，不能直接对本列取阈值。
    coverage = pd.Series(np.nan, index=f.index)
    coverage[no_interest] = float(FIN_COVERAGE_CLIP)
    coverage[known] = pd.Series(
        [interest_coverage(e, i) for e, i in zip(ebit[known], interest_expense[known])],
        index=ebit[known].index, dtype=float).clip(
            lower=-FIN_COVERAGE_CLIP, upper=FIN_COVERAGE_CLIP)
    # -1 是输出契约中的显式未知哨兵；实际负保障倍数仍保留真实值，并由
    # fin_ebit_covers_interest 的未知(-1)/已观测(0或1)区分，不把缺失填成无限覆盖。
    f['fin_interest_coverage_x'] = coverage.fillna(-1.0)
    f['fin_interest_coverage_thin'] = flag(
        known | no_interest, known & (coverage < FIN_INTEREST_COVERAGE_MIN_X))
    cash_covers = pd.Series(-1, index=f.index)
    # 短期有息负债为 0 的企业无短期偿付压力，恒视为已覆盖。
    cash_covers[short_debt.eq(0)] = 1
    known_cash = cash.notna() & short_debt.notna() & (short_debt > 0)
    cash_covers[known_cash] = (
        cash[known_cash] >= short_debt[known_cash] * FIN_CASH_SHORT_DEBT_MIN_X
    ).astype(int)
    f['fin_cash_covers_short_debt'] = cash_covers.astype(int)
    # 权益缓冲：只在三期净利与归母净资产均已披露、且净资产为正时计算。
    # 缺字段保持未知，不命中权益耗尽。
    equity = pick('equity_parent_yi')
    known_equity = (profit.notna() & prev.notna() & prev2.notna()
                    & equity.notna() & (equity > 0))
    cumulative_loss = (-(profit + prev + prev2)).clip(lower=0.0)
    loss_to_equity = pd.Series(np.nan, index=f.index)
    loss_to_equity[known_equity] = (
        cumulative_loss[known_equity] / equity[known_equity])
    f['fin_cumulative_loss_to_equity_ratio'] = loss_to_equity.clip(0.0, 1000.0).fillna(-1.0)
    # 匹配为空时函数会在上方提前返回，匹配存在但不属于当前企业时会走完整计算。
    # 两条路径必须产生相同的数值列类型，否则同一企业仅因输入多了一条无关记录就
    # 被判为特征矩阵发生变化，破坏跨批次/重排不变量。
    for column in _LISTED_FIN_COLS:
        f[column] = pd.to_numeric(f[column], errors='coerce').astype('float64')
    return f
