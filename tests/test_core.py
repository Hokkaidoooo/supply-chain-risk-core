"""用手算反例验证公开模块；不依赖企业数据、赛务材料或网络。"""
from pathlib import Path
import json
import math
import tempfile
import unittest

import networkx as nx

from supply_chain_risk_core.financial_metrics import (
    CANONICAL_EBIT_BASIS,
    canonicalize_earnings,
    interest_coverage,
)
from supply_chain_risk_core.submission_contract import SubmissionContractError, load_submission_contract
from supply_chain_risk_core.supply_chain import (
    downstream_of,
    upstream_of,
    transmission_paths,
    credit_transmission_paths,
)


class PropagationTests(unittest.TestCase):
    def test_diamond_uses_strongest_path_not_sum(self):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([
            ("A", "B", 0.5), ("B", "D", 0.4),
            ("A", "C", 0.3), ("C", "D", 0.8),
        ])
        result = downstream_of("A", graph, max_hops=2)
        self.assertAlmostEqual(result["D"], 0.24)
        self.assertNotAlmostEqual(result["D"], 0.44)
        self.assertAlmostEqual(upstream_of("D", graph, max_hops=2)["A"], 0.24)

    def test_hop_bound_does_not_reach_a_third_edge(self):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([("A", "B", 0.5), ("B", "C", 0.5), ("C", "D", 0.5)])
        self.assertEqual(downstream_of("A", graph, max_hops=1), {"B": 0.5})
        self.assertNotIn("D", downstream_of("A", graph, max_hops=2))

    def test_cycle_does_not_revisit_source(self):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([("A", "B", 0.5), ("B", "A", 0.8), ("B", "C", 0.4)])
        result = downstream_of("A", graph, max_hops=8)
        self.assertEqual(set(result), {"B", "C"})
        self.assertAlmostEqual(result["C"], 0.2)

    def test_unknown_node_is_no_graph_coverage(self):
        graph = nx.DiGraph()
        graph.add_edge("A", "B", weight=0.5)
        self.assertEqual(downstream_of("unknown", graph), {})
        self.assertEqual(upstream_of(None, graph), {})

    def test_aggregate_endpoint_is_not_a_fictional_bridge(self):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([("A", "aggregate", 0.8), ("aggregate", "B", 0.7)])
        self.assertEqual(downstream_of("A", graph, endpoint_only={"aggregate"}), {"aggregate": 0.8})

    def test_invalid_model_parameters_fail_explicitly(self):
        graph = nx.DiGraph()
        graph.add_edge("A", "B", weight=1.1)
        with self.assertRaises(ValueError):
            downstream_of("A", graph)
        graph["A"]["B"]["weight"] = 0.5
        with self.assertRaises(ValueError):
            downstream_of("A", graph, max_hops=0)

    def test_path_report_does_not_assume_estimation_status(self):
        graph = nx.DiGraph()
        graph.add_edge("A", "B", weight=0.5, dep="强")
        with self.assertRaisesRegex(ValueError, "estimated"):
            transmission_paths("B", graph)

    def test_cost_path_keeps_direction_and_evidence(self):
        graph = nx.DiGraph()
        graph.add_edge("原料", "加工", weight=0.5, dep="强", provenance="synthetic", estimated=False)
        graph.add_edge("加工", "终端", weight=0.4, dep="弱", provenance="synthetic", estimated=True)
        paths = transmission_paths("终端", graph, max_hops=2)
        path = next(item for item in paths if item["source"] == "原料")
        self.assertEqual(path["nodes"], ["原料", "加工", "终端"])
        self.assertAlmostEqual(path["strength"], 0.2)
        self.assertEqual(path["mechanism"], "cost_push")
        self.assertTrue(path["contains_estimated_edge"])
        self.assertEqual(path["edge_provenance"], ["synthetic", "synthetic"])

    def test_credit_does_not_invent_factual_edges(self):
        self.assertEqual(credit_transmission_paths("A"), [])
        with self.assertRaises(ValueError):
            credit_transmission_paths("A", [{"source": "B", "target": "A"}])


class FinancialTests(unittest.TestCase):
    def record(self):
        return {
            "ebit_basis": CANONICAL_EBIT_BASIS,
            "total_profit_yi": 1.2,
            "interest_expense_yi": 0.3,
            "ebit_yi": 1.5,
        }

    def test_canonical_basis_is_consistent_and_immutable(self):
        record = self.record()
        before = dict(record)
        result = canonicalize_earnings(record, allow_legacy=False)
        self.assertEqual(record, before)
        self.assertAlmostEqual(result["interest_cover_x"], 5.0)
        self.assertIsNot(result, record)

    def test_disagreeing_ebit_is_rejected(self):
        record = self.record()
        record["ebit_yi"] = 3
        with self.assertRaisesRegex(ValueError, "不一致"):
            canonicalize_earnings(record, allow_legacy=False)

    def test_missing_is_not_zero(self):
        record = self.record()
        del record["interest_expense_yi"]
        with self.assertRaisesRegex(ValueError, "缺失"):
            canonicalize_earnings(record, allow_legacy=False, allow_missing=False)

    def test_unversioned_input_is_not_silently_canonical(self):
        record = self.record()
        del record["ebit_basis"]
        with self.assertRaisesRegex(ValueError, "旧版"):
            canonicalize_earnings(record, allow_legacy=False)

    def test_zero_interest_is_undefined_not_infinity(self):
        self.assertIsNone(interest_coverage(1.5, 0))

    def test_negative_and_nonfinite_interest_are_rejected(self):
        for interest in (-1, math.inf, math.nan):
            with self.subTest(interest=interest), self.assertRaises(ValueError):
                interest_coverage(1.5, interest)

    def test_boolean_financial_amount_is_rejected(self):
        record = self.record()
        record["interest_expense_yi"] = True
        with self.assertRaisesRegex(ValueError, "布尔"):
            canonicalize_earnings(record, allow_legacy=False)


class ContractTests(unittest.TestCase):
    def test_duplicate_json_key_fails_before_contract_interpretation(self):
        with tempfile.TemporaryDirectory(prefix="risk-core-test-") as folder:
            path = Path(folder) / "duplicate.json"
            path.write_text('{"schema_version":5,"schema_version":5}', encoding="utf-8")
            with self.assertRaisesRegex(SubmissionContractError, "重复"):
                load_submission_contract(path, project_root=folder)

    def test_missing_contract_fields_fail_explicitly(self):
        with tempfile.TemporaryDirectory(prefix="risk-core-test-") as folder:
            path = Path(folder) / "incomplete.json"
            path.write_text(json.dumps({"schema_version": 5}), encoding="utf-8")
            with self.assertRaises(SubmissionContractError):
                load_submission_contract(path, project_root=folder)


if __name__ == "__main__":
    unittest.main()
