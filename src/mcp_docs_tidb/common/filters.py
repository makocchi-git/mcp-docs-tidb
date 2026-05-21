"""
Translators from user-facing filter declarations into the dict-shaped
filter DSL that pytidb understands.

pytidb accepts a MongoDB-like dict: ``{"col": value}`` for equality,
``{"col": {"$gt": x}}`` for operators, and ``{"$and": [...]}`` / ``{"$or": [...]}``
for boolean combinators. Nested JSON columns are addressed with
``"<column>.<json_field>"``.

This module exposes two top-level builders:

* :func:`build_filter_from_fields` — typed-arguments path used when
  ``filterable_fields`` are declared in settings.
* :func:`build_filter_from_arbitrary` — converts the
  ``{"must": [...], "must_not": [...]}`` schema exposed as the generic
  ``query_filter`` MCP tool argument.
"""

from __future__ import annotations

from typing import Any

from mcp_docs_tidb.settings import METADATA_COLUMN, FilterableField

_OP_NEGATE = {
    "$eq": "$ne",
    "$ne": "$eq",
    "$gt": "$lte",
    "$gte": "$lt",
    "$lt": "$gte",
    "$lte": "$gt",
    "$in": "$nin",
    "$nin": "$in",
}


def _metadata_key(field_name: str) -> str:
    """Dotted path to a top-level JSON field stored inside ``metadata``."""
    if not field_name.replace("_", "").isalnum():
        raise ValueError(f"Unsupported filterable field name: {field_name!r}")
    return f"{METADATA_COLUMN}.{field_name}"


def _coerce_value(field: FilterableField, value: Any) -> Any:
    if field.field_type == "boolean":
        return 1 if bool(value) else 0
    return value


_CONDITION_TO_OP = {
    "==": "$eq",
    "!=": "$ne",
    ">": "$gt",
    ">=": "$gte",
    "<": "$lt",
    "<=": "$lte",
}


def build_filter_from_fields(
    filterable_fields: dict[str, FilterableField],
    values: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Translate ``{field_name: user_value}`` plus the field declarations into
    a pytidb-style dict filter. Returns ``None`` if no condition is active.
    """
    clauses: list[dict[str, Any]] = []

    for raw_name, raw_value in values.items():
        if raw_name not in filterable_fields:
            raise ValueError(f"Field {raw_name} is not a filterable field")
        field = filterable_fields[raw_name]
        if raw_value is None:
            if field.required:
                raise ValueError(f"Field {raw_name} is required")
            continue

        key = _metadata_key(field.name)
        cond = field.condition

        if cond in _CONDITION_TO_OP:
            if field.field_type == "boolean" and cond not in {"==", "!="}:
                raise ValueError(
                    f"Only '==' / '!=' are supported for boolean field {raw_name!r}"
                )
            op = _CONDITION_TO_OP[cond]
            clauses.append({key: {op: _coerce_value(field, raw_value)}})
        elif cond == "any":
            if field.field_type == "float":
                raise ValueError(
                    f"Conditions 'any'/'except' are not supported for float field {raw_name!r}"
                )
            if not isinstance(raw_value, list) or not raw_value:
                raise ValueError(f"Field {raw_name} ('any') requires a non-empty list")
            clauses.append(
                {key: {"$in": [_coerce_value(field, v) for v in raw_value]}}
            )
        elif cond == "except":
            if field.field_type == "float":
                raise ValueError(
                    f"Conditions 'any'/'except' are not supported for float field {raw_name!r}"
                )
            if not isinstance(raw_value, list) or not raw_value:
                raise ValueError(
                    f"Field {raw_name} ('except') requires a non-empty list"
                )
            clauses.append(
                {key: {"$nin": [_coerce_value(field, v) for v in raw_value]}}
            )
        elif cond is None:
            continue
        else:
            raise ValueError(
                f"Invalid condition {cond!r} for field {raw_name!r} "
                f"(type={field.field_type})"
            )

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


_ARBITRARY_OP_MAP = {
    "==": "$eq",
    "!=": "$ne",
    ">": "$gt",
    ">=": "$gte",
    "<": "$lt",
    "<=": "$lte",
    "in": "$in",
    "not_in": "$nin",
}


def _emit_arbitrary_clause(cond: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cond, dict):
        raise ValueError(f"Filter condition must be an object, got {type(cond)}")
    field = cond.get("field")
    op = cond.get("op")
    value = cond.get("value")
    if not isinstance(field, str) or not field:
        raise ValueError("Filter condition is missing a non-empty 'field'")
    if op not in _ARBITRARY_OP_MAP:
        raise ValueError(f"Unsupported filter operator: {op!r}")
    if op in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise ValueError(f"Operator {op!r} requires a non-empty list value")

    key = _metadata_key(field)
    return {key: {_ARBITRARY_OP_MAP[op]: value}}


def _negate_clause(clause: dict[str, Any]) -> dict[str, Any]:
    """Flip a single ``{key: {op: value}}`` clause."""
    if len(clause) != 1:
        raise ValueError("Cannot negate compound clauses")
    (key, op_value), = clause.items()
    if not isinstance(op_value, dict) or len(op_value) != 1:
        raise ValueError("Cannot negate a clause without a single operator")
    (op, value), = op_value.items()
    if op not in _OP_NEGATE:
        raise ValueError(f"Cannot negate operator: {op!r}")
    return {key: {_OP_NEGATE[op]: value}}


def build_filter_from_arbitrary(
    spec: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Build a pytidb-style dict filter from an arbitrary
    ``{"must": [...], "must_not": [...]}`` specification.

    Supported per-condition ``op`` values: ``==``, ``!=``, ``>``, ``>=``,
    ``<``, ``<=``, ``in``, ``not_in``.
    """
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise ValueError("query_filter must be a JSON object")

    must = spec.get("must") or []
    must_not = spec.get("must_not") or []

    clauses: list[dict[str, Any]] = []
    for cond in must:
        clauses.append(_emit_arbitrary_clause(cond))
    for cond in must_not:
        clauses.append(_negate_clause(_emit_arbitrary_clause(cond)))

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
