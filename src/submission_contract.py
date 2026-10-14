# -*- coding: utf-8 -*-
"""官方提交契约的严格加载、证据绑定与运行参数核对。"""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import ntpath
import os
from pathlib import Path, PurePosixPath
import re


# v3/v4 retain their historical scoring semantics and manifest shapes.
# v5 describes the selection explicitly; schedule changes are not schema changes.
CONTRACT_SCHEMA_VERSION = 5
SUBMISSION_SELECTIONS = (
    'last_submission',
    'highest_score_across_submissions',
)
_HISTORICAL_SUBMISSION_SELECTIONS = {
    3: 'last_submission',
    4: 'highest_score_across_submissions',
}
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TOP_LEVEL_FIELDS = {
    'schema_version', 'confirmed', 'received_at', 'official_source',
    'submission', 'company_id', 'action', 'risk_control_advice',
    'scoring_submission_selection', 'as_of', 'owner_decisions', 'notes',
}
_SUMMARY_FIELDS_V3 = {
    'schema_version', 'path', 'sha256', 'confirmed', 'source_reference',
    'source_path', 'source_sha256', 'submission_sheet', 'expected_firm_count',
    'advice_min_n', 'advice_max_n',
    'scoring_rule', 'action_encoding', 'as_of',
}
_SUMMARY_FIELDS_WITH_SELECTION = _SUMMARY_FIELDS_V3 | {
    'scoring_submission_selection',
}
_SUMMARY_FIELDS_BY_SCHEMA = {
    3: _SUMMARY_FIELDS_V3,
    4: _SUMMARY_FIELDS_WITH_SELECTION,
    CONTRACT_SCHEMA_VERSION: _SUMMARY_FIELDS_WITH_SELECTION,
}
_COLUMN_ORDER = ['company_id', 'action', 'risk_control_advice']
_ACTION_ENCODING = {'放宽': '1', '维持': '0', '收紧': '-1'}
_ACTION_VALUES = ['-1', '0', '1']
_SCORING_METHOD = 'exact_match'
_HEX_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class SubmissionContractError(ValueError):
    """提交契约缺失、歧义、证据不一致或与运行参数冲突。"""


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _json_object_without_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SubmissionContractError('提交契约 JSON 存在重复字段: %s' % key)
        value[key] = item
    return value


def _require_object(value, label):
    if not isinstance(value, dict):
        raise SubmissionContractError('%s 必须是 JSON 对象' % label)
    return value


def _require_exact_fields(value, fields, label):
    obj = _require_object(value, label)
    actual = set(obj)
    missing = sorted(fields - actual)
    extra = sorted(actual - fields)
    if missing or extra:
        raise SubmissionContractError(
            '%s 字段不符合 schema；缺失=%s，额外=%s'
            % (label, missing, extra))
    return obj


def _require_text(value, label, *, allow_none=False):
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise SubmissionContractError('%s 必须是非空且无首尾空格的字符串' % label)
    return value


def _require_exact_or_none(value, expected, label, *, confirmed):
    if value is None and not confirmed:
        return None
    if value != expected or type(value) is not type(expected):
        raise SubmissionContractError('%s 必须为 %r' % (label, expected))
    return value


def _summary_fields_for_schema(schema_version):
    """原始契约与已加载摘要使用同一版本边界，历史形状不可漂移。"""
    if type(schema_version) is not int:
        raise SubmissionContractError('schema_version 必须是整数')
    if schema_version not in _SUMMARY_FIELDS_BY_SCHEMA:
        raise SubmissionContractError(
            '不支持的提交契约 schema_version=%r，当前要求 %d'
            % (schema_version, CONTRACT_SCHEMA_VERSION))
    return _SUMMARY_FIELDS_BY_SCHEMA[schema_version]


def _validate_submission_selection(value, schema_version, *, confirmed):
    """只为历史版本保留固定规则；v5 必须自描述，未确认也不能猜规则。"""
    if schema_version in _HISTORICAL_SUBMISSION_SELECTIONS:
        return _require_exact_or_none(
            value, _HISTORICAL_SUBMISSION_SELECTIONS[schema_version],
            'scoring_submission_selection', confirmed=confirmed)
    if not isinstance(value, str) or value not in SUBMISSION_SELECTIONS:
        raise SubmissionContractError(
            'scoring_submission_selection 必须为已实现的枚举之一 %r'
            % (SUBMISSION_SELECTIONS,))
    return value


