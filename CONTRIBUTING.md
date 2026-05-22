# Contributing

Thanks for helping improve the GAS server and client, and contributing to the new geospatial agentic services development.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[test]
```

Some agents depend on geospatial libraries such as GeoPandas, Rasterio, PySAL,
and their native dependencies. Use an environment that can install and import
those packages before running the full test suite.

## Add A New Agent

New agents should follow the plugin-style structure documented in
`docs/adding_an_agent_service.md`:

```text
gas_server/agents/my_new_agent.py
gas_server/services/my_new_agent_service.py
gas_server/capabilities/my_new_agent.json
```

Agent classes should inherit from `gas_server.core.geo_agent.GeoAgent` and
implement `run()`. Do not implement agent-specific HTTP routes or service
startup code in the agent file; the GAS framework provides the common
operations.

## Tests

Run the full suite before opening a pull request:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .tmp_pytest_run\local -p no:cacheprovider
```

Use a workspace-local `--basetemp` on Windows if your system temp folder has
permission restrictions.

## Secrets And Generated Files

Do not commit API keys, `.env` files, downloaded datasets, generated outputs,
build artifacts, or notebook outputs. Use `.env.example` as the template for
local configuration.

If a secret is accidentally committed or shared, remove it from the repository
and rotate the affected credential immediately.
