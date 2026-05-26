"""
Unit tests for the dict-filter builders in `mcp_docs_tidb.common.filters`.
These tests do not require a running TiDB instance.
"""

from __future__ import annotations

import pytest

from mcp_docs_tidb.common.filters import (
    build_filter_from_arbitrary,
    build_filter_from_fields,
)
from mcp_docs_tidb.settings import FilterableField


def _field(
    name: str,
    field_type: str,
    condition: str | None = "==",
    required: bool = False,
) -> FilterableField:
    return FilterableField(
        name=name,
        description="test",
        field_type=field_type,  # type: ignore[arg-type]
        condition=condition,  # type: ignore[arg-type]
        required=required,
    )


# ---------------------------------------------------------------------------
# build_filter_from_fields
# ---------------------------------------------------------------------------


def test_keyword_equals_emits_eq_clause() -> None:
    fields = {"category": _field("category", "keyword", "==")}
    f = build_filter_from_fields(fields, {"category": "work"})
    assert f == {"metadata.category": {"$eq": "work"}}


def test_integer_range_uses_gte() -> None:
    fields = {"year": _field("year", "integer", ">=")}
    f = build_filter_from_fields(fields, {"year": 2024})
    assert f == {"metadata.year": {"$gte": 2024}}


def test_float_less_than_uses_lt() -> None:
    fields = {"score": _field("score", "float", "<")}
    f = build_filter_from_fields(fields, {"score": 0.5})
    assert f == {"metadata.score": {"$lt": 0.5}}


def test_boolean_equals_coerces_to_integer() -> None:
    fields = {"archived": _field("archived", "boolean", "==")}
    f = build_filter_from_fields(fields, {"archived": True})
    assert f == {"metadata.archived": {"$eq": 1}}


def test_any_emits_in_clause() -> None:
    fields = {"tags": _field("tags", "keyword", "any")}
    f = build_filter_from_fields(fields, {"tags": ["a", "b", "c"]})
    assert f == {"metadata.tags": {"$in": ["a", "b", "c"]}}


def test_except_emits_nin_clause() -> None:
    fields = {"tags": _field("tags", "keyword", "except")}
    f = build_filter_from_fields(fields, {"tags": ["x", "y"]})
    assert f == {"metadata.tags": {"$nin": ["x", "y"]}}


def test_multiple_conditions_joined_with_and() -> None:
    fields = {
        "category": _field("category", "keyword", "=="),
        "year": _field("year", "integer", ">="),
    }
    f = build_filter_from_fields(fields, {"category": "work", "year": 2024})
    assert f == {
        "$and": [
            {"metadata.category": {"$eq": "work"}},
            {"metadata.year": {"$gte": 2024}},
        ]
    }


def test_none_value_for_optional_field_is_skipped() -> None:
    fields = {"category": _field("category", "keyword", "==")}
    f = build_filter_from_fields(fields, {"category": None})
    assert f is None


def test_none_value_for_required_field_raises() -> None:
    fields = {"category": _field("category", "keyword", "==", required=True)}
    with pytest.raises(ValueError, match="required"):
        build_filter_from_fields(fields, {"category": None})


def test_unknown_field_raises() -> None:
    with pytest.raises(ValueError, match="not a filterable field"):
        build_filter_from_fields({}, {"category": "x"})


def test_empty_values_returns_none() -> None:
    fields = {"category": _field("category", "keyword", "==")}
    f = build_filter_from_fields(fields, {})
    assert f is None


def test_invalid_field_name_with_dot_is_rejected() -> None:
    fields = {"bad.name": _field("bad.name", "keyword", "==")}
    with pytest.raises(ValueError, match="filterable field name"):
        build_filter_from_fields(fields, {"bad.name": "x"})


# ---------------------------------------------------------------------------
# build_filter_from_arbitrary
# ---------------------------------------------------------------------------


def test_arbitrary_must_emits_clause() -> None:
    spec = {"must": [{"field": "category", "op": "==", "value": "work"}]}
    f = build_filter_from_arbitrary(spec)
    assert f == {"metadata.category": {"$eq": "work"}}


def test_arbitrary_must_not_negates() -> None:
    spec = {"must_not": [{"field": "lang", "op": "==", "value": "en"}]}
    f = build_filter_from_arbitrary(spec)
    assert f == {"metadata.lang": {"$ne": "en"}}


