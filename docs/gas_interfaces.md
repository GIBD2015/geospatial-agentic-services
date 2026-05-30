# GAS Interfaces

The GAS JSON documents define the machine-readable interfaces between GAS
servers, geospatial agent services, clients, browsers, notebooks, and AI
orchestrators. In software terms, these interfaces also act as service
contracts, but this document uses "interfaces" to emphasize discovery,
interoperability, and implementation independence.

These JSON interfaces are central to geospatial intelligence interoperability:
a client should be able to discover available services, understand an agent's
capabilities, submit a task, monitor execution, and interpret returned
artifacts without importing the agent's Python code or knowing its internal
implementation.

## Interface Files

The current implementation defines these public JSON interfaces:

- [gas_server/capabilities/capabilities.json](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/capabilities.json)
- [gas_server/capabilities/{agent_id}.json](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/capabilities)
- [gas_server/schemas/capabilities.schema.json](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/schemas/capabilities.schema.json)
- [gas_server/schemas/describe_agent.schema.json](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/schemas/describe_agent.schema.json)
- [gas_server/schemas/execute_task_request.schema.json](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/schemas/execute_task_request.schema.json)
- [gas_server/schemas/task_response.schema.json](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/schemas/task_response.schema.json)

`capabilities.json` and each `{agent_id}.json` file are service documents.
The files in `gas_server/schemas` define the expected structure of those
documents and task messages.

Example interface file mapping:

```json
{
  "server_discovery_document": "gas_server/capabilities/capabilities.json",
  "agent_description_document": "gas_server/capabilities/vector_analysis_agent.json",
  "server_discovery_schema": "gas_server/schemas/capabilities.schema.json",
  "agent_description_schema": "gas_server/schemas/describe_agent.schema.json",
  "task_request_schema": "gas_server/schemas/execute_task_request.schema.json",
  "task_response_schema": "gas_server/schemas/task_response.schema.json"
}
```

## Server Discovery: GetCapabilities

The server-level capability document is returned by:

```text
GET /?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities
```

For the GAS server query interface, parameter names are matched
case-insensitively. For example, `SERVICE`, `service`, and `Service` are
treated as the same parameter name. The `SERVICE` and `REQUEST` values are
also matched case-insensitively. The `VERSION` parameter is required and must
currently be `1.0.0`; a misspelled parameter such as `vs=1.0.0` is not treated
as a valid version.

It tells clients what the GAS server publishes. Important fields include:

- `service`: identifies the service family, currently `GAS`.
- `version`: the GAS interface version used by this server.
- `base_url`: the public base URL configured by the server host.
- `provider`: the organization or person operating the GAS server.
- `operations`: the common server operations, including `GetCapabilities`,
  `DescribeAgent`, `ExecuteTask`, `GetTaskStatus`, `GetTaskResult`, and
  `CancelTask`.
- `agents`: the published geospatial agent services, each with `agent_id`,
  human-readable `name`, and `DescribeAgent` URL.

`capabilities.schema.json` validates this document. Agent developers normally
do not edit the shared schema, but server hosts should keep `capabilities.json`
accurate for their deployment URL, provider, operations, and published agents.

Example `GetCapabilities` response:

```json
{
  "service": "GAS",
  "version": "1.0.0",
  "base_url": "https://your-gas-server.example.com",
  "request": "GetCapabilities",
  "title": "Geospatial Agentic Services",
  "description": "Server implementation for publishing geospatial agent services.",
  "provider": {
    "name": "Example GIS Lab",
    "website": "https://example.com",
    "contact": {
      "name": "Service Maintainer",
      "email": "maintainer@example.com"
    }
  },
  "operations": [
    {
      "operation_id": "execute_task",
      "name": "ExecuteTask",
      "method": "POST",
      "path": "/agents/{agent_id}/tasks",
      "url": "https://your-gas-server.example.com/agents/{agent_id}/tasks",
      "description": "Executes an agent task using sync, async, or stream mode.",
      "request_schema": "https://your-gas-server.example.com/schemas/execute_task_request.schema.json",
      "response_schema": "https://your-gas-server.example.com/schemas/task_response.schema.json"
    }
  ],
  "agents": [
    {
      "agent_id": "vector_analysis_agent",
      "name": "Vector Analysis Agent",
      "DescribeAgent": "https://your-gas-server.example.com/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=vector_analysis_agent"
    }
  ]
}
```

