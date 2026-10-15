import copy
import itertools
import json
import math
import random
import unittest

import networkx as nx
from supply_chain_risk_core import (
    canonicalize_earnings, liquidity_metrics, compute_ebit, interest_coverage,
    downstream_of, upstream_of, transmission_paths, exposure_report,
    TraversalBudgetExceeded, credit_transmission_paths,
)
from supply_chain_risk_core.financial_metrics import CANONICAL_EBIT_BASIS


class StrictFinancialTests(unittest.TestCase):
    def test_migration_is_opt_in_and_auditable(self):
        legacy = {'ebit_yi': 8, 'finance_expense_yi': 3, 'interest_expense_yi': 2}
        before = copy.deepcopy(legacy)
        with self.assertRaisesRegex(ValueError, '旧版'):
            canonicalize_earnings(legacy)
        result = canonicalize_earnings(legacy, allow_legacy=True)
        self.assertEqual(result['total_profit_yi'], 5)
        self.assertEqual(result['ebit_yi'], 7)
        self.assertEqual(result['net_finance_ebit_proxy_yi'], 8)
        self.assertIn('rule', result['earnings_migration'])
        self.assertEqual(legacy, before)

    def test_public_arithmetic_rejects_bad_numbers(self):
        for bad in (None, '', True, math.inf, math.nan):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    compute_ebit(2, bad)
                with self.assertRaises(ValueError):
                    interest_coverage(2, bad)
        self.assertEqual(compute_ebit(0, 0), 0)
        self.assertIsNone(interest_coverage(0, 0))

    def test_explicit_missing_mode_retains_unknown(self):
        record = {'ebit_basis': CANONICAL_EBIT_BASIS, 'ebit_yi': None,
                  'total_profit_yi': 5, 'interest_expense_yi': None}
        with self.assertRaises(ValueError):
            canonicalize_earnings(record)
        self.assertTrue(math.isnan(canonicalize_earnings(record, allow_missing=True)['ebit_yi']))

    def test_book_liquidity_requires_opt_in(self):
        record = {'cash_yi': 5, 'short_term_debt_yi': 2}
        with self.assertRaisesRegex(ValueError, '历史账面'):
            liquidity_metrics(record)
        result = liquidity_metrics(record, allow_legacy=True)
        self.assertEqual(result['cash_for_debt_service_yi'], 5)
        self.assertEqual(result['liquidity_basis'], 'book_cash_availability_not_reviewed')
        for amount in (-1, None, math.inf, True):
            with self.assertRaises(ValueError):
                liquidity_metrics(dict(record, cash_yi=amount), allow_legacy=True)

    def test_incomplete_later_balance_never_silently_falls_back(self):
        amounts = dict(cash_restricted_yi=1, cash_earmarked_yi=0,
                       other_short_borrowings_yi=0, non_financing_deductions_yi=0)
        later = dict(amounts, company_id='synthetic-A', credit_code='synthetic-code',
                     balance_date='2026-03-31', pub_date='2026-04-30',
                     source_url='https://example.org/synthetic-later', pdf_sha256='b' * 64,
                     pdf_pages=[1], cash_yi=10, short_term_debt_yi=4, cash_restricted_yi=None)
        adjustment = dict(amounts, schema_version=2, company_id='synthetic-A', annual_pdf_sha256='a' * 64,
                          pub_date='2026-03-01', source_url='https://example.org/synthetic-annual',
                          pdf_pages=[1], subsequent_balance=later)
        record = dict(company_id='synthetic-A', credit_code='synthetic-code', fiscal_year=2025,
                      cash_yi=5, short_term_debt_yi=2, annual_pdf_sha256='a' * 64,
                      liquidity_adjustments=json.dumps(adjustment))
        with self.assertRaisesRegex(ValueError, '不完整'):
            liquidity_metrics(record, as_of='2026-06-30')
        result = liquidity_metrics(record, as_of='2026-06-30', allow_annual_fallback=True)
        self.assertEqual(result['cash_for_debt_service_yi'], 4)
        self.assertEqual(result['liquidity_balance_date'], '2025-12-31')
        self.assertEqual(result['liquidity_subsequent_source_sha256'], 'b' * 64)
        self.assertIn('subsequent_incomplete', result['liquidity_basis'])


def brute_paths(graph, source, hops, endpoints):
    """通过节点排列穷举候选，独立于被测 DFS 的遍历与剪枝。"""
    others = [node for node in graph if node != source]
    result = []
    for length in range(1, hops + 1):
        for tail in itertools.permutations(others, length):
            chain = (source, *tail)
            if any(node in endpoints for node in chain[1:-1]):
                continue
            if all(graph.has_edge(a, b) for a, b in zip(chain, chain[1:])):
                strength = math.prod(graph[a][b]['weight'] for a, b in zip(chain, chain[1:]))
                result.append((chain, strength))
    return result


