"""用稳定节点身份投影拓扑、风险路径、比较表与正文。"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from numbers import Real
import re


MAX_LAYERS = 5
MAX_NODES_PER_LAYER = 3
DIRECTIONS = {"cost_push": (1, "正向传导"), "demand_contraction": (-1, "逆向传导"),
              "price_pressure_reverse": (-1, "逆向传导"), "credit_contagion": (-1, "逆向信用传导")}
EVENT_KINDS = {"observed": "已观察事件", "conditional_scenario": "条件情景"}
# 依赖分按官方评分案例的两项事实量化：份额与替代难度。档位以案例为参照设置——
# “原材料采购占比80%、替代供应商少”=主导80与锁定90均值85；“电池供应商有多家”=可观50与可行40均值45。
SHARE_BANDS = {"dominant": ("主导", 80), "substantial": ("可观", 50), "moderate": ("一般", 30), "minor": ("较小", 10)}
DOMINANT_SHARE_BOUNDARY, SUBSTANTIAL_SHARE_MIN, MODERATE_SHARE_MIN = 50, 25, 10
SUBSTITUTION_BANDS = {"locked": ("锁定", 90), "costly": ("较难", 60), "feasible": ("可行", 40), "easy": ("容易", 20)}
STRONG_THRESHOLD, MEDIUM_THRESHOLD = 70, 50
DEPENDENCY_LEVELS = {"强": "强依赖", "中": "中依赖", "弱": "弱依赖"}
DEPENDENCY_BASIS_KINDS = {"disclosed": "已披露份额", "structural": "结构推断"}
STRUCTURAL_MARK = "*"
DEPENDENCY_RULE = ("依赖强度指承接节点对传出节点的依赖，按官方评分案例的两项事实量化：一是份额，成本传导看该投入在承接节点"
                   "成本中的占比，需求、价格或信用逆向传导看承接节点销售或敞口对传出节点的去向占比；信用敞口份额以同类融资组合为分母，"
                   "不以净资产代替，追偿是损失缓冲而非既有责任的替代；二是替代难度，即本批交付"
                   "窗口内换源、换客户或改配的可行性。份额分档：主导（过半）80分、可观（约四分之一至一半）50分、一般（约一成至"
                   "四分之一）30分、较小（不足一成）10分；替代难度分档：锁定（窗口内不可替代）90分、较难（需重新认证或设计）"
                   "60分、可行（有可比来源但有代价）40分、容易（多个可比来源）20分。依赖分＝两项均值，70分及以上为强依赖、"
                   "50至69分为中依赖、50分以下为弱依赖。档位参照官方案例设置：“原材料采购占比80%、替代供应商少”得85分强依赖，"
                   "“电池供应商有多家”得45分弱依赖。每一跳都必须给出等级与分数，并标注份额档位的依据：〔已披露份额〕表示有"
                   "分子、分母及业务范围与该跳一致的定期报告比例直接支撑；总材料占比、跨产品地区占比或应收占收入比不能替代该跳份额。〔结构推断〕（图上标" + STRUCTURAL_MARK + "）表示无逐项份额披露，按该环节的"
                   "认证、工艺、适配与可替代条件判断，待核信息在逐跳依据中列明。依赖分用于比较传导强弱，不是损失比例或违约概率。")
NODE_SCORE_KEYS = ("impact", "substitutability", "consequence")
NODE_RULE = ("节点风险值＝影响范围×不可替代性×传导后果，三项各按1至5分评定：影响范围看扰动沿链波及的环节与业务规模，"
             "不可替代性看本批窗口内换源、换客户或改配的难度，传导后果看扰动经过或落在本节点时的放大、停摆或损失程度。"
             "▲红色为风险路径上风险值最高的节点，▼绿色为最低的节点，评分须唯一决出高低；风险值用于同一条链内的相对比较，"
             "不跨链比较，也不是违约概率。")
STEP_KEYS = frozenset({"effect", "share_band", "substitution_band", "basis_kind", "tag", "basis", "disclosed_share"})
DISCLOSED_SHARE_KEYS = frozenset({"percentage", "numerator", "denominator", "source_id", "pages"})
MAX_TAG_CHARS = 8
RANKING_KEYS = ("impact", "substitutability", "consequence", "counter_evidence", "reversal_condition")
BASIS_LABELS = (("影响范围", "impact"), ("不可替代性", "substitutability"),
                ("传导后果", "consequence"), ("对冲与反证", "counter_evidence"), ("适用边界", "boundary"))
# 依据正文只陈述事实、档位理由与待核信息；强/中/弱等级只能由分数派生，不接受手写第二真源。
HANDWRITTEN_LEVEL = re.compile(r"[强中弱]依赖")
DERIVED_FIELDS = frozenset({"edges", "node_risks", "comparison_rows", "transmission_path", "transmission_label",
                            "mechanism_label", "path_steps", "event_kind_label", "path_text", "relation_text",
                            "node_text", "basis_text", "company_names", "impact_text", "core_name", "core_text",
                            "risk_values", "company_positions"})


def required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}须为非空文字")
    return value


def node_name(node: dict) -> str:
    return required_text(node.get("label"), "节点名称").replace("\n", "")


def share_band_for_percent(value: float) -> str:
    """已披露敞口占比直接确定份额档位，避免数值变化后手写档位未同步。"""
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or not 0 <= value <= 100:
        raise ValueError("已披露份额须为0至100的有限百分比")
    if value > DOMINANT_SHARE_BOUNDARY:
        return "dominant"
    if value >= SUBSTANTIAL_SHARE_MIN:
        return "substantial"
    if value >= MODERATE_SHARE_MIN:
        return "moderate"
    return "minor"


def dependency_score(share_band: str, substitution_band: str) -> int:
    """两项档位分值的均值；档位分值均为10的整数倍，均值恰为整数，不引入取整口径。"""
    return (SHARE_BANDS[share_band][1] + SUBSTITUTION_BANDS[substitution_band][1]) // 2


def dependency_level(score: int) -> str:
    return "强" if score >= STRONG_THRESHOLD else "中" if score >= MEDIUM_THRESHOLD else "弱"


def _scores(values: object, node_id: str) -> dict:
    if not isinstance(values, dict) or set(values) != set(NODE_SCORE_KEYS):
        raise ValueError(f"节点{node_id}须恰好给出影响范围、不可替代性、传导后果三项评分")
    for key in NODE_SCORE_KEYS:
        if type(values[key]) is not int or not 1 <= values[key] <= 5:
            raise ValueError(f"节点{node_id}的{key}评分须为1至5的整数")
    return values


def _nodes(chain: dict) -> dict:
    layers = chain.get("layers")
    if not isinstance(layers, list) or not 2 <= len(layers) <= MAX_LAYERS:
        raise ValueError(f"层数须在2至{MAX_LAYERS}之间")
    nodes = {}
    for i, layer in enumerate(layers):
        if not isinstance(layer, list) or not 1 <= len(layer) <= MAX_NODES_PER_LAYER:
            raise ValueError(f"第{i + 1}层节点数须在1至{MAX_NODES_PER_LAYER}之间")
        for j, node in enumerate(layer):
            if not isinstance(node, dict) or set(node) != {"id", "label", "scores", "comparison"}:
                raise ValueError("规范节点必须恰好包含id、label、scores和comparison；风险标记只能派生")
            node_id = node["id"]
            if not isinstance(node_id, str) or re.fullmatch(r"[a-z][a-z0-9_]*", node_id) is None:
                raise ValueError("节点id须为稳定的小写英文字母、数字与下划线标识")
            if node_id in nodes:
                raise ValueError(f"节点id重复：{node_id}")
            node_name(node)
            scores = _scores(node["scores"], node_id)
            values = node["comparison"]
            if not isinstance(values, dict) or set(values) != set(RANKING_KEYS):
                raise ValueError(f"节点{node_id}比较必须恰好包含三因子、反证和翻转条件")
            for key in RANKING_KEYS:
                required_text(values[key], f"节点{node_id}比较{key}")
            risk = scores["impact"] * scores["substitutability"] * scores["consequence"]
            nodes[node_id] = {"position": [i, j], "node": node, "risk_value": risk}
    return nodes


def _links(chain: dict, nodes: dict) -> tuple[list, set]:
    links = chain.get("links")
    if not isinstance(links, list) or not links:
        raise ValueError("规范links必须包含稳定节点id对")
    groups, seen = [[] for _ in chain["layers"][1:]], set()
    for link in links:
        if (not isinstance(link, list) or len(link) != 2
                or any(not isinstance(node_id, str) or node_id not in nodes for node_id in link)):
            raise ValueError(f"links含非法或未登记节点id：{link!r}")
        source, target = link
        if tuple(link) in seen:
            raise ValueError(f"业务边重复：{source}→{target}")
        a, b = nodes[source]["position"], nodes[target]["position"]
        if b[0] != a[0] + 1:
            raise ValueError(f"业务边必须顺序连接相邻层，不允许节点跨层漂移：{source}→{target}")
        groups[a[0]].append([a[1], b[1]])
        seen.add((source, target))
    incoming, outgoing = {b for _, b in seen}, {a for a, _ in seen}
    for node_id, entry in nodes.items():
        layer = entry["position"][0]
        if (layer > 0 and node_id not in incoming) or (layer < len(groups) and node_id not in outgoing):
            raise ValueError(f"节点{node_id}没有完整上下游连接")
    return groups, seen


def _selections(chain: dict, nodes: dict) -> dict:
    """▲▼只在风险路径节点中按风险值唯一决出；声明的最高/最低必须与评分一致，理由文字随之绑定。"""
    selected = {key: chain.get(key) for key in ("highest_node", "lowest_node")}
    if any(not isinstance(value, str) or value not in nodes for value in selected.values()):
        raise ValueError("最高/最低风险必须引用已登记的稳定节点id")
    if selected["highest_node"] == selected["lowest_node"]:
        raise ValueError("最高与最低风险节点必须不同")
    path = chain.get("risk_path")
    if not isinstance(path, list) or any(node_id not in nodes for node_id in path if isinstance(node_id, str)):
        raise ValueError("规范risk_path至少包含两个已登记的稳定节点id，不接受位置索引")
    values = {node_id: nodes[node_id]["risk_value"] for node_id in path if isinstance(node_id, str)}
    ordered = sorted(values.values())
    if len(ordered) < 2 or ordered[-1] == ordered[-2] or ordered[0] == ordered[1]:
        raise ValueError("风险路径上的最高或最低风险值并列，须调整评分以唯一决出▲▼")
    highest = max(values, key=values.get)
    lowest = min(values, key=values.get)
    if (selected["highest_node"], selected["lowest_node"]) != (highest, lowest):
        raise ValueError(f"声明的最高/最低节点与三因子评分不一致：评分最高{highest}、最低{lowest}")
    return {node_id: "high" if node_id == highest else "low" if node_id == lowest else "plain" for node_id in nodes}


def _share_band(step: dict, number: int, references: list) -> str:
    """只有带数值及来源范围的直接披露能生成披露档位；代理指标须显式选结构推断。"""
    share, disclosed = step["share_band"], step["disclosed_share"]
    if step["basis_kind"] == "structural":
        if disclosed is not None:
            raise ValueError(f"第{number}跳结构推断不得携带直接披露份额")
        if not isinstance(share, str) or share not in SHARE_BANDS:
            raise ValueError(f"第{number}跳份额档位只能为{'、'.join(SHARE_BANDS)}")
        return share
    if share is not None:
        raise ValueError(f"第{number}跳披露份额必须由数值派生档位，不得另写share_band")
    if not isinstance(disclosed, dict) or set(disclosed) != DISCLOSED_SHARE_KEYS:
        raise ValueError(f"第{number}跳披露份额须绑定比例、分子、分母、来源与页码")
    for field in ("numerator", "denominator", "source_id"):
        required_text(disclosed[field], f"第{number}跳披露份额{field}")
    pages = disclosed["pages"]
    if (not isinstance(pages, list) or not pages or any(type(page) is not int or page < 1 for page in pages)
            or len(pages) != len(set(pages))):
        raise ValueError(f"第{number}跳披露份额页码须为不重复的正整数列表")
    matches = [ref for ref in references if isinstance(ref, dict) and ref.get("source_id") == disclosed["source_id"]]
    if len(matches) != 1 or any(page not in matches[0].get("pages", []) for page in pages):
        raise ValueError(f"第{number}跳披露份额来源或页码未绑定本链引用")
    return share_band_for_percent(disclosed["percentage"])


def _step(step: object, number: int, references: list) -> dict:
    if not isinstance(step, dict) or set(step) != STEP_KEYS:
        raise ValueError(f"第{number}跳须恰好包含机制、份额档位、替代难度档位、判级依据、图注和依据")
    substitution = step["substitution_band"]
    if not isinstance(substitution, str) or substitution not in SUBSTITUTION_BANDS:
        raise ValueError(f"第{number}跳替代难度档位只能为{'、'.join(SUBSTITUTION_BANDS)}")
    basis_kind = step["basis_kind"]
    if not isinstance(basis_kind, str) or basis_kind not in DEPENDENCY_BASIS_KINDS:
        raise ValueError(f"第{number}跳判级依据只能为disclosed或structural")
    share = _share_band(step, number, references)
    basis = required_text(step["basis"], f"第{number}跳依据")
    if "待核：" not in basis:
        raise ValueError(f"第{number}跳依据须列明“待核：”，说明档位的不确定来源")
    if "事实：" not in basis:
        raise ValueError(f"第{number}跳依据须以“事实：”给出披露或可核验事实")
    if HANDWRITTEN_LEVEL.search(basis):
        raise ValueError(f"第{number}跳依据不得手写强/中/弱依赖等级；等级只由档位分数派生")
    tag = required_text(step["tag"], f"第{number}跳图注")
    if len(tag) > MAX_TAG_CHARS or any(char in tag for char in "\r\n\t"):
        raise ValueError(f"第{number}跳图注须为不超过{MAX_TAG_CHARS}字的单行短语")
    score = dependency_score(share, substitution)
    level = dependency_level(score)
    mark = STRUCTURAL_MARK if basis_kind == "structural" else ""
    share_label, share_points = SHARE_BANDS[share]
    substitution_label, substitution_points = SUBSTITUTION_BANDS[substitution]
    return {"effect": required_text(step["effect"], f"第{number}跳机制"), "share_band": share,
            "substitution_band": substitution, "score": score, "level": level,
            "level_label": DEPENDENCY_LEVELS[level], "basis_kind": basis_kind,
            "basis_kind_label": DEPENDENCY_BASIS_KINDS[basis_kind],
            "level_mark": f"{DEPENDENCY_LEVELS[level]}{score}{mark}",
            "level_detail": f"{DEPENDENCY_LEVELS[level]}{score}分（{DEPENDENCY_BASIS_KINDS[basis_kind]}）",
            "score_text": (f"依赖分：份额{share_label}{share_points}与替代难度{substitution_label}{substitution_points}"
                           f"的均值＝{score}分，{DEPENDENCY_LEVELS[level]}"),
            "tag": tag, "basis": basis}


def _path(chain: dict, nodes: dict, links: set) -> dict:
    required_text(chain.get("risk_event"), "risk_event")
    path = chain.get("risk_path")
    if (not isinstance(path, list) or len(path) < 2
            or any(not isinstance(node_id, str) or node_id not in nodes for node_id in path)):
        raise ValueError("规范risk_path至少包含两个已登记的稳定节点id，不接受位置索引")
    if len(path) != len(set(path)):
        raise ValueError("风险路径不得重复经过同一节点")
    mechanism, kind = chain.get("mechanism"), chain.get("event_kind")
    if not isinstance(mechanism, str) or mechanism not in DIRECTIONS:
        raise ValueError("未知风险传导机制")
    if not isinstance(kind, str) or kind not in EVENT_KINDS:
        raise ValueError("event_kind必须明确为observed或conditional_scenario")
    direction = DIRECTIONS[mechanism][0]
    hops = chain.get("steps")
    if not isinstance(hops, list) or len(hops) != len(path) - 1:
        raise ValueError("steps必须与规范风险路径的每一跳一一对应")
    steps = []
    for index, (source, target) in enumerate(zip(path, path[1:])):
        a, b = nodes[source]["position"], nodes[target]["position"]
        if b[0] - a[0] != direction:
            raise ValueError("风险传导方向或逐层顺序与机制不一致")
        edge = (source, target) if direction == 1 else (target, source)
        if edge not in links:
            raise ValueError(f"风险传导跳过或不存在物料/业务连接：{source}→{target}")
        steps.append({"from_id": source, "to_id": target, "from": a, "to": b,
                      "from_name": node_name(nodes[source]["node"]), "to_name": node_name(nodes[target]["node"]),
                      **_step(hops[index], index + 1, chain.get("source_refs", []))})
    sequence = " → ".join(node_name(nodes[node_id]["node"]) for node_id in path)
    dependency = "；".join(f"{step['from_name']}→{step['to_name']} {step['level_detail']}" for step in steps)
    required_text(chain.get("event_scope"), "event_scope")
    required_text(chain.get("path_boundary"), "path_boundary")
    return {"transmission_path": [nodes[node_id]["position"] for node_id in path],
            "transmission_label": sequence, "mechanism_label": DIRECTIONS[mechanism][1],
            "path_steps": steps, "event_kind_label": EVENT_KINDS[kind],
            "path_text": f"风险传导路径（{DIRECTIONS[mechanism][1]}）：{sequence}\n逐跳依赖强度：{dependency}"}


def _company_nodes(chain: dict, nodes: dict) -> list:
    """目标企业所在环节须已登记，且至少一个位于风险路径上，才能说明风险如何到达本企业。"""
    selected = chain.get("company_nodes")
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(node_id, str) or node_id not in nodes for node_id in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("company_nodes须为不重复、已登记的稳定节点id列表")
    if not set(selected) & set(chain["risk_path"]):
        raise ValueError("目标企业所在环节至少一个须位于风险传导路径上")
    return [node_name(nodes[node_id]["node"]) for node_id in selected]


def _position(company_layer: int, core_layer: int) -> str:
    if company_layer < core_layer:
        return "位于核心环节上游"
    if company_layer > core_layer:
        return "位于核心环节下游"
    return "与核心环节同层"


def _core(chain: dict, nodes: dict) -> dict:
    """核心环节是组织本链订单、定价或信用的节点；本企业相对它的上下游位置由层序派生。"""
    core = chain.get("core_node")
    if not isinstance(core, str) or core not in nodes:
        raise ValueError("core_node须引用已登记的稳定节点id")
    reason = required_text(chain.get("core_reason"), "core_reason")
    core_layer = nodes[core]["position"][0]
    positions = []
    for node_id in chain["company_nodes"]:
        name = node_name(nodes[node_id]["node"])
        where = "即核心环节" if node_id == core else _position(nodes[node_id]["position"][0], core_layer)
        positions.append(f"{name}（{where}）")
    core_name = node_name(nodes[core]["node"])
    return {"core_name": core_name, "company_positions": positions,
            "core_text": f"核心环节：{core_name}（{reason}）\n本企业定位：{'；'.join(positions)}"}


def _texts(chain: dict, nodes: dict, company_names: list, core_text: str) -> dict:
    if all(len(layer) == 1 for layer in chain["layers"]):
        relation = " → ".join(node_name(layer[0]) for layer in chain["layers"])
    else:
        relation = "；".join(f"{node_name(nodes[a]['node'])} → {node_name(nodes[b]['node'])}"
                            for a, b in chain["links"])
    high = required_text(chain.get("high_reason"), "high_reason")
    low = required_text(chain.get("low_reason"), "low_reason")
    high_id, low_id = chain["highest_node"], chain["lowest_node"]
    high_name, low_name = (node_name(nodes[node_id]["node"]) for node_id in (high_id, low_id))
    basis = " ".join(f"{label}：{required_text(chain.get(key), key)}" for label, key in BASIS_LABELS)
    impact = required_text(chain.get("target_impact"), "target_impact")
    return {"relation_text": (f"产业链关系识别：{relation}\n目标企业所在环节：{'、'.join(company_names)}\n"
                              f"{core_text}"),
            "node_text": (f"风险节点判断：▲最高＝{high_name}（风险值{nodes[high_id]['risk_value']}：{high}）；"
                          f"▼最低＝{low_name}（风险值{nodes[low_id]['risk_value']}：{low}）。"),
            "impact_text": f"对目标企业的传导影响：{impact}",
            "basis_text": "判断依据：" + basis}


def projected_fields(chain: dict) -> dict:
    """唯一解析入口；仅读取规范字段，重算所有可见结构和比较内容。"""
    if any(key in chain for key in ("ranking", "nodes", "risk", "step_effects")):
        raise ValueError("不接受独立ranking、nodes、risk或旧step_effects第二真源")
    nodes = _nodes(chain)
    edges, links = _links(chain, nodes)
    path = _path(chain, nodes, links)
    risks = _selections(chain, nodes)
    company_names = _company_nodes(chain, nodes)
    core = _core(chain, nodes)
    on_path = set(chain["risk_path"])
    comparisons = [{"id": node_id, "node": entry["position"], "name": node_name(entry["node"]),
                    "risk": risks[node_id], "company": node_id in chain["company_nodes"],
                    "core": node_id == chain["core_node"], "on_path": node_id in on_path,
                    "risk_value": entry["risk_value"], "scores": dict(entry["node"]["scores"]),
                    **entry["node"]["comparison"]} for node_id, entry in nodes.items()]
    return {"edges": edges, "node_risks": risks, "comparison_rows": comparisons, "company_names": company_names,
            "risk_values": {node_id: entry["risk_value"] for node_id, entry in nodes.items()},
            **core, **path, **_texts(chain, nodes, company_names, core["core_text"])}


def project_chain(chain: dict) -> dict:
    """规范输入不允许携带缓存派生字段；返回独立对象且不修改调用方。"""
    forbidden = DERIVED_FIELDS.intersection(chain)
    if forbidden:
        raise ValueError(f"不接受手写派生字段：{sorted(forbidden)}；请修改规范节点、路径或条件")
    data = deepcopy(chain)
    data.update(projected_fields(data))
    return data