## Agent Description: DescribeAgent

Each agent has a `DescribeAgent` document returned by:

```text
GET /?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={agent_id}
```

This is the main interoperability document for a single geospatial agent
service. It describes what the agent does, what it expects, what it returns,
and what runtime credentials or parameters it supports.

Important sections include:

- `profile`: stable identity and service metadata, including `agent_id`,
  display `name`, `description`, `version`, `base_url`, provider, and optional
  default model.
- `keywords`: search and discovery terms.
- `skills`: agent-specific capabilities and constraints.
- `execute_task`: the task submission interface for this agent, including
  supported modes, input requirements, output artifact expectations,
  parameters, and credentials.
- `conformance`: implementation notes about standard GAS behavior supported by
  the agent.
- `provenance_and_reproducibility`: whether provenance, reproducibility, and
  validation information are returned.
- `governance`: autonomy level, human-review expectations, risk notes, and
  data sensitivity guidance.
- `extensions`: agent-specific metadata that does not fit the shared core
  interface.

Credential requirements are intentionally agent-specific. A `DescribeAgent`
document should state whether the agent requires a caller-provided LLM key,
uses a deployment-provided key, supports a particular model provider, requires
data-source credentials, or runs without an LLM key. Actual credential values
must not be placed in capability documents.

`describe_agent.schema.json` validates the structure of each agent description.

Example `DescribeAgent` document:

```json
{
  "profile": {
    "agent_id": "vector_analysis_agent",
    "name": "Vector Analysis Agent",
    "description": "Performs vector geospatial analysis such as joins, buffers, clips, intersections, filtering, and aggregation.",
    "version": "1.0.0",
    "base_url": "https://your-gas-server.example.com/agents/vector_analysis_agent",
    "default_model": "gpt-5.2",
    "provider": {
      "name": "Example GIS Lab",
      "website": "https://example.com",
      "contacts": [
        {
          "name": "Service Maintainer",
          "email": "maintainer@example.com",
          "role": "maintainer"
        }
      ]
    }
  },
  "keywords": ["vector analysis", "spatial join", "buffer", "clip"],
  "skills": [
    {
      "skill_id": "vector_overlay",
      "name": "Vector Overlay and Join",
      "description": "Runs common vector overlay and join workflows on supplied datasets.",
      "constraints": {
        "inputs": "Requires at least one vector dataset readable by GeoPandas.",
        "outputs": "Returns GeoPackage by default unless another supported format is requested."
      }
    }
  ],
  "execute_task": {
    "operation_id": "execute_task",
    "name": "ExecuteTask",
    "description": "Submits a natural-language vector analysis task.",
    "request_schema": "https://your-gas-server.example.com/schemas/execute_task_request.schema.json",
    "response_schema": "https://your-gas-server.example.com/schemas/task_response.schema.json",
    "modes": ["sync", "async", "stream"],
    "task": {
      "instructions": {
        "required": true,
        "description": "Natural-language task instructions."
      }
    },
    "inputs": {
      "input_datasets": {
        "required": true,
        "supported_item_types": ["URL", "server_path", "encoded_file_object"],
        "description": "Vector or tabular datasets used by the analysis."
      }
    },
    "outputs": {
      "artifact_delivery": {
        "required": false,
        "default": "URL",
        "allowed_values": ["URL", "Encoded"],
        "description": "Controls how generated artifacts are delivered in the task response."
      },
      "primary_artifacts": [
        {
          "type": "vector",
          "formats": ["GeoPackage", "GeoJSON"],
          "required": true,
          "description": "Vector analysis result."
        }
      ]
    },
    "parameters": {
      "model": {
        "required": false,
        "default": "gpt-5.2",
        "description": "Optional model override for model-assisted workflows."
      }
    },
    "credentials": {
      "required": true,
      "one_of": ["OPENAI_API_KEY", "GIBD_API_KEY"],
      "description": "Credential options supported by this specific agent deployment."
    }
  },
  "conformance": {
    "gas_version": "1.0.0",
    "supports_async_tasks": true,
    "supports_streaming": true,
    "supports_artifact_delivery": ["URL", "Encoded"]
  },
  "provenance_and_reproducibility": {
    "provenance": {
      "supported": true,
      "description": "Returns LLM calls, tool calls, token usage, and artifact counts."
    },
    "reproducibility": {
      "supported": true,
      "description": "Returns generated code and runtime environment when available."
    },
    "validation": {
      "supported": true,
      "description": "Reports artifact readability and spatial metadata inspection."
    }
  },
  "governance": {
    "autonomy_level": "automatic",
    "human_review": "recommended",
    "risk_level": "low",
    "data_sensitivity": "depends_on_input_data",
    "policy_notes": "Review analytical outputs before decision-making use."
  },
  "extensions": {
    "implementation_notes": {
      "description": "Common joins, buffers, clips, and intersections use deterministic fast paths."
    }
  }
}
```

