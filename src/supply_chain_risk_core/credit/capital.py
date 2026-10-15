"""工商资本的唯一文字语法与万元人民币换算入口。"""
from decimal import (
    Context, Decimal, DecimalException, Inexact, InvalidOperation,
    MAX_EMAX, MIN_EMIN, Overflow, ROUND_HALF_EVEN, Underflow, localcontext,
)
import math
import re
import unicodedata

from ..missing_values import is_missing_text


# 固定近似汇率只服务特征可比性，不代表实时结售汇价格。
_FX = {"元": "1", "人民币": "1", "元人民币": "1", "美元": "7.2",
       "欧元": "7.8", "香港元": "0.92", "港元": "0.92", "英镑": "9.1", "日元": "0.05"}
_MAGNITUDES = {"": "0.0001", "万": "1", "亿": "10000"}
_INTEGER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,3}(?: +[0-9]{3})+|[0-9]+)"
_NUMBER = rf"\+?(?:{_INTEGER}(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_CURRENCIES = "|".join(sorted(_FX, key=len, reverse=True))
_CAPITAL = re.compile(rf"(?P<number>{_NUMBER}) *(?P<magnitude>亿|万)? *(?P<currency>{_CURRENCIES})")


def parse_capital_wan(value):
    """返回万元人民币；显式缺失返回 NaN，非空坏数据抛错。"""
    if value is None or isinstance(value, float) and math.isnan(value):
        return math.nan
    if not isinstance(value, str):
        raise ValueError("资本金额须为带货币单位的原始文字或 null")
    if is_missing_text(value):
        return math.nan
    normalized = re.sub(r"\s", " ", unicodedata.normalize("NFKC", value)).strip()
    match = _CAPITAL.fullmatch(normalized)
    if match is None:
        raise ValueError("资本金额的数值、千分位或货币单位不能解析")
    number = re.sub(r"[ ,]", "", match["number"])
    try:
        amount = Decimal(number)
        scale = Decimal(_MAGNITUDES[match["magnitude"] or ""])
        rate = Decimal(_FX[match["currency"]])
        # 有效位数之和足以容纳精确乘积；不继承调用方的精度、舍入或指数范围。
        precision = sum(len(value.as_tuple().digits) for value in (amount, scale, rate))
        context = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX,
                          capitals=1, clamp=0, flags=[], traps=[InvalidOperation, Overflow, Underflow, Inexact])
        with localcontext(context):
            result = float(amount * scale * rate)
    except (DecimalException, OverflowError) as error:
        raise ValueError("资本金额换算超出有限数范围") from error
    if not math.isfinite(result) or (amount != 0 and result == 0):
        raise ValueError("资本金额换算溢出或下溢")
    return result