def _parse_iso_date(value, label, *, allow_none=False):
    if value is None and allow_none:
        return None
    text = _require_text(value, label)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        raise SubmissionContractError('%s 必须使用 YYYY-MM-DD' % label)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise SubmissionContractError('%s 不是有效日期: %s' % (label, text)) from exc


def _validate_received_at(value, *, confirmed):
    if value is None and not confirmed:
        return
    text = _require_text(value, 'received_at')
    try:
        if 'T' in text:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('datetime 缺少时区')
        else:
            date.fromisoformat(text)
    except ValueError as exc:
        raise SubmissionContractError(
            'received_at 必须是有效 ISO 日期或带时区时间') from exc


def _resolve_project_file(path, project_root, label):
    root = Path(project_root).resolve()
    raw = Path(path)
    candidate = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise SubmissionContractError('%s 必须位于项目目录内' % label) from exc
    if not candidate.is_file():
        raise SubmissionContractError('%s 不存在或不是普通文件: %s' % (label, path))
    return candidate, relative.as_posix()


def _resolve_source_file(relative_path, project_root):
    text = _require_text(relative_path, 'official_source.path')
    if ('\\' in text or os.path.isabs(text) or ntpath.isabs(text)
            or ntpath.splitdrive(text)[0]):
        raise SubmissionContractError('官方来源路径必须是项目内 POSIX 相对路径')
    pure = PurePosixPath(text)
    if (pure.is_absolute() or any(part in ('', '.', '..') for part in pure.parts)
            or pure.as_posix() != text):
        raise SubmissionContractError('官方来源路径必须是规范的项目内相对路径')

    root = Path(project_root).resolve()
    source = root.joinpath(*pure.parts).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise SubmissionContractError('官方来源路径禁止逃逸项目目录') from exc
    if not source.is_file():
        raise SubmissionContractError('官方来源路径不存在或不是普通文件: %s' % text)
    return source


def _validate_source(source, project_root, *, confirmed):
    source = _require_exact_fields(
        source, {'reference', 'path', 'sha256'}, 'official_source')
    values = [source['reference'], source['path'], source['sha256']]
    if all(value is None for value in values) and not confirmed:
        return
    if any(value is None for value in values):
        raise SubmissionContractError('官方来源 reference/path/sha256 必须同时填写')

    _require_text(source['reference'], 'official_source.reference')
    source_path = _resolve_source_file(source['path'], project_root)
    source_sha256 = _require_text(source['sha256'], 'official_source.sha256')
    if not _HEX_SHA256.fullmatch(source_sha256):
        raise SubmissionContractError('官方来源 SHA-256 必须是 64 位小写十六进制')
    actual = _sha256_file(source_path)
    if actual != source_sha256:
        raise SubmissionContractError(
            '官方来源 SHA-256 不一致: contract=%s actual=%s'
            % (source_sha256, actual))


def _validate_submission(value, *, confirmed):
    submission = _require_exact_fields(value, {
        'single_sheet', 'sheet_name', 'expected_rows', 'column_order', 'all_cells_text',
        'row_order_equals_input', 'blank_rows_allowed',
    }, 'submission')
    _require_exact_or_none(
        submission['single_sheet'], True, 'submission.single_sheet',
        confirmed=confirmed)
    _require_text(
        submission['sheet_name'], 'submission.sheet_name',
        allow_none=not confirmed)
    expected_rows = submission['expected_rows']
    if expected_rows is None and not confirmed:
        pass
    elif type(expected_rows) is not int or expected_rows < 1:
        raise SubmissionContractError('submission.expected_rows 必须是正整数')
    _require_exact_or_none(
        submission['column_order'], _COLUMN_ORDER, 'submission.column_order',
        confirmed=confirmed)
    _require_exact_or_none(
        submission['all_cells_text'], True, 'submission.all_cells_text',
        confirmed=confirmed)
    _require_exact_or_none(
        submission['row_order_equals_input'], True,
        'submission.row_order_equals_input', confirmed=confirmed)
    _require_exact_or_none(
        submission['blank_rows_allowed'], False,
        'submission.blank_rows_allowed', confirmed=confirmed)


