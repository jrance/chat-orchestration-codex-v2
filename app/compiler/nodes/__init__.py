"""Node compiler registry."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder

NodeCompiler = Callable[["GraphBuilder", dict[str, Any], dict[str, Any]], None]

_compilers: Dict[str, NodeCompiler] = {}
_bootstrapped = False


def register(kind: str, fn: NodeCompiler) -> None:
    """Register a node compiler for ``kind``."""

    _compilers[kind] = fn


def get(kind: str) -> NodeCompiler | None:
    """Fetch a node compiler for ``kind``."""

    return _compilers.get(kind)


def available() -> Dict[str, NodeCompiler]:
    """Return a copy of the registered compilers."""

    return dict(_compilers)


def clear() -> None:
    """Reset the registry (used in tests)."""

    _compilers.clear()


def ensure_builtin_compilers() -> None:
    """Ensure core node compilers are loaded."""

    global _bootstrapped
    if _bootstrapped:
        return

    for module_name in ("agent_codeless", "router", "sequential", "concurrent"):
        import_module(f"{__name__}.{module_name}")

    _bootstrapped = True
