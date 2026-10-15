"""显式投入产出矩阵上的总需求与固定源头价格冲击。"""
import numpy as np

from .validation import finite_number, nonempty_list, text

DEFAULT_MAX_NODES = 256
DEFAULT_CONDITION_LIMIT = 1e12


def validate_matrix(nodes, coefficients, *, max_nodes=DEFAULT_MAX_NODES):
    nonempty_list(nodes, "nodes")
    if max_nodes is not None and (type(max_nodes) is not int or max_nodes < 1):
        raise ValueError("max_nodes 须为正整数或 None")
    if max_nodes is not None and len(nodes) > max_nodes:
        raise ValueError(f"矩阵节点数超过预算 {max_nodes}；可显式调整 max_nodes")
    labels = [text(node, "node") for node in nodes]
    if len(set(labels)) != len(labels):
        raise ValueError("投入产出节点名称重复")
    if not isinstance(coefficients, list) or len(coefficients) != len(nodes):
        raise ValueError("直接消耗矩阵行数须等于节点数")
    rows = []
    for row in coefficients:
        if not isinstance(row, list) or len(row) != len(nodes):
            raise ValueError("直接消耗矩阵必须为方阵")
        numbers = [finite_number(value, "直接消耗系数") for value in row]
        if any(value < 0 for value in numbers):
            raise ValueError("直接消耗系数不得为负")
        rows.append(numbers)
    return labels, np.asarray(rows, dtype=float)


def _system(matrix, condition_limit):
    radius = float(np.max(np.abs(np.linalg.eigvals(matrix))))
    if not np.isfinite(radius) or radius >= 1:
        raise ValueError("直接消耗矩阵的谱半径须小于 1")
    system = np.eye(len(matrix)) - matrix
    condition = float(np.linalg.cond(system))
    if condition_limit is not None:
        limit = finite_number(condition_limit, "condition_limit")
        if limit <= 1:
            raise ValueError("condition_limit 须大于 1 或为 None")
        if condition > limit:
            raise ValueError(f"矩阵病态，条件数 {condition:g} 超过 {limit:g}")
    return system, radius, condition


def analyze_input_output(nodes, coefficients, price_source, *,
                         max_nodes=DEFAULT_MAX_NODES, condition_limit=DEFAULT_CONDITION_LIMIT):
    """A[i,j] 是行业 j 单位产出直接消耗行业 i 的金额。"""
    labels, matrix = validate_matrix(nodes, coefficients, max_nodes=max_nodes)
    if price_source not in labels:
        raise ValueError("price_source 未登记在 nodes 中")
    system, radius, condition = _system(matrix, condition_limit)
    leontief = np.linalg.solve(system, np.eye(len(labels)))
    source = labels.index(price_source)
    others = [index for index in range(len(labels)) if index != source]
    prices = np.zeros(len(labels))
    prices[source] = 1
    if others:
        # 固定源头自身价格；其余行业的成本方程只对其他价格求解。
        block = np.eye(len(others)) - matrix[np.ix_(others, others)].T
        prices[others] = np.linalg.solve(block, matrix[source, others])
    if not np.isfinite(leontief).all() or not np.isfinite(prices).all():
        raise ValueError("投入产出求解产生非有限结果")
    return {
        "nodes": labels, "direct_coefficients": matrix.tolist(),
        "coefficient_basis": "A[i,j] = 行业j每单位产出直接消耗的行业i投入",
        "leontief_inverse": leontief.tolist(),
        "total_consumption": (leontief - np.eye(len(labels))).tolist(),
        "price_source": price_source,
        "source_price_coefficients": dict(zip(labels, prices.tolist())),
        "price_basis": "固定源头产品价格，其他行业按投入成本变化求解",
        "spectral_radius": radius, "condition_number": condition,
        "max_nodes": max_nodes, "condition_limit": condition_limit,
    }
