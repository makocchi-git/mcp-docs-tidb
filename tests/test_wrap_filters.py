"""
Unit tests for `wrap_filters` — verifies the dynamic signature rewriting and
that filter values are translated into a pytidb-style dict filter.
"""

from __future__ import annotations

import inspect
from typing import Any

from mcp_docs_tidb.common.wrap_filters import wrap_filters
from mcp_docs_tidb.settings import FilterableField


def _field(
    name: str,
    field_type: str,
    condition: str | None = "==",
    required: bool = False,
) -> FilterableField:
    return FilterableField(
        name=name,
        description=f"{name} field",
        field_type=field_type,  # type: ignore[arg-type]
        condition=condition,  # type: ignore[arg-type]
        required=required,
    )


def test_signature_replaces_query_filter_with_typed_args() -> None:
    def find(
        query: str,
        collection_name: str,
        query_filter: dict[str, Any] | None = None,
    ) -> list[str]:
        return []

    wrapped = wrap_filters(
        find,
        {
            "category": _field("category", "keyword", "=="),
            "year": _field("year", "integer", ">="),
        },
    )

    sig = inspect.signature(wrapped)
    params = list(sig.parameters)
    assert "query_filter" not in params
    assert params[:2] == ["query", "collection_name"]
    assert set(params[2:]) == {"category", "year"}


def test_required_field_comes_before_optional() -> None:
    def find(query: str) -> list[str]:
        return []

    wrapped = wrap_filters(
        find,
        {
            "optional_field": _field("optional_field", "keyword", "==", required=False),
            "required_field": _field("required_field", "keyword", "==", required=True),
        },
    )
    params = list(inspect.signature(wrapped).parameters)
    assert params.index("required_field") < params.index("optional_field")


def test_wrapper_translates_kwargs_to_dict_filter() -> None:
    captured: dict[str, Any] = {}

    def find(
        query: str,
        collection_name: str,
        dict_filter: dict[str, Any] | None = None,
    ) -> str:
        captured["dict_filter"] = dict_filter
        return "ok"

    wrapped = wrap_filters(
        find,
        {"category": _field("category", "keyword", "==")},
    )

    result = wrapped(query="q", collection_name="t", category="work")

    assert result == "ok"
    assert captured["dict_filter"] == {"metadata.category": {"$eq": "work"}}


def test_any_condition_produces_list_type() -> None:
    def find(
        query: str,
        dict_filter: dict[str, Any] | None = None,
    ) -> str:
        return "ok"

    wrapped = wrap_filters(
        find,
        {"tags": _field("tags", "keyword", "any")},
    )
    annotation = inspect.signature(wrapped).parameters["tags"].annotation
    assert "list[str]" in repr(annotation)
