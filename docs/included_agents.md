# Included GAS Agents

This GAS server includes working geospatial agents that also serve as concrete
implementation examples for developers. They show different ways to build GAS
services: deterministic geospatial workflows, model-assisted code execution,
data retrieval, data inspection, workflow planning, mapping, raster processing,
vector analysis, and spatial statistics.

When adding a new agent, start by finding the included agent that is closest to
your intended design. Then inspect its implementation file, service wrapper,
and capability document.

## Quick Comparison

| Agent ID | Agent Name | Main Purpose | Implementation Pattern | Inputs | Primary Outputs |
|---|---|---|---|---|---|
| `geospatial_data_retrieval_agent` | Geospatial Data Retrieval Agent | Retrieves geospatial datasets from supported external sources. | Model-assisted source selection and code generation using data-source handbooks. | Optional input datasets. | GeoPackage, GeoJSON, GeoTIFF, Shapefile, CSV, or source-specific files. |
| `pasda_agent` | PASDA Discovery Agent | Finds and downloads datasets from PASDA. | Repository-specific discovery workflow with model-assisted search and packaging. | Optional input datasets. | GeoPackage, GeoJSON, or source-specific PASDA files. |
| `geospatial_data_inspection_agent` | Geospatial Data Inspection Agent | Checks vector, raster, and tabular datasets for quality and workflow readiness. | Deterministic inspection plus optional LLM-assisted interpretation. | Required input datasets. | TXT and HTML inspection reports. |
| `geospatial_workflow_planning_agent` | Geospatial Workflow Planning Agent | Discovers GAS capabilities and plans client-side service chains. | Capability-aware LLM planning with JSON, Markdown, code, notebook, and graph artifacts. | Optional input datasets and optional GAS GetCapabilities URLs. | Workflow plan JSON, Markdown, optional Python, notebook, and HTML graph. |
| `vector_analysis_agent` | Vector Analysis Agent | Performs vector joins, buffers, clips, intersections, filtering, and aggregation. | Deterministic fast paths for common operations plus model-backed fallback. | Required input datasets. | GeoPackage, GeoJSON, or CSV. |
| `raster_agent` | Raster Agent | Performs raster and mixed raster-vector analysis. | Code-driven workflow with persistent runtime registry. | Required input datasets. | GeoTIFF, GeoPackage, GeoJSON, or CSV. |
| `map_projection_agent` | Map Projection Agent | Reprojects geospatial datasets between coordinate reference systems. | Deterministic local CRS selection and reprojection with pyproj/geopandas. | Required input datasets. | GeoPackage, GeoJSON, GeoTIFF, or Shapefile. |
| `mapping_agent` | Mapping Agent | Creates static maps and charts from prepared datasets. | Visualization workflow using geospatial plotting libraries. | Required input datasets. | PNG maps or charts. |
| `web_mapping_app_agent` | Web Mapping App Agent | Creates browser-ready web mapping apps from vector, raster, or tabular geospatial data. | LLM-assisted app design and code generation with deterministic fallback behavior. | Required input datasets. | HTML web mapping applications. |
| `spatial_statistics_agent` | Spatial Statistics Agent | Runs PySAL-based spatial statistics and modeling workflows. | LLM-assisted PySAL method selection, code generation, and report generation. | Required input datasets. | TXT and HTML reports, plus optional maps or charts. |

## Agent Details

### Geospatial Data Retrieval Agent

`geospatial_data_retrieval_agent` interprets a data request, selects a supported
data source, uses source-specific handbook guidance, generates download code,
and packages the retrieved data.

Useful developer pattern:

- Build a domain-specific knowledge base for source selection.
- Use handbooks to constrain LLM-generated code.
- Normalize downloaded outputs into standard GAS artifacts.
- Document source-specific credentials in `extensions`.

Files:

- `gas_server/agents/geospatial_data_retrieval_agent.py`
- `gas_server/services/geospatial_data_retrieval_agent_service.py`
- `gas_server/capabilities/geospatial_data_retrieval_agent.json`
- `gas_server/agents/geospatial_data_retrieval_handbooks/`

### PASDA Discovery Agent

`pasda_agent` is a repository-specific data retrieval agent for Pennsylvania
Spatial Data Access. It demonstrates how to build a focused gateway to one
external geospatial data portal.

Useful developer pattern:

- Specialize an agent around one data repository.
- Search, inspect, sample, and download candidate layers.
- Package source-specific files into standard GAS artifacts.

Files:

- `gas_server/agents/pasda_agent.py`
- `gas_server/services/pasda_agent_service.py`
- `gas_server/capabilities/pasda_agent.json`

### Geospatial Data Inspection Agent

`geospatial_data_inspection_agent` inspects vector, raster, and tabular inputs
for CRS, geometry validity, missing values, duplicates, metadata completeness,
and workflow readiness. It returns human-readable TXT and HTML reports.

Useful developer pattern:

- Start with deterministic inspection facts.
- Use an LLM only for workflow-specific interpretation when helpful.
- Return report artifacts while preserving the standard GAS response shape.

Files:

- `gas_server/agents/geospatial_data_inspection_agent.py`
- `gas_server/services/geospatial_data_inspection_agent_service.py`
- `gas_server/capabilities/geospatial_data_inspection_agent.json`

### Geospatial Workflow Planning Agent

