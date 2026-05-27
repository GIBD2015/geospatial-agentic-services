"""Small public Python client for Geospatial Agentic Services (GAS).

The client intentionally mirrors the GAS operation names: GetCapabilities,
DescribeAgent, ExecuteTask, GetTaskStatus, GetTaskResult, and CancelTask.
It is meant to be easy to read and copy into notebooks, scripts, browser
backends, or AI orchestrator integrations.
"""

from __future__ import annotations
import base64
from collections.abc import Mapping
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
import requests


class GasAgentClient:
    """Convenience wrapper bound to one GAS agent service.

    Users normally get this object from `client.agent("agent_id")`. It lets
    them call `agent.execute_task(...)` without passing the same `agent_id`
    repeatedly.
    """

    def __init__(self, client: GasClient, agent_id: str) -> None:
        self.client = client
        self.agent_id = client.resolve_agent_id(agent_id)

    def describe(self, refresh: bool = False) -> dict:
        """Fetch this agent's DescribeAgent document."""
        return self.client.describe_agent(self.agent_id, refresh=refresh)

    def operations(self) -> dict[str, str]:
        """Return the shared GAS operation URLs resolved for this agent."""
        return self.client.get_supported_operations(self.agent_id)

    def status(self):
        """Call GetAgentStatus for this agent."""
        return self.client.get_agent_status(self.agent_id)

    def execute_task(self, instructions: str, **kwargs):
        """Call ExecuteTask for this agent using natural-language instructions."""
        return self.client.execute_task(self.agent_id, instructions, **kwargs)

    def execute_task_request(self, request_body: dict, *, timeout: int | None = None):
        """Call ExecuteTask with a complete canonical GAS request body."""
        return self.client.execute_task_request(self.agent_id, request_body, timeout=timeout)

    def get_task_status(self, task_id: str):
        """Call GetTaskStatus for one task created by this agent."""
        return self.client.get_task_status(self.agent_id, task_id)

    def get_task_result(self, task_id: str):
        """Call GetTaskResult for one completed task created by this agent."""
        return self.client.get_task_result(self.agent_id, task_id)

    def wait_for_task(self, task_id: str, **kwargs):
        """Poll this agent's task until it reaches a terminal status."""
        return self.client.wait_for_task(self.agent_id, task_id, **kwargs)

    def cancel_task(self, task_id: str):
        """Request best-effort cancellation for one task created by this agent."""
        return self.client.cancel_task(self.agent_id, task_id)


