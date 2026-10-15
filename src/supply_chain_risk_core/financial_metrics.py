"""统一财务口径；旧版净财务费用代理只在显式历史迁移入口接受。"""
from __future__ import annotations

import math
import json
import re
from datetime import date

from .submission_contract import SubmissionContractError, _json_object_without_duplicates

CANONICAL_EBIT_BASIS = 'total_profit_plus_interest_expense'
LEGACY_EBIT_BASIS = 'total_profit_plus_finance_expense'
EBIT_TOLERANCE_YI = 0.00015


def compute_ebit(total_profit, interest_expense, *, allow_missing=False):
    """息税前利润不把利息收入、汇兑损益等净财务费用混入利息费用。"""
    profit = _number(total_profit, 'total_profit_yi', allow_missing)
    interest = _number(interest_expense, 'interest_expense_yi', allow_missing)
    if interest < 0:
        raise ValueError('interest_expense_yi 不得为负')
    result = profit + interest
    if math.isinf(result):
        raise ValueError('EBIT 超出有限数范围')
    return result


def interest_coverage(ebit, interest_expense):
    """无利息费用时倍数无定义；调用方分别表达无付息压力与数值展示。"""
    ebit = _number(ebit, 'ebit_yi', False)
    interest_expense = _number(interest_expense, 'interest_expense_yi', False)
    if interest_expense < 0:
        raise ValueError('interest_expense_yi 不得为负')
    if not (math.isfinite(ebit) and math.isfinite(interest_expense)):
        raise ValueError('利息保障倍数的组成项必须为有限数')
    if interest_expense == 0:
        return None
    result = ebit / interest_expense
    if not math.isfinite(result):
        raise ValueError('利息保障倍数超出有限数范围')
    return result


def _number(value, field, allow_missing):
    if value is None or isinstance(value, str) and not value.strip():
        if allow_missing:
            return math.nan
        raise ValueError('%s 缺失' % field)
    if isinstance(value, bool):
        raise ValueError('%s 不得为布尔值' % field)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('%s 不是数值: %r' % (field, value)) from exc
    if math.isnan(number) and allow_missing:
        return number
    if not math.isfinite(number):
        raise ValueError('%s 不是有限数: %r' % (field, value))
    return number


def _check_match(actual, expected, label, allow_missing):
    if allow_missing and (math.isnan(actual) or math.isnan(expected)):
        return
    if not math.isclose(actual, expected, rel_tol=0, abs_tol=EBIT_TOLERANCE_YI):
        raise ValueError('%s 与财务组成项不一致: %r != %r' % (label, actual, expected))


def canonicalize_earnings(record, *, allow_legacy=False, allow_missing=False):
    """返回新记录；仅显式 allow_legacy=True 才按已知旧代理定义迁移。

    默认关闭历史兼容。新生产者必须给出 canonical basis、利润总额
    和利息费用；不根据数字恰好相等猜测版本，也不把未知当成零。
    """
    if type(allow_legacy) is not bool or type(allow_missing) is not bool:
        raise TypeError('兼容与缺失值开关必须为布尔值')
    basis = record.get('ebit_basis')
    if (basis is None or isinstance(basis, str) and not basis.strip()
            or isinstance(basis, float) and math.isnan(basis)):
        basis = LEGACY_EBIT_BASIS
    if basis not in (LEGACY_EBIT_BASIS, CANONICAL_EBIT_BASIS):
        raise ValueError('不支持的 ebit_basis: %r' % basis)
    if basis == LEGACY_EBIT_BASIS and not allow_legacy:
        raise ValueError('旧版净财务费用代理输入已禁用，请迁移 ebit_basis')
    book = _number(record.get('ebit_yi'), 'ebit_yi', allow_missing)
    interest = _number(record.get('interest_expense_yi'), 'interest_expense_yi', allow_missing)
    if interest < 0:
        raise ValueError('interest_expense_yi 不得为负')
    normalized = dict(record)
    if basis == LEGACY_EBIT_BASIS:
        net_finance = _number(record.get('finance_expense_yi'), 'finance_expense_yi', allow_missing)
        raw_profit = record.get('total_profit_yi')
        if (raw_profit is None or raw_profit == ''
                or isinstance(raw_profit, float) and math.isnan(raw_profit)):
            profit = book - net_finance
        else:
            profit = _number(record['total_profit_yi'], 'total_profit_yi', allow_missing)
            _check_match(book, profit + net_finance, '历史净财务费用代理', allow_missing)
        normalized['net_finance_ebit_proxy_yi'] = book
        normalized['earnings_migration'] = {
            'input_basis': record.get('ebit_basis'),
            'assumed_legacy_basis': LEGACY_EBIT_BASIS,
            'rule': 'profit = old_ebit - finance_expense; ebit = profit + interest_expense',
        }
    else:
        if 'finance_expense_yi' in record:
            _number(record['finance_expense_yi'], 'finance_expense_yi', allow_missing)
        profit = _number(record.get('total_profit_yi'), 'total_profit_yi', allow_missing)
        _check_match(book, compute_ebit(profit, interest, allow_missing=allow_missing), 'EBIT', allow_missing)
    ebit = compute_ebit(profit, interest, allow_missing=allow_missing)
    normalized.update(total_profit_yi=profit, interest_expense_yi=interest,
                      ebit_yi=ebit, ebit_basis=CANONICAL_EBIT_BASIS,
                      interest_cover_x=(None if math.isnan(ebit) or math.isnan(interest)
                                        else interest_coverage(ebit, interest)))
    return normalized


