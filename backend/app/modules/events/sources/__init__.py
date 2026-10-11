import importlib
import pkgutil

from .spec import Source, SourceSpec


def discover() -> dict[str, Source]:
    found: dict[str, Source] = {}
    for info in pkgutil.iter_modules(__path__):
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
