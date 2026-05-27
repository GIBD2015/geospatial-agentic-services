import json

import pytest

from gas_client import (
    GASClient,
    GasClient,
    GasClientError,
    GasTaskTimeoutError,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, lines=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._lines = lines or []
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        yield from self._lines


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def capabilities_payload(*agents):
    return {
        "service": "GAS",
        "version": "1.0.0",
        "request": "GetCapabilities",
        "base_url": "http://127.0.0.1:4042",
        "operations": [
            {
                "operation_id": "get_capabilities",
                "name": "GetCapabilities",
                "method": "GET",
                "path": "/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities",
                "url": "http://127.0.0.1:4042/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities",
            },
            {
                "operation_id": "describe_agent",
                "name": "DescribeAgent",
                "method": "GET",
                "path": "/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={agent_id}",
                "url": "http://127.0.0.1:4042/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={agent_id}",
            },
            {
                "operation_id": "execute_task",
                "name": "ExecuteTask",
                "method": "POST",
                "path": "/agents/{agent_id}/tasks",
                "url": "http://127.0.0.1:4042/agents/{agent_id}/tasks",
            },
            {
                "operation_id": "get_task_status",
                "name": "GetTaskStatus",
                "method": "GET",
                "path": "/agents/{agent_id}/tasks/{task_id}/status",
                "url": "http://127.0.0.1:4042/agents/{agent_id}/tasks/{task_id}/status",
            },
            {
                "operation_id": "get_task_result",
                "name": "GetTaskResult",
                "method": "GET",
                "path": "/agents/{agent_id}/tasks/{task_id}/result",
                "url": "http://127.0.0.1:4042/agents/{agent_id}/tasks/{task_id}/result",
            },
            {
                "operation_id": "cancel_task",
                "name": "CancelTask",
                "method": "POST",
                "path": "/agents/{agent_id}/tasks/{task_id}/cancel",
                "url": "http://127.0.0.1:4042/agents/{agent_id}/tasks/{task_id}/cancel",
            },
            {
                "operation_id": "get_agent_status",
                "name": "GetAgentStatus",
                "method": "GET",
                "path": "/agents/{agent_id}/status",
                "url": "http://127.0.0.1:4042/agents/{agent_id}/status",
            },
        ],
        "agents": [
            {
                "agent_id": agent,
                "name": agent.replace("_", " ").title(),
                "DescribeAgent": f"http://127.0.0.1:4042/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={agent}",
            }
            for agent in agents
        ],
    }


def describe_payload(agent, *, operations=None):
    execute_task = (operations or [{"operation_id": "execute_task", "name": "ExecuteTask"}])[0]
    return {
        "profile": {"name": agent},
        "execute_task": execute_task,
    }


def test_print_stream_event_uses_timestamp_and_colon(capsys):
    client = GasClient("http://127.0.0.1:4042", load_capabilities=False)

    client.print_stream_event(
        {
            "event": "progress",
            "timestamp": "2026-05-18T22:52:14+00:00",
            "message": "Working on it.",
        },
        agent_name="Spatial Statistics Agent",
    )

    output = capsys.readouterr().out.strip()

    assert "] Spatial Statistics Agent: Working on it." in output


def test_print_stream_event_rewrites_framework_progress_voice(capsys):
    client = GasClient("http://127.0.0.1:4042", load_capabilities=False)

    client.print_stream_event(
        {
            "event": "progress",
            "timestamp": "2026-05-18T22:52:14+00:00",
            "message": "The spatial_statistics_agent is still working. Long LLM calls, code execution, or geospatial file processing can take a little while.",
        },
        agent_name="Spatial Statistics Agent",
    )

    output = capsys.readouterr().out.strip()

    assert "] Spatial Statistics Agent: I am still working." in output


def test_print_task_summary_includes_usage_artifacts_and_diagnostics(capsys):
    client = GasClient("http://127.0.0.1:4042", load_capabilities=False)

    client.print_task_summary(
        {
            "task": {"id": "task-1", "status": "successful"},
            "agent": {"id": "mapping_agent", "name": "Mapping Agent", "version": "1.0.0", "model": "gpt-test"},
            "outputs": {
                "summary": "Created a map.",
                "artifacts": [
                    {
                        "name": "map.png",
                        "format": "png",
                        "type": "downloadable_file",
                        "size_bytes": 1234,
                        "url": "http://example.test/map.png",
                    }
                ],
            },
            "execution": {"duration_seconds": 2.5, "iterations": 3},
            "provenance": {
                "llm_calls": 2,
                "tool_calls": 1,
                "token_usage": {"input_tokens": 100, "output_tokens": 25, "total_tokens": None},
            },
            "diagnostics": {"has_error": False, "warnings": []},
        }
    )

    output = capsys.readouterr().out

    assert "GAS Task Summary" in output
    assert "Mapping Agent" in output
    assert "Input tokens : 100" in output
    assert "Output tokens: 25" in output
    assert "Total tokens : 125" in output
    assert "map.png" in output
    assert "Warnings     : -" in output


def test_build_execute_task_request_adds_credentials_datasets_and_artifact_delivery():
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"OPENAI_API_KEY": "openai-key"},
        artifact_delivery="URL",
        load_capabilities=False,
    )

    payload = client.build_execute_task_request(
        "Map this",
        input_datasets=[
            "Data/example.geojson",
            "http://example.test/data.geojson",
        ],
        model="gpt-test-model",
        parameters={"requested_skill": "map_generation"},
    )

    assert payload["task"] == {"instructions": "Map this", "mode": "sync"}
    assert payload["credentials"]["OPENAI_API_KEY"] == "openai-key"
    assert payload["outputs"]["artifact_delivery"] == "URL"
    assert payload["inputs"]["input_datasets"] == [
        "Data/example.geojson",
        "http://example.test/data.geojson",
    ]
    assert payload["parameters"]["model"] == "gpt-test-model"
    assert payload["parameters"]["requested_skill"] == "map_generation"


