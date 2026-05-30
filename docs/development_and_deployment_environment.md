# GAS Server Deployment and Development Environment

This document explains how to set up the GAS server for local development,
temporary public demos, and production-style hosting. It also gives practical
resource-planning guidance for developers who want to build and host their own
GAS agent services.

The GAS server can run on a normal development machine, laptop, workstation, or
cloud server. The exact requirements depend on the agents being hosted. A
simple deterministic or light model-backed agent can run on a typical web
server. Agents that process large rasters, large vector layers, spatial
statistics, or long-running downloads need more CPU, memory, disk, and runtime
tuning.

## Local Development Environment

Recommended local tools:

- Python 3.10 or newer.
- VS Code or another Python IDE.
- Git.
- A Python virtual environment.
- Jupyter, if you want to run the example notebooks.
- A geospatial Python stack that can install and import packages such as
  GeoPandas, Rasterio, Shapely, PyProj, and PySAL.

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the server package and test dependencies:

```powershell
python -m pip install -e .[test]
```

If your environment has trouble installing geospatial libraries from PyPI,
consider using a conda/mamba environment for the geospatial stack, then install
this repository in editable mode inside that environment.

## VS Code Workflow

A typical VS Code workflow is:

1. Open the repository folder.
2. Select the Python interpreter from `.venv` or your conda environment.
3. Open the integrated terminal.
4. Start the GAS server from the repository root.
5. Run tests from the same environment.
6. Open the
   [example notebooks](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/examples_for_using_gas_services)
   and select the same Python environment.

Useful commands:

```powershell
python -m gas_server.entrypoints.gas_server
python -m gas_registry.app
python -m pytest --basetemp .tmp_pytest_run\local -p no:cacheprovider
```

Using `--basetemp` keeps pytest temporary files inside the workspace, which can
avoid Windows temp-folder permission problems.

## Run The GAS Server Locally

Start the local development server:

```powershell
python -m gas_server.entrypoints.gas_server
```

By default, this implementation binds to:

```text
http://127.0.0.1:4042
```

Port `4042` is only the local development default. A GAS server host can choose
another host, port, and public base URL. The default host and port are defined
in:

```text
gas_server/core/config.py
```

The Flask development server is useful for local development, testing,
notebooks, and demos. It should not be treated as the production web server.

## Run The GAS Registry Locally

The repository also includes the GAS Registry, a catalog web app for indexing
published GAS services from one or more GAS servers. The public registry is:

```text
http://geospatial-agentic-services.online/registry
```

For local testing, run the registry as a separate process from the GAS server.
The registry uses port `4043` by default so it does not conflict with the GAS
server's default port `4042`.

```powershell
python -m gas_registry.app
```

Then open:

```text
http://127.0.0.1:4043/registry
```

The registry stores fetched agent metadata in a local SQLite database under
`gas_registry/`. This database is runtime state and is ignored by Git. See
[gas_registry.md](gas_registry.md) for registration workflow and API examples.

## Get Capabilities Test

After starting the server, test discovery in a browser or Python client:

```text
http://127.0.0.1:4042/?SERVICE=GAS&VERSION=1.0.0&REQUEST=GetCapabilities
```

With the SDK:

```python
from gas_client import GasClient

client = GasClient("http://127.0.0.1:4042")
print(client.list_agents())
```

Also test one `DescribeAgent` request:

```text
http://127.0.0.1:4042/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id=geospatial_data_retrieval_agent
```

## Expose a Local GAS Server with ngrok to the Public Internet

