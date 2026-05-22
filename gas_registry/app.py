from __future__ import annotations

import os
import sqlite3
import urllib.parse
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import gas_registry


REGISTRY_DIR = Path(__file__).resolve().parent
DB_PATH = str(REGISTRY_DIR / "gas_registry.db")

API_PATH = "/registry/api/gas"
BOOL_FILTERS = (
    "provenance_supported",
    "reproducibility_supported",
    "validation_supported",
)

app = Flask(__name__)


def _describe_url(registry_id: str) -> str:
    params = urllib.parse.urlencode({
        "SERVICE": "GAS",
        "VERSION": "1.0.0",
        "REQUEST": "DescribeAgent",
        "registry_id": registry_id,
    })
    return f"{API_PATH}?{params}"


def _source_describe_url(describe_url: str | None, source_base_url: str | None, agent_id: str | None) -> str:
    if describe_url and "/registry/api/gas" not in describe_url:
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


def _list_agents(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    gas_registry.init_db(db_path).close()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT name, agent_id, source_base_url, describe_url, description
            FROM agents
            ORDER BY source_base_url, agent_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "registry_id": registry_id,
            "agent_id": agent_id or registry_id,
            "name": registry_id,
            "source_base_url": source_base_url,
            "sourceBaseUrl": source_base_url,
            "description": description,
            "detailUrl": _describe_url(registry_id),
            "DescribeAgent": _describe_url(registry_id),
            "describeUrl": _source_describe_url(describe_url, source_base_url, agent_id or registry_id),
        }
        for registry_id, agent_id, source_base_url, describe_url, description in rows
    ]


def _load_detail(db_path: str, registry_id: str) -> dict | None:
    return gas_registry.load_agent_from_db(registry_id, db_path=db_path)


@app.route("/registry")
def gas_registry_index():
    return send_from_directory(REGISTRY_DIR, "index.html")


@app.route(API_PATH)
def gas_registry_api():
    service = (request.args.get("SERVICE", "") or "").upper()
    req = (request.args.get("REQUEST", "") or "").lower()
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
            agents=_list_agents(DB_PATH),
        )

    if req == "describeagent":
        if not registry_id:
            return jsonify(ok=False, error="Missing registry_id"), 400

        detail = _load_detail(DB_PATH, registry_id)
        if detail is None:
            return jsonify(ok=False, error="Agent not found"), 404
        return jsonify(detail)

    return jsonify(ok=False, error="Invalid REQUEST"), 400


@app.route(f"{API_PATH}/register", methods=["POST"])
def gas_register():
    """Register every agent from a remote GAS GetCapabilities URL."""

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.args.get("url") or "").strip()

    if not url:
        return jsonify(ok=False, error="Missing 'url'."), 400

    try:
        names = gas_registry.register_server(url, DB_PATH)
    except Exception as exc:
        return jsonify(ok=False, error=f"Registration failed: {exc}"), 502

    return jsonify(ok=True, registered=names, count=len(names))


@app.route(f"{API_PATH}/search")
def gas_search():
    """SQL-backed search over gas_registry.db."""

    if not os.path.exists(DB_PATH):
        return jsonify(ok=False, error=f"Database not found: {DB_PATH}"), 503
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

    return jsonify(
        ok=True,
        query=q,
        count=len(rows),
        agents=[
            {
                "name": registry_id,
                "registry_id": registry_id,
                "agent_id": agent_id or registry_id,
                "source_base_url": source_base_url,
                "sourceBaseUrl": source_base_url,
                "description": description,
                "detailUrl": _describe_url(registry_id),
                "describeUrl": _source_describe_url(describe_url, source_base_url, agent_id or registry_id),
            }
            for registry_id, agent_id, source_base_url, describe_url, description in rows
        ],
    )


@app.route(f"{API_PATH}/list-remote", methods=["GET", "POST"])
def gas_list_remote():
    """List all agents advertised by a remote GAS server."""

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or request.args.get("url") or "").strip()
    if not url:
        return jsonify(ok=False, error="Missing 'url'."), 400

    base_url = gas_registry._base_url_from_capabilities_url(url)
    try:
        agents = gas_registry.get_capabilities(base_url=base_url)
    except Exception as exc:
        return jsonify(ok=False, error=f"List failed: {exc}"), 502

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
    return jsonify(ok=True, agents=enriched)


@app.route(f"{API_PATH}/register-selected", methods=["POST"])
def gas_register_selected():
    """Register a selected subset of a remote server's agents."""

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    names = data.get("names") or []
    if not url:
        return jsonify(ok=False, error="Missing 'url'."), 400
    if not isinstance(names, list) or not names:
        return jsonify(ok=False, error="Missing 'names'."), 400

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
        return jsonify(ok=False, error="Registration failed", details=errors), 502
    return jsonify(ok=True, registered=registered, count=len(registered), errors=errors)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=4043, debug=False)
