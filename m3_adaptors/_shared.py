"""Shared machinery for the Path 2 monkey-patch layer."""
import types


def rebind(func, module):
    """Recreate ``func`` with ``module.__dict__`` as its globals.

    Lifted function bodies then resolve module-level helpers (and any
    test monkeypatching of them) through the target module's namespace at
    call time, exactly as if they still lived in the original file.
    ``classmethod``/``staticmethod`` wrappers are preserved.
    """
    if isinstance(func, (classmethod, staticmethod)):
        return type(func)(rebind(func.__func__, module))
    rebound = types.FunctionType(
        func.__code__, module.__dict__, func.__name__, func.__defaults__,
        func.__closure__)
    rebound.__kwdefaults__ = func.__kwdefaults__
    rebound.__annotations__ = dict(func.__annotations__)
    rebound.__doc__ = func.__doc__
    rebound.__module__ = module.__name__
    return rebound
