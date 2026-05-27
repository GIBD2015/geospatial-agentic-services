from __future__ import annotations
from gas_server.core.agent_registration import register_geo_agent

from gas_server.agents.usgs_earthquake_agent import USGSEarthquakeAgent

REGISTRATION = register_geo_agent(USGSEarthquakeAgent, __name__)

_APP = None
_SPEC = None


def _publish():
    global _APP, _SPEC
    if _APP is None or _SPEC is None:
        from gas_server.core.service_publisher import publish_service

        _APP, _SPEC, _ = publish_service(REGISTRATION.agent_id)
    return _APP, _SPEC


def get_service_app():
    return _publish()[0]


def get_service_spec():
    return _publish()[1]


def __getattr__(name: str):
    if name == "app":
        return get_service_app()
    if name == "SPEC":
        return get_service_spec()
    raise AttributeError(name)
