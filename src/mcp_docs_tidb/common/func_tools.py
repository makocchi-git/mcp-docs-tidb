import inspect
from functools import wraps
from typing import Any, Callable


def make_partial_function(
    original_func: Callable[..., Any], fixed_values: dict[str, Any]
) -> Callable[..., Any]:
    """
    Return a wrapper around `original_func` where the parameters listed in
    `fixed_values` are removed from the public signature and silently injected
    as keyword arguments at call time. Used to hide arguments that are already
    determined by configuration (e.g. a default collection name).
    """
    sig = inspect.signature(original_func)
    remaining_params = [name for name in sig.parameters if name not in fixed_values]

    @wraps(original_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound_args: dict[str, Any] = {}
        for name, value in zip(remaining_params, args):
            bound_args[name] = value
        bound_args.update(kwargs)
        # Fixed values are applied last so they cannot be overridden by the
        # caller — this is the whole point of make_partial_function.
        bound_args.update(fixed_values)
        return original_func(**bound_args)

    new_params = [sig.parameters[name] for name in remaining_params]
    wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
    return wrapper