def _validate_company_id(value, *, confirmed):
    company_id = _require_exact_fields(value, {
        'column_name', 'type', 'leading_zero_rule', 'duplicate_rule',
    }, 'company_id')
    expected = {
        'column_name': 'company_id',
        'type': 'text',
        'leading_zero_rule': 'preserve_exactly',
        'duplicate_rule': 'forbidden',
    }
    for field, wanted in expected.items():
        _require_exact_or_none(
            company_id[field], wanted, 'company_id.%s' % field,
            confirmed=confirmed)


def _validate_action(value, *, confirmed):
    action = _require_exact_fields(
        value, {'encoding', 'cell_type', 'allowed_values'}, 'action')
    encoding = action['encoding']
    if encoding is None and not confirmed:
        pass
    else:
        encoding = _require_exact_fields(
            encoding, set(_ACTION_ENCODING), 'action.encoding')
        if encoding != _ACTION_ENCODING:
            raise SubmissionContractError(
                'action.encoding 必须为 %r' % _ACTION_ENCODING)
    _require_exact_or_none(
        action['cell_type'], 'text', 'action.cell_type', confirmed=confirmed)
    _require_exact_or_none(
        action['allowed_values'], _ACTION_VALUES, 'action.allowed_values',
        confirmed=confirmed)


def _validate_scoring_rule(value, *, confirmed):
    if value is None and not confirmed:
        return
    rule = _require_exact_fields(
        value, {'method', 'partial_credit', 'example'},
        'risk_control_advice.error_scoring_rule')
    _require_exact_or_none(
        rule['method'], _SCORING_METHOD,
        'risk_control_advice.error_scoring_rule.method', confirmed=confirmed)
    _require_exact_or_none(
        rule['partial_credit'], False,
        'risk_control_advice.error_scoring_rule.partial_credit',
        confirmed=confirmed)
    example = rule['example']
    if example is None and not confirmed:
        return
    example = _require_exact_fields(
        example, {'answer', 'submitted', 'score'},
        'risk_control_advice.error_scoring_rule.example')
    wanted = {'answer': [1, 2, 3], 'submitted': [2, 3, 4], 'score': '0'}
    if example != wanted:
        raise SubmissionContractError(
            'advice 计分示例必须保留官方原值 %r' % wanted)


def _validate_advice(value, *, confirmed):
    advice = _require_exact_fields(value, {
        'number_range', 'separator', 'min_items', 'max_items', 'duplicate_items_allowed',
        'error_scoring_rule',
    }, 'risk_control_advice')
    _require_exact_or_none(
        advice['number_range'], '1-9', 'risk_control_advice.number_range',
        confirmed=confirmed)
    _require_exact_or_none(
        advice['separator'], ',', 'risk_control_advice.separator',
        confirmed=confirmed)

    minimum = advice['min_items']
    if minimum is None and not confirmed:
        pass
    elif type(minimum) is not int or minimum != 0:
        raise SubmissionContractError(
            'risk_control_advice.min_items 必须是整数 0')

    maximum = advice['max_items']
    if maximum is None and not confirmed:
        pass
    elif type(maximum) is not int or not 1 <= maximum <= 9:
        raise SubmissionContractError(
            'risk_control_advice.max_items 必须是 1..9 的整数')

    _require_exact_or_none(
        advice['duplicate_items_allowed'], False,
        'risk_control_advice.duplicate_items_allowed', confirmed=confirmed)
    _validate_scoring_rule(advice['error_scoring_rule'], confirmed=confirmed)


def _validate_owner_decisions(value):
    owner = _require_exact_fields(value, {'note', 'decisions'}, 'owner_decisions')
    if owner['note'] is not None:
        _require_text(owner['note'], 'owner_decisions.note')
    if not isinstance(owner['decisions'], list):
        raise SubmissionContractError('owner_decisions.decisions 必须是数组')
    fields = {
        'topic', 'decision', 'decided_by', 'decided_at', 'official_confirmed',
    }
    for index, decision in enumerate(owner['decisions']):
        label = 'owner_decisions.decisions[%d]' % index
        decision = _require_exact_fields(decision, fields, label)
        for field in ('topic', 'decision', 'decided_by'):
            _require_text(decision[field], '%s.%s' % (label, field))
        _parse_iso_date(decision['decided_at'], '%s.decided_at' % label)
        if type(decision['official_confirmed']) is not bool:
            raise SubmissionContractError(
                '%s.official_confirmed 必须是布尔值' % label)