def test_arbitrary_in_emits_in_clause() -> None:
    spec = {"must": [{"field": "tags", "op": "in", "value": ["a", "b"]}]}
    f = build_filter_from_arbitrary(spec)
    assert f == {"metadata.tags": {"$in": ["a", "b"]}}


def test_arbitrary_must_not_in_becomes_nin() -> None:
    spec = {"must_not": [{"field": "tags", "op": "in", "value": ["a"]}]}
    f = build_filter_from_arbitrary(spec)
    assert f == {"metadata.tags": {"$nin": ["a"]}}


def test_arbitrary_none_returns_none() -> None:
    f = build_filter_from_arbitrary(None)
    assert f is None


def test_arbitrary_unknown_op_raises() -> None:
    spec = {"must": [{"field": "x", "op": "approx", "value": 1}]}
    with pytest.raises(ValueError, match="Unsupported filter operator"):
        build_filter_from_arbitrary(spec)


def test_arbitrary_missing_field_raises() -> None:
    spec = {"must": [{"op": "==", "value": 1}]}
    with pytest.raises(ValueError, match="non-empty 'field'"):
        build_filter_from_arbitrary(spec)


def test_arbitrary_combines_must_and_must_not_with_and() -> None:
    spec = {
        "must": [{"field": "category", "op": "==", "value": "fruit"}],
        "must_not": [{"field": "lang", "op": "==", "value": "en"}],
    }
    f = build_filter_from_arbitrary(spec)
    assert f == {
        "$and": [
            {"metadata.category": {"$eq": "fruit"}},
            {"metadata.lang": {"$ne": "en"}},
        ]
    }


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_boolean_field_rejects_gt_operator() -> None:
    fields = {"active": _field("active", "boolean", ">")}
    with pytest.raises(ValueError, match="boolean field"):
        build_filter_from_fields(fields, {"active": True})


def test_boolean_field_rejects_gte_operator() -> None:
    fields = {"active": _field("active", "boolean", ">=")}
    with pytest.raises(ValueError, match="boolean field"):
        build_filter_from_fields(fields, {"active": True})


def test_float_field_rejects_any_condition() -> None:
    fields = {"score": _field("score", "float", "any")}
    with pytest.raises(ValueError, match="float field"):
        build_filter_from_fields(fields, {"score": [0.1, 0.2]})


def test_float_field_rejects_except_condition() -> None:
    fields = {"score": _field("score", "float", "except")}
    with pytest.raises(ValueError, match="float field"):
        build_filter_from_fields(fields, {"score": [0.1]})


def test_condition_none_field_is_skipped_even_with_value() -> None:
    fields = {"hidden": _field("hidden", "keyword", None)}
    f = build_filter_from_fields(fields, {"hidden": "value"})
    assert f is None


def test_negate_compound_clause_raises() -> None:
    from mcp_docs_tidb.common.filters import _negate_clause

    with pytest.raises(ValueError, match="compound"):
        _negate_clause({"a": 1, "b": 2})


def test_negate_clause_without_operator_raises() -> None:
    from mcp_docs_tidb.common.filters import _negate_clause

    with pytest.raises(ValueError, match="single operator"):
        _negate_clause({"key": "not_a_dict"})


def test_arbitrary_in_requires_nonempty_list() -> None:
    spec = {"must": [{"field": "tags", "op": "in", "value": []}]}
    with pytest.raises(ValueError, match="non-empty list"):
        build_filter_from_arbitrary(spec)


def test_arbitrary_not_in_requires_nonempty_list() -> None:
    spec = {"must": [{"field": "tags", "op": "not_in", "value": []}]}
    with pytest.raises(ValueError, match="non-empty list"):
        build_filter_from_arbitrary(spec)


def test_arbitrary_non_dict_spec_raises() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        build_filter_from_arbitrary("not a dict")  # type: ignore[arg-type]


def test_arbitrary_non_dict_condition_raises() -> None:
    spec = {"must": ["not_an_object"]}
    with pytest.raises(ValueError, match="object"):
        build_filter_from_arbitrary(spec)


def test_arbitrary_empty_dict_returns_none() -> None:
    f = build_filter_from_arbitrary({})
    assert f is None
