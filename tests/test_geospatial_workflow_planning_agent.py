import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from gas_server.agents import geospatial_workflow_planning_agent
from gas_server.agents.geospatial_workflow_planning_agent import GeospatialWorkflowPlanningAgent
from gas_server.core.service_core import _build_task_payload, _extract_text_and_input_dataset_paths, _gas_execute_task_to_run_payload
from gas_server.services.geospatial_workflow_planning_agent_service import get_service_app


class FakeCompletions:
    def create(self, **kwargs):
        content = json.dumps(
            {
                "schema_version": "1.0.0",
                "readiness": "ready_with_warnings",
                "summary": "Plan a data retrieval, raster processing, vector sampling, and mapping workflow.",
                "workflow_steps": [
                    {
                        "step_id": "download_boundary",
                        "order": 1,
                        "title": "Download county boundary",
                        "purpose": "Get the county polygon used for clipping and sampling.",
                        "agent_id": "geospatial_data_retrieval_agent",
                        "agent_name": "Geospatial Data Retrieval Agent",
                        "gas_server_base_url": "https://www.geospatial-agentic-services.online",
                        "operation": "execute_task",
                        "recommended_mode": "sync",
                        "instructions": "Download the Richland County, South Carolina boundary.",
                        "depends_on": [],
                        "input_from_steps": [],
                        "expected_outputs": ["County boundary vector dataset"],
                        "validation_checks": ["Confirm the boundary has polygon geometry and CRS."],
                        "credentials_required": ["OPENAI_API_KEY"],
                        "confidence": "high",
                        "notes": [],
                    },
                    {
                        "step_id": "clip_dem",
                        "order": 2,
                        "title": "Clip DEM",
                        "purpose": "Clip the DEM to the county boundary.",
                        "agent_id": "raster_agent",
                        "agent_name": "Raster Agent",
                        "gas_server_base_url": "https://www.geospatial-agentic-services.online",
                        "operation": "execute_task",
                        "recommended_mode": "async",
                        "instructions": "Clip the DEM to the county boundary.",
                        "depends_on": ["download_boundary"],
                        "input_from_steps": ["download_boundary"],
                        "expected_outputs": ["Clipped DEM GeoTIFF"],
                        "validation_checks": ["Confirm raster and county boundary overlap."],
                        "credentials_required": ["OPENAI_API_KEY"],
                        "confidence": "high",
                        "notes": [],
                    },
                ],
                "unmatched_steps": [],
                "credentials_required": [{"agent_id": "raster_agent", "credential": "OPENAI_API_KEY"}],
                "validation_plan": ["Check CRS compatibility before clipping."],
                "assumptions": ["A DEM-capable data source is available."],
                "limitations": ["The plan is not executed by the planning agent."],
            }
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )


class TemperatureRejectingCompletions(FakeCompletions):
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise RuntimeError(
                "Unsupported value: 'temperature' does not support 0.1 with this model. "
                "Only the default (1) value is supported."
            )
        return super().create(**kwargs)


def test_geospatial_workflow_planning_agent_generates_selected_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_workflow_planning_agent, "DATA_DIR", tmp_path / "Data")
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    agent.set_request_parameters(
        {
            "plan_outputs": [
                "workflow_json",
                "human_readable",
                "gas_client_python",
                "notebook_skeleton",
                "interactive_workflow_graph",
            ],
            "plan_detail": "executable",
        }
    )
    events = []

    result = agent.run(
        "Plan a workflow to download a DEM, clip it, sample elevations, make a histogram, and map points.",
        progress_callback=events.append,
    )

    stages = {event["stage"] for event in events}
    assert "source_selection" in stages
    assert "planning" in stages
    assert "artifact_generation" in stages
    assert "complete" in stages
    assert result["outputs"]["workflow_plan"]["workflow_steps"][0]["agent_id"] == "geospatial_data_retrieval_agent"
    assert result["outputs"]["workflow_plan"]["planning_detail_type"] == "executable"
    assert result["outputs"]["workflow_plan_file"].endswith(".json")
    assert result["outputs"]["human_readable_plan_file"].endswith(".md")
    assert result["outputs"]["gas_client_python_file"].endswith(".py")
    assert result["outputs"]["notebook_skeleton_file"].endswith(".ipynb")
    assert result["outputs"]["interactive_workflow_graph_file"].endswith(".html")
    graph_html = result["outputs"]["interactive_workflow_graph_file"]
    graph_text = open(graph_html, encoding="utf-8").read()
    assert "node_type" in graph_text
    assert "function relaxLayout" in graph_text
    assert "function animateRelax" in graph_text
    assert "zoom-in" in graph_text
    assert "toggle-details" in graph_text
    assert "curve-edges" in graph_text
    assert "gravity-force" in graph_text
    assert "buildOperationCode" in graph_text
    assert "buildDataNodeCode" in graph_text
    assert "wheel" in graph_text
    assert "Data Details" in graph_text
    assert ".blur()" in graph_text
    assert "document.createElement('div')" in graph_text
    assert "releasePointerCapture" in graph_text
    assert "save-png" in graph_text
    assert "exportGraphPng" in graph_text
    assert "canvas.toBlob" in graph_text
    assert "drawArrowHead" in graph_text
    assert "shrinkFontToFit" in graph_text
    assert "context.clip()" in graph_text
    assert "User workflow goal" in graph_text
    assert "Final result" in graph_text
    assert "Planning Type: Executable" in graph_text
    assert "marker-end" in graph_text
    assert result["metrics"]["llm_calls"] == 1
    assert result["total_tokens"] == 150


