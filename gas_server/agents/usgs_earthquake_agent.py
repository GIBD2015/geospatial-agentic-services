from __future__ import annotations

import html
import json
import math
import platform
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests
from shapely.geometry import Point, box, mapping

from gas_server.core.config import DATA_DIR, ensure_runtime_dirs
from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent, ProgressCallback
from gas_server.core.llm_client import build_llm_client, format_service_name


ensure_runtime_dirs()


USGS_CATALOG_ENDPOINT = "https://earthquake.usgs.gov/fdsnws/event/1/query"
USGS_FEED_BASE = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"
EARTH_RADIUS_KM = 6371.0088


REGION_BBOXES = {
    "alaska": (-179.9, 51.0, -129.0, 72.0),
    "california": (-124.6, 32.4, -114.1, 42.1),
    "hawaii": (-161.0, 18.5, -154.5, 22.5),
    "japan": (122.0, 24.0, 154.0, 46.5),
    "puerto rico": (-68.2, 17.7, -65.0, 18.7),
    "southern california": (-121.0, 32.4, -114.1, 36.8),
    "pacific northwest": (-125.5, 41.5, -116.0, 49.2),
    "conterminous us": (-125.0, 24.0, -66.5, 49.5),
    "united states": (-179.9, 18.0, -65.0, 72.0),
}

KNOWN_LOCATIONS = {
    "los angeles": (34.0522, -118.2437),
    "san francisco": (37.7749, -122.4194),
    "seattle": (47.6062, -122.3321),
    "anchorage": (61.2181, -149.9003),
    "tokyo": (35.6762, 139.6503),
    "honolulu": (21.3069, -157.8583),
    "mexico city": (19.4326, -99.1332),
}


@dataclass
class EarthquakePlan:
    action: str
    query_mode: str
    output_format: str
    include_map: bool
    map_style: str
    include_animation: bool
    include_grid_summary: bool
    include_buffers: bool
    include_html_report: bool
    include_markdown_report: bool
    include_alert_summary: bool
    include_clusters: bool
    buffer_km: float
    grid_degrees: float
    parameters: dict[str, Any]
    source: str
    notes: list[str]


