"""产业链路径传播：从参赛实现抽离，图、系数与出处由调用方明确提供。

没有内置行业表或企业数据。路径乘积表示给定系数下的传导暴露，
不是违约概率；多路径取最大值，不冒充 Leontief 逆得到的总效应。
"""
from math import isfinite
from dataclasses import dataclass
from time import perf_counter
import heapq
import networkx as nx

from .facts import credit_transmission_paths

DEPENDENCY_ORDER = {"无": 0, "弱": 1, "强": 2, "核心": 3}
MAX_HOPS = 8
DEFAULT_MAX_EXPANSIONS = 100_000


class TraversalBudgetExceeded(RuntimeError):
    """预算耗尽时不返回局部结果，计数可用于诊断。"""
    def __init__(self, limit, examined_edges, emitted_paths):
        super().__init__(f'路径遍历超过 max_expansions={limit}，结果不完整')
        self.limit = limit
        self.examined_edges = examined_edges
        self.emitted_paths = emitted_paths


@dataclass
class _Traversal:
    limit: int | None
    examined_edges: int = 0
    emitted_paths: int = 0

    def __post_init__(self):
        if self.limit is not None and (type(self.limit) is not int or self.limit < 1):
            raise ValueError('max_expansions 必须为正整数，或显式 None 关闭预算')

    def examine(self):
        if self.limit is not None and self.examined_edges >= self.limit:
            raise TraversalBudgetExceeded(self.limit, self.examined_edges, self.emitted_paths)
        self.examined_edges += 1


def _walk(node, graph, max_hops, endpoint_only, upstream, stats, min_strength=0):
    """单一深度优先枚举器；只保留当前分支，不一次压入整个邻接层。"""
    neighbors = graph.predecessors if upstream else graph.successors
    stack = [(iter(neighbors(node)), (node,), 1.0)]
    while stack:
        iterator, chain, strength = stack[-1]
        neighbor = next(iterator, None)
        if neighbor is None:
            stack.pop()
            continue
        stats.examine()
        if neighbor in chain:
            continue
        edge = graph[neighbor][chain[-1]] if upstream else graph[chain[-1]][neighbor]
        value = strength * edge['weight']
        if value < min_strength:
            continue
        path = (*chain, neighbor)
        stats.emitted_paths += 1
        yield path, value
        if len(path) - 1 < max_hops and neighbor not in endpoint_only:
            stack.append((iter(neighbors(neighbor)), path, value))


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


def exposure_report(node, graph, max_hops=2, *, direction='downstream',
                    endpoint_only=frozenset(), max_expansions=DEFAULT_MAX_EXPANSIONS):
    """返回最强暴露与实际遍历计数；空结果仅表示图中没有可达覆盖。"""
    start = perf_counter()
    _validate(graph, max_hops, endpoint_only)
    if direction not in ('upstream', 'downstream'):
        raise ValueError('direction 必须为 upstream 或 downstream')
    stats = _Traversal(max_expansions)
    result = {}
    if node in graph:
        for chain, value in _walk(node, graph, max_hops, endpoint_only, direction == 'upstream', stats):
            result[chain[-1]] = max(value, result.get(chain[-1], 0.0))
    return {'exposures': result, 'examined_edges': stats.examined_edges,
            'emitted_paths': stats.emitted_paths, 'elapsed_seconds': perf_counter() - start}


def upstream_of(node, graph, max_hops=2, *, endpoint_only=frozenset(), max_expansions=DEFAULT_MAX_EXPANSIONS):
    """上游最强简单路径暴露；空结果仅表示图中无覆盖或无可达节点。"""
    return exposure_report(node, graph, max_hops, direction='upstream',
                           endpoint_only=endpoint_only, max_expansions=max_expansions)['exposures']


def downstream_of(node, graph, max_hops=2, *, endpoint_only=frozenset(), max_expansions=DEFAULT_MAX_EXPANSIONS):
    """下游最强简单路径暴露；聚合代理可作为端点，但不继续穿透。"""
    return exposure_report(node, graph, max_hops, endpoint_only=endpoint_only,
                           max_expansions=max_expansions)['exposures']


def transmission_paths(node, graph, max_hops=3, top_k=10, min_strength=1e-3,
                       *, endpoint_only=frozenset(), max_expansions=DEFAULT_MAX_EXPANSIONS):
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
    stats = _Traversal(max_expansions)
    if node not in graph:
        return []

    paths = []
    for serial, (chain, value) in enumerate(_walk(node, graph, max_hops, endpoint_only, True, stats, min_strength)):
        nodes = list(reversed(chain))
        edges = [graph[a][b] for a, b in zip(nodes, nodes[1:])]
        dependencies = [edge["dep"] for edge in edges]
        record = {
            "mechanism": "cost_push",
            "coefficient_type": "direct_input_coefficient_a",
            "table_year": graph.graph.get("table_year"),
            "source": nodes[0], "target": node,
            "path": " -> ".join(map(str, nodes)), "nodes": nodes,
            "hops": len(nodes) - 1, "strength": value,
            "dep_chain": dependencies,
            "bottleneck_dep": min(dependencies, key=DEPENDENCY_ORDER.__getitem__),
            "edge_provenance": [edge.get("provenance") for edge in edges],
            "contains_estimated_edge": any(edge["estimated"] for edge in edges),
        }
        heapq.heappush(paths, (value, -serial, record))
        if len(paths) > top_k:
            heapq.heappop(paths)
    return [item[2] for item in sorted(paths, key=lambda item: (item[0], item[1]), reverse=True)]
