<img align="left" src="assets/gas-logo.png" alt="Geospatial Agentic Services logo" width="125">

# GAS - Geospatial Agentic Services

<br clear="left">

Geospatial Agentic Services (GAS) is an interoperability framework for
discovering, describing, invoking, composing, validating, and reusing
geospatial agents and services.

GAS focuses on geospatial interoperability in the era of autonomous GIS and
geospatial AI agents. It provides a way for heterogeneous geospatial agents to
publish machine-readable capability documents, expose standard task operations,
return reusable artifacts, and participate in larger distributed workflows.

GAS does not prescribe how geospatial agents should be designed, how they
should reason, how their performance should be improved, or how general
agentic systems should be built. Instead, it focuses on the shared service
contracts needed when different geospatial agents, applications, notebooks, and
AI orchestrators need to work together.

This repository provides a reference GAS server, a Python client SDK, a GAS
Registry web app, example notebooks, interface schemas, developer
documentation, and working reference agent implementations. The public
[GAS Registry](http://geospatial-agentic-services.online/registry) catalogs
published GAS services by reading their `GetCapabilities` and `DescribeAgent`
documents.

For the broader conceptual framework, see the
[GAS paper](https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence).

[View the GitHub source repository](https://github.com/GIBD2015/geospatial-agentic-services).

This documentation site is organized around four entry points and a compact
set of core references.

## Getting Started

### Use GAS Services

Start here if you want to discover and call existing GAS services from
notebooks, applications, GIS workflows, or AI orchestrators.

- [Use GAS Services](gas_client_sdk.md)
- [GAS Interfaces](gas_interfaces.md)
- [GAS Registry](gas_registry.md)
- [Included Agents](included_agents.md)
- [Notebook Examples](examples.md)

### Add an Agent Service

Start here if you want to publish a new geospatial capability into the GAS
ecosystem.

- [Add an Agent Service](adding_an_agent_service.md)
- [Server Architecture](gas_server_architecture.md)
- [GAS Interfaces](gas_interfaces.md)
- [Included Agents](included_agents.md)

### Host a GAS Server

Start here if you want to operate a public or private GAS server.

- [Host a GAS Server](development_and_deployment_environment.md)
- [Server Architecture](gas_server_architecture.md)
- [GAS Registry](gas_registry.md)
- [Security](security.md)

### Improve the Codebase

Start here if you want to contribute to the GAS server framework, registry,
client SDK, schemas, examples, tests, or documentation.

- [Contributing](contributing.md)
- [Server Architecture](gas_server_architecture.md)
- [Use GAS Services](gas_client_sdk.md)
- [GAS Registry](gas_registry.md)

## Core Documentation

- [GAS Interfaces](gas_interfaces.md) explains the discovery, description,
  task request, task response, and artifact metadata contracts.
- [Server Architecture](gas_server_architecture.md) explains the server
  framework, plugin-style service structure, request flow, credentials, and
  artifacts.
- [GAS Registry](gas_registry.md) explains the registry web app and API.
- [Included Agents](included_agents.md) catalogs the reference agents and the
  implementation patterns they demonstrate.

## Project Links

- [GAS GitHub repository](https://github.com/GIBD2015/geospatial-agentic-services)
- [GAS Registry](http://geospatial-agentic-services.online/registry)
- [GAS paper](https://www.researchgate.net/publication/404738967_Geospatial_Agentic_Services_A_Framework_for_Interoperable_Geospatial_Intelligence)
- [GAS Client on PyPI](https://pypi.org/project/gas-client/)

## Examples

- [Notebook Examples](examples.md)
