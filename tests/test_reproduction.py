import copy
import math
import unittest

from supply_chain_risk_core.reproduction import compare_recomputed_analysis
from supply_chain_risk_core.validation import canonical_bytes


def analysis(condition):
    return {'input_output': {'condition_number': condition, 'source_price_coefficients': {'终端': .44}},
            'action': '维持', 'order': ['原料', '终端']}


def advance(value, count):
    for _ in range(count):
        value = math.nextafter(value, math.inf)
    return value


class RecomputedAnalysisTests(unittest.TestCase):
    def test_observed_ci_roundoff_is_reported_without_changing_values(self):
        saved, current = analysis(2.777237851013947), analysis(2.7772378510139455)
        before = canonical_bytes([saved, current])
        differences = compare_recomputed_analysis(saved, current)
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]['path'], '$.input_output.condition_number')
        self.assertEqual(differences[0]['ulp_distance'], 3)
        self.assertEqual(differences[0]['max_ulps'], 32)
        self.assertEqual(canonical_bytes([saved, current]), before)

    def test_exact_ulp_boundary_across_a_power_of_two(self):
        saved = analysis(math.nextafter(2.0, 0.0))
        current = analysis(advance(saved['input_output']['condition_number'], 32))
        self.assertEqual(compare_recomputed_analysis(saved, current)[0]['ulp_distance'], 32)
        current['input_output']['condition_number'] = advance(current['input_output']['condition_number'], 1)
        with self.assertRaisesRegex(ValueError, 'condition_number'):
            compare_recomputed_analysis(saved, current)

    def test_exact_mode_rejects_observed_roundoff(self):
        with self.assertRaisesRegex(ValueError, 'condition_number.*0x'):
            compare_recomputed_analysis(analysis(2.777237851013947), analysis(2.7772378510139455),
                                         exact_recompute=True)

    def test_other_numbers_structure_and_decisions_remain_exact(self):
        saved = analysis(2.777237851013947)
        variants = []
        changed = copy.deepcopy(saved)
        changed['input_output']['source_price_coefficients']['终端'] = math.nextafter(.44, math.inf)
        variants.append(changed)
        variants.extend([dict(saved, action='收紧'), dict(saved, order=['终端', '原料']),
                         dict(saved, order=['原料']), dict(saved, extra=True)])
        for current in variants:
            with self.subTest(current=current), self.assertRaises(ValueError):
                compare_recomputed_analysis(saved, current)
        with self.assertRaises(ValueError):
            compare_recomputed_analysis({'value': 0.0}, {'value': -0.0})
        with self.assertRaises(ValueError):
            compare_recomputed_analysis({'value': 1}, {'value': True})

    def test_invalid_condition_values_do_not_gain_a_tolerance(self):
        for value in (0.0, -1.0, math.inf, math.nan, True, '2.777237851013947'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compare_recomputed_analysis(analysis(value), analysis(2.777237851013947))


if __name__ == '__main__':
    unittest.main()