def test_geospatial_workflow_planning_agent_uses_reasoning_model_by_default():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    assert agent.model == "gpt-5.2"


def test_geospatial_workflow_planning_agent_retries_without_temperature(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_workflow_planning_agent, "DATA_DIR", tmp_path / "Data")
    completions = TemperatureRejectingCompletions()
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    agent.set_request_parameters({"plan_outputs": ["workflow_json"]})

    result = agent.run("Plan a workflow from GAS capabilities.")

    assert len(completions.calls) == 2
    assert completions.calls[0]["temperature"] == 0.1
    assert "temperature" not in completions.calls[1]
    assert result["metrics"]["llm_calls"] == 1
    assert result["outputs"]["workflow_plan"]["readiness"] == "ready_with_warnings"
    assert result["stochasticity"]["controls"] == ["provider default temperature"]
    assert "fallback" not in " ".join(result["outputs"]["workflow_plan"].get("limitations", [])).lower()


def test_geospatial_workflow_planning_agent_builds_data_operation_graph():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    plan = {
        "user_goal": "Create a workflow.",
        "workflow_steps": [
            {
                "step_id": "download_data",
                "order": 1,
                "title": "Download data",
                "purpose": "Collect input data.",
                "expected_outputs": ["County boundary"],
            },
            {
                "step_id": "analyze_data",
                "order": 2,
                "title": "Analyze data",
                "purpose": "Run analysis.",
                "input_from_steps": ["download_data"],
                "expected_outputs": ["Analysis result"],
            },
        ],
    }

    graph = agent._build_networkx_workflow_graph(plan)

    assert graph.nodes["operation:download_data"]["node_type"] == "operation"
    assert graph.nodes["data:download_data:output_1"]["node_type"] == "data"
    assert ("operation:download_data", "operation:analyze_data") not in graph.edges
    assert ("operation:download_data", "data:download_data:output_1") in graph.edges
    assert ("data:download_data:output_1", "operation:analyze_data") in graph.edges


