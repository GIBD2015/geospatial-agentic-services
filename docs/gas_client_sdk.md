# GAS Client SDK

The GAS Client SDK is a lightweight Python package for discovering and calling
Geospatial Agentic Services (GAS). It is intended for notebooks, scripts,
browser backends, workflow platforms, and AI orchestrators that consume GAS
services through the public web-service interfaces.

The SDK does not run the GAS server and does not install geospatial processing
libraries such as GeoPandas, Rasterio, PySAL, or GDAL. It only provides client
helpers for HTTP discovery, task execution, streaming events, task polling,
artifact handling, and readable result summaries.

## Install

Install the published client from PyPI:

```powershell
python -m pip install gas-client
```

For local development from this repository:

```powershell
cd packages/gas-client
python -m pip install -e .
```

## Import

```python
from gas_client import GasClient
```

The package also exports:

```python
from gas_client import (
    GASClient,
    GasAgentClient,
    GasClientError,
    GasTaskTimeoutError,
)
```

`GASClient` is an alias for `GasClient`.

## Create A Client

```python
client = GasClient("https://your-gas-server.com")
```

You can also configure provider-neutral default credentials when the same key
should be sent to many calls. Before choosing the credential field name, inspect
the selected agent's `DescribeAgent` JSON and use the key name that agent
advertises.

```python
client = GasClient(
    "https://your-gas-server.com",
    default_credentials={
        "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY",
    },
)
```

Common constructor arguments:

| Argument | Purpose |
|---|---|
| `server_url` | Root URL of the GAS server, such as `http://127.0.0.1:4042`. |
| `default_credentials` | Optional dictionary of default credential keys to send with task requests, such as `{"GEMINI_API_KEY": "..."}` or any key advertised by a server/agent. |
| `artifact_delivery` | Default artifact delivery mode: `URL` or `Encoded`. |
| `timeout` | Default HTTP timeout in seconds. |
| `session` | Optional custom `requests.Session` for advanced users or tests. |
| `load_capabilities` | Whether to fetch `GetCapabilities` during initialization. |

Credential requirements are service-specific. Users and orchestrating agents
should always inspect the selected agent's `DescribeAgent` document to see
whether it needs an LLM key, a data-source key, both, or no credential, and to
identify the exact credential field names to send.

You can provide no key when creating the client and pass credentials only to the
agent calls that need them. If a server or agent uses another provider, such as
Gemini, pass that key through `credentials` or through `default_credentials`.
The SDK does not interpret provider-specific names; it forwards the credential
fields expected by the selected GAS server. Request-level `credentials` override
client defaults for that call.

## Discovery

### GetCapabilities

```python
capabilities = client.get_capabilities()
```

Use this to retrieve the server-level capabilities document, including shared
operations and advertised agents.

### List Agents

```python
agent_ids = client.list_agents()
```

### DescribeAgent

```python
description = client.describe_agent("geospatial_data_retrieval_agent")
```

Use `DescribeAgent` before calling a service. It documents the agent profile,
skills, supported inputs, output artifacts, credentials, provenance support,
governance notes, and extensions.

### Agent Catalog

```python
catalog = client.get_agent_catalog(include_descriptions=True)
```

### Search Agents

```python
matches = client.find_agents("raster", include_descriptions=True)
```

### Orchestrator Tool Specs

```python
tools = client.get_orchestrator_tools()
```

This returns simple function-style tool descriptions that an external AI
orchestrator can expose to a model. The GAS server itself does not coordinate
agents; orchestration happens in clients or workflow systems.

## Agent-Bound Client

For repeated calls to one agent, bind the client to that agent:

```python
data_agent = client.agent("geospatial_data_retrieval_agent")
```

The returned `GasAgentClient` has convenience methods:

| Method | Purpose |
|---|---|
| `describe()` | Fetch this agent's `DescribeAgent` document. |
| `operations()` | Return operation URLs resolved for this agent. |
| `status()` | Call `GetAgentStatus`. |
| `execute_task(...)` | Execute a natural-language task. |
| `execute_task_request(...)` | Execute a complete canonical GAS request body. |
| `get_task_status(task_id)` | Get task status. |
| `get_task_result(task_id)` | Get task result. |
| `wait_for_task(task_id)` | Poll until task completion. |
| `cancel_task(task_id)` | Request best-effort cancellation. |

## ExecuteTask Modes

GAS supports three task execution modes:

| Mode | Behavior |
|---|---|
| `sync` | Request waits for the final task result. |
| `async` | Request returns a task ID; the client checks status/result later. |
| `stream` | Request streams progress events and the final task result. |

### Sync Task

```python
result = data_agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="sync",
    credentials={"OPENAI_API_KEY": "YOUR_OPENAI_API_KEY"},
)

client.print_task_summary(result)
```

### Async Task

```python
submitted = data_agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="async",
)

task_id = client.get_task_id(submitted)
status = data_agent.get_task_status(task_id)
result = data_agent.wait_for_task(task_id, poll_interval=5, timeout_seconds=900)
```

### Streaming Task

