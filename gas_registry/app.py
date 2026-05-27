from __future__ import annotations

import os
import sqlite3
import secrets
import urllib.parse
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

try:
    from . import gas_registry
except ImportError:
    import gas_registry


REGISTRY_DIR = Path(__file__).resolve().parent
load_dotenv(REGISTRY_DIR / ".env")
DB_PATH = str(REGISTRY_DIR / "gas_registry.db")

API_PATH = "/api"
BOOL_FILTERS = (
    "provenance_supported",
    "reproducibility_supported",
    "validation_supported",
)
ADMIN_TOKEN_ENV = "GAS_REGISTRY_ADMIN_TOKEN"
ADMIN_TOKEN_REQUIRED_ENV = "GAS_REGISTRY_REQUIRE_ADMIN_TOKEN"

app = Flask(__name__)
app.url_map.strict_slashes = False


def _describe_url(registry_id: str) -> str:
    return f"/registry{API_PATH}/agents/{urllib.parse.quote(registry_id, safe='')}"


def _source_describe_url(describe_url: str | None, source_base_url: str | None, agent_id: str | None) -> str:
    if describe_url and "/registry/api" not in describe_url:
        return describe_url
    if not source_base_url or not agent_id:
        return ""
    params = urllib.parse.urlencode({
        "SERVICE": "GAS",
        "VERSION": "1.0.0",
        "REQUEST": "DescribeAgent",
        "agent_id": agent_id,
    })
    return source_base_url.rstrip("/") + "/?" + params


def _list_agents(db_path: str, server: str | None = None) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    gas_registry.init_db(db_path).close()
    conn = sqlite3.connect(db_path)
    try:
        sql = """
            SELECT name, agent_id, source_base_url, describe_url, description
            FROM agents
        """
        params = []
        if server:
            sql += " WHERE source_base_url = ?"
            params.append(server.rstrip("/"))
        sql += """
            ORDER BY source_base_url, agent_id
        """
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "registry_id": registry_id,
            "agent_id": agent_id or registry_id,
            "source_base_url": source_base_url,
            "description": description,
            "detailUrl": _describe_url(registry_id),
            "describeUrl": _source_describe_url(describe_url, source_base_url, agent_id or registry_id),
        }
        for registry_id, agent_id, source_base_url, describe_url, description in rows
    ]


def _list_agents_for_legacy_ui(db_path: str) -> list[dict]:
    agents = []
    for agent in _list_agents(db_path):
        item = dict(agent)
        item["name"] = agent["registry_id"]
        item["sourceBaseUrl"] = agent["source_base_url"]
        item["DescribeAgent"] = agent["detailUrl"]
        agents.append(item)
    return agents


def _load_detail(db_path: str, registry_id: str) -> dict | None:
    return gas_registry.load_agent_from_db(registry_id, db_path=db_path)


def _delete_agents(db_path: str, registry_ids: list[str]) -> dict:
    return gas_registry.delete_agents_from_db(registry_ids, db_path=db_path)


def _list_servers(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    gas_registry.init_db(db_path).close()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT source_base_url, COUNT(*) AS agent_count, MAX(fetched_at) AS last_fetched_at
            FROM agents
            WHERE source_base_url IS NOT NULL AND source_base_url != ''
            GROUP BY source_base_url
            ORDER BY source_base_url
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "source_base_url": source_base_url,
            "agent_count": agent_count,
            "last_fetched_at": last_fetched_at,
        }
        for source_base_url, agent_count, last_fetched_at in rows
    ]


def _success(**payload):
    return jsonify({"status": "success", **payload})


def _error(message: str, status_code: int):
    return jsonify(status="error", error=message), status_code