def test_geospatial_workflow_planning_agent_repairs_disconnected_downstream_dependencies():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    plan = {
        "workflow_steps": [
            {
                "step_id": "county_boundary",
                "order": 1,
                "title": "Download county boundary",
                "agent_id": "geospatial_data_retrieval_agent",
                "purpose": "Provides the county boundary.",
                "expected_outputs": ["Centre County boundary GeoPackage"],
            },
            {
                "step_id": "parcels",
                "order": 2,
                "title": "Download parcel centroids",
                "agent_id": "pasda_agent",
                "purpose": "Provides parcel centroids.",
                "expected_outputs": ["Centre County parcels GeoPackage"],
            },
            {
                "step_id": "hospitals",
                "order": 3,
                "title": "Download hospitals",
                "agent_id": "geospatial_data_retrieval_agent",
                "purpose": "Provides hospitals.",
                "expected_outputs": ["Hospitals GeoPackage"],
            },
            {
                "step_id": "nearest_hospital",
                "order": 4,
                "title": "Compute nearest hospital distance",
                "agent_id": "vector_analysis_agent",
                "purpose": "Core analysis: restrict hospitals to within the county boundary, then compute nearest hospital distance for parcel points.",
                "expected_outputs": ["Centre County parcels with nearest hospital distance"],
            },
            {
                "step_id": "distance_map",
                "order": 5,
                "title": "Create distance cluster map",
                "agent_id": "mapping_agent",
                "purpose": "Produce the requested cartographic output showing parcel clusters and nearest hospital distances.",
                "expected_outputs": ["Parcels by nearest hospital distance cluster map PNG"],
            },
        ],
        "unmatched_steps": [],
    }

    normalized = agent._normalize_plan(plan, "Map parcels by nearest hospital distance.", [])
    steps = {step["step_id"]: step for step in normalized["workflow_steps"]}

    assert set(steps["nearest_hospital"]["input_from_steps"]) == {
        "county_boundary",
        "parcels",
        "hospitals",
    }
    assert "nearest_hospital" in steps["distance_map"]["input_from_steps"]

    graph = agent._build_networkx_workflow_graph(normalized)
    assert nx.is_weakly_connected(graph)
    assert ("data:county_boundary:output_1", "operation:nearest_hospital") in graph.edges
    assert ("data:parcels:output_1", "operation:nearest_hospital") in graph.edges
    assert ("data:hospitals:output_1", "operation:nearest_hospital") in graph.edges
    assert ("data:nearest_hospital:output_1", "operation:distance_map") in graph.edges


def test_geospatial_workflow_planning_agent_keeps_independent_retrieval_branches_parallel():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    plan = {
        "workflow_steps": [
            {
                "step_id": "earthquakes",
                "order": 1,
                "title": "Download recent earthquakes",
                "agent_id": "geospatial_data_retrieval_agent",
                "purpose": "Acquire recent earthquake point events.",
                "expected_outputs": ["California recent earthquakes point dataset"],
            },
            {
                "step_id": "tracts",
                "order": 2,
                "title": "Download tract polygons",
                "agent_id": "geospatial_data_retrieval_agent",
                "purpose": "Obtain tract polygons to summarize earthquake counts.",
                "expected_outputs": ["California census tract polygons"],
            },
            {
                "step_id": "count_by_tract",
                "order": 3,
                "title": "Count earthquakes by tract",
                "agent_id": "vector_analysis_agent",
                "purpose": "Spatially count earthquake points within tract polygons.",
                "expected_outputs": ["Tracts with earthquake counts"],
            },
        ],
    }

    normalized = agent._normalize_plan(plan, "Summarize recent California earthquakes by tract.", [])
    steps = {step["step_id"]: step for step in normalized["workflow_steps"]}
    graph = agent._build_networkx_workflow_graph(normalized)

    assert steps["earthquakes"]["input_from_steps"] == []
    assert steps["tracts"]["input_from_steps"] == []
    assert set(steps["count_by_tract"]["input_from_steps"]) == {"earthquakes", "tracts"}
    assert ("data:earthquakes:output_1", "operation:tracts") not in graph.edges
    assert ("data:user_goal", "operation:earthquakes") in graph.edges
    assert ("data:user_goal", "operation:tracts") in graph.edges


def test_geospatial_workflow_planning_agent_removes_invalid_dependencies_from_retrieval_steps():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    plan = {
        "workflow_steps": [
            {
                "step_id": "parcels",
                "order": 1,
                "title": "Obtain Centre County parcel polygons",
                "agent_id": "geospatial_data_retrieval_agent",
                "purpose": "Acquire parcel polygons for Centre County.",
                "expected_outputs": ["Centre County parcel polygons"],
            },
            {
                "step_id": "hospitals",
                "order": 2,
                "title": "Obtain candidate hospital point features",
                "agent_id": "geospatial_data_retrieval_agent",
                "purpose": "Obtain hospital point features that will later be spatially filtered.",
                "input_from_steps": ["parcels"],
                "depends_on": ["parcels"],
                "expected_outputs": ["Centre County candidate hospital points"],
            },
            {
                "step_id": "nearest_hospital",
                "order": 3,
                "title": "Calculate nearest hospital distance",
                "agent_id": "vector_analysis_agent",
                "purpose": "Filter hospitals to the county and calculate nearest hospital distance for parcels.",
                "expected_outputs": ["Parcels with nearest hospital distance"],
            },
        ],
    }

    normalized = agent._normalize_plan(plan, "Map parcel distance to hospitals.", [])
    steps = {step["step_id"]: step for step in normalized["workflow_steps"]}
    graph = agent._build_networkx_workflow_graph(normalized)

    assert steps["hospitals"]["input_from_steps"] == []
    assert steps["hospitals"]["depends_on"] == []
    assert set(steps["nearest_hospital"]["input_from_steps"]) == {"parcels", "hospitals"}
    assert ("data:parcels:output_1", "operation:hospitals") not in graph.edges
    assert ("data:user_goal", "operation:hospitals") in graph.edges
    assert ("data:parcels:output_1", "operation:nearest_hospital") in graph.edges
    assert ("data:hospitals:output_1", "operation:nearest_hospital") in graph.edges


