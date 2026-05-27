import json
from pathlib import Path

from gas_server.agents import usgs_earthquake_agent as earthquake_module
from gas_server.agents.usgs_earthquake_agent import USGSEarthquakeAgent
from gas_server.core.service_core import _build_task_payload
from gas_server.services.usgs_earthquake_agent_service import get_service_app


def sample_usgs_payload():
    return {
        "type": "FeatureCollection",
        "metadata": {"count": 2},
        "features": [
            {
                "type": "Feature",
                "id": "us-test-1",
                "properties": {
                    "mag": 5.2,
                    "place": "10 km S of Testville, California",
                    "time": 1760000000000,
                    "updated": 1760000300000,
                    "tz": None,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us-test-1",
                    "detail": "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us-test-1&format=geojson",
                    "felt": 12,
                    "sig": 420,
                    "alert": "green",
                    "tsunami": 0,
                    "type": "earthquake",
                    "status": "reviewed",
                    "net": "us",
                    "code": "test-1",
                },
                "geometry": {"type": "Point", "coordinates": [-118.2, 34.1, 12.0]},
            },
            {
                "type": "Feature",
                "id": "us-test-2",
                "properties": {
                    "mag": 4.1,
                    "place": "5 km N of Example City, California",
                    "time": 1760003600000,
                    "updated": 1760003900000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us-test-2",
                    "detail": "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us-test-2&format=geojson",
                    "felt": 2,
                    "sig": 270,
                    "alert": None,
                    "tsunami": 0,
                    "type": "earthquake",
                    "status": "reviewed",
                    "net": "us",
                    "code": "test-2",
                },
                "geometry": {"type": "Point", "coordinates": [-118.4, 34.3, 8.0]},
            },
        ],
    }


def test_usgs_earthquake_agent_retrieves_exports_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(earthquake_module, "DATA_DIR", tmp_path / "Data")
    agent = USGSEarthquakeAgent(api_key=None)
    monkeypatch.setattr(agent, "_request_json", lambda url, params=None: sample_usgs_payload())

    result = agent.run(
        "Find M4+ earthquakes in California this week and create a depth-colored map, buffers, grid summary, and report."
    )

    outputs = result["outputs"]
    artifacts = outputs["dataset_paths"]
    suffixes = {Path(path).suffix.lower() for path in artifacts}

    assert outputs["earthquake_summary"]["event_count"] == 2
    assert outputs["earthquake_summary"]["magnitude"]["m5_plus"] == 1
    assert outputs["alert_summary"] is None
    assert outputs["query"]["parameters"]["minmagnitude"] == 4.0
    assert outputs["query"]["parameters"]["minlatitude"] == 32.4
    assert ".geojson" in suffixes
    assert ".csv" in suffixes
    assert ".png" in suffixes
    assert ".md" in suffixes
    assert ".html" in suffixes
    assert any("buffers" in Path(path).stem for path in artifacts)
    assert any("grid" in Path(path).stem for path in artifacts)


def test_usgs_earthquake_agent_feed_alert_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(earthquake_module, "DATA_DIR", tmp_path / "Data")
    agent = USGSEarthquakeAgent(api_key=None)
    calls = []

    def fake_request(url, params=None):
        calls.append((url, params))
        return sample_usgs_payload()

    monkeypatch.setattr(agent, "_request_json", fake_request)

    result = agent.run("Run a latest significant earthquake check and produce an alert-ready summary.")

    assert calls[0][0].endswith("/significant_day.geojson")
    assert result["outputs"]["alert_summary"]
    assert "earthquake event" in result["outputs"]["alert_summary"]


def test_usgs_earthquake_agent_simple_export_has_no_duplicate_table_or_report(tmp_path, monkeypatch):
    monkeypatch.setattr(earthquake_module, "DATA_DIR", tmp_path / "Data")
    agent = USGSEarthquakeAgent(api_key=None)
    monkeypatch.setattr(agent, "_request_json", lambda url, params=None: sample_usgs_payload())

    raw_result = agent.run_service(
        "Retrieve M2.5+ earthquakes in California during the past 30 days and export the event dataset as CSV.",
        parameters={"output_format": "csv"},
    )
    payload = _build_task_payload(
        task_id="earthquake-export-task",
        agent_id="usgs_earthquake_agent",
        agent_name="USGS Earthquake Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Retrieve M2.5+ earthquakes in California during the past 30 days and export the event dataset as CSV.",
        requested_skill=None,
        result=raw_result,
        error_message=None,
        agent_id_for_artifacts="usgs_earthquake_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    artifact_formats = [artifact.get("format") for artifact in payload["outputs"]["artifacts"]]
    assert artifact_formats == ["csv"]


def test_usgs_earthquake_agent_standard_response_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(earthquake_module, "DATA_DIR", tmp_path / "Data")
    agent = USGSEarthquakeAgent(api_key=None)
    monkeypatch.setattr(agent, "_request_json", lambda url, params=None: sample_usgs_payload())

    raw_result = agent.run("Find M5+ earthquakes near Los Angeles today and make a report.")
    payload = _build_task_payload(
        task_id="earthquake-task",
        agent_id="usgs_earthquake_agent",
        agent_name="USGS Earthquake Agent",
        agent_version="1.0.0",
        state="TASK_STATE_COMPLETED",
        query="Find M5+ earthquakes near Los Angeles today and make a report.",
        requested_skill=None,
        result=raw_result,
        error_message=None,
        agent_id_for_artifacts="usgs_earthquake_agent",
        output_delivery="url",
        public_base_url="http://testserver",
    )

    assert payload["agent"]["id"] == "usgs_earthquake_agent"
    assert payload["outputs"]["artifacts"]
    artifact_formats = {artifact.get("format") for artifact in payload["outputs"]["artifacts"]}
    assert {"geojson", "csv", "png", "html", "md"} <= artifact_formats
    assert payload["reproducibility"]["output_artifacts"]
    assert all("data" not in artifact for artifact in payload["reproducibility"]["output_artifacts"])


def test_usgs_earthquake_agent_service_describe_agent():
    app = get_service_app()
    response = app.test_client().get("/?SERVICE=GAS&VERSION=1.0.0&REQUEST=DescribeAgent")
    payload = json.loads(response.data)

    assert response.status_code == 200
    assert payload["profile"]["agent_id"] == "usgs_earthquake_agent"
    assert payload["execute_task"]["credentials"]["required"] is False
