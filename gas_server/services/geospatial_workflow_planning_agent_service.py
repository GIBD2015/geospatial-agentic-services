from __future__ import annotations
from gas_server.core.agent_registration import register_geo_agent

# This file is the GAS service publication wrapper for GeospatialWorkflowPlanningAgent.
#
# To publish a new agent, copy this file and change only the marked lines:
#   1. CHANGE THIS import to point to your agent class, for example:
#      from gas_server.agents.my_new_agent import MyNewAgent
#   2. CHANGE THIS registration line to use your agent class:
#      REGISTRATION = register_geo_agent(MyNewAgent, __name__)
#
# Keep the _publish(), get_service_app(), get_service_spec(), and __getattr__
# functions unchanged. They lazily create the Flask service app and preserve
# compatibility with code that imports `app` or `SPEC` from this module.


# CHANGE THIS for a new agent: import the GeoAgent subclass you want to publish.
from gas_server.agents.geospatial_workflow_planning_agent import GeospatialWorkflowPlanningAgent

# CHANGE THIS for a new agent: pass your imported agent class here.
REGISTRATION = register_geo_agent(GeospatialWorkflowPlanningAgent, __name__)

# ======================Do not change anything below this line for a new agent.============ #
# NOTE: The following code is a standard pattern for lazy service publication.
# It ensures that the Flask app and service specification are only created when
# needed, and it allows other code to import `app` and `SPEC` directly from this
# module without triggering publication until necessary.
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