`geospatial_workflow_planning_agent` reads GAS GetCapabilities and
DescribeAgent documents, decomposes a broad user goal into service steps, and
matches each step to suitable GAS agents. It returns plans and optional
client-side execution artifacts, but it does not run downstream services.

Useful developer pattern:

- Use GAS capability documents as the source of truth for service discovery.
- Keep planning separate from execution.
- Return both machine-readable and human-readable artifacts.
- Generate optional client-side code or notebooks without embedding real keys.

Files:

- `gas_server/agents/geospatial_workflow_planning_agent.py`
- `gas_server/services/geospatial_workflow_planning_agent_service.py`
- `gas_server/capabilities/geospatial_workflow_planning_agent.json`

### Vector Analysis Agent

`vector_analysis_agent` performs common vector spatial operations such as
attribute joins, spatial joins, buffers, clips, intersections, filtering, and
aggregation. It uses deterministic fast paths for common tasks and a
model-backed fallback for more complex requests.

Useful developer pattern:

- Implement deterministic fast paths for common operations.
- Validate outputs before returning them.
- Use model-backed code execution only when the request is too open-ended for a
  known fast path.

Files:

- `gas_server/agents/vector_analysis_agent.py`
- `gas_server/services/vector_analysis_agent_service.py`
- `gas_server/capabilities/vector_analysis_agent.json`

### Raster Agent

`raster_agent` performs raster and mixed raster-vector workflows such as
clipping, raster calculations, rasterization, and combined spatial analysis. It
maintains a runtime registry so multi-step generated code can reuse intermediate
variables.

Useful developer pattern:

- Support complex multi-step analysis with a controlled execution loop.
- Preserve georeferencing and raster metadata.
- Return GeoTIFF for raster outputs and GeoPackage/GeoJSON for vector outputs.

Files:

- `gas_server/agents/raster_agent.py`
- `gas_server/services/raster_agent_service.py`
- `gas_server/capabilities/raster_agent.json`

### Map Projection Agent

`map_projection_agent` transforms geospatial datasets between coordinate
reference systems. It uses local pyproj/geopandas logic first, so no external
CRS lookup key is required. If model credentials are supplied, the agent can use
an optional LLM fallback to interpret ambiguous natural-language CRS requests
while still choosing from local CRS candidates.

Useful developer pattern:

- Prefer deterministic execution when the request is explicit.
- Use local CRS databases and dataset extent before considering external services.
- Keep outputs aligned with the input data type.

Files:

- `gas_server/agents/map_projection_agent.py`
- `gas_server/services/map_projection_agent_service.py`
- `gas_server/capabilities/map_projection_agent.json`

### Mapping Agent

`mapping_agent` creates static maps and charts from prepared datasets and
returns PNG outputs. It is useful for final presentation or communication
stages after data retrieval or analysis.

Useful developer pattern:

- Convert analytical outputs into visual artifacts.
- Use plotting libraries to produce stable file outputs.
- Keep visualization parameters discoverable in the capability document.

Files:

- `gas_server/agents/mapping_agent.py`
- `gas_server/services/mapping_agent_service.py`
- `gas_server/capabilities/mapping_agent.json`

### Web Mapping App Agent

`web_mapping_app_agent` creates browser-ready HTML web mapping applications
from one or more input datasets. It considers map layers, symbology, popups,
legends, basemaps, layer controls, spatial extent, side panels, filters,
summary sections, and other lightweight app UI features.

Useful developer pattern:

- Generate rich HTML app artifacts.
- Use LLM-assisted design and code generation for flexible user instructions.
- Enforce important default behavior, such as layer controls and legends for
  choropleth-style maps, while supporting richer app layouts when useful.

Files:

- `gas_server/agents/web_mapping_app_agent.py`
- `gas_server/services/web_mapping_app_agent_service.py`
- `gas_server/capabilities/web_mapping_app_agent.json`

Example notebook:

- `examples_for_using_gas_services/pa_health_food_hospitals_web_mapping_app_workflow.ipynb`

### Spatial Statistics Agent

`spatial_statistics_agent` uses the PySAL ecosystem for spatial statistics,
spatial weights, spatial autocorrelation, modeling, classification, and
visualization workflows. It returns both plain text and polished HTML reports.

Useful developer pattern:

- Wrap a major domain library as an expert GAS service.
- Generate reports as first-class artifacts.
- Include maps or charts in HTML reports when the workflow produces them.

Files:

- `gas_server/agents/spatial_statistics_agent.py`
- `gas_server/services/spatial_statistics_agent_service.py`
- `gas_server/capabilities/spatial_statistics_agent.json`

## Choosing An Example To Follow

Use these examples as starting points:

- Data retrieval from external sources: `geospatial_data_retrieval_agent`
- Repository-specific search and download: `pasda_agent`
- Input quality checks and workflow readiness: `geospatial_data_inspection_agent`
- Deterministic vector operations: `vector_analysis_agent`
- Raster or mixed raster-vector analysis: `raster_agent`
- CRS transformation: `map_projection_agent`
- Static visualization: `mapping_agent`
- Web mapping app development: `web_mapping_app_agent`
- Spatial statistics and modeling: `spatial_statistics_agent`

For the step-by-step workflow, see
[adding_an_agent_service.md](adding_an_agent_service.md).
