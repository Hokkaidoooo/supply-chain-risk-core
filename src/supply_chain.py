"""产业链路径传播：从参赛实现抽离，图、系数与出处由调用方明确提供。

没有内置行业表或企业数据。路径乘积表示给定系数下的传导暴露，
不是违约概率；多路径取最大值，不冒充 Leontief 逆得到的总效应。
"""
from math import isfinite
import networkx as nx

DEPENDENCY_ORDER = {"无": 0, "弱": 1, "强": 2, "核心": 3}
MAX_HOPS = 8


def _validate(graph, max_hops, endpoint_only):
    if not isinstance(graph, nx.DiGraph) or graph.is_multigraph():
        raise TypeError("graph 必须是 NetworkX DiGraph，不接受多重边图")
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= MAX_HOPS:
        raise ValueError(f"max_hops 必须为 1 到 {MAX_HOPS} 的整数")
    if not set(endpoint_only).issubset(graph.nodes):
        raise ValueError("endpoint_only 中的节点必须存在于图中")
    for _, _, data in graph.edges(data=True):
        weight = data.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError("每条边必须提供数值 weight")
        if not isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError("路径模型要求有限且位于 [0, 1] 的边权")


def _best_exposures(node, graph, max_hops, endpoint_only, upstream):
    _validate(graph, max_hops, endpoint_only)
    if node not in graph:
        return {}
    result = {}
    stack = [(node, 1.0, frozenset([node]), 0)]
    while stack:
        current, strength, visited, hops = stack.pop()
        if hops >= max_hops or current != node and current in endpoint_only:
            continue
        neighbors = graph.predecessors(current) if upstream else graph.successors(current)
        for neighbor in neighbors:
            if neighbor in visited:
                continue
            edge = graph[neighbor][current] if upstream else graph[current][neighbor]
            value = strength * edge["weight"]
            result[neighbor] = max(value, result.get(neighbor, 0.0))
            stack.append((neighbor, value, visited | {neighbor}, hops + 1))
    return result


def upstream_of(node, graph, max_hops=2, *, endpoint_only=frozenset()):
    """上游最强简单路径暴露；空结果仅表示图中无覆盖或无可达节点。"""
    return _best_exposures(node, graph, max_hops, endpoint_only, upstream=True)


def downstream_of(node, graph, max_hops=2, *, endpoint_only=frozenset()):
    """下游最强简单路径暴露；聚合代理可作为端点，但不继续穿透。"""
    return _best_exposures(node, graph, max_hops, endpoint_only, upstream=False)


def transmission_paths(node, graph, max_hops=3, top_k=10, min_strength=1e-3,
                       *, endpoint_only=frozenset()):
    """枚举到目标节点的成本传导路径，保留边级出处与估计标记。

    节点别名由数据适配层处理。table_year 从 graph.graph 读取，
    未提供时为 None，不能猜测数据年份。
    """
    _validate(graph, max_hops, endpoint_only)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k 必须为正整数")
    if (isinstance(min_strength, bool) or not isinstance(min_strength, (int, float))
            or not isfinite(min_strength) or not 0 <= min_strength <= 1):
        raise ValueError("min_strength 必须为 [0, 1] 的有限数")
    for _, _, data in graph.edges(data=True):
        if data.get("dep") not in DEPENDENCY_ORDER:
            raise ValueError("路径报告的每条边必须显式提供 dep：无、弱、强或核心")
        if "estimated" not in data or not isinstance(data["estimated"], bool):
            raise ValueError("路径报告的每条边必须显式提供布尔 estimated")
    if node not in graph:
        return []

    paths = []
    stack = [(node, [node], 1.0)]
    while stack:
        current, chain, strength = stack.pop()
        if len(chain) - 1 >= max_hops or current != node and current in endpoint_only:
            continue
        for parent in graph.predecessors(current):
            if parent in chain:
                continue
            value = strength * graph[parent][current]["weight"]
            if value < min_strength:
                continue
            nodes = [parent, *chain]
            edges = [graph[a][b] for a, b in zip(nodes, nodes[1:])]
            dependencies = [edge["dep"] for edge in edges]
            paths.append({
                "mechanism": "cost_push",
                "coefficient_type": "direct_input_coefficient_a",
                "table_year": graph.graph.get("table_year"),
                "source": parent, "target": node,
                "path": " -> ".join(map(str, nodes)), "nodes": nodes,
                "hops": len(nodes) - 1, "strength": value,
                "dep_chain": dependencies,
                "bottleneck_dep": min(dependencies, key=DEPENDENCY_ORDER.__getitem__),
                "edge_provenance": [edge.get("provenance") for edge in edges],
                "contains_estimated_edge": any(edge["estimated"] for edge in edges),
            })
            stack.append((parent, nodes, value))
    return sorted(paths, key=lambda item: item["strength"], reverse=True)[:top_k]


def credit_transmission_paths(node, factual_edges=None):
    """未实现企业事实关系消费；无事实边时不编造信用传导路径。"""
    if not factual_edges:
        return []
    raise NotImplementedError("信用传导需要独立的事实边处理，当前公开核心未实现")
