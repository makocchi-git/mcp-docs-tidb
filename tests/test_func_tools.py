"""
Unit tests for `make_partial_function`. No TiDB required.
"""

from __future__ import annotations

import inspect

from mcp_docs_tidb.common.func_tools import make_partial_function


def test_fixed_param_is_removed_from_signature() -> None:
    def f(a: int, b: int, c: int) -> int:
        return a + b + c

    wrapped = make_partial_function(f, {"c": 100})

    sig = inspect.signature(wrapped)
    assert list(sig.parameters) == ["a", "b"]
    assert wrapped(1, 2) == 103


def test_fixed_value_overrides_caller_value() -> None:
    def f(*, a: int, b: int) -> tuple[int, int]:
        return a, b

    wrapped = make_partial_function(f, {"a": 7})
    # 'a' is fixed; passing it again should be silently overridden by the fix.
    assert wrapped(b=3, a=99) == (7, 3)


def test_unknown_fixed_key_is_passed_through() -> None:
    # Keys in fixed_values that don't exist on the wrapped function are
    # forwarded as kwargs at call time. Use a function that accepts **kwargs
    # so the call succeeds — this documents the current behaviour.
    def f(a: int, **kwargs: int) -> dict[str, int]:
        return {"a": a, **kwargs}

    wrapped = make_partial_function(f, {"extra": 5})
    assert wrapped(a=1) == {"a": 1, "extra": 5}


def test_no_params_function_works() -> None:
    def f() -> str:
        return "hello"

    wrapped = make_partial_function(f, {})
    assert inspect.signature(wrapped).parameters == {}
    assert wrapped() == "hello"


def test_function_name_and_doc_preserved() -> None:
    def my_func(x: int) -> int:
        """My docstring."""
        return x

    wrapped = make_partial_function(my_func, {})
    assert wrapped.__name__ == "my_func"
    assert wrapped.__doc__ == "My docstring."


def test_async_function_is_wrapped_correctly() -> None:
    import asyncio

    async def af(a: int, b: int) -> int:
        return a + b

    wrapped = make_partial_function(af, {"b": 10})
    sig = inspect.signature(wrapped)
    assert "b" not in sig.parameters
    result = asyncio.run(wrapped(a=5))
    assert result == 15
