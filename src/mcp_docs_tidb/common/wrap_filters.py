"""
Replaces the generic ``query_filter`` parameter on the ``find`` tool with
one parameter per declared filterable field — so that the MCP client sees
a typed schema like ``category: str, year: int | None`` instead of an
opaque dict.
"""

import inspect
from functools import wraps
from typing import Annotated, Any, Callable, Optional

from pydantic import Field

from mcp_docs_tidb.common.filters import build_filter_from_fields
from mcp_docs_tidb.settings import FilterableField


def wrap_filters(
    original_func: Callable[..., Any],
    filterable_fields: dict[str, FilterableField],
) -> Callable[..., Any]:
    sig = inspect.signature(original_func)

    @wraps(original_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        filter_values: dict[str, Any] = {}
        for field_name in filterable_fields:
            if field_name in kwargs:
                filter_values[field_name] = kwargs.pop(field_name)

        dict_filter = build_filter_from_fields(filterable_fields, filter_values)
        return original_func(*args, **kwargs, dict_filter=dict_filter)

    base_params = [
        sig.parameters[name]
        for name in sig.parameters
        if name not in {"dict_filter", "query_filter"}
    ]

    required_new_params: list[inspect.Parameter] = []
    optional_new_params: list[inspect.Parameter] = []

    for field in filterable_fields.values():
        if field.field_type == "keyword":
            py_type: type = str
        elif field.field_type == "integer":
            py_type = int
        elif field.field_type == "float":
            py_type = float
        elif field.field_type == "boolean":
            py_type = bool
        else:
            raise ValueError(f"Unsupported field type: {field.field_type}")

        if field.condition in {"any", "except"}:
            if py_type not in {str, int}:
                raise ValueError(
                    'Only "keyword" and "integer" types are supported '
                    f'for "{field.condition}" condition'
                )
            py_type = list[py_type]  # type: ignore[valid-type]

        if field.required:
            annotation: Any = Annotated[py_type, Field(description=field.description)]  # type: ignore[valid-type]
            parameter = inspect.Parameter(
                name=field.name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
            )
            required_new_params.append(parameter)
        else:
            annotation = Annotated[
                Optional[py_type], Field(description=field.description)  # type: ignore[valid-type]
            ]
            parameter = inspect.Parameter(
                name=field.name,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=annotation,
            )
            optional_new_params.append(parameter)

    new_params = base_params + required_new_params + optional_new_params
    new_signature = sig.replace(parameters=new_params)
    wrapper.__signature__ = new_signature  # type: ignore[attr-defined]

    # FastMCP reads __annotations__ directly (in addition to __signature__)
    # when building the JSON schema for a tool. Updating __signature__ alone
    # is not enough — __annotations__ must be kept in sync so that the MCP
    # client sees the correct typed parameter list for the find tool.
    new_annotations: dict[str, Any] = {}
    for param in new_signature.parameters.values():
        if param.annotation is not inspect.Parameter.empty:
            new_annotations[param.name] = param.annotation
    if new_signature.return_annotation is not inspect.Signature.empty:
        new_annotations["return"] = new_signature.return_annotation
    wrapper.__annotations__ = new_annotations
    return wrapper
