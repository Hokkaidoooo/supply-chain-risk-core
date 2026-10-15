"""核验全部公开模块、合成输入和案例成品的指纹；不修改清单。"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
expected = {path.relative_to(root).as_posix() for path in (root / 'src' / 'supply_chain_risk_core').rglob('*.py')}
listed = [module['path'] for module in manifest['modules']]
if set(listed) != expected or len(listed) != len(set(listed)):
    raise ValueError('来源清单未完整且唯一地覆盖公开模块')
for module in manifest['modules']:
    actual = hashlib.sha256((root / module['path']).read_bytes()).hexdigest()
    if actual != module['public_sha256']:
        raise ValueError(f"来源清单未同步：{module['path']}")
fixture_paths = [item['path'] for item in manifest['fixtures']]
actual_fixtures = {path.relative_to(root).as_posix() for path in (root / 'src' / 'supply_chain_risk_core' / 'data').rglob('*') if path.is_file()}
if set(fixture_paths) != actual_fixtures or len(fixture_paths) != len(set(fixture_paths)):
    raise ValueError('合成输入目录与清单成员不一致')
for fixture in manifest['fixtures']:
    if hashlib.sha256((root / fixture['path']).read_bytes()).hexdigest() != fixture['sha256']:
        raise ValueError(f"合成输入指纹未同步：{fixture['path']}")
cases = json.loads((root / 'case-studies' / 'finals' / 'source-manifest.json').read_text(encoding='utf-8'))
case_paths = [item['path'] for item in cases['files']]
actual_cases = {path.relative_to(root).as_posix() for path in (root / 'case-studies' / 'finals').rglob('*')
                if path.is_file() and path.name not in {'README.md', 'source-manifest.json'}}
if set(case_paths) != actual_cases or len(case_paths) != len(set(case_paths)):
    raise ValueError('案例目录与成品清单成员不一致')
for item in cases['files']:
    path = root / item['path']
    if path.stat().st_size != item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError(f"案例文件指纹不一致：{item['path']}")
print(f"来源清单：{len(manifest['modules'])} 个模块、{len(manifest['fixtures'])} 个合成输入、{len(cases['files'])} 件案例成品一致")
