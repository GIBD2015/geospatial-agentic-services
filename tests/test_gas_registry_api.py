from pathlib import Path
import uuid

import pytest

import gas_registry.app as registry_app


@pytest.fixture(autouse=True)
def clear_registry_admin_token(monkeypatch):
    monkeypatch.delenv("GAS_REGISTRY_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("GAS_REGISTRY_REQUIRE_ADMIN_TOKEN", "false")


def _sample_describe_agent(agent_id="mapping_agent"):
    return {
        "profile": {
            "agent_id": agent_id,
            "name": "Mapping Agent",
            "description": "Creates cartographic map outputs from geospatial datasets.",
            "version": "1.0.0",
            "base_url": f"https://example.test/agents/{agent_id}",
        },
        "keywords": ["mapping", "cartography"],
        "skills": [
            {
                "name": "Static mapping",
                "description": "Create map images from geospatial datasets.",
            }
        ],
        "operations": [{"name": "ExecuteTask"}],
        "provenance_and_reproducibility": {
            "provenance": {"supported": True},
            "reproducibility": {"supported": True},
            "validation": {"supported": False},
        },
        "conformance": {"gas_version": "1.0.0"},
    }


def _registry_client(monkeypatch):
    scratch_dir = Path(".tmp_pytest_run") / f"registry_api_{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    db_path = scratch_dir / "gas_registry.db"
    monkeypatch.setattr(registry_app, "DB_PATH", str(db_path))
    monkeypatch.setattr(
        registry_app.gas_registry,
        "describe_agent",
        lambda agent_name, **kwargs: _sample_describe_agent(agent_name),
    )
    registry_app.gas_registry.save_agent_to_db(
        "mapping_agent",
        db_path=str(db_path),
        base_url="https://example.test",
    )
    return registry_app.app.test_client()


def _add_registered_agent(monkeypatch, db_path, agent_id, base_url):
    monkeypatch.setattr(
        registry_app.gas_registry,
        "describe_agent",
        lambda agent_name, **kwargs: _sample_describe_agent(agent_name),
    )
    registry_app.gas_registry.save_agent_to_db(
        agent_id,
        db_path=str(db_path),
        base_url=base_url,
    )


def _patch_remote_server(monkeypatch):
    monkeypatch.setattr(
        registry_app.gas_registry,
        "get_capabilities",
        lambda **kwargs: [
            {
                "name": "mapping_agent",
                "describeUrl": "https://example.test/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=mapping_agent",
            },
            {
                "name": "raster_agent",
                "describeUrl": "https://example.test/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=raster_agent",
            },
        ],
    )


def test_registry_index_is_available_with_registry_prefix():
    response = registry_app.app.test_client().get("/registry")

    assert response.status_code == 200


def test_registry_api_root_lists_public_endpoints():
    response = registry_app.app.test_client().get("/registry/api/")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["endpoints"]["agents"] == "/registry/api/agents"
    assert payload["endpoints"]["servers"] == "/registry/api/servers"
    assert payload["endpoints"]["remote_agents"] == "/registry/api/remote-agents"
    assert payload["endpoints"]["register_selected_agents"] == "/registry/api/servers/selected-agents"


def test_registry_agents_api_lists_registered_agents(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/agents")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["agents"][0]["agent_id"] == "mapping_agent"
    assert "name" not in payload["agents"][0]
    assert "sourceBaseUrl" not in payload["agents"][0]
    assert payload["agents"][0]["detailUrl"].startswith("/registry/api/agents/")
    assert payload["agents"][0]["describeUrl"].startswith("https://example.test/?")


def test_registry_agents_api_filters_by_server(monkeypatch):
    scratch_dir = Path(".tmp_pytest_run") / f"registry_api_{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    db_path = scratch_dir / "gas_registry.db"
    monkeypatch.setattr(registry_app, "DB_PATH", str(db_path))
    _add_registered_agent(monkeypatch, db_path, "mapping_agent", "https://example.test")
    _add_registered_agent(monkeypatch, db_path, "raster_agent", "https://other.example")

    response = registry_app.app.test_client().get(
        "/registry/api/agents?server=https%3A%2F%2Fother.example"
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["agents"][0]["agent_id"] == "raster_agent"
    assert payload["agents"][0]["source_base_url"] == "https://other.example"


def test_registry_agent_detail_api_returns_stored_agent(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/api/agents/mapping_agent")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["profile"]["agent_id"] == "mapping_agent"


def test_registry_agent_detail_api_accepts_trailing_slash(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/agents/mapping_agent/")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["profile"]["agent_id"] == "mapping_agent"


def test_registry_search_api_returns_agents(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/agents/search?q=map&field=skills")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["agents"][0]["agent_id"] == "mapping_agent"
    assert "name" not in payload["agents"][0]
    assert "sourceBaseUrl" not in payload["agents"][0]


def test_registry_search_api_accepts_trailing_slash(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/agents/search/?q=map&field=skills")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1


def test_registry_legacy_ui_kvp_api_keeps_name_alias(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/gas?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["agents"][0]["name"].startswith("mapping_agent@")
    assert payload["agents"][0]["sourceBaseUrl"] == "https://example.test"


def test_registry_legacy_ui_search_keeps_name_alias(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/gas/search?q=map&field=skills")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["agents"][0]["name"].startswith("mapping_agent@")
    assert payload["agents"][0]["sourceBaseUrl"] == "https://example.test"


def test_registry_internal_remote_agents_api_uses_get(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.get("/registry/api/remote-agents?url=https%3A%2F%2Fexample.test")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 2
    assert [agent["name"] for agent in payload["agents"]] == ["mapping_agent", "raster_agent"]


def test_registry_internal_remote_agents_api_accepts_trailing_slash(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.get("/registry/api/remote-agents/?url=https%3A%2F%2Fexample.test")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"


def test_registry_legacy_ui_remote_agents_keeps_ok_shape(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.post("/registry/api/gas/list-remote", json={"url": "https://example.test"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert "status" not in payload


def test_registry_servers_api_lists_registered_servers(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/servers")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["servers"][0]["source_base_url"] == "https://example.test"
    assert payload["servers"][0]["agent_count"] == 1


def test_registry_servers_api_accepts_trailing_slash(monkeypatch):
    client = _registry_client(monkeypatch)

    response = client.get("/registry/api/servers/")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 1


def test_registry_register_server_api_uses_post(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.post("/registry/api/servers", json={"url": "https://example.test"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["count"] == 2


def test_registry_register_server_api_accepts_trailing_slash(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.post("/registry/api/servers/", json={"url": "https://example.test"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"


def test_registry_register_server_api_requires_admin_token_when_configured(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post("/registry/api/servers", json={"url": "https://example.test"})
    payload = response.get_json()

    assert response.status_code == 401
    assert payload["status"] == "error"


def test_registry_register_server_api_fails_closed_when_token_required_but_missing(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.delenv("GAS_REGISTRY_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("GAS_REGISTRY_REQUIRE_ADMIN_TOKEN", "true")

    response = client.post("/registry/api/servers", json={"url": "https://example.test"})
    payload = response.get_json()

    assert response.status_code == 500
    assert payload["status"] == "error"
    assert "GAS_REGISTRY_ADMIN_TOKEN is not set" in payload["error"]


def test_registry_legacy_ui_register_requires_admin_token_when_configured(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post("/registry/api/gas/register", json={"url": "https://example.test"})
    payload = response.get_json()

    assert response.status_code == 401
    assert payload["ok"] is False


def test_registry_legacy_ui_register_rejects_invalid_admin_token(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post(
        "/registry/api/gas/register",
        json={"url": "https://example.test"},
        headers={"X-Registry-Admin-Token": "wrong-token"},
    )
    payload = response.get_json()

    assert response.status_code == 401
    assert payload["ok"] is False
    assert payload["error"] == "Invalid registry admin token."


def test_registry_legacy_ui_register_accepts_admin_token_header(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post(
        "/registry/api/gas/register",
        json={"url": "https://example.test"},
        headers={"X-Registry-Admin-Token": "secret-token"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True


def test_registry_register_server_api_accepts_bearer_admin_token(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post(
        "/registry/api/servers",
        json={"url": "https://example.test"},
        headers={"Authorization": "Bearer secret-token"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"


def test_registry_register_selected_agents_api_uses_post(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.post(
        "/registry/api/servers/selected-agents",
        json={"url": "https://example.test", "names": ["raster_agent"]},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["registered"] == ["raster_agent"]


def test_registry_register_selected_agents_api_accepts_trailing_slash(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)

    response = client.post(
        "/registry/api/servers/selected-agents/",
        json={"url": "https://example.test", "names": ["raster_agent"]},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"


def test_registry_register_selected_agents_accepts_admin_token_header(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post(
        "/registry/api/servers/selected-agents",
        json={"url": "https://example.test", "names": ["raster_agent"]},
        headers={"X-Registry-Admin-Token": "secret-token"},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"


def test_registry_legacy_ui_register_selected_requires_admin_token_when_configured(monkeypatch):
    client = _registry_client(monkeypatch)
    _patch_remote_server(monkeypatch)
    monkeypatch.setenv("GAS_REGISTRY_ADMIN_TOKEN", "secret-token")

    response = client.post(
        "/registry/api/gas/register-selected",
        json={"url": "https://example.test", "names": ["raster_agent"]},
    )
    payload = response.get_json()

    assert response.status_code == 401
    assert payload["ok"] is False
