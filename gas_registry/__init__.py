"""GAS Registry package.

The registry stores and displays published GAS agent capability documents from
one or more GAS servers. Re-export the helper functions so `gas_registry.app`
can import `gas_registry` consistently when loaded by a WSGI server.
"""

from .gas_registry import *  # noqa: F401,F403
