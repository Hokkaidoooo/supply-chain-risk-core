import copy
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import networkx as nx

from supply_chain_risk_core.cli import example_document, run_to_directory, verify_directory
from supply_chain_risk_core.credit.workflow import WorkflowOptions
from supply_chain_risk_core.input_output import analyze_input_output
from supply_chain_risk_core.scenarios import cost_impact, demand_impact
from supply_chain_risk_core.stage2.quantitative import number, revenue_overlap_bounds
from supply_chain_risk_core.study import analyze_study
from supply_chain_risk_core.supply_chain import downstream_of
from supply_chain_risk_core.validation import canonical_bytes, file_digest, load_json


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.data = example_document()

    def test_complete_case_and_independent_arithmetic(self):
        before = copy.deepcopy(self.data)
        report = analyze_study(self.data)
        self.assertEqual(self.data, before)
        companies = {row['company_id']: row for row in report['credit']['companies']}
        self.assertEqual([companies[key]['action'] for key in ('SYN001', 'SYN002', 'SYN003')], ['放宽', '收紧', '维持'])
        self.assertEqual(companies['SYN001']['risk_score'], companies['SYN002']['risk_score'])
        self.assertTrue(companies['SYN002']['financial_rule_hits'])
        self.assertTrue(companies['SYN003']['review_required'])
        self.assertIn('financials', companies['SYN003']['missing_inputs'])
        cost = report['chains'][0]['scenario']['result']
        self.assertAlmostEqual(cost['added_cost_yi'], 20 * (.5 * .4 + .3 * .8) * .1 * (1 - .3))
        demand = report['chains'][1]['scenario']['result']
        self.assertAlmostEqual(demand['revenue_loss_yi'], 10 * .4 * .2)
        self.assertIsNone(demand['ebit_loss_yi'])
        self.assertEqual(demand['profit_impact_status'], 'unknown_contribution_margin')
        cases = {case['id']: case['result'] for case in report['quantitative_cases']}
        self.assertAlmostEqual(cases['copper']['retained_cost_per_100_revenue'], 1.152)
        self.assertAlmostEqual(cases['feed']['cost_change_pct'], 1.0)
        self.assertEqual([cases['board'][key] for key in ('board_retained_cost', 'brand_retained_cost', 'downstream_cost')], [40, 45, 15])
        self.assertEqual((cases['overlap']['lower'], cases['overlap']['upper']), (30, 60))

    def test_reordering_does_not_change_company_results(self):
        one = analyze_study(self.data)['credit']['companies']
        self.data['companies'].reverse()
        two = analyze_study(self.data)['credit']['companies']
        self.assertEqual({row['company_id']: row for row in one}, {row['company_id']: row for row in two})

    def test_missing_review_switch_keeps_unknown_evidence(self):
        report = analyze_study(self.data, WorkflowOptions(review_on_missing=False))
        row = report['credit']['companies'][2]
        self.assertFalse(row['review_required'])
        self.assertTrue(row['missing_inputs'])
        self.assertEqual(row['model_advice_assessment']['status'], 'insufficient_evidence')

    def test_bad_values_are_not_converted_to_neutral(self):
        changes = [('registration', '注册资本', '无法解析的金额'), ('registration', '实缴资本', '100万XYZ'),
                   ('registration', '注册资本', '1000'), ('registration', '参保人数', '-3人'),
                   ('registration', '成立日期', '2027-01-01'), ('risk_signals', 'is_dishonest', 'false'),
                   ('risk_signals', 'penalty_cnt_1y', -1), ('context', 'ind_cost_shock', float('nan')),
                   ('financials', 'ebit_yi', 123), ('financials', 'loss_years_3y', 2.5)]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                bad = copy.deepcopy(self.data)
                bad['companies'][0][section][key] = value
                with self.assertRaises(ValueError):
                    analyze_study(bad)

    def test_duplicate_identity_and_missing_keys_fail(self):
        self.data['companies'][1]['company_id'] = 'SYN001'
        with self.assertRaisesRegex(ValueError, '重复'):
            analyze_study(self.data)
        self.data = example_document()
        del self.data['companies'][0]['context']['export_shock']
        with self.assertRaisesRegex(ValueError, '字段'):
            analyze_study(self.data)

    def test_temporal_and_hash_contracts(self):
        source_id = 'source-SYN001'
        changes = [('published_at', '2026-07-01'), ('valid_to', '2026-06-29'),
                   ('content', '篡改来源'), ('url', 'javascript:alert(1)'), ('kind', 'public_evidence')]
        for key, value in changes:
            with self.subTest(key=key):
                bad = copy.deepcopy(self.data)
                bad['sources'][source_id][key] = value
                with self.assertRaises(ValueError):
                    analyze_study(bad)

    def test_graph_and_source_failures_are_visible(self):
        variants = []
        missing_edge = copy.deepcopy(self.data)
        missing_edge['chains'][0]['links'].pop()
        variants.append(missing_edge)
        stale_derived = copy.deepcopy(self.data)
        stale_derived['chains'][0]['risk_values'] = {'assembly': 1}
        variants.append(stale_derived)
        unknown_source = copy.deepcopy(self.data)
        unknown_source['chains'][0]['source_refs'] = ['not-registered']
        variants.append(unknown_source)
        wrong_kind = copy.deepcopy(self.data)
        wrong_kind['chains'][0]['scenario']['kind'] = 'demand_contraction'
        variants.append(wrong_kind)
        for bad in variants:
            with self.assertRaises(ValueError):
                analyze_study(bad)


