"""复算比较：只允许矩阵条件数的有限机器精度差异。"""
import struct

from .validation import canonical_bytes, json_ready

CONDITION_NUMBER_PATH = ('input_output', 'condition_number')
CONDITION_NUMBER_MAX_ULPS = 32
RECOMPUTE_POLICY = f'condition_number_{CONDITION_NUMBER_MAX_ULPS}_ulps'


def _location(path):
    return '$' + ''.join(f'[{key}]' if isinstance(key, int) else f'.{key}' for key in path)


def _describe(value):
    return f'{value!r} ({value.hex()})' if type(value) is float else repr(value)


def _mismatch(path, saved, current):
    raise ValueError(f'重新计算结果与保存的分析不一致：{_location(path)}，'
                     f'保存 {_describe(saved)}，重算 {_describe(current)}')


def _condition_roundoff(saved, current, path):
    if path != CONDITION_NUMBER_PATH or type(saved) is not float or type(current) is not float:
        return None
    if saved <= 0 or current <= 0:
        return None
    saved_bits = struct.unpack('!Q', struct.pack('!d', saved))[0]
    current_bits = struct.unpack('!Q', struct.pack('!d', current))[0]
    distance = abs(saved_bits - current_bits)
    if distance > CONDITION_NUMBER_MAX_ULPS:
        return None
    return {'path': _location(path), 'saved': saved, 'recomputed': current,
            'ulp_distance': distance, 'max_ulps': CONDITION_NUMBER_MAX_ULPS}


def _compare(saved, current, path, exact_recompute):
    if type(saved) is not type(current):
        _mismatch(path, saved, current)
    if isinstance(saved, dict):
        if saved.keys() != current.keys():
            _mismatch(path, sorted(saved), sorted(current))
        return [difference for key in sorted(saved)
                for difference in _compare(saved[key], current[key], (*path, key), exact_recompute)]
    if isinstance(saved, list):
        if len(saved) != len(current):
            _mismatch((*path, 'length'), len(saved), len(current))
        return [difference for index, (left, right) in enumerate(zip(saved, current))
                for difference in _compare(left, right, (*path, index), exact_recompute)]
    if canonical_bytes(saved) == canonical_bytes(current):
        return []
    if not exact_recompute:
        difference = _condition_roundoff(saved, current, path)
        if difference is not None:
            return [difference]
    _mismatch(path, saved, current)


def compare_recomputed_analysis(saved, current, *, exact_recompute=False):
    """按规范 JSON 语义比较并返回条件数微差，不改写输入、结果或其指纹。"""
    if type(exact_recompute) is not bool:
        raise TypeError('exact_recompute 须为布尔值')
    saved_json, current_json = json_ready(saved), json_ready(current)
    if canonical_bytes(saved_json) == canonical_bytes(current_json):
        return []
    return _compare(saved_json, current_json, (), exact_recompute)