def _validate_payload(payload, project_root, *, require_confirmed):
    payload = _require_exact_fields(payload, _TOP_LEVEL_FIELDS, '提交契约')
    schema_version = payload['schema_version']
    _summary_fields_for_schema(schema_version)
    if type(payload['confirmed']) is not bool:
        raise SubmissionContractError('confirmed 必须是布尔值')
    confirmed = payload['confirmed']
    if require_confirmed and not confirmed:
        raise SubmissionContractError(
            '正式发布要求提交契约 confirmed=true；未知字段不得由默认值替代')

    _validate_received_at(payload['received_at'], confirmed=confirmed)
    _validate_source(payload['official_source'], project_root, confirmed=confirmed)
    _validate_submission(payload['submission'], confirmed=confirmed)
    _validate_company_id(payload['company_id'], confirmed=confirmed)
    _validate_action(payload['action'], confirmed=confirmed)
    _validate_advice(payload['risk_control_advice'], confirmed=confirmed)
    _validate_submission_selection(
        payload['scoring_submission_selection'], schema_version,
        confirmed=confirmed)
    _parse_iso_date(payload['as_of'], 'as_of', allow_none=not confirmed)
    _validate_owner_decisions(payload['owner_decisions'])
    if payload['notes'] is not None:
        _require_text(payload['notes'], 'notes')
    return payload