def test_request_credentials_override_client_default_credentials():
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={
            "OPENAI_API_KEY": "client-openai-key",
            "GIBD_API_KEY": "client-gibd-key",
            "GEMINI_API_KEY": "client-gemini-key",
            "CUSTOM_PROVIDER_KEY": "client-custom-key",
        },
        load_capabilities=False,
    )

    payload = client.build_execute_task_request(
        "Run with request-specific credentials",
        credentials={
            "OPENAI_API_KEY": "request-openai-key",
            "GEMINI_API_KEY": "request-gemini-key",
            "source_credentials": {
                "OpenTopography": {"key": "source-key"},
            },
        },
    )

    assert payload["credentials"]["OPENAI_API_KEY"] == "request-openai-key"
    assert payload["credentials"]["GIBD_API_KEY"] == "client-gibd-key"
    assert payload["credentials"]["GEMINI_API_KEY"] == "request-gemini-key"
    assert payload["credentials"]["CUSTOM_PROVIDER_KEY"] == "client-custom-key"
    assert payload["credentials"]["source_credentials"]["OpenTopography"]["key"] == "source-key"


def test_get_capabilities_and_list_agents_use_advertised_response():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent", "spatial_statistics_agent")),
            FakeResponse(payload=describe_payload("mapping_agent")),
            FakeResponse(payload=describe_payload("spatial_statistics_agent")),
        ]
    )
    client = GasClient("http://127.0.0.1:4042", session=session)

    assert client.list_agents() == ["mapping_agent", "spatial_statistics_agent"]
    assert session.calls[0][1] == (
        "http://127.0.0.1:4042/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities"
    )


def test_discover_catalog_find_and_orchestrator_tools():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent", "spatial_statistics_agent")),
            FakeResponse(payload=describe_payload("mapping_agent")),
            FakeResponse(payload=describe_payload("spatial_statistics_agent")),
        ]
    )
    client = GasClient("http://127.0.0.1:4042", session=session)

    discovery = client.discover()
    catalog = client.get_agent_catalog(include_descriptions=True)
    matches = client.find_agents("spatial")
    tools = client.get_orchestrator_tools()

    assert [agent["agent_id"] for agent in discovery["agents"]] == [
        "mapping_agent",
        "spatial_statistics_agent",
    ]
    assert catalog[0]["operations"]
    assert matches[0]["agent_id"] == "spatial_statistics_agent"
    assert tools[0]["type"] == "function"
    assert tools[0]["metadata"]["operation"] == "execute_task"


def test_execute_task_uses_capabilities_execute_task_endpoint():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("spatial_statistics_agent")),
            FakeResponse(
                payload={
                    "task": {"id": "task-1", "status": "successful"},
                    "outputs": {"artifacts": []},
                }
            ),
        ]
    )
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"GIBD_API_KEY": "gibd-key"},
        session=session,
    )

    result = client.execute_task("spatial_statistics_agent", "Run Moran's I")

    assert result["task"]["id"] == "task-1"
    method, url, kwargs = session.calls[1]
    assert method == "POST"
    assert url == "http://127.0.0.1:4042/agents/spatial_statistics_agent/tasks"
    assert kwargs["json"]["credentials"]["GIBD_API_KEY"] == "gibd-key"