def _legacy_write_response(response):
    """Return the legacy UI write-response shape while sharing POST logic."""

    status_code = 200
    flask_response = response
    if isinstance(response, tuple):
        flask_response = response[0]
        status_code = response[1]

    body = flask_response.get_json(silent=True) or {}
    if body.get("status") == "success":
        body = {key: value for key, value in body.items() if key != "status"}
        return jsonify(ok=True, **body), status_code

    message = body.get("error") or "Request failed"
    details = body.get("details")
    payload = {"ok": False, "error": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status_code


def _configured_admin_token() -> str:
    return (os.environ.get(ADMIN_TOKEN_ENV) or "").strip()


def _admin_token_required() -> bool:
    value = (os.environ.get(ADMIN_TOKEN_REQUIRED_ENV) or "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _request_admin_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (request.headers.get("X-Registry-Admin-Token") or "").strip()


def _require_admin_token():
    expected = _configured_admin_token()
    if not expected:
        if _admin_token_required():
            return _error(
                "Registry admin token is required by configuration, but GAS_REGISTRY_ADMIN_TOKEN is not set.",
                500,
            )
        return None
    supplied = _request_admin_token()
    if supplied and secrets.compare_digest(supplied, expected):
        return None
    if supplied:
        return _error("Invalid registry admin token.", 401)
    return _error("Registry admin token is required for this operation.", 401)


@app.route("/")
@app.route("/registry")
@app.route("/registry/")
def gas_registry_index():
    return send_from_directory(REGISTRY_DIR, "index.html")


@app.route(API_PATH)
@app.route(f"{API_PATH}/")
@app.route(f"/registry{API_PATH}")
@app.route(f"/registry{API_PATH}/")
def registry_api_root():
    return _success(
        name="GAS Registry API",
        version="1.0.0",
        endpoints={
            "agents": f"/registry{API_PATH}/agents",
            "agent_detail": f"/registry{API_PATH}/agents/{{registry_id}}",
            "delete_agent": f"/registry{API_PATH}/agents/{{registry_id}}",
            "delete_agents": f"/registry{API_PATH}/agents/delete",
            "agent_search": f"/registry{API_PATH}/agents/search",
            "servers": f"/registry{API_PATH}/servers",
            "remote_agents": f"/registry{API_PATH}/remote-agents",
            "register_server": f"/registry{API_PATH}/servers",
            "register_selected_agents": f"/registry{API_PATH}/servers/selected-agents",
        },
    )


@app.route(f"{API_PATH}/agents")
@app.route(f"{API_PATH}/agents/")
@app.route(f"/registry{API_PATH}/agents")
@app.route(f"/registry{API_PATH}/agents/")
def list_registered_agents():
    agents = _list_agents(DB_PATH, server=(request.args.get("server") or "").strip() or None)
    return _success(count=len(agents), agents=agents)


@app.route(f"{API_PATH}/agents/delete", methods=["POST"])
@app.route(f"{API_PATH}/agents/delete/", methods=["POST"])
@app.route(f"/registry{API_PATH}/agents/delete", methods=["POST"])
@app.route(f"/registry{API_PATH}/agents/delete/", methods=["POST"])
def delete_registered_agents():
    auth_error = _require_admin_token()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    registry_ids = data.get("registry_ids") or data.get("names") or []
    if isinstance(registry_ids, str):
        registry_ids = [name.strip() for name in registry_ids.split(",") if name.strip()]
    if not registry_ids:
        return _error("Missing 'registry_ids'.", 400)

    result = _delete_agents(DB_PATH, registry_ids)
    return _success(**result, count=len(result["deleted"]))


@app.route(f"{API_PATH}/agents/<path:registry_id>")
@app.route(f"{API_PATH}/agents/<path:registry_id>/")
@app.route(f"/registry{API_PATH}/agents/<path:registry_id>")
@app.route(f"/registry{API_PATH}/agents/<path:registry_id>/")
def get_registered_agent(registry_id: str):
    registry_id = registry_id.rstrip("/")
    detail = _load_detail(DB_PATH, registry_id)
    if detail is None:
        return _error("Agent not found", 404)
    return jsonify(detail)


@app.route(f"{API_PATH}/agents/<path:registry_id>", methods=["DELETE"])
@app.route(f"{API_PATH}/agents/<path:registry_id>/", methods=["DELETE"])
@app.route(f"/registry{API_PATH}/agents/<path:registry_id>", methods=["DELETE"])
@app.route(f"/registry{API_PATH}/agents/<path:registry_id>/", methods=["DELETE"])
def delete_registered_agent(registry_id: str):
    auth_error = _require_admin_token()
    if auth_error:
        return auth_error

    registry_id = registry_id.rstrip("/")
    if not registry_id:
        return _error("Missing 'registry_id'.", 400)
    result = _delete_agents(DB_PATH, [registry_id])
    if not result["deleted"]:
        return _error("Agent not found", 404)
    return _success(**result, count=len(result["deleted"]))


@app.route("/api/gas/delete-selected", methods=["POST"])
@app.route("/api/gas/delete-selected/", methods=["POST"])
@app.route("/registry/api/gas/delete-selected", methods=["POST"])
@app.route("/registry/api/gas/delete-selected/", methods=["POST"])
def delete_registered_agents_legacy():
    return _legacy_write_response(delete_registered_agents())


@app.route("/api/gas")
@app.route("/api/gas/")
@app.route("/registry/api/gas")
@app.route("/registry/api/gas/")
def gas_registry_legacy_kvp_api():
    service = (request.args.get("SERVICE") or request.args.get("service") or "").upper()
    req = (request.args.get("REQUEST") or request.args.get("request") or "").lower()
    registry_id = (
        request.args.get("registry_id")
        or request.args.get("agent_id")
        or request.args.get("name")
        or request.args.get("agent")
    )

    if service and service != "GAS":
        return jsonify(ok=False, error="Invalid SERVICE"), 400

    if req == "getcapabilities":
        return jsonify(
            service="GAS",
            version="1.0.0",
            request="GetCapabilities",
            agents=_list_agents_for_legacy_ui(DB_PATH),
        )

    if req == "describeagent":
        if not registry_id:
            return jsonify(ok=False, error="Missing registry_id"), 400
        detail = _load_detail(DB_PATH, registry_id)
        if detail is None:
            return jsonify(ok=False, error="Agent not found"), 404
        return jsonify(detail)

    return jsonify(ok=False, error="Invalid REQUEST"), 400


@app.route(f"{API_PATH}/servers", methods=["GET"])
@app.route(f"{API_PATH}/servers/", methods=["GET"])
@app.route(f"/registry{API_PATH}/servers", methods=["GET"])
@app.route(f"/registry{API_PATH}/servers/", methods=["GET"])
def list_registered_servers():
    servers = _list_servers(DB_PATH)
    return _success(count=len(servers), servers=servers)


@app.route(f"{API_PATH}/servers", methods=["POST"])
@app.route(f"{API_PATH}/servers/", methods=["POST"])
@app.route(f"/registry{API_PATH}/servers", methods=["POST"])
@app.route(f"/registry{API_PATH}/servers/", methods=["POST"])
def gas_register():
    """Register every agent from a remote GAS GetCapabilities URL."""

    auth_error = _require_admin_token()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.args.get("url") or "").strip()

    if not url:
        return _error("Missing 'url'.", 400)

    try:
        names = gas_registry.register_server(url, DB_PATH)
    except Exception as exc:
        return _error(f"Registration failed: {exc}", 502)

    return _success(registered=names, count=len(names))


@app.route(f"{API_PATH}/agents/search")
@app.route(f"{API_PATH}/agents/search/")
@app.route(f"/registry{API_PATH}/agents/search")
@app.route(f"/registry{API_PATH}/agents/search/")
@app.route("/api/gas/search")
@app.route("/api/gas/search/")
@app.route("/registry/api/gas/search")
@app.route("/registry/api/gas/search/")
def gas_search():
    """SQL-backed search over gas_registry.db."""

    if not os.path.exists(DB_PATH):
        if request.path.endswith("/api/gas/search"):
            return jsonify(ok=False, error=f"Database not found: {DB_PATH}"), 503
        return _error(f"Database not found: {DB_PATH}", 503)
    gas_registry.init_db(DB_PATH).close()

    q = (request.args.get("q") or "").strip()
    field = (request.args.get("field") or "").strip().lower()

    field_map = {
        "name": ["name", "agent_id"],
        "keywords": ["keywords"],
        "skills": ["skill_names", "skill_descriptions"],
    }
    where = []
    params = []

    if q:
        like = f"%{q}%"
        if field in field_map:
            columns = field_map[field]
            where.append("(" + " OR ".join(f"a.{column} LIKE ?" for column in columns) + ")")
            params.extend([like] * len(columns))
        else:
            where.append("a.agent_info LIKE ?")
            params.append(like)

    for name in BOOL_FILTERS:
        value = request.args.get(name)
        if value not in (None, ""):
            where.append(f"a.{name} = ?")
            params.append(value)

    sql = "SELECT a.name, a.agent_id, a.source_base_url, a.describe_url, a.description FROM agents a"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY a.name"

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    legacy_ui_response = request.path.endswith("/api/gas/search")
    agents = []
    for registry_id, agent_id, source_base_url, describe_url, description in rows:
        item = {
            "registry_id": registry_id,
            "agent_id": agent_id or registry_id,
            "source_base_url": source_base_url,
            "description": description,
            "detailUrl": _describe_url(registry_id),
            "describeUrl": _source_describe_url(describe_url, source_base_url, agent_id or registry_id),
        }
        if legacy_ui_response:
            item["name"] = registry_id
            item["sourceBaseUrl"] = source_base_url
        agents.append(item)

    response_payload = {
        "query": q,
        "count": len(rows),
        "agents": agents,
    }
    if legacy_ui_response:
        return jsonify(ok=True, **response_payload)
    return _success(**response_payload)


@app.route("/api/gas/register", methods=["POST"])
@app.route("/api/gas/register/", methods=["POST"])
@app.route("/registry/api/gas/register", methods=["POST"])
@app.route("/registry/api/gas/register/", methods=["POST"])
def gas_register_legacy():
    return _legacy_write_response(gas_register())


@app.route(f"{API_PATH}/remote-agents")
@app.route(f"{API_PATH}/remote-agents/")
@app.route(f"/registry{API_PATH}/remote-agents")
@app.route(f"/registry{API_PATH}/remote-agents/")
@app.route("/api/gas/list-remote", methods=["GET", "POST"])
@app.route("/api/gas/list-remote/", methods=["GET", "POST"])
@app.route("/registry/api/gas/list-remote", methods=["GET", "POST"])
@app.route("/registry/api/gas/list-remote/", methods=["GET", "POST"])
def gas_list_remote():
    """List all agents advertised by a remote GAS server."""

    legacy_ui_response = "/api/gas/list-remote" in request.path
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.args.get("url") or "").strip()
    if not url:
        if legacy_ui_response:
            return jsonify(ok=False, error="Missing 'url'."), 400
        return _error("Missing 'url'.", 400)

    base_url = gas_registry._base_url_from_capabilities_url(url)
    try:
        agents = gas_registry.get_capabilities(base_url=base_url)
    except Exception as exc:
        if legacy_ui_response:
            return jsonify(ok=False, error=f"List failed: {exc}"), 502
        return _error(f"List failed: {exc}", 502)

    enriched = []
    for agent in agents:
        name = agent.get("name")
        item = {
            "name": name,
            "describeUrl": agent.get("describeUrl"),
            "sourceBaseUrl": base_url,
            "displayName": "",
            "description": "",
            "version": "",
        }
        try:
            detail = gas_registry.describe_agent(name, base_url=base_url)
            profile = (detail or {}).get("profile") or {}
            item["displayName"] = profile.get("name") or ""
            item["description"] = profile.get("description") or ""
            item["version"] = profile.get("version") or ""
        except Exception:
            pass
        enriched.append(item)
    if legacy_ui_response:
        return jsonify(ok=True, agents=enriched)
    return _success(count=len(enriched), agents=enriched)


