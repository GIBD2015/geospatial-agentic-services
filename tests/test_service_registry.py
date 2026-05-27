from pathlib import Path

from gas_server.core.agent_specs import SPECS
from gas_server.core.service_registry import (
    SERVICE_REGISTRY,
    agent_ids,
    capability_files_by_agent_id,
    load_service_apps,
)


REQUIRED_INPUT_DATASET_AGENTS = {
    "exploratory_spatial_data_analysis_agent",
    "geospatial_data_inspection_agent",
    "web_mapping_app_agent",
    "mapping_agent",
    "map_projection_agent",
    "raster_agent",
    "spatial_analysis_agent",
    "spatial_statistics_agent",
    "vector_analysis_agent",
}


def test_service_registry_matches_agent_specs():
    assert set(SERVICE_REGISTRY) == set(SPECS)
    assert agent_ids() == tuple(SPECS)


def test_service_registry_capability_files_exist():
    capability_dir = Path("gas_server") / "capabilities"

    for agent_id, filename in capability_files_by_agent_id().items():
        assert filename == f"{agent_id}.json"
        assert (capability_dir / filename).is_file()


def test_service_modules_use_declarative_geo_agent_registration():
    service_dir = Path("gas_server") / "services"

    for registration in SERVICE_REGISTRY.values():
        path = service_dir / f"{registration.agent_id}_service.py"
        text = path.read_text(encoding="utf-8")

        assert "register_geo_agent" in text, path
        assert "gas_server.core.agent_registration" in text, path
        assert "gas_server.core.service_registry import register_geo_agent" not in text, path
        assert "def _publish()" in text, path
        assert "def get_service_app()" in text, path
        assert "def get_service_spec()" in text, path
        assert "ServiceRegistration(" not in text, path
        assert "run_agent=lambda" not in text, path
        assert "lazy_service_accessors" not in text, path


def test_service_registry_loads_all_service_apps():
    apps = load_service_apps()

    assert set(apps) == set(SPECS)
    for agent_id, app in apps.items():
        assert app.config["AGENT_SPEC"].agent_id == agent_id


def test_dataset_dependent_services_reject_requests_without_input_datasets():
    apps = load_service_apps()
    payload = {
        "task": {"instructions": "Run the requested geospatial task.", "mode": "sync"},
        "credentials": {"OPENAI_API_KEY": "test-key"},
    }

    for agent_id in REQUIRED_INPUT_DATASET_AGENTS:
        response = apps[agent_id].test_client().post("/tasks", json=payload)
        assert response.status_code == 400, agent_id
        assert "input_datasets" in response.get_json()["error"]["message"]


def test_gas_server_get_capabilities_is_registry_driven():
    from gas_server.entrypoints.gas_server import app

    response = app.test_client().get(
        "/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities"
    )
    payload = response.get_json()

    assert response.status_code == 200
    operations = {operation["operation_id"]: operation for operation in payload["operations"]}
    assert operations["execute_task"]["url"] == "http://localhost/agents/{agent_id}/tasks"
    assert operations["get_task_status"]["url"] == "http://localhost/agents/{agent_id}/tasks/{task_id}/status"
    assert {
        agent["agent_id"]
        for agent in payload["agents"]
    } == {
        registration.agent_id
        for registration in SERVICE_REGISTRY.values()
    }
    for agent in payload["agents"]:
        assert agent["name"]
        assert agent["name"] != agent["agent_id"]
        assert "describeUrl" not in agent
        assert agent["DescribeAgent"].endswith(f"REQUEST=DescribeAgent&agent_id={agent['agent_id']}")
        assert agent["DescribeAgent"].startswith("http://localhost/")


def test_gas_server_discovery_query_parameter_names_are_case_insensitive():
    from gas_server.entrypoints.gas_server import app

    response = app.test_client().get("/?service=gas&version=1.0.0&request=getcapabilities")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["request"] == "GetCapabilities"


def test_gas_server_discovery_requires_version_parameter():
    from gas_server.entrypoints.gas_server import app

    response = app.test_client().get("/?SERVICE=GAS&vs=1.0.0&REQUEST=GetCapabilities")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["error"]["code"] == "MISSING_VERSION"


def test_gas_server_discovery_rejects_unsupported_version():
    from gas_server.entrypoints.gas_server import app

    response = app.test_client().get("/?SERVICE=GAS&VERSION=2.0.0&REQUEST=GetCapabilities")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["error"]["code"] == "INVALID_VERSION"


def test_describe_agent_rewrites_profile_base_url_to_request_host():
    from gas_server.entrypoints.gas_server import app

    response = app.test_client().get(
        "/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=mapping_agent",
        headers={"Host": "example.test"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["profile"]["base_url"] == "http://example.test/agents/mapping_agent"


def test_describe_agent_query_parameter_names_are_case_insensitive():
    from gas_server.entrypoints.gas_server import app

    response = app.test_client().get(
        "/?Service=gas&Version=1.0.0&Request=describeagent&Agent_ID=mapping_agent",
        headers={"Host": "example.test"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["profile"]["agent_id"] == "mapping_agent"
    assert payload["profile"]["base_url"] == "http://example.test/agents/mapping_agent"


def test_agent_describe_endpoint_query_parameter_names_are_case_insensitive():
    app = load_service_apps()["mapping_agent"]

    response = app.test_client().get("/?service=gas&version=1.0.0&request=describeagent")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["profile"]["agent_id"] == "mapping_agent"


def test_agent_describe_endpoint_requires_version_parameter():
    app = load_service_apps()["mapping_agent"]

    response = app.test_client().get("/?SERVICE=GAS&vs=1.0.0&REQUEST=DescribeAgent")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["error"]["code"] == "MISSING_VERSION"



