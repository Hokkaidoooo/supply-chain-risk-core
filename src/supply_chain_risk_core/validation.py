"""公开输入与序列化的共用契约。"""
from datetime import date
from decimal import Decimal
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import re

from .submission_contract import _json_object_without_duplicates


def require_object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    return value


def exact_keys(value, required, label, optional=()):
    require_object(value, label)
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f"{label} 字段不匹配：缺少 {sorted(missing)}，多出 {sorted(extra)}")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须为非空文字")
    return value.strip()


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{label} 必须为数值，不能是布尔值或字符串")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须是有限数值")
    return result


def day(value, label):
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"{label} 必须使用 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} 不是有效日期") from exc


def nonempty_list(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} 必须为非空列表")
    return value


def _invalid_constant(value):
    raise ValueError(f"JSON 不接受 {value}")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_json_object_without_duplicates,
                      parse_constant=_invalid_constant)


def json_ready(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (Real, Decimal)):
        return finite_number(value, "输出数值")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON 对象的键必须是字符串")
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    raise TypeError(f"无法序列化的输出类型：{type(value).__name__}")


def canonical_bytes(value):
    return json.dumps(json_ready(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
