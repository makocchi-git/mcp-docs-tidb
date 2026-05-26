"""Additional tests for settings validation not covered by test_settings.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    FilterableField,
    TiDBSettings,
    ToolSettings,
)


class TestTiDBSettingsPortValidation:
    def test_port_zero_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="TIDB_PORT"):
            TiDBSettings(port=0)

    def test_port_negative_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="TIDB_PORT"):
            TiDBSettings(port=-1)

    def test_port_65536_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="TIDB_PORT"):
            TiDBSettings(port=65536)

    def test_port_1_is_accepted(self) -> None:
        s = TiDBSettings(port=1)
        assert s.port == 1

    def test_port_65535_is_accepted(self) -> None:
        s = TiDBSettings(port=65535)
        assert s.port == 65535

    def test_port_non_numeric_string_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TIDB_PORT", "abc")
        with pytest.raises(ValidationError):
            TiDBSettings()


class TestToolSettings:
    def test_default_descriptions_are_non_empty(self) -> None:
        s = ToolSettings()
        assert s.tool_store_description
        assert s.tool_find_description
        assert s.tool_ingest_description
        assert s.tool_list_description

    def test_env_override_is_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOL_STORE_DESCRIPTION", "custom store desc")
        s = ToolSettings()
        assert s.tool_store_description == "custom store desc"

    def test_empty_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_docs_tidb.settings import DEFAULT_TOOL_FIND_DESCRIPTION

        monkeypatch.setenv("TOOL_FIND_DESCRIPTION", "")
        s = ToolSettings()
        assert s.tool_find_description == DEFAULT_TOOL_FIND_DESCRIPTION


class TestEmbeddingProviderSettings:
    def test_defaults_are_fastembed_and_minilm(self) -> None:
        from mcp_docs_tidb.embeddings.types import EmbeddingProviderType

        s = EmbeddingProviderSettings()
        assert s.provider_type == EmbeddingProviderType.FASTEMBED
        assert "MiniLM" in s.model_name

    def test_model_name_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMBEDDING_MODEL", "custom/embed-model")
        s = EmbeddingProviderSettings()
        assert s.model_name == "custom/embed-model"


class TestFilterableField:
    def test_valid_field_is_constructed(self) -> None:
        f = FilterableField(
            name="category",
            description="The category",
            field_type="keyword",
            condition="==",
        )
        assert f.name == "category"
        assert f.field_type == "keyword"

    def test_invalid_field_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            FilterableField(
                name="f",
                description="d",
                field_type="bogus",  # type: ignore[arg-type]
                condition="==",
            )

    def test_invalid_condition_raises(self) -> None:
        with pytest.raises(ValidationError):
            FilterableField(
                name="f",
                description="d",
                field_type="keyword",
                condition="LIKE",  # type: ignore[arg-type]
            )

    def test_condition_none_is_allowed(self) -> None:
        f = FilterableField(name="f", description="d", field_type="integer")
        assert f.condition is None


class TestFilterableFieldsDictMethods:
    def test_filterable_fields_dict_returns_empty_for_none(self) -> None:
        s = TiDBSettings(filterable_fields=None)
        assert s.filterable_fields_dict() == {}

    def test_filterable_fields_dict_returns_empty_for_empty_list(self) -> None:
        s = TiDBSettings(filterable_fields=[])
        assert s.filterable_fields_dict() == {}

    def test_filterable_fields_dict_with_conditions_excludes_none_condition(self) -> None:
        fields = [
            FilterableField(name="a", description="d", field_type="keyword", condition="=="),
            FilterableField(name="b", description="d", field_type="integer"),
        ]
        s = TiDBSettings(filterable_fields=fields)
        result = s.filterable_fields_dict_with_conditions()
        assert "a" in result
        assert "b" not in result