class InputOutputTests(unittest.TestCase):
    def test_path_max_is_different_from_sum_of_indirect_inputs(self):
        data = example_document()['input_output']
        result = analyze_input_output(data['nodes'], data['direct_coefficients'], '原料')
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([('原料', '加工', .5), ('原料', '替代环节', .3),
                                       ('加工', '终端', .4), ('替代环节', '终端', .8)])
        self.assertAlmostEqual(downstream_of('原料', graph)['终端'], .24)
        self.assertAlmostEqual(result['leontief_inverse'][0][3], .44)
        self.assertAlmostEqual(result['source_price_coefficients']['终端'], .44)

    def test_fixed_source_price_does_not_reprice_itself_through_cycle(self):
        result = analyze_input_output(['source', 'target'], [[0, .5], [.2, 0]], 'source')
        self.assertAlmostEqual(result['source_price_coefficients']['target'], .5)
        self.assertAlmostEqual(result['leontief_inverse'][0][1], .5 / .9)

    def test_nonproductive_and_bad_matrices_fail(self):
        for matrix in ([[1, 0], [0, 0]], [[0, -1], [0, 0]], [[0, True], [0, 0]], [[0], [0, 0]]):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                analyze_input_output(['a', 'b'], matrix, 'a')
        with self.assertRaisesRegex(ValueError, '预算'):
            analyze_input_output(['a', 'b'], [[0, 0], [0, 0]], 'a', max_nodes=1)
        with self.assertRaisesRegex(ValueError, '病态'):
            analyze_input_output(['a', 'b'], [[1 - 1e-14, 0], [0, 0]], 'a')

    def test_scenario_boundaries_and_conservation(self):
        for value in (-1, True, float('inf')):
            with self.assertRaises(ValueError):
                cost_impact(10, 1, .2, value)
        result = demand_impact(10, 1, .2, .4, .2, contribution_margin=.25)
        self.assertAlmostEqual(result['ebit_loss_yi'], .2)
        self.assertAlmostEqual(result['ebit_after_yi'], .8)
        self.assertIsNone(demand_impact(10, 1, 0, .4, .2)['interest_cover_after_x'])
        self.assertEqual(number('1,234.50', '金额'), Decimal('1234.50'))
        for bad in ('1,2.3', 'NaN', True):
            with self.assertRaises(ValueError):
                number(bad, '金额')
        with self.assertRaises(ValueError):
            revenue_overlap_bounds(Decimal(100), Decimal(101), Decimal(60))


