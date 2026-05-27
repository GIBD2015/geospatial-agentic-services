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
- selected-agent deletion from the local registry database
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
http://127.0.0.1:4043/registry/api
```

When the Flask app is mounted at the site root during local development, the
same API is also available at:

```text
http://127.0.0.1:4043/api
```

API paths accept an optional trailing slash. For example,
`/registry/api/agents` and `/registry/api/agents/` are equivalent.

## API Access For Developers And AI Agents

The registry exposes JSON endpoints so notebooks, applications, workflow
engines, and AI agents can discover registered GAS services without scraping
the web page. This API is intentionally a registry API, not a GAS server API:
it uses straightforward resource paths for listing, searching, registering,
and maintaining published GAS service metadata. The registry still reads GAS
`GetCapabilities` and `DescribeAgent` documents from source servers, but
clients access the registry through the endpoints below.

Public read endpoints use `GET` and return JSON with:

```json
{
  "status": "success"
}
```

when the request succeeds. Error responses use:

```json
{
  "status": "error",
  "error": "Error message"
}
```

Write endpoints use `POST` or `DELETE` because they modify the registry
database.

### API Root

List public registry API endpoints:

```text
GET /registry/api
```

Example response shape:

```json
{
  "status": "success",
  "name": "GAS Registry API",
  "version": "1.0.0",
  "endpoints": {
    "agents": "/registry/api/agents",
    "agent_detail": "/registry/api/agents/{registry_id}",
    "delete_agent": "/registry/api/agents/{registry_id}",
    "delete_agents": "/registry/api/agents/delete",
    "agent_search": "/registry/api/agents/search",
    "servers": "/registry/api/servers",
    "remote_agents": "/registry/api/remote-agents",
    "register_server": "/registry/api/servers",
    "register_selected_agents": "/registry/api/servers/selected-agents"
  }
}
```

### Get Registered Agents

List all registered agents:

```text
GET /registry/api/agents
```

List agents from one registered source GAS server:

```text
GET /registry/api/agents?server=https%3A%2F%2Fwww.geospatial-agentic-services.online
```

Example response shape:

```json
{
  "status": "success",
  "count": 1,
  "agents": [
    {
      "registry_id": "mapping_agent@www.geospatial-agentic-services.online",
      "agent_id": "mapping_agent",
      "description": "Creates cartographic map outputs from geospatial datasets.",
      "source_base_url": "https://www.geospatial-agentic-services.online",
      "detailUrl": "/registry/api/agents/mapping_agent%40www.geospatial-agentic-services.online",
      "describeUrl": "https://www.geospatial-agentic-services.online/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=mapping_agent"
    }
  ]
}
```

`detailUrl` points to the registry's stored copy of the agent description.
`describeUrl` points back to the source GAS server's live `DescribeAgent`
document.

The `server` query parameter filters by the source GAS server base URL. Use
the exact `source_base_url` returned by `/registry/api/servers` or
`/registry/api/agents`.

### Get One Registered Agent

Retrieve the stored full capability document for one registered agent:

```text
GET /registry/api/agents/{registry_id}
```

Use the server-qualified `registry_id` returned by `/registry/api/agents` when
the same `agent_id` may appear on multiple source servers.

This endpoint returns the full stored `DescribeAgent` JSON document directly,
not a shortened list item. A missing agent returns `404` with
`status: "error"`.

### Search Registered Agents

Search the local registry database:

```text
GET /registry/api/agents/search?q=map
GET /registry/api/agents/search?q=raster&field=skills
GET /registry/api/agents/search?provenance_supported=1
```

Supported `field` values are:

- `name`
- `keywords`
- `skills`

Without `field`, the search scans the stored agent JSON text.

Example response shape:

```json
{
  "status": "success",
  "query": "raster",
  "count": 1,
  "agents": [
    {
      "registry_id": "raster_agent@www.geospatial-agentic-services.online",
      "agent_id": "raster_agent",
      "source_base_url": "https://www.geospatial-agentic-services.online",
      "description": "Inspects raster and related geospatial datasets.",
      "detailUrl": "/registry/api/agents/raster_agent%40www.geospatial-agentic-services.online",
      "describeUrl": "https://www.geospatial-agentic-services.online/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=raster_agent"
    }
  ]
}
```

### List Registered Servers

List source GAS servers already represented in this registry:

```text
GET /registry/api/servers
```

Example response shape:

```json
{
  "status": "success",
  "count": 1,
  "servers": [
    {
      "source_base_url": "https://www.geospatial-agentic-services.online",
      "agent_count": 10,
      "last_fetched_at": "2026-05-22T18:40:12+00:00"
    }
  ]
}
```

### Preview Remote Server Agents

Preview all agents advertised by a remote GAS server before registering them:

```text
GET /registry/api/remote-agents?url=https%3A%2F%2Fyour-gas-server.example
```

Example success response:

```json
{
  "status": "success",
  "count": 2,
  "agents": [
    {
      "name": "mapping_agent",
      "describeUrl": "https://your-gas-server.example/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=mapping_agent",
      "sourceBaseUrl": "https://your-gas-server.example",
      "displayName": "Mapping Agent",
      "description": "Creates cartographic map outputs from geospatial datasets.",
      "version": "1.0.0"
    }
  ]
}
```

This endpoint does not modify the registry database and does not require an
admin token. It fetches the remote server's `GetCapabilities` document and, when
available, each advertised agent's `DescribeAgent` document.

### Register GAS Servers

These write endpoints update the registry database. Token protection is
required by default. Copy `gas_registry/.env.example` to
`gas_registry/.env`, set `GAS_REGISTRY_ADMIN_TOKEN` in the local/server-only
`.env` file, and include that token with each request as either:

```http
Authorization: Bearer <token>
```

or:

```http
X-Registry-Admin-Token: <token>
```

If `GAS_REGISTRY_REQUIRE_ADMIN_TOKEN=true` and `GAS_REGISTRY_ADMIN_TOKEN` is
missing or blank, write endpoints fail closed with a configuration error. This
protects production deployments from accidentally publishing an unprotected
registry. Set `GAS_REGISTRY_REQUIRE_ADMIN_TOKEN=false` only for trusted local
development when open write APIs are intentional.

Example local `gas_registry/.env`:

```text
GAS_REGISTRY_REQUIRE_ADMIN_TOKEN=true
GAS_REGISTRY_ADMIN_TOKEN=replace-with-a-long-random-token
```

The committed `gas_registry/.env.example` intentionally contains no token.
The real `gas_registry/.env` runtime file is ignored by Git and should not be
pushed to GitHub.

Register all agents from a GAS server:

```http
POST /registry/api/servers
Content-Type: application/json
Authorization: Bearer <token>