## Task Submission: ExecuteTask Request

Tasks are submitted to an agent service with:

```text
POST /agents/{agent_id}/tasks
```

The canonical request body is defined by
`execute_task_request.schema.json`. Its top-level sections are:

- `task`: the natural-language task and interaction mode.
- `inputs`: input datasets or other formal task inputs.
- `outputs`: client preferences for returned artifacts.
- `parameters`: optional execution parameters, such as model overrides or
  agent-specific options.
- `credentials`: runtime credentials supplied by the caller, following the
  selected agent's `DescribeAgent` document.
- `metadata`: optional client-side context for tracing, logging,
  reproducibility, or orchestration.

Example:

```json
{
  "task": {
    "instructions": "Create a web mapping app from these county boundaries and hospital points.",
    "mode": "stream"
  },
  "inputs": {
    "input_datasets": [
      "https://example.com/pa_counties.geojson",
      {
        "filename": "pa_hospitals.geojson",
        "encoding": "base64",
        "mime_type": "application/geo+json",
        "data": "BASE64_ENCODED_FILE_CONTENT"
      }
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
  },
  "metadata": {
    "client_id": "jupyter-notebook",
    "request_id": "map-demo-001"
  }
}
```

`task.mode` controls interaction style:

- `sync`: wait for the final task response.
- `async`: return a task ID immediately, then use status/result operations.
- `stream`: return progress events followed by the final task response.

`inputs.input_datasets` may include URL/path strings or encoded file objects.
The GAS server materializes those inputs before calling the agent.

`outputs.artifact_delivery` controls how generated files are represented in the
response:

- `URL`: artifacts are returned as downloadable links.
- `Encoded`: artifacts are embedded as base64 content.

Example URL-based input dataset:

```json
{
  "inputs": {
    "input_datasets": [
      "https://example.com/data/counties.gpkg"
    ]
  }
}
```

Example encoded input dataset:

```json
{
  "inputs": {
    "input_datasets": [
      {
        "filename": "hospitals.geojson",
        "encoding": "base64",
        "mime_type": "application/geo+json",
        "data": "BASE64_ENCODED_FILE_CONTENT"
      }
    ]
  }
}
```

Example asynchronous task request:

```json
{
  "task": {
    "instructions": "Join these hospital points to county boundaries using county FIPS codes.",
    "mode": "async"
  },
  "inputs": {
    "input_datasets": [
      "https://example.com/counties.gpkg",
      "https://example.com/hospitals.csv"
    ]
  },
  "outputs": {
    "artifact_delivery": "URL"
  },
  "credentials": {
    "YOUR_AGENT_API_KEY": "YOUR_RUNTIME_SECRET"
  }
}
```

## Task Result: Standard Response

Completed tasks return the standard GAS task response, validated by
`task_response.schema.json`. This response shape should be consistent across
agents, even when the internal agent implementations are very different.