class USGSEarthquakeAgent(GeoAgent):
    agent_id = "usgs_earthquake_agent"
    agent_name = "USGS Earthquake Agent"
    agent_version = "1.0.0"
    agent_description = (
        "Retrieves, maps, summarizes, reports, and monitors earthquake activity "
        "from the USGS Earthquake Catalog and real-time feeds."
    )
    requires_input_datasets = False
    requires_model_credentials = False

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key,
            model=model or "gpt-5.2",
            output_dir=DATA_DIR / self.agent_id,
        )
        self.service_name = format_service_name(self.agent_name)
        self.client = build_llm_client(service_name=self.service_name, openai_api_key=self.api_key)
        self.session = requests.Session()
        self.tool_trace: list[dict[str, Any]] = []

    def _output_path(self, query: str, extension: str, fallback: str) -> str:
        directory = self.ensure_directory(self.output_dir)
        filename = build_output_filename(query, extension=extension, fallback=fallback, max_words=4)
        return str(directory / f"{fallback}_{filename}")

    def _record_tool(self, name: str, **details: Any) -> None:
        self.increment_tool_calls()
        self.tool_trace.append({"tool": name, **details})

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=120)
        response.raise_for_status()
        return response.json()

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", text or "", flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _llm_plan(self, query: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            return {}
        prompt = (
            "Extract a conservative JSON plan for a USGS earthquake task. "
            "Use only these keys when relevant: action, query_mode, output_format, "
            "include_map, map_style, include_animation, include_grid_summary, "
            "include_buffers, include_html_report, include_markdown_report, "
            "include_alert_summary, include_clusters, buffer_km, grid_degrees, "
            "parameters. Parameters may include starttime, endtime, minmagnitude, "
            "maxmagnitude, mindepth, maxdepth, minlatitude, maxlatitude, "
            "minlongitude, maxlongitude, latitude, longitude, maxradiuskm, "
            "alertlevel, eventtype, feed_period, feed_magnitude. Return JSON only.\n\n"
            f"User request: {query}\nExisting parameters: {json.dumps(parameters, default=str)}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You plan earthquake data workflows. Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            self.increment_llm_calls()
            content = response.choices[0].message.content
            return self._extract_json_object(content)
        except Exception as exc:
            return {"notes": [f"LLM planning was unavailable, so deterministic parsing was used: {exc}"]}

    def _parse_relative_time(self, query_lower: str) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        days = 30
        if "past hour" in query_lower or "last hour" in query_lower:
            days = 1
            start = now - timedelta(hours=1)
            return start.date().isoformat(), now.date().isoformat()
        if "today" in query_lower or "past day" in query_lower or "last day" in query_lower:
            days = 1
        elif "past week" in query_lower or "last week" in query_lower or "7 day" in query_lower:
            days = 7
        else:
            match = re.search(r"(?:past|last)\s+(\d+)\s+day", query_lower)
            if match:
                days = max(1, min(int(match.group(1)), 3650))
            match = re.search(r"(?:past|last)\s+(\d+)\s+month", query_lower)
            if match:
                days = max(1, min(int(match.group(1)) * 30, 3650))
        return (now - timedelta(days=days)).date().isoformat(), now.date().isoformat()

    def _parse_explicit_parameters(self, query: str) -> dict[str, Any]:
        lower = query.lower()
        params: dict[str, Any] = {}
        start, end = self._parse_relative_time(lower)
        params["starttime"] = start
        params["endtime"] = end

        mag_match = re.search(r"\bM\s*([0-9]+(?:\.[0-9]+)?)\s*\+|\b([0-9]+(?:\.[0-9]+)?)\s*\+\s*(?:magnitude|mag|earthquake)", query, re.I)
        if mag_match:
            params["minmagnitude"] = float(mag_match.group(1) or mag_match.group(2))
        elif "significant" in lower:
            params["minmagnitude"] = 4.5
        elif "large" in lower:
            params["minmagnitude"] = 5.0

        depth_match = re.search(r"(?:shallower than|depth less than|max depth|maxdepth)\s*([0-9]+(?:\.[0-9]+)?)", lower)
        if depth_match:
            params["maxdepth"] = float(depth_match.group(1))
        elif "shallow" in lower:
            params["maxdepth"] = 70.0

        bbox_match = re.search(
            r"bbox\s*[:=]?\s*\[?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
            query,
            re.I,
        )
        if bbox_match:
            minlon, minlat, maxlon, maxlat = [float(value) for value in bbox_match.groups()]
            params.update(
                {
                    "minlongitude": minlon,
                    "minlatitude": minlat,
                    "maxlongitude": maxlon,
                    "maxlatitude": maxlat,
                }
            )

        for region, bbox_values in REGION_BBOXES.items():
            if region in lower:
                minlon, minlat, maxlon, maxlat = bbox_values
                params.update(
                    {
                        "minlongitude": minlon,
                        "minlatitude": minlat,
                        "maxlongitude": maxlon,
                        "maxlatitude": maxlat,
                    }
                )
                params["region_name"] = region
                break

        radius_match = re.search(r"within\s+([0-9]+(?:\.[0-9]+)?)\s*km\s+of\s+([a-zA-Z .,-]+)", query, re.I)
        if radius_match:
            radius = float(radius_match.group(1))
            place = radius_match.group(2).strip(" .,").lower()
            for name, (lat, lon) in KNOWN_LOCATIONS.items():
                if name in place:
                    params.update({"latitude": lat, "longitude": lon, "maxradiuskm": radius, "region_name": name})
                    break

        explicit_dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", query)
        if explicit_dates:
            params["starttime"] = explicit_dates[0]
            params["endtime"] = explicit_dates[1] if len(explicit_dates) > 1 else params.get("endtime")

        return params

    def _feed_from_query(self, query_lower: str, parameters: dict[str, Any]) -> tuple[str, str] | None:
        feed_period = str(parameters.get("feed_period") or "").lower()
        feed_mag = str(parameters.get("feed_magnitude") or "").lower()
        if feed_period and feed_mag:
            return feed_mag, feed_period
        if "feed" not in query_lower and "latest" not in query_lower and "alert" not in query_lower:
            return None
        period = "day"
        if "past hour" in query_lower or "last hour" in query_lower:
            period = "hour"
        elif "past week" in query_lower or "7 day" in query_lower:
            period = "week"
        elif "past month" in query_lower or "30 day" in query_lower:
            period = "month"
        magnitude = "significant" if "significant" in query_lower else "all"
        for token in ("4.5", "2.5", "1.0"):
            if token in query_lower:
                magnitude = token
                break
        return magnitude, period

    def _build_plan(self, query: str) -> EarthquakePlan:
        query_lower = query.lower()
        request_params = dict(self.request_parameters or {})
        deterministic_params = self._parse_explicit_parameters(query)
        llm_plan = self._llm_plan(query, request_params)
        llm_params = llm_plan.get("parameters") if isinstance(llm_plan.get("parameters"), dict) else {}

        params = {}
        params.update(deterministic_params)
        params.update({key: value for key, value in request_params.items() if value not in (None, "")})
        params.update({key: value for key, value in llm_params.items() if value not in (None, "")})

        output_format = str(params.pop("output_format", llm_plan.get("output_format", "geojson")) or "geojson").lower()
        if output_format not in {"geojson", "gpkg", "csv", "parquet"}:
            output_format = "geojson"

        explicit_map = any(token in query_lower for token in ("map", "epicenter", "depth-colored", "depth colored"))
        explicit_animation = any(token in query_lower for token in ("animation", "animated", "time-animation", "time animation"))
        explicit_grid = any(token in query_lower for token in ("grid", "regional summary", "summary layer"))
        explicit_buffers = any(token in query_lower for token in ("buffer", "impact screening", "impact area"))
        explicit_alert = any(token in query_lower for token in ("alert", "threshold", "latest significant", "digest", "brief"))
        explicit_clusters = any(token in query_lower for token in ("cluster", "swarm", "aftershock"))
        explicit_report = any(token in query_lower for token in ("html", "report", "brief", "digest", "markdown"))
        explicit_table = any(token in query_lower for token in ("table", "top events", "event list"))

        include_map = bool(explicit_map or request_params.get("include_map"))
        map_style = str(llm_plan.get("map_style") or ("depth" if "depth" in query_lower else "magnitude")).lower()
        include_animation = bool(explicit_animation or request_params.get("include_animation"))
        include_grid_summary = bool(explicit_grid or request_params.get("include_grid_summary"))
        include_buffers = bool(explicit_buffers or request_params.get("include_buffers"))
        include_alert = bool(explicit_alert or request_params.get("include_alert_summary"))
        include_clusters = bool(explicit_clusters or request_params.get("include_clusters"))
        include_html = bool(explicit_report or request_params.get("include_html_report"))
        include_markdown = bool(explicit_report or explicit_alert or request_params.get("include_markdown_report"))
        include_table = bool(explicit_table or include_html or include_alert or include_grid_summary or include_clusters or request_params.get("include_event_table"))

        query_mode = str(llm_plan.get("query_mode") or "catalog").lower()
        feed = self._feed_from_query(query_lower, params)
        if feed:
            query_mode = "feed"
            params["feed_magnitude"], params["feed_period"] = feed

        notes = []
        if isinstance(llm_plan.get("notes"), list):
            notes.extend(str(note) for note in llm_plan["notes"])

        return EarthquakePlan(
            action=str(llm_plan.get("action") or "earthquake_activity"),
            query_mode=query_mode,
            output_format=output_format,
            include_map=include_map,
            map_style=map_style,
            include_animation=include_animation,
            include_grid_summary=include_grid_summary,
            include_buffers=include_buffers,
            include_html_report=include_html,
            include_markdown_report=include_markdown,
            include_alert_summary=include_alert,
            include_clusters=include_clusters,
            buffer_km=float(llm_plan.get("buffer_km") or params.pop("buffer_km", 50)),
            grid_degrees=float(llm_plan.get("grid_degrees") or params.pop("grid_degrees", 1.0)),
            parameters={**params, "_include_event_table": include_table},
            source="llm_assisted" if llm_plan and self.llm_calls else "deterministic",
            notes=notes,
        )

    def query_usgs_catalog(self, params: dict[str, Any]) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
        allowed = {
            "starttime",
            "endtime",
            "minmagnitude",
            "maxmagnitude",
            "mindepth",
            "maxdepth",
            "minlatitude",
            "maxlatitude",
            "minlongitude",
            "maxlongitude",
            "latitude",
            "longitude",
            "maxradiuskm",
            "alertlevel",
            "eventtype",
            "orderby",
            "limit",
        }
        query_params = {key: value for key, value in params.items() if key in allowed and value not in (None, "")}
        query_params.setdefault("format", "geojson")
        query_params.setdefault("orderby", "time")
        payload = self._request_json(USGS_CATALOG_ENDPOINT, query_params)
        self._record_tool("query_usgs_catalog", endpoint=USGS_CATALOG_ENDPOINT, parameters=query_params)
        return self._feature_collection_to_gdf(payload), {"endpoint": USGS_CATALOG_ENDPOINT, "parameters": query_params}

    def query_usgs_feed(self, magnitude: str, period: str) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
        safe_magnitude = magnitude if magnitude in {"significant", "all", "4.5", "2.5", "1.0"} else "all"
        safe_period = period if period in {"hour", "day", "week", "month"} else "day"
        url = f"{USGS_FEED_BASE}/{safe_magnitude}_{safe_period}.geojson"
        payload = self._request_json(url)
        self._record_tool("query_usgs_feed", endpoint=url, parameters={"feed_magnitude": safe_magnitude, "feed_period": safe_period})
        return self._feature_collection_to_gdf(payload), {"endpoint": url, "parameters": {"feed_magnitude": safe_magnitude, "feed_period": safe_period}}

    def _feature_collection_to_gdf(self, payload: dict[str, Any]) -> gpd.GeoDataFrame:
        records: list[dict[str, Any]] = []
        for feature in payload.get("features", []) if isinstance(payload, dict) else []:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            geom = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
            coords = geom.get("coordinates") if isinstance(geom.get("coordinates"), list) else [None, None, None]
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            depth = coords[2] if len(coords) > 2 else None
            event_time = props.get("time")
            records.append(
                {
                    "event_id": feature.get("id"),
                    "time": datetime.fromtimestamp(event_time / 1000, tz=timezone.utc).isoformat() if isinstance(event_time, (int, float)) else None,
                    "updated": props.get("updated"),
                    "place": props.get("place"),
                    "mag": props.get("mag"),
                    "depth_km": depth,
                    "longitude": lon,
                    "latitude": lat,
                    "sig": props.get("sig"),
                    "felt": props.get("felt"),
                    "alert": props.get("alert"),
                    "tsunami": props.get("tsunami"),
                    "type": props.get("type"),
                    "status": props.get("status"),
                    "url": props.get("url"),
                    "detail": props.get("detail"),
                    "net": props.get("net"),
                    "code": props.get("code"),
                    "geometry": Point(lon, lat) if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) else None,
                }
            )
        if not records:
            columns = [
                "event_id",
                "time",
                "updated",
                "place",
                "mag",
                "depth_km",
                "longitude",
                "latitude",
                "sig",
                "felt",
                "alert",
                "tsunami",
                "type",
                "status",
                "url",
                "detail",
                "net",
                "code",
                "geometry",
            ]
            return gpd.GeoDataFrame({column: [] for column in columns}, geometry="geometry", crs="EPSG:4326")
        gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
        return gdf

    def save_events(self, gdf: gpd.GeoDataFrame, query: str, output_format: str) -> str:
        path = self._output_path(query, output_format, "earthquakes")
        self._record_tool("save_events", output_format=output_format, event_count=int(len(gdf)))
        if output_format == "csv":
            pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore")).to_csv(path, index=False)
        elif output_format == "parquet":
            gdf.to_parquet(path, index=False)
        elif output_format == "gpkg":
            gdf.to_file(path, driver="GPKG")
        else:
            gdf.to_file(path, driver="GeoJSON")
        return path

    def create_event_table(self, gdf: gpd.GeoDataFrame, query: str) -> str:
        path = self._output_path(query, "csv", "earthquake_table")
        columns = ["event_id", "time", "place", "mag", "depth_km", "latitude", "longitude", "sig", "felt", "alert", "tsunami", "url"]
        available = [column for column in columns if column in gdf.columns]
        pd.DataFrame(gdf[available]).to_csv(path, index=False)
        self._record_tool("create_event_table", path=path)
        return path

    def generate_epicenter_map(self, gdf: gpd.GeoDataFrame, query: str, style: str) -> str:
        path = self._output_path(query, "png", "earthquake_map")
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.set_title("USGS Earthquake Epicenters")
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        if gdf.empty:
            ax.text(0.5, 0.5, "No earthquake events matched the request.", ha="center", va="center", transform=ax.transAxes)
        else:
            mags = pd.to_numeric(gdf.get("mag"), errors="coerce").fillna(0)
            depths = pd.to_numeric(gdf.get("depth_km"), errors="coerce").fillna(0)
            sizes = (mags.clip(lower=0) + 1) ** 2 * 8
            color_values = depths if style == "depth" else mags
            label = "Depth (km)" if style == "depth" else "Magnitude"
            scatter = ax.scatter(gdf["longitude"], gdf["latitude"], s=sizes, c=color_values, cmap="viridis", alpha=0.75, edgecolor="black", linewidth=0.3)
            fig.colorbar(scatter, ax=ax, label=label)
            bounds = gdf.total_bounds
            if all(math.isfinite(float(value)) for value in bounds):
                xpad = max((bounds[2] - bounds[0]) * 0.08, 0.5)
                ypad = max((bounds[3] - bounds[1]) * 0.08, 0.5)
                ax.set_xlim(bounds[0] - xpad, bounds[2] + xpad)
                ax.set_ylim(bounds[1] - ypad, bounds[3] + ypad)
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        self._record_tool("generate_epicenter_map", style=style, path=path)
        return path

    def generate_interactive_map(self, gdf: gpd.GeoDataFrame, query: str, style: str) -> str:
        path = self._output_path(query, "html", "earthquake_interactive_map")
        try:
            import folium
        except ImportError:
            html_body = "<p>folium is required to generate the interactive basemap artifact.</p>"
            Path(path).write_text(f"<!doctype html><html><body>{html_body}</body></html>", encoding="utf-8")
            self._record_tool("generate_interactive_map", style=style, path=path, fallback="missing_folium")
            return path

        if gdf.empty:
            center = [0, 0]
            zoom_start = 2
        else:
            center = [float(gdf["latitude"].mean()), float(gdf["longitude"].mean())]
            zoom_start = 5 if len(gdf) > 1 else 7

        fmap = folium.Map(location=center, zoom_start=zoom_start, tiles="CartoDB positron", control_scale=True)
        folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(fmap)
        folium.TileLayer("CartoDB dark_matter", name="Dark basemap").add_to(fmap)

        for _, row in gdf.iterrows():
            lat = row.get("latitude")
            lon = row.get("longitude")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            mag = row.get("mag")
            depth = row.get("depth_km")
            radius = max(4.0, (float(mag) if isinstance(mag, (int, float)) else 1.0) * 3.0)
            color = self._depth_color(depth) if style == "depth" else "#d95f02"
            popup = folium.Popup(
                html=(
                    f"<strong>{html.escape(str(row.get('place') or 'Earthquake'))}</strong><br>"
                    f"Magnitude: {html.escape(str(mag))}<br>"
                    f"Depth km: {html.escape(str(depth))}<br>"
                    f"Time: {html.escape(str(row.get('time')))}<br>"
                    f"<a href=\"{html.escape(str(row.get('url') or '#'))}\" target=\"_blank\">USGS event page</a>"
                ),
                max_width=320,
            )
            folium.CircleMarker(
                location=[lat, lon],
                radius=radius,
                color=color,
                weight=1,
                fill=True,
                fill_color=color,
                fill_opacity=0.68,
                popup=popup,
                tooltip=f"M{mag} | {row.get('place')}",
            ).add_to(fmap)

        if not gdf.empty:
            minx, miny, maxx, maxy = [float(value) for value in gdf.total_bounds]
            fmap.fit_bounds([[miny, minx], [maxy, maxx]], padding=(30, 30))
        folium.LayerControl(collapsed=False).add_to(fmap)
        fmap.save(path)
        self._record_tool("generate_interactive_map", style=style, path=path)
        return path

    def create_time_animation_geojson(self, gdf: gpd.GeoDataFrame, query: str) -> str:
        path = self._output_path(query, "geojson", "earthquake_animation")
        animation = gdf.copy()
        animation["times"] = animation.get("time")
        animation["style_radius"] = pd.to_numeric(animation.get("mag"), errors="coerce").fillna(0).clip(lower=0).add(2).mul(2)
        animation["style_color"] = pd.to_numeric(animation.get("depth_km"), errors="coerce").apply(self._depth_color)
        animation.to_file(path, driver="GeoJSON")
        self._record_tool("create_time_animation_geojson", path=path)
        return path

    def _depth_color(self, depth: Any) -> str:
        try:
            value = float(depth)
        except (TypeError, ValueError):
            return "#888888"
        if value < 70:
            return "#d7191c"
        if value < 300:
            return "#fdae61"
        return "#2c7bb6"

    def create_impact_buffers(self, gdf: gpd.GeoDataFrame, query: str, buffer_km: float) -> str:
        path = self._output_path(query, "geojson", "earthquake_buffers")
        if gdf.empty:
            buffers = gpd.GeoDataFrame(columns=["event_id", "mag", "buffer_km", "geometry"], geometry="geometry", crs="EPSG:4326")
        else:
            projected = gdf.to_crs("EPSG:3857")
            distances = pd.to_numeric(projected.get("mag"), errors="coerce").fillna(0).apply(lambda mag: max(buffer_km, float(mag) * buffer_km / 2.0))
            projected["buffer_km"] = distances
            projected["geometry"] = projected.geometry.buffer(projected["buffer_km"] * 1000)
            buffers = projected[["event_id", "mag", "buffer_km", "geometry"]].to_crs("EPSG:4326")
        buffers.to_file(path, driver="GeoJSON")
        self._record_tool("create_impact_buffers", buffer_km=buffer_km, path=path)
        return path

    def summarize_by_grid(self, gdf: gpd.GeoDataFrame, query: str, grid_degrees: float) -> tuple[str | None, dict[str, Any]]:
        if gdf.empty:
            return None, {"grid_cell_count": 0, "max_events_per_cell": 0}
        minx, miny, maxx, maxy = [float(value) for value in gdf.total_bounds]
        step = max(0.1, min(float(grid_degrees), 10.0))
        cells = []
        y = math.floor(miny / step) * step
        while y <= maxy:
            x = math.floor(minx / step) * step
            while x <= maxx:
                cells.append({"grid_id": f"{round(x, 4)}_{round(y, 4)}", "geometry": box(x, y, x + step, y + step)})
                x += step
            y += step
        grid = gpd.GeoDataFrame(cells, geometry="geometry", crs="EPSG:4326")
        joined = gpd.sjoin(gdf[["event_id", "mag", "depth_km", "geometry"]], grid, how="left", predicate="within")
        summary = joined.groupby("grid_id", dropna=True).agg(
            event_count=("event_id", "count"),
            max_magnitude=("mag", "max"),
            mean_depth_km=("depth_km", "mean"),
        ).reset_index()
        grid = grid.merge(summary, on="grid_id", how="left")
        grid["event_count"] = grid["event_count"].fillna(0).astype(int)
        path = self._output_path(query, "geojson", "earthquake_grid_summary")
        grid[grid["event_count"] > 0].to_file(path, driver="GeoJSON")
        stats = {"grid_cell_count": int((grid["event_count"] > 0).sum()), "max_events_per_cell": int(grid["event_count"].max())}
        self._record_tool("summarize_by_grid", grid_degrees=step, path=path)
        return path, stats

    def detect_clusters(self, gdf: gpd.GeoDataFrame) -> dict[str, Any]:
        if len(gdf) < 3:
            return {"cluster_count": 0, "method": "simple_distance_bins", "clusters": []}
        try:
            from sklearn.cluster import DBSCAN

            coords = gdf[["latitude", "longitude"]].dropna().to_numpy()
            labels = DBSCAN(eps=1.0, min_samples=3).fit_predict(coords)
            labeled = pd.Series(labels)
            clusters = []
            for label in sorted(value for value in labeled.unique() if value >= 0):
                count = int((labeled == label).sum())
                clusters.append({"cluster_id": int(label), "event_count": count})
            self._record_tool("detect_clusters", method="DBSCAN", cluster_count=len(clusters))
            return {"cluster_count": len(clusters), "method": "DBSCAN_eps_1_degree_min_samples_3", "clusters": clusters}
        except Exception:
            rounded = gdf.assign(lat_bin=gdf["latitude"].round(), lon_bin=gdf["longitude"].round())
            groups = rounded.groupby(["lat_bin", "lon_bin"]).size().reset_index(name="event_count")
            clusters = groups[groups["event_count"] >= 3].to_dict("records")
            self._record_tool("detect_clusters", method="rounded_degree_bins", cluster_count=len(clusters))
            return {"cluster_count": len(clusters), "method": "rounded_degree_bins", "clusters": clusters}

    def summarize_events(self, gdf: gpd.GeoDataFrame, query_info: dict[str, Any], clusters: dict[str, Any] | None = None) -> dict[str, Any]:
        if gdf.empty:
            return {
                "event_count": 0,
                "top_events": [],
                "magnitude": {},
                "depth": {},
                "time_range": {},
                "alert_counts": {},
                "tsunami_flag_count": 0,
                "clusters": clusters or {},
            }
        mags = pd.to_numeric(gdf["mag"], errors="coerce")
        depths = pd.to_numeric(gdf["depth_km"], errors="coerce")
        top_cols = ["event_id", "time", "place", "mag", "depth_km", "sig", "felt", "alert", "tsunami", "url"]
        top_events = (
            gdf.sort_values(["mag", "sig"], ascending=False)[[column for column in top_cols if column in gdf.columns]]
            .head(10)
            .to_dict("records")
        )
        return {
            "event_count": int(len(gdf)),
            "top_events": top_events,
            "magnitude": {
                "min": float(mags.min()) if mags.notna().any() else None,
                "max": float(mags.max()) if mags.notna().any() else None,
                "mean": float(mags.mean()) if mags.notna().any() else None,
                "m4_plus": int((mags >= 4).sum()),
                "m5_plus": int((mags >= 5).sum()),
                "m6_plus": int((mags >= 6).sum()),
            },
            "depth": {
                "min_km": float(depths.min()) if depths.notna().any() else None,
                "max_km": float(depths.max()) if depths.notna().any() else None,
                "mean_km": float(depths.mean()) if depths.notna().any() else None,
                "shallow_lt_70km": int((depths < 70).sum()),
            },
            "time_range": {"start": str(gdf["time"].min()), "end": str(gdf["time"].max())},
            "alert_counts": {str(key): int(value) for key, value in gdf["alert"].fillna("none").value_counts().items()} if "alert" in gdf else {},
            "tsunami_flag_count": int(pd.to_numeric(gdf.get("tsunami"), errors="coerce").fillna(0).sum()) if "tsunami" in gdf else 0,
            "query": query_info,
            "clusters": clusters or {},
        }

    def create_alert_summary(self, summary: dict[str, Any], plan: EarthquakePlan) -> str:
        count = summary.get("event_count", 0)
        mag = summary.get("magnitude", {})
        max_mag = mag.get("max")
        region = plan.parameters.get("region_name") or "the requested area"
        threshold = plan.parameters.get("minmagnitude")
        threshold_text = f"M{threshold}+" if threshold is not None else "matching"
        if count == 0:
            return f"No {threshold_text} earthquakes were found for {region} in the requested time window."
        top = summary.get("top_events", [{}])[0] if summary.get("top_events") else {}
        return (
            f"{count} {threshold_text} earthquake event(s) were found for {region}. "
            f"The largest event was M{max_mag} near {top.get('place', 'an unspecified location')} at {top.get('time', 'an unknown time')}."
        )

    def _chart_paths(self, gdf: gpd.GeoDataFrame, query: str) -> list[str]:
        if gdf.empty:
            return []
        paths = []
        mags = pd.to_numeric(gdf["mag"], errors="coerce").dropna()
        depths = pd.to_numeric(gdf["depth_km"], errors="coerce").dropna()
        if not mags.empty:
            path = self._output_path(query, "png", "earthquake_magnitude_histogram")
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.hist(mags, bins=min(12, max(3, len(mags))), color="#4c78a8", edgecolor="white")
            ax.set_title("Magnitude Distribution")
            ax.set_xlabel("Magnitude")
            ax.set_ylabel("Event count")
            fig.tight_layout()
            fig.savefig(path, dpi=150)
            plt.close(fig)
            paths.append(path)
        if not depths.empty:
            path = self._output_path(query, "png", "earthquake_depth_histogram")
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.hist(depths, bins=min(12, max(3, len(depths))), color="#59a14f", edgecolor="white")
            ax.set_title("Depth Distribution")
            ax.set_xlabel("Depth (km)")
            ax.set_ylabel("Event count")
            fig.tight_layout()
            fig.savefig(path, dpi=150)
            plt.close(fig)
            paths.append(path)
        self._record_tool("generate_charts", chart_count=len(paths))
        return paths

    def generate_markdown_report(self, query: str, summary: dict[str, Any], query_info: dict[str, Any], alert_summary: str | None) -> str:
        path = self._output_path(query, "md", "earthquake_report")
        top_events = summary.get("top_events", [])
        lines = [
            "# USGS Earthquake Activity Report",
            "",
            f"Task: {query}",
            f"Retrieved at: {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Summary",
            "",
            f"- Event count: {summary.get('event_count', 0)}",
            f"- Maximum magnitude: {summary.get('magnitude', {}).get('max')}",
            f"- Mean depth km: {summary.get('depth', {}).get('mean_km')}",
        ]
        if alert_summary:
            lines.extend(["", "## Alert Summary", "", alert_summary])
        lines.extend(["", "## Top Events", ""])
        if top_events:
            lines.append("| Time | Place | Magnitude | Depth km | Alert | URL |")
            lines.append("|---|---|---:|---:|---|---|")
            for event in top_events[:10]:
                lines.append(
                    f"| {event.get('time')} | {event.get('place')} | {event.get('mag')} | "
                    f"{event.get('depth_km')} | {event.get('alert')} | {event.get('url')} |"
                )
        else:
            lines.append("No events matched the request.")
        lines.extend(
            [
                "",
                "## Methods And Provenance",
                "",
                f"- USGS endpoint: {query_info.get('endpoint')}",
                f"- Query parameters: `{json.dumps(query_info.get('parameters', {}), default=str)}`",
                "",
                "## Limitations",
                "",
                "- Recent USGS event records can be revised after initial publication.",
                "- Magnitudes, depths, and locations may change as reviews are completed.",
                "- Very recent or low-magnitude catalogs may be incomplete depending on network coverage.",
            ]
        )
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self._record_tool("generate_markdown_report", path=path)
        return path

    def generate_html_report(
        self,
        query: str,
        summary: dict[str, Any],
        query_info: dict[str, Any],
        artifact_paths: list[str],
        alert_summary: str | None,
    ) -> str:
        path = self._output_path(query, "html", "earthquake_report")
        top_rows = []
        for event in summary.get("top_events", [])[:10]:
            top_rows.append(
                "<tr>"
                f"<td>{html.escape(str(event.get('time')))}</td>"
                f"<td>{html.escape(str(event.get('place')))}</td>"
                f"<td>{html.escape(str(event.get('mag')))}</td>"
                f"<td>{html.escape(str(event.get('depth_km')))}</td>"
                f"<td>{html.escape(str(event.get('alert')))}</td>"
                f"<td><a href=\"{html.escape(str(event.get('url') or '#'))}\">USGS</a></td>"
                "</tr>"
            )
        image_tags = "\n".join(
            f"<figure><img src=\"{html.escape(Path(p).name)}\" alt=\"{html.escape(Path(p).stem)}\"><figcaption>{html.escape(Path(p).name)}</figcaption></figure>"
            for p in artifact_paths
            if Path(p).suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>USGS Earthquake Activity Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #222; }}
    h1, h2 {{ color: #1f3b57; }}
    .metric {{ display: inline-block; margin: 0 16px 16px 0; padding: 12px 14px; border: 1px solid #ddd; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f4f6f8; }}
    img {{ max-width: 100%; height: auto; border: 1px solid #ddd; }}
  </style>
</head>
<body>
  <h1>USGS Earthquake Activity Report</h1>
  <p>{html.escape(query)}</p>
  <section>
    <h2>Summary</h2>
    <div class="metric"><strong>Events</strong><br>{summary.get('event_count', 0)}</div>
    <div class="metric"><strong>Max magnitude</strong><br>{summary.get('magnitude', {}).get('max')}</div>
    <div class="metric"><strong>Mean depth km</strong><br>{summary.get('depth', {}).get('mean_km')}</div>
    <div class="metric"><strong>Tsunami flags</strong><br>{summary.get('tsunami_flag_count', 0)}</div>
    {f"<p><strong>Alert:</strong> {html.escape(alert_summary)}</p>" if alert_summary else ""}
  </section>
  <section>
    <h2>Maps And Charts</h2>
    {image_tags}
  </section>
  <section>
    <h2>Top Events</h2>
    <table>
      <thead><tr><th>Time</th><th>Place</th><th>Magnitude</th><th>Depth km</th><th>Alert</th><th>URL</th></tr></thead>
      <tbody>{''.join(top_rows) or '<tr><td colspan="6">No matching events.</td></tr>'}</tbody>
    </table>
  </section>
  <section>
    <h2>Methods And Provenance</h2>
    <p>USGS endpoint: {html.escape(str(query_info.get('endpoint')))}</p>
    <pre>{html.escape(json.dumps(query_info.get('parameters', {}), indent=2, default=str))}</pre>
    <p>Retrieved at: {datetime.now(timezone.utc).isoformat()}</p>
  </section>
  <section>
    <h2>Limitations</h2>
    <ul>
      <li>Recent USGS event records can be revised after initial publication.</li>
      <li>Magnitudes, depths, and locations may change as reviews are completed.</li>
      <li>Very recent or low-magnitude catalogs may be incomplete depending on network coverage.</li>
    </ul>
  </section>
</body>
</html>"""
        Path(path).write_text(body, encoding="utf-8")
        self._record_tool("generate_html_report", path=path)
        return path

    def run(
        self,
        query: str,
        input_dataset_paths: list[str] | str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        start_time = time.time()
        self.reset_metrics()
        self.tool_trace = []
        input_paths = self.normalize_dataset_paths(input_dataset_paths)
        self.emit_progress(progress_callback, stage="planning", message="I am interpreting the earthquake request and choosing USGS query and output tools.")
        plan = self._build_plan(query)

        self.emit_progress(progress_callback, stage="download_start", message="I am retrieving earthquake events from USGS.", data={"query_mode": plan.query_mode})
        if plan.query_mode == "feed":
            gdf, query_info = self.query_usgs_feed(str(plan.parameters.get("feed_magnitude", "all")), str(plan.parameters.get("feed_period", "day")))
        else:
            gdf, query_info = self.query_usgs_catalog(plan.parameters)
        self.emit_progress(progress_callback, stage="download_complete", message=f"USGS returned {len(gdf)} earthquake event(s).")

        artifacts: list[str] = []
        dataset_path = self.save_events(gdf, query, plan.output_format)
        artifacts.append(dataset_path)
        if plan.parameters.pop("_include_event_table", False) and plan.output_format != "csv":
            table_path = self.create_event_table(gdf, query)
            artifacts.append(table_path)

        map_path = None
        if plan.include_map or plan.include_html_report:
            map_path = self.generate_epicenter_map(gdf, query, "depth" if plan.map_style == "depth" else "magnitude")
            artifacts.append(map_path)
            artifacts.append(self.generate_interactive_map(gdf, query, "depth" if plan.map_style == "depth" else "magnitude"))
        if plan.include_animation:
            artifacts.append(self.create_time_animation_geojson(gdf, query))
        grid_stats = {}
        if plan.include_grid_summary:
            grid_path, grid_stats = self.summarize_by_grid(gdf, query, plan.grid_degrees)
            if grid_path:
                artifacts.append(grid_path)
        if plan.include_buffers:
            artifacts.append(self.create_impact_buffers(gdf, query, plan.buffer_km))
        clusters = self.detect_clusters(gdf) if plan.include_clusters else {}
        chart_paths = self._chart_paths(gdf, query) if plan.include_html_report else []
        artifacts.extend(chart_paths)

        summary = self.summarize_events(gdf, query_info, clusters=clusters)
        if grid_stats:
            summary["grid_summary"] = grid_stats
        alert_summary = self.create_alert_summary(summary, plan) if plan.include_alert_summary else None

        if plan.include_markdown_report:
            artifacts.append(self.generate_markdown_report(query, summary, query_info, alert_summary))
        if plan.include_html_report:
            artifacts.append(self.generate_html_report(query, summary, query_info, artifacts, alert_summary))

        artifacts = list(dict.fromkeys(artifacts))
        self.set_artifact_count(len(artifacts))
        self.emit_progress(progress_callback, stage="response_preparation", message="I am packaging earthquake datasets, maps, reports, provenance, and limitations.")

        text = (
            f"Retrieved {summary.get('event_count', 0)} USGS earthquake event(s). "
            f"Primary dataset saved as {Path(dataset_path).name}."
        )
        if alert_summary:
            text = f"{text} {alert_summary}"

        duration = time.time() - start_time
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model if self.llm_calls else None,
            "duration": duration,
            "inputs": {"dataset_paths": input_paths, "parameters": self.request_parameters},
            "outputs": {
                "text": text,
                "output_files": artifacts,
                "dataset_path": dataset_path,
                "dataset_paths": artifacts,
                "dataset_size": {
                    "type": "earthquake_events",
                    "feature_count": int(len(gdf)),
                    "dimensions": [int(len(gdf)), int(len(gdf.columns))],
                },
                "earthquake_summary": summary,
                "alert_summary": alert_summary,
                "query": query_info,
                "plan": {
                    "action": plan.action,
                    "query_mode": plan.query_mode,
                    "source": plan.source,
                    "output_format": plan.output_format,
                    "notes": plan.notes,
                },
            },
            "metrics": self.metrics(number_of_artifacts=len(artifacts)),
            "environment": {
                "python_version": platform.python_version(),
                "domain-specific_libraries": ["requests", "pandas", "geopandas", "shapely", "matplotlib"],
            },
            "complementary": {
                "Execution": {
                    "Inputs": {"task": query, "dataset_paths": input_paths, "parameters": self.request_parameters},
                    "Outputs": {"summary": text, "artifacts": artifacts, "query": query_info},
                },
                "Provenance": {
                    "Lineage": [
                        "Parsed the earthquake request into USGS query and output options.",
                        "Retrieved events from the USGS Earthquake Catalog or real-time feed.",
                        "Created deterministic dataset, table, map, summary, monitoring, and report artifacts as requested.",
                    ],
                    "Tool Calls": {"count": self.tool_calls, "tools": self.tool_trace},
                    "LLM Calls": {"count": self.llm_calls},
                },
                "Validation": {
                    "status": "passed",
                    "checks": [
                        {"name": "usgs_response_parsed", "status": "passed", "message": f"Parsed {len(gdf)} event(s) into a GeoDataFrame."},
                        {"name": "artifact_count", "status": "passed", "message": f"Created {len(artifacts)} artifact(s)."},
                    ],
                },
                "Assumptions and Limitations": {
                    "assumptions": [
                        "USGS event coordinates are WGS84 longitude, latitude, depth-kilometer triples.",
                        "Natural-language place resolution is limited to built-in common regions unless explicit coordinates or bounding boxes are supplied.",
                    ],
                    "limitations": [
                        "Recent USGS records can be revised after initial publication.",
                        "Magnitude, depth, and location uncertainty are not modeled beyond fields returned by USGS.",
                        "Low-magnitude catalog completeness depends on seismic network coverage and time since event.",
                    ],
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {},
                    "Persisted Artifacts": {"paths": artifacts},
                },
            },
        }