def test_execute_task_request_sends_canonical_json_body_unchanged():
    request_body = {
        "task": {
            "instructions": "Run Moran's I",
            "mode": "sync",
        },
        "outputs": {
            "artifact_delivery": "URL",
        },
        "credentials": {
            "OPENAI_API_KEY": "request-key",
        },
    }
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("spatial_statistics_agent")),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
        ]
    )
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"OPENAI_API_KEY": "client-key"},
        session=session,
    )

    result = client.execute_task_request("spatial_statistics_agent", request_body)

    assert result["task"]["id"] == "task-1"
    assert session.calls[1][1] == "http://127.0.0.1:4042/agents/spatial_statistics_agent/tasks"
    assert session.calls[1][2]["json"] == request_body


def test_bound_agent_client_runs_without_repeating_agent_id():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent")),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
        ]
    )
    client = GasClient("http://127.0.0.1:4042", session=session)

    mapping = client.agent("mapping_agent")
    result = mapping.execute_task("Create a map")

    assert mapping.agent_id == "mapping_agent"
    assert result["task"]["id"] == "task-1"
    assert session.calls[1][1] == "http://127.0.0.1:4042/agents/mapping_agent/tasks"


def test_bound_agent_client_can_execute_canonical_request_body():
    request_body = {
        "task": {
            "instructions": "Create a map",
            "mode": "sync",
        }
    }
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent")),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
        ]
    )
    client = GasClient("http://127.0.0.1:4042", session=session)

    result = client.agent("mapping_agent").execute_task_request(request_body)

    assert result["task"]["id"] == "task-1"
    assert session.calls[1][2]["json"] == request_body


def test_gasclient_alias_and_style_helpers(tmp_path):
    dataset = tmp_path / "sample.geojson"
    dataset.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    client = GASClient("http://127.0.0.1:4042", load_capabilities=False)

    encoded = client.encode_dataset_file(dataset)
    matches = client.get_value_by_key({"a": [{"url": "one"}, {"nested": {"url": "two"}}]}, "url")

    assert isinstance(client, GasClient)
    assert encoded["filename"] == "sample.geojson"
    assert encoded["encoding"] == "base64"
    assert len(matches) == 2
    assert matches[1]["full_path"] == ["a", 1, "nested", "url"]


def test_execute_task_async_mode_returns_task_id_and_wait_polls_until_terminal(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent")),
            FakeResponse(payload={"task": {"id": "task-1", "status": "accepted"}}),
            FakeResponse(payload={"task": {"id": "task-1", "status": "running"}}),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
        ]
    )
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"OPENAI_API_KEY": "test-key"},
        session=session,
    )
    monkeypatch.setattr("gas_client.client.time.sleep", lambda seconds: None)

    accepted = client.execute_task("mapping_agent", "Create a map", mode="async")
    task_id = client.get_task_id(accepted)
    result = client.wait_for_task("mapping_agent", task_id, poll_interval=0.01)

    assert task_id == "task-1"
    assert result["task"]["status"] == "successful"
    assert [call[0] for call in session.calls] == ["GET", "POST", "GET", "GET", "GET"]
    assert session.calls[1][1] == "http://127.0.0.1:4042/agents/mapping_agent/tasks"
    assert session.calls[2][1] == "http://127.0.0.1:4042/agents/mapping_agent/tasks/task-1/status"


def test_async_execute_task_can_be_combined_with_wait_for_task(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent")),
            FakeResponse(payload={"task": {"id": "task-1", "status": "accepted"}}),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
            FakeResponse(payload={"task": {"id": "task-1", "status": "successful"}}),
        ]
    )
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"OPENAI_API_KEY": "test-key"},
        session=session,
    )
    monkeypatch.setattr("gas_client.client.time.sleep", lambda seconds: None)

    accepted = client.execute_task("mapping_agent", "Create a map", mode="async")
    result = client.wait_for_task("mapping_agent", client.get_task_id(accepted), poll_interval=0.01)

    assert result["task"]["status"] == "successful"
    assert [call[0] for call in session.calls] == ["GET", "POST", "GET", "GET"]