LIQUIDITY_ADJUSTMENT_FIELDS = frozenset((
    'schema_version', 'company_id', 'annual_pdf_sha256', 'pub_date', 'source_url',
    'pdf_pages', 'cash_restricted_yi', 'cash_earmarked_yi',
    'other_short_borrowings_yi', 'non_financing_deductions_yi'))
LIQUIDITY_AMOUNT_FIELDS = (
    'cash_restricted_yi', 'cash_earmarked_yi',
    'other_short_borrowings_yi', 'non_financing_deductions_yi')
SUBSEQUENT_BALANCE_FIELDS = frozenset((
    'company_id', 'credit_code', 'balance_date', 'pub_date', 'source_url',
    'pdf_sha256', 'pdf_pages', 'cash_yi', 'short_term_debt_yi',
    *LIQUIDITY_AMOUNT_FIELDS))
LIQUIDITY_BASIS = 'disclosed_cash_constraints_and_short_financing'
ANNUAL_FALLBACK_BASIS = 'annual_' + LIQUIDITY_BASIS + '_subsequent_incomplete'


def _liquidity_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('%s 必须为日精度日期' % field)
    return date.fromisoformat(value)


def _liquidity_source(source, digest_key, as_of):
    digest = source[digest_key]
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('流动性证据缺有效 SHA-256')
    url = source['source_url']
    if not isinstance(url, str) or not url.startswith('https://') or not url[8:].split('/')[0]:
        raise ValueError('流动性证据缺公开 HTTPS 来源')
    pages = source['pdf_pages']
    if not isinstance(pages, list) or not pages or any(type(p) is not int or p <= 0 for p in pages):
        raise ValueError('流动性证据缺物理页码')
    published = _liquidity_date(source['pub_date'], '流动性证据公开日')
    if as_of is not None and published > _liquidity_date(str(as_of)[:10], 'as_of'):
        raise ValueError('流动性证据公开日越过截止日')
    return published


def _parse_liquidity_adjustment(raw):
    if not isinstance(raw, str):
        raise ValueError('liquidity_adjustments 必须为显式 JSON 文本')
    try:
        adjustment = json.loads(raw, object_pairs_hook=_json_object_without_duplicates)
    except json.JSONDecodeError as exc:
        raise ValueError('liquidity_adjustments 不是有效 JSON') from exc
    except SubmissionContractError as exc:
        raise ValueError('liquidity_adjustments 含重复字段') from exc
    if not isinstance(adjustment, dict):
        raise ValueError('liquidity_adjustments 字段集合不合格')
    version = adjustment.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('不支持的 liquidity_adjustments schema_version')
    expected = LIQUIDITY_ADJUSTMENT_FIELDS
    if version == 2:
        expected = expected | {'subsequent_balance'}
    if set(adjustment) != expected:
        raise ValueError('liquidity_adjustments 字段集合不合格')
    return adjustment


def _adjusted_liquidity(cash, debt, adjustment, *, allow_unknown):
    amounts = {name: (math.nan if allow_unknown and adjustment[name] is None
                      else _number(adjustment[name], name, False))
               for name in LIQUIDITY_AMOUNT_FIELDS}
    if any(value < 0 for value in amounts.values()) or not (math.isfinite(cash) and math.isfinite(debt)):
        raise ValueError('流动性调整金额为负或账面组成项缺失')
    if cash < 0 or debt < 0:
        raise ValueError('流动性账面现金或短债不得为负')
    restricted = amounts['cash_restricted_yi']
    earmarked = amounts['cash_earmarked_yi']
    # 已知限制的下界已超过账面数时，即使其他限制未知也必须拒绝。
    known_restrictions = sum(value for value in (restricted, earmarked) if math.isfinite(value))
    if known_restrictions > cash:
        raise ValueError('流动性现金限制超过账面金额，可能重复扣除或单位错误')
    available = cash - restricted - earmarked
    financing = debt + amounts['other_short_borrowings_yi'] - amounts['non_financing_deductions_yi']
    if available < 0 or financing < 0:
        raise ValueError('流动性调整超过账面金额，可能重复扣除或单位错误')
    return {'cash_for_debt_service_yi': available, 'short_financing_debt_yi': financing,
            'liquidity_basis': LIQUIDITY_BASIS}


