# -*- coding: utf-8 -*-
"""缺失哨兵词表的唯一真源。

工商登记数据用多种写法表示"无此项"：空白、``-``、``—``、``无``、``未公示``、
``未披露``、``暂无``、``/``、``N/A``。此前 data_loading（文件契约）、
field_profile（画像闸门）、deep_features（特征构造）三处各自维护一份很窄的词表
（只认 ``-``/nan/none/nat/null/空串），于是同一个单元格在一处算"缺失"、
在另一处算"已披露"。

后果不是崩溃而是**静默反向**：``网址='无'`` 会让 ``has_website=1``（本该 0，
既误加分又把 website_absent 从空壳分里摘掉），``曾用名='无'`` 伪造
``rename_recent_change`` 信号，``最新年报地址='无'`` 伪造
``addr_mismatch_strong``（+12 分且可触发 R4 组合硬收紧）。字段漂移闸门只检查
非空率相对基线的**跌幅**，哨兵填充反而让非空率**上升**，因此一路放行。

本模块无任何项目内依赖，三处统一 import 它即可，不存在循环 import 问题
（这正是历史上三份副本各自漂移的原因）。
"""

# 全部以小写、去首尾空格后比对。只收录明确表示"该项不存在/未提供"的写法；
# 掩码（***）不在此列——掩码意味着有值但被隐去，与"无此项"语义不同。
MISSING_TEXT = frozenset({
    '',
    # 各种破折号/连字符（半角、全角、中文破折号、en/em dash）
    '-', '--', '---', '—', '——', '–', '－', '‐', '_',
    # 斜杠占位
    '/', '//', '／',
    # 编程/表格空值
    'nan', 'none', 'nat', 'null', 'nil', 'n/a', 'na', 'n.a.', '#n/a',
    # 中文缺失表述
    '无', '無', '暂无', '暫無', '没有', '空', '空白',
    '未公示', '未披露', '未提供', '未填写', '未填報', '未知', '不详', '不詳',
})


def is_missing_text(value) -> bool:
    """判断单个取值是否表示"缺失"。

    对 None、float('nan')、pandas 的 NaT/NA 以及上表中的文本写法返回 True。
    不导入 pandas/numpy：用 ``value != value`` 识别 NaN，用 duck typing 兜
    pandas 的空值单例，保证本模块零依赖。
    """
    if value is None:
        return True
    # NaN（含 numpy.float64('nan')）唯一不等于自身；pd.NaT 同样满足。
    try:
        if value != value:
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in MISSING_TEXT


def normalize_missing(value, placeholder='-'):
    """缺失归一为 ``placeholder``，否则返回去首尾空格的原文本。"""
    if is_missing_text(value):
        return placeholder
    return str(value).strip()