Top-level sections include:

- `response`: response type, schema version, and timestamps.
- `task`: task ID, status, terminal flag, original user request, requested
  skill, and artifact delivery mode.
- `agent`: agent ID, name, version, and model used when applicable.
- `outputs`: summary, generated artifacts, and aggregate data summary.
- `execution`: status, duration, inputs, generated code when available, and
  runtime environment.
- `provenance`: LLM calls, tool calls, token usage, artifact counts, and
  lineage when available.
- `reproducibility`: code, environment, parameters, input artifact references,
  output artifact references, and stochasticity notes.
- `diagnostics`: errors, warnings, validation results, assumptions, and
  limitations.

`reproducibility.input_artifacts` and `reproducibility.output_artifacts` are
lightweight provenance references. Actual artifact delivery belongs in
`outputs.artifacts`, including downloadable URLs or encoded file data when the
caller requests encoded artifacts. Reproducibility artifact references should
therefore identify the artifact and its role, but should not repeat large
encoded payloads.

Task status values are:

```text
accepted
running
successful
failed
canceled
rejected
```

Example successful task response:

```json
{
  "response": {
    "type": "task_result",
    "schema_version": "1.0.0",
    "created_at": "2026-05-19T15:00:00Z",
    "completed_at": "2026-05-19T15:00:12Z"
  },
  "task": {
    "id": "7b2f0f6d-3d34-4d9f-bbe3-6c5d1c4b8b52",
    "status": "successful",
    "terminal": true,
    "user_request": "Join county boundaries to obesity rates and return a GeoPackage.",
    "requested_skill": null,
    "artifact_delivery": "URL"
  },
  "agent": {
    "id": "vector_analysis_agent",
    "name": "Vector Analysis Agent",
    "version": "1.0.0",
    "model": "gpt-5.2"
  },
  "outputs": {
    "summary": "Joined county boundaries to obesity rates using county FIPS codes and saved the result as a GeoPackage.",
    "artifacts": [
      {
        "name": "county_obesity_join.gpkg",
        "type": "vector",
        "format": "GPKG",
        "mime_type": "application/geopackage+sqlite3",
        "size_bytes": 1843920,
        "url": "https://your-gas-server.example.com/agents/vector_analysis_agent/data/county_obesity_join.gpkg",
        "spatial_metadata": {
          "type": "vector",
          "crs": "EPSG:4326",
          "bbox": [-124.8, 24.5, -66.9, 49.4],
          "geometry_type": "MultiPolygon",
          "feature_count": 3108,
          "dimensions": null,
          "schema": {
            "GEOID": "str",
            "NAME": "str",
            "obesity_rate": "float"
          },
          "raster": null
        },
        "validation": {
          "status": "passed",
          "checks": [
            {
              "name": "artifact_readable",
              "status": "passed",
              "message": "The GeoPackage was opened successfully."
            }
          ]
        }
      }
    ],
    "data_summary": {
      "artifact_count": 1,
      "artifact_types": ["vector"],
      "formats": ["GPKG"],
      "crs": ["EPSG:4326"],
      "has_vector": true,
      "has_raster": false,
      "has_table": false,
      "feature_count_total": 3108
    }
  },
  "execution": {
    "status": "successful",
    "duration_seconds": 12.4,
    "iterations": 1,
    "inputs": {
      "dataset_paths": ["counties.gpkg", "obesity.csv"],
      "parameters": {}
    },
    "code": {
      "available": true,
      "language": "python",
      "script": "import geopandas as gpd\n..."
    },
    "runtime": {
      "python_version": "3.12",
      "domain-specific_libraries": ["geopandas", "pandas", "shapely"]
    }
  },
  "provenance": {
    "llm_calls": 0,
    "tool_calls": 1,
    "artifacts_created": 1,
    "token_usage": {
      "input_tokens": null,
      "output_tokens": null,
      "total_tokens": null
    },
    "lineage": []
  },
  "reproducibility": {
    "code_available": true,
    "environment_available": true,
    "parameters_available": true,
    "input_artifacts": [
      {
        "name": "counties.gpkg",
        "role": "input_dataset"
      },
      {
        "name": "obesity.csv",
        "role": "input_dataset"
      }
    ],
    "output_artifacts": [
      {
        "name": "county_obesity_join.gpkg",
        "role": "primary_output",
        "format": "GPKG"
      }
    ],
    "parameters": {},
    "stochasticity": {
      "used": false,
      "controls": []
    },
    "notes": []
  },
  "diagnostics": {
    "has_error": false,
    "error": null,
    "warnings": [],
    "validation": {},
    "assumptions": ["County FIPS fields are comparable after zero-padding."],
    "limitations": []
  }
}
```

