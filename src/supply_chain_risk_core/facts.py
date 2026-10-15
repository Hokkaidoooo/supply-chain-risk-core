"""最小企业事实关系：只消费有出处、方向明确且查询时有效的直接边。"""
from datetime import date
import re
from urllib.parse import urlparse

_FIELDS = frozenset({'source', 'target', 'relation', 'direction', 'valid_from', 'valid_to',
                     'published_at', 'source_url', 'evidence_id', 'estimated'})


def _date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{field} 必须是 YYYY-MM-DD')
    return date.fromisoformat(value)


def _validate_edge(edge, query_date):
    if not isinstance(edge, dict) or set(edge) != _FIELDS:
        raise ValueError('企业事实关系字段不完整或含未知字段')
    for field in ('source', 'target', 'evidence_id'):
        if not isinstance(edge[field], str) or not edge[field].strip():
            raise ValueError(f'{field} 必须提供非空主体或证据标识')
    if edge['source'] == edge['target']:
        raise ValueError('企业事实关系不能连接同一主体')
    if edge['relation'] not in ('guarantee', 'receivable') or edge['direction'] != 'risk_source_to_affected':
        raise ValueError('关系类型或风险传导方向不明确')
    if type(edge['estimated']) is not bool:
        raise ValueError('estimated 必须显式标记为布尔值')
    source_url = edge['source_url']
    if not isinstance(source_url, str):
        raise ValueError('企业事实关系缺少 HTTPS 来源')
    url = urlparse(source_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ValueError('企业事实关系缺少公开 HTTPS 来源')
    start, end = _date(edge['valid_from'], 'valid_from'), _date(edge['valid_to'], 'valid_to')
    published = _date(edge['published_at'], 'published_at')
    if start > end:
        raise ValueError('关系有效期起止顺序错误')
    if not start <= query_date <= end or published > query_date:
        raise ValueError('关系在查询时点未生效、已过期或尚未公开')
    return dict(edge)


def credit_transmission_paths(node, factual_edges=None, *, as_of=None):
    """返回指向 node 的直接事实边。空列表表示无提供的事实覆盖，不表示无风险。

    guarantee: source 为被担保债务人，target 为承担代偿风险的担保人。
    receivable: source 为付款义务人，target 为收款债权人。暂不推断多跳关系。
    """
    if factual_edges is None:
        return []
    if not isinstance(factual_edges, (list, tuple)):
        raise TypeError('factual_edges 必须为显式事实边列表')
    if not factual_edges:
        return []
    query_date = _date(as_of, 'as_of')
    if not isinstance(node, str) or not node.strip():
        raise ValueError('node 必须为非空主体标识')
    results, seen = [], set()
    for edge in factual_edges:
        record = _validate_edge(edge, query_date)
        key = (record['source'], record['target'], record['relation'], record['evidence_id'])
        if key in seen:
            raise ValueError('企业事实关系重复')
        seen.add(key)
        if record['target'] == node:
            results.append({'mechanism': 'factual_direct_relation', 'nodes': [record['source'], node],
                            'hops': 1, 'as_of': as_of, 'evidence': record})
    return results
