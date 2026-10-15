"""使用指定的原工程，只读对照公开案例；原工程不随仓库分发。"""
import argparse
import ast
from decimal import Decimal, InvalidOperation
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

from supply_chain_risk_core._console import configure_utf8_output
from supply_chain_risk_core.cli import example_document
from supply_chain_risk_core.credit import decision as public_decision, rules as public_rules, scoring as public_scoring
from supply_chain_risk_core.credit.deep_features import deep_features as public_deep
from supply_chain_risk_core.credit.workflow import _input_rows
from supply_chain_risk_core.credit.financial_features import listed_financial_features
from supply_chain_risk_core.financial_metrics import interest_coverage
from supply_chain_risk_core.stage2.projection import project_chain
from supply_chain_risk_core.study import _quantitative_case, analyze_study
from supply_chain_risk_core.validation import canonical_bytes


def load_selected(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise ValueError(f'原模块缺少所需函数：{path.name}')
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def compare(original_root):
    sys.dont_write_bytecode = True
    source = Path(original_root).resolve(strict=True) / 'src'
    sys.path.insert(0, str(source))
    original_rules = importlib.import_module('config')
    original_deep = importlib.import_module('deep_features')
    original_scoring = importlib.import_module('scoring')
    original_decision = importlib.import_module('decision')
    data = example_document()
    raw, finance = _input_rows(data['companies'], data['sources'], data['as_of'])
    current = public_deep(raw, data['as_of'])
    pd.testing.assert_frame_equal(current, original_deep.deep_features(raw, data['as_of']))
    expired = current['biz_term_expired'] > 0
    current['biz_term_expired_confirmed'] = (expired & (current['status_risk'] >= 1)).astype(int)
    current['biz_term_status_conflict'] = (expired & (current['status_risk'] < 1)).astype(int)
    current = listed_financial_features(current, finance, data['as_of'])
    scored = public_scoring.composite_score(current, include_policy_prior=False)
    pd.testing.assert_frame_equal(scored, original_scoring.composite_score(current, include_policy_prior=False))
    ours = public_decision.decide_all(scored, advice_policy='business_complete')
    theirs = original_decision.decide_all(scored, advice_policy='business_complete')
    pd.testing.assert_frame_equal(ours, theirs)
    constants = ('RISK_ADVICE', 'ADVICE_MAX_N', 'FIN_LOSS_YEAR_VALUES', 'INDUSTRY_POLICY', 'SCORE_WEIGHTS',
                 'ENTITY_SCORE', 'SIGNAL_SCORE', 'INDUSTRY_SCORE', 'REGION_SCORE', 'DECISION', 'ADVICE_MAP')
    for name in constants:
        if getattr(original_rules, name) != getattr(public_rules, name):
            raise AssertionError(f'规则常量不同：{name}')
    spec = importlib.util.spec_from_file_location('original_projection', source / 'stage2' / 'projection.py')
    projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projection)
    for chain in data['chains']:
        if canonical_bytes(project_chain(chain)) != canonical_bytes(projection.project_chain(chain)):
            raise AssertionError(f'链投影不同：{chain["id"]}')
    functions = {'number', 'fraction', 'ratio_pct', 'price_change_fraction', 'retained_cost_per_base_revenue',
                 'feed_cost_change', 'neutral_feed_ratio', 'board_transfer_costs', 'revenue_overlap_bounds'}
    scope = load_selected(source / 'stage2' / 'quantitative.py', functions,
                          {'Decimal': Decimal, 'InvalidOperation': InvalidOperation,
                           'HUNDRED': Decimal(100), 'NUMBER': re.compile(r'[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?')})
    expected = [scope['retained_cost_per_base_revenue'](**data['quantitative_cases'][0]['inputs']),
                scope['feed_cost_change'](**data['quantitative_cases'][1]['inputs']),
                scope['board_transfer_costs'](**data['quantitative_cases'][2]['inputs']),
                scope['revenue_overlap_bounds'](*(Decimal(data['quantitative_cases'][3]['inputs'][key]) for key in ('total', 'product', 'region')))]
    actual = [_quantitative_case(case)['result'] for case in data['quantitative_cases']]
    assert float(expected[0]) == actual[0]['retained_cost_per_100_revenue']
    assert float(expected[1]) == actual[1]['cost_change_pct']
    assert canonical_bytes(expected[2]) == canonical_bytes({key: value for key, value in actual[2].items() if key != 'unit'})
    assert tuple(map(float, expected[3])) == (actual[3]['lower'], actual[3]['upper'])
    scenario_names = {'_finite_number', 'validate_scenario_parameters', 'validate_demand_contribution_margin',
                      'cost_impact', '_demand_threshold', '_demand_verdict', 'demand_impact'}
    scenario = load_selected(source / 'transmission_impact.py', scenario_names,
                             {'math': math, 'np': np, 'interest_coverage': interest_coverage, 'DEFAULT_SHOCK': .1})
    report = analyze_study(data)
    first, second = data['companies'][0]['financials'], data['companies'][1]['financials']
    cost = scenario['cost_impact'](first['revenue_yi'], first['ebit_yi'], first['interest_expense_yi'], .44, .1, .3)
    demand = scenario['demand_impact'](second['revenue_yi'], second['ebit_yi'], second['interest_expense_yi'], .4, .2, contribution_margin=None)
    assert canonical_bytes(cost) == canonical_bytes(report['chains'][0]['scenario']['result'])
    assert canonical_bytes(demand) == canonical_bytes(report['chains'][1]['scenario']['result'])
    return {'status': 'PASS', 'companies_compared': len(data['companies']), 'rule_constants_compared': len(constants),
            'chain_projections_compared': len(data['chains']), 'quantitative_cases_compared': len(actual),
            'financial_scenarios_compared': 2,
            'scope': '合成案例的工商特征、评分、决策、规则常量、链投影与定量结果；不代表全历史输入等价'}


if __name__ == '__main__':
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.original_root), ensure_ascii=False))
