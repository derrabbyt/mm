"""The Sources, discovered rather than listed.

Any module in this package that defines a `SPEC` plus `fetch` and `parse` is a
Source. Adding one is adding a file - there is no registration list to forget to
update, which matters at twenty-one of them.
"""

import importlib
import pkgutil

from .spec import Source, SourceSpec


def discover() -> dict[str, Source]:
    """Every Source in this package, by name."""
    found: dict[str, Source] = {}
    for info in pkgutil.iter_modules(__path__):
        # `spec` and `http` are in here too and fall out on their own: neither
        # defines a SPEC. Naming them would be the registration list this
        # docstring says there is none of.
        module = importlib.import_module(f"{__name__}.{info.name}")
        spec = getattr(module, "SPEC", None)
        if not isinstance(spec, SourceSpec):
            continue
        if not callable(getattr(module, "fetch", None)):
            continue
        if not callable(getattr(module, "parse", None)):
            continue
        found[spec.name] = module  # type: ignore[assignment]
    return found
