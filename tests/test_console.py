import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ConsoleEncodingTests(unittest.TestCase):
    def run_python(self, *arguments, expected_code=0):
        result = subprocess.run(
            [sys.executable, '-B', *map(str, arguments)],
            env=dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0'),
            capture_output=True, timeout=60,
        )
        stdout = result.stdout.decode('utf-8')
        stderr = result.stderr.decode('utf-8')
        self.assertEqual(result.returncode, expected_code, stderr)
        return stdout, stderr

    def test_demo_emits_utf8_on_legacy_console(self):
        stdout, _ = self.run_python('-m', 'supply_chain_risk_core.demo')
        self.assertIn('最强简单路径', stdout)
        self.assertAlmostEqual(json.loads(stdout)['downstream_exposure']['终端'], .24)

    def test_cli_roundtrip_uses_utf8_for_chinese_paths(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / '中文结果'
            stdout, _ = self.run_python('-m', 'supply_chain_risk_core.cli', 'demo',
                                        '--without-office', '--output', output)
            self.assertEqual(json.loads(stdout)['output'], str(output))
            stdout, _ = self.run_python('-m', 'supply_chain_risk_core.cli', 'verify', output, '--exact-recompute')
            self.assertTrue(json.loads(stdout)['recomputed'])
            self.assertEqual(json.loads(stdout)['recompute_policy'], 'exact')

    def test_command_help_uses_utf8(self):
        commands = [('-m', 'supply_chain_risk_core.cli', '--help'),
                    (ROOT / 'tools' / 'compare_original.py', '--help')]
        for command in commands:
            with self.subTest(command=command):
                stdout, _ = self.run_python(*command)
                self.assertIn('报告' if command[0] == '-m' else '原工程', stdout)

    def test_cli_errors_preserve_unicode_and_nonzero_status(self):
        _, stderr = self.run_python('-m', 'supply_chain_risk_core.cli', '未知命令', expected_code=2)
        self.assertIn('未知命令', stderr)
        self.assertNotIn('UnicodeEncodeError', stderr)

    def test_manifest_checker_uses_utf8(self):
        stdout, _ = self.run_python(ROOT / 'tools' / 'check_manifest.py')
        self.assertIn('来源清单：', stdout)
        self.assertIn('件案例成品一致', stdout)

    def test_imports_preserve_host_stream_encoding(self):
        stdout, _ = self.run_python('-c', '\n'.join([
            'import json, sys',
            'before = [sys.stdout.encoding, sys.stderr.encoding]',
            'import supply_chain_risk_core.cli',
            'import supply_chain_risk_core.demo',
            'print(json.dumps([before, [sys.stdout.encoding, sys.stderr.encoding]]))',
        ]))
        self.assertEqual(json.loads(stdout), [['cp1252', 'cp1252'], ['cp1252', 'cp1252']])


if __name__ == '__main__':
    unittest.main()