[ngrok](https://ngrok.com/) can expose a GAS server running on a local computer to the public internet. This is useful for demos, workshops, remote notebooks, browser clients, or AI orchestrators that need to call a GAS server temporarily hosted from a laptop.

ngrok is recommended for development and demos, not as a substitute for
production hosting.

### 1. Start The GAS Server Locally

```powershell
python -m gas_server.entrypoints.gas_server
```

The local server normally listens on:

```text
http://127.0.0.1:4042
```

### 2. Start ngrok

Install ngrok from:

```text
https://ngrok.com/download
```

Then expose the local GAS port:

```powershell
ngrok http 4042
```

ngrok will show a public forwarding URL, for example:

```text
https://abc123.ngrok-free.app
```

### 3. Use The ngrok URL As The GAS Base URL

In a remote notebook or client:

```python
from gas_client import GasClient

client = GasClient("https://abc123.ngrok-free.app")

print(client.list_agents())
```

When clients call the server through the ngrok URL, `GetCapabilities`,
`DescribeAgent`, and artifact URLs are generated from the public request host,
so the returned links should also use the ngrok host.

### 4. Security Notes For ngrok

- Treat the ngrok URL as public.
- Do not expose private datasets or sensitive internal services casually.
- Do not put API keys in URLs, notebooks, capability documents, or source code.
- Stop ngrok when the demo is finished.
- Use a reserved ngrok domain if you need a stable temporary URL.
- Use a production deployment for stable public hosting.

## Production Hosting

For production, run the Flask app with a production WSGI server and place it
behind a reverse proxy for HTTPS, logging, compression, and request-size
control.

On Windows, Waitress is a practical option:

```powershell
waitress-serve --host=0.0.0.0 --port=4042 gas_server.entrypoints.gas_server:app
```

On Linux, Gunicorn is a common option:

```bash
gunicorn --bind 0.0.0.0:4042 --workers 2 --threads 4 gas_server.entrypoints.gas_server:app
```

The port in these examples can be changed. In many deployments, a reverse proxy
such as Nginx, Caddy, or Apache listens on ports `80` and `443`, then forwards
requests to the internal WSGI server port.

## Cloud Hosting Options

GAS can be hosted on many cloud platforms because it is a normal Python web
application. The best choice depends on whether the deployment is a short demo,
a moderate public service, or a heavy geoprocessing server.

Common options:

- **PythonAnywhere**: convenient for prototypes, teaching, and lightweight
  demos. A paid plan is usually needed for always-on web apps and outbound
  internet access. It is easiest when the hosted agents have modest dependency
  and compute requirements.
- **Render or Railway**: practical for moderate production-style deployments.
  These platforms are friendly to Git-based deployment and environment
  variables. Use them when the agents are mostly web/API, data retrieval,
  lightweight vector processing, or model-backed workflows.
- **Google Cloud Run, AWS Fargate, or similar container services**: good when
  you want containerized deployment, autoscaling, and managed infrastructure.
  They work well with Docker images, but cold starts can affect the first
  request after an idle period. For streaming tasks, long-running tasks, and
  large geospatial jobs, configure request timeouts, CPU allocation, memory,
  and concurrency carefully.
- **Dedicated VPS providers such as DigitalOcean, Hetzner, AWS EC2, Google
  Compute Engine, or Azure Virtual Machines**: often the best fit for heavier
  geoprocessing, large raster processing, persistent disk needs, custom GDAL
  builds, or long-running agents. A VPS gives more control over system
  packages, disk layout, background workers, reverse proxies, and monitoring.

Important geospatial dependency note: packages such as GeoPandas, Rasterio,
Fiona, PyProj, Shapely, and PySAL may depend on native geospatial libraries
such as GDAL, GEOS, and PROJ. Some cloud platforms make these dependencies easy;
others require custom build steps. For robust deployment, prefer one of these
approaches:

- Use a Docker image that installs the needed system packages and Python
  dependencies together.
- Use a platform that explicitly supports native system dependencies.
- Use conda/mamba on a VPS when that is simpler than compiling geospatial
  libraries from source.
- Test imports for `geopandas`, `rasterio`, `pyproj`, and any agent-specific
  libraries during deployment, not only after the server starts.

For public deployments, store model-provider keys, data-source keys, and other
deployment secrets in the cloud provider's secret/environment-variable system.
Do not bake credentials into notebooks, Docker images, capability JSON files,
or source code.

Production checklist:

- Choose the public domain and HTTPS endpoint.
- Configure firewall or cloud security-group rules.
- Run the GAS app with a WSGI server, not Flask's development server.
- Put Nginx, Caddy, Apache, or another reverse proxy in front of the WSGI
  server when appropriate.
- Store deployment settings and credentials in environment variables or a
  secret manager.
- Keep internal streaming debug history disabled in public deployments. It is
  disabled by default; set `GAS_ENABLE_STREAM_DEBUG=true` only when actively
  diagnosing stream behavior in a trusted environment.
- Test `GetCapabilities`, `DescribeAgent`, one sync task, one streaming task,
  and one artifact download through the public URL.
- Monitor logs, disk usage, failed tasks, and long-running requests.

## Minimum Server Requirements

For the built-in agents and typical demo workloads, a modest web server or
workstation is usually enough:

| Resource | Practical Minimum | Recommended For Comfortable Demos |
|---|---:|---:|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8-16 GB |
| Disk | 20 GB free | 50+ GB free |
| Network | Outbound internet | Stable outbound internet |
| Python | 3.10+ | 3.12+ where dependencies support it |

These numbers are only baseline guidance. They are not universal GAS
requirements.

## Agent-Specific Resource Planning

Resource needs depend heavily on the hosted agents:

- Data retrieval agents need outbound network access and enough temporary disk
  space for downloaded files.
- Vector analysis agents need memory proportional to feature count and geometry
  complexity.
- Raster agents may need much more disk, RAM, and CPU for large GeoTIFFs,
  mosaics, reprojection, clipping, and raster calculations.
- Spatial statistics agents may need more memory and CPU for large spatial
  weights matrices or model fitting.
- Web mapping app agents can create large HTML artifacts when many features are
  embedded directly in the browser app.
- LLM-backed agents need network access to the selected model provider unless
  they use a local model.
- Deterministic agents without LLM calls can often run with lower network and
  credential requirements.

When developing a new agent, document any special resource expectations in the
agent's `DescribeAgent` JSON, especially if it is likely to process large
rasters, high-volume vector layers, or long-running external downloads.

## Operational Tips

- Keep generated files in `Data/` and `Output/`; they are ignored by git.
- Periodically clean old artifacts if the server creates many outputs.
- Set request-size limits at the reverse proxy if accepting encoded input
  datasets.
- Tune WSGI worker and thread counts based on the agent workload.
- For very long tasks, heavy parallel usage, or production-grade scheduling,
  consider adding an external task queue in a future deployment architecture.
- Keep `DescribeAgent` documents accurate so clients know what each deployed
  service requires and returns.