class GasClient:
    """
    Python client for Geospatial Agentic Services.

    The method names intentionally follow the GAS operation names:
    GetCapabilities, DescribeAgent, GetAgentStatus, ExecuteTask,
    GetTaskStatus, GetTaskResult, and CancelTask.
    """

    # Task statuses that mean the server-side task has reached a final state.
    # `wait_for_task()` stops polling when it sees one of these values.
    TERMINAL_STATUSES = {"successful", "failed", "canceled", "rejected"}

    def __init__(
        self,
        server_url: str,
        *,
        default_credentials: Mapping | None = None,
        artifact_delivery: str = "URL",
        timeout: int = 30,
        session=None,
        load_capabilities: bool = True,
    ) -> None:
        """Create a GAS client.

        Parameters are intentionally simple:
        - `server_url` is the root GAS server URL, such as
          `http://127.0.0.1:4042`.
        - `default_credentials` is an optional dictionary of server/agent
          credential keys to include with ExecuteTask requests by default.
          Per-request `credentials` overrides any client defaults.
        - `artifact_delivery` controls whether artifacts are returned as URLs
          or encoded file payloads by default.
        - `session` may be supplied by tests or advanced users who need custom
          HTTP behavior.
        """
        self.server_url = server_url.rstrip("/")
        self.default_credentials = dict(default_credentials or {})
        self.artifact_delivery = artifact_delivery
        self.timeout = timeout
        self.session = session or requests.Session()
        self._capabilities: dict | None = None
        self._agent_descriptions: dict[str, dict] = {}

        if load_capabilities:
            self.get_capabilities()

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------
    # These are private implementation details behind the public
    # `print_stream_event()` and `print_task_summary()` methods. Keeping them
    # inside the class makes the public-facing module easier to scan.
    @staticmethod
    def _format_display_value(value):
        if value in (None, "", [], {}):
            return "-"
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{value:,.2f}"
        return str(value)

    @staticmethod
    def _format_duration_seconds(value):
        if value in (None, ""):
            return "-"
        try:
            return f"{float(value):.2f}s"
        except Exception:
            return str(value)

    @staticmethod
    def _stream_event_time(event: Mapping) -> str:
        """Return a compact local-time string for a GAS streaming event."""

        timestamp = event.get("timestamp")
        if not timestamp:
            return "--:--:--"
        try:
            return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
        except Exception:
            return str(timestamp)

    @staticmethod
    def _display_agent_name_from_event(event: Mapping) -> str | None:
        display_name = event.get("_display_agent_name")
        if display_name:
            return str(display_name)
        agent = event.get("agent") if isinstance(event.get("agent"), Mapping) else {}
        return agent.get("name") or agent.get("id")

    @classmethod
    def _format_stream_message(cls, event: Mapping, display_agent_name: str | None) -> str:
        message = str(event.get("message") or event.get("status") or "")
        event_type = event.get("event")
        if event_type != "progress":
            return message

        if message.startswith("The user wants help from "):
            return "I received your request."

        if " is still working. Long LLM calls, code execution, or geospatial file processing can take a little while." in message:
            return "I am still working. Long LLM calls, code execution, or geospatial file processing can take a little while."

        if display_agent_name and message == f"The {display_agent_name} reported a workflow update.":
            return "I reported a workflow update."

        return message

    @classmethod
    def _print_stream_event(cls, event: Mapping) -> None:
        """Print one GAS stream event using a readable timestamped format."""

        time_text = cls._stream_event_time(event)
        event_type = event.get("event")
        event_name = cls._format_display_value(event_type)
        display_agent_name = cls._display_agent_name_from_event(event)
        message = cls._format_stream_message(event, display_agent_name)

        if event_type == "task_result":
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
            task_id = task.get("id")
            print(f"[{time_text}] task_result: final task received {task_id or ''}".rstrip())
            return

        label = display_agent_name if event_type == "progress" and display_agent_name else event_name
        print(f"[{time_text}] {label}: {message}".rstrip())

    @classmethod
    def _print_task_summary(cls, task_result: Mapping) -> None:
        """Print a compact, human-readable summary of a GAS task result."""

        task = task_result.get("task") if isinstance(task_result.get("task"), Mapping) else {}
        agent = task_result.get("agent") if isinstance(task_result.get("agent"), Mapping) else {}
        outputs = task_result.get("outputs") if isinstance(task_result.get("outputs"), Mapping) else {}
        execution = task_result.get("execution") if isinstance(task_result.get("execution"), Mapping) else {}
        provenance = task_result.get("provenance") if isinstance(task_result.get("provenance"), Mapping) else {}
        diagnostics = task_result.get("diagnostics") if isinstance(task_result.get("diagnostics"), Mapping) else {}
        token_usage = provenance.get("token_usage") if isinstance(provenance.get("token_usage"), Mapping) else {}
        artifacts = outputs.get("artifacts") if isinstance(outputs.get("artifacts"), list) else []

        input_tokens = token_usage.get("input_tokens")
        output_tokens = token_usage.get("output_tokens")
        total_tokens = token_usage.get("total_tokens")
        if total_tokens in (None, "") and isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens = input_tokens + output_tokens

        print("\n" + "=" * 72)
        print("GAS Task Summary")
        print("=" * 72)
        print(f"Task         : {cls._format_display_value(task.get('id'))}")
        print(f"Status       : {cls._format_display_value(task.get('status'))}")
        print(f"Agent        : {cls._format_display_value(agent.get('name') or agent.get('id'))}")
        print(f"Version      : {cls._format_display_value(agent.get('version'))}")
        print(f"Model        : {cls._format_display_value(agent.get('model'))}")
        print(f"Duration     : {cls._format_duration_seconds(execution.get('duration_seconds'))}")
        print(f"Iterations   : {cls._format_display_value(execution.get('iterations'))}")

        print("\nUsage")
        print("-----")
        print(f"LLM calls    : {cls._format_display_value(provenance.get('llm_calls'))}")
        print(f"Tool calls   : {cls._format_display_value(provenance.get('tool_calls'))}")
        print(f"Input tokens : {cls._format_display_value(input_tokens)}")
        print(f"Output tokens: {cls._format_display_value(output_tokens)}")
        print(f"Total tokens : {cls._format_display_value(total_tokens)}")

        print("\nOutputs")
        print("-------")
        print(f"Summary      : {cls._format_display_value(outputs.get('summary'))}")
        print(f"Artifacts    : {len(artifacts)}")

        for index, artifact in enumerate(artifacts, start=1):
            if not isinstance(artifact, Mapping):
                continue
            spatial_metadata = artifact.get("spatial_metadata") if isinstance(artifact.get("spatial_metadata"), Mapping) else {}
            name = artifact.get("filename") or artifact.get("name") or f"artifact_{index}"
            artifact_type = artifact.get("type") or spatial_metadata.get("type")
            artifact_format = artifact.get("format") or artifact.get("mime_type")
            size_bytes = artifact.get("size_bytes")
            print(f"  {index}. {name}")
            print(
                "     "
                f"type={cls._format_display_value(artifact_type)} "
                f"format={cls._format_display_value(artifact_format)} "
                f"size={cls._format_display_value(size_bytes)} bytes"
            )
            if artifact.get("url"):
                print(f"     url={artifact['url']}")

        print("\nDiagnostics")
        print("-----------")
        print(f"Has error    : {cls._format_display_value(diagnostics.get('has_error'))}")
        if diagnostics.get("error"):
            print(f"Error        : {diagnostics.get('error')}")
        warnings = diagnostics.get("warnings") if isinstance(diagnostics.get("warnings"), list) else []
        if warnings:
            print("Warnings     :")
            for warning in warnings:
                print(f"  - {warning}")
        else:
            print("Warnings     : -")
        print("=" * 72)

    def print_stream_event(self, event: Mapping, *, agent_name: str | None = None) -> None:
        """Print one GAS stream event using a readable timestamped format."""

        if agent_name and isinstance(event, Mapping):
            display_event = dict(event)
            display_event.setdefault("_display_agent_name", agent_name)
            self._print_stream_event(display_event)
            return
        self._print_stream_event(event)

    def print_task_summary(self, task_result: Mapping) -> None:
        """Print a compact, human-readable summary of a GAS task result."""

        self._print_task_summary(task_result)

    # ------------------------------------------------------------------
    # Discovery operations
    # ------------------------------------------------------------------
    def get_capabilities(self, refresh: bool = False) -> dict:
        """Fetch the GAS GetCapabilities document."""

        if self._capabilities is not None and not refresh:
            return self._capabilities

        response = self.session.get(self._capabilities_url(), timeout=self.timeout)
        self._capabilities = self._json_or_raise(response)
        self._agent_descriptions.clear()
        return self._capabilities

    def list_agents(self, refresh: bool = False) -> list[str]:
        """Return agent IDs advertised by GetCapabilities."""

        capabilities = self.get_capabilities(refresh=refresh)
        return [
            str(agent.get("agent_id") or agent["name"])
            for agent in capabilities.get("agents", [])
            if isinstance(agent, dict) and (agent.get("agent_id") or agent.get("name"))
        ]

    def describe_agent(self, agent_id: str, refresh: bool = False) -> dict:
        """Fetch one DescribeAgent document."""

        resolved_agent_id = self.resolve_agent_id(agent_id)
        if resolved_agent_id in self._agent_descriptions and not refresh:
            return self._agent_descriptions[resolved_agent_id]

        agent_entry = self._agent_entry(resolved_agent_id)
        describe_url = agent_entry.get("DescribeAgent")
        if not isinstance(describe_url, str) or not describe_url:
            describe_template = self._capability_operation_url("describe_agent")
            if not isinstance(describe_template, str) or "{agent_id}" not in describe_template:
                raise GasClientError(f"No DescribeAgent URL is advertised for agent '{resolved_agent_id}'.")
            describe_url = describe_template.replace("{agent_id}", resolved_agent_id)

        response = self.session.get(
            self._absolute_url(describe_url),
            headers=self._headers(),
            timeout=self.timeout,
        )
        description = self._json_or_raise(response)
        self._agent_descriptions[resolved_agent_id] = description
        return description

    def resolve_agent_id(self, agent_id: str) -> str:
        """Resolve an agent ID exactly as advertised by GetCapabilities."""

        requested = agent_id.strip("/")
        for advertised in self.list_agents():
            if requested == advertised:
                return advertised
        raise GasClientError(
            f"Unknown agent_id '{agent_id}'. Available agents: {', '.join(self.list_agents()) or '(none)'}"
        )

    def agent(self, agent_id: str) -> GasAgentClient:
        """Bind the client to one agent for repeated calls."""

        return GasAgentClient(self, agent_id)

    def discover(self, refresh: bool = False) -> dict:
        """Return a compact discovery summary."""

        return {
            "server_url": self.server_url,
            "gas_version": self.get_capabilities(refresh=refresh).get("version"),
            "agents": self.get_agent_catalog(refresh=refresh),
        }

    def get_agent_catalog(self, refresh: bool = False, include_descriptions: bool = False) -> list[dict]:
        """Return a user-friendly catalog of advertised GAS agents."""

        catalog = []
        for entry in self.get_capabilities(refresh=refresh).get("agents", []):
            if not isinstance(entry, dict):
                continue
            agent_id = entry.get("agent_id") or entry.get("name")
            if not agent_id:
                continue
            item = {
                "agent_id": str(agent_id),
                "name": entry.get("name") or agent_id,
                "description": entry.get("description"),
                "DescribeAgent": entry.get("DescribeAgent"),
            }
            if include_descriptions:
                description = self.describe_agent(str(agent_id))
                profile = description.get("profile", {}) if isinstance(description.get("profile"), dict) else {}
                item["description"] = item["description"] or profile.get("description")
                item["operations"] = sorted(self.get_supported_operations(str(agent_id)))
                item["skills"] = description.get("skills", [])
            catalog.append(item)
        return catalog

    def find_agents(self, keyword: str, include_descriptions: bool = False) -> list[dict]:
        """Search the advertised agent catalog by keyword."""

        needle = keyword.lower()
        return [
            item
            for item in self.get_agent_catalog(include_descriptions=include_descriptions)
            if needle in json.dumps(item, default=str).lower()
        ]

    def get_orchestrator_tools(self, include_descriptions: bool = True) -> list[dict]:
        """Return simple tool specs that AI orchestrators can expose to models."""

        tools = []
        for agent in self.get_agent_catalog(include_descriptions=include_descriptions):
            agent_id = str(agent["agent_id"])
            tool_name = f"gas_{self._safe_tool_name(agent_id)}"
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": agent.get("description") or f"Execute a GAS task with {agent_id}.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "instructions": {
                                    "type": "string",
                                    "description": "Natural-language task instructions for the GAS agent.",
                                },
                                "input_datasets": {
                                    "type": "array",
                                    "description": "Input dataset URLs, server paths, or encoded dataset objects.",
                                    "items": {"anyOf": [{"type": "string"}, {"type": "object"}]},
                                },
                                "artifact_delivery": {
                                    "type": "string",
                                    "enum": ["URL", "Encoded"],
                                    "default": self.artifact_delivery,
                                },
                                "parameters": {
                                    "type": "object",
                                    "description": "Additional GAS execution parameters.",
                                },
                            },
                            "required": ["instructions"],
                        },
                    },
                    "metadata": {
                        "agent_id": agent_id,
                        "operation": "execute_task",
                    },
                }
            )
        return tools

    def get_supported_operations(self, agent_id: str | None = None) -> dict:
        """Return supported operation URLs from one agent or all agents."""

        if agent_id is not None:
            return self._operation_map(agent_id)

        return {
            current_agent_id: self.get_supported_operations(current_agent_id)
            for current_agent_id in self.list_agents()
        }

    def get_agent_operation_url(self, agent_id: str, operation_id: str) -> str:
        """Return the advertised URL for a GAS operation."""

        operation_url = self.get_supported_operations(agent_id).get(operation_id)
        if not operation_url:
            raise GasClientError(f"Operation '{operation_id}' is not listed for agent '{agent_id}'.")
        return str(operation_url)

    def get_agent_status(self, agent_id: str):
        """Call GetAgentStatus."""

        response = self.session.get(
            self.get_agent_operation_url(agent_id, "get_agent_status"),
            timeout=self.timeout,
        )
        return self._json_or_text(response)

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------
    def execute_task(
        self,
        agent_id: str,
        instructions: str,
        *,
        mode: str = "sync",
        input_datasets=None,
        artifact_delivery: str | None = None,
        parameters: dict | None = None,
        credentials: dict | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ):
        """Call ExecuteTask with mode='sync', 'async', or 'stream'."""

        if mode not in {"sync", "async", "stream"}:
            raise ValueError("mode must be one of 'sync', 'async', or 'stream'.")

        response = self.session.post(
            self._operation_url(agent_id, "execute_task", fallback_path="tasks"),
            json=self.build_execute_task_request(
                instructions,
                mode=mode,
                input_datasets=input_datasets,
                artifact_delivery=artifact_delivery,
                parameters=parameters,
                credentials=credentials,
                model=model,
            ),
            headers=self._headers(),
            stream=(mode == "stream"),
            timeout=timeout or self.timeout,
        )

        if mode == "stream":
            self._raise_for_status(response)
            return self._stream_events(response, display_agent_name=self._agent_display_name(agent_id))
        return self._json_or_raise(response)

    def execute_task_request(
        self,
        agent_id: str,
        request_body: dict,
        *,
        timeout: int | None = None,
    ):
        """Send a canonical GAS ExecuteTask request body exactly as provided."""

        if not isinstance(request_body, dict):
            raise ValueError("request_body must be a dictionary.")
        task = request_body.get("task") if isinstance(request_body.get("task"), dict) else {}
        mode = str(task.get("mode") or "sync").strip().lower()
        if mode not in {"sync", "async", "stream"}:
            raise ValueError("request_body.task.mode must be one of 'sync', 'async', or 'stream'.")

        response = self.session.post(
            self._operation_url(agent_id, "execute_task", fallback_path="tasks"),
            json=request_body,
            headers=self._headers(),
            stream=(mode == "stream"),
            timeout=timeout or self.timeout,
        )
        if mode == "stream":
            self._raise_for_status(response)
            return self._stream_events(response, display_agent_name=self._agent_display_name(agent_id))
        return self._json_or_raise(response)

    def get_task_status(self, agent_id: str, task_id: str, *, timeout: int | None = None) -> dict:
        """Call GetTaskStatus."""

        response = self.session.get(
            self._task_operation_url(agent_id, "get_task_status", task_id),
            headers=self._headers(),
            timeout=timeout or self.timeout,
        )
        return self._json_or_raise(response)

    def get_task_result(self, agent_id: str, task_id: str, *, timeout: int | None = None) -> dict:
        """Call GetTaskResult."""

        response = self.session.get(
            self._task_operation_url(agent_id, "get_task_result", task_id),
            headers=self._headers(),
            timeout=timeout or self.timeout,
        )
        return self._json_or_raise(response)

    def wait_for_task(
        self,
        agent_id: str,
        task_id: str,
        *,
        poll_interval: float = 5,
        timeout_seconds: float = 900,
    ) -> dict:
        """Poll GetTaskStatus until terminal, then return GetTaskResult."""

        started = time.monotonic()
        while True:
            task_status = self.get_task_status(agent_id, task_id)
            if self.get_task_status_value(task_status) in self.TERMINAL_STATUSES:
                return self.get_task_result(agent_id, task_id)
            if time.monotonic() - started > timeout_seconds:
                raise GasTaskTimeoutError(f"Task '{task_id}' did not finish within {timeout_seconds} seconds.")
            time.sleep(poll_interval)

    def cancel_task(self, agent_id: str, task_id: str) -> dict:
        """Call CancelTask."""

        response = self.session.post(
            self._task_operation_url(agent_id, "cancel_task", task_id),
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._json_or_raise(response)

    def build_execute_task_request(
        self,
        instructions: str,
        *,
        mode: str = "sync",
        input_datasets=None,
        artifact_delivery: str | None = None,
        parameters: dict | None = None,
        credentials: dict | None = None,
        model: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Build the canonical GAS ExecuteTask request body."""

        if mode not in {"sync", "async", "stream"}:
            raise ValueError("mode must be one of 'sync', 'async', or 'stream'.")

        resolved_artifact_delivery = artifact_delivery or self.artifact_delivery
        if resolved_artifact_delivery not in {"URL", "Encoded", "url", "encoded"}:
            raise ValueError("artifact_delivery must be either 'URL' or 'Encoded'.")

        request_parameters = dict(parameters or {})
        if model:
            request_parameters.setdefault("model", model)

        request_credentials = dict(self.default_credentials)
        request_credentials.update(dict(credentials or {}))

        payload = {
            "task": {
                "instructions": instructions,
                "mode": mode,
            },
            "outputs": {
                "artifact_delivery": "Encoded" if resolved_artifact_delivery.lower() == "encoded" else "URL",
            },
        }

        if input_datasets is not None:
            payload["inputs"] = {
                "input_datasets": [
                    dict(item) if isinstance(item, Mapping) else item
                    for item in self._as_list(input_datasets)
                ]
            }
        if request_parameters:
            payload["parameters"] = request_parameters
        if request_credentials:
            payload["credentials"] = request_credentials
        if metadata:
            payload["metadata"] = dict(metadata)
        return payload

    def encode_dataset_file(self, file_path: str | Path) -> dict:
        """Encode a local file as a base64 input dataset object."""

        path = Path(file_path)
        return {
            "filename": path.name,
            "encoding": "base64",
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    def get_task_id(self, task: dict) -> str:
        """Extract task.id from a standard GAS task response."""

        task_id = task.get("task", {}).get("id")
        if not task_id:
            raise GasClientError("Response did not include task.id.")
        return str(task_id)

    def get_task_status_value(self, task: dict) -> str | None:
        """Extract task.status from a standard GAS task response."""

        status = task.get("task", {}).get("status")
        return str(status) if status else None

    def get_artifacts(self, task: dict) -> list[dict]:
        """Return artifact metadata from a standard GAS task response."""

        artifacts = task.get("outputs", {}).get("artifacts", [])
        if not isinstance(artifacts, list):
            return []
        return [artifact for artifact in artifacts if isinstance(artifact, dict)]

    def get_artifact_urls(self, task: dict) -> list[str]:
        """Return artifact URLs from a standard GAS task response."""

        return [
            artifact["url"]
            for artifact in self.get_artifacts(task)
            if isinstance(artifact.get("url"), str)
        ]

    def get_value_by_key(self, data, keyword, parent_path=None) -> list[dict]:
        """Recursively search a JSON-like object for keys that match a keyword."""

        if parent_path is None:
            parent_path = []

        results = []
        if isinstance(data, dict):
            for key, value in data.items():
                current_path = parent_path + [key]
                if key == keyword:
                    results.append(
                        {
                            "keyword": key,
                            "value": value,
                            "parents": parent_path,
                            "full_path": current_path,
                        }
                    )
                results.extend(self.get_value_by_key(value, keyword, current_path))
        elif isinstance(data, list):
            for index, item in enumerate(data):
                results.extend(self.get_value_by_key(item, keyword, parent_path + [index]))
        return results

    # ------------------------------------------------------------------
    # Internal HTTP and URL helpers
    # ------------------------------------------------------------------
    def _capabilities_url(self) -> str:
        parsed = urlparse(self.server_url)
        query = parse_qs(parsed.query)
        if query.get("REQUEST", [""])[0] == "GetCapabilities":
            return self.server_url
        params = {
            "SERVICE": "GAS",
            "VERSION": "1.0.0",
            "REQUEST": "GetCapabilities",
        }
        return f"{self.server_url.rstrip('/')}/?{urlencode(params)}"

    def _agent_entry(self, agent_id: str) -> dict:
        for entry in self.get_capabilities().get("agents", []):
            if isinstance(entry, dict) and (entry.get("agent_id") == agent_id or entry.get("name") == agent_id):
                return entry
        raise GasClientError(f"Agent '{agent_id}' is not advertised by GetCapabilities.")

    def _capability_operation_url(self, operation_id: str) -> str | None:
        for operation in self.get_capabilities().get("operations", []):
            if isinstance(operation, dict) and operation.get("operation_id") == operation_id:
                operation_url = operation.get("url") or operation.get("path")
                return str(operation_url) if operation_url else None
        return None

    def _operation_url(self, agent_id: str, operation_id: str, *, fallback_path: str) -> str:
        operation = self._operation(agent_id, operation_id)
        operation_url = self._operation_url_value(operation)
        if isinstance(operation_url, str) and operation_url:
            return self._absolute_agent_url(agent_id, operation_url.replace("{agent_id}", agent_id))
        return self._absolute_agent_url(agent_id, fallback_path)

    def _operation_map(self, agent_id: str) -> dict[str, str]:
        operations = {}
        for operation in self.get_capabilities().get("operations", []):
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operation_id") or operation.get("name")
            operation_url = self._operation_url_value(operation)
            if operation_id and operation_url:
                operations[str(operation_id)] = self._absolute_agent_url(
                    agent_id,
                    str(operation_url).replace("{agent_id}", agent_id),
                )

        for operation_id, fallback_path in {
            "execute_task": "tasks",
            "get_task_status": "tasks/<task_id>/status",
            "get_task_result": "tasks/<task_id>/result",
            "cancel_task": "tasks/<task_id>/cancel",
            "get_agent_status": "status",
        }.items():
            operations.setdefault(operation_id, self._absolute_agent_url(agent_id, fallback_path))
        return operations

    def _task_operation_url(self, agent_id: str, operation_id: str, task_id: str) -> str:
        fallback_by_operation = {
            "get_task_status": f"tasks/{task_id}/status",
            "get_task_result": f"tasks/{task_id}/result",
            "cancel_task": f"tasks/{task_id}/cancel",
        }
        operation = self._operation(agent_id, operation_id)
        operation_url = self._operation_url_value(operation)
        if isinstance(operation_url, str) and operation_url:
            operation_url = (
                operation_url
                .replace("{agent_id}", agent_id)
                .replace("<task_id>", task_id)
                .replace("{task_id}", task_id)
            )
            return self._absolute_agent_url(agent_id, operation_url)
        return self._absolute_agent_url(agent_id, fallback_by_operation[operation_id])

    def _operation(self, agent_id: str, operation_id: str) -> dict | None:
        self._agent_entry(agent_id)
        for operation in self.get_capabilities().get("operations", []):
            if isinstance(operation, dict) and (operation.get("operation_id") or operation.get("name")) == operation_id:
                return operation
        return None

    def _operation_url_value(self, operation: dict | None) -> str | None:
        if not isinstance(operation, Mapping):
            return None
        endpoint_url = operation.get("endpoint", {}).get("url") if isinstance(operation.get("endpoint"), Mapping) else None
        operation_url = operation.get("url") or operation.get("path") or endpoint_url
        return str(operation_url) if operation_url else None

    def _absolute_agent_url(self, agent_id: str, endpoint_url: str) -> str:
        endpoint_url = endpoint_url.strip()
        if endpoint_url.startswith(("http://", "https://")):
            return endpoint_url
        if endpoint_url.startswith("/agents/"):
            return self._absolute_url(endpoint_url)
        return f"{self._agent_base_url(agent_id).rstrip('/')}/{endpoint_url.lstrip('/')}"

    def _agent_base_url(self, agent_id: str) -> str:
        return self._absolute_url(f"/agents/{agent_id}")

    def _agent_display_name(self, agent_id: str) -> str:
        try:
            description = self.describe_agent(agent_id)
        except GasClientError:
            description = {}
        profile = description.get("profile") if isinstance(description.get("profile"), Mapping) else {}
        return str(profile.get("name") or description.get("name") or agent_id.replace("_", " ").title())

    def _absolute_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urljoin(f"{self._server_root().rstrip('/')}/", path_or_url.lstrip("/"))

    def _server_root(self) -> str:
        parsed = urlparse(self.server_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return self.server_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _json_or_text(self, response):
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError:
            return response.text

    def _json_or_raise(self, response) -> dict:
        self._raise_for_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GasClientError(f"Response was not valid JSON: {response.text}") from exc
        if not isinstance(payload, dict):
            raise GasClientError("Response JSON was not an object.")
        return payload

    @staticmethod
    def _stream_events(response, *, display_agent_name: str | None = None):
        for line in response.iter_lines(decode_unicode=True):
            if line:
                event = json.loads(line)
                if display_agent_name and isinstance(event, dict):
                    event.setdefault("_display_agent_name", display_agent_name)
                yield event

    def _raise_for_status(self, response) -> None:
        if response.status_code < 400:
            return
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        raise GasClientError(f"GAS request failed with HTTP {response.status_code}: {payload}")

    @staticmethod
    def _as_list(value):
        if isinstance(value, (str, Mapping)):
            return [value]
        return list(value)

    @staticmethod
    def _safe_tool_name(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)


class GasClientError(RuntimeError):
    """Raised when a GAS service request fails."""


class GasTaskTimeoutError(TimeoutError):
    """Raised when a GAS task does not finish before the timeout."""


GASClient = GasClient
