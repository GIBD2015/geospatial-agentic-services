# GAS Registry

The GAS Registry is a lightweight catalog application for discovering published
Geospatial Agentic Services across one or more GAS servers. It supports the GAS
goal of geospatial intelligence interoperability by making agent capability
documents easier for people, applications, and AI orchestrators to find,
inspect, compare, and reuse.

The public registry page is:

[http://geospatial-agentic-services.online/registry](http://geospatial-agentic-services.online/registry)

## Role In GAS

GAS servers publish interoperable geospatial agents through `GetCapabilities`
and `DescribeAgent` interfaces. The registry consumes those interfaces and
stores the advertised agent descriptions in a searchable catalog.

The GAS Registry is not intended to be a single central registry. Like GAS
servers and GAS clients, registries can be created, deployed, and hosted by
different organizations or communities. A registry can index one public GAS
server, many distributed GAS servers, or a focused set of services for a
specific project, domain, course, or organization. This distributed registry
model supports geospatial intelligence interoperability without requiring all
services to be listed in one central catalog.

## What The Registry Stores

When a GAS server is registered, the registry reads:

```text
{gas_server_base_url}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities
```

For each advertised agent, it then reads:

```text
{gas_server_base_url}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={agent_id}
```

The registry stores the full `DescribeAgent` JSON plus selected queryable
fields in a local SQLite database. Stored fields include agent identity,
provider, source GAS server, default model, keywords, skill summaries,
operation summaries, provenance support, reproducibility support, validation
support, governance, conformance, and extension notes.

The same `agent_id` can appear on different GAS servers. The registry keeps
those records separate by using a server-qualified registry identifier.

## User Interface

The registry web page supports:

- card and list views
- search by name, keyword, skill, or full text
- pagination
- registration of all agents from a GAS server
- listing remote agents before registration
- selected-agent registration
- detailed agent popups
- direct links to each source server's original `DescribeAgent` document

The detailed popup is designed for interoperability review. It shows provider
metadata, GAS server source, agent profile, inputs, outputs, skills,
ExecuteTask information, credentials, provenance and reproducibility support,
governance, conformance, and implementation-specific extensions.

## Run Locally

Start the main GAS server and the registry as separate processes when testing
locally. The GAS server uses port `4042` by default, while the registry uses
port `4043` by default.

```powershell
python -m gas_server.entrypoints.gas_server
```

In another terminal:

```powershell
python -m gas_registry.app
```

Open:

```text
http://127.0.0.1:4043/registry
```

The registry API is available under:

```text
http://127.0.0.1:4043/registry/api/gas
```

## Register A GAS Server

From the registry UI, enter the GAS server base URL, such as:

```text
http://127.0.0.1:4042
```

or:

```text
https://your-public-gas-server.example
```

Click **Register** to register every agent published by that server. If the
server has already been registered, the latest capability information updates
the existing records for that server. Click **List Agents** when you want to
preview the advertised agents and register only selected services.

## Deployment Notes

The registry is a normal Flask application. In production, run it with a WSGI
server such as Waitress or Gunicorn and place it behind the same reverse proxy
used for the GAS server, or behind a separate virtual host.

This repository includes an empty starter SQLite database at
`gas_registry/gas_registry.db`, so a downloaded copy can run the registry with
minimal setup. When a host registers GAS servers, that database is updated with
the fetched capability information. For a larger public registry, the same
storage model can be moved to a managed database while keeping the public
registry interface unchanged.

Because the registry fetches remote GAS capability documents, the deployment
environment needs outbound network access to registered GAS servers. Treat
registered descriptions as public metadata, and do not store real API keys in
capability JSON documents.
