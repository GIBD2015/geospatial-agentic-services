from __future__ import annotations

import html
import json
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import networkx as nx

from gas_server.core.config import DATA_DIR, PROJECT_ROOT, ensure_runtime_dirs
from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent, ProgressCallback
from gas_server.core.llm_client import build_llm_client, format_service_name


ensure_runtime_dirs()


SUPPORTED_PLAN_OUTPUTS = {
    "workflow_json",
    "human_readable",
    "gas_client_python",
    "notebook_skeleton",
    "interactive_workflow_graph",
}

ALL_PLAN_OUTPUTS = [
    "workflow_json",
    "human_readable",
    "gas_client_python",
    "notebook_skeleton",
    "interactive_workflow_graph",
]

DEFAULT_PLAN_OUTPUTS = [
    "interactive_workflow_graph",
    "workflow_json",
    "notebook_skeleton",
]


class GeospatialWorkflowPlanningAgent(GeoAgent):
    agent_id = "geospatial_workflow_planning_agent"
    agent_name = "Geospatial Workflow Planning Agent"
    agent_version = "1.0.0"
    agent_description = (
        "Discovers GAS agent capabilities and generates executable workflow plans "
        "for client-side orchestration."
    )
    requires_input_datasets = False
    requires_model_credentials = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(service_name=self.service_name, openai_api_key=self.api_key)
        self.input_tokens = 0
        self.output_tokens = 0
        self._last_temperature_control = "temperature=0.1"

    def _parameters(self) -> dict[str, Any]:
        raw_parameters = dict(getattr(self, "request_parameters", {}) or {})
        flattened: dict[str, Any] = {}

        parameter_containers = {
            "metadata",
            "parameters",
            "outputs",
            "planning_parameters",
            "planner_parameters",
            "workflow_planning_parameters",
        }

        for container_name in parameter_containers:
            nested = raw_parameters.get(container_name)
            if isinstance(nested, dict):
                flattened.update(nested)

        for key, value in raw_parameters.items():
            if key not in parameter_containers:
                flattened[key] = value

        return flattened

    def _as_list(self, value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    def _bool_param(self, value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _chat_completion(self, messages: list[dict[str, str]]):
        """Create a planning completion, retrying without temperature when required.

        Some high-reasoning model versions only accept their default sampling
        settings. For those models, a temperature error should not cause the
        planning agent to fall back to a weak deterministic plan.
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
            )
            self._last_temperature_control = "temperature=0.1"
            return response
        except Exception as exc:
            message = str(exc)
            if "temperature" not in message or "unsupported" not in message.lower():
                raise
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
            )
            self._last_temperature_control = "provider default temperature"
            return response

    def _plan_outputs(self, parameters: dict[str, Any], query: str | None = None) -> list[str]:
        selector_keys = (
            "plan_outputs",
            "plan_output",
            "return_options",
            "return_option",
            "output_options",
            "output_option",
            "output_artifacts",
            "output_artifact",
            "artifact_outputs",
            "artifact_output",
            "output_format",
            "output_formats",
            "format",
            "formats",
        )
        raw = None
        selector_was_supplied = False
        for key in selector_keys:
            if key in parameters:
                candidate = parameters.get(key)
                if candidate is None or candidate == "":
                    continue
                raw = candidate
                selector_was_supplied = True
                break

        if raw is None and query:
            query_lower = query.lower()
            if re.search(r"\b(all|five|5)\b.*\b(output|artifact|option)s?\b", query_lower):
                return list(ALL_PLAN_OUTPUTS)
            if re.search(r"\bonly\b.*\b(interactive|workflow)?\s*graph\b", query_lower):
                return ["interactive_workflow_graph"]
            inferred = []
            if "json" in query_lower:
                inferred.append("workflow_json")
            if "markdown" in query_lower or "human" in query_lower or "readable" in query_lower:
                inferred.append("human_readable")
            if "python" in query_lower or "gas client" in query_lower:
                inferred.append("gas_client_python")
            if "notebook" in query_lower or "ipynb" in query_lower or "jupyter" in query_lower:
                inferred.append("notebook_skeleton")
            if "graph" in query_lower or "html" in query_lower or "visual" in query_lower:
                inferred.append("interactive_workflow_graph")
            raw = inferred or DEFAULT_PLAN_OUTPUTS
        if raw is None:
            raw = DEFAULT_PLAN_OUTPUTS
        if isinstance(raw, str):
            items = [item.strip() for item in re.split(r"[,;]", raw) if item.strip()]
        else:
            items = [str(item).strip() for item in self._as_list(raw) if str(item).strip()]
        normalized = []
        for item in items:
            value = item.lower().replace("-", "_").replace(" ", "_")
            if value in {"all", "all_outputs", "all_options"}:
                return list(ALL_PLAN_OUTPUTS)
            if value in {"interactive_graph", "workflow_graph", "html_graph", "graph", "html", "interactive_html", "workflow_html"}:
                value = "interactive_workflow_graph"
            if value in {"python", "python_code", "gas_python", "gas_client_code", "gas_client_script"}:
                value = "gas_client_python"
            if value in {"notebook", "jupyter", "ipynb", "notebook_code"}:
                value = "notebook_skeleton"
            if value in SUPPORTED_PLAN_OUTPUTS and value not in normalized:
                normalized.append(value)
        if normalized:
            return normalized
        if selector_was_supplied:
            supported = ", ".join(sorted(SUPPORTED_PLAN_OUTPUTS))
            raise ValueError(f"plan_outputs did not contain a supported output option. Supported values are: {supported}.")
        return list(DEFAULT_PLAN_OUTPUTS)

    def _query_requests_inspection_step(self, query: str) -> bool:
        query_lower = query.lower()
        return bool(
            re.search(
                r"\b(inspect|inspection|quality|validate|validation|readiness|check\s+(?:the\s+)?(?:data|dataset|datasets)|data\s+quality)\b",
                query_lower,
            )
        )

    def _gas_server_urls(self, parameters: dict[str, Any]) -> list[str]:
        raw = (
            parameters.get("gas_servers")
            or parameters.get("gas_server_urls")
            or parameters.get("capabilities_urls")
            or parameters.get("capabilities_url")
            or parameters.get("gas_server")
        )
        return [str(item).strip() for item in self._as_list(raw) if str(item).strip()]

    def _describe_url_from_capabilities(self, capabilities_url: str, agent: dict[str, Any]) -> str:
        direct = agent.get("DescribeAgent") or agent.get("describe_agent") or agent.get("describeUrl")
        if isinstance(direct, str) and direct.strip():
            if direct.startswith("http://") or direct.startswith("https://"):
                return direct
            parsed = urlparse(capabilities_url)
            return urlunparse((parsed.scheme, parsed.netloc, direct, "", "", ""))

        agent_id = agent.get("agent_id") or agent.get("id") or agent.get("name")
        parsed = urlparse(capabilities_url)
        query = parse_qs(parsed.query)
        query.update({"SERVICE": ["GAS"], "VERSION": ["1.0.0"], "REQUEST": ["DescribeAgent"], "agent_id": [str(agent_id)]})
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", urlencode(query, doseq=True), ""))

    def _base_url_from_capabilities_url(self, capabilities_url: str, payload: dict[str, Any]) -> str:
        base_url = payload.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            return base_url.rstrip("/")
        parsed = urlparse(capabilities_url)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")

    def _load_remote_server_catalog(self, capabilities_url: str) -> dict[str, Any]:
        response = requests.get(capabilities_url, timeout=60)
        response.raise_for_status()
        capabilities = response.json()
        base_url = self._base_url_from_capabilities_url(capabilities_url, capabilities)
        agents = []
        for agent in capabilities.get("agents", []):
            if not isinstance(agent, dict):
                continue
            describe_url = self._describe_url_from_capabilities(capabilities_url, agent)
            try:
                describe_response = requests.get(describe_url, timeout=60)
                describe_response.raise_for_status()
                describe_agent = describe_response.json()
            except Exception as exc:
                describe_agent = {
                    "profile": {
                        "agent_id": agent.get("agent_id") or agent.get("id"),
                        "name": agent.get("name") or agent.get("agent_id"),
                        "description": f"DescribeAgent could not be fetched: {exc}",
                    },
                    "skills": [],
                    "execute_task": {},
                }
            agents.append(self._summarize_agent_capability(describe_agent, base_url, describe_url))
        return {
            "source": capabilities_url,
            "base_url": base_url,
            "title": capabilities.get("title"),
            "agents": agents,
        }

    def _load_local_server_catalog(self) -> dict[str, Any]:
        capability_dir = PROJECT_ROOT / "gas_server" / "capabilities"
        capabilities_path = capability_dir / "capabilities.json"
        capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
        base_url = str(capabilities.get("base_url") or "https://your-gas-server.example").rstrip("/")
        agents = []
        for path in sorted(capability_dir.glob("*_agent.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            profile = payload.get("profile", {})
            describe_url = f"{base_url}/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent&agent_id={profile.get('agent_id', path.stem)}"
            agents.append(self._summarize_agent_capability(payload, base_url, describe_url))
        return {
            "source": "current_server_local_capabilities",
            "base_url": base_url,
            "title": capabilities.get("title"),
            "agents": agents,
        }

    def _summarize_agent_capability(self, describe_agent: dict[str, Any], base_url: str, describe_url: str) -> dict[str, Any]:
        profile = describe_agent.get("profile", {}) if isinstance(describe_agent, dict) else {}
        execute_task = describe_agent.get("execute_task", {}) if isinstance(describe_agent, dict) else {}
        outputs = execute_task.get("outputs", {}) if isinstance(execute_task, dict) else {}
        parameters = execute_task.get("parameters", {}) if isinstance(execute_task, dict) else {}
        credentials = execute_task.get("credentials", {}) if isinstance(execute_task, dict) else {}
        skills = describe_agent.get("skills", []) if isinstance(describe_agent, dict) else []
        keywords = describe_agent.get("keywords", []) if isinstance(describe_agent, dict) else []
        agent_id = profile.get("agent_id") or profile.get("id")
        return {
            "agent_id": agent_id,
            "name": profile.get("name") or agent_id,
            "description": profile.get("description"),
            "base_url": f"{base_url}/agents/{agent_id}" if agent_id else base_url,
            "describe_agent_url": describe_url,
            "default_model": profile.get("default_model"),
            "keywords": keywords,
            "skills": [
                {
                    "skill_id": skill.get("skill_id"),
                    "name": skill.get("name"),
                    "description": skill.get("description"),
                    "constraints": skill.get("constraints"),
                }
                for skill in skills
                if isinstance(skill, dict)
            ],
            "execute_task": {
                "modes": execute_task.get("modes", []),
                "inputs": execute_task.get("inputs", {}),
                "outputs": outputs,
                "parameters": parameters,
                "credentials": credentials,
            },
        }

    def _discover_capabilities(self, gas_server_urls: list[str], progress_callback: ProgressCallback | None) -> list[dict[str, Any]]:
        self.emit_progress(
            progress_callback,
            stage="source_selection",
            message="I am discovering GAS servers and reading their published agent capabilities.",
            data={"gas_server_count": len(gas_server_urls) or 1},
        )
        catalogs = []
        if gas_server_urls:
            for url in gas_server_urls:
                catalogs.append(self._load_remote_server_catalog(url))
                self.increment_tool_calls()
        else:
            catalogs.append(self._load_local_server_catalog())
            self.increment_tool_calls()
        self.emit_progress(
            progress_callback,
            stage="source_validation",
            message="Capability discovery is complete. I will use the DescribeAgent information for workflow matching.",
            data={
                "server_count": len(catalogs),
                "agent_count": sum(len(catalog.get("agents", [])) for catalog in catalogs),
            },
        )
        return catalogs

    def _build_planning_prompt(
        self,
        query: str,
        catalogs: list[dict[str, Any]],
        *,
        plan_detail: str,
        include_validation_steps: bool,
        max_steps: int,
    ) -> list[dict[str, str]]:
        compact_catalog = [
            {
                "source": catalog.get("source"),
                "base_url": catalog.get("base_url"),
                "agents": catalog.get("agents", []),
            }
            for catalog in catalogs
        ]
        if plan_detail == "conceptual":
            schema = {
                "schema_version": "1.0.0",
                "readiness": "ready | ready_with_warnings | needs_review | blocked",
                "summary": "short conceptual workflow summary",
                "workflow_steps": [
                    {
                        "step_id": "short_snake_case_id",
                        "order": 1,
                        "title": "Conceptual step title",
                        "purpose": "What this step accomplishes in the workflow",
                        "agent_id": "selected_agent_id_or_null",
                        "agent_name": "selected agent display name or null",
                        "match_status": "matched | unmatched",
                        "required_capability": "capability needed when match_status is unmatched",
                        "recommended_action": "what service or capability should be added when unmatched",
                        "depends_on": [],
                        "input_from_steps": [],
                        "expected_outputs": [],
                        "confidence": "high | medium | low",
                        "notes": [],
                    }
                ],
                "unmatched_steps": [
                    {
                        "title": "step title",
                        "required_capability": "missing capability",
                        "reason": "why no suitable agent was found",
                        "recommended_action": "what kind of GAS agent should be added or discovered",
                    }
                ],
                "assumptions": [],
                "limitations": [],
            }
            detail_instruction = (
                "The requested plan_detail is conceptual. Produce a high-level service-composition plan. "
                "Do not include runnable instructions, execution modes, code-level parameters, or step-by-step client API calls. "
                "Focus on the smallest clear set of agent/service roles, data flow, dependencies, expected artifacts, assumptions, and limitations. "
            )
        else:
            schema = {
            "schema_version": "1.0.0",
            "readiness": "ready | ready_with_warnings | needs_review | blocked",
            "summary": "short workflow summary",
            "workflow_steps": [
                {
                    "step_id": "short_snake_case_id",
                    "order": 1,
                    "title": "Step title",
                    "purpose": "Why this step is needed",
                    "agent_id": "selected_agent_id_or_null",
                    "agent_name": "selected agent display name or null",
                    "match_status": "matched | unmatched",
                    "required_capability": "capability needed when match_status is unmatched",
                    "recommended_action": "what service or capability should be added when unmatched",
                    "gas_server_base_url": "server base URL or null",
                    "operation": "execute_task",
                    "recommended_mode": "sync | async | stream",
                    "instructions": "Natural language instructions to send to this agent",
                    "depends_on": [],
                    "input_from_steps": [],
                    "expected_outputs": [],
                    "validation_checks": [],
                    "credentials_required": [],
                    "confidence": "high | medium | low",
                    "notes": [],
                }
            ],
            "unmatched_steps": [
                {
                    "title": "step title",
                    "required_capability": "missing capability",
                    "reason": "why no suitable agent was found",
                    "recommended_action": "what kind of GAS agent should be added or discovered",
                }
            ],
            "credentials_required": [
                {"agent_id": "agent_id", "credential": "credential description"}
            ],
            "validation_plan": [],
            "assumptions": [],
            "limitations": [],
            }
            detail_instruction = (
                "The requested plan_detail is executable. Produce a plan detailed enough for a client or orchestrator "
                "to call the selected GAS agents. Include recommended execution modes, downstream task instructions, "
                "dependencies, validation checks, and expected artifacts, but keep the step list as compact as possible. "
            )
        minimal_planning_policy = (
            "Default planning policy: produce the simplest valid GAS workflow. "
            "Use the fewest necessary agent steps. Do not split a task into multiple steps unless different GAS agents "
            "are required or the output of one step is truly needed by another. "
            "Treat independent data retrieval or acquisition steps as parallel branches from the user goal; do not make "
            "one retrieval step consume the output of another retrieval step unless the user explicitly asks for that. "
            "Combine closely related vector operations such as filtering, joins, nearest-distance calculation, spatial counts, "
            "and clustering into one Vector Analysis Agent step when that agent can perform them together. "
            "Combine closely related raster operations such as clipping, sampling, extraction, and raster calculations into one Raster Agent step when possible. "
            "Use only one final mapping or web-mapping step unless the user explicitly asks for multiple maps, reports, or visual products. "
            "Do not add optional inspection, validation, conversion, or intermediate reporting steps unless the user explicitly requests them or they are necessary to make the workflow valid. "
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are a Geospatial Workflow Planning Agent for Geospatial Agentic Services (GAS). "
                    "Your job is to plan client-side service chains, not to execute them. "
                    "Use only agents advertised in the provided GAS capability catalog. "
                    + minimal_planning_policy +
                    "If a needed capability is missing, keep that step in workflow_steps with agent_id null, agent_name 'No matching GAS agent found', and match_status 'unmatched'. "
                    "Also put the same gap in unmatched_steps with required_capability, reason, and recommended_action. Do not invent an agent. "
                    "Do not add a data inspection or data quality agent as a workflow step unless the user explicitly asks for data inspection, data quality assessment, validation, readiness checking, or similar quality-control work. "
                    "The workflow must form one connected dataflow DAG from the user goal to the final output. "
                    "Every downstream operation must list the previous step IDs whose artifacts it consumes in both depends_on and input_from_steps. "
                    "Do not leave analysis, mapping, reporting, or visualization steps disconnected from the data retrieval or processing steps that supply their inputs. "
                    "Use clear human-readable names for expected_outputs, such as 'Richland County boundary GeoPackage' or 'Elevation histogram PNG'. "
                    "Do not use code-style variable names such as richland_county_boundary_gpkg or elevation_histogram_png for expected_outputs. "
                    + detail_instruction +
                    "Return strict JSON only, with no Markdown."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_goal": query,
                        "plan_detail": plan_detail,
                        "include_validation_steps": include_validation_steps,
                        "max_steps": max_steps,
                        "available_gas_capabilities": compact_catalog,
                        "required_response_schema": schema,
                    },
                    indent=2,
                ),
            },
        ]

    def _extract_json_object(self, text: str | None) -> dict[str, Any]:
        if not text:
            raise ValueError("The workflow planning model did not return a response.")
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
            if not match:
                raise
            value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("The workflow planning model response must be a JSON object.")
        return value

    def _fallback_plan(self, query: str, catalogs: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        agents = [agent for catalog in catalogs for agent in catalog.get("agents", [])]
        retrieval = self._best_agent(agents, ("retrieval", "download", "data"))
        inspection = self._best_agent(agents, ("inspection", "quality", "validation")) if self._query_requests_inspection_step(query) else None
        vector = self._best_agent(agents, ("vector", "spatial join", "buffer", "overlay"))
        raster = self._best_agent(agents, ("raster", "dem", "elevation", "clip"))
        mapping = self._best_agent(agents, ("mapping", "map", "visualization"))
        candidates = [retrieval, inspection, vector or raster, mapping]
        workflow_steps = []
        previous = None
        for index, agent in enumerate([item for item in candidates if item], start=1):
            step_id = f"step_{index}_{agent['agent_id']}"
            workflow_steps.append(
                {
                    "step_id": step_id,
                    "order": index,
                    "title": f"Use {agent.get('name') or agent['agent_id']}",
                    "purpose": "Fallback planning selected this agent from its advertised capabilities.",
                    "agent_id": agent.get("agent_id"),
                    "agent_name": agent.get("name"),
                    "gas_server_base_url": agent.get("base_url", "").removesuffix(f"/agents/{agent.get('agent_id')}"),
                    "operation": "execute_task",
                    "recommended_mode": "stream" if index == len([item for item in candidates if item]) else "sync",
                    "instructions": query if index == 1 else f"Continue the workflow goal: {query}",
                    "depends_on": [previous] if previous else [],
                    "input_from_steps": [previous] if previous else [],
                    "expected_outputs": ["standard GAS task response artifacts"],
                    "validation_checks": ["Confirm the task response status is successful and at least one expected artifact is returned."],
                    "credentials_required": self._credential_labels(agent),
                    "confidence": "low",
                    "notes": ["Generated by deterministic fallback because the LLM plan could not be parsed."],
                }
            )
            previous = step_id
        return {
            "schema_version": "1.0.0",
            "readiness": "needs_review",
            "summary": "A fallback workflow plan was generated from available GAS capabilities.",
            "workflow_steps": workflow_steps,
            "unmatched_steps": [],
            "credentials_required": [
                {"agent_id": step["agent_id"], "credential": credential}
                for step in workflow_steps
                for credential in step.get("credentials_required", [])
            ],
            "validation_plan": ["Review the fallback plan before execution."],
            "assumptions": ["The available capability text was sufficient for approximate agent matching."],
            "limitations": [reason],
        }

    def _best_agent(self, agents: list[dict[str, Any]], terms: tuple[str, ...]) -> dict[str, Any] | None:
        best = None
        best_score = 0
        for agent in agents:
            text = json.dumps(
                {
                    "agent_id": agent.get("agent_id"),
                    "name": agent.get("name"),
                    "description": agent.get("description"),
                    "keywords": agent.get("keywords"),
                    "skills": agent.get("skills"),
                },
                default=str,
            ).lower()
            score = sum(1 for term in terms if term.lower() in text)
            if score > best_score:
                best = agent
                best_score = score
        return best

    def _credential_labels(self, agent: dict[str, Any]) -> list[str]:
        credentials = agent.get("execute_task", {}).get("credentials", {})
        if not isinstance(credentials, dict) or not credentials.get("required"):
            return []
        return [str(item) for item in credentials.get("one_of", []) if item and item != "none"]

    def _normalize_plan(self, plan: dict[str, Any], query: str, catalogs: list[dict[str, Any]], plan_detail: str = "executable") -> dict[str, Any]:
        agent_lookup = {
            agent.get("agent_id"): agent
            for catalog in catalogs
            for agent in catalog.get("agents", [])
            if agent.get("agent_id")
        }
        plan.setdefault("schema_version", "1.0.0")
        plan.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        plan.setdefault("user_goal", query)
        plan.setdefault("planner", {"agent_id": self.agent_id, "name": self.agent_name, "version": self.agent_version})
        plan.setdefault("readiness", "ready_with_warnings")
        plan.setdefault("summary", "Generated a GAS workflow plan.")
        plan.setdefault("workflow_steps", [])
        plan.setdefault("unmatched_steps", [])
        plan.setdefault("credentials_required", [])
        plan.setdefault("validation_plan", [])
        plan.setdefault("assumptions", [])
        plan.setdefault("limitations", [])
        if plan_detail == "conceptual":
            plan["credentials_required"] = []
        if not self._query_requests_inspection_step(query):
            plan["workflow_steps"] = self._remove_unrequested_inspection_steps(plan.get("workflow_steps") or [])
        normalized_steps = []
        unmatched_steps = [item for item in (plan.get("unmatched_steps") or []) if isinstance(item, dict)]
        for index, step in enumerate(plan.get("workflow_steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("step_id") or f"step_{index}").strip()
            step["step_id"] = re.sub(r"[^a-zA-Z0-9_]+", "_", step_id).strip("_").lower() or f"step_{index}"
            step["order"] = int(step.get("order") or index)
            step.setdefault("operation", "execute_task")
            step.setdefault("depends_on", [])
            step.setdefault("input_from_steps", [])
            step.setdefault("expected_outputs", [])
            step["expected_outputs"] = self._normalize_expected_outputs(
                step.get("expected_outputs"),
                fallback=f"{step.get('title') or step.get('step_id') or f'Step {index}'} output",
            )
            step.setdefault("confidence", "medium")
            if plan_detail == "conceptual":
                for executable_key in ("recommended_mode", "instructions", "validation_checks", "credentials_required", "operation"):
                    step.pop(executable_key, None)
            else:
                step.setdefault("recommended_mode", "sync")
                step.setdefault("validation_checks", [])
                step.setdefault("credentials_required", [])
            raw_agent_id = str(step.get("agent_id") or "").strip()
            agent = agent_lookup.get(raw_agent_id)
            if agent:
                step["agent_id"] = agent.get("agent_id")
                step["match_status"] = "matched"
                step.setdefault("agent_name", agent.get("name"))
                step.setdefault("gas_server_base_url", agent.get("base_url", "").removesuffix(f"/agents/{agent.get('agent_id')}"))
                if plan_detail != "conceptual" and not step["credentials_required"]:
                    step["credentials_required"] = self._credential_labels(agent)
            else:
                if raw_agent_id:
                    step["attempted_agent_id"] = raw_agent_id
                step["agent_id"] = None
                step["agent_name"] = "No matching GAS agent found"
                step["match_status"] = "unmatched"
                step.setdefault("confidence", "low")
                step.setdefault("required_capability", step.get("title") or step.get("purpose") or f"Step {index} capability")
                step.setdefault(
                    "recommended_action",
                    "Discover, add, or implement a GAS agent that advertises this missing capability.",
                )
                reason = (
                    "No discovered GAS agent advertised a capability that clearly matches this workflow step."
                    if not raw_agent_id
                    else f"Advertised GAS capabilities did not include agent_id '{raw_agent_id}'."
                )
                unmatched_record = {
                    "step_id": step["step_id"],
                    "order": step["order"],
                    "title": step.get("title") or f"Step {index}",
                    "required_capability": step.get("required_capability"),
                    "reason": step.get("reason") or reason,
                    "recommended_action": step.get("recommended_action"),
                }
                if not any(item.get("step_id") == unmatched_record["step_id"] for item in unmatched_steps):
                    unmatched_steps.append(unmatched_record)
            normalized_steps.append(step)
        plan["workflow_steps"] = sorted(normalized_steps, key=lambda item: item.get("order", 0))
        plan["unmatched_steps"] = unmatched_steps
        if unmatched_steps:
            if plan.get("readiness") in {"ready", "ready_with_warnings"}:
                plan["readiness"] = "needs_review"
            if not any("unmatched" in str(item).lower() for item in plan.get("limitations", [])):
                plan["limitations"].append(
                    f"{len(unmatched_steps)} workflow step(s) could not be matched to discovered GAS agent capabilities."
                )
        repair_notes = self._repair_workflow_dependencies(plan["workflow_steps"], agent_lookup)
        if repair_notes:
            plan.setdefault("normalization_notes", [])
            plan["normalization_notes"].extend(repair_notes)
            if not any("dependency" in str(item).lower() for item in plan.get("limitations", [])):
                plan["limitations"].append(
                    "Some workflow dependencies were inferred during normalization because the original plan omitted or used non-step dependency references."
                )
        plan["discovery"] = {
            "server_count": len(catalogs),
            "agent_count": sum(len(catalog.get("agents", [])) for catalog in catalogs),
            "servers": [
                {
                    "source": catalog.get("source"),
                    "base_url": catalog.get("base_url"),
                    "agent_count": len(catalog.get("agents", [])),
                }
                for catalog in catalogs
            ],
        }
        return plan

    def _repair_workflow_dependencies(
        self,
        workflow_steps: list[dict[str, Any]],
        agent_lookup: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Normalize and infer step dependencies so the dataflow graph is connected.

        LLM plans occasionally pick the right agents but omit `depends_on` for
        downstream analysis/mapping steps, or use descriptive data names instead
        of step IDs. GAS workflow graphs must represent data flowing from
        earlier operation outputs into later operation inputs, so this pass
        conservatively maps those references back to previous step IDs.
        """

        notes: list[str] = []
        step_ids = [str(step.get("step_id") or "") for step in workflow_steps]
        known_ids = {step_id for step_id in step_ids if step_id}

        def _dedupe(values: list[str]) -> list[str]:
            out: list[str] = []
            for value in values:
                if value and value in known_ids and value not in out:
                    out.append(value)
            return out

        for index, step in enumerate(workflow_steps):
            step_id = str(step.get("step_id") or "")
            previous_steps = workflow_steps[:index]
            original_refs = [
                str(item)
                for item in (step.get("input_from_steps") or step.get("depends_on") or [])
                if item
            ]
            consumes_prior_outputs = self._step_likely_consumes_prior_outputs(step, agent_lookup)
            if not consumes_prior_outputs:
                if original_refs:
                    step["depends_on"] = []
                    step["input_from_steps"] = []
                    notes.append(
                        f"Removed invalid upstream dependencies from source/acquisition step "
                        f"{step_id or f'step_{index + 1}'}."
                    )
                continue

            normalized_refs = self._map_dependency_references(original_refs, previous_steps)
            inferred_refs = self._infer_dependencies_from_step_text(step, previous_steps)
            dependencies = _dedupe([*normalized_refs, *inferred_refs])

            if not dependencies and previous_steps:
                dependencies = [str(previous_steps[-1].get("step_id"))]

            if dependencies:
                old_refs = _dedupe([ref for ref in original_refs if ref in known_ids])
                step["depends_on"] = dependencies
                step["input_from_steps"] = dependencies
                if dependencies != old_refs:
                    notes.append(
                        f"Inferred dependencies for {step_id or f'step_{index + 1}'}: {', '.join(dependencies)}."
                    )

        return notes

    def _map_dependency_references(self, references: list[str], previous_steps: list[dict[str, Any]]) -> list[str]:
        """Map dependency strings to previous step IDs when possible."""

        mapped: list[str] = []
        for reference in references:
            ref = str(reference).strip()
            if not ref:
                continue
            ref_key = self._token_key(ref)
            for step in previous_steps:
                step_id = str(step.get("step_id") or "")
                if ref == step_id or ref_key == self._token_key(step_id):
                    mapped.append(step_id)
                    break
                labels = [
                    step.get("title"),
                    step.get("purpose"),
                    step.get("agent_id"),
                    step.get("agent_name"),
                    *self._as_list(step.get("expected_outputs")),
                ]
                if any(ref_key and ref_key == self._token_key(label) for label in labels):
                    mapped.append(step_id)
                    break
                if any(self._dependency_text_matches(ref, label) for label in labels):
                    mapped.append(step_id)
                    break
        return mapped

    def _infer_dependencies_from_step_text(self, step: dict[str, Any], previous_steps: list[dict[str, Any]]) -> list[str]:
        """Infer dependencies by matching current task text to prior outputs."""

        current_text = self._step_search_text(step)
        current_tokens = self._meaningful_tokens(current_text)
        inferred: list[str] = []
        for previous in previous_steps:
            previous_id = str(previous.get("step_id") or "")
            previous_text = self._step_search_text(previous)
            previous_tokens = self._meaningful_tokens(previous_text)
            shared = current_tokens & previous_tokens
            if self._dependency_signal(shared, current_text, previous_text):
                inferred.append(previous_id)
        return inferred

    def _step_likely_consumes_prior_outputs(
        self,
        step: dict[str, Any],
        agent_lookup: dict[str, dict[str, Any]],
    ) -> bool:
        agent_id = str(step.get("agent_id") or step.get("attempted_agent_id") or "")
        agent = agent_lookup.get(agent_id) or {}
        inputs = agent.get("execute_task", {}).get("inputs", {}) if isinstance(agent, dict) else {}
        input_datasets = inputs.get("input_datasets") if isinstance(inputs, dict) else {}
        if isinstance(input_datasets, dict) and input_datasets.get("required"):
            return True
        text = self._step_search_text(step).lower()
        if "retrieval" in agent_id or "download" in text or "retrieve" in text:
            return False
        return any(
            term in text
            for term in (
                "analy",
                "join",
                "clip",
                "map",
                "visual",
                "count",
                "extract",
                "filter",
                "overlay",
                "buffer",
                "distance",
                "cluster",
                "sample",
                "summar",
            )
        )

    def _step_search_text(self, step: dict[str, Any]) -> str:
        values = [
            step.get("step_id"),
            step.get("title"),
            step.get("purpose"),
            step.get("instructions"),
            step.get("agent_id"),
            step.get("agent_name"),
            step.get("required_capability"),
            *self._as_list(step.get("expected_outputs")),
        ]
        return " ".join(str(value) for value in values if value)

    def _token_key(self, value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def _meaningful_tokens(self, text: str) -> set[str]:
        stop_words = {
            "a",
            "an",
            "and",
            "agent",
            "all",
            "as",
            "by",
            "create",
            "data",
            "dataset",
            "datasets",
            "for",
            "from",
            "gas",
            "generate",
            "get",
            "in",
            "into",
            "it",
            "map",
            "mapping",
            "of",
            "on",
            "output",
            "outputs",
            "produce",
            "provide",
            "result",
            "results",
            "service",
            "step",
            "task",
            "the",
            "this",
            "to",
            "use",
            "using",
            "with",
        }
        tokens = {
            token
            for token in re.split(r"[^a-zA-Z0-9]+", text.lower())
            if len(token) >= 3 and token not in stop_words
        }
        aliases = {
            "county": {"counties"},
            "counties": {"county"},
            "hospital": {"hospitals"},
            "hospitals": {"hospital"},
            "parcel": {"parcels"},
            "parcels": {"parcel"},
            "boundary": {"boundaries"},
            "boundaries": {"boundary"},
            "dem": {"elevation"},
            "elevation": {"dem"},
        }
        expanded = set(tokens)
        for token in tokens:
            expanded.update(aliases.get(token, set()))
        return expanded

    def _dependency_signal(self, shared: set[str], current_text: str, previous_text: str) -> bool:
        strong_terms = {
            "boundary",
            "boundaries",
            "county",
            "counties",
            "dem",
            "elevation",
            "hospital",
            "hospitals",
            "parcel",
            "parcels",
            "points",
            "population",
            "raster",
            "vector",
        }
        if shared & strong_terms:
            return True
        if len(shared) >= 2:
            return True
        return self._dependency_text_matches(current_text, previous_text)

    def _dependency_text_matches(self, reference: Any, label: Any) -> bool:
        ref_tokens = self._meaningful_tokens(str(reference or ""))
        label_tokens = self._meaningful_tokens(str(label or ""))
        if not ref_tokens or not label_tokens:
            return False
        shared = ref_tokens & label_tokens
        return bool(shared & {"boundary", "county", "hospital", "parcel", "dem", "elevation"}) or len(shared) >= 2

    def _normalize_expected_outputs(self, outputs: Any, fallback: str) -> list[str]:
        """Return human-readable artifact names for workflow data nodes."""

        normalized: list[str] = []
        for index, output in enumerate(self._as_list(outputs), start=1):
            label = self._human_readable_artifact_label(output, fallback=f"{fallback} {index}")
            if label and label not in normalized:
                normalized.append(label)
        return normalized or [self._human_readable_artifact_label(fallback, fallback="GAS task response artifact")]

    def _human_readable_artifact_label(self, value: Any, fallback: str = "GAS task response artifact") -> str:
        """Convert LLM output names into display-ready artifact names."""

        if isinstance(value, dict):
            for key in (
                "name",
                "title",
                "label",
                "description",
                "output",
                "artifact",
                "dataset",
                "data_objective",
                "objective",
                "format",
                "type",
            ):
                candidate = value.get(key)
                if candidate:
                    return self._human_readable_artifact_label(candidate, fallback=fallback)
            return fallback

        if isinstance(value, (list, tuple)):
            text = " ".join(self._human_readable_artifact_label(item, fallback="") for item in value)
        else:
            text = str(value or fallback)

        text = text.strip()
        if not text:
            return fallback
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\([^)]{60,}\)", "", text).strip()

        # LLMs sometimes return variable/file-stem style names for data nodes.
        code_style = bool(re.search(r"[_]{1,}|[a-z0-9]+(?:-[a-z0-9]+)+", text)) and not re.search(r"\s", text)
        if code_style:
            text = text.replace("-", "_")
            replacements = {
                "gpkg": "GeoPackage",
                "geopackage": "GeoPackage",
                "geojson": "GeoJSON",
                "geotiff": "GeoTIFF",
                "tif": "GeoTIFF",
                "tiff": "GeoTIFF",
                "png": "PNG",
                "jpg": "JPEG",
                "jpeg": "JPEG",
                "csv": "CSV",
                "json": "JSON",
                "html": "HTML",
                "dem": "DEM",
                "crs": "CRS",
                "fips": "FIPS",
                "url": "URL",
            }
            words = []
            for part in [item for item in text.split("_") if item]:
                lower = part.lower()
                words.append(replacements.get(lower, part.capitalize()))
            small_words = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "to", "with"}
            words = [word.lower() if index > 0 and word.lower() in small_words else word for index, word in enumerate(words)]
            text = " ".join(words)

        # Clean common code-ish leftovers even when spaces are present.
        text = re.sub(r"\b(?:artifact|output)_url\b", "artifact URL", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip(" ._-")
        return text[:74] + "..." if len(text) > 77 else text

    def _remove_unrequested_inspection_steps(self, workflow_steps: list[Any]) -> list[Any]:
        """Remove inspection service calls unless the user asks for them."""

        inspection_ids = {
            str(step.get("step_id"))
            for step in workflow_steps
            if isinstance(step, dict)
            and str(step.get("agent_id") or "").strip().lower() == "geospatial_data_inspection_agent"
        }
        if not inspection_ids:
            return workflow_steps

        replacement_dependencies: dict[str, list[str]] = {}
        for step in workflow_steps:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("step_id") or "")
            if step_id in inspection_ids:
                replacement_dependencies[step_id] = [
                    str(item)
                    for item in (step.get("input_from_steps") or step.get("depends_on") or [])
                    if item and str(item) not in inspection_ids
                ]

        def _rewrite_dependencies(values: Any) -> list[str]:
            rewritten: list[str] = []
            for value in values or []:
                dependency = str(value)
                if dependency in replacement_dependencies:
                    rewritten.extend(replacement_dependencies[dependency])
                elif dependency not in inspection_ids:
                    rewritten.append(dependency)
            deduped = []
            for dependency in rewritten:
                if dependency and dependency not in deduped:
                    deduped.append(dependency)
            return deduped

        filtered = []
        for step in workflow_steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("step_id") or "") in inspection_ids:
                continue
            step = dict(step)
            if step.get("input_from_steps"):
                step["input_from_steps"] = _rewrite_dependencies(step.get("input_from_steps"))
            if step.get("depends_on"):
                step["depends_on"] = _rewrite_dependencies(step.get("depends_on"))
            filtered.append(step)
        return filtered

    def _write_json_plan(self, query: str, plan: dict[str, Any]) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension="json", fallback="workflow_plan")
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return str(path)

    def _plan_detail_label(self, plan: dict[str, Any]) -> str:
        plan_detail = str(plan.get("planning_detail_type") or plan.get("plan_detail") or "executable").strip().lower()
        return "Conceptual" if plan_detail == "conceptual" else "Executable"

    def _markdown_plan(self, plan: dict[str, Any]) -> str:
        plan_detail = str(plan.get("plan_detail") or "executable").lower()
        lines = [
            "# GAS Workflow Plan",
            "",
            f"**Planning Type:** {self._plan_detail_label(plan)}",
            "",
            f"**Readiness:** {plan.get('readiness')}",
            "",
            str(plan.get("summary") or ""),
            "",
            "## Workflow Steps",
            "",
        ]
        for step in plan.get("workflow_steps", []):
            dependencies = ", ".join(step.get("depends_on", [])) or "none"
            outputs = ", ".join(str(item) for item in step.get("expected_outputs", [])) or "standard GAS artifacts"
            lines.extend(
                [
                    f"### {step.get('order')}. {step.get('title') or step.get('step_id')}",
                    "",
                    f"- Agent: `{step.get('agent_id') or 'unmatched'}`",
                    f"- Purpose: {step.get('purpose') or ''}",
                    f"- Depends on: {dependencies}",
                    f"- Expected outputs: {outputs}",
                    f"- Confidence: {step.get('confidence') or 'medium'}",
                    "",
                ]
            )
            if plan_detail != "conceptual":
                lines.insert(-1, f"- Instructions: {step.get('instructions') or ''}")
                lines.insert(-2, f"- Mode: `{step.get('recommended_mode')}`")
        if plan.get("unmatched_steps"):
            lines.extend(["## Unmatched Steps", ""])
            for item in plan["unmatched_steps"]:
                lines.append(f"- {item.get('title') or 'Unmatched step'}: {item.get('reason')}")
            lines.append("")
        if plan.get("validation_plan"):
            lines.extend(["## Validation Plan", ""])
            for item in plan["validation_plan"]:
                lines.append(f"- {item}")
            lines.append("")
        if plan.get("assumptions"):
            lines.extend(["## Assumptions", ""])
            for item in plan["assumptions"]:
                lines.append(f"- {item}")
            lines.append("")
        if plan.get("limitations"):
            lines.extend(["## Limitations", ""])
            for item in plan["limitations"]:
                lines.append(f"- {item}")
            lines.append("")
        return "\n".join(lines)

    def _write_markdown_plan(self, query: str, plan: dict[str, Any]) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension="md", fallback="workflow_plan")
        path.write_text(self._markdown_plan(plan), encoding="utf-8")
        return str(path)

    def _write_python_workflow(self, query: str, plan: dict[str, Any]) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension="py", fallback="gas_workflow")
        steps = plan.get("workflow_steps", [])
        code = [
            '"""Generated GAS Client workflow skeleton.',
            "",
            f"Planning Type: {self._plan_detail_label(plan)}",
            "",
            "Review the plan, fill in credentials and any missing dataset choices, then run from a client environment.",
            '"""',
            "",
            "from gas_client import GasClient",
            "",
            "OPENAI_API_KEY = \"YOUR_OPENAI_API_KEY\"",
            "GIBD_API_KEY = None",
            "results = {}",
            "",
        ]
        clients: dict[str, str] = {}
        for step in steps:
            base_url = step.get("gas_server_base_url") or "https://your-gas-server.example"
            var = re.sub(r"[^a-zA-Z0-9_]+", "_", base_url).strip("_").lower() or "gas_server"
            var = f"client_{len(clients) + 1}" if var in clients.values() else var[:40]
            if base_url not in clients:
                clients[base_url] = var
                code.extend(
                    [
                        f"{var} = GasClient(",
                        f"    {base_url!r},",
                        "    openai_api_key=OPENAI_API_KEY,",
                        "    gibd_api_key=GIBD_API_KEY,",
                        ")",
                        "",
                    ]
                )
        for step in steps:
            step_id = step.get("step_id")
            agent_id = step.get("agent_id")
            if not step_id or not agent_id:
                continue
            client_var = clients.get(step.get("gas_server_base_url") or "https://your-gas-server.example", "client_1")
            dependencies = step.get("input_from_steps") or step.get("depends_on") or []
            input_expr = "None"
            if dependencies:
                dep_refs = ", ".join(f"*{dep}_artifacts" for dep in dependencies)
                code.extend(
                    [
                        f"{step_id}_artifacts = []",
                        f"for _result in [{', '.join('results.get(' + repr(dep) + ')' for dep in dependencies)}]:",
                        "    if _result:",
                        "        for _artifact in _result.get('outputs', {}).get('artifacts', []):",
                        "            if _artifact.get('url'):",
                        f"                {step_id}_artifacts.append(_artifact['url'])",
                        "",
                    ]
                )
                input_expr = f"{step_id}_artifacts"
            code.extend(
                [
                    f"# Step {step.get('order')}: {step.get('title')}",
                    f"results[{step_id!r}] = {client_var}.execute_task(",
                    f"    {agent_id!r},",
                    f"    {(step.get('instructions') or step.get('purpose') or step.get('title') or '')!r},",
                    f"    mode={step.get('recommended_mode', 'sync')!r},",
                    f"    input_datasets={input_expr},",
                    ")",
                    f"{client_var}.print_task_summary(results[{step_id!r}])",
                    "",
                ]
            )
        path.write_text("\n".join(code), encoding="utf-8")
        return str(path)

    def _write_notebook_skeleton(self, query: str, plan: dict[str, Any]) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension="ipynb", fallback="gas_workflow_notebook")
        markdown = self._markdown_plan(plan)
        cells = [
            {"cell_type": "markdown", "metadata": {}, "source": [line + "\n" for line in markdown.splitlines()]},
        ]
        python_path = None
        if str(plan.get("plan_detail") or "executable").lower() != "conceptual":
            python_path = self._write_python_workflow(query, plan)
            python_code = Path(python_path).read_text(encoding="utf-8")
            cells.append(
                {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": [line + "\n" for line in python_code.splitlines()]}
            )
        notebook = {
            "cells": cells,
            "metadata": {
                "gas": {
                    "artifact_type": "workflow_plan_notebook",
                    "planning_detail_type": str(plan.get("planning_detail_type") or plan.get("plan_detail") or "executable"),
                },
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "language_info": {"name": "python", "pygments_lexer": "ipython3"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
        if python_path:
            try:
                Path(python_path).unlink()
            except OSError:
                pass
        return str(path)

    def _compact_data_label(self, value: Any, fallback: str) -> str:
        text = self._human_readable_artifact_label(value, fallback=fallback)
        return text[:74] + "..." if len(text) > 77 else text

    def _build_networkx_workflow_graph(self, plan: dict[str, Any]) -> nx.DiGraph:
        graph = nx.DiGraph()
        step_outputs: dict[str, list[str]] = {}
        plan_detail = str(plan.get("plan_detail") or "executable").lower()

        graph.add_node(
            "data:user_goal",
            node_type="data",
            role="goal",
            data_path="",
            description=self._compact_data_label(plan.get("user_goal"), "User workflow goal"),
            label="User workflow goal",
            variable_name="user_goal",
            layer=0,
        )

        for index, step in enumerate(plan.get("workflow_steps", []) or [], start=1):
            step_id = str(step.get("step_id") or f"step_{index}")
            operation_node = f"operation:{step_id}"
            graph.add_node(
                operation_node,
                node_type="operation",
                role="unmatched" if step.get("match_status") == "unmatched" or not step.get("agent_id") else "matched",
                data_path="",
                description=str(step.get("purpose") or step.get("instructions") or step.get("title") or step_id),
                label=self._compact_data_label(f"{index}. {step.get('agent_name') or step.get('agent_id')}", f"{index}. Step"),
                operation_title=self._compact_data_label(step.get("title"), f"Step {index}"),
                agent_id=str(step.get("agent_id") or ""),
                agent_name=str(step.get("agent_name") or step.get("agent_id") or "Unmatched"),
                mode="" if plan_detail == "conceptual" else str(step.get("recommended_mode") or "sync"),
                step_id=step_id,
                order=int(step.get("order") or index),
                variable_name=f"{step_id}_result",
                layer=index * 2 - 1,
                details=json.dumps(step, ensure_ascii=False),
            )

            dependencies = [str(item) for item in (step.get("input_from_steps") or step.get("depends_on") or []) if item]
            connected_inputs = False
            for dependency in dependencies:
                for output_node in step_outputs.get(dependency, []):
                    graph.add_edge(output_node, operation_node)
                    connected_inputs = True

            if not connected_inputs:
                graph.add_edge("data:user_goal", operation_node)

            outputs = step.get("expected_outputs") or ["standard GAS task response artifacts"]
            output_nodes = []
            for output_index, output in enumerate(outputs, start=1):
                data_node = f"data:{step_id}:output_{output_index}"
                label = self._compact_data_label(output, f"{step.get('title') or step_id} output")
                graph.add_node(
                    data_node,
                    node_type="data",
                    role="data",
                    data_path="",
                    description=label,
                    label=label,
                    source_step=step_id,
                    variable_name=f"{step_id}_artifact_url" if output_index == 1 else f"{step_id}_artifact_{output_index}_url",
                    layer=index * 2,
                )
                graph.add_edge(operation_node, data_node)
                output_nodes.append(data_node)
            step_outputs[step_id] = output_nodes

        if not nx.is_weakly_connected(graph):
            components = list(nx.weakly_connected_components(graph))
            for component in components[1:]:
                first_node = sorted(component)[0]
                if graph.nodes[first_node].get("node_type") == "operation":
                    graph.add_edge("data:user_goal", first_node)
                else:
                    graph.add_edge(first_node, "operation:" + str(plan.get("workflow_steps", [{}])[0].get("step_id", "step_1")))
        for node_id in graph.nodes:
            if graph.nodes[node_id].get("node_type") == "data" and graph.out_degree(node_id) == 0:
                graph.nodes[node_id]["role"] = "final_output"
        return graph

    def _graph_payload(self, graph: nx.DiGraph) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        node_width = 220
        node_height = 76
        canvas_width = 2200
        canvas_height = 1120
        padding_x = 130
        padding_y = 120
        collision_margin = 56
        node_count = max(1, graph.number_of_nodes())
        layout_k = max(0.55, 2.2 / (node_count**0.5))
        raw_positions = nx.spring_layout(graph, seed=42, k=layout_k, iterations=180)

        def _scale(value: float, source_min: float, source_max: float, target_min: float, target_max: float) -> float:
            if abs(source_max - source_min) < 1e-9:
                return (target_min + target_max) / 2
            return target_min + ((value - source_min) / (source_max - source_min)) * (target_max - target_min)

        if raw_positions:
            raw_xs = [float(position[0]) for position in raw_positions.values()]
            raw_ys = [float(position[1]) for position in raw_positions.values()]
            min_x, max_x = min(raw_xs), max(raw_xs)
            min_y, max_y = min(raw_ys), max(raw_ys)
        else:
            min_x = max_x = min_y = max_y = 0.0

        layers = [int(attrs.get("layer") or 0) for _, attrs in graph.nodes(data=True)]
        min_layer = min(layers) if layers else 0
        max_layer = max(layers) if layers else 1
        positions: dict[str, tuple[float, float]] = {}
        for node_id in graph.nodes:
            raw_x, raw_y = raw_positions.get(node_id, (0.0, 0.0))
            layer = int(graph.nodes[node_id].get("layer") or 0)
            x = _scale(
                float(layer),
                min_layer,
                max_layer,
                padding_x,
                canvas_width - padding_x - node_width,
            )
            x += _scale(float(raw_x), min_x, max_x, -35, 35)
            y = _scale(
                float(raw_y),
                min_y,
                max_y,
                padding_y,
                canvas_height - padding_y - node_height,
            )
            positions[node_id] = (x, y)

        def _remove_overlaps(position_map: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
            ids = list(position_map)
            mutable = {node_id: [float(x), float(y)] for node_id, (x, y) in position_map.items()}
            min_gap_x = node_width + collision_margin
            min_gap_y = node_height + collision_margin
            max_x = canvas_width - padding_x - node_width
            max_y = canvas_height - padding_y - node_height

            for _ in range(260):
                moved = False
                for index, source_id in enumerate(ids):
                    for target_id in ids[index + 1 :]:
                        sx, sy = mutable[source_id]
                        tx, ty = mutable[target_id]
                        dx = (sx + node_width / 2) - (tx + node_width / 2)
                        dy = (sy + node_height / 2) - (ty + node_height / 2)
                        overlap_x = min_gap_x - abs(dx)
                        overlap_y = min_gap_y - abs(dy)
                        if overlap_x <= 0 or overlap_y <= 0:
                            continue
                        if abs(dx) < 0.001 and abs(dy) < 0.001:
                            dx = 1.0
                            dy = 1.0
                        if overlap_x < overlap_y:
                            shift = overlap_x / 2
                            direction = 1 if dx >= 0 else -1
                            mutable[source_id][0] += shift * direction
                            mutable[target_id][0] -= shift * direction
                        else:
                            shift = overlap_y / 2
                            direction = 1 if dy >= 0 else -1
                            mutable[source_id][1] += shift * direction
                            mutable[target_id][1] -= shift * direction
                        moved = True
                for node_id in ids:
                    mutable[node_id][0] = min(max(mutable[node_id][0], padding_x), max_x)
                    mutable[node_id][1] = min(max(mutable[node_id][1], padding_y), max_y)
                if not moved:
                    break
            return {node_id: (coordinates[0], coordinates[1]) for node_id, coordinates in mutable.items()}

        positions = _remove_overlaps(positions)

        nodes = []
        for node_id, attrs in graph.nodes(data=True):
            x, y = positions.get(node_id, (0.0, 0.0))
            payload = {
                "id": node_id,
                "label": attrs.get("label") or node_id,
                "node_type": attrs.get("node_type"),
                "role": attrs.get("role", ""),
                "source_step": attrs.get("source_step", ""),
                "variable_name": attrs.get("variable_name", ""),
                "data_path": attrs.get("data_path", ""),
                "description": attrs.get("description", ""),
                "operation_title": attrs.get("operation_title", ""),
                "agent_id": attrs.get("agent_id", ""),
                "agent_name": attrs.get("agent_name", ""),
                "mode": attrs.get("mode", ""),
                "step_id": attrs.get("step_id", ""),
                "order": attrs.get("order", ""),
                "details": attrs.get("details", ""),
                "x": float(x),
                "y": float(y),
            }
            nodes.append(payload)

        edges = [{"from": source, "to": target} for source, target in graph.edges()]
        return nodes, edges, {
            "width": int(canvas_width),
            "height": int(canvas_height),
            "node_width": node_width,
            "node_height": node_height,
        }

    def _write_interactive_graph(self, query: str, plan: dict[str, Any], linked_files: dict[str, str] | None = None) -> str:
        path = Path(self.output_dir) / build_output_filename(query, extension="html", fallback="workflow_graph")
        graph = self._build_networkx_workflow_graph(plan)
        nodes, edges, canvas = self._graph_payload(graph)
        artifact_label_by_key = {
            "workflow_plan_file": "Workflow JSON",
            "human_readable_plan_file": "Markdown Plan",
            "gas_client_python_file": "Python Workflow",
            "notebook_skeleton_file": "Notebook Skeleton",
        }
        artifact_links = []
        for key, file_path in (linked_files or {}).items():
            if key not in artifact_label_by_key or not file_path:
                continue
            filename = Path(file_path).name
            artifact_links.append(
                f'<a href="{html.escape(filename)}" target="_blank" rel="noopener">{html.escape(artifact_label_by_key[key])}</a>'
            )
        artifact_links_html = (
            '<div class="artifact-links">' + "".join(artifact_links) + "</div>"
            if artifact_links
            else '<div class="artifact-links muted">No companion artifacts were requested.</div>'
        )
        planning_detail_type = self._plan_detail_label(plan)
        html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GAS Workflow Plan</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, sans-serif; color: #172033; background: #f7f9fc; }}
header {{ padding: 18px 24px; background: #163b6d; color: white; }}
header h1 {{ margin: 0 0 6px; font-size: 24px; }}
header p {{ margin: 0; max-width: 1100px; opacity: .9; line-height: 1.45; }}
.planning-detail {{ display: inline-flex; width: fit-content; margin-top: 12px; padding: 5px 10px; border-radius: 999px; background: #eaf3ff; border: 1px solid #bdd4ee; color: #0f3d66; font-size: 12px; font-weight: 700; }}
main {{ display: grid; grid-template-columns: minmax(720px, 1fr) 360px; gap: 18px; padding: 18px; transition: grid-template-columns .2s ease; }}
main.details-hidden {{ grid-template-columns: 1fr 0; }}
#graph {{ min-height: 760px; position: relative; overflow: hidden; background: white; border: 1px solid #d9e0ea; border-radius: 8px; background-image: linear-gradient(#eef2f7 1px, transparent 1px), linear-gradient(90deg, #eef2f7 1px, transparent 1px); background-size: 32px 32px; user-select: none; -webkit-user-select: none; touch-action: none; }}
#graph.panning {{ cursor: grabbing; }}
#graph-canvas {{ position: absolute; left: 0; top: 0; transform-origin: 0 0; }}
.graph-toolbar {{ position: absolute; top: 12px; left: 12px; display: flex; flex-wrap: wrap; gap: 6px; z-index: 10; max-width: calc(100% - 24px); user-select: none; -webkit-user-select: none; }}
.graph-toolbar button, #toggle-details, .graph-option {{ border: 1px solid #bcc8d8; background: rgba(255,255,255,.94); border-radius: 6px; padding: 6px 10px; color: #203047; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
.graph-toolbar button:focus, .graph-option input:focus, .node:focus {{ outline: none; }}
.graph-option {{ display: inline-flex; align-items: center; gap: 5px; font-size: 13px; cursor: default; }}
.graph-option input {{ margin: 0; }}
.node {{ position: absolute; width: {canvas["node_width"]}px; min-height: {canvas["node_height"]}px; padding: 8px 10px; border: 1px solid #8ea7c7; border-radius: 8px; background: #eef5ff; box-shadow: 0 4px 12px rgba(0,0,0,.08); cursor: grab; text-align: left; z-index: 2; user-select: none; -webkit-user-select: none; touch-action: none; }}
.node:active {{ cursor: grabbing; }}
.node.data {{ background: #fff8d8; border-color: #d4a72c; border-radius: 999px; display: flex; flex-direction: column; justify-content: center; text-align: center; }}
.node.operation {{ background: #eaf3ff; border-color: #6b93c6; }}
.node.unmatched {{ background: #fff2d6; border-color: #d48b25; }}
.node.goal {{ width: 132px; min-height: 132px; background: #ffffff; border-color: #111827; border-radius: 999px; color: #111827; }}
.node.final {{ width: 144px; min-height: 144px; background: #dff4d8; border-color: #4b9b46; border-radius: 999px; }}
.node strong {{ display: block; margin-bottom: 4px; color: #0f2f55; font-size: 11.5px; line-height: 1.18; }}
.node span {{ display: block; font-size: 10.5px; color: #44546a; line-height: 1.22; }}
svg {{ position: absolute; inset: 0; pointer-events: none; z-index: 1; }}
aside {{ background: white; border: 1px solid #d9e0ea; border-radius: 8px; padding: 16px; max-height: 760px; overflow: auto; transition: opacity .2s ease; }}
aside h2 {{ margin: 0 0 10px; font-size: 16px; }}
aside h3 {{ margin: 0 0 8px; font-size: 14px; line-height: 1.25; }}
aside p, aside li {{ font-size: 12px; line-height: 1.4; }}
main.details-hidden aside {{ opacity: 0; pointer-events: none; overflow: hidden; padding: 0; border: 0; }}
pre {{ white-space: pre-wrap; word-break: break-word; background: #f1f4f8; padding: 10px; border-radius: 6px; font-size: 12px; }}
pre.code-snippet {{ background: #0f172a; color: #e5eefb; font-size: 11.5px; line-height: 1.45; }}
.badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #dff4e8; color: #155b31; font-size: 12px; margin-top: 8px; }}
.legend {{ display: flex; gap: 10px; align-items: center; padding: 10px 18px 0; color: #526174; font-size: 13px; }}
.legend-bar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-right: 18px; }}
.legend-items {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
.artifact-links {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; font-size: 12px; }}
.artifact-links a {{ color: #164f8f; background: #eef5ff; border: 1px solid #bfd3ec; border-radius: 999px; padding: 5px 9px; text-decoration: none; }}
.artifact-links a:hover {{ background: #dfefff; }}
.artifact-links.muted {{ color: #7b8797; }}
.swatch {{ width: 14px; height: 14px; border-radius: 4px; display: inline-block; border: 1px solid #999; }}
.swatch.circle {{ border-radius: 999px; }}
@media (max-width: 980px) {{ main {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>GAS Workflow Plan</h1>
  <p>{html.escape(str(plan.get("summary") or ""))}</p>
  <div class="planning-detail">Planning Type: {html.escape(planning_detail_type)}</div>
</header>
<div class="legend-bar">
  <div class="legend legend-items">
    <span class="swatch circle" style="background:#ffffff"></span>User workflow goal
    <span class="swatch" style="background:#eaf3ff"></span>Operation node
    <span class="swatch" style="background:#fff2d6"></span>Unmatched step
    <span class="swatch circle" style="background:#fff8e5"></span>Data node
    <span class="swatch circle" style="background:#dff4d8"></span>Final result
  </div>
  {artifact_links_html}
</div>
<main>
  <section id="graph" aria-label="Interactive workflow graph">
    <div class="graph-toolbar">
      <button type="button" id="zoom-in">Zoom in</button>
      <button type="button" id="zoom-out">Zoom out</button>
      <button type="button" id="zoom-fit">Fit</button>
      <button type="button" id="save-png">Save PNG</button>
      <label class="graph-option"><input type="checkbox" id="curve-edges" checked> Curved edges</label>
      <label class="graph-option"><input type="checkbox" id="gravity-force"> Gravity force</label>
      <button type="button" id="toggle-details">Hide details</button>
    </div>
    <div id="graph-canvas">
      <svg id="edges"></svg>
    </div>
  </section>
  <aside>
    <h2 id="details-title">Step Details</h2>
    <div id="details">Select or drag a node. Operation nodes are GAS agent calls; data nodes are inputs and outputs passed between agents.</div>
  </aside>
</main>
<script>
const nodes = {json.dumps(nodes)};
const edges = {json.dumps(edges)};
const planDetail = {json.dumps(str(plan.get("plan_detail") or "executable").lower())};
const main = document.querySelector('main');
const graph = document.getElementById('graph');
const graphCanvas = document.getElementById('graph-canvas');
const svg = document.getElementById('edges');
const details = document.getElementById('details');
const detailsTitle = document.getElementById('details-title');
const nodeWidth = {canvas["node_width"]};
const nodeHeight = {canvas["node_height"]};
const canvasWidth = {canvas["width"]};
const canvasHeight = {canvas["height"]};
const positions = new Map();
const nodeElements = new Map();
let draggedNodeId = null;
let animationFrame = null;
let zoom = 1;
let panX = 0;
let panY = 0;
let useCurvedEdges = true;
let useGravityForce = false;
svg.setAttribute('width', canvasWidth);
svg.setAttribute('height', canvasHeight);
svg.setAttribute('viewBox', `0 0 ${{canvasWidth}} ${{canvasHeight}}`);
graphCanvas.style.width = `${{canvasWidth}}px`;
graphCanvas.style.height = `${{canvasHeight}}px`;

function updateTransform() {{
  graphCanvas.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoom}})`;
}}

function setZoom(nextZoom) {{
  zoom = clamp(nextZoom, 0.35, 1.8);
  updateTransform();
}}

function zoomAt(nextZoom, clientX, clientY) {{
  const rect = graph.getBoundingClientRect();
  const oldZoom = zoom;
  const clampedZoom = clamp(nextZoom, 0.35, 1.8);
  const graphX = clientX - rect.left;
  const graphY = clientY - rect.top;
  const worldX = (graphX - panX) / oldZoom;
  const worldY = (graphY - panY) / oldZoom;
  zoom = clampedZoom;
  panX = graphX - worldX * zoom;
  panY = graphY - worldY * zoom;
  updateTransform();
}}

function fitToView() {{
  const availableWidth = Math.max(320, graph.clientWidth - 28);
  const availableHeight = Math.max(320, graph.clientHeight - 28);
  zoom = Math.min(1, availableWidth / canvasWidth, availableHeight / canvasHeight);
  panX = 14;
  panY = 14;
  updateTransform();
}}

function exportGraphPng() {{
  renderEdges();
  const width = Math.max(800, graph.clientWidth);
  const height = Math.max(500, graph.clientHeight);
  const scale = 2;
  const canvas = document.createElement('canvas');
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext('2d');
  context.scale(scale, scale);
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, width, height);

  context.strokeStyle = '#eef2f7';
  context.lineWidth = 1;
  const grid = 32 * zoom;
  const offsetX = ((panX % grid) + grid) % grid;
  const offsetY = ((panY % grid) + grid) % grid;
  for (let x = offsetX; x < width; x += grid) {{
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, height);
    context.stroke();
  }}
  for (let y = offsetY; y < height; y += grid) {{
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }}

  function toScreen(point) {{
    return {{ x: panX + point.x * zoom, y: panY + point.y * zoom }};
  }}

  function nodeScreenBox(id) {{
    const box = nodeBox(id);
    if (!box) return null;
    return {{
      x: panX + box.x * zoom,
      y: panY + box.y * zoom,
      width: box.width * zoom,
      height: box.height * zoom,
      centerX: panX + box.centerX * zoom,
      centerY: panY + box.centerY * zoom
    }};
  }}

  function drawArrowHead(start, end) {{
    const angle = Math.atan2(end.y - start.y, end.x - start.x);
    const length = 12;
    context.beginPath();
    context.moveTo(end.x, end.y);
    context.lineTo(end.x - length * Math.cos(angle - Math.PI / 6), end.y - length * Math.sin(angle - Math.PI / 6));
    context.lineTo(end.x - length * Math.cos(angle + Math.PI / 6), end.y - length * Math.sin(angle + Math.PI / 6));
    context.closePath();
    context.fillStyle = '#50637a';
    context.fill();
  }}

  context.strokeStyle = '#50637a';
  context.lineWidth = 2;
  edges.forEach(edge => {{
    const sourceBox = nodeBox(edge.from);
    const targetBox = nodeBox(edge.to);
    if (!sourceBox || !targetBox) return;
    const a = toScreen(boundaryPoint(sourceBox, targetBox));
    const b = toScreen(boundaryPoint(targetBox, sourceBox));
    context.beginPath();
    context.moveTo(a.x, a.y);
    if (useCurvedEdges) {{
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const curveOffset = Math.min(90, Math.max(24, distance * 0.18));
      const normalX = -dy / distance;
      const normalY = dx / distance;
      const c1x = a.x + dx * 0.45 + normalX * curveOffset;
      const c1y = a.y + dy * 0.45 + normalY * curveOffset;
      const c2x = a.x + dx * 0.55 + normalX * curveOffset;
      const c2y = a.y + dy * 0.55 + normalY * curveOffset;
      context.bezierCurveTo(c1x, c1y, c2x, c2y, b.x, b.y);
      context.stroke();
      drawArrowHead({{ x: c2x, y: c2y }}, b);
    }} else {{
      context.lineTo(b.x, b.y);
      context.stroke();
      drawArrowHead(a, b);
    }}
  }});

  function drawRoundRect(x, y, w, h, radius) {{
    const r = Math.min(radius, w / 2, h / 2);
    context.beginPath();
    context.moveTo(x + r, y);
    context.lineTo(x + w - r, y);
    context.quadraticCurveTo(x + w, y, x + w, y + r);
    context.lineTo(x + w, y + h - r);
    context.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    context.lineTo(x + r, y + h);
    context.quadraticCurveTo(x, y + h, x, y + h - r);
    context.lineTo(x, y + r);
    context.quadraticCurveTo(x, y, x + r, y);
    context.closePath();
  }}

  function wrapTextLines(text, maxWidth, maxLines) {{
    const words = String(text || '').split(/\\s+/).filter(Boolean);
    const lines = [];
    let line = '';
    words.forEach(word => {{
      const testLine = line ? `${{line}} ${{word}}` : word;
      if (context.measureText(testLine).width > maxWidth && line) {{
        lines.push(line);
        line = word;
      }} else {{
        line = testLine;
      }}
    }});
    if (line) lines.push(line);
    const limited = lines.slice(0, maxLines);
    if (lines.length > maxLines && limited.length) {{
      let last = limited[limited.length - 1];
      while (last.length > 3 && context.measureText(`${{last}}...`).width > maxWidth) {{
        last = last.slice(0, -1);
      }}
      limited[limited.length - 1] = `${{last}}...`;
    }}
    return limited;
  }}

  function shrinkFontToFit(text, baseSize, maxWidth, maxLines, weight = 'bold') {{
    let size = baseSize;
    while (size > 7) {{
      context.font = `${{weight}} ${{size}}px Arial, sans-serif`;
      const lines = wrapTextLines(text, maxWidth, maxLines);
      if (lines.every(line => context.measureText(line).width <= maxWidth)) {{
        return {{ size, lines }};
      }}
      size -= 0.5;
    }}
    context.font = `${{weight}} ${{size}}px Arial, sans-serif`;
    return {{ size, lines: wrapTextLines(text, maxWidth, maxLines) }};
  }}

  function drawLines(lines, x, y, lineHeight) {{
    lines.forEach((item, index) => context.fillText(item, x, y + index * lineHeight));
  }}

  nodes.forEach(node => {{
    const box = nodeScreenBox(node.id);
    if (!box) return;
    const isCircle = node.role === 'goal' || node.role === 'final_output' || node.node_type === 'data';
    const fill = node.role === 'goal' ? '#ffffff' : (node.role === 'final_output' ? '#dff4d8' : (node.role === 'unmatched' ? '#fff2d6' : (node.node_type === 'data' ? '#fff8d8' : '#eaf3ff')));
    const stroke = node.role === 'goal' ? '#111827' : (node.role === 'final_output' ? '#4b9b46' : (node.role === 'unmatched' ? '#d48b25' : (node.node_type === 'data' ? '#d4a72c' : '#6b93c6')));
    context.fillStyle = fill;
    context.strokeStyle = stroke;
    context.lineWidth = 1.5;
    context.save();
    if (isCircle) {{
      context.beginPath();
      context.ellipse(box.centerX, box.centerY, box.width / 2, box.height / 2, 0, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.clip();
    }} else {{
      drawRoundRect(box.x, box.y, box.width, box.height, 8);
      context.fill();
      context.stroke();
      context.clip();
    }}
    const horizontalPadding = (isCircle ? 28 : 12) * zoom;
    const maxWidth = Math.max(30, box.width - horizontalPadding * 2);
    const titleBaseSize = Math.max(8, (isCircle ? 10.5 : 11.5) * zoom);
    const subtitleBaseSize = Math.max(7, 9.5 * zoom);
    const titleFit = shrinkFontToFit(node.label, titleBaseSize, maxWidth, isCircle ? 3 : 2, 'bold');
    const titleLineHeight = titleFit.size * 1.18;
    let subtitleFit = {{ size: subtitleBaseSize, lines: [] }};
    if (node.node_type === 'operation' && node.operation_title) {{
      subtitleFit = shrinkFontToFit(node.operation_title, subtitleBaseSize, maxWidth, 2, 'normal');
    }}
    const subtitleLineHeight = subtitleFit.size * 1.2;
    const totalTextHeight = titleFit.lines.length * titleLineHeight + (subtitleFit.lines.length ? 7 * zoom + subtitleFit.lines.length * subtitleLineHeight : 0);
    const startY = isCircle
      ? box.centerY - totalTextHeight / 2
      : box.y + Math.max(8 * zoom, (box.height - totalTextHeight) / 2);
    context.textAlign = isCircle ? 'center' : 'left';
    context.textBaseline = 'top';
    const textX = isCircle ? box.centerX : box.x + horizontalPadding;
    context.fillStyle = '#0f2f55';
    context.font = `bold ${{titleFit.size}}px Arial, sans-serif`;
    drawLines(titleFit.lines, textX, startY, titleLineHeight);
    if (subtitleFit.lines.length) {{
      context.fillStyle = '#44546a';
      context.font = `${{subtitleFit.size}}px Arial, sans-serif`;
      drawLines(subtitleFit.lines, textX, startY + titleFit.lines.length * titleLineHeight + 7 * zoom, subtitleLineHeight);
    }}
    context.restore();
  }});

  canvas.toBlob(blob => {{
    if (!blob) {{
      alert('The graph could not be exported as PNG in this browser.');
      return;
    }}
    const pngUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = pngUrl;
    link.download = 'gas_workflow_graph.png';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(pngUrl), 500);
  }}, 'image/png');
}}

function escapeHtml(value) {{
  return String(value ?? '').replace(/[&<>"']/g, character => ({{
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }})[character]);
}}

function quotePython(value) {{
  return JSON.stringify(String(value ?? ''));
}}

function variableNameForStep(stepId) {{
  return `${{String(stepId || 'step').replace(/[^a-zA-Z0-9_]/g, '_')}}_result`;
}}

function artifactVariableNameForDataNode(node) {{
  if (node.variable_name) return node.variable_name;
  if (node.role === 'goal') return 'user_goal';
  return `${{String(node.id || 'artifact').replace(/[^a-zA-Z0-9_]/g, '_')}}_url`;
}}

function buildOperationCode(node, step) {{
  const stepId = step.step_id || node.step_id || 'step';
  const resultVar = variableNameForStep(stepId);
  const inputNodes = edges
    .filter(edge => edge.to === node.id)
    .map(edge => nodes.find(item => item.id === edge.from))
    .filter(Boolean)
    .filter(item => item.role !== 'goal');
  const inputVars = inputNodes.map(artifactVariableNameForDataNode);
  const inputLine = inputVars.length
    ? `${{stepId}}_input_datasets = [${{inputVars.join(', ')}}]`
    : `${{stepId}}_input_datasets = None`;
  if (!step.agent_id || step.match_status === 'unmatched') {{
    return [
      `# ${{step.title || node.operation_title || node.label}}`,
      '# No matching GAS agent was found for this step.',
      `# Required capability: ${{step.required_capability || step.title || 'unspecified'}}`,
      `# Recommended action: ${{step.recommended_action || 'Discover or implement a GAS agent for this capability.'}}`,
    ].join('\\n');
  }}
  return [
    'from gas_client import GasClient',
    '',
    'gas = GasClient(',
    `    ${{quotePython(step.gas_server_base_url || 'https://your-gas-server.example')}},`,
    '    openai_api_key=OPENAI_API_KEY,',
    '    gibd_api_key=GIBD_API_KEY,',
    ')',
    '',
    inputLine,
    `${{resultVar}} = gas.execute_task(`,
    `    ${{quotePython(step.agent_id || node.agent_id)}},`,
    `    ${{quotePython(step.instructions || '')}},`,
    `    mode=${{quotePython(step.recommended_mode || node.mode || 'sync')}},`,
    `    input_datasets=${{stepId}}_input_datasets,`,
    ')',
    '',
    `gas.print_task_summary(${{resultVar}})`,
    `${{stepId}}_artifact_urls = [`,
    `    artifact.get('url')`,
    `    for artifact in ${{resultVar}}.get('outputs', {{}}).get('artifacts', [])`,
    `    if artifact.get('url')`,
    `]`,
  ].join('\\n');
}}

function buildDataNodeCode(node) {{
  const variableName = artifactVariableNameForDataNode(node);
  if (node.role === 'goal') {{
    return `${{variableName}} = ${{quotePython(node.description || '')}}`;
  }}
  const sourceStep = node.source_step || 'previous_step';
  const resultVar = variableNameForStep(sourceStep);
  return [
    `# Artifact variable produced by ${{sourceStep}}`,
    `${{variableName}} = next(`,
    '    (artifact.get(\\'url\\')',
    `     for artifact in ${{resultVar}}.get('outputs', {{}}).get('artifacts', [])`,
    '     if artifact.get(\\'url\\')),',
    '    None,',
    ')',
    `if not ${{variableName}}:`,
    `    raise RuntimeError(${{quotePython('Expected artifact URL was not returned for ' + node.label)}})`,
  ].join('\\n');
}}

function nodeBox(id) {{
  const position = positions.get(id);
  const element = nodeElements.get(id);
  if (!position) return null;
  const width = element ? element.offsetWidth : nodeWidth;
  const height = element ? element.offsetHeight : nodeHeight;
  return {{
    x: position.x,
    y: position.y,
    width,
    height,
    centerX: position.x + width / 2,
    centerY: position.y + height / 2
  }};
}}

function boundaryPoint(sourceBox, targetBox) {{
  const dx = targetBox.centerX - sourceBox.centerX;
  const dy = targetBox.centerY - sourceBox.centerY;
  if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) {{
    return {{ x: sourceBox.centerX, y: sourceBox.centerY }};
  }}
  const scaleX = (sourceBox.width / 2 + 8) / Math.max(Math.abs(dx), 0.001);
  const scaleY = (sourceBox.height / 2 + 8) / Math.max(Math.abs(dy), 0.001);
  const scale = Math.min(scaleX, scaleY);
  return {{
    x: sourceBox.centerX + dx * scale,
    y: sourceBox.centerY + dy * scale
  }};
}}

function clamp(value, minValue, maxValue) {{
  return Math.max(minValue, Math.min(maxValue, value));
}}

function applyNodePosition(id) {{
  const position = positions.get(id);
  const element = nodeElements.get(id);
  if (!position || !element) return;
  element.style.left = `${{position.x}}px`;
  element.style.top = `${{position.y}}px`;
}}

function applyAllNodePositions() {{
  nodes.forEach(node => applyNodePosition(node.id));
}}

function relaxLayout(iterations = 1) {{
  const ids = nodes.map(node => node.id);
  for (let iteration = 0; iteration < iterations; iteration += 1) {{
    const forces = new Map(ids.map(id => [id, {{ x: 0, y: 0 }}]));

    for (let i = 0; i < ids.length; i += 1) {{
      for (let j = i + 1; j < ids.length; j += 1) {{
        const a = positions.get(ids[i]);
        const b = positions.get(ids[j]);
        if (!a || !b) continue;
        const aBox = nodeBox(ids[i]) || {{ width: nodeWidth, height: nodeHeight }};
        const bBox = nodeBox(ids[j]) || {{ width: nodeWidth, height: nodeHeight }};
        const ax = a.x + aBox.width / 2;
        const ay = a.y + aBox.height / 2;
        const bx = b.x + bBox.width / 2;
        const by = b.y + bBox.height / 2;
        let dx = ax - bx;
        let dy = ay - by;
        let distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < 0.001) {{
          dx = 1;
          dy = 1;
          distance = 1.414;
        }}

        const overlapX = (aBox.width + bBox.width) / 2 + 36 - Math.abs(dx);
        const overlapY = (aBox.height + bBox.height) / 2 + 36 - Math.abs(dy);
        if (overlapX > 0 && overlapY > 0) {{
          const push = Math.min(overlapX, overlapY) * 0.42;
          const fx = (dx / distance) * push;
          const fy = (dy / distance) * push;
          forces.get(ids[i]).x += fx;
          forces.get(ids[i]).y += fy;
          forces.get(ids[j]).x -= fx;
          forces.get(ids[j]).y -= fy;
        }}

        const repulsionRadius = 360;
        if (distance < repulsionRadius) {{
          const push = ((repulsionRadius - distance) / repulsionRadius) * 3.2;
          const fx = (dx / distance) * push;
          const fy = (dy / distance) * push;
          forces.get(ids[i]).x += fx;
          forces.get(ids[i]).y += fy;
          forces.get(ids[j]).x -= fx;
          forces.get(ids[j]).y -= fy;
        }}
      }}
    }}

    edges.forEach(edge => {{
      const source = positions.get(edge.from);
      const target = positions.get(edge.to);
      if (!source || !target) return;
      const sourceBox = nodeBox(edge.from) || {{ width: nodeWidth, height: nodeHeight }};
      const targetBox = nodeBox(edge.to) || {{ width: nodeWidth, height: nodeHeight }};
      const sx = source.x + sourceBox.width / 2;
      const sy = source.y + sourceBox.height / 2;
      const tx = target.x + targetBox.width / 2;
      const ty = target.y + targetBox.height / 2;
      const dx = tx - sx;
      const dy = ty - sy;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const sourceLayer = Number(nodes.find(node => node.id === edge.from)?.x || 0);
      const targetLayer = Number(nodes.find(node => node.id === edge.to)?.x || 0);
      const desiredDistance = Math.max(260, Math.abs(targetLayer - sourceLayer) * 0.95);
      const pull = (distance - desiredDistance) * 0.004;
      const fx = (dx / distance) * pull;
      const fy = (dy / distance) * pull;
      forces.get(edge.from).x += fx;
      forces.get(edge.from).y += fy;
      forces.get(edge.to).x -= fx;
      forces.get(edge.to).y -= fy;
    }});

    ids.forEach(id => {{
      if (id === draggedNodeId) return;
      const position = positions.get(id);
      const force = forces.get(id);
      if (!position || !force) return;
      if (useGravityForce) {{
        const anchor = nodes.find(node => node.id === id);
        if (anchor) {{
          force.x += (Number(anchor.x) - position.x) * 0.035;
          force.y += (Number(anchor.y) - position.y) * 0.018;
        }}
      }}
      positions.set(id, {{
        x: clamp(position.x + force.x, 18, canvasWidth - nodeWidth - 18),
        y: clamp(position.y + force.y, 18, canvasHeight - nodeHeight - 18)
      }});
    }});
  }}
  applyAllNodePositions();
  renderEdges();
}}

function animateRelax(frames = 60) {{
  if (animationFrame) cancelAnimationFrame(animationFrame);
  let remaining = frames;
  const tick = () => {{
    relaxLayout(2);
    remaining -= 1;
    if (remaining > 0) {{
      animationFrame = requestAnimationFrame(tick);
    }} else {{
      animationFrame = null;
    }}
  }};
  animationFrame = requestAnimationFrame(tick);
}}

function renderEdges() {{
  svg.innerHTML = '<defs><marker id="arrow" markerWidth="14" markerHeight="14" refX="12" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,10 L12,5 z" fill="#50637a"></path></marker></defs>';
  edges.forEach(edge => {{
    const sourceBox = nodeBox(edge.from);
    const targetBox = nodeBox(edge.to);
    if (!sourceBox || !targetBox) return;
    const a = boundaryPoint(sourceBox, targetBox);
    const b = boundaryPoint(targetBox, sourceBox);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    if (useCurvedEdges) {{
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const curveOffset = Math.min(90, Math.max(24, distance * 0.18));
      const normalX = -dy / distance;
      const normalY = dx / distance;
      const c1x = a.x + dx * 0.45 + normalX * curveOffset;
      const c1y = a.y + dy * 0.45 + normalY * curveOffset;
      const c2x = a.x + dx * 0.55 + normalX * curveOffset;
      const c2y = a.y + dy * 0.55 + normalY * curveOffset;
      path.setAttribute('d', `M ${{a.x}} ${{a.y}} C ${{c1x}} ${{c1y}}, ${{c2x}} ${{c2y}}, ${{b.x}} ${{b.y}}`);
    }} else {{
      path.setAttribute('d', `M ${{a.x}} ${{a.y}} L ${{b.x}} ${{b.y}}`);
    }}
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', '#50637a');
    path.setAttribute('stroke-width', '2');
    path.setAttribute('marker-end', 'url(#arrow)');
    svg.appendChild(path);
  }});
}}

function showDetails(node) {{
  let parsedDetails = node;
  try {{ parsedDetails = node.details ? JSON.parse(node.details) : node; }} catch (error) {{}}
  if (node.node_type === 'data') {{
    detailsTitle.textContent = 'Data Details';
    const producedBy = node.role === 'goal' ? 'Client/user request' : (node.source_step || '-');
    const variableName = artifactVariableNameForDataNode(node);
    const consumedBy = edges
      .filter(edge => edge.from === node.id)
      .map(edge => {{
        const target = nodes.find(item => item.id === edge.to);
        return target ? target.label : edge.to;
      }});
    const roleLabel = node.role === 'goal' ? 'User workflow goal' : (node.role === 'final_output' ? 'Final result' : 'Intermediate data/output');
    if (planDetail === 'conceptual') {{
      details.innerHTML = `<h3>${{escapeHtml(node.label)}}</h3><p><strong>Role:</strong> ${{escapeHtml(roleLabel)}}</p><p><strong>Description:</strong> ${{escapeHtml(node.description || '-')}}</p><p><strong>Produced by:</strong> ${{escapeHtml(producedBy)}}</p><p><strong>Consumed by:</strong> ${{escapeHtml(consumedBy.join(', ') || '-')}}</p>`;
    }} else {{
      details.innerHTML = `<h3>${{escapeHtml(node.label)}}</h3><p><strong>Role:</strong> ${{escapeHtml(roleLabel)}}</p><p><strong>Description:</strong> ${{escapeHtml(node.description || '-')}}</p><p><strong>Variable:</strong> <code>${{escapeHtml(variableName)}}</code></p><p><strong>Produced by:</strong> ${{escapeHtml(producedBy)}}</p><p><strong>Consumed by:</strong> ${{escapeHtml(consumedBy.join(', ') || '-')}}</p><h3>Python Code</h3><pre class="code-snippet">${{escapeHtml(buildDataNodeCode(node))}}</pre>`;
    }}
    return;
  }}
  detailsTitle.textContent = 'Step Details';
  const dependencies = Array.isArray(parsedDetails.depends_on) ? parsedDetails.depends_on.join(', ') : '-';
  const inputs = Array.isArray(parsedDetails.input_from_steps) ? parsedDetails.input_from_steps.join(', ') : '-';
  const outputs = Array.isArray(parsedDetails.expected_outputs) ? parsedDetails.expected_outputs.join(', ') : '-';
  const validation = Array.isArray(parsedDetails.validation_checks) ? parsedDetails.validation_checks.join('; ') : '-';
  const matchStatus = parsedDetails.match_status || (node.agent_id ? 'matched' : 'unmatched');
  const gapDetails = matchStatus === 'unmatched'
    ? `<p><strong>Required capability:</strong> ${{escapeHtml(parsedDetails.required_capability || '-')}}</p><p><strong>Recommended action:</strong> ${{escapeHtml(parsedDetails.recommended_action || '-')}}</p>`
    : '';
  if (planDetail === 'conceptual') {{
    details.innerHTML = `<h3>${{escapeHtml(node.label)}}</h3><p><strong>Step:</strong> ${{escapeHtml(node.operation_title || '-')}}</p><p><strong>Agent:</strong> ${{escapeHtml(node.agent_name || node.agent_id || '-')}}</p><p><strong>Match status:</strong> ${{escapeHtml(matchStatus)}}</p>${{gapDetails}}<p><strong>Purpose:</strong> ${{escapeHtml(node.description || '-')}}</p><p><strong>Depends on:</strong> ${{escapeHtml(dependencies)}}</p><p><strong>Input from steps:</strong> ${{escapeHtml(inputs)}}</p><p><strong>Expected outputs:</strong> ${{escapeHtml(outputs)}}</p><p><strong>Confidence:</strong> ${{escapeHtml(parsedDetails.confidence || '-')}}</p>`;
  }} else {{
    details.innerHTML = `<h3>${{escapeHtml(node.label)}}</h3><p><strong>Step:</strong> ${{escapeHtml(node.operation_title || '-')}}</p><p><strong>Agent:</strong> ${{escapeHtml(node.agent_name || node.agent_id || '-')}}</p><p><strong>Match status:</strong> ${{escapeHtml(matchStatus)}}</p>${{gapDetails}}<p><strong>Result variable:</strong> <code>${{escapeHtml(variableNameForStep(parsedDetails.step_id || node.step_id))}}</code></p><p><strong>Purpose:</strong> ${{escapeHtml(node.description || '-')}}</p><p><strong>Instructions:</strong> ${{escapeHtml(parsedDetails.instructions || '-')}}</p><p><strong>Depends on:</strong> ${{escapeHtml(dependencies)}}</p><p><strong>Input from steps:</strong> ${{escapeHtml(inputs)}}</p><p><strong>Expected outputs:</strong> ${{escapeHtml(outputs)}}</p><p><strong>Validation:</strong> ${{escapeHtml(validation)}}</p><h3>Python Code</h3><pre class="code-snippet">${{escapeHtml(buildOperationCode(node, parsedDetails))}}</pre>`;
  }}
}}
nodes.forEach((node, index) => {{
  const div = document.createElement('div');
  const roleClass = node.role === 'goal' ? 'goal' : (node.role === 'final_output' ? 'final' : (node.role === 'unmatched' ? 'unmatched' : ''));
  div.className = `node ${{node.node_type}} ${{roleClass}}`;
  div.setAttribute('role', 'button');
  div.setAttribute('tabindex', '0');
  const subtitle = node.node_type === 'operation' ? (node.operation_title || node.agent_id || 'Operation') : '';
  div.innerHTML = `<strong>${{escapeHtml(node.label)}}</strong>${{subtitle ? `<span>${{escapeHtml(subtitle)}}</span>` : ''}}`;
  div.addEventListener('click', () => showDetails(node));
  div.addEventListener('keydown', event => {{
    if (event.key === 'Enter' || event.key === ' ') {{
      event.preventDefault();
      showDetails(node);
      div.blur();
    }}
  }});
  div.addEventListener('pointerdown', event => {{
    event.preventDefault();
    event.stopPropagation();
    draggedNodeId = node.id;
    if (animationFrame) {{
      cancelAnimationFrame(animationFrame);
      animationFrame = null;
    }}
    const pointerId = event.pointerId;
    div.setPointerCapture(pointerId);
    const startX = event.clientX;
    const startY = event.clientY;
    const original = positions.get(node.id);
    const onMove = moveEvent => {{
      const nextX = Math.max(8, original.x + (moveEvent.clientX - startX) / zoom);
      const nextY = Math.max(8, original.y + (moveEvent.clientY - startY) / zoom);
      positions.set(node.id, {{
        x: clamp(nextX, 8, canvasWidth - nodeWidth - 8),
        y: clamp(nextY, 8, canvasHeight - nodeHeight - 8)
      }});
      applyNodePosition(node.id);
      relaxLayout(1);
    }};
    const onUp = endEvent => {{
      draggedNodeId = null;
      if (div.hasPointerCapture(pointerId)) {{
        div.releasePointerCapture(pointerId);
      }}
      div.blur();
      div.removeEventListener('pointermove', onMove);
      div.removeEventListener('pointerup', onUp);
      div.removeEventListener('pointercancel', onUp);
      if (useGravityForce) {{
        animateRelax(45);
      }} else {{
        renderEdges();
      }}
    }};
    div.addEventListener('pointermove', onMove);
    div.addEventListener('pointerup', onUp);
    div.addEventListener('pointercancel', onUp);
  }});
  graphCanvas.appendChild(div);
  nodeElements.set(node.id, div);
  positions.set(node.id, {{ x: Number(node.x), y: Number(node.y) }});
  applyNodePosition(node.id);
}});
relaxLayout(180);
fitToView();
document.getElementById('zoom-in').addEventListener('click', event => {{
  setZoom(zoom * 1.18);
  event.currentTarget.blur();
}});
document.getElementById('zoom-out').addEventListener('click', event => {{
  setZoom(zoom / 1.18);
  event.currentTarget.blur();
}});
document.getElementById('zoom-fit').addEventListener('click', event => {{
  fitToView();
  event.currentTarget.blur();
}});
document.getElementById('save-png').addEventListener('click', event => {{
  exportGraphPng();
  event.currentTarget.blur();
}});
graph.addEventListener('wheel', event => {{
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
  zoomAt(zoom * factor, event.clientX, event.clientY);
}}, {{ passive: false }});
graph.addEventListener('pointerdown', event => {{
  if (event.target.closest('.node') || event.target.closest('.graph-toolbar')) return;
  const pointerId = event.pointerId;
  graph.setPointerCapture(pointerId);
  graph.classList.add('panning');
  const startX = event.clientX;
  const startY = event.clientY;
  const originalPanX = panX;
  const originalPanY = panY;
  const onMove = moveEvent => {{
    panX = originalPanX + moveEvent.clientX - startX;
    panY = originalPanY + moveEvent.clientY - startY;
    updateTransform();
  }};
  const onUp = endEvent => {{
    if (graph.hasPointerCapture(pointerId)) {{
      graph.releasePointerCapture(pointerId);
    }}
    graph.classList.remove('panning');
    graph.removeEventListener('pointermove', onMove);
    graph.removeEventListener('pointerup', onUp);
    graph.removeEventListener('pointercancel', onUp);
  }};
  graph.addEventListener('pointermove', onMove);
  graph.addEventListener('pointerup', onUp);
  graph.addEventListener('pointercancel', onUp);
}});
document.getElementById('curve-edges').addEventListener('change', event => {{
  useCurvedEdges = event.target.checked;
  renderEdges();
  event.target.blur();
}});
document.getElementById('gravity-force').addEventListener('change', event => {{
  useGravityForce = event.target.checked;
  if (useGravityForce) animateRelax(45);
  event.target.blur();
}});
document.getElementById('toggle-details').addEventListener('click', event => {{
  main.classList.toggle('details-hidden');
  event.target.textContent = main.classList.contains('details-hidden') ? 'Show details' : 'Hide details';
  setTimeout(fitToView, 220);
  event.currentTarget.blur();
}});
window.addEventListener('resize', fitToView);
</script>
</body>
</html>
"""
        path.write_text(html_text, encoding="utf-8")
        return str(path)

    def _write_requested_artifacts(self, query: str, plan: dict[str, Any], plan_outputs: list[str]) -> dict[str, str]:
        files: dict[str, str] = {}
        if "workflow_json" in plan_outputs:
            files["workflow_plan_file"] = self._write_json_plan(query, plan)
        if "human_readable" in plan_outputs:
            files["human_readable_plan_file"] = self._write_markdown_plan(query, plan)
        if "gas_client_python" in plan_outputs:
            files["gas_client_python_file"] = self._write_python_workflow(query, plan)
        if "notebook_skeleton" in plan_outputs:
            files["notebook_skeleton_file"] = self._write_notebook_skeleton(query, plan)
        if "interactive_workflow_graph" in plan_outputs:
            files["interactive_workflow_graph_file"] = self._write_interactive_graph(query, plan, linked_files=files)
        return files

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        self.reset_metrics()
        self.input_tokens = 0
        self.output_tokens = 0
        self.ensure_directory(self.output_dir)

        parameters = self._parameters()
        gas_server_urls = self._gas_server_urls(parameters)
        plan_outputs = self._plan_outputs(parameters, query=query)
        plan_detail = str(parameters.get("plan_detail") or "executable").strip().lower()
        if plan_detail not in {"conceptual", "executable"}:
            raise ValueError("plan_detail must be either 'conceptual' or 'executable'.")
        include_validation_steps = self._bool_param(parameters.get("include_validation_steps"), True)
        max_steps = int(parameters.get("max_steps") or 12)

        self.emit_progress(
            progress_callback,
            stage="start",
            message="I will discover GAS capabilities, match agents to workflow steps, and return a plan for client-side execution.",
            data={"plan_outputs": plan_outputs, "plan_detail": plan_detail},
        )

        catalogs = self._discover_capabilities(gas_server_urls, progress_callback)
        self.emit_progress(
            progress_callback,
            stage="planning",
            message="I am decomposing the user goal and matching each step to suitable GAS agent capabilities.",
            data={"max_steps": max_steps},
        )

        plan: dict[str, Any]
        try:
            messages = self._build_planning_prompt(
                query,
                catalogs,
                plan_detail=plan_detail,
                include_validation_steps=include_validation_steps,
                max_steps=max_steps,
            )
            self.increment_llm_calls()
            response = self._chat_completion(messages)
            usage = getattr(response, "usage", None)
            if usage:
                self.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.output_tokens += getattr(usage, "completion_tokens", 0) or 0
            content = response.choices[0].message.content
            plan = self._extract_json_object(content)
            self.emit_progress(
                progress_callback,
                stage="method_selection",
                message="The workflow planning model returned a structured plan. I will validate and normalize its steps.",
                data={"llm_calls": self.llm_calls},
            )
        except Exception as exc:
            self.emit_progress(
                progress_callback,
                stage="fallback_start",
                message="The model-backed plan could not be completed, so I will generate a conservative fallback plan from the discovered capabilities.",
                data={"error": str(exc)},
            )
            plan = self._fallback_plan(query, catalogs, str(exc))
            self.emit_progress(
                progress_callback,
                stage="fallback_complete",
                message="The fallback workflow plan is ready for review.",
                data={},
            )

        plan = self._normalize_plan(plan, query, catalogs, plan_detail=plan_detail)
        plan["requested_plan_outputs"] = plan_outputs
        plan["plan_detail"] = plan_detail
        plan["planning_detail_type"] = plan_detail
        plan["execution_policy"] = "plan_only"

        self.emit_progress(
            progress_callback,
            stage="artifact_generation",
            message="I am writing the requested workflow plan artifacts.",
            data={"plan_outputs": plan_outputs},
        )
        files = self._write_requested_artifacts(query, plan, plan_outputs)

        summary = (
            f"Generated a {plan_detail} GAS workflow plan with {len(plan.get('workflow_steps', []))} step(s) "
            f"using {plan.get('discovery', {}).get('agent_count', 0)} discovered agent capability document(s). "
            f"Readiness: {plan.get('readiness')}. The planning agent returned artifacts for "
            f"{', '.join(plan_outputs)} and does not execute downstream services."
        )
        self.emit_progress(
            progress_callback,
            stage="complete",
            message="Workflow planning is complete. The final response will include the selected plan artifacts.",
            data={"artifact_count": len(files), "readiness": plan.get("readiness")},
        )

        validation_status = "passed" if plan.get("workflow_steps") else "warning"
        unmatched_count = len(plan.get("unmatched_steps") or [])
        if unmatched_count:
            validation_status = "warning"
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": round(time.time() - start_time, 2),
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "inputs": {
                "text": query,
                "dataset_paths": self.normalize_dataset_paths(input_dataset_paths),
                "parameters": {
                    "gas_servers": gas_server_urls,
                    "plan_outputs": plan_outputs,
                    "plan_detail": plan_detail,
                    "include_validation_steps": include_validation_steps,
                    "max_steps": max_steps,
                },
            },
            "outputs": {
                "text": summary,
                "workflow_plan": plan,
                **files,
            },
            "metrics": self.metrics(number_of_artifacts=len(files)),
            "environment": {
                "python_version": platform.python_version(),
                "domain-specific libraries": ["requests"],
            },
            "stochasticity": {
                "used": True,
                "controls": [self._last_temperature_control],
            },
            "reproducibility_notes": [
                "The planning agent discovers capability documents at request time.",
                "Generated code and notebook artifacts are returned to the client but are not executed by this agent.",
            ],
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "task": query,
                        "gas_servers": gas_server_urls or ["current_server_local_capabilities"],
                        "plan_outputs": plan_outputs,
                    },
                    "Outputs": {
                        "summary": summary,
                        **files,
                    },
                },
                "Provenance": {
                    "Lineage": [
                        "Read GetCapabilities and DescribeAgent information.",
                        "Matched user goal steps to advertised GAS agent capabilities.",
                        "Generated selected client-side planning artifacts.",
                    ],
                    "Tool Calls": {"count": self.tool_calls},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Validation": {
                    "status": validation_status,
                    "checks": [
                        {
                            "name": "capability_discovery",
                            "status": "passed",
                            "message": f"Discovered {plan.get('discovery', {}).get('agent_count', 0)} agent capability document(s).",
                        },
                        {
                            "name": "workflow_steps",
                            "status": validation_status,
                            "message": f"Plan contains {len(plan.get('workflow_steps', []))} workflow step(s).",
                        },
                        {
                            "name": "capability_matching",
                            "status": "warning" if unmatched_count else "passed",
                            "message": (
                                f"{unmatched_count} workflow step(s) could not be matched to discovered GAS capabilities."
                                if unmatched_count
                                else "All workflow steps were matched to discovered GAS capabilities."
                            ),
                        },
                        {
                            "name": "plan_artifacts",
                            "status": "passed" if files else "warning",
                            "message": f"Created {len(files)} requested plan artifact(s).",
                        },
                    ],
                },
                "Assumptions and Limitations": {
                    "assumptions": plan.get("assumptions", []),
                    "limitations": [
                        "This agent plans workflows and generates client-side execution artifacts, but it does not run downstream GAS services.",
                        *plan.get("limitations", []),
                    ],
                },
            },
        }
