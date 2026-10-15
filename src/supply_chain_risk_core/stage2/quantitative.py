"""阶段二可独立复用的成本分配、采购组合和收入交叉区间计算。"""
from decimal import Decimal, InvalidOperation
import re

HUNDRED = Decimal(100)
NUMBER = re.compile(r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")

def number(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{label}必须为明确数值")
    if isinstance(value, str) and "," in value and NUMBER.fullmatch(value) is None:
        raise ValueError(f"{label}包含非法数字分组")
    try:
        result = Decimal(str(value).replace(",", ""))
    except InvalidOperation as error:
        raise ValueError(f"{label}不是有效数值") from error
    if not result.is_finite():
        raise ValueError(f"{label}必须为有限数值")
    return result


def fraction(value: object, label: str) -> Decimal:
    result = number(value, label)
    if not 0 <= result <= 1:
        raise ValueError(f"{label}必须在0至1之间")
    return result


def ratio_pct(numerator: object, denominator: object) -> Decimal:
    a, b = number(numerator, "分子"), number(denominator, "分母")
    if b <= 0:
        raise ValueError("比例分母必须大于0")
    return HUNDRED * a / b

def price_change_fraction(value: object, label: str) -> Decimal:
    result = number(value, label)
    if result < -1:
        raise ValueError(f"{label}不能低于-100%，采购价格不能为负")
    return result


def retained_cost_per_base_revenue(material_to_revenue: object, copper_share: object,
                                   unhedged: object, price_change: object, transfer: object) -> Decimal:
    """转价额按增量成本计；结果为每百元基期收入吸收额，非新收入分母毛利率。"""
    weight = fraction(material_to_revenue, "直接材料占收入") * fraction(copper_share, "材料内铜权重")
    exposure = fraction(unhedged, "未对冲净敞口")
    return HUNDRED * weight * exposure * price_change_fraction(price_change, "价格变化") * (1 - fraction(transfer, "成本转嫁率"))


def feed_cost_change(corn_change: object, soymeal_change: object,
                     corn_share: object, soymeal_share: object) -> Decimal:
    corn, soy = fraction(corn_share, "玉米采购金额权重"), fraction(soymeal_share, "豆粕采购金额权重")
    if corn + soy > 1:
        raise ValueError("玉米与豆粕采购金额权重之和不能超过1")
    return HUNDRED * (corn * price_change_fraction(corn_change, "玉米变化")
                      + soy * price_change_fraction(soymeal_change, "豆粕变化"))


def neutral_feed_ratio(corn_change: object, soymeal_change: object) -> Decimal:
    corn, soy = price_change_fraction(corn_change, "玉米变化"), price_change_fraction(soymeal_change, "豆粕变化")
    if corn <= 0 or soy >= 0:
        raise ValueError("当前中性权重比公式要求玉米上涨且豆粕下跌")
    return -soy / corn

def board_transfer_costs(incremental_cost: object, board_transfer: object, brand_transfer: object) -> dict:
    """同批一板配一机、同一存储增量的成本分配；不比较总体风险或毛利率。"""
    delta = number(incremental_cost, "存储成本增量")
    if delta < 0:
        raise ValueError("本情景存储成本增量必须为非负值")
    t, u = fraction(board_transfer, "板卡转嫁率"), fraction(brand_transfer, "品牌继续转嫁率")
    board, brand, downstream = delta * (1 - t), delta * t * (1 - u), delta * t * u
    difference = brand - board
    return {"board_transfer_fraction": t, "brand_transfer_fraction": u,
            "board_retained_cost": board, "brand_retained_cost": brand,
            "downstream_cost": downstream, "brand_minus_board_cost": difference,
            "comparison": "品牌吸收较多" if difference > 0 else "板卡吸收较多" if difference < 0 else "两者相同"}

def revenue_overlap_bounds(total: Decimal, product: Decimal, region: Decimal) -> tuple[Decimal, Decimal]:
    """只有产品与地区边际合计时，交叉收入仅能由非负加总约束识别为一个区间。"""
    total, product, region = (number(value, label) for value, label in ((total, "总收入"), (product, "产品收入"), (region, "地区收入")))
    if not 0 < product <= total or not 0 <= region <= total:
        raise ValueError("产品、地区收入须为同一合并范围内的非负金额，产品收入须为正")
    return max(Decimal(0), product + region - total), min(product, region)