def test_geospatial_workflow_planning_agent_uses_default_plan_outputs():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    assert agent._plan_outputs({}) == [
        "interactive_workflow_graph",
        "workflow_json",
        "notebook_skeleton",
    ]


def test_geospatial_workflow_planning_agent_honors_single_graph_output():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    assert agent._plan_outputs({"plan_outputs": ["interactive_workflow_graph"]}) == ["interactive_workflow_graph"]
    assert agent._plan_outputs({"plan_output": "graph"}) == ["interactive_workflow_graph"]
    assert agent._plan_outputs({"output_format": "HTML"}) == ["interactive_workflow_graph"]
    assert agent._plan_outputs({}, query="Only return the interactive workflow graph.") == ["interactive_workflow_graph"]


def test_geospatial_workflow_planning_agent_flattens_nested_request_parameters():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.set_request_parameters(
        {
            "parameters": {
                "plan_outputs": ["interactive_workflow_graph"],
                "plan_detail": "executable",
            },
            "mode": "stream",
        }
    )

    parameters = agent._parameters()

    assert parameters["plan_outputs"] == ["interactive_workflow_graph"]
    assert parameters["plan_detail"] == "executable"
    assert parameters["mode"] == "stream"
    assert agent._plan_outputs(parameters) == ["interactive_workflow_graph"]


def test_geospatial_workflow_planning_agent_flattens_planning_parameters_wrapper():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.set_request_parameters(
        {
            "planning_parameters": {
                "gas_servers": ["http://example.test/?SERVICE=GAS&REQUEST=GetCapabilities"],
                "plan_outputs": ["interactive_workflow_graph"],
            }
        }
    )

    parameters = agent._parameters()

    assert parameters["gas_servers"] == ["http://example.test/?SERVICE=GAS&REQUEST=GetCapabilities"]
    assert parameters["plan_outputs"] == ["interactive_workflow_graph"]
    assert agent._gas_server_urls(parameters) == ["http://example.test/?SERVICE=GAS&REQUEST=GetCapabilities"]
    assert agent._plan_outputs(parameters) == ["interactive_workflow_graph"]


def test_geospatial_workflow_planning_agent_accepts_output_aliases():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    assert agent._plan_outputs({"plan_outputs": ["python_code", "notebook", "html"]}) == [
        "gas_client_python",
        "notebook_skeleton",
        "interactive_workflow_graph",
    ]


def test_geospatial_workflow_planning_agent_rejects_unknown_plan_outputs():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    try:
        agent._plan_outputs({"plan_outputs": ["not_a_supported_output"]})
    except ValueError as exc:
        assert "Supported values" in str(exc)
    else:
        raise AssertionError("Expected unsupported plan_outputs to raise ValueError.")


def test_geospatial_workflow_planning_agent_conceptual_prompt_omits_executable_fields():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    messages = agent._build_planning_prompt(
        "Plan a conceptual GAS workflow.",
        catalogs=[],
        plan_detail="conceptual",
        include_validation_steps=True,
        max_steps=6,
    )

    prompt_payload = json.loads(messages[1]["content"])
    step_schema = prompt_payload["required_response_schema"]["workflow_steps"][0]

    assert prompt_payload["plan_detail"] == "conceptual"
    assert "high-level service-composition plan" in messages[0]["content"]
    assert "recommended_mode" not in step_schema
    assert "instructions" not in step_schema
    assert "validation_checks" not in step_schema
    assert "credentials_required" not in step_schema