Example failed task response:

```json
{
  "response": {
    "type": "task_result",
    "schema_version": "1.0.0",
    "created_at": "2026-05-19T15:10:00Z",
    "completed_at": "2026-05-19T15:10:03Z"
  },
  "task": {
    "id": "3a786094-58c7-4f96-bb30-e86f927cd871",
    "status": "failed",
    "terminal": true,
    "user_request": "Join these datasets.",
    "requested_skill": null,
    "artifact_delivery": "URL"
  },
  "agent": {
    "id": "vector_analysis_agent",
    "name": "Vector Analysis Agent",
    "version": "1.0.0",
    "model": "gpt-5.2"
  },
  "outputs": {
    "summary": "",
    "artifacts": [],
    "data_summary": {
      "artifact_count": 0,
      "artifact_types": [],
      "formats": [],
      "has_vector": false,
      "has_raster": false,
      "has_table": false
    }
  },
  "execution": {
    "status": "failed",
    "duration_seconds": 3.1,
    "iterations": 0,
    "inputs": {
      "dataset_paths": [],
      "parameters": {}
    },
    "code": {
      "available": false,
      "language": null,
      "script": null
    },
    "runtime": {
      "python_version": "3.12",
      "domain-specific_libraries": []
    }
  },
  "provenance": {
    "llm_calls": 0,
    "tool_calls": 0,
    "artifacts_created": 0,
    "token_usage": {
      "input_tokens": null,
      "output_tokens": null,
      "total_tokens": null
    },
    "lineage": []
  },
  "reproducibility": {
    "code_available": false,
    "environment_available": true,
    "parameters_available": false,
    "input_artifacts": [],
    "output_artifacts": [],
    "parameters": {},
    "stochasticity": {
      "used": "unknown",
      "controls": []
    },
    "notes": []
  },
  "diagnostics": {
    "has_error": true,
    "error": "No input datasets were provided.",
    "warnings": [],
    "validation": {},
    "assumptions": [],
    "limitations": ["The task could not run without input data."]
  }
}
```

## Artifact Metadata

Artifacts are the concrete outputs produced by an agent, such as GeoPackage,
GeoJSON, GeoTIFF, CSV, HTML, TXT, PNG, or other files. Artifact records in the
standard task response should include enough information for another program to
inspect, download, display, or pass the artifact into another GAS service.

Common artifact fields include:

- `name`: artifact file name.
- `type`: high-level artifact category, such as vector, raster, table, report,
  map, chart, or downloadable file.
- `format`: file format, such as GPKG, GeoJSON, GeoTIFF, CSV, HTML, TXT, or
  PNG.
- `mime_type`: media type when known.
- `size_bytes`: artifact size.
- `url`: downloadable artifact URL when `artifact_delivery` is `URL`.
- `encoding` and `data`: encoded content when `artifact_delivery` is
  `Encoded`.
- `spatial_metadata`: CRS, bounding box, geometry type, feature count, raster
  dimensions, schema, and raster metadata when applicable.
- `validation`: structured checks that describe whether the artifact was
  created, readable, spatially inspectable, or otherwise valid for the task.

Artifact metadata and validation are inspection-oriented. They help clients and
orchestrators reason about returned outputs without having to open every file
manually.

Example vector artifact:

