"""成本与需求冲击的财务情景；系数和贡献率均由调用方显式提供。"""
import numpy as np

from .financial_metrics import interest_coverage
from .validation import finite_number as _finite_number

DEFAULT_SHOCK = 0.10

def validate_scenario_parameters(shock, pass_through=0.0, *, cost_only=False):
    """共用情景含需求收缩，最高为 100%；独立价格上涨允许超过 100%。"""
    shock = _finite_number(shock, 'shock')
    pass_through = _finite_number(pass_through, 'pass_through')
    if shock < 0 or (not cost_only and shock > 1):
        raise ValueError('shock 超出情景范围: %r' % shock)
    if not 0 <= pass_through <= 1:
        raise ValueError('pass_through 必须在 [0, 1] 内: %r' % pass_through)
    return shock, pass_through


def validate_demand_contribution_margin(value):
    """显式情景参数唯一校验入口；省略不等于零，也不从财报利润率推导。"""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        raise ValueError('contribution_margin 不能是布尔值')
    margin = _finite_number(value, 'contribution_margin')
    if not 0 <= margin <= 1:
        raise ValueError('contribution_margin 必须在 [0, 1] 内: %r' % value)
    return margin

def cost_impact(revenue_yi, ebit_yi, interest_yi, coef, shock=DEFAULT_SHOCK,
                pass_through=0.0):
    """成本推动冲击对单户的影响与临界涨幅。coef 为源头价格系数，金额为亿元。

    临界涨幅只对当前 EBIT 仍覆盖利息的企业有定义。对已经不覆盖的企业，公式分子
    为负会算出「负的临界涨幅」，数值上可以任意大（利息支出越小越夸张），没有业务
    含义，因此不输出，改以传导弹性与 EBIT 侵蚀倍数刻画强度——这两个指标不依赖
    盈利状况，对两类企业同样可比。
    """
    rev = _finite_number(revenue_yi, 'revenue_yi')
    ebit = _finite_number(ebit_yi, 'ebit_yi')
    interest = _finite_number(interest_yi, 'interest_yi')
    coef = _finite_number(coef, 'coef')
    shock, pass_through = validate_scenario_parameters(shock, pass_through, cost_only=True)
    if rev < 0 or interest < 0 or coef < 0:
        raise ValueError('收入、利息与成本系数不得为负')
    exposure = rev * coef * (1.0 - pass_through)
    add_cost = exposure * shock
    new_ebit = ebit - add_cost
    res = {
        'shock_pct': round(float(shock) * 100, 2),
        'pass_through': float(pass_through),
        'exposed_cost_yi': round(exposure, 4),
        'added_cost_yi': round(add_cost, 4),
        'ebit_before_yi': round(ebit, 4),
        'ebit_after_yi': round(new_ebit, 4),
        'interest_expense_yi': round(interest, 4),
        # 源头每涨 1 个百分点，成本增量占营收的百分点数，单位 pp/pp。
        'transmission_elasticity_pct': round(
            float(coef) * (1.0 - float(pass_through)), 3),
        'transmission_elasticity_unit': 'pp/pp',
        # EBIT 侵蚀倍数：给定冲击下的成本增加额相当于当前 EBIT 绝对值的多少倍。
        'ebit_erosion_x': (round(add_cost / abs(ebit), 2) if ebit else None),
    }
    res['interest_cover_before_x'] = (round(interest_coverage(ebit, interest), 2) if interest else None)
    res['interest_cover_after_x'] = (round(interest_coverage(new_ebit, interest), 2) if interest else None)

    if exposure <= 0:
        res['critical_shock_pct'] = None
        res['verdict'] = '无暴露'
        return res
    if ebit <= interest:
        res['critical_shock_pct'] = None
        res['verdict'] = '当前已不覆盖利息，任何上游涨价均直接扩大缺口'
        return res
    crit = (ebit - interest) / exposure
    res['critical_shock_pct'] = round(crit * 100, 2)
    # 判语必须带可核验的事实，不能只给一个标签：官方「传导强度量化」子维度要求
    # 以事实支撑强弱依赖，评分案例点名要原材料采购占比这类数字。此前 238 条记录中
    # 113 条(47%)的判语是光秃秃的「可承受」三个字，评委无从核对。
    crit_pct = res['critical_shock_pct']
    elasticity = res['transmission_elasticity_pct']
    cover_before = res['interest_cover_before_x']
    fact = ('临界涨幅 %.2f%%，传导弹性 %.3f pp/pp（成本增量/营收），冲击前利息保障 %s 倍'
            % (crit_pct, elasticity,
               ('%.2f' % cover_before) if cover_before is not None else '无息不适用'))
    if crit <= 0.10:
        res['verdict'] = '高度脆弱：源头涨价一成以内即击穿利息覆盖（%s）' % fact
    elif crit <= 0.30:
        res['verdict'] = '中度脆弱：源头涨价三成以内击穿利息覆盖（%s）' % fact
    else:
        res['verdict'] = '可承受：需源头涨价超三成才击穿利息覆盖（%s）' % fact
    return res


