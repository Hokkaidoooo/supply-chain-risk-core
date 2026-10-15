"""核对输入来源的内容指纹、公开日期与适用区间。"""
from copy import deepcopy
import hashlib
import re
from urllib.parse import urlsplit

from .validation import day, exact_keys, nonempty_list, require_object, text

SOURCE_FIELDS = {"title", "url", "published_at", "valid_from", "valid_to", "content", "sha256", "kind"}
SOURCE_KINDS = {"synthetic_demonstration", "public_evidence"}


def validate_sources(sources, as_of, data_kind):
    require_object(sources, "sources")
    if not sources:
        raise ValueError("sources 不得为空")
    if data_kind not in SOURCE_KINDS:
        raise ValueError(f"data_kind 只允许 {sorted(SOURCE_KINDS)}")
    cutoff = day(as_of, "as_of")
    for source_id, source in sources.items():
        text(source_id, "source_id")
        exact_keys(source, SOURCE_FIELDS, source_id)
        text(source["title"], f"{source_id}.title")
        url = urlsplit(text(source["url"], f"{source_id}.url"))
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            raise ValueError(f"{source_id} 须提供无凭据的 HTTP(S) 来源链接")
        if source["kind"] != data_kind:
            raise ValueError(f"{source_id} 的数据性质与任务不一致")
        published = day(source["published_at"], f"{source_id}.published_at")
        start = day(source["valid_from"], f"{source_id}.valid_from")
        end = None if source["valid_to"] is None else day(source["valid_to"], f"{source_id}.valid_to")
        if published > cutoff or start > cutoff or (end is not None and cutoff > end):
            raise ValueError(f"{source_id} 在 {as_of} 尚未公开或不在适用区间")
        if end is not None and end < start:
            raise ValueError(f"{source_id} 适用区间倒置")
        text(source["content"], f"{source_id}.content")
        digest = source["sha256"]
        if not isinstance(digest, str) or re.fullmatch("[a-f0-9]{64}", digest) is None:
            raise ValueError(f"{source_id} 缺少有效 SHA-256")
        if hashlib.sha256(source["content"].encode("utf-8")).hexdigest() != digest:
            raise ValueError(f"{source_id} 的内容指纹不一致")
    return deepcopy(sources)


def validate_references(references, sources, label):
    nonempty_list(references, label)
    if any(not isinstance(ref, str) or ref not in sources for ref in references):
        raise ValueError(f"{label} 引用了未登记来源")
    if len(set(references)) != len(references):
        raise ValueError(f"{label} 不接受重复来源")
    return list(references)


def source_summary(sources):
    return [{"id": key, **{field: source[field] for field in ("title", "url", "published_at", "sha256", "kind")}}
            for key, source in sorted(sources.items())]
