# GAS Client

Python SDK for Geospatial Agentic Services (GAS).

This package contains only the lightweight client layer. It does not install the
GAS server, Flask, GeoPandas, Rasterio, PySAL, or other geospatial runtime
dependencies.

## Install

Install from PyPI:

```powershell
python -m pip install gas-client
```

For local development from this repository:

```powershell
cd packages/gas-client
python -m pip install -e .
```

## Quick Start

```python
from gas_client import GasClient

client = GasClient(
    "https://your-gas-server.com",
    openai_api_key="YOUR_OPENAI_API_KEY",
)

print(client.list_agents())

agent = client.agent("geospatial_data_retrieval_agent")

result = agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="sync",
)

client.print_task_summary(result)
```

## Streaming Tasks

```python
for event in agent.execute_task(
    "Download Pennsylvania county boundaries from Census Bureau.",
    mode="stream",
):
    client.print_stream_event(event)
    if event.get("event") == "task_result":
        result = event.get("payload")

client.print_task_summary(result)
```

## Canonical GAS Request Body

Credential requirements are defined by each service's `DescribeAgent`
capability document. Inspect the selected agent before submitting a task: one
service may require an OpenAI key, another may use a different model provider,
another may require data-source credentials, and deterministic services may not
need an LLM key.

```python
request_body = client.build_execute_task_request(
    "Create a web mapping app.",
    mode="stream",
    input_datasets=[
        "https://example.com/counties.geojson",
    ],
    artifact_delivery="URL",
    credentials={
        "OPENAI_API_KEY": "YOUR_OPENAI_API_KEY",
    },
)

for event in client.agent("web_mapping_app_agent").execute_task_request(request_body):
    client.print_stream_event(event)
```

## Public API

```python
from gas_client import (
    GASClient,
    GasAgentClient,
    GasClient,
    GasClientError,
    GasTaskTimeoutError,
)
```

Important methods:

- `get_capabilities()`
- `list_agents()`
- `describe_agent(agent_id)`
- `agent(agent_id)`
- `execute_task(agent_id, instructions, mode="sync")`
- `execute_task_request(agent_id, request_body)`
- `get_task_status(agent_id, task_id)`
- `get_task_result(agent_id, task_id)`
- `wait_for_task(agent_id, task_id)`
- `cancel_task(agent_id, task_id)`
- `encode_dataset_file(path)`
- `print_stream_event(event)`
- `print_task_summary(result)`

For the full SDK guide, including task modes, artifact handling, encoded input
datasets, and service chaining patterns, see:

https://github.com/GIBD2015/geospatial-agentic-services/blob/main/docs/gas_client_sdk.md
