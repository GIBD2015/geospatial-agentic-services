# Included GAS Agents

This GAS server includes working geospatial agents that also serve as concrete
implementation examples for developers. They show different ways to build GAS
services: deterministic geospatial workflows, model-assisted code execution,
data retrieval, data inspection, exploratory spatial data analysis, workflow
planning, mapping, raster processing, spatial analysis, spatiotemporal
conflict-event preprocessing, vector analysis, and spatial statistics.

When adding a new agent, start by finding the included agent that is closest to
your intended design. Then inspect its implementation file, service wrapper,
and capability document.

## Quick Comparison

| Agent ID | Agent Name | Main Purpose | Implementation Pattern | Inputs | Primary Outputs |
|---|---|---|---|---|---|
| [`geospatial_data_retrieval_agent`](#geospatial-data-retrieval-agent) | Geospatial Data Retrieval Agent | Retrieves one or more geospatial datasets from supported external sources. | Model-assisted request decomposition, source selection, and code generation using data-source handbooks. | Optional input datasets. | One or more GeoPackage, GeoJSON, GeoTIFF, Shapefile, CSV, or source-specific files. |
| [`usgs_earthquake_agent`](#usgs-earthquake-agent) | USGS Earthquake Agent | Retrieves, maps, summarizes, monitors, and reports earthquake activity from USGS catalog and real-time feeds. | Deterministic USGS/geospatial tools with optional LLM-assisted tool planning. | Optional input datasets. | Earthquake datasets, event tables, maps, grid summaries, buffers, alert summaries, and reports. |
| [`pasda_agent`](#pasda-discovery-agent) | PASDA Discovery Agent | Finds and downloads datasets from PASDA. | Repository-specific discovery workflow with model-assisted search and packaging. | Optional input datasets. | GeoPackage, GeoJSON, or source-specific PASDA files. |
| [`geospatial_data_inspection_agent`](#geospatial-data-inspection-agent) | Geospatial Data Inspection Agent | Checks vector, raster, and tabular datasets for quality and workflow readiness. | Deterministic inspection plus optional LLM-assisted interpretation. | Required input datasets. | TXT and HTML inspection reports. |
| [`exploratory_spatial_data_analysis_agent`](#exploratory-spatial-data-analysis-agent) | Exploratory Spatial Data Analysis Agent | Profiles tabular and geospatial datasets to summarize distributions, missingness, correlations, categories, geometry, and lightweight spatial patterns. | LLM-generated ESDA scripts with deterministic pandas/geopandas/matplotlib fallback. | Required input datasets. | HTML and TXT ESDA reports plus chart images. |
| [`geospatial_workflow_planning_agent`](#geospatial-workflow-planning-agent) | Geospatial Workflow Planning Agent | Discovers GAS capabilities and plans client-side service chains. | Capability-aware LLM planning with JSON, Markdown, code, notebook, and graph artifacts. | Optional input datasets and optional GAS GetCapabilities URLs. | Workflow plan JSON, Markdown, optional Python, notebook, and HTML graph. |
| [`spatiotemporal_conflict_event_agent`](#spatiotemporal-conflict-event-layer-agent) | Spatiotemporal Conflict Event Layer Agent | Converts unstructured conflict reports or structured event tables into standardized, GIS-ready spatiotemporal event layers. | Structured table normalization with optional LLM extraction and optional OpenCage geocoding. | Optional input datasets or task text. | CSV event table, GeoJSON point layer, TXT/HTML reports, and optional HTML map. |
| [`vector_analysis_agent`](#vector-analysis-agent) | Vector Analysis Agent | Performs vector joins, buffers, clips, intersections, filtering, and aggregation. | Deterministic fast paths for common operations plus model-backed fallback. | Required input datasets. | GeoPackage, GeoJSON, or CSV. |
| [`raster_agent`](#raster-agent) | Raster Agent | Performs raster and mixed raster-vector analysis. | Code-driven workflow with persistent runtime registry. | Required input datasets. | GeoTIFF, GeoPackage, GeoJSON, or CSV. |
| [`spatial_analysis_agent`](#spatial-analysis-agent) | Spatial Analysis Agent | Builds and executes an end-to-end geoprocessing workflow from input datasets and a natural-language task. | LLM-designed NetworkX workflow DAG, per-operation code generation, assembly, and sandboxed execution. | Required input datasets. | GeoPackage, GeoJSON, CSV, PNG, or HTML workflow results. |
| [`map_projection_agent`](#map-projection-agent) | Map Projection Agent | Reprojects geospatial datasets between coordinate reference systems. | Deterministic local CRS selection and reprojection with pyproj/geopandas. | Required input datasets. | GeoPackage, GeoJSON, GeoTIFF, or Shapefile. |
| [`mapping_agent`](#mapping-agent) | Mapping Agent | Creates static maps and charts from prepared datasets. | Visualization workflow using geospatial plotting libraries. | Required input datasets. | PNG maps or charts. |
| [`web_mapping_app_agent`](#web-mapping-app-agent) | Web Mapping App Agent | Creates browser-ready web mapping apps from vector, raster, or tabular geospatial data. | LLM-assisted app design and code generation with deterministic fallback behavior. | Required input datasets. | HTML web mapping applications. |
| [`spatial_statistics_agent`](#spatial-statistics-agent) | Spatial Statistics Agent | Runs PySAL-based spatial statistics and modeling workflows. | LLM-assisted PySAL method selection, code generation, and report generation. | Required input datasets. | TXT and HTML reports, plus optional maps or charts. |

## Agent Details

### Geospatial Data Retrieval Agent

`geospatial_data_retrieval_agent` interprets a data request, selects supported
data sources, uses source-specific handbook guidance, generates download code,
and packages the retrieved data. When a request asks for multiple independent
datasets, the agent decomposes it into sub-requests, downloads each dataset,
and returns all artifacts together with per-sub-task diagnostics.

Useful developer pattern:

- Build a domain-specific knowledge base for source selection.
- Use handbooks to constrain LLM-generated code.
- Decompose multi-dataset requests while preserving one self-contained request
  per output dataset.
- Normalize downloaded outputs into standard GAS artifacts.
- Document source-specific credentials in `extensions`.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/geospatial_data_retrieval_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/geospatial_data_retrieval_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/geospatial_data_retrieval_agent.json)
- [retrieval handbooks](https://github.com/GIBD2015/geospatial-agentic-services/tree/main/gas_server/agents/geospatial_data_retrieval_handbooks)

### USGS Earthquake Agent

`usgs_earthquake_agent` is a focused domain agent for USGS earthquake data. It
queries the USGS Earthquake Catalog or real-time feeds, exports event datasets,
creates static PNG and interactive HTML basemap maps with magnitude-scaled or
depth-colored symbols, builds animation-ready GeoJSON, generates grid summaries
and impact buffers, prepares alert-ready summaries, and writes Markdown or HTML
activity reports.

Useful developer pattern:

- Keep the data-source API and geospatial operations deterministic.
- Let the LLM, when credentials are supplied, select among trusted tools rather
  than generate arbitrary code.
- Return reusable data artifacts even when the user asks primarily for a map,
  brief, alert, or report.
- Include methods, query parameters, retrieval time, and limitations in reports.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/usgs_earthquake_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/usgs_earthquake_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/usgs_earthquake_agent.json)

### PASDA Discovery Agent

`pasda_agent` is a repository-specific data retrieval agent for Pennsylvania
Spatial Data Access. It demonstrates how to build a focused gateway to one
external geospatial data portal.

Useful developer pattern:

- Specialize an agent around one data repository.
- Search, inspect, sample, and download candidate layers.
- Package source-specific files into standard GAS artifacts.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/pasda_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/pasda_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/pasda_agent.json)

### Geospatial Data Inspection Agent

`geospatial_data_inspection_agent` inspects vector, raster, and tabular inputs
for CRS, geometry validity, missing values, duplicates, metadata completeness,
and workflow readiness. It returns human-readable TXT and HTML reports.

Useful developer pattern:

- Start with deterministic inspection facts.
- Use an LLM only for workflow-specific interpretation when helpful.
- Return report artifacts while preserving the standard GAS response shape.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/geospatial_data_inspection_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/geospatial_data_inspection_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/geospatial_data_inspection_agent.json)

### Exploratory Spatial Data Analysis Agent

`exploratory_spatial_data_analysis_agent` profiles tabular and geospatial
datasets to summarize what is in the data and how it is arranged in space. It
returns polished HTML and plain-text reports with descriptive statistics,
missingness summaries, distribution charts, correlation views, categorical
breakdowns, geometry diagnostics, classified choropleths, point-density maps,
and a quick global spatial-autocorrelation check when dependencies are
available.

Useful developer pattern:

- Generate tailored analysis code from a dataset profile and task focus.
- Keep ESDA descriptive and exploratory rather than formal inference.
- Provide deterministic fallback reports when model credentials or generated
  code are unavailable.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/exploratory_spatial_data_analysis_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/exploratory_spatial_data_analysis_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/exploratory_spatial_data_analysis_agent.json)

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

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/geospatial_workflow_planning_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/geospatial_workflow_planning_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/geospatial_workflow_planning_agent.json)

### Spatiotemporal Conflict Event Layer Agent

`spatiotemporal_conflict_event_agent` converts unstructured conflict reports or
structured event-like tables into standardized spatiotemporal event records.
It extracts or normalizes event locations, dates, categories, descriptions,
evidence quotes, source fields, and coordinates, then writes GIS-ready CSV,
GeoJSON, TXT, HTML, and optional Folium map artifacts for downstream GAS
mapping, web mapping, spatial analysis, and spatial statistics workflows.

Useful developer pattern:

- Treat messy domain text or tables as a preprocessing step for downstream GIS
  agents.
- Keep structured table normalization deterministic when fields and coordinates
  are already present.
- Use model-backed extraction only for unstructured text and optional OpenCage
  geocoding only when coordinates are missing.
- Preserve records without valid coordinates in CSV and reports while excluding
  them from point GeoJSON output.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/spatiotemporal_conflict_event_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/spatiotemporal_conflict_event_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/spatiotemporal_conflict_event_agent.json)

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

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/vector_analysis_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/vector_analysis_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/vector_analysis_agent.json)

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

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/raster_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/raster_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/raster_agent.json)

### Spatial Analysis Agent

`spatial_analysis_agent` runs an end-to-end LLM-driven geoprocessing pipeline.
It audits input datasets into a data registry, asks an LLM to design a NetworkX
workflow DAG, generates operation-node code, assembles the operations into one
program, and executes the program in a per-task working directory. A caller may
also provide a pre-built workflow graph or data registry to skip those stages.

Useful developer pattern:

- Convert a broad spatial analysis request into an explicit workflow graph.
- Generate operation code with ancestor and descendant context.
- Persist final results separately from intermediate outputs.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/spatial_analysis_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/spatial_analysis_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/spatial_analysis_agent.json)

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

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/map_projection_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/map_projection_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/map_projection_agent.json)

### Mapping Agent

`mapping_agent` creates static maps and charts from prepared datasets and
returns PNG outputs. It is useful for final presentation or communication
stages after data retrieval or analysis.

Useful developer pattern:

- Convert analytical outputs into visual artifacts.
- Use plotting libraries to produce stable file outputs.
- Keep visualization parameters discoverable in the capability document.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/mapping_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/mapping_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/mapping_agent.json)

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

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/web_mapping_app_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/web_mapping_app_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/web_mapping_app_agent.json)

Example notebook:

- [PA health, food, and hospitals web mapping workflow](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/examples_for_using_gas_services/pa_health_food_hospitals_web_mapping_app_workflow.ipynb)

### Spatial Statistics Agent

`spatial_statistics_agent` uses the PySAL ecosystem for spatial statistics,
spatial weights, spatial autocorrelation, modeling, classification, and
visualization workflows. It returns both plain text and polished HTML reports.

Useful developer pattern:

- Wrap a major domain library as an expert GAS service.
- Generate reports as first-class artifacts.
- Include maps or charts in HTML reports when the workflow produces them.

Files:

- [agent implementation](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/agents/spatial_statistics_agent.py)
- [service wrapper](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/services/spatial_statistics_agent_service.py)
- [capability document](https://github.com/GIBD2015/geospatial-agentic-services/blob/main/gas_server/capabilities/spatial_statistics_agent.json)

## Choosing An Example To Follow

Use these examples as starting points:

- Data retrieval from external sources: [`geospatial_data_retrieval_agent`](#geospatial-data-retrieval-agent)
- Focused USGS earthquake retrieval, mapping, alerts, and reports: [`usgs_earthquake_agent`](#usgs-earthquake-agent)
- Repository-specific search and download: [`pasda_agent`](#pasda-discovery-agent)
- Input quality checks and workflow readiness: [`geospatial_data_inspection_agent`](#geospatial-data-inspection-agent)
- Exploratory descriptive analysis and charts: [`exploratory_spatial_data_analysis_agent`](#exploratory-spatial-data-analysis-agent)
- Conflict report or event-table preprocessing: [`spatiotemporal_conflict_event_agent`](#spatiotemporal-conflict-event-layer-agent)
- End-to-end LLM-designed spatial workflows: [`spatial_analysis_agent`](#spatial-analysis-agent)
- Deterministic vector operations: [`vector_analysis_agent`](#vector-analysis-agent)
- Raster or mixed raster-vector analysis: [`raster_agent`](#raster-agent)
- CRS transformation: [`map_projection_agent`](#map-projection-agent)
- Static visualization: [`mapping_agent`](#mapping-agent)
- Web mapping app development: [`web_mapping_app_agent`](#web-mapping-app-agent)
- Spatial statistics and modeling: [`spatial_statistics_agent`](#spatial-statistics-agent)

For the step-by-step workflow, see
[adding_an_agent_service.md](adding_an_agent_service.md).
