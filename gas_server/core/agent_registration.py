from __future__ import annotations

from typing import Any


def register_geo_agent(*args: Any, **kwargs: Any):
    """Lazily delegate to the service-registry registration helper.

    Service wrapper modules import this shim. Keeping the import lazy avoids a
    circular import when a wrapper is imported directly in a fresh Python
    process while the registry is still discovering other wrappers.
    """
    from gas_server.core.service_registry import register_geo_agent as _register_geo_agent

    return _register_geo_agent(*args, **kwargs)

__all__ = ["register_geo_agent"]