def _read_validated_contract(path, *, project_root, require_confirmed):
    """只解析一次字节快照，摘要和候选改写共享字段、重复键及来源校验。"""
    root = PROJECT_ROOT if project_root is None else Path(project_root)
    contract_path, relative_path = _resolve_project_file(path, root, '提交契约文件')
    try:
        with open(contract_path, 'rb') as fh:
            contract_bytes = fh.read()
        payload = json.loads(
            contract_bytes.decode('utf-8'),
            object_pairs_hook=_json_object_without_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubmissionContractError('提交契约 JSON 无法读取: %s' % path) from exc

    payload = _validate_payload(
        payload, root, require_confirmed=require_confirmed)
    return payload, relative_path, hashlib.sha256(contract_bytes).hexdigest()


def load_submission_contract_payload(path, *, project_root=None,
                                     require_confirmed=True):
    """返回严格校验后的独立 payload，供改写候选；不修改源文件。"""
    payload, _, _ = _read_validated_contract(
        path, project_root=project_root, require_confirmed=require_confirmed)
    return payload


def load_submission_contract(path, *, project_root=None, require_confirmed=True):
    """读取契约并返回可直接写入 release manifest 的规范摘要。"""
    payload, relative_path, contract_sha256 = _read_validated_contract(
        path, project_root=project_root, require_confirmed=require_confirmed)
    source = payload['official_source']
    advice = payload['risk_control_advice']
    summary = {
        'schema_version': payload['schema_version'],
        'path': relative_path,
        'sha256': contract_sha256,
        'confirmed': payload['confirmed'],
        'source_reference': source['reference'],
        'source_path': source['path'],
        'source_sha256': source['sha256'],
        'submission_sheet': payload['submission']['sheet_name'],
        'expected_firm_count': payload['submission']['expected_rows'],
        'advice_min_n': advice['min_items'],
        'advice_max_n': advice['max_items'],
        'scoring_rule': deepcopy(advice['error_scoring_rule']),
        'action_encoding': deepcopy(payload['action']['encoding']),
        'as_of': payload['as_of'],
    }
    if 'scoring_submission_selection' in _summary_fields_for_schema(
            payload['schema_version']):
        summary['scoring_submission_selection'] = payload[
            'scoring_submission_selection']
    return summary


def _validate_loaded_summary(contract):
    if not isinstance(contract, dict):
        raise SubmissionContractError('已加载提交契约必须是对象')
    schema_version = contract.get('schema_version')
    fields = _summary_fields_for_schema(schema_version)
    contract = _require_exact_fields(contract, fields, '已加载提交契约')
    path = _require_text(contract['path'], '已加载提交契约.path')
    pure = PurePosixPath(path)
    if (pure.is_absolute() or '\\' in path
            or any(part in ('', '.', '..') for part in pure.parts)
            or pure.as_posix() != path):
        raise SubmissionContractError('已加载提交契约.path 必须是规范相对路径')
    if (not isinstance(contract['sha256'], str)
            or not _HEX_SHA256.fullmatch(contract['sha256'])):
        raise SubmissionContractError('已加载提交契约.sha256 非法')
    if type(contract['confirmed']) is not bool:
        raise SubmissionContractError('已加载提交契约.confirmed 必须是布尔值')
    confirmed = contract['confirmed']

    source_values = [
        contract['source_reference'], contract['source_path'],
        contract['source_sha256'],
    ]
    if all(value is None for value in source_values) and not confirmed:
        pass
    elif any(value is None for value in source_values):
        raise SubmissionContractError('已加载提交契约来源字段必须同时填写')
    else:
        _require_text(
            contract['source_reference'], '已加载提交契约.source_reference')
        source_path = _require_text(
            contract['source_path'], '已加载提交契约.source_path')
        source_pure = PurePosixPath(source_path)
        if (source_pure.is_absolute() or '\\' in source_path
                or any(part in ('', '.', '..') for part in source_pure.parts)
                or source_pure.as_posix() != source_path):
            raise SubmissionContractError(
                '已加载提交契约.source_path 必须是规范相对路径')
        if not _HEX_SHA256.fullmatch(contract['source_sha256']):
            raise SubmissionContractError('已加载提交契约.source_sha256 非法')

    _require_text(
        contract['submission_sheet'], '已加载提交契约.submission_sheet',
        allow_none=not confirmed)
    expected_count = contract['expected_firm_count']
    if expected_count is None and not confirmed:
        pass
    elif type(expected_count) is not int or expected_count < 1:
        raise SubmissionContractError(
            '已加载提交契约.expected_firm_count 必须是正整数')
    minimum = contract['advice_min_n']
    if minimum is None and not confirmed:
        pass
    elif type(minimum) is not int or minimum != 0:
        raise SubmissionContractError(
            '已加载提交契约.advice_min_n 必须是整数 0')
    maximum = contract['advice_max_n']
    if maximum is None and not confirmed:
        pass
    elif type(maximum) is not int or not 1 <= maximum <= 9:
        raise SubmissionContractError(
            '已加载提交契约.advice_max_n 必须是 1..9 的整数')
    _validate_scoring_rule(contract['scoring_rule'], confirmed=confirmed)
    if 'scoring_submission_selection' in fields:
        _validate_submission_selection(
            contract['scoring_submission_selection'], schema_version,
            confirmed=confirmed)
    encoding = contract['action_encoding']
    if encoding is None and not confirmed:
        pass
    elif encoding != _ACTION_ENCODING:
        raise SubmissionContractError(
            '已加载提交契约.action_encoding 必须为 %r' % _ACTION_ENCODING)
    _parse_iso_date(
        contract['as_of'], '已加载提交契约.as_of', allow_none=not confirmed)
    return contract


def contract_manifest(contract):
    """返回隔离副本，避免 manifest 组装过程污染已验证对象。"""
    contract = _validate_loaded_summary(contract)
    return deepcopy(contract)


def assert_contract_matches(contract, *, advice_max_n, submission_sheet,
                            as_of, contract_reference=None):
    """确认正式运行参数逐项等于官方契约；引用可省略但不可伪造。"""
    checked = contract_manifest(contract)
    if checked['confirmed'] is not True:
        raise SubmissionContractError('正式运行禁止使用未确认提交契约')
    if type(advice_max_n) is not int or advice_max_n != checked['advice_max_n']:
        raise SubmissionContractError(
            '运行 advice_max_n 与提交契约不一致: run=%r contract=%r'
            % (advice_max_n, checked['advice_max_n']))
    if submission_sheet != checked['submission_sheet']:
        raise SubmissionContractError(
            '运行 submission_sheet 与提交契约不一致: run=%r contract=%r'
            % (submission_sheet, checked['submission_sheet']))
    normalized_as_of = _parse_iso_date(as_of, '运行 as_of')
    if normalized_as_of != checked['as_of']:
        raise SubmissionContractError(
            '运行 as_of 与提交契约不一致: run=%r contract=%r'
            % (normalized_as_of, checked['as_of']))
    if (contract_reference is not None
            and contract_reference != checked['source_reference']):
        raise SubmissionContractError(
            '运行 contract_reference 与提交契约来源引用不一致: '
            'run=%r contract=%r'
            % (contract_reference, checked['source_reference']))
    return contract