```python
final_result = None

for event in data_agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="stream",
):
    client.print_stream_event(event)
    if event.get("event") == "task_result":
        final_result = event.get("payload")

client.print_task_summary(final_result)
```

Streaming events are useful for long-running agents because the user can see
progress while the task is running. If an agent does not emit detailed progress,
the server still streams generic lifecycle events and the final result.

## Canonical GAS Request Body

For normal use, `execute_task(...)` is simpler. For orchestrators or systems
that want full control over the JSON body, use
`build_execute_task_request(...)` and `execute_task_request(...)`.

```python
request_body = client.build_execute_task_request(
    "Create a web mapping app.",
    mode="stream",
    input_datasets=[
        "https://example.com/counties.geojson",
    ],
    artifact_delivery="URL",
    # Optional: include credentials here only when this call needs a key
    # and the client was not created with suitable default credentials.
    # Credential names are server- and agent-dependent.
    credentials={
        "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
    },
    parameters={
        "model": "gpt-5.2",
    },
)

for event in client.agent("web_mapping_app_agent").execute_task_request(request_body):
    client.print_stream_event(event)
```

The generated request follows the GAS `ExecuteTask` schema:

```json
{
  "task": {
    "instructions": "Create a web mapping app.",
    "mode": "stream"
  },
  "inputs": {
    "input_datasets": [
      "https://example.com/counties.geojson"
    ]
  },
  "outputs": {
    "artifact_delivery": "URL"
  },
  "parameters": {
    "model": "gpt-5.2"
  },
  "credentials": {
    "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY"
  }
}
```

## Input Datasets

`input_datasets` may contain:

- URL strings, such as `https://example.com/data.gpkg`
- Server-accessible path strings, when supported by the deployment
- Encoded file objects created from local files

Encode a local file:

```python
encoded = client.encode_dataset_file("local_data.geojson")

result = client.agent("vector_analysis_agent").execute_task(
    "Buffer these features by 5 miles.",
    mode="sync",
    input_datasets=[encoded],
)
```

`build_execute_task_request(...)` applies the same credential-default behavior
as `execute_task(...)`: client-level credentials are included when present, and
keys in the `credentials` argument take precedence. Credential field names are
server- and agent-dependent. `execute_task_request(...)` sends a fully built
request body unchanged.

## Artifact Delivery

`artifact_delivery` controls how output artifacts are returned:

| Value | Behavior |
|---|---|
| `URL` | Artifacts are returned as downloadable URLs. This is the default. |
| `Encoded` | Artifacts are embedded in the response as encoded payloads when supported. |

For most notebooks and web workflows, `URL` is recommended because geospatial
artifacts can be large.

## Artifact Helpers

```python
artifacts = client.get_artifacts(result)
urls = client.get_artifact_urls(result)
```

Each artifact is part of the standard GAS task response under
`outputs.artifacts`. A single task can return multiple artifacts; for example,
`geospatial_data_retrieval_agent` may return several URLs when one request asks
for multiple independent datasets.

The `reproducibility.input_artifacts` and
`reproducibility.output_artifacts` fields are provenance references, not
artifact delivery fields. They identify the input and output files involved in
the task for audit or rerun workflows. Large payloads, including base64 encoded
artifact data, are returned only through `outputs.artifacts` according to the
requested `artifact_delivery` mode.

## Display Helpers

### Print Stream Events

```python
client.print_stream_event(event)
```

This prints a timestamped, readable streaming event. Progress events use the
agent's human-readable name when available.

### Print Task Summary

```python
client.print_task_summary(result)
```

This prints task ID, status, agent, model, duration, token usage, artifacts,
and diagnostics in a compact notebook-friendly format.

## Error Classes

```python
from gas_client import GasClientError, GasTaskTimeoutError
```

`GasClientError` is raised for GAS client/request issues. `GasTaskTimeoutError`
is raised when `wait_for_task(...)` reaches its timeout before a terminal task
status.

## Notebook Pattern

A common notebook pattern is:

```python
from gas_client import GasClient

client = GasClient("http://127.0.0.1:4042")

agent = client.agent("geospatial_data_retrieval_agent")

result = None
for event in agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="stream",
    credentials={"OPENAI_API_KEY": "YOUR_OPENAI_API_KEY"},
):
    client.print_stream_event(event)
    if event.get("event") == "task_result":
        result = event.get("payload")

client.print_task_summary(result)
```

## Service Chaining Pattern

Clients and orchestrators can chain GAS services by passing artifact URLs from
one task into the next:

```python
data_result = client.agent("geospatial_data_retrieval_agent").execute_task(
    "Download Pennsylvania county boundaries as GeoPackage.",
    mode="sync",
)

county_url = client.get_artifact_urls(data_result)[0]

map_result = client.agent("web_mapping_app_agent").execute_task(
    "Create a choropleth web mapping app.",
    mode="sync",
    input_datasets=[county_url],
)
```

This is the intended GAS pattern: each agent is an independent service, and
external clients, notebooks, workflow systems, or AI orchestrators coordinate
service chains.

For multi-dataset retrieval requests, collect all URLs from
`client.get_artifact_urls(data_result)` and pass the subset needed by each
downstream service.