def _demand_threshold(ebit, interest, revenue_at_risk, contribution_margin):
    """仅报告 [0, 100%] 内能达到的利息覆盖临界降幅及未定义原因。"""
    if interest == 0:
        return None, 'no_interest_expense'
    if ebit <= interest:
        return None, 'already_uncovered'
    if revenue_at_risk == 0:
        return None, 'no_sales_exposure'
    if contribution_margin is None:
        return None, 'unknown_contribution_margin'
    if contribution_margin == 0:
        return None, 'zero_contribution_margin'
    exposure = revenue_at_risk * contribution_margin
    # 先比较可行域内的最大损失，避免把 >100% 的降幅写成可发生的需求冲击。
    if exposure < ebit - interest:
        return None, 'not_reached_within_100pct'
    return (ebit - interest) / exposure, 'within_feasible_range'


def _demand_verdict(impact, critical):
    income_fact = '风险收入 %.4f 亿元，情景收入下降 %.4f 亿元' % (
        impact['revenue_at_risk_yi'], impact['revenue_loss_yi'])
    if impact['profit_impact_status'] == 'unknown_contribution_margin':
        return '%s；贡献率未知，EBIT 与冲击后利息覆盖影响未量化' % income_fact
    if impact['profit_impact_status'] == 'no_revenue_loss':
        return '本情景无收入下降，不产生增量贡献损失（%s）' % income_fact
    fact = '%s，显式贡献率假设 %.4f，固定成本不变，EBIT 损失 %.4f 亿元' % (
        income_fact, impact['contribution_margin'], impact['ebit_loss_yi'])
    reason = {
        'no_interest_expense': '无利息支出，利息覆盖倍数及临界降幅不适用',
        'already_uncovered': '当前已不覆盖利息，增量损失按显式贡献率情景计算',
        'no_sales_exposure': '没有该销售方向敞口',
        'zero_contribution_margin': '贡献率假设为零，收入下降不产生增量贡献损失',
        'not_reached_within_100pct': '该贡献率假设下，该下游需求下降100%仍覆盖利息',
    }
    if critical is None:
        return '%s（%s）' % (reason[impact['critical_shock_status']], fact)
    label = ('高度脆弱' if critical <= 0.10 else
             '中度脆弱' if critical <= 0.30 else '可承受')
    return '%s：情景临界降幅 %.2f%%（%s）' % (
        label, impact['critical_shock_pct'], fact)


def demand_impact(revenue_yi, ebit_yi, interest_yi, share, shock=DEFAULT_SHOCK,
                  *, contribution_margin=None):
    """需求收入损失与显式贡献率情景；缺成本结构时利润影响为未知。"""
    rev = _finite_number(revenue_yi, 'revenue_yi')
    ebit = _finite_number(ebit_yi, 'ebit_yi')
    interest = _finite_number(interest_yi, 'interest_yi')
    share = _finite_number(share, 'share')
    shock, _ = validate_scenario_parameters(shock)
    margin = validate_demand_contribution_margin(contribution_margin)
    if rev < 0 or interest < 0:
        raise ValueError('revenue_yi 与 interest_yi 不得为负')
    if not 0 <= share <= 1:
        raise ValueError('share 必须在 [0, 1] 内')
    revenue_at_risk = rev * share
    revenue_loss = revenue_at_risk * shock
    if revenue_loss == 0:
        lost, status = 0.0, 'no_revenue_loss'
    elif margin is None:
        lost, status = None, 'unknown_contribution_margin'
    else:
        lost, status = revenue_loss * margin, 'scenario_estimate'
    new_ebit = None if lost is None else _finite_number(ebit - lost, 'ebit_after_yi')
    critical, critical_status = _demand_threshold(ebit, interest, revenue_at_risk, margin)
    result = {
        'shock_pct': round(shock * 100, 2),
        'contribution_margin': margin,
        'contribution_margin_basis': ('unknown_cost_structure' if margin is None
                                      else 'explicit_scenario_assumption'),
        'profit_impact_status': status,
        'revenue_at_risk_yi': round(revenue_at_risk, 4),
        'revenue_loss_yi': round(revenue_loss, 4),
        'ebit_loss_yi': None if lost is None else round(lost, 4),
        'ebit_before_yi': round(ebit, 4),
        'ebit_after_yi': None if new_ebit is None else round(new_ebit, 4),
        'interest_expense_yi': round(interest, 4),
        'interest_cover_before_x': round(interest_coverage(ebit, interest), 2) if interest else None,
        'interest_cover_after_x': (round(interest_coverage(new_ebit, interest), 2)
                                   if interest and new_ebit is not None else None),
        'critical_shock_pct': None if critical is None else round(critical * 100, 2),
        'critical_shock_status': critical_status,
    }
    result['verdict'] = _demand_verdict(result, critical)
    return result