```json
{
  "name": "analysis_result.gpkg",
  "type": "vector",
  "format": "GPKG",
  "mime_type": "application/geopackage+sqlite3",
  "size_bytes": 1843920,
  "url": "https://your-gas-server.example.com/agents/vector_analysis_agent/data/analysis_result.gpkg",
  "spatial_metadata": {
    "type": "vector",
    "crs": "EPSG:4326",
    "bbox": [-80.5, 39.7, -74.7, 42.3],
    "geometry_type": "Polygon",
    "feature_count": 67,
    "dimensions": null,
    "schema": {
      "GEOID": "str",
      "NAME": "str"
    },
    "raster": null
  },
  "validation": {
    "status": "passed",
    "checks": [
      {
        "name": "artifact_readable",
        "status": "passed",
        "message": "The artifact can be opened by GeoPandas."
      }
    ]
  }
}
```

Example raster artifact:

```json
{
  "name": "elevation_clip.tif",
  "type": "raster",
  "format": "GeoTIFF",
  "mime_type": "image/tiff",
  "size_bytes": 923100,
  "url": "https://your-gas-server.example.com/agents/raster_agent/data/elevation_clip.tif",
  "spatial_metadata": {
    "type": "raster",
    "crs": "EPSG:3857",
    "bbox": [-8580000.0, 4930000.0, -8520000.0, 4990000.0],
    "geometry_type": null,
    "feature_count": null,
    "dimensions": [1024, 1024],
    "schema": null,
    "raster": {
      "width": 1024,
      "height": 1024,
      "count": 1,
      "dtype": "float32",
      "nodata": -9999
    }
  },
  "validation": {
    "status": "passed",
    "checks": [
      {
        "name": "artifact_readable",
        "status": "passed",
        "message": "The artifact can be opened by Rasterio."
      }
    ]
  }
}
```

Example report artifact:

```json
{
  "name": "spatial_statistics_report.html",
  "type": "report",
  "format": "HTML",
  "mime_type": "text/html",
  "size_bytes": 48120,
  "url": "https://your-gas-server.example.com/agents/spatial_statistics_agent/data/spatial_statistics_report.html",
  "spatial_metadata": {
    "type": null,
    "crs": null,
    "bbox": null,
    "geometry_type": null,
    "feature_count": null,
    "dimensions": null,
    "schema": null,
    "raster": null
  },
  "validation": {
    "status": "passed",
    "checks": [
      {
        "name": "artifact_created",
        "status": "passed",
        "message": "The HTML report was created."
      }
    ]
  }
}
```

## Developer Guidance

Agent developers do not need to manually assemble every response section or
check every schema while developing a new agent. The GAS server handles
much of the common interface work, including request parsing, input dataset
materialization, task status handling, streaming lifecycle events, artifact
delivery, artifact inspection, response normalization, and schema validation in
the test suite.

For most new agents, the developer's interface responsibilities are focused and
practical:

- Write the agent implementation and return a normal `GeoAgent` result.
- Create a `DescribeAgent` JSON document that honestly describes what the
  agent does.
- Document required inputs, optional parameters, supported credentials, and
  primary output artifacts.
- Put agent-specific metadata in `extensions`.
- Run the test suite so the framework can validate the public JSON interfaces.

The shared schemas are mainly for conformance checking and interoperability.
Developers usually only need to edit a schema when the GAS interface itself is
intentionally changing, not when adding an ordinary new agent.

The goal is that a client, notebook, browser application, or AI orchestrator can
use the JSON interfaces as the source of truth for discovering and invoking GAS
services, while agent developers can focus on the geospatial capability.

Example practical checklist for a new agent:

```json
{
  "new_agent_interface_checklist": {
    "agent_class_created": "gas_server/agents/my_new_agent.py",
    "service_wrapper_created": "gas_server/services/my_new_agent_service.py",
    "capability_document_created": "gas_server/capabilities/my_new_agent.json",
    "agent_id_matches_route_and_file": true,
    "execute_task_inputs_documented": true,
    "execute_task_outputs_documented": true,
    "credentials_documented": true,
    "extensions_used_for_agent_specific_metadata": true,
    "agent_returns_geoagent_result": true,
    "tests_pass": true
  }
}
```
