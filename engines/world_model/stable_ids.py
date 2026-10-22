"""Stable, deterministic, content-addressable IDs (spec: docs/stable-ids.md).

Same input -> same ID on every machine and every re-run. A Python port of the Meterless
reference `stableIds.ts`; golden values from the TypeScript reference are checked in the tests.
"""

import hashlib
import json
import re
import unicodedata


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_scalar(value):
    # JSON.stringify prints 1.0 as 1; do the same so ids agree across implementations.
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_json(value):
    """Deterministic JSON: keys sorted, None members dropped (JS `undefined`)."""
    if value is None or not isinstance(value, (dict, list, tuple)):
        return _canonical_scalar(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    keys = sorted(k for k, v in value.items() if v is not None)
    return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(value[k]) for k in keys) + "}"


_COMBINING = re.compile("[̀-ͯ]")
_ZERO_WIDTH = re.compile("[​-‍﻿]")


def normalize_name(name):
    """NFKD -> strip combining marks -> strip zero-width -> lowercase -> collapse whitespace -> trim."""
    text = unicodedata.normalize("NFKD", name)
    text = _COMBINING.sub("", text)
    text = _ZERO_WIDTH.sub("", text)
    return re.sub(r"\s+", " ", text.lower()).strip()


def external_id(type_, system, id_):
    """Externally keyed entity or context: the literal grammar type:system:externalId."""
    return f"{type_}:{system}:{id_}"


def entity_id_from_name(type_, name):
    """Name-keyed entity: ent_<first 16 hex of sha256(type:normalizedName)>."""
    return "ent_" + sha256_hex(f"{type_}:{normalize_name(name)}")[:16]


def context_id_from_attrs(type_, attrs, parent=None):
    return "ctx_" + sha256_hex(f"{type_}:{canonical_json(attrs)}:{parent or ''}")[:16]


def relationship_id(from_, type_, to, context=None, valid_from=None):
    return "rel_" + sha256_hex(f"{from_}|{type_}|{to}|{context or ''}|{valid_from or ''}")[:16]


def fact_id(about, predicate, value, asserted_at, source_kind, source_url=None):
    return "fact_" + sha256_hex(
        f"{about}|{predicate}|{canonical_json(value)}|{asserted_at}|{source_kind}|{source_url or ''}"
    )[:16]