{"url": "https://your-gas-server.example"}
```

Example success response:

```json
{
  "status": "success",
  "registered": ["mapping_agent", "raster_agent"],
  "count": 2
}
```

Register selected agents:

```http
POST /registry/api/servers/selected-agents
Content-Type: application/json
Authorization: Bearer <token>

{
  "url": "https://your-gas-server.example",
  "names": ["mapping_agent", "raster_agent"]
}
```

### Delete Registered Agents

Delete endpoints remove records from the local registry database only. They do
not call, modify, or delete anything on the source GAS server.

Use the server-qualified `registry_id` returned by `/registry/api/agents`.
This matters when the same `agent_id` is registered from more than one source
GAS server.

Delete one registered agent:

```http
DELETE /registry/api/agents/{registry_id}
Authorization: Bearer <token>
```

Delete multiple registered agents:

```http
POST /registry/api/agents/delete
Content-Type: application/json
Authorization: Bearer <token>

{
  "registry_ids": [
    "mapping_agent@www.geospatial-agentic-services.online",
    "raster_agent@www.geospatial-agentic-services.online"
  ]
}
```

Example success response:

```json
{
  "status": "success",
  "requested": [
    "mapping_agent@www.geospatial-agentic-services.online",
    "missing_agent@example"
  ],
  "deleted": [
    "mapping_agent@www.geospatial-agentic-services.online"
  ],
  "missing": [
    "missing_agent@example"
  ],
  "count": 1
}
```

The batch endpoint reports missing registry IDs but still deletes the records
that exist. The single-agent `DELETE` endpoint returns `404` when the requested
record is not present.

Example token errors:

Missing token in a request:

```json
{
  "status": "error",
  "error": "Registry admin token is required for this operation."
}
```

Wrong token in a request:

```json
{
  "status": "error",
  "error": "Invalid registry admin token."
}
```

Token required by configuration but not set on the server:

```json
{
  "status": "error",
  "error": "Registry admin token is required by configuration, but GAS_REGISTRY_ADMIN_TOKEN is not set."
}
```

The legacy routes used internally by the web UI keep their UI-oriented
`ok: true` / `ok: false` response shape for browser compatibility. Public
developer and AI-agent integrations should use the `/registry/api/...`
endpoints documented above.

Legacy UI routes include:

- `/registry/api/gas`
- `/registry/api/gas/search`
- `/registry/api/gas/list-remote`
- `/registry/api/gas/register`
- `/registry/api/gas/register-selected`
- `/registry/api/gas/delete-selected`

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

To remove records from the registry UI, select the checkbox on one or more
agent cards, click **Delete Selected**, enter the registry admin token, and
confirm deletion. This removes only the selected local registry records. It
does not remove the agent from its source GAS server.

If the deployment sets `GAS_REGISTRY_ADMIN_TOKEN`, the registration modal's
optional **Admin Token** field must contain that token before registration
requests can update the registry database. The token is provided by the
registry administrator and is not generated or stored by the web UI. The token
is sent only with write requests such as registration and deletion, not with
public read/search/list requests.

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