def test_execute_task_stream_mode_yields_json_events():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("web_mapping_app_agent")),
            FakeResponse(
                lines=[
                    json.dumps({"event": "stream_connected", "status": "accepted"}),
                    json.dumps({"event": "task_result", "payload": {"task": {"id": "task-1"}}}),
                ]
            ),
            FakeResponse(payload=describe_payload("web_mapping_app_agent")),
        ]
    )
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"OPENAI_API_KEY": "test-key"},
        session=session,
    )

    events = list(client.execute_task("web_mapping_app_agent", "Create a web mapping app", mode="stream"))

    assert events[0]["event"] == "stream_connected"
    assert events[1]["payload"]["task"]["id"] == "task-1"
    assert session.calls[1][1] == "http://127.0.0.1:4042/agents/web_mapping_app_agent/tasks"
    assert session.calls[1][2]["stream"] is True


def test_bound_agent_client_stream_mode_uses_execute_task_only():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("web_mapping_app_agent")),
            FakeResponse(
                lines=[
                    json.dumps({"event": "stream_connected", "status": "accepted"}),
                    json.dumps({"event": "progress", "message": "Working"}),
                    json.dumps({"event": "task_result", "payload": {"task": {"id": "task-1"}}}),
                ]
            ),
            FakeResponse(payload=describe_payload("web_mapping_app_agent")),
        ]
    )
    client = GasClient(
        "http://127.0.0.1:4042",
        default_credentials={"OPENAI_API_KEY": "test-key"},
        session=session,
    )
    events = []

    for event in client.agent("web_mapping_app_agent").execute_task("Create a web mapping app", mode="stream"):
        events.append(event)
        if event.get("event") == "task_result":
            result = event["payload"]
            break

    assert result["task"]["id"] == "task-1"
    assert [event["event"] for event in events] == ["stream_connected", "progress", "task_result"]


def test_absolute_operation_urls_are_used_without_local_route_assumptions():
    capabilities = capabilities_payload("remote_agent")
    for operation in capabilities["operations"]:
        if operation["operation_id"] == "execute_task":
            operation["url"] = "https://remote.example.org/custom/tasks"

    session = FakeSession(
        [
            FakeResponse(payload=capabilities),
            FakeResponse(payload={"task": {"id": "remote-task"}}),
        ]
    )
    client = GasClient("https://catalog.example.org/gas", session=session)

    client.execute_task("remote_agent", "Run remotely")

    assert session.calls[1][1] == "https://remote.example.org/custom/tasks"


def test_get_supported_operations_returns_described_operation_urls():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent")),
        ]
    )
    client = GasClient("http://127.0.0.1:4042", session=session)

    operations = client.get_supported_operations("mapping_agent")

    assert operations["execute_task"] == "http://127.0.0.1:4042/agents/mapping_agent/tasks"
    assert operations["get_agent_status"] == "http://127.0.0.1:4042/agents/mapping_agent/status"


def test_helpers_extract_task_state_and_artifact_urls():
    client = GasClient("http://127.0.0.1:4042", load_capabilities=False)
    task = {
        "task": {"id": "task-1", "status": "successful"},
        "outputs": {"artifacts": [{"url": "http://example.test/a.html"}, {"name": "local.txt"}]},
    }

    assert client.get_task_id(task) == "task-1"
    assert client.get_task_status_value(task) == "successful"
    assert client.get_artifact_urls(task) == ["http://example.test/a.html"]


def test_wait_for_task_raises_timeout(monkeypatch):
    client = GasClient("http://127.0.0.1:4042", load_capabilities=False)
    monkeypatch.setattr(client, "get_task_status", lambda agent, task_id: {"task": {"status": "running"}})
    monkeypatch.setattr("gas_client.client.time.sleep", lambda seconds: None)
    ticks = iter([0, 2])
    monkeypatch.setattr("gas_client.client.time.monotonic", lambda: next(ticks))

    with pytest.raises(GasTaskTimeoutError):
        client.wait_for_task("mapping", "task-1", poll_interval=0, timeout_seconds=1)


def test_http_errors_raise_client_error():
    session = FakeSession(
        [
            FakeResponse(payload=capabilities_payload("mapping_agent")),
            FakeResponse(status_code=400, payload={"error": {"message": "bad"}}),
        ]
    )
    client = GasClient("http://127.0.0.1:4042", session=session)

    with pytest.raises(GasClientError):
        client.get_task_result("mapping_agent", "missing")

