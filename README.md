# Geospatial Agentic Services (GAS) Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)
[![GAS Client on PyPI](https://img.shields.io/pypi/v/gas-client.svg)](https://pypi.org/project/gas-client/)
[![GAS Paper](https://img.shields.io/badge/GAS-Paper-green.svg)](https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence)
[![GIBD Lab](https://img.shields.io/badge/GIBD-Lab-lightgrey.svg)](https://giscience.psu.edu/)

This repository provides a concrete implementation of the server component of
the Geospatial Agentic Services (GAS) framework. It publishes geospatial agents
as independent web services, discoverable through
`GetCapabilities` and `DescribeAgent` JSON documents.

The included agents are working GAS services and concrete implementation
examples. Developers can use them as references for different design patterns,
including deterministic geospatial workflows, model-assisted code generation,
data retrieval, mapping, raster analysis, vector analysis, data inspection, and
spatial statistics.

For the conceptual framework behind this implementation, please refer to the
GAS paper: [Geospatial Agentic Services: A Framework for Interoperable
Geospatial Intelligence](https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence).

## Run the GAS Server

```powershell
python -m gas_server.entrypoints.gas_server
```

In local development, this implementation uses port `4042` by default. A GAS
server host can change the host, port, and public base URL for their deployment.
All registered agents are published under the configured server URL:

```text
http://127.0.0.1:4042/agents/{agent_id}
```

Common operations include:

- `/status`
- `/tasks` with `task.mode` set to `sync`, `async`, or `stream`
- `/tasks/<task_id>/status`
- `/tasks/<task_id>/result`
- `/tasks/<task_id>/cancel`
- `/data/<filename>`

## Discover Geospatial Agents

Get the server-level capability document:

```text
/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities
```

Describe one agent:

```text
/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=mapping_agent
```

## Add A New Geospatial Agent

Adding a new agent is plugin-style. Normally you add three files:

```text
gas_server/agents/my_new_agent.py
gas_server/services/my_new_agent_service.py
gas_server/capabilities/my_new_agent.json
```

The agent should inherit from `GeoAgent` and implement the standard `run()`
method. Do not implement `run_service()` in agent files; the base
`GeoAgent.run_service()` method is the shared service adapter used by the GAS
server.

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

    def run(self, query, input_dataset_paths=None, progress_callback=None):
        self.reset_metrics()
        input_dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.emit_progress(
            progress_callback,
            stage="start",
            message="Starting the geospatial workflow.",
            data={"input_dataset_count": len(input_dataset_paths)},
        )
        return self.success_result(
            "Task completed.",
            metrics=self.metrics(number_of_artifacts=0),
        )
```

Emitting task progress is highly recommended for transparency, especially for
long-running agents that perform LLM calls, code execution, large geospatial
file processing, or remote downloads. Progress events only apply to streaming
requests. If an agent does not emit progress, streaming mode still works, but
the server can only send generic lifecycle updates until the final result.

Credential requirements are agent-specific and should be documented in each
agent's `DescribeAgent` capability JSON. A new agent may require a
caller-provided LLM key, use a deployment-provided key, call another model
provider such as Gemini or DeepSeek, use a local/open-source model, require
data-source credentials, or run as a fully deterministic workflow with no LLM
key at all.

The built-in example agents in this repository support request-time
`OPENAI_API_KEY` or `GIBD_API_KEY` credentials for model-backed execution. Data-source
credentials can be passed with `source_credentials`, for example
`{"EPA_AQS": {"email": "...", "key": "..."}}`.

Each agent keeps a developer-selected default model. Clients may optionally
override it per request with `parameters.model`; if omitted, the default
model advertised in the agent's `DescribeAgent` document is used.

Use progress stages consistently. Recommended common stages include `start`,
`input_inspection`, `data_validation`, `method_selection`, `planning`,
`llm_generation`, `code_execution`, `analysis_execution`, `artifact_generation`,
`response_preparation`, `complete`, `retry`, `fallback_start`,
`fallback_complete`, `warning`, and `error`. Domain-specific agents can also use
stages such as `source_selection`, `download_complete`, `map_design`,
`layer_preparation`, `html_generation`, `model_selection`, `model_execution`,
and `report_generation`.

The service file should stay tiny:

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

See [docs/adding_an_agent_service.md](docs/adding_an_agent_service.md) for the full workflow,
capability JSON requirements, request payload examples, and production
deployment notes.

See [docs/gas_server_architecture.md](docs/gas_server_architecture.md) for an
overview of the SOA design, plugin-style service structure, and request flow.

See [docs/included_agents.md](docs/included_agents.md) for a catalog of the
included agents and the implementation patterns they demonstrate.

See [docs/gas_interfaces.md](docs/gas_interfaces.md) for the GAS
interfaces that define service discovery, agent description, task submission,
standard task responses, and artifact metadata.

See [docs/development_and_deployment_environment.md](docs/development_and_deployment_environment.md)
for local development setup, VS Code workflow, ngrok demos, server resource
planning, and production hosting guidance.

## GAS Client SDK

This repository also includes the lightweight GAS client SDK. The server is the
reference framework for publishing geospatial agent services, while the client
shows how Python notebooks, applications, and AI orchestrators can discover and
call those services.

Install the published SDK from PyPI:

```powershell
python -m pip install gas-client
```

Basic usage:

```python
from gas_client import GasClient

client = GasClient(
    "https://your-gas-server.com",
    openai_api_key="YOUR_OPENAI_API_KEY",
)

agent = client.agent("geospatial_data_retrieval_agent")
result = agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="sync",
)

client.print_task_summary(result)
```

The SDK source is kept in [gas_client](gas_client), and its standalone package
files are in [packages/gas-client](packages/gas-client). See
[docs/gas_client_sdk.md](docs/gas_client_sdk.md) for the SDK API and usage
guide. The package README is in
[packages/gas-client/README.md](packages/gas-client/README.md). Maintainer
publishing notes are in
[docs/gas_client_publishing.md](docs/gas_client_publishing.md).

## Example Notebooks

Example workflows are available in [examples_for_using_gas_services](examples_for_using_gas_services).
The notebook
[pa_health_food_hospitals_web_mapping_app_workflow.ipynb](examples_for_using_gas_services/pa_health_food_hospitals_web_mapping_app_workflow.ipynb)
demonstrates a streamed multi-agent workflow that downloads CDC health data,
OpenStreetMap fast-food restaurants, and PASDA hospitals, then uses
`web_mapping_app_agent` to build a browser-ready Pennsylvania web mapping app.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

For contribution guidance, see [CONTRIBUTING.md](CONTRIBUTING.md). For
credential and vulnerability reporting guidance, see [SECURITY.md](SECURITY.md).

## Repository Hygiene

Use `.env.example` as a safe template for local environment variables. Do not
commit real API keys, downloaded datasets, generated outputs, build artifacts,
or notebook execution outputs. The root `.gitignore` excludes common local
folders used by this repository, including `Data/`, `Output/`, `cache/`, test
scratch folders, virtual environments, and Python build artifacts.

## Acknowledgments

We thank the coauthors of the paper
[Geospatial Agentic Services: A Framework for Interoperable Geospatial
Intelligence](https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence)
for their contributions to the development of the broader GAS concepts.

We welcome contributions from the broader geospatial community, particularly
efforts to improve the GAS server and to develop, deploy, and host GAS agent
services.

[Geoinformation and Big Data Research Laboratory
(GIBD)](https://giscience.psu.edu/), Department of Geography, Penn State.

## License

This repository is released under the MIT License. See [LICENSE](LICENSE).

## Production Deployment

For production, run the Flask app with a WSGI server such as Waitress on Windows
or Gunicorn on Linux, and place it behind a reverse proxy for TLS, logging,
compression, and request-size control. See
[docs/development_and_deployment_environment.md](docs/development_and_deployment_environment.md) for
details.