def test_geospatial_workflow_planning_agent_prompt_prefers_minimal_workflows():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)

    messages = agent._build_planning_prompt(
        "Plan a parcel distance-to-hospital workflow.",
        catalogs=[],
        plan_detail="executable",
        include_validation_steps=True,
        max_steps=12,
    )

    system_prompt = messages[0]["content"]

    assert "simplest valid GAS workflow" in system_prompt
    assert "fewest necessary agent steps" in system_prompt
    assert "independent data retrieval or acquisition steps as parallel branches" in system_prompt
    assert "Combine closely related vector operations" in system_prompt
    assert "Use only one final mapping or web-mapping step" in system_prompt


def test_geospatial_workflow_planning_agent_conceptual_normalization_removes_executable_fields():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    plan = {
        "credentials_required": [{"agent_id": "mapping_agent", "credential": "OPENAI_API_KEY"}],
        "workflow_steps": [
            {
                "step_id": "map",
                "order": 1,
                "agent_id": "mapping_agent",
                "operation": "execute_task",
                "recommended_mode": "stream",
                "instructions": "Create a map.",
                "validation_checks": ["Review map output."],
                "credentials_required": ["OPENAI_API_KEY"],
            }
        ],
    }

    normalized = agent._normalize_plan(plan, "Plan a map workflow.", [], plan_detail="conceptual")
    step = normalized["workflow_steps"][0]

    assert normalized["credentials_required"] == []
    assert "operation" not in step
    assert "recommended_mode" not in step
    assert "instructions" not in step
    assert "validation_checks" not in step
    assert "credentials_required" not in step


def test_geospatial_workflow_planning_agent_conceptual_run_returns_only_requested_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_workflow_planning_agent, "DATA_DIR", tmp_path / "Data")
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    agent.set_request_parameters({"plan_outputs": ["interactive_workflow_graph"], "plan_detail": "conceptual"})

    result = agent.run("Plan a conceptual workflow from GAS capabilities.")
    output_keys = set(result["outputs"])
    plan = result["outputs"]["workflow_plan"]
    graph_path = Path(result["outputs"]["interactive_workflow_graph_file"])

    assert result["inputs"]["parameters"]["plan_outputs"] == ["interactive_workflow_graph"]
    assert result["inputs"]["parameters"]["plan_detail"] == "conceptual"
    assert "interactive_workflow_graph_file" in output_keys
    assert "workflow_plan_file" not in output_keys
    assert "human_readable_plan_file" not in output_keys
    assert "gas_client_python_file" not in output_keys
    assert "notebook_skeleton_file" not in output_keys
    assert all("instructions" not in step for step in plan["workflow_steps"])
    assert all("recommended_mode" not in step for step in plan["workflow_steps"])
    assert plan["planning_detail_type"] == "conceptual"
    assert 'const planDetail = "conceptual";' in graph_path.read_text(encoding="utf-8")
    assert "Planning Type: Conceptual" in graph_path.read_text(encoding="utf-8")


def test_execute_task_outputs_can_carry_planning_options():
    payload = _gas_execute_task_to_run_payload(
        {
            "task": {"instructions": "Plan a workflow.", "mode": "sync"},
            "outputs": {
                "artifact_delivery": "URL",
                "plan_outputs": ["interactive_workflow_graph"],
            },
        }
    )

    assert payload["output_delivery"] == "URL"
    assert payload["plan_outputs"] == ["interactive_workflow_graph"]


def test_execute_task_parameters_survive_streaming_parser():
    run_payload = _gas_execute_task_to_run_payload(
        {
            "task": {"instructions": "Plan a workflow.", "mode": "stream"},
            "parameters": {
                "gas_servers": ["http://example.test/?SERVICE=GAS&REQUEST=GetCapabilities"],
                "plan_outputs": ["interactive_workflow_graph"],
                "plan_detail": "executable",
                "include_validation_steps": True,
                "max_steps": 12,
            },
            "credentials": {"OPENAI_API_KEY": "test-key"},
            "outputs": {"artifact_delivery": "URL"},
        }
    )

    query, dataset_paths, params, _message = _extract_text_and_input_dataset_paths(run_payload)

    assert query == "Plan a workflow."
    assert dataset_paths == []
    assert params["gas_servers"] == ["http://example.test/?SERVICE=GAS&REQUEST=GetCapabilities"]
    assert params["plan_outputs"] == ["interactive_workflow_graph"]
    assert params["plan_detail"] == "executable"
    assert params["include_validation_steps"] is True
    assert params["max_steps"] == 12
    assert params["OPENAI_API_KEY"] == "test-key"
    assert params["output_delivery"] == "URL"


