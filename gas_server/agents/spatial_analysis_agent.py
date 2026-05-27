from __future__ import annotations

import contextlib
import glob as globmod
import html as html_lib
import io
import json
import logging
import os
import platform
import re
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import networkx as nx

from dotenv import load_dotenv

from gas_server.core.config import DATA_DIR, ensure_runtime_dirs
from gas_server.core.geo_agent import GeoAgent, ProgressCallback
from gas_server.core.llm_client import build_llm_client, format_service_name

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


load_dotenv()
ensure_runtime_dirs()



WORKFLOW_GENERATOR_ROLE = (
    "A professional Geo-information scientist and programmer good at Python. You have worked on "
    "Geographic information science more than 20 years, and know every detail and pitfall when "
    "processing spatial data and coding. You know well how to set up workflows for spatial "
    "analysis tasks. You have significant experience on graph theory, application, and "
    "implementation. You are also experienced in generating maps using Matplotlib and GeoPandas."
)

WORKFLOW_GENERATOR_TASK_PREFIX = (
    "Generate a directed acyclic graph (DAG) whose nodes are (1) operation steps and (2) data "
    "nodes. When multiple independent data sources exist, create PARALLEL branches that each "
    "load and process their data independently, then converge where the analysis requires "
    "combined inputs. The workflow should resemble a real geoprocessing model (e.g., ArcGIS "
    "Model Builder) with multiple input branches, NOT a single linear chain. Solve this question:"
)

WORKFLOW_GENERATOR_REPLY_EXAMPLE = """```python
import networkx as nx
G = nx.DiGraph()

# === Branch A: Load and process tract boundaries ===
G.add_node("tract_shp_path", node_type="data", data_path="data/tracts.gpkg", description="Census tract boundary file")
G.add_node("load_tracts", node_type="operation", description="Load census tract boundaries")
G.add_edge("tract_shp_path", "load_tracts")
G.add_node("tract_gdf", node_type="data", data_path="", description="Census tract GeoDataFrame")
G.add_edge("load_tracts", "tract_gdf")

# === Branch B: Load and process hazardous waste sites ===
G.add_node("haz_waste_path", node_type="data", data_path="data/hw_sites.gpkg", description="Hazardous waste facility shapefile")
G.add_node("load_haz_waste", node_type="operation", description="Load hazardous waste facility data")
G.add_edge("haz_waste_path", "load_haz_waste")
G.add_node("haz_waste_gdf", node_type="data", data_path="", description="Hazardous waste facility GeoDataFrame")
G.add_edge("load_haz_waste", "haz_waste_gdf")

# === Convergence: Both branches feed into spatial analysis ===
G.add_node("count_facilities", node_type="operation", description="Count hazardous waste facilities within each census tract")
G.add_edge("tract_gdf", "count_facilities")
G.add_edge("haz_waste_gdf", "count_facilities")
G.add_node("tract_with_counts", node_type="data", data_path="", description="Tracts with facility counts")
G.add_edge("count_facilities", "tract_with_counts")

# === Final output ===
G.add_node("create_map", node_type="operation", description="Create choropleth map of facility counts per tract")
G.add_edge("tract_with_counts", "create_map")
G.add_node("final_map", node_type="data", data_path="", description="Choropleth map output")
G.add_edge("create_map", "final_map")
```"""

WORKFLOW_GENERATOR_REQUIREMENTS = [
    "Think step by step.",
    "Steps and data (both input and output) form a directed acyclic graph (DAG) stored in NetworkX. Disconnected components are NOT allowed.",
    "Each step is a data process operation: the input can be data paths or variables, and the output can be data paths or variables.",
    "There are two types of nodes: a) operation node, and b) data node (both input and output data). Data nodes are also input nodes for the next operation node.",
    "When multiple independent data sources are provided, load and process each in its OWN PARALLEL BRANCH. An operation that needs results from multiple branches must have edges from ALL required data nodes (multiple incoming edges).",
    "Carefully name each output data node — make names human readable but not too long.",
    "The data and operation nodes form a DAG. Multiple branches MUST be used when the analysis involves multiple independent datasets that are processed separately before being combined.",
    "The first operations are data loading or collection, and the output of the last operation is the final answer to the task.",
    "Operation nodes connect via output data nodes; DO NOT connect operation nodes directly.",
    'The node attributes include: 1) node_type (data or operation), 2) data_path (data node only; "" if not given), and description.',
    "Connections between data nodes and operation nodes are edges.",
    "Add all nodes and edges, including node attributes, to a NetworkX DiGraph named G. DO NOT change the attribute names.",
    "DO NOT generate code to implement the steps (only the graph).",
    "Join an attribute table to a vector layer via a common attribute if necessary.",
    "Put your reply into a Python code block (enclosed by ```python and ```), NO explanation outside the code block.",
    "GraphML writer does not support class dict or list as attribute values — keep them strings.",
    "You need spatial data (vector or raster) to make a map.",
    "Do not put the GraphML writing process as a step in the graph.",
    "Keep the graph concise; DO NOT use too many operation nodes.",
]

OPERATION_ROLE = (
    "A professional Geo-information scientist and programmer good at Python. You have worked on "
    "Geographic information science more than 20 years, and know every detail and pitfall when "
    "processing spatial data and coding. You design robust functions with clear interfaces, "
    "sensible CRS handling, safe joins, and consistent variable naming across a multi-step workflow."
)

OPERATION_REPLY_EXAMPLE = """```python
def Load_csv(tract_population_csv_url='https://example.com/data.csv'):
    # Description: Load a CSV file from a URL
    tract_population_df = pd.read_csv(tract_population_csv_url)
    return tract_population_df
```"""

OPERATION_REQUIREMENTS = [
    "DO NOT change the given variable names and paths.",
    "Put your reply into a Python code block (enclosed by ```python and ```), NO explanation or conversation outside the code block.",
    (
        "OUTPUT DIRECTORY (mandatory): a single variable OUTPUT_DIR is injected into the execution "
        "scope at runtime. When your function writes a file, build its path with "
        "os.path.join(OUTPUT_DIR, <filename>). Never hardcode absolute paths, never use bare relative "
        "paths, and do not redefine OUTPUT_DIR."
    ),
    (
        "FIGURES / MAPS: use plt.savefig(os.path.join(OUTPUT_DIR, <filename>), dpi=150, bbox_inches='tight') "
        "followed by plt.close(). Never call plt.show(). Folium/Plotly maps must be saved with .save(...)."
    ),
    "Do not infer output paths from input paths. Output must live under OUTPUT_DIR.",
    (
        "Write a file ONLY when this operation produces a final/terminal output of the workflow (a node "
        "with no descendant operations). The final output may be ANY file type (.gpkg, .geojson, .csv, "
        ".png, .html, .json, .txt, ...). Intermediate operations should return their result in memory "
        "(GeoDataFrame, DataFrame, array, value) rather than writing an intermediate file."
    ),
    "Receive data via function parameters. Do NOT reload data that an ancestor function already returns.",
    "When doing spatial analysis, reproject all involved spatial layers into a common CRS before the operation.",
    "If joining DataFrame and GeoDataFrame on common columns, do NOT convert the DataFrame to a GeoDataFrame.",
    "Drop NaNs in join/calculation columns before further processing (df.dropna(subset=[...])).",
    "Treat FIPS / GEOID columns as strings with leading zeros (state: 2, county: 5, tract: 11, block group: 12) when joining across datasets.",
    "Note geopandas.sjoin() may return one-to-many results — drop_duplicates when the desired output is one row per left feature.",
    'Do not use \'if __name__ == "__main__":\' — the assembly is executed via exec().',
]

ASSEMBLY_ROLE = (
    "A professional Geo-information scientist and programmer good at Python. You are very good at "
    "assembling several functions and small programs together into one robust executable script."
)

ASSEMBLY_REQUIREMENTS = [
    "Think step by step.",
    "Each function is one step toward solving the question; the output of the final function is the final answer.",
    "Put your reply in a code block (enclosed by ```python and ```), NO explanation or conversation outside the code block.",
    (
        "OUTPUT DIRECTORY: OUTPUT_DIR is already defined in the execution scope. Write every file with "
        "os.path.join(OUTPUT_DIR, ...). Do not redefine it. Do not use bare relative or hardcoded absolute paths."
    ),
    (
        "PERSIST ONLY THE FINAL OUTPUT: the workflow's terminal/final output node(s) MUST be written "
        "to a file inside OUTPUT_DIR — never just printed or left in memory. The final output may be "
        "ANY file type appropriate to the result (e.g. .gpkg, .geojson, .csv, .png, .html, .json, .txt); "
        "do not assume it is a GeoPackage or map image. Intermediate results are NOT required on disk — "
        "pass them between functions in memory (as return values) instead of writing intermediate files."
    ),
    (
        "FIGURES: for every matplotlib figure call plt.savefig(os.path.join(OUTPUT_DIR, <filename>), dpi=150, bbox_inches='tight') then plt.close(). "
        "Never call plt.show(). Save folium / plotly maps via .save(<path>)."
    ),
    'The program must be executable. Put the orchestration code in a function named assembely_solution() and call it. Do NOT use \'if __name__ == "__main__":\'.',
    "Use real built-in functions or library attributes — never invent fake ones; choose an alternative method instead.",
]




