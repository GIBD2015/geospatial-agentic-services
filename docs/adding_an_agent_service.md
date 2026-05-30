# Adding a GAS Agent Service

This GAS server implementation publishes every GIS agent as a GAS web service.
Each service gets the same standard operations from the shared service
framework, while the agent implementation can be completely different.
The main focus of GAS is interoperability. GAS does not aim to prescribe how
geospatial agents should be designed, how they should reason, how their
performance should be improved, or how general agentic systems should be built.
It also does not assume that all geospatial agents must follow one specific
architecture, protocol, or implementation pattern. Instead, GAS focuses on the
interoperability layer needed when heterogeneous geospatial agents and services
need to work together.

The agents included in this repository are intended to be used as
implementation examples as well as working services. Before adding a new agent,
it is often helpful to inspect an existing agent with a similar style: for
example, a deterministic analysis agent, a model-assisted code-generation
agent, a data retrieval agent, a mapping agent, or a report-generating agent.
See [included_agents.md](included_agents.md) for a catalog of these examples.

Adding a new agent service is lightweight. In the common case,
you add three small pieces:

1. Agent implementation:
   [gas_server/agents/my_new_agent.py](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/agents)
2. Service wrapper:
   [gas_server/services/my_new_agent_service.py](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/services)
3. Capability document:
   [gas_server/capabilities/my_new_agent.json](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/capabilities)

The GAS server handles the repetitive server work: routing, request
parsing, input dataset materialization, sync/async/stream modes, task status,
artifact delivery, response normalization, and schema validation through tests.
The agent developer mainly focuses on the geospatial capability itself and
keep the `DescribeAgent` JSON aligned with what the agent actually supports.

The basic workflow is:

```text
implement the agent -> publish it with a tiny service wrapper -> describe it in JSON -> run tests
```

## 1. Add The Agent Implementation

Create a file in `gas_server/agents`, for example:

```text
gas_server/agents/my_new_agent.py
```

For examples, browse the existing
[agent implementations](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/agents)
and the [included agent guide](included_agents.md).

New agents must inherit from `GeoAgent` and implement the standard `run()`
method. 

```python
from gas_server.core.geo_agent import GeoAgent


class MyNewAgent(GeoAgent):
    agent_id = "my_new_agent"
    agent_name = "My New Agent"
    agent_version = "1.0.0"
    agent_description = "Describe what this geospatial agent does."
    requires_input_datasets = True

    def __init__(self, api_key=None, model=None):
        super().__init__(api_key=api_key, model=model or "gpt-5.2")

    def run(self, query: str, input_dataset_paths=None, progress_callback=None):
        self.reset_metrics()
        input_dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.emit_progress(
            progress_callback,
            stage="start",
            message="Starting the geospatial workflow.",
            data={"input_dataset_count": len(input_dataset_paths)},
        )

        # Do the agent-specific work here.
        self.increment_tool_calls()

        return self.success_result(
            "Task completed.",
            outputs={
                "dataset_paths": [],
                "dataset_size": {},
            },
            metrics=self.metrics(number_of_artifacts=0),
        )
```

If your agent needs at least one input dataset, set
`requires_input_datasets = True` on the agent class. The service registry will
automatically reject requests without `inputs.input_datasets`, so the agent
implementation can assume required input files were provided.

Emitting task progress is highly recommended. It gives users, browser clients,
and AI orchestrators transparent updates about what the agent is doing,
especially for agents with long execution times, LLM calls, code execution,
large file processing, or remote data downloads.

Progress events only affect streaming requests where `task.mode` is `"stream"`.
For non-streaming requests, `progress_callback` is normally `None`, and
`self.emit_progress(...)` safely does nothing.

To emit progress, accept `progress_callback` in `run()` and use
`self.emit_progress(...)` with the shared GAS stage vocabulary:

```python
self.emit_progress(
    progress_callback,
    stage="input_inspection",
    message="Inspecting input datasets.",
    data={"dataset_count": len(input_dataset_paths)},
)
```

If an agent does not implement progress events, streaming mode still works. The
server will still send generic lifecycle updates and the final result, but users
will not see detailed agent-specific execution progress while the agent runs.

### Progress Stage Design

Use `stage` as a short, stable, machine-readable lifecycle label. Use `message`
for the human-readable explanation, and `data` for optional structured details.

Common stages for most GAS agents:

```text
start
input_inspection
data_validation
method_selection
planning
llm_generation
code_execution
analysis_execution
artifact_generation
response_preparation
complete
retry
fallback_start
fallback_complete
warning
error
```

Domain-specific stages may be used when they describe the agent better:

```text
source_selection
source_validation
download_start
download_complete
normalization
map_design
layer_preparation
symbology
html_generation
model_selection
weights_construction
model_execution
diagnostics_generation
report_generation
```

Rule of thumb: keep `stage` reusable across agents, keep `message` specific to
the current task, and put counts, filenames, attempts, or selected methods in
`data`.

## 2. Publish the Agent as a GAS Service

Create:

```text
gas_server/services/my_new_agent_service.py
```

This file is only the publication wrapper for the agent. For most new agents,
the only lines you need to change are:

```python
from gas_server.agents.my_new_agent import MyNewAgent
REGISTRATION = register_geo_agent(MyNewAgent, __name__)
```

Replace `my_new_agent` and `MyNewAgent` with your new agent module and class.
Everything below the registration line should usually stay unchanged.

This file publishes the agent as a GAS service. It should stay intentionally
small: import the agent class, register it, and expose the shared lazy app/spec
accessors. 

```python
from __future__ import annotations

from gas_server.core.agent_registration import register_geo_agent

# CHANGE THIS for a new agent: import the GeoAgent subclass you want to publish.
from gas_server.agents.my_new_agent import MyNewAgent


# CHANGE THIS for a new agent: pass your imported agent class here.
REGISTRATION = register_geo_agent(MyNewAgent, __name__)

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
```

The registry auto-discovers every `*_agent_service.py` file with a
`REGISTRATION` object. That registration automatically drives:

- agent specs
- GAS server routing
- DescribeAgent document serving
- GetCapabilities agent listing
- runtime data folders

## 3. Add The Capability Document

Create:

```text
gas_server/capabilities/my_new_agent.json
```

For examples, browse the existing
[capability documents](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/capabilities)
and the [GAS interface guide](gas_interfaces.md).

Use the same `agent_id` everywhere: the agent class, service file, capability
document name, registry key, route, and runtime data folder. There is no
separate service key. For example, an agent with
`agent_id = "my_new_agent"` should use:

```text
gas_server/agents/my_new_agent.py
gas_server/services/my_new_agent_service.py
gas_server/capabilities/my_new_agent.json
/agents/my_new_agent
```

This file is part of the public GAS JSON interface for the agent. For a broader
explanation of the discovery, description, request, response, and artifact
interfaces, see [gas_interfaces.md](gas_interfaces.md).

The document should include:

- `profile`
- `keywords`
- `skills`
- `execute_task`
- `conformance`
- `provenance_and_reproducibility`
- `governance`
- `extensions`

`execute_task` is the agent-specific description of the standard GAS
ExecuteTask operation. Shared operation URLs are advertised once in
`GetCapabilities`; the agent document should not repeat them. The
`execute_task` section should include:

- `operation_id`
- `name`
- `description`
- `request_schema`
- `response_schema`
- `modes`
- `task`
- `inputs`
- `outputs`
- `parameters`
- `credentials`

For example:

```json
{
  "execute_task": {
    "operation_id": "execute_task",
    "name": "ExecuteTask",
    "description": "Submits a natural-language task to this agent.",
    "request_schema": "https://www.geospatial-agentic-services.online/schemas/execute_task_request.schema.json",
    "response_schema": "https://www.geospatial-agentic-services.online/schemas/task_response.schema.json",
    "modes": ["sync", "async", "stream"],
    "task": {
      "instructions": {
        "required": true,
        "description": "Natural-language instructions describing the task to perform."
      }
    },
    "inputs": {
      "input_datasets": {
        "required": true,
        "description": "One or more input datasets. Items may be URLs, server-accessible paths, or encoded file objects.",
        "supported_item_types": ["URL", "server_path", "encoded_file_object"]
      }
    },
    "outputs": {
      "artifact_delivery": {
        "required": false,
        "default": "URL",
        "allowed_values": ["URL", "Encoded"],
        "description": "Controls whether returned artifacts are delivered as downloadable URLs or encoded content."
      },
      "primary_artifacts": [
        {
          "type": "dataset",
          "formats": ["GeoJSON"],
          "required": true,
          "description": "Primary artifact returned by this agent."
        }
      ]
    },
    "parameters": {
      "model": {
        "required": false,
        "default": "gpt-5.2",
        "description": "Optional model override. If omitted, the agent default model is used."
      }
    },
    "credentials": {
      "required": true,
      "one_of": ["YOUR_AGENT_API_KEY"],
      "description": "Document the runtime credentials required by this agent. The field names and provider choices are agent-specific."
    }
  }
}
```

## Request Input Datasets

Use one public request field for all input data:

```json
{
  "task": {
    "instructions": "Analyze these datasets",
    "mode": "sync"
  },
  "inputs": {
    "input_datasets": [
      "https://example.com/counties.geojson",
      {
        "filename": "hospitals.geojson",
        "encoding": "base64",
        "data": "..."
      }
    ]
  },
  "outputs": {
    "artifact_delivery": "URL"
  }
}
```

`input_datasets` may contain URL strings or base64-encoded file objects. The
server materializes every item into a local file before calling the agent. New
agents should only expect:

```python
input_dataset_paths: list[str]
```

`outputs.artifact_delivery` controls how generated artifacts are returned. Use `URL` for
download links or `Encoded` for base64-embedded files.

## Credential Design

Document every credential requirement in the agent's `DescribeAgent` JSON. This
is part of the public service interface. A service consumer should be able to
determine from the capability document whether the agent needs an LLM API key,
a data-source key, both, or no key at all.

Credential design is agent-specific. A new agent developer may
choose to support OpenAI, Gemini, DeepSeek, a local/open-source model, a
deployment-provided built-in key, source-specific API keys, or a fully
deterministic workflow with no LLM key. The GAS framework only defines where
runtime credentials are supplied:

```json
{
  "credentials": {
    "YOUR_AGENT_API_KEY": "..."
  }
}
```

The included model-backed example agents support either a direct OpenAI API key
or a GIBD key:

```json
{
  "credentials": {
    "OPENAI_API_KEY": "..."
  },
  "outputs": {
    "artifact_delivery": "URL"
  }
}
```

or:

```json
{
  "credentials": {
    "GIBD_API_KEY": "..."
  },
  "outputs": {
    "artifact_delivery": "URL"
  }
}
```

Do not put actual API key values in capability documents or source code. The
capability document should describe the credential names, whether they are
required, where users can obtain them, and whether a deployment provides any
built-in credential for that agent.

Agents should keep a sensible default model in their constructor:

```python
def __init__(self, api_key=None, model=None):
    super().__init__(api_key=api_key, model=model or "gpt-5.2")
```

The default model is advertised as `profile.default_model` in the
`DescribeAgent` document. Clients may override it for a single request with
`parameters.model`; when that field is omitted, the agent uses the default
selected by the agent developer.

Data-source credentials should also be provided at request time. For data
retriever handbooks, pass them in `credentials.source_credentials` using the
handbook data source ID:

```json
{
  "task": {
    "instructions": "Download recent EPA AQS PM2.5 data.",
    "mode": "sync"
  },
  "credentials": {
    "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
    "source_credentials": {
      "EPA_AQS": {
        "email": "you@example.com",
        "key": "YOUR_EPA_AQS_KEY"
      },
    }
  },
  "outputs": {
    "artifact_delivery": "URL"
  }
}
```

The geospatial data retrieval agent replaces handbook placeholders such as `{EPA_AQS_email}` and
`{EPA_AQS_key}` from `source_credentials` during the request. Do not commit
data-source API keys to the repository.

## 4. Run Tests

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .tmp_pytest
```

## 5. Run The GAS Server

For normal use, start one GAS server. It loads every registered service and
publishes them all through the server's configured host and port:

```powershell
python -m gas_server.entrypoints.gas_server
```

The new service is then available at:

```text
/agents/my_new_agent/status
/agents/my_new_agent/tasks
/agents/my_new_agent/tasks/<task_id>/status
/agents/my_new_agent/tasks/<task_id>/result
/agents/my_new_agent/tasks/<task_id>/cancel
/agents/my_new_agent/data/<filename>
```

For environment setup, VS Code workflow, ngrok demos, resource planning, and
production hosting guidance, see
[development_and_deployment_environment.md](development_and_deployment_environment.md).

## Production Deployment

The command above uses Flask's built-in server, which is useful for local
development and demos. For production, run the GAS server with a production WSGI
server.

On Windows, a practical deployment option is Waitress. The example below uses
port `4042`, matching this repository's local development default, but the host
and port should be chosen by the server host:

```powershell
waitress-serve --host=0.0.0.0 --port=4042 gas_server.entrypoints.gas_server:app
```

On Linux, a common deployment option is Gunicorn. The example below also uses
port `4042` only as a deployment example:

```bash
gunicorn --bind 0.0.0.0:4042 --workers 2 --threads 4 gas_server.entrypoints.gas_server:app
```

Place the WSGI server behind a reverse proxy such as Nginx, Caddy, or Apache for
TLS termination, request size limits, access logging, compression, and process
supervision. Keep API keys and deployment settings in environment variables, not
in capability documents or source code. For heavy or long-running agents, tune
worker and thread counts carefully, and consider external task queues if request
durations become too long for HTTP clients.
