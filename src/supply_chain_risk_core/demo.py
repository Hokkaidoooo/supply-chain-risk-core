"""在实际比赛模块上运行合成示例，不生成比赛预测或提交件。"""
import json

import networkx as nx

from ._console import configure_utf8_output
from .financial_metrics import CANONICAL_EBIT_BASIS, canonicalize_earnings
from .supply_chain import downstream_of


def main():
    configure_utf8_output()
    graph = nx.DiGraph()
    graph.add_weighted_edges_from([
        ("原料", "加工", 0.5),
        ("加工", "终端", 0.4),
        ("原料", "替代环节", 0.3),
        ("替代环节", "终端", 0.8),
    ])
    exposure = downstream_of("原料", graph, max_hops=2)
    earnings = canonicalize_earnings({
        "ebit_basis": CANONICAL_EBIT_BASIS,
        "total_profit_yi": 1.2,
        "interest_expense_yi": 0.3,
        "ebit_yi": 1.5,
    }, allow_legacy=False, allow_missing=False)
    print(json.dumps({
        "data_kind": "synthetic_demonstration",
        "scope": "最强简单路径的系数连乘；不是冲击概率或 Leontief 总效应",
        "downstream_exposure": exposure,
        "terminal_expected": max(0.5 * 0.4, 0.3 * 0.8),
        "earnings": earnings,
        "limitations": [
            "示例边与金额均为合成数据，不对应真实企业或比赛结果。",
            "没有运行完整风险评分或生成信贷建议。",
            "财务金额统一使用亿元；零利息费用的保障倍数无定义。",
        ],
    }, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
