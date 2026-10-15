"""资本单位和三期亏损历史的输入不变量。"""
import copy
from decimal import Inexact, ROUND_UP, localcontext
import itertools
import json
import math
import unittest

import pandas as pd

from supply_chain_risk_core.cli import example_document
from supply_chain_risk_core.credit.deep_features import _parse_capital, deep_features
from supply_chain_risk_core.credit.financial_features import listed_financial_features
from supply_chain_risk_core.study import analyze_study


class CapitalInputTests(unittest.TestCase):
    def test_parser_ignores_callers_decimal_precision_and_limits(self):
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_UP
            context.Emax, context.Emin = 2, -2
            context.traps[Inexact] = True
            before = context.copy()
            self.assertEqual(_parse_capital(pd.Series(["12345万元", "1234567.5万元"])).tolist(),
                             [12345, 1234567.5])
            self.assertEqual(context.flags, before.flags)
            self.assertEqual(context.traps, before.traps)
            self.assertEqual((context.prec, context.rounding, context.Emax, context.Emin),
                             (before.prec, before.rounding, before.Emax, before.Emin))

    def test_currency_units_represent_the_same_amount(self):
        rates = {"元": 1, "人民币": 1, "元人民币": 1, "美元": 7.2,
                 "欧元": 7.8, "香港元": .92, "港元": .92, "英镑": 9.1, "日元": .05}
        for currency, expected in rates.items():
            amounts = [f"10000{currency}", f"1万{currency}", f".0001亿{currency}", f"1e4{currency}"]
            with self.subTest(currency=currency):
                result = _parse_capital(pd.Series(amounts))
                self.assertEqual(result.tolist(), [expected] * len(amounts))

    def test_valid_grouping_and_fullwidth_numbers(self):
        values = ["1,234,567.5万元", "１，２３４，５６７．５万元",
                  "1 234 567.5万元", "1\u00a0234\u00a0567.5万元", "+1234567.5 万元人民币"]
        self.assertEqual(_parse_capital(pd.Series(values)).tolist(), [1234567.5] * len(values))

    def test_bad_amounts_raise_instead_of_becoming_unknown(self):
        values = ["1,00万元", "12,34,567万元", "1 00万元", "1,234 567万元",
                  "100美元人民币", "100人民币人民币", "100元美元", "100万XYZ",
                  "1234", "-1万元", "1e309万元", "1e-400万元", "人民币100元",
                  "约100万元", "100万", "1,234.5.6万元", "1,,000万元"]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _parse_capital(pd.Series([value]))

    def test_missing_and_zero_are_distinct_and_index_is_preserved(self):
        series = pd.Series([None, "", "-", "未公示", "0英镑"], index=[2, 4, 7, 9, 12])
        result = _parse_capital(series)
        self.assertEqual(result.index.tolist(), series.index.tolist())
        self.assertTrue(all(math.isnan(value) for value in result.iloc[:4]))
        self.assertEqual(result.iloc[4], 0)

    def test_equivalent_capital_text_has_identical_features_and_score(self):
        rows = []
        for value in ("100人民币", "100元人民币", "0.01万元"):
            data = example_document()
            data["companies"][0]["registration"]["注册资本"] = value
            rows.append(analyze_study(data)["credit"]["companies"][0])
        self.assertEqual(rows[0]["features"]["reg_capital_wan"], .01)
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(rows[1], rows[2])

    def test_workflow_rejects_the_same_invalid_grammar(self):
        for value in ("1,00万元", "100美元人民币", "1 00万元"):
            data = example_document()
            data["companies"][0]["registration"]["注册资本"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "注册资本"):
                analyze_study(data)

    def test_direct_features_cannot_rewrite_invalid_capital_before_parsing(self):
        for value in ("1\n00万元", "1\r00万元", "1_x000D_00万元"):
            data = example_document()["companies"][0]["registration"]
            data["注册资本"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                deep_features(pd.DataFrame([data]), "2026-06-30")

    def test_direct_features_preserve_dataframe_missing_values(self):
        for value in (None, math.nan, pd.NA):
            data = example_document()["companies"][0]["registration"]
            data.update(注册资本=value, 实缴资本=value)
            result = deep_features(pd.DataFrame([data]), "2026-06-30").iloc[0]
            self.assertEqual(result["reg_capital_wan"], -1)
            self.assertEqual(result["paid_capital_wan"], -1)


class LossHistoryInputTests(unittest.TestCase):
    @staticmethod
    def inputs(profits, declared):
        company = example_document()["companies"][0]
        record = dict(company["financials"], company_id=company["company_id"], credit_code=company["company_id"])
        record.update(zip(("net_profit_yi", "net_profit_prev_yi", "net_profit_prev2_yi"), profits))
        record["loss_years_3y"] = declared
        record["liquidity_adjustments"] = json.dumps(record["liquidity_adjustments"], ensure_ascii=False)
        return (pd.DataFrame([{"company_id": company["company_id"], "credit_code": company["company_id"]}]),
                pd.DataFrame([record], dtype=object))

    def features(self, profits, declared):
        firms, records = self.inputs(profits, declared)
        return listed_financial_features(firms, records, "2026-06-30").iloc[0]

    def test_all_sign_combinations_agree_with_declared_loss_count(self):
        for profits in itertools.product((-1, 0, 1), repeat=3):
            count = sum(value < 0 for value in profits)
            with self.subTest(profits=profits):
                row = self.features(profits, count)
                self.assertEqual(row["fin_loss_years_3y"], count)
                self.assertEqual(row["fin_consecutive_loss_3y"], int(count == 3))
                with self.assertRaisesRegex(ValueError, "loss_years_3y.*不一致"):
                    self.features(profits, (count + 1) % 4)

    def test_complete_profits_supply_missing_derived_count(self):
        row = self.features((-1, 0, -2), None)
        self.assertEqual(row["fin_loss_years_3y"], 2)
        self.assertEqual(row["fin_consecutive_loss_3y"], 0)

    def test_partial_history_retains_unknown_or_consistent_declared_count(self):
        self.assertEqual(self.features((-1, None, None), None)["fin_loss_years_3y"], -1)
        self.assertEqual(self.features((-1, None, None), 2)["fin_loss_years_3y"], 2)
        self.assertEqual(self.features((0, None, 1), 1)["fin_loss_years_3y"], 1)
        for profits, declared in [((-1, None, None), 0), ((0, None, 1), 2), ((None, 1, 1), 3)]:
            with self.subTest(profits=profits, declared=declared), self.assertRaisesRegex(ValueError, "不一致"):
                self.features(profits, declared)

    def test_invalid_labels_fail_at_direct_feature_entry(self):
        for value in (True, -1, 4, 1.5, math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "loss_years_3y"):
                self.features((None, None, None), value)

    def test_dataframe_nullable_numeric_column_is_accepted(self):
        counts = pd.DataFrame({"count": [0, None]})["count"]
        self.assertEqual(self.features((1, 1, 1), counts.iloc[0])["fin_loss_years_3y"], 0)
        self.assertEqual(self.features((None, None, None), counts.iloc[1])["fin_loss_years_3y"], -1)

    def test_public_json_still_requires_integer_or_null(self):
        for value in (True, "0", 0.0, math.nan, math.inf):
            data = example_document()
            data["companies"][0]["financials"]["loss_years_3y"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "loss_years_3y"):
                analyze_study(data)

    def test_direct_feature_entry_does_not_mutate_inputs(self):
        firms, records = self.inputs((-1, 0, -2), None)
        before_firms, before_records = firms.copy(deep=True), records.copy(deep=True)
        listed_financial_features(firms, records, "2026-06-30")
        pd.testing.assert_frame_equal(firms, before_firms)
        pd.testing.assert_frame_equal(records, before_records)

    def test_bad_label_cannot_hide_three_year_loss_in_public_workflow(self):
        data = example_document()
        data["companies"][0]["financials"].update(
            net_profit_yi=-1, net_profit_prev_yi=-2, net_profit_prev2_yi=-3,
            loss_years_3y=0, turn_to_loss=0)
        before = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, "loss_years_3y.*不一致"):
            analyze_study(data)
        self.assertEqual(data, before)
        data["companies"][0]["financials"]["loss_years_3y"] = 3
        row = analyze_study(data)["credit"]["companies"][0]
        self.assertEqual(row["action"], "收紧")
        self.assertIn("F2_consecutive_loss", row["financial_rule_hits"])


if __name__ == "__main__":
    unittest.main()
