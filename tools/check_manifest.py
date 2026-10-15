"""核验来源模块的公开字节指纹；不修改清单。"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
for module in manifest['modules']:
    actual = hashlib.sha256((root / module['path']).read_bytes()).hexdigest()
    if actual != module['public_sha256']:
        raise ValueError(f"来源清单未同步：{module['path']}")
print(f"来源清单：{len(manifest['modules'])} 个模块指纹一致")