TRACKED_OUTPUT_EXTENSIONS = (
    "*.csv", "*.png", "*.jpg", "*.jpeg", "*.json", "*.html", "*.txt",
    "*.gpkg", "*.shp", "*.geojson", "*.md", "*.npy",
    "*.tif", "*.tiff", "*.parquet", "*.xlsx", "*.xls", "*.pdf",
    "*.svg", "*.kml", "*.gml", "*.feather", "*.pkl", "*.zip",
    "*.nc", "*.h5", "*.hdf5", "*.dbf",
)

ERROR_TAXONOMY: Dict[str, tuple[str, ...]] = {
    "import_error": ("ModuleNotFoundError", "ImportError"),
    "column_error": ("KeyError",),
    "file_error": ("FileNotFoundError", "OSError", "PermissionError"),
    "syntax_error": ("SyntaxError", "IndentationError"),
    "type_error": ("TypeError", "ValueError"),
    "logic_error": ("AttributeError", "IndexError", "ZeroDivisionError", "NameError"),
    "spatial_error": ("CRSError", "TopologicalError", "GEOSException"),
}




class SpatialAnalysisAgent(GeoAgent):
    """End-to-end LLM-driven geoprocessing workflow runner."""

    agent_id = "spatial_analysis_agent"
    agent_name = "Spatial Analysis Agent"
    agent_version = "1.2.0"
    agent_description = (
        "Audits input datasets, asks an LLM to design a NetworkX workflow DAG that solves the task, "
        "generates Python code for each operation node, assembles the operations into a single "
        "executable program, and runs it in a sandboxed environment."
    )
    requires_input_datasets = True

    DEFAULT_GRAPH_MODEL = "gpt-4o"
    DEFAULT_OPERATION_MODEL = "gpt-4o"
    DEFAULT_ASSEMBLY_MODEL = "gpt-5.2"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        debug: bool = True,
    ):
        if OpenAI is None:
            raise ImportError("Please install the 'openai' package.")
        super().__init__(
            api_key=api_key,
            model=model or self.DEFAULT_ASSEMBLY_MODEL,
            output_dir=DATA_DIR / self.agent_id,
        )
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
        )
        self.debug = debug

        # Per-run state
        self.solution_graph: Optional[nx.DiGraph] = None
        self.operations: List[Dict[str, Any]] = []
        self.assembly_prompt: Optional[str] = None
        self.assembly_code: str = ""
        self.workflow_code: str = ""
        # Tracked-output baseline, seeded once per run before the first assembly attempt.
        self._output_baseline_mtimes: Optional[Dict[str, float]] = None

  

    @staticmethod
    def _peek_dataset(file_path: str) -> Optional[Dict[str, Any]]:
        """Inspect a file and return ``{file, path, shape, columns, head, source}``.

        Mirrors AGSI ``utils.agm_helper._peek_file``: drops the geometry column
        from the head sample to keep LLM context small.
        """
        import pandas as pd

        if not os.path.exists(file_path):
            return None

        ext = os.path.splitext(file_path)[1].lower()
        try:
            geom_name: Optional[str] = None
            if ext == ".csv":
                df_peek = pd.read_csv(file_path, nrows=3)
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fp:
                    total_rows = sum(1 for _ in fp) - 1
            elif ext in (".gpkg", ".shp", ".geojson", ".zip", ".kml"):
                import geopandas as gpd
                df_peek = gpd.read_file(file_path, rows=3)
                total_rows = len(gpd.read_file(file_path, ignore_geometry=True))
                geom_name = getattr(getattr(df_peek, "geometry", None), "name", None)
            elif ext in (".xlsx", ".xls"):
                df_peek = pd.read_excel(file_path, nrows=3)
                total_rows = len(pd.read_excel(file_path))
            elif ext in (".tif", ".tiff", ".nc", ".img", ".vrt"):
                try:
                    import rasterio
                    with rasterio.open(file_path) as src:
                        bands = src.count
                        height = src.height
                        width = src.width
                        crs = str(src.crs) if src.crs else "unknown"
                        band_names = [src.descriptions[i] or f"band_{i + 1}" for i in range(bands)]
                    return {
                        "file": os.path.splitext(os.path.basename(file_path))[0],
                        "path": file_path,
                        "shape": [height, width],
                        "columns": {b: "raster_band" for b in band_names},
                        "head": {"bands": bands, "crs": crs, "width": width, "height": height},
                        "source": "input",
                    }
                except Exception:
                    return {
                        "file": os.path.splitext(os.path.basename(file_path))[0],
                        "path": file_path,
                        "shape": [0, 0],
                        "columns": {},
                        "head": {},
                        "source": "input",
                    }
            else:
                return None

            preview_df = df_peek.head(2)
            if geom_name and geom_name in preview_df.columns:
                preview_df = preview_df.drop(columns=[geom_name], errors="ignore")
            head_dict = preview_df.to_dict(orient="list")

            return {
                "file": os.path.splitext(os.path.basename(file_path))[0],
                "path": file_path,
                "shape": [total_rows, len(df_peek.columns)],
                "columns": {c: str(df_peek[c].dtype) for c in df_peek.columns},
                "head": head_dict,
                "source": "input",
            }
        except Exception as exc:
            logging.warning("Failed to peek %s: %s", file_path, exc)
            return None

    def _build_data_registry(
        self,
        dataset_paths: List[str],
        progress_callback: Optional[ProgressCallback],
    ) -> List[Dict[str, Any]]:
        registry: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for path in dataset_paths:
            if str(path).lower().endswith((".graphml", ".gml")):
                continue
            norm = os.path.normpath(path)
            if norm in seen:
                continue
            seen.add(norm)
            info = self._peek_dataset(path)
            if info:
                registry.append(info)
        self._emit_progress(
            progress_callback,
            stage="input_inspection",
            message=f"I audited the input datasets and built a data registry with {len(registry)} entry/entries.",
            data={"registry_size": len(registry)},
        )
        return registry

    @staticmethod
    def _data_registry_summary(data_registry: List[Dict[str, Any]]) -> str:
        lines = []
        for s in data_registry:
            lines.append(
                f"- {s.get('file', '')} ({s.get('source', '')}) | path: {s.get('path', '')} | "
                f"shape: {s.get('shape', '')} | columns: {s.get('columns', '')}"
            )
        return "\n".join(lines)


    def _workflow_generator_prompt(
        self,
        task: str,
        data_registry: List[Dict[str, Any]],
        graph_file: str,
    ) -> str:
        requirements = list(WORKFLOW_GENERATOR_REQUIREMENTS)
        requirements.append(f"Save the network into GraphML format at: {graph_file}")
        requirements_str = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(requirements))
        return (
            f"Your role: {WORKFLOW_GENERATOR_ROLE}\n\n"
            f"Your task: {WORKFLOW_GENERATOR_TASK_PREFIX}\n{task}\n\n"
            f"Your reply needs to meet these requirements:\n{requirements_str}\n\n"
            f"Your reply example:\n{WORKFLOW_GENERATOR_REPLY_EXAMPLE}\n\n"
            f"Data locations (each data is a node):\n{self._data_registry_summary(data_registry)}\n"
        )

    def _execute_graph_code(self, code: str, graph_file: str) -> str:
        """Run LLM-generated graph code; materialize the .graphml on disk."""
        os.makedirs(os.path.dirname(graph_file) or ".", exist_ok=True)
        exec_globals: Dict[str, Any] = {
            "nx": nx,
            "networkx": nx,
            "graph_file": graph_file,
            "__builtins__": __builtins__,
        }
        exec_locals: Dict[str, Any] = {}
        exec(code, exec_globals, exec_locals)
        graph_obj = exec_locals.get("G") or exec_globals.get("G")
        if graph_obj is not None:
            # GraphML cannot serialize None — coerce to empty strings.
            for node in graph_obj.nodes():
                for key, value in list(graph_obj.nodes[node].items()):
                    if value is None:
                        graph_obj.nodes[node][key] = ""
            for source, target in graph_obj.edges():
                for key, value in list(graph_obj.edges[source, target].items()):
                    if value is None:
                        graph_obj.edges[source, target][key] = ""
            nx.write_graphml(graph_obj, graph_file)
        elif not os.path.exists(graph_file):
            raise RuntimeError(
                "Workflow graph code executed without producing a graph 'G' or writing the GraphML file."
            )
        return graph_file

    def _generate_workflow_graph(
        self,
        task: str,
        data_registry: List[Dict[str, Any]],
        out_dir: str,
        progress_callback: Optional[ProgressCallback],
    ) -> tuple[str, str]:
        """Ask the LLM to design a workflow DAG and write it to disk.

        Returns ``(graphml_path, generator_code)``.
        """
        graph_dir = os.path.join(out_dir, "graphfiles")
        os.makedirs(graph_dir, exist_ok=True)
        graph_file = os.path.join(graph_dir, f"workflow_{uuid.uuid4().hex[:8]}.graphml")

        self._emit_progress(
            progress_callback,
            stage="planning",
            message="I am asking the LLM to design a geoprocessing workflow DAG for the task.",
            data={"graph_file": graph_file},
        )
        graph_model = self.request_parameters.get("graph_model") or self.DEFAULT_GRAPH_MODEL
        system_prompt = self._workflow_generator_prompt(task, data_registry, graph_file)
        content = self._llm_complete(system_prompt, graph_model)
        code = self._extract_code(content)
        if not code:
            raise RuntimeError("Workflow generator returned no code block.")
        self._execute_graph_code(code, graph_file)
        self._emit_progress(
            progress_callback,
            stage="planning",
            message="I received the workflow DAG from the LLM and wrote it to GraphML.",
            data={"graph_file": graph_file, "operation_count": sum(1 for _ in nx.read_graphml(graph_file).nodes())},
        )
        return graph_file, code


    def _load_solution_graph(self, graph_path: str) -> nx.DiGraph:
        if not os.path.exists(graph_path):
            raise FileNotFoundError(f"Workflow graph file not found: {graph_path}")
        self.solution_graph = nx.read_graphml(graph_path)
        return self.solution_graph


    @staticmethod
    def _layered_layout(
        G: nx.DiGraph,
        col_spacing: int = 280,
        row_spacing: int = 110,
        sweep_iterations: int = 24,
    ) -> tuple[Dict[str, int], Dict[str, tuple[float, float]], int, int]:
        """Compute a Sugiyama-style layered layout with barycenter sweep.

        Returns ``(node_level, positions, canvas_w, canvas_h)`` where
        ``positions[node] = (x, y)``. X is fixed by topological level; Y is
        ordered to minimize edge crossings by sweeping the barycenter heuristic
        forward and backward across levels.
        """
        from collections import defaultdict
        from statistics import mean

        # X coordinate = topological level (longest path from any root)
        try:
            topo = list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible:
            topo = list(G.nodes())

        node_level: Dict[str, int] = {}
        for n in topo:
            preds = list(G.predecessors(n))
            node_level[n] = 0 if not preds else max(node_level.get(p, 0) for p in preds) + 1
        max_level = max(node_level.values()) if node_level else 0

        # Group nodes by level, in topological order
        levels: Dict[int, List[str]] = defaultdict(list)
        for n in topo:
            levels[node_level[n]].append(n)
        sorted_levels = sorted(levels.keys())

        # Initial row index within each level (stable from topo order)
        y_index: Dict[str, float] = {}
        for lvl in sorted_levels:
            for i, n in enumerate(levels[lvl]):
                y_index[n] = float(i)

        # Barycenter sweep: alternate forward/backward passes
        for _ in range(sweep_iterations):
            for lvl in sorted_levels:
                if lvl == sorted_levels[0]:
                    continue
                ordered = sorted(
                    levels[lvl],
                    key=lambda n: (
                        mean(y_index[p] for p in G.predecessors(n))
                        if list(G.predecessors(n))
                        else y_index[n]
                    ),
                )
                levels[lvl] = ordered
                for i, n in enumerate(ordered):
                    y_index[n] = float(i)
            for lvl in reversed(sorted_levels):
                if lvl == sorted_levels[-1]:
                    continue
                ordered = sorted(
                    levels[lvl],
                    key=lambda n: (
                        mean(y_index[s] for s in G.successors(n))
                        if list(G.successors(n))
                        else y_index[n]
                    ),
                )
                levels[lvl] = ordered
                for i, n in enumerate(ordered):
                    y_index[n] = float(i)

        # Canvas dimensions
        max_per_level = max(len(levels[lvl]) for lvl in sorted_levels) if sorted_levels else 1
        canvas_w = max(1200, (max_level + 1) * col_spacing + 200)
        canvas_h = max(500, max_per_level * row_spacing + 120)

        # Final pixel positions — center each level vertically
        positions: Dict[str, tuple[float, float]] = {}
        for lvl in sorted_levels:
            members = levels[lvl]
            count = len(members)
            total_h = (count - 1) * row_spacing
            start_y = (canvas_h - total_h) / 2
            for i, n in enumerate(members):
                positions[n] = (140 + lvl * col_spacing, start_y + i * row_spacing)

        return node_level, positions, canvas_w, canvas_h

    @staticmethod
    def _render_workflow_html(
        G: nx.DiGraph,
        output_path: str,
        title: str = "Geoprocessing Workflow",
        research_question: str = "",
    ) -> str:
        """Render a NetworkX DiGraph as an interactive D3-based DAG HTML page.

        Layout style: ArcGIS Model Builder — blue ovals for input data, yellow
        rectangles for operations, green ovals for intermediate data, purple
        ovals for final outputs. Node positions are computed by a layered
        (Sugiyama-style) layout with barycenter sweep — no force simulation,
        so the initial render is already crossing-minimal. Nodes remain
        draggable for fine-tuning; zoom, pan, and click-to-popup are
        preserved.
        """
        col_spacing = 280
        row_spacing = 110
        node_level, positions, canvas_w, canvas_h = SpatialAnalysisAgent._layered_layout(
            G, col_spacing=col_spacing, row_spacing=row_spacing,
        )
        max_level = max(node_level.values()) if node_level else 0

        root_data: set = set()
        final_data: set = set()
        for n, d in G.nodes(data=True):
            if d.get("node_type") == "data":
                if G.in_degree(n) == 0:
                    root_data.add(n)
                if G.out_degree(n) == 0:
                    final_data.add(n)

        # Model Builder palette
        INPUT_FILL, INPUT_STROKE = "#6BA3D6", "#2F74B5"
        TOOL_FILL, TOOL_STROKE = "#F5D576", "#C4A030"
        OUTPUT_FILL, OUTPUT_STROKE = "#6BB56A", "#3D8B3D"
        FINAL_FILL, FINAL_STROKE = "#9B6DBF", "#6C3483"
        FONT = "Segoe UI, Arial, sans-serif"

        node_id_map = {n: i for i, n in enumerate(G.nodes())}
        nodes_json: List[Dict[str, Any]] = []
        details: Dict[str, str] = {}

        for n, d in G.nodes(data=True):
            nt = d.get("node_type", "data")
            desc = d.get("description", n)
            data_path = d.get("data_path", "")
            is_input = n in root_data
            is_final = n in final_data

            if nt == "operation":
                fill, stroke, shape = TOOL_FILL, TOOL_STROKE, "rect"
            elif is_input:
                fill, stroke, shape = INPUT_FILL, INPUT_STROKE, "ellipse"
            elif is_final:
                fill, stroke, shape = FINAL_FILL, FINAL_STROKE, "ellipse"
            else:
                fill, stroke, shape = OUTPUT_FILL, OUTPUT_STROKE, "ellipse"

            label = desc if len(desc) < 28 else desc[:25] + "..."
            x, y = positions[n]
            nodes_json.append({
                "id": n,
                "idx": node_id_map[n],
                "level": node_level.get(n, 0),
                "shape": shape,
                "fill": fill,
                "stroke": stroke,
                "label": label,
                "isFinal": is_final,
                "x": x,
                "y": y,
            })

            if nt == "operation":
                inputs = list(G.predecessors(n))
                outputs = list(G.successors(n))
                details[n] = (
                    f"<h3>{html_lib.escape(desc)}</h3>"
                    f"<p><b>Type:</b> Operation</p>"
                    f"<p><b>Inputs:</b> {', '.join(html_lib.escape(i) for i in inputs)}</p>"
                    f"<p><b>Outputs:</b> {', '.join(html_lib.escape(o) for o in outputs)}</p>"
                )
            else:
                kind = "Input Data" if is_input else ("Final Output" if is_final else "Intermediate Data")
                det = f"<h3>{html_lib.escape(n)}</h3><p><b>Type:</b> {kind}</p>"
                if desc:
                    det += f"<p><b>Description:</b> {html_lib.escape(desc)}</p>"
                if data_path:
                    det += f"<p><b>Path:</b> <code>{html_lib.escape(data_path)}</code></p>"
                details[n] = det

        links_json = [
            {"source": node_id_map[u], "target": node_id_map[v]} for u, v in G.edges()
        ]

        rq_block = ""
        if research_question:
            rq_block = (
                f'<div class="subtitle">{html_lib.escape(research_question)}</div>'
            )

        html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html_lib.escape(title)}</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  body {{ margin:0; font-family:{FONT}; background:#f8f9fa; display:flex; flex-direction:column; height:100vh; }}
  h1 {{ text-align:center; padding:12px 0 4px; margin:0; font-size:18px; color:#2C3E50; }}
  .subtitle {{ text-align:center; font-size:12px; color:#555; padding:0 24px 8px; }}
  #graph {{ flex:1; overflow:hidden; position:relative; }}
  svg {{ width:100%; height:100%; }}
  .link {{ fill:none; stroke:#555; stroke-width:2; }}
  .node {{ cursor:grab; }}
  .node:active {{ cursor:grabbing; }}
  .node:hover {{ filter:brightness(1.12) drop-shadow(0 0 6px rgba(0,0,0,.3)); }}
  .node-label {{ pointer-events:none; font-family:{FONT}; font-size:12px; font-weight:bold; text-anchor:middle; }}
  .legend {{ position:absolute; bottom:10px; left:10px; background:#fff; border-radius:8px; padding:10px 14px; box-shadow:0 2px 8px rgba(0,0,0,.15); border:1px solid #ddd; font-size:11px; z-index:50; }}
  .legend-item {{ display:flex; align-items:center; gap:8px; margin:4px 0; }}
  .legend-swatch {{ width:28px; height:16px; border:1px solid #999; }}
  .toolbar {{ position:absolute; top:10px; right:10px; display:flex; flex-direction:column; gap:4px; z-index:50; background:#fff; border-radius:8px; padding:6px; box-shadow:0 2px 8px rgba(0,0,0,.15); border:1px solid #ddd; }}
  .tbtn {{ width:32px; height:32px; border:1px solid #ddd; border-radius:6px; background:#fff; cursor:pointer; font-size:15px; display:flex; align-items:center; justify-content:center; color:#444; padding:0; }}
  .tbtn:hover {{ background:#e9ecef; }}
  .popup-overlay {{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,.35); z-index:100; justify-content:center; align-items:center; }}
  .popup-overlay.visible {{ display:flex; }}
  .popup-box {{ background:#fff; border-radius:10px; padding:24px 28px; max-width:580px; width:92%; max-height:75vh; overflow-y:auto; box-shadow:0 8px 32px rgba(0,0,0,.25); position:relative; animation:popIn .18s ease-out; }}
  @keyframes popIn {{ from{{transform:scale(.92);opacity:0}} to{{transform:scale(1);opacity:1}} }}
  .popup-box h3 {{ margin-top:0; color:#2C3E50; border-bottom:2px solid #4C72B0; padding-bottom:8px; }}
  .popup-box p {{ line-height:1.6; color:#333; font-size:13px; }}
  .popup-close {{ position:absolute; top:10px; right:14px; background:none; border:none; font-size:20px; color:#999; cursor:pointer; padding:4px 8px; border-radius:4px; }}
  .popup-close:hover {{ background:#f0f0f0; color:#333; }}
</style>
</head>
<body>
<h1>{html_lib.escape(title)}</h1>
{rq_block}
<div id="graph">
  <div class="toolbar">
    <button class="tbtn" id="zoomInBtn" title="Zoom in">+</button>
    <button class="tbtn" id="zoomOutBtn" title="Zoom out">&minus;</button>
    <button class="tbtn" id="fitBtn" title="Fit to view">&#9974;</button>
    <button class="tbtn" id="resetBtn" title="Reset layout">&#8634;</button>
  </div>
  <div class="legend">
    <div class="legend-item"><div class="legend-swatch" style="background:{INPUT_FILL};border-radius:8px"></div> Input Data</div>
    <div class="legend-item"><div class="legend-swatch" style="background:{TOOL_FILL};border-radius:3px"></div> Operation</div>
    <div class="legend-item"><div class="legend-swatch" style="background:{OUTPUT_FILL};border-radius:8px"></div> Intermediate Data</div>
    <div class="legend-item"><div class="legend-swatch" style="background:{FINAL_FILL};border-radius:8px"></div> Final Output</div>
  </div>
</div>

<div class="popup-overlay" id="popupOverlay" onclick="closePopup(event)">
  <div class="popup-box" onclick="event.stopPropagation()">
    <button class="popup-close" onclick="closePopup()">&times;</button>
    <div id="popupContent"></div>
  </div>
</div>

<script>
const nodesData = {json.dumps(nodes_json)};
const linksData = {json.dumps(links_json)};
const details   = {json.dumps(details)};

const W = {canvas_w}, H = {canvas_h};
const RW = 170, RH = 56, RX = 8;
const EX = 80,  EY = 28;

// Save original layout positions so the user can reset after dragging.
const ORIGINAL_POS = new Map(nodesData.map(d => [d.idx, {{x: d.x, y: d.y}}]));

const svg = d3.select('#graph').append('svg').attr('viewBox', [0, 0, W, H]);
const zoomG = svg.append('g');
const zoomBehavior = d3.zoom().scaleExtent([0.15, 5]).on('zoom', (e) => zoomG.attr('transform', e.transform));
svg.call(zoomBehavior);

svg.append('defs').append('marker')
  .attr('id', 'arrow').attr('viewBox', '0 0 10 7')
  .attr('refX', 10).attr('refY', 3.5)
  .attr('markerWidth', 10).attr('markerHeight', 7)
  .attr('orient', 'auto')
  .append('polygon').attr('points', '0 0,10 3.5,0 7').attr('fill', '#555');

// Resolve link endpoints to actual node objects (the JSON encodes them as indices).
linksData.forEach(l => {{
  l.sourceNode = nodesData[l.source];
  l.targetNode = nodesData[l.target];
}});

function nodeAnchor(d, side) {{
  // Returns the (x, y) where an edge should attach to node d on the given side.
  if (d.shape === 'rect') {{
    return {{ x: d.x + (side === 'right' ? RW/2 : -RW/2), y: d.y }};
  }}
  return {{ x: d.x + (side === 'right' ? EX : -EX), y: d.y }};
}}

function edgePath(l) {{
  const s = nodeAnchor(l.sourceNode, 'right');
  const t = nodeAnchor(l.targetNode, 'left');
  const mx = (s.x + t.x) / 2;
  if (Math.abs(s.y - t.y) > 5) {{
    return `M${{s.x}},${{s.y}} C${{mx}},${{s.y}} ${{mx}},${{t.y}} ${{t.x}},${{t.y}}`;
  }}
  return `M${{s.x}},${{s.y}} L${{t.x}},${{t.y}}`;
}}

// Flag set by the drag handler when the user actually moved a node; the
// click handler reads it to avoid opening the popup at the end of a drag.
let dragSuppressClick = false;

const link = zoomG.append('g').selectAll('path')
  .data(linksData).join('path')
  .attr('class', 'link').attr('marker-end', 'url(#arrow)')
  .attr('d', edgePath);

const node = zoomG.append('g').selectAll('g')
  .data(nodesData).join('g')
  .attr('class', 'node')
  .attr('transform', d => `translate(${{d.x}},${{d.y}})`)
  .on('click', (e, d) => {{
    if (dragSuppressClick) {{ dragSuppressClick = false; return; }}
    showPopup(d.id);
  }});

node.each(function(d) {{
  const g = d3.select(this);
  if (d.shape === 'rect') {{
    g.append('rect').attr('width', RW).attr('height', RH).attr('x', -RW/2).attr('y', -RH/2).attr('rx', RX)
      .attr('fill', d.fill).attr('stroke', d.stroke).attr('stroke-width', 2);
    g.append('text').attr('class', 'node-label').attr('y', 4).attr('fill', '#333').text(d.label);
  }} else {{
    const rx = d.isFinal ? EX+5 : EX;
    const ry = d.isFinal ? EY+3 : EY;
    g.append('ellipse').attr('rx', rx).attr('ry', ry)
      .attr('fill', d.fill).attr('stroke', d.stroke).attr('stroke-width', d.isFinal ? 2.5 : 1.5);
    g.append('text').attr('class', 'node-label').attr('y', 4).attr('fill', 'white').text(d.label);
  }}
}});

// Drag: free movement. We use a regular function so `this` is the dragged
// SVG group, and we only redraw the incident edges (not every edge).
const drag = d3.drag()
  .on('start', function(e, d) {{
    dragSuppressClick = false;
    d3.select(this).raise();          // bring the dragged node above siblings
    d._dragStartX = d.x; d._dragStartY = d.y;
  }})
  .on('drag', function(e, d) {{
    d.x = e.x; d.y = e.y;
    d3.select(this).attr('transform', `translate(${{d.x}},${{d.y}})`);
    link.filter(l => l.sourceNode === d || l.targetNode === d).attr('d', edgePath);
  }})
  .on('end', function(e, d) {{
    // If the cursor actually moved, swallow the upcoming click so the popup
    // does not open just because the user finished dragging.
    if (Math.hypot(d.x - d._dragStartX, d.y - d._dragStartY) > 2) {{
      dragSuppressClick = true;
    }}
  }});
node.call(drag);

function fitToView() {{
  const b = zoomG.node().getBBox();
  const fw = W / (b.width  + 80);
  const fh = H / (b.height + 80);
  const s = Math.min(fw, fh, 1.6);
  const tx = (W - b.width  * s) / 2 - b.x * s;
  const ty = (H - b.height * s) / 2 - b.y * s;
  svg.transition().duration(400).call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(s));
}}

d3.select('#zoomInBtn').on('click',  () => svg.transition().call(zoomBehavior.scaleBy, 1.3));
d3.select('#zoomOutBtn').on('click', () => svg.transition().call(zoomBehavior.scaleBy, 0.7));
d3.select('#fitBtn').on('click',     fitToView);
d3.select('#resetBtn').on('click',   () => {{
  // Snap every node back to the layered-layout position and re-fit.
  nodesData.forEach(d => {{
    const p = ORIGINAL_POS.get(d.idx);
    if (p) {{ d.x = p.x; d.y = p.y; }}
  }});
  node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
  link.attr('d', edgePath);
  fitToView();
}});

// Auto-fit on first paint
requestAnimationFrame(fitToView);

function showPopup(nodeId) {{
  document.getElementById('popupContent').innerHTML = details[nodeId] || '<p>No details.</p>';
  document.getElementById('popupOverlay').classList.add('visible');
}}
function closePopup(e) {{
  if (e && e.target !== document.getElementById('popupOverlay')) return;
  document.getElementById('popupOverlay').classList.remove('visible');
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closePopup(); }});
</script>
</body>
</html>"""

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        Path(output_path).write_text(html_doc, encoding="utf-8")
        return output_path

    def _operation_node_names(self) -> List[str]:
        assert self.solution_graph is not None
        return [
            name for name in self.solution_graph.nodes()
            if self.solution_graph.nodes[name].get("node_type") == "operation"
        ]

    def _generate_function_def(self, node_name: str) -> Dict[str, Any]:
        G = self.solution_graph
        node = G.nodes[node_name]
        predecessors = list(G.predecessors(node_name))

        default_params, plain_params = "", ""
        for pred in predecessors:
            pred_node = G.nodes[pred]
            data_path = pred_node.get("data_path", "")
            if data_path:
                default_params += f"{pred}='{data_path}', "
            else:
                plain_params += f"{pred}={pred}, "

        all_params = (plain_params + default_params).rstrip(", ")
        successors = list(G.successors(node_name))
        return_line = "return " + ", ".join(successors) if successors else "return None"

        return {
            "node_name": node_name,
            "function_definition": f"{node_name}({all_params})",
            "return_line": return_line,
            "description": node.get("description", ""),
        }

    def _initialize_operations(self) -> None:
        self.operations = [self._generate_function_def(n) for n in self._operation_node_names()]

    def _ancestor_operations(self, node_name: str) -> List[Dict[str, Any]]:
        ancestors = nx.ancestors(self.solution_graph, node_name)
        op_names = set(self._operation_node_names())
        return [op for op in self.operations if op["node_name"] in ancestors and op["node_name"] in op_names]

    def _descendant_operations(self, node_name: str) -> List[Dict[str, Any]]:
        descendants = nx.descendants(self.solution_graph, node_name)
        op_names = set(self._operation_node_names())
        return [op for op in self.operations if op["node_name"] in descendants and op["node_name"] in op_names]

    def _final_output_nodes(self) -> List[Dict[str, str]]:
        out = []
        for name in self.solution_graph.nodes():
            node = self.solution_graph.nodes[name]
            if node.get("node_type") == "data" and self.solution_graph.out_degree(name) == 0:
                out.append({"name": name, "description": node.get("description", "")})
        return out


    def _operation_prompt(
        self,
        operation: Dict[str, Any],
        task: str,
        data_registry: List[Dict[str, Any]],
        workflow_code: str,
    ) -> str:
        node_name = operation["node_name"]
        ancestors = self._ancestor_operations(node_name)
        ancestor_code = "\n".join(op.get("operation_code", "") for op in ancestors)

        descendants = self._descendant_operations(node_name)
        keys = ["node_name", "description", "function_definition", "return_line"]
        descendant_defs = "\n".join(
            str({k: op[k] for k in keys if k in op}) for op in descendants
        )

        pre_requirements = [
            f"The function description is: {operation['description']}",
            f"The function definition is: {operation['function_definition']}",
            f"The function return line is: {operation['return_line']}",
        ]
        requirements = "\n".join(
            f"{i + 1}. {line}" for i, line in enumerate(pre_requirements + OPERATION_REQUIREMENTS)
        )

        return (
            f"Your role: {OPERATION_ROLE}\n\n"
            f"operation_task: You need to generate a Python function to do: {operation['description']}\n\n"
            f"This function is one step to solve the question/task: {task}\n\n"
            f"The Python code that built the workflow graph for this task is:\n{workflow_code}\n\n"
            f"Data input summary:\n{self._data_registry_summary(data_registry)}\n\n"
            f"Your reply example:\n{OPERATION_REPLY_EXAMPLE}\n\n"
            f"Your reply needs to meet these requirements:\n{requirements}\n\n"
            f"The ancestor function code (follow the generated file names and attribute names):\n{ancestor_code}\n\n"
            f"The descendant function definitions (node_name is the function name):\n{descendant_defs}"
        )

    def _assembly_prompt(self, task: str, data_registry: List[Dict[str, Any]]) -> str:
        labeled = []
        for idx, op in enumerate(self.operations):
            labeled.append(
                f"### Function {idx + 1}: {op.get('node_name')}\n"
                f"Description: {op.get('description', '')}\n"
                f"```python\n{op.get('operation_code', '')}\n```"
            )
        all_operation_code = "\n\n".join(labeled)

        final_outputs = self._final_output_nodes()
        if final_outputs:
            final_list = "\n".join(f"  - **{n['name']}**: {n['description']}" for n in final_outputs)
            final_section = (
                "\n\n## FINAL OUTPUT NODES (from the workflow graph)\n"
                "The workflow graph has the following terminal output node(s) — these are the result "
                "the user actually needs, and each MUST be written to a file in OUTPUT_DIR using "
                f"os.path.join(OUTPUT_DIR, <filename>):\n{final_list}\n\n"
                "Pick whatever file type fits the result (.gpkg, .geojson, .csv, .png, .html, .json, "
                ".txt, ...). Intermediate data does NOT need to be written to disk — pass it between "
                "functions in memory; only the final output(s) above must be persisted."
            )
        else:
            final_section = ""

        requirements = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(ASSEMBLY_REQUIREMENTS))

        return (
            f"Your role: {ASSEMBLY_ROLE}\n\n"
            f"Your task is to assemble ALL of the given Python functions below into a complete, "
            f"executable Python program that solves the question: {task}\n\n"
            f"CRITICAL: include EVERY function listed below in your assembly program. Copy each "
            f"function definition EXACTLY as given. The assembly program must define all "
            f"{len(self.operations)} functions, then call them in the correct order inside "
            f"assembely_solution().\n\n"
            f"Requirements:\n{requirements}\n\n"
            f"Data location:\n{self._data_registry_summary(data_registry)}"
            f"{final_section}\n\n"
            f"There are {len(self.operations)} functions in total. ALL must appear in your code:\n\n"
            f"{all_operation_code}"
        )


    @staticmethod
    def _extract_code(response_content: str) -> str:
        match = re.search(r"```(?:python)?\s*(.*?)```", response_content, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
        return response_content.strip()

    def _llm_complete(self, system_prompt: str, model: str) -> str:
        """Run a single chat completion and return the model's text response.

        Token usage is accumulated on ``self.input_tokens`` /
        ``self.output_tokens``; the per-call LLM count is incremented.
        Progress reporting is left to the caller (each pipeline stage emits its
        own ``llm_generation`` event) — same convention used by the other GAS
        agents in this server.
        """
        self.increment_llm_calls()
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_prompt}],
        )
        usage = getattr(response, "usage", None)
        if usage:
            self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
        return response.choices[0].message.content or ""



    def _generate_operation_code(
        self,
        task: str,
        data_registry: List[Dict[str, Any]],
        workflow_code: str,
        progress_callback: Optional[ProgressCallback],
    ) -> None:
        op_model = self.request_parameters.get("operation_model") or self.DEFAULT_OPERATION_MODEL
        for idx, op in enumerate(self.operations):
            node_name = op["node_name"]
            self._emit_progress(
                progress_callback,
                stage="llm_generation",
                message=f"I am generating code for operation {idx + 1}/{len(self.operations)}: {node_name}.",
                data={"node_name": node_name, "operation_index": idx + 1},
            )
            system_prompt = self._operation_prompt(op, task, data_registry, workflow_code)
            try:
                content = self._llm_complete(system_prompt, op_model)
                op["response"] = content
                op["operation_code"] = self._extract_code(content)
            except Exception as exc:
                if self.debug:
                    logging.warning("Operation code generation failed for %s: %s", node_name, exc)
                op["response"] = ""
                op["operation_code"] = ""
                self.increment_retries()

    def _generate_assembly_code(
        self,
        task: str,
        data_registry: List[Dict[str, Any]],
        progress_callback: Optional[ProgressCallback],
    ) -> str:
        self.assembly_prompt = self._assembly_prompt(task, data_registry)
        self._emit_progress(
            progress_callback,
            stage="llm_generation",
            message="I am assembling the generated operation functions into a single executable program.",
        )
        asm_model = self.request_parameters.get("assembly_model") or self.model or self.DEFAULT_ASSEMBLY_MODEL
        try:
            content = self._llm_complete(self.assembly_prompt, asm_model)
            self.assembly_code = self._extract_code(content)
        except Exception as exc:
            if self.debug:
                logging.warning("Assembly LLM call failed: %s", exc)
            self.assembly_code = ""
        return self.assembly_code



    # Invocation of the assembled entry point, e.g. ``assembely_solution()``,
    # ``result = assembely_solution()`` or ``print(assembely_solution())`` — but NOT the
    # ``def assembely_solution():`` line. Captures the leading indentation so we can
    # replace the call with ``pass`` (preserving block structure) and invoke the function
    # ourselves to capture its return value without executing the workflow twice.
    _ENTRYPOINT_CALL_RE = re.compile(
        r"(?m)^(?P<indent>[ \t]*)(?!def\b)[^\n]*\bassembely_solution\s*\(\s*\)[^\n]*$"
    )

    @classmethod
    def _strip_entrypoint_call(cls, code: str) -> tuple[str, bool]:
        """Return ``(code_without_call, had_call)`` for the assembly entry point.

        The call is replaced with an indented ``pass`` rather than deleted, so removing
        a guarded call (e.g. inside ``if __name__ == "__main__":`` — which the LLM is
        told not to write but sometimes does) cannot leave an empty block behind.
        """
        had_call = bool(cls._ENTRYPOINT_CALL_RE.search(code))
        if not had_call:
            return code, False
        stripped = cls._ENTRYPOINT_CALL_RE.sub(lambda m: f"{m.group('indent')}pass", code)
        return stripped, True

    @staticmethod
    def _existing_paths_from_value(value: Any) -> List[str]:
        """Collect existing file paths referenced anywhere in a return value.

        ``assembely_solution()`` typically returns the final output path, or a
        dict/list of them. We walk the structure and keep strings that point at
        a real file so the terminal output is captured even when the mtime
        scanner misses it (e.g. the file was written outside ``OUTPUT_DIR``).
        """
        found: List[str] = []

        def visit(v: Any) -> None:
            if isinstance(v, str):
                try:
                    if os.path.isfile(v):
                        found.append(os.path.normpath(os.path.abspath(v)))
                except (OSError, ValueError):
                    pass
            elif isinstance(v, dict):
                for item in v.values():
                    visit(item)
            elif isinstance(v, (list, tuple, set)):
                for item in v:
                    visit(item)

        visit(value)
        return found

    @staticmethod
    def _snapshot_tracked_mtimes(output_dir: str) -> Dict[str, float]:
        """Map ``normalized path -> mtime`` for tracked output files under ``output_dir``."""
        snapshot: Dict[str, float] = {}
        for ext in TRACKED_OUTPUT_EXTENSIONS:
            for fp in globmod.glob(os.path.join(output_dir, "**", ext), recursive=True):
                try:
                    snapshot[os.path.normpath(fp)] = os.path.getmtime(fp)
                except OSError:
                    continue
        return snapshot

    def _execute_assembly(self, code: str, output_dir: str) -> Dict[str, Any]:
        self.increment_code_executions()
        os.makedirs(output_dir, exist_ok=True)

        # Baseline of tracked files that existed before assembly ran. Seeded ONCE per
        # run (before the first attempt) so files written by an earlier *failed* attempt
        # are still reported as created by the repair/regen attempt that ultimately
        # succeeds. Re-snapshotting per attempt would fold those files into the baseline
        # and drop them from the artifacts — the "debug loses outputs" bug. Falls back to
        # a local snapshot when no per-run baseline was seeded.
        before_mtimes = (
            self._output_baseline_mtimes
            if self._output_baseline_mtimes is not None
            else self._snapshot_tracked_mtimes(output_dir)
        )

        def detect_created_files() -> List[str]:
            found = []
            for ext in TRACKED_OUTPUT_EXTENSIONS:
                for fp in globmod.glob(os.path.join(output_dir, "**", ext), recursive=True):
                    norm = os.path.normpath(fp)
                    try:
                        cur = os.path.getmtime(fp)
                    except OSError:
                        continue
                    if norm not in before_mtimes or cur > before_mtimes[norm]:
                        found.append(norm)
            return sorted(set(found))

        def merge_outputs(final_paths: List[str]) -> List[str]:
            """Final-output paths (from the entry point's return) first, then any
            other detected files, de-duplicated by normalized path."""
            ordered: List[str] = []
            seen: set[str] = set()
            for p in final_paths + detect_created_files():
                norm = os.path.normpath(p)
                if norm not in seen:
                    seen.add(norm)
                    ordered.append(norm)
            return ordered

        # Strip the script's own ``assembely_solution()`` call so we can invoke it
        # ourselves and capture the terminal node's return value (single execution).
        exec_code, had_entrypoint = self._strip_entrypoint_call(code)
        stdout_capture = io.StringIO()
        exec_globals: Dict[str, Any] = {
            "__builtins__": __builtins__,
            # Under bare exec(), an unset __name__ resolves to "builtins", so any
            # ``if __name__ == "__main__":`` guard the LLM added (despite being told not
            # to) silently evaluates False and the entry point never runs. Force it to
            # "__main__" so such guards execute. We still strip the in-script call above
            # and invoke the entry point ourselves, so this never double-executes.
            "__name__": "__main__",
            "OUTPUT_DIR": output_dir,
            # Backward-compat alias: previously generated scripts may still reference
            # FINAL_OUTPUT_DIR. Point it at OUTPUT_DIR so they keep running. New code
            # is prompted to use OUTPUT_DIR only.
            "FINAL_OUTPUT_DIR": output_dir,
        }
        prev_cwd = os.getcwd()
        try:
            try:
                os.chdir(output_dir)
            except OSError:
                pass
            try:
                returned_value: Any = None
                with contextlib.redirect_stdout(stdout_capture):
                    exec(exec_code, exec_globals)
                    entrypoint = exec_globals.get("assembely_solution")
                    if had_entrypoint and callable(entrypoint):
                        returned_value = entrypoint()
                final_paths = self._existing_paths_from_value(returned_value)
                return {
                    "status": "completed",
                    "output": stdout_capture.getvalue(),
                    "code": code,
                    "created_files": merge_outputs(final_paths),
                    "result_value": returned_value if isinstance(returned_value, (str, int, float, bool, dict, list)) else None,
                }
            except Exception as err:
                exc_type = type(err).__name__
                return {
                    "status": "error",
                    "output": stdout_capture.getvalue(),
                    "code": code,
                    "error": str(err) or exc_type,
                    "exception_type": exc_type,
                    "error_traceback": traceback.format_exc(),
                    "created_files": detect_created_files(),
                }
        finally:
            try:
                os.chdir(prev_cwd)
            except OSError:
                pass



    @staticmethod
    def _classify_error(exception_type: str) -> str:
        for label, exceptions in ERROR_TAXONOMY.items():
            if exception_type in exceptions:
                return label
        return "unknown"

    def _repair_prompt(
        self,
        failed_code: str,
        result: Dict[str, Any],
        task: str,
        data_registry: List[Dict[str, Any]],
    ) -> str:
        error_class = self._classify_error(result.get("exception_type", ""))
        return (
            f"Your role: {ASSEMBLY_ROLE}\n\n"
            "The previous assembly program failed at runtime. Diagnose the failure and return a "
            "corrected, COMPLETE assembly program that resolves it. Keep the original structure "
            "(all operation functions + an assembely_solution() entry point) and the OUTPUT_DIR "
            "convention. Reply with a single Python code block only.\n\n"
            f"User question/task: {task}\n\n"
            f"Error category: {error_class}\n"
            f"Exception type: {result.get('exception_type', '')}\n"
            f"Error message: {result.get('error', '')}\n\n"
            f"Captured stdout (most recent):\n{(result.get('output') or '')[-1500:]}\n\n"
            f"Traceback:\n{(result.get('error_traceback') or '')[-2000:]}\n\n"
            f"Data registry:\n{self._data_registry_summary(data_registry)}\n\n"
            "Failed assembly code:\n```python\n"
            f"{failed_code}\n```"
        )

    def _attempt_repair(
        self,
        failed_code: str,
        result: Dict[str, Any],
        task: str,
        data_registry: List[Dict[str, Any]],
        out_dir: str,
        progress_callback: Optional[ProgressCallback],
    ) -> Optional[Dict[str, Any]]:
        self.increment_retries()
        self._emit_progress(
            progress_callback,
            stage="retry",
            message="The assembly run failed; I will ask the LLM to repair the code and re-run.",
            data={"exception_type": result.get("exception_type", "")},
        )
        asm_model = self.request_parameters.get("assembly_model") or self.model or self.DEFAULT_ASSEMBLY_MODEL
        try:
            content = self._llm_complete(
                self._repair_prompt(failed_code, result, task, data_registry),
                asm_model,
            )
        except Exception as exc:
            if self.debug:
                logging.warning("Repair LLM call failed: %s", exc)
            return None
        repaired = self._extract_code(content)
        if not repaired:
            return None
        repaired = self._inject_missing_operations(repaired)
        code_file = os.path.join(out_dir, f"repaired_assembly_{uuid.uuid4().hex[:8]}.py")
        Path(code_file).write_text(repaired, encoding="utf-8")
        self._emit_progress(
            progress_callback,
            stage="code_execution",
            message="I am re-executing the repaired assembly program.",
            data={"code_file": code_file},
        )
        run = self._execute_assembly(repaired, out_dir)
        run["code_file"] = code_file
        run["code"] = repaired
        return run

    def _attempt_regenerate(
        self,
        prior_failure: Dict[str, Any],
        task: str,
        data_registry: List[Dict[str, Any]],
        out_dir: str,
        progress_callback: Optional[ProgressCallback],
    ) -> Optional[Dict[str, Any]]:
        self.increment_retries()
        self._emit_progress(
            progress_callback,
            stage="fallback_start",
            message="The repair attempt did not resolve the failure; I am regenerating the assembly from scratch with error context.",
        )
        regen_hint = (
            "\n\n## IMPORTANT — Previous Attempts Failed\n"
            f"A prior assembly attempt failed with {prior_failure.get('exception_type', '')}: "
            f"{prior_failure.get('error', '')}\n"
            f"Traceback excerpt:\n{(prior_failure.get('error_traceback') or '')[-1500:]}\n\n"
            "Generate a COMPLETELY NEW implementation that avoids this failure. Do NOT reuse the "
            "same approach — try an alternative strategy.\n"
        )
        regen_prompt = self._assembly_prompt(task, data_registry) + regen_hint
        asm_model = self.request_parameters.get("assembly_model") or self.model or self.DEFAULT_ASSEMBLY_MODEL
        try:
            content = self._llm_complete(regen_prompt, asm_model)
        except Exception as exc:
            if self.debug:
                logging.warning("Regeneration LLM call failed: %s", exc)
            return None
        regenerated = self._extract_code(content)
        if not regenerated:
            return None
        regenerated = self._inject_missing_operations(regenerated)
        code_file = os.path.join(out_dir, f"regenerated_assembly_{uuid.uuid4().hex[:8]}.py")
        Path(code_file).write_text(regenerated, encoding="utf-8")
        self._emit_progress(
            progress_callback,
            stage="code_execution",
            message="I am executing the regenerated assembly program.",
            data={"code_file": code_file},
        )
        run = self._execute_assembly(regenerated, out_dir)
        run["code_file"] = code_file
        run["code"] = regenerated
        if run.get("status") == "completed":
            self._emit_progress(
                progress_callback,
                stage="fallback_complete",
                message="The regenerated assembly program executed successfully.",
            )
        return run

  

    def _inject_missing_operations(self, assembly_code: str) -> str:
        """Prepend any operation function the LLM omitted from the assembly."""
        defined = set(re.findall(r"^def\s+(\w+)\s*\(", assembly_code, re.MULTILINE))
        missing_blocks = []
        for op in self.operations:
            code = (op.get("operation_code") or "").strip()
            if not code:
                continue
            match = re.search(r"^def\s+(\w+)\s*\(", code, re.MULTILINE)
            fn_name = match.group(1) if match else None
            if fn_name and fn_name in defined:
                continue
            missing_blocks.append(f"# --- {op['node_name']} ---\n{code}")
        if not missing_blocks:
            return assembly_code
        return "\n\n".join(missing_blocks) + "\n\n" + assembly_code

    def _environment_info(self) -> Dict[str, Any]:
        return {
            "python_version": platform.python_version(),
            "domain-specific libraries": ["networkx", "geopandas", "pandas", "matplotlib"],
        }

    def _summary_text(self, result: Dict[str, Any], created: List[str]) -> str:
        if result.get("status") == "completed":
            paths = created
            if paths:
                return (
                    f"Executed a {len(self.operations)}-operation workflow and produced "
                    f"{len(paths)} artifact(s)."
                )
            return f"Executed a {len(self.operations)}-operation workflow; no files were detected."
        return f"Workflow execution failed: {result.get('error', '')}"

    def _error_response(
        self,
        query: str,
        dataset_paths: List[str],
        start_time: float,
        message: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        if progress_callback is not None:
            self._emit_progress(
                progress_callback,
                stage="error",
                message=message,
            )
        duration = round(time.time() - start_time, 2)
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": duration,
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "error": message,
            "inputs": {"text": query, "dataset_path": dataset_paths, "parameters": dict(self.request_parameters)},
            "outputs": {"text": message, "dataset_path": None, "dataset_paths": []},
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.code_executions,
                "number_of_artifacts": 0,
            },
            "environment": self._environment_info(),
            "complementary": {
                "Execution": {
                    "Inputs": {"text": query, "dataset_paths": dataset_paths},
                    "Outputs": {},
                    "Error": {"message": message},
                },
                "Provenance": {
                    "Lineage": ["Failed before producing artifacts."],
                    "Tool Calls": {"count": self.code_executions},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Artifacts and Logs": {"Inline Artifacts": {}, "Persisted Artifacts": {}},
            },
        }


    def run(
        self,
        query: str,
        input_dataset_paths: Optional[list[str] | str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        start_time = time.time()
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.reset_metrics()

        # Reset per-run state
        self.solution_graph = None
        self.operations = []
        self.assembly_prompt = None
        self.assembly_code = ""
        self.workflow_code = ""
        self._output_baseline_mtimes = None

        self._emit_progress(
            progress_callback,
            stage="start",
            message=(
                "I will audit the input datasets, design a workflow DAG, generate code for each "
                "operation, assemble them into one program, and execute the program in a sandbox."
            ),
            data={"dataset_count": len(dataset_paths)},
        )

        if not dataset_paths:
            return self._error_response(
                query, dataset_paths, start_time,
                "No input datasets supplied — the spatial analysis agent needs at least one dataset.",
                progress_callback,
            )

        # Task-scoped output directory — every artifact (final and intermediate) lands here.
        task_id = uuid.uuid4().hex[:8]
        out_dir = str(self.ensure_directory(Path(self.output_dir) / task_id))

        # ── Stage 1: Data registry ────────────────────────────────────
        data_registry = self.request_parameters.get("data_registry")
        registry_was_supplied = isinstance(data_registry, list) and bool(data_registry)
        if not registry_was_supplied:
            try:
                data_registry = self._build_data_registry(dataset_paths, progress_callback)
            except Exception as exc:
                return self._error_response(
                    query, dataset_paths, start_time, f"Dataset audit failed: {exc}", progress_callback,
                )
        else:
            self._emit_progress(
                progress_callback,
                stage="input_inspection",
                message="I used the pre-supplied data registry and skipped the dataset audit stage.",
                data={"registry_size": len(data_registry)},
            )
        if not data_registry:
            return self._error_response(
                query, dataset_paths, start_time,
                "None of the supplied datasets could be inspected — they may be missing or unsupported.",
                progress_callback,
            )

        # ── Stage 2: Workflow graph ───────────────────────────────────
        graph_path = self.request_parameters.get("workflow_graph_path")
        workflow_code = str(self.request_parameters.get("workflow_code") or "")
        graph_was_supplied = bool(graph_path)
        if not graph_path:
            for path in dataset_paths:
                if str(path).lower().endswith((".graphml", ".gml")):
                    graph_path = path
                    graph_was_supplied = True
                    break

        if not graph_path:
            try:
                graph_path, workflow_code = self._generate_workflow_graph(
                    query, data_registry, out_dir, progress_callback,
                )
            except Exception as exc:
                if self.debug:
                    logging.warning("Workflow generation failed: %s", exc)
                return self._error_response(
                    query, dataset_paths, start_time, f"Workflow generation failed: {exc}", progress_callback,
                )
        else:
            self._emit_progress(
                progress_callback,
                stage="planning",
                message="I used the pre-supplied workflow graph and skipped the workflow design stage.",
                data={"workflow_graph_path": graph_path},
            )
        self.workflow_code = workflow_code

        try:
            self._load_solution_graph(graph_path)
        except Exception as exc:
            return self._error_response(
                query, dataset_paths, start_time, f"Failed to load workflow graph: {exc}", progress_callback,
            )

        # Render an interactive HTML visualization of the workflow DAG.
        workflow_html_path: Optional[str] = None
        try:
            workflow_html_path = os.path.join(
                os.path.dirname(graph_path) or out_dir,
                Path(graph_path).stem + ".html",
            )
            self._render_workflow_html(
                self.solution_graph,
                workflow_html_path,
                title="Geoprocessing Workflow",
                research_question=query,
            )
            self._emit_progress(
                progress_callback,
                stage="artifact_generation",
                message=(
                    "I rendered an interactive HTML visualization of the workflow DAG: "
                    f"{workflow_html_path}"
                ),
                data={"workflow_graph_html": workflow_html_path},
            )
        except Exception as exc:
            if self.debug:
                logging.warning("Workflow HTML rendering failed: %s", exc)
            workflow_html_path = None

        self._initialize_operations()
        if not self.operations:
            return self._error_response(
                query, dataset_paths, start_time,
                "The workflow graph contains no operation nodes.",
                progress_callback,
            )

        # ── Stage 3: Per-operation code generation + assembly ─────────
        self._generate_operation_code(query, data_registry, workflow_code, progress_callback)

        self._emit_progress(
            progress_callback,
            stage="planning",
            message="I am assembling the generated operation functions into a single executable program.",
            data={"operation_count": len(self.operations)},
        )
        assembly_code = self._generate_assembly_code(query, data_registry, progress_callback)
        if not assembly_code:
            return self._error_response(
                query, dataset_paths, start_time, "Assembly code generation failed.", progress_callback,
            )
        assembly_code = self._inject_missing_operations(assembly_code)

        code_file = os.path.join(out_dir, f"generated_assembly_{uuid.uuid4().hex[:8]}.py")
        Path(code_file).write_text(assembly_code, encoding="utf-8")

        # ── Stage 4: Execute (with repair + regen fallback) ───────────
        # Seed the tracked-output baseline ONCE, before the first attempt, so that
        # files written by a failed attempt are still attributed to the repair/regen
        # attempt that ultimately succeeds (otherwise debugged runs drop artifacts).
        self._output_baseline_mtimes = self._snapshot_tracked_mtimes(out_dir)

        self._emit_progress(
            progress_callback,
            stage="code_execution",
            message="I am executing the assembly program in the sandboxed environment.",
            data={"code_file": code_file},
        )
        result = self._execute_assembly(assembly_code, out_dir)
        result["code_file"] = code_file
        result["code"] = assembly_code

        repair_history: List[Dict[str, Any]] = []
        if result.get("status") == "error":
            repair_history.append({
                "stage": "initial",
                "exception_type": result.get("exception_type", ""),
                "error": result.get("error", ""),
                "code_file": code_file,
            })
            repaired = self._attempt_repair(
                assembly_code, result, query, data_registry, out_dir, progress_callback,
            )
            if repaired:
                repair_history.append({
                    "stage": "repair",
                    "status": repaired.get("status"),
                    "exception_type": repaired.get("exception_type", ""),
                    "error": repaired.get("error", ""),
                    "code_file": repaired.get("code_file"),
                })
                if repaired.get("status") == "completed":
                    result = repaired
                else:
                    regenerated = self._attempt_regenerate(
                        repaired, query, data_registry, out_dir, progress_callback,
                    )
                    if regenerated:
                        repair_history.append({
                            "stage": "regenerate",
                            "status": regenerated.get("status"),
                            "exception_type": regenerated.get("exception_type", ""),
                            "error": regenerated.get("error", ""),
                            "code_file": regenerated.get("code_file"),
                        })
                        result = regenerated
            else:
                regenerated = self._attempt_regenerate(
                    result, query, data_registry, out_dir, progress_callback,
                )
                if regenerated:
                    repair_history.append({
                        "stage": "regenerate",
                        "status": regenerated.get("status"),
                        "exception_type": regenerated.get("exception_type", ""),
                        "error": regenerated.get("error", ""),
                        "code_file": regenerated.get("code_file"),
                    })
                    result = regenerated

        assembly_code = result.get("code", assembly_code)
        code_file = result.get("code_file", code_file)

        # ── Build response ────────────────────────────────────────────
        created = result.get("created_files", []) or []
        primary_output = created[0] if created else None
        self.set_artifact_count(len(created))

        summary = self._summary_text(result, created)
        self._emit_progress(
            progress_callback,
            stage="complete",
            message=summary,
            data={
                "status": result.get("status"),
                "artifact_count": self.number_of_artifacts,
            },
        )

        duration = round(time.time() - start_time, 2)
        operation_lineage = [
            {"node_name": op["node_name"], "description": op.get("description", "")}
            for op in self.operations
        ]
        lineage_steps = [
            "Audited input datasets and built data registry."
            if not registry_was_supplied else "Used pre-supplied data registry.",
            "Generated workflow DAG via LLM."
            if not graph_was_supplied else "Used pre-supplied workflow graph.",
            f"Generated code for {len(self.operations)} operation node(s).",
            "Assembled operations into a single executable program.",
            f"Executed the assembly program (status: {result.get('status')}).",
        ]

        complementary: Dict[str, Any] = {
            "Execution": {
                "Inputs": {
                    "task": query,
                    "dataset_paths": dataset_paths,
                    "workflow_graph_path": graph_path,
                    "data_registry": data_registry,
                },
                "Outputs": {
                    "summary": summary,
                    "status": result.get("status"),
                    "primary_artifact": primary_output,
                    "dataset_paths": created,
                    "result_value": result.get("result_value"),
                    "stdout": (result.get("output") or "")[:4000],
                },
            },
            "Provenance": {
                "Lineage": lineage_steps,
                "Tool Calls": {
                    "dataset_audits": 0 if registry_was_supplied else len(data_registry),
                    "workflow_graph_generations": 0 if graph_was_supplied else 1,
                    "operation_code_generations": len(self.operations),
                    "assembly_code_generations": 1,
                    "code_executions": self.code_executions,
                },
                "LLM Calls": {"count": self.llm_calls},
                "Operation Lineage": operation_lineage,
            },
            "Artifacts and Logs": {
                "Inline Artifacts": {
                    "assembly_code_file": code_file,
                    "workflow_graph_file": graph_path,
                    "workflow_graph_html": workflow_html_path,
                    # Same path under a "*_file" key so the service layer relocates it
                    # into the agent data dir and delivers it as a downloadable URL
                    # artifact (the key above is kept for in-process callers).
                    "workflow_graph_html_file": workflow_html_path,
                    "workflow_generator_code": workflow_code,
                },
                "Persisted Artifacts": {
                    "paths": created,
                    "output_dir": out_dir,
                },
            },
            "Validation": {
                "status": "passed" if result.get("status") == "completed" else "failed",
                "checks": [
                    f"Assembly execution status: {result.get('status')}",
                    f"Artifact files detected: {len(created)}",
                ],
            },
        }
        if result.get("status") == "error":
            complementary["Execution"]["Error"] = {
                "message": result.get("error", ""),
                "type": result.get("exception_type", ""),
                "category": self._classify_error(result.get("exception_type", "")),
                "traceback": (result.get("error_traceback") or "")[:4000],
            }
        if repair_history:
            complementary["Execution"]["RepairHistory"] = repair_history
            complementary["Provenance"]["Tool Calls"]["repair_attempts"] = sum(
                1 for h in repair_history if h.get("stage") in {"repair", "regenerate"}
            )

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": duration,
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {
                "text": query,
                "dataset_paths": dataset_paths,
                "parameters": dict(self.request_parameters),
            },
            "outputs": {
                "text": summary,
                "dataset_path": primary_output,
                "dataset_paths": created,
                "dataset_size": {"type": "workflow_artifacts", "feature_count": None, "dimensions": None},
            },
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": self.code_executions,
                "number_of_artifacts": self.number_of_artifacts,
            },
            "environment": self._environment_info(),
            "script": assembly_code,
            "complementary": complementary,
        }