class ReproductionTests(unittest.TestCase):
    def test_report_labels_units_and_assumptions(self):
        from supply_chain_risk_core.reporting import markdown_report, sections
        report = analyze_study(example_document())
        rendered = markdown_report(report)
        self.assertNotIn('F4_turn_to_loss_leverage', rendered)
        self.assertIn('转亏叠加高杠杆', rendered)
        self.assertIn('源头价格上涨10%', rendered)
        board = next(section for section in sections(report) if section.title == '板卡与整机的同批成本分配')
        self.assertIn(('板卡转嫁率', 60.0, '%'), board.rows)
        self.assertIn(('品牌吸收额', 45.0, '元/同批一板一机'), board.rows)

    def test_long_node_labels_are_not_truncated(self):
        import xml.etree.ElementTree as ET
        from supply_chain_risk_core.reporting import chain_svg
        from supply_chain_risk_core.stage2.projection import project_chain
        chain = example_document()['chains'][0]
        label = '一个需要完整显示的较长产业链节点名称和末端标记'
        chain['layers'][0][0]['label'] = label
        svg = ET.fromstring(chain_svg(project_chain(chain)))
        self.assertEqual(''.join(node.text or '' for node in svg.iter() if node.attrib.get('data-node') == 'raw'), label)

    def test_office_outputs_repeat_and_preserve_value_types(self):
        import xml.etree.ElementTree as ET
        from zipfile import ZipFile
        from openpyxl import load_workbook
        from supply_chain_risk_core.reporting import write_reports
        data = example_document()
        data['title'] = '=1+1'
        report = analyze_study(data)
        with TemporaryDirectory() as temporary:
            roots = [Path(temporary) / name for name in ('one', 'two')]
            for root in roots:
                root.mkdir()
                write_reports(report, root)
            for name in ('report.docx', 'report.xlsx'):
                self.assertEqual(file_digest(roots[0] / name), file_digest(roots[1] / name))
            book = load_workbook(roots[0] / 'report.xlsx')
            self.assertEqual(book['授信概览']['D8'].value, 19.6)
            self.assertEqual(book['授信概览']['D8'].data_type, 'n')
            self.assertEqual(book['授信概览']['A1'].data_type, 's')
            self.assertEqual(book['授信概览']['A1'].value, '=1+1')
            with ZipFile(roots[0] / 'report.docx') as archive:
                styles = ET.fromstring(archive.read('word/styles.xml'))
                self.assertFalse(list(styles.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr')))

    def test_publish_recompute_and_no_overwrite(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / 'first', root / 'second'
            data = example_document()
            run_to_directory(data, first, office=False)
            run_to_directory(data, second, office=False)
            self.assertTrue(verify_directory(first)['recomputed'])
            self.assertEqual({path.name: file_digest(path) for path in first.iterdir()},
                             {path.name: file_digest(path) for path in second.iterdir()})
            before = (first / 'analysis.json').read_bytes()
            with self.assertRaises(FileExistsError):
                run_to_directory(data, first, office=False)
            self.assertEqual((first / 'analysis.json').read_bytes(), before)
            self.assertNotIn(b'\r\n', (first / 'report.md').read_bytes())

    def test_tampered_analysis_fails_even_if_file_hash_is_updated(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / 'report'
            run_to_directory(example_document(), root, office=False)
            analysis = load_json(root / 'analysis.json')
            analysis['credit']['companies'][0]['risk_score'] = 99
            (root / 'analysis.json').write_bytes(canonical_bytes(analysis) + b'\n')
            manifest = load_json(root / 'manifest.json')
            for entry in manifest['files']:
                if entry['path'] == 'analysis.json':
                    entry.update(sha256=file_digest(root / 'analysis.json'), bytes=(root / 'analysis.json').stat().st_size)
            (root / 'manifest.json').write_bytes(canonical_bytes(manifest))
            with self.assertRaisesRegex(ValueError, '计算结果'):
                verify_directory(root)

    def test_duplicate_json_fields_and_nonfinite_literals_fail(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'input.json'
            for payload in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}'):
                path.write_text(payload, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_json(path)

    def test_generated_html_escapes_untrusted_titles(self):
        from supply_chain_risk_core.reporting import html_report
        data = example_document()
        data['title'] = '<script>alert(1)</script>'
        html = html_report(analyze_study(data))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__':
    unittest.main()