class PathBudgetTests(unittest.TestCase):
    def test_seeded_small_graphs_match_independent_permutations(self):
        for seed in range(20):
            rng = random.Random(seed)
            graph = nx.DiGraph()
            graph.add_nodes_from(range(6))
            for a, b in itertools.product(range(6), repeat=2):
                if rng.random() < .3:
                    graph.add_edge(a, b, weight=rng.choice([0, .25, .5, 1]),
                                   dep='强', estimated=False, provenance='synthetic')
            for hops in (1, 2, 3):
                with self.subTest(seed=seed, hops=hops):
                    expected = {}
                    paths = brute_paths(graph, 0, hops, {3})
                    for chain, strength in paths:
                        expected[chain[-1]] = max(strength, expected.get(chain[-1], 0))
                    report = exposure_report(0, graph, hops, endpoint_only={3})
                    self.assertEqual(report['exposures'], expected)
                    self.assertEqual(report['emitted_paths'], len(paths))
                    self.assertEqual(upstream_of(0, graph.reverse(), hops, endpoint_only={3}), expected)
                    expected_paths = {tuple(reversed(chain)): strength for chain, strength in paths}
                    actual = transmission_paths(0, graph.reverse(), hops, top_k=1000,
                                                min_strength=0, endpoint_only={3})
                    self.assertEqual({tuple(p['nodes']): p['strength'] for p in actual}, expected_paths)

    def test_budget_exhaustion_raises_even_when_top_k_is_full(self):
        graph = nx.complete_graph(7, create_using=nx.DiGraph)
        nx.set_edge_attributes(graph, .5, 'weight')
        nx.set_edge_attributes(graph, '强', 'dep')
        nx.set_edge_attributes(graph, False, 'estimated')
        for call in (lambda: downstream_of(0, graph, 4, max_expansions=10),
                     lambda: transmission_paths(0, graph, 4, top_k=1, max_expansions=10)):
            with self.assertRaises(TraversalBudgetExceeded) as caught:
                call()
            self.assertEqual(caught.exception.examined_edges, 10)
            self.assertGreater(caught.exception.emitted_paths, 0)
        self.assertEqual(len(downstream_of(0, graph, 4, max_expansions=None)), 6)

    def test_exact_budget_boundary_and_input_immutability(self):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([('a', 'b', 0), ('b', 'c', 1)])
        before = copy.deepcopy(graph)
        self.assertEqual(downstream_of('a', graph, 2, max_expansions=2), {'b': 0, 'c': 0})
        self.assertTrue(nx.utils.graphs_equal(graph, before))
        with self.assertRaises(TraversalBudgetExceeded):
            downstream_of('a', graph, 2, max_expansions=1)
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                downstream_of('a', graph, max_expansions=value)


class FactsTests(unittest.TestCase):
    def edge(self):
        return dict(source='synthetic-debtor', target='synthetic-guarantor', relation='guarantee',
                    direction='risk_source_to_affected', valid_from='2026-01-01', valid_to='2026-12-31',
                    published_at='2026-01-02', source_url='https://example.org/synthetic-guarantee',
                    evidence_id='synthetic-evidence-1', estimated=False)

    def test_valid_fact_is_traceable_and_immutable(self):
        edge = self.edge()
        original = copy.deepcopy(edge)
        paths = credit_transmission_paths(edge['target'], [edge], as_of='2026-06-30')
        self.assertEqual(paths[0]['nodes'], [edge['source'], edge['target']])
        self.assertEqual(paths[0]['evidence'], original)
        paths[0]['evidence']['source'] = 'changed-output'
        self.assertEqual(edge, original)
        self.assertEqual(credit_transmission_paths(edge['source'], [edge], as_of='2026-06-30'), [])

    def test_missing_expired_unpublished_or_ambiguous_facts_fail(self):
        for field, value in [('source_url', ''), ('evidence_id', ''), ('estimated', None),
                             ('direction', 'unknown'), ('published_at', '2026-07-01'),
                             ('valid_to', '2026-06-29')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                credit_transmission_paths('synthetic-guarantor', [dict(self.edge(), **{field: value})], as_of='2026-06-30')
        for when in ('2025-12-31', '2026-01-01', '2027-01-01', None):
            with self.subTest(when=when), self.assertRaises(ValueError):
                credit_transmission_paths('synthetic-guarantor', [self.edge()], as_of=when)
        for when in ('2026-01-02', '2026-12-31'):
            self.assertEqual(len(credit_transmission_paths('synthetic-guarantor', [self.edge()], as_of=when)), 1)