@app.route("/api/gas/register-selected", methods=["POST"])
@app.route("/api/gas/register-selected/", methods=["POST"])
@app.route("/registry/api/gas/register-selected", methods=["POST"])
@app.route("/registry/api/gas/register-selected/", methods=["POST"])
def gas_register_selected_legacy():
    return _legacy_write_response(gas_register_selected())


@app.route(f"{API_PATH}/servers/selected-agents", methods=["POST"])
@app.route(f"{API_PATH}/servers/selected-agents/", methods=["POST"])
@app.route(f"/registry{API_PATH}/servers/selected-agents", methods=["POST"])
@app.route(f"/registry{API_PATH}/servers/selected-agents/", methods=["POST"])
def gas_register_selected():
    """Register a selected subset of a remote server's agents."""

    auth_error = _require_admin_token()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.args.get("url") or "").strip()
    names = data.get("names") or request.args.getlist("name") or request.args.getlist("names")
    if len(names) == 1 and "," in names[0]:
        names = [name.strip() for name in names[0].split(",") if name.strip()]
    if not url:
        return _error("Missing 'url'.", 400)
    if not names:
        return _error("Missing 'name'.", 400)

    base_url = gas_registry._base_url_from_capabilities_url(url)
    registered = []
    errors = []
    remote_agents = {}
    try:
        remote_agents = {
            agent.get("name"): agent.get("describeUrl")
            for agent in gas_registry.get_capabilities(base_url=base_url)
        }
    except Exception:
        remote_agents = {}
    for name in names:
        try:
            gas_registry.save_agent_to_db(
                name,
                db_path=DB_PATH,
                describe_url=remote_agents.get(name),
                base_url=base_url,
            )
            registered.append(name)
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})

    if errors and not registered:
        return jsonify(status="error", error="Registration failed", details=errors), 502
    return _success(registered=registered, count=len(registered), errors=errors)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=4043, debug=False)