def test_geospatial_workflow_planning_agent_run_service_honors_nested_single_output(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_workflow_planning_agent, "DATA_DIR", tmp_path / "Data")
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = agent.run_service(
        "Plan a workflow from GAS capabilities.",
        parameters={
            "parameters": {
                "plan_outputs": ["interactive_workflow_graph"],
                "plan_detail": "executable",
            }
        },
    )

    output_keys = set(result["outputs"])

    assert result["inputs"]["parameters"]["plan_outputs"] == ["interactive_workflow_graph"]
    assert "interactive_workflow_graph_file" in output_keys
    assert "workflow_plan_file" not in output_keys
    assert "human_readable_plan_file" not in output_keys
    assert "gas_client_python_file" not in output_keys
    assert "notebook_skeleton_file" not in output_keys


def test_geospatial_workflow_planning_agent_run_service_honors_planning_parameters_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_workflow_planning_agent, "DATA_DIR", tmp_path / "Data")
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = agent.run_service(
        "Plan a workflow from GAS capabilities.",
        parameters={
            "planning_parameters": {
                "plan_outputs": ["interactive_workflow_graph"],
                "plan_detail": "executable",
            }
        },
    )

    output_keys = set(result["outputs"])

    assert result["inputs"]["parameters"]["plan_outputs"] == ["interactive_workflow_graph"]
    assert "interactive_workflow_graph_file" in output_keys
    assert "workflow_plan_file" not in output_keys
    assert "human_readable_plan_file" not in output_keys
    assert "gas_client_python_file" not in output_keys
    assert "notebook_skeleton_file" not in output_keys


def test_geospatial_workflow_planning_agent_removes_unrequested_inspection_step():
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    plan = {
        "workflow_steps": [
            {"step_id": "download", "order": 1, "agent_id": "geospatial_data_retrieval_agent"},
            {
                "step_id": "inspect",
                "order": 2,
                "agent_id": "geospatial_data_inspection_agent",
                "input_from_steps": ["download"],
            },
            {
                "step_id": "map",
                "order": 3,
                "agent_id": "mapping_agent",
                "input_from_steps": ["inspect"],
                "depends_on": ["inspect"],
            },
        ]
    }

    normalized = agent._normalize_plan(plan, "Map the downloaded data.", [])

    assert [step["step_id"] for step in normalized["workflow_steps"]] == ["download", "map"]
    assert normalized["workflow_steps"][1]["input_from_steps"] == ["download"]


def test_geospatial_workflow_planning_agent_normalizes_to_standard_response(tmp_path, monkeypatch):
    monkeypatch.setattr(geospatial_workflow_planning_agent, "DATA_DIR", tmp_path / "Data")
    agent = GeospatialWorkflowPlanningAgent(api_key=None)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    agent.set_request_parameters({"plan_outputs": ["workflow_json", "interactive_workflow_graph"]})
    raw_result = agent.run("Plan a workflow from GAS capabilities.")

    payload = _build_task_payload(
        task_id="workflow-planning-test-task",
        agent_id="geospatial_workflow_planning_agent",
        agent_name=raw_result["agent_name"],
        agent_version=raw_result["agent_version"],
        state="TASK_STATE_COMPLETED",
        query="Plan a workflow from GAS capabilities.",
        requested_skill=None,
        result=raw_result,
        error_message=None,
        agent_id_for_artifacts="geospatial_workflow_planning_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    assert payload["agent"]["id"] == "geospatial_workflow_planning_agent"
    assert payload["outputs"]["summary"]
    artifact_formats = {artifact.get("format") for artifact in payload["outputs"]["artifacts"]}
    assert {"json", "html"} <= artifact_formats
    assert payload["provenance"]["llm_calls"] == 1
    assert payload["diagnostics"]["validation"]["status"] == "passed"


def test_geospatial_workflow_planning_agent_service_requires_model_credentials():
    app = get_service_app()
    response = app.test_client().post(
        "/tasks",
        json={
            "task": {"instructions": "Plan a geospatial workflow.", "mode": "sync"},
            "parameters": {"plan_outputs": ["workflow_json"]},
        },
    )

    assert response.status_code == 400
    assert "OPENAI_API_KEY or GIBD_API_KEY" in response.get_json()["error"]["message"]