def _subsequent_liquidity(record, snapshot, as_of, annual, allow_annual_fallback):
    if as_of is None:
        raise ValueError('后续流动性余额必须提供明确 as_of 截止日')
    if not isinstance(snapshot, dict) or set(snapshot) != SUBSEQUENT_BALANCE_FIELDS:
        raise ValueError('subsequent_balance 字段集合不合格')
    if snapshot['company_id'] != str(record.get('company_id')):
        raise ValueError('后续流动性证据主体不一致')
    credit_code = record.get('credit_code')
    if not isinstance(credit_code, str) or not credit_code or snapshot['credit_code'] != credit_code:
        raise ValueError('后续流动性证据统一社会信用代码不一致')
    year = _number(record.get('fiscal_year'), 'fiscal_year', False)
    if year != int(year):
        raise ValueError('fiscal_year 必须为完整会计年度')
    annual_date = date(int(year), 12, 31)
    observed = _liquidity_date(snapshot['balance_date'], '后续资产负债表日')
    published = _liquidity_source(snapshot, 'pdf_sha256', as_of)
    if not annual_date < observed <= published:
        raise ValueError('后续资产负债表日必须晚于年末且不晚于公开日')
    result = _adjusted_liquidity(
        _number(snapshot['cash_yi'], '后续 cash_yi', False),
        _number(snapshot['short_term_debt_yi'], '后续 short_term_debt_yi', False),
        snapshot, allow_unknown=True)
    incomplete = any(math.isnan(result[name]) for name in (
        'cash_for_debt_service_yi', 'short_financing_debt_yi'))
    if incomplete and not allow_annual_fallback:
        raise ValueError('后续流动性披露不完整；如需历史年报口径须显式开启 allow_annual_fallback')
    if incomplete:
        # 不把年报限制金额套到后续余额上（那是混用资产负债表日），而是整体改用
        # 年末同日口径；后续余额日与来源仍然留痕，口径名显式标注为回退。
        fallback = dict(annual, liquidity_basis=ANNUAL_FALLBACK_BASIS)
        fallback.update(liquidity_balance_date=annual_date.isoformat(),
                        liquidity_source_sha256=record.get('annual_pdf_sha256'),
                        liquidity_subsequent_balance_date=observed.isoformat(),
                        liquidity_subsequent_source_sha256=snapshot['pdf_sha256'])
        return fallback
    result.update(liquidity_basis=('subsequent_balance_disclosures_incomplete' if incomplete
                                  else 'subsequent_' + LIQUIDITY_BASIS),
                  liquidity_balance_date=observed.isoformat(),
                  liquidity_source_sha256=snapshot['pdf_sha256'])
    return result


def liquidity_metrics(record, *, as_of=None, allow_legacy=False, allow_annual_fallback=False):
    """只在统一入口计算偿债口径，不覆写原始余额或混合不同资产负债表日。

    schema_version=2 显式启用后续同日余额；只有 JSON null 表示当期明细未知。
    不继承年末限制，也不把融资流量、理财或授信授权额度加到期末存量。
    保留 schema_version=1（不带 subsequent_balance）即关闭后续余额消费；该口径
    仍仅代表历史年报时点。账面兼容和后续披露不全时的年报回退默认均关闭，
    仅通过 allow_legacy / allow_annual_fallback 显式开启并保留口径标记。
    """
    if type(allow_legacy) is not bool or type(allow_annual_fallback) is not bool:
        raise TypeError('兼容与回退开关必须为布尔值')
    cash = _number(record.get('cash_yi'), 'cash_yi', False)
    debt = _number(record.get('short_term_debt_yi'), 'short_term_debt_yi', False)
    if cash < 0 or debt < 0:
        raise ValueError('流动性账面现金或短债不得为负')
    raw = record.get('liquidity_adjustments')
    missing = (raw is None or isinstance(raw, str) and not raw.strip()
               or isinstance(raw, float) and math.isnan(raw))
    if missing:
        if not allow_legacy:
            raise ValueError('缺少流动性调整证据，历史账面口径已禁用')
        return {'cash_for_debt_service_yi': cash, 'short_financing_debt_yi': debt,
                'liquidity_basis': 'book_cash_availability_not_reviewed'}
    adjustment = _parse_liquidity_adjustment(raw)
    if adjustment['company_id'] != str(record.get('company_id')):
        raise ValueError('流动性证据主体不一致')
    _liquidity_source(adjustment, 'annual_pdf_sha256', as_of)
    if adjustment['annual_pdf_sha256'] != record.get('annual_pdf_sha256'):
        raise ValueError('流动性证据未绑定当前年报 SHA-256')
    annual = _adjusted_liquidity(cash, debt, adjustment, allow_unknown=False)
    if adjustment['schema_version'] == 1:
        return annual
    return _subsequent_liquidity(record, adjustment['subsequent_balance'], as_of,
                                 annual=annual, allow_annual_fallback=allow_annual_fallback)
