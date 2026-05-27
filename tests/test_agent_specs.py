import json
from types import SimpleNamespace

import pytest

from gas_server.core.agent_specs import SPECS
from gas_server.core.service_registry import SERVICE_REGISTRY
from gas_server.agents import mapping_agent
from gas_server.agents.geospatial_data_retrieval_agent import GeospatialDataRetrievalAgent
from gas_server.agents.map_projection_agent import MapProjectionAgent
from gas_server.agents.mapping_agent import MappingAgent
from gas_server.agents.pasda_agent import PasdaAgent
from gas_server.agents.raster_agent import RasterAgent
from gas_server.agents.vector_analysis_agent import VectorAnalysisAgent


REQUIRED_INPUT_DATASET_AGENTS = {
    "exploratory_spatial_data_analysis_agent",
    "geospatial_data_inspection_agent",
    "web_mapping_app_agent",
    "mapping_agent",
    "map_projection_agent",
    "raster_agent",
    "spatial_analysis_agent",
    "spatial_statistics_agent",
    "vector_analysis_agent",
}


def test_all_registered_services_have_agent_specs():
    assert set(SPECS) == set(SERVICE_REGISTRY)


def test_agent_specs_have_required_identity_and_callables():
    for agent_id, spec in SPECS.items():
        assert spec.agent_id == agent_id
        assert spec.agent_id.endswith("_agent")
        assert callable(spec.build_agent)
        assert callable(spec.run_agent)
        assert spec.run_agent_with_progress is None or callable(spec.run_agent_with_progress)
        assert callable(spec.get_name)
        assert callable(spec.get_version)
        assert callable(spec.validate_inputs)


def test_dataset_dependent_agents_require_input_datasets():
    for agent_id, spec in SPECS.items():
        validation_error = spec.validate_inputs([])
        if agent_id in REQUIRED_INPUT_DATASET_AGENTS:
            assert validation_error is not None
            assert "input_datasets" in validation_error
            continue
        assert validation_error is None


def test_agent_specs_are_generated_from_service_registry():
    assert set(SPECS) == set(SERVICE_REGISTRY)
    for agent_id, registration in SERVICE_REGISTRY.items():
        assert SPECS[agent_id].agent_id == registration.agent_id
        assert SPECS[agent_id].build_agent == registration.build_agent
        assert SPECS[agent_id].run_agent == registration.run_agent


def test_mapping_agent_has_logging_available_for_retry_path():
    assert hasattr(mapping_agent, "logging")


def test_mapping_agent_fallback_choropleth_uses_census_population_strings(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "census_population.geojson"
    output_path = tmp_path / "choropleth.png"

    gdf = gpd.GeoDataFrame(
        {
            "B01001_001E:Total:": ["1,000", "2,500", "4,000", "8,000", "16,000"],
            "county_name": ["A", "B", "C", "D", "E"],
            "state_fips": ["01", "01", "01", "01", "01"],
            "county_fips": ["001", "003", "005", "007", "009"],
            "year": ["2021", "2021", "2021", "2021", "2021"],
            "geometry": [
                shapely_geometry.box(0, 0, 1, 1),
                shapely_geometry.box(1, 0, 2, 1),
                shapely_geometry.box(2, 0, 3, 1),
                shapely_geometry.box(3, 0, 4, 1),
                shapely_geometry.box(4, 0, 5, 1),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = MappingAgent(api_key=None)
    success = agent._fallback_visualization(
        "Create a county-level choropleth map of the 2021 population using 5 quantile classes.",
        [str(dataset_path)],
        str(output_path),
    )

    assert success is True
    assert output_path.exists()
    assert agent.feature_count == 5
    assert "B01001_001E:Total:" in agent.final_summary


def test_mapping_agent_fast_renderer_handles_common_choropleth_classification_terms(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "population.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "population": [100, 200, 300, 400, 500],
            "geometry": [
                shapely_geometry.box(0, 0, 1, 1),
                shapely_geometry.box(1, 0, 2, 1),
                shapely_geometry.box(2, 0, 3, 1),
                shapely_geometry.box(3, 0, 4, 1),
                shapely_geometry.box(4, 0, 5, 1),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = MappingAgent(api_key=None)

    assert not agent._should_try_fast_renderer(
        "Create a choropleth map using 5 quantile classes.",
        [str(dataset_path)],
    )
    assert agent._should_try_fast_renderer(
        "Create a quick map using 5 quantile classes.",
        [str(dataset_path)],
    )
    agent.set_request_parameters({"renderer": "deterministic"})
    assert agent._should_try_fast_renderer(
        "Create a choropleth map using 5 quantile classes.",
        [str(dataset_path)],
    )
    assert agent._requested_classification_scheme("Use equal interval classes") == "equal_interval"
    assert agent._requested_classification_scheme("Use natural breaks") == "natural_breaks"


def test_mapping_agent_sanitizes_loc_colormap_assignment():
    agent = MappingAgent(api_key=None)
    code = "gdf.loc[gdf['class'] == i, '_color'] = cmap(i)"

    sanitized = agent._sanitize_generated_code(code)

    assert "__gas_mask" in sanitized
    assert "gdf.loc[__gas_mask, '_color'] = [cmap(i)] * int(__gas_mask.sum())" in sanitized


def test_raster_agent_direct_vector_rasterization_preserves_georeferencing(tmp_path, monkeypatch):
    gpd = pytest.importorskip("geopandas")
    rasterio = pytest.importorskip("rasterio")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    import gas_server.agents.raster_agent as raster_module

    monkeypatch.setattr(raster_module, "BASE_DIR", str(tmp_path))

    dataset_path = tmp_path / "pa_density.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "STUSPS": ["PA", "PA"],
            "population_density": [10.0, 20.0],
            "geometry": [
                shapely_geometry.box(0, 0, 300, 300),
                shapely_geometry.box(300, 0, 600, 300),
            ],
        },
        crs="EPSG:5070",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = RasterAgent(api_key=None)
    result = agent.run(
        (
            "Create a georeferenced GeoTIFF raster for Pennsylvania from the county polygon dataset. "
            "Use the 'population_density' attribute as the raster value. Use 300-meter pixels."
        ),
        [str(dataset_path)],
    )

    output_paths = result["outputs"]["dataset_paths"]
    assert len(output_paths) == 1
    output_path = output_paths[0]

    with rasterio.open(output_path) as src:
        assert src.crs.to_epsg() == 5070
        assert src.transform.a == 300
        assert src.transform.e == -300
        assert src.width == 2
        assert src.height == 1
        assert src.nodata == -9999.0
        assert src.read(1).tolist() == [[10.0, 20.0]]

    assert result["metrics"]["number_of_artifacts"] == 1
    assert result["outputs"]["dataset_size"]["type"] == "raster"


def test_raster_agent_save_result_accepts_array_profile_artifact(tmp_path, monkeypatch):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    import gas_server.agents.raster_agent as raster_module

    monkeypatch.setattr(raster_module, "BASE_DIR", str(tmp_path))

    agent = RasterAgent(api_key=None)
    agent.registry["raster_result"] = {
        "array": np.array([[1, 2], [3, 4]], dtype="float32"),
        "profile": {
            "driver": "GTiff",
            "height": 2,
            "width": 2,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:5070",
            "transform": rasterio.transform.from_origin(100, 200, 300, 300),
            "nodata": -9999.0,
        },
    }
    agent.final_artifact_key = "raster_result"

    output_paths, metadata, saved_artifacts = agent._save_result("Save test raster")

    assert len(output_paths) == 1
    assert metadata["type"] == "raster"
    assert saved_artifacts[0]["metadata"]["crs"] == "EPSG:5070"

    with rasterio.open(output_paths[0]) as src:
        assert src.crs.to_epsg() == 5070
        assert src.transform.a == 300
        assert src.transform.e == -300
        assert src.read(1).tolist() == [[1.0, 2.0], [3.0, 4.0]]


def test_raster_agent_save_result_skips_metadata_dict_when_valid_artifact_exists(tmp_path, monkeypatch):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    import gas_server.agents.raster_agent as raster_module

    monkeypatch.setattr(raster_module, "BASE_DIR", str(tmp_path))

    agent = RasterAgent(api_key=None)
    agent.registry["raster_result"] = {
        "array": np.array([[1, 2], [3, 4]], dtype="float32"),
        "profile": {
            "driver": "GTiff",
            "height": 2,
            "width": 2,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:5070",
            "transform": rasterio.transform.from_origin(100, 200, 300, 300),
            "nodata": -9999.0,
        },
    }
    agent.registry["raster_info"] = {"crs": "EPSG:5070", "width": 2, "height": 2}
    agent.final_artifact_keys = ["raster_info", "raster_result"]

    output_paths, metadata, saved_artifacts = agent._save_result("Save raster and metadata")

    assert len(output_paths) == 1
    assert len(saved_artifacts) == 1
    assert metadata["type"] == "raster"
    assert "Skipped registered artifact 'raster_info'" in agent.runtime_memory["warnings"][0]


def test_raster_agent_resolution_parser_supports_common_units():
    agent = RasterAgent(api_key=None)

    assert agent._extract_requested_resolution("Use 300-meter pixels") == 300
    assert agent._extract_requested_resolution("Use 0.5 km cells") == 500
    assert agent._extract_requested_resolution("Use 1000 ft cells") == pytest.approx(304.8)


def test_raster_agent_saves_vector_artifacts_as_geopackage_by_default(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    agent = RasterAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    agent.registry["vector_result"] = gpd.GeoDataFrame(
        {"id": [1], "geometry": [shapely_geometry.Point(0, 0)]},
        crs="EPSG:4326",
    )
    agent.final_artifact_keys = ["vector_result"]

    output_paths, metadata, _ = agent._save_result("Save vector output")

    assert output_paths[0].endswith(".gpkg")
    assert metadata["type"] == "vector"


def test_raster_agent_rejects_ungeoreferenced_numpy_artifact(tmp_path, monkeypatch):
    import numpy as np
    import gas_server.agents.raster_agent as raster_module

    monkeypatch.setattr(raster_module, "BASE_DIR", str(tmp_path))

    agent = RasterAgent(api_key=None)
    agent.registry["raw_array"] = np.array([[1, 2], [3, 4]], dtype="float32")
    agent.final_artifact_key = "raw_array"

    with pytest.raises(ValueError, match="missing georeferencing metadata"):
        agent._save_result("Save raw array")


def test_raster_agent_fails_fast_when_rasterio_missing(monkeypatch):
    import gas_server.agents.raster_agent as raster_module

    monkeypatch.setattr(raster_module, "rasterio", None)

    agent = RasterAgent(api_key=None)
    result = agent.run(
        "Rasterize the polygons to a GeoTIFF using 100-meter pixels.",
        ["dummy.geojson"],
    )

    assert result["agent_name"] == "Raster Agent"
    assert result["agent_version"] == "3.0.0"
    assert result["outputs"]["dataset_paths"] == []
    assert "rasterio is required" in result["outputs"]["text"]
    assert result["metrics"]["llm_calls"] == 0


def test_raster_agent_meter_rasterization_requires_projected_crs(tmp_path, monkeypatch):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    import gas_server.agents.raster_agent as raster_module

    monkeypatch.setattr(raster_module, "BASE_DIR", str(tmp_path))

    dataset_path = tmp_path / "pa_density_wgs84.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "STUSPS": ["PA"],
            "population_density": [10.0],
            "geometry": [shapely_geometry.box(-77, 40, -76.9, 40.1)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = RasterAgent(api_key=None)
    result = agent.run(
        "Rasterize the 'population_density' field over PA at a resolution of 300 meters.",
        [str(dataset_path)],
    )

    assert result["outputs"]["dataset_paths"] == []
    assert "Project the data first" in result["outputs"]["text"]


def test_raster_agent_sandbox_toolkit_loads_and_rasterizes_input(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "density.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "population_density": [5.0, 15.0],
            "geometry": [
                shapely_geometry.box(0, 0, 100, 100),
                shapely_geometry.box(100, 0, 200, 100),
            ],
        },
        crs="EPSG:5070",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = RasterAgent(api_key=None)
    agent.registry["input_paths"] = [str(dataset_path)]
    result = agent._execute_in_sandbox(
        "\n".join(
            [
                "gdf = load_input(0)",
                "rasterize_vector(gdf, 'population_density', 100, key='density_raster')",
                "print(list_registry())",
            ]
        )
    )

    assert result["error"] is None
    raster_artifact = agent.registry["density_raster"]
    assert raster_artifact["array"].tolist() == [[5.0, 15.0]]
    assert str(raster_artifact["profile"]["crs"]) == "EPSG:5070"
    assert raster_artifact["profile"]["transform"].a == 100


def test_raster_agent_sandbox_toolkit_clips_raster_with_vector(tmp_path):
    gpd = pytest.importorskip("geopandas")
    rasterio = pytest.importorskip("rasterio")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    import numpy as np

    raster_path = tmp_path / "source.tif"
    profile = {
        "driver": "GTiff",
        "height": 2,
        "width": 2,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:5070",
        "transform": rasterio.transform.from_origin(0, 200, 100, 100),
        "nodata": -9999.0,
    }
    with rasterio.open(raster_path, "w", **profile) as dst:
        dst.write(np.array([[1, 2], [3, 4]], dtype="float32"), 1)

    vector_path = tmp_path / "clip.geojson"
    clip_gdf = gpd.GeoDataFrame(
        {"geometry": [shapely_geometry.box(0, 0, 100, 200)]},
        crs="EPSG:5070",
    )
    clip_gdf.to_file(vector_path, driver="GeoJSON")

    agent = RasterAgent(api_key=None)
    agent.registry["input_paths"] = [str(raster_path), str(vector_path)]
    result = agent._execute_in_sandbox(
        "\n".join(
            [
                "src = load_input(0)",
                "clip = load_input(1)",
                "clip_raster_with_vector(src, clip, key='clipped')",
            ]
        )
    )

    assert result["error"] is None
    clipped = agent.registry["clipped"]
    assert clipped["array"].shape == (1, 2, 1)
    assert clipped["array"][0].tolist() == [[1.0], [3.0]]
    assert str(clipped["profile"]["crs"]) == "EPSG:5070"


def test_geospatial_data_retrieval_fallback_selects_census_boundary_source():
    agent = GeospatialDataRetrievalAgent(api_key=None)
    handbook_files = agent.collect_handbook_files(source_dir=agent.handbook_dir)
    _, data_source_dict = agent.assemble_handbook_description(handbook_files)

    selection = agent.fallback_source_selection(
        "Download Pennsylvania county boundaries from Census Bureau",
        data_source_dict,
    )

    assert selection["Selected data source"] == "US Census Bureau boundary"


def test_geospatial_data_retrieval_postprocesses_contiguous_us_statefp(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "counties.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "STATEFP": ["01", "02", "06", "15", "72"],
            "COUNTYFP": ["001", "001", "001", "001", "001"],
            "NAME": ["A", "B", "C", "D", "E"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(5)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    processed = agent.apply_request_postprocessing(
        [str(dataset_path)],
        "Download county boundaries for the contiguous US",
        "counties",
        "US Census Bureau boundary",
    )

    result = gpd.read_file(processed[0])
    assert set(result["STATEFP"]) == {"01", "06"}


def test_geospatial_data_retrieval_postprocesses_contiguous_us_geoid(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "counties.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "GEOID": ["01001", "02001", "06001", "15001", "72001"],
            "NAME": ["A", "B", "C", "D", "E"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(5)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    processed = agent.apply_request_postprocessing(
        [str(dataset_path)],
        "Download county boundaries for the lower 48 states",
        "counties",
        "US Census Bureau boundary",
    )

    result = gpd.read_file(processed[0])
    assert set(result["GEOID"]) == {"01001", "06001"}


def test_geospatial_data_retrieval_postprocesses_requested_state(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "counties.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "STATEFP": ["06", "42", "42", "36"],
            "NAME": ["Los Angeles", "Centre", "Dauphin", "Albany"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(4)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    processed = agent.apply_request_postprocessing(
        [str(dataset_path)],
        "Download Pennsylvania county boundaries",
        "pa_counties",
        "US Census Bureau boundary",
    )

    result = gpd.read_file(processed[0])
    assert set(result["STATEFP"]) == {"42"}
    assert set(result["NAME"]) == {"Centre", "Dauphin"}


def test_geospatial_data_retrieval_postprocesses_requested_county(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "counties.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "STATEFP": ["42", "42", "36"],
            "NAMELSAD": ["Centre County", "Dauphin County", "Centre County"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(3)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    processed = agent.apply_request_postprocessing(
        [str(dataset_path)],
        "Download Centre County boundaries in Pennsylvania",
        "centre_county",
        "US Census Bureau boundary",
    )

    result = gpd.read_file(processed[0])
    assert len(result) == 1
    assert result.iloc[0]["STATEFP"] == "42"
    assert result.iloc[0]["NAMELSAD"] == "Centre County"


def test_geospatial_data_retrieval_postprocesses_requested_year(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "county_population.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "GEOID": ["01001", "01001", "01001"],
            "year": ["2020", "2021", "2022"],
            "population": [10, 11, 12],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(3)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    processed = agent.apply_request_postprocessing(
        [str(dataset_path)],
        "Download 2021 county population",
        "county_population",
        "US Census Bureau demography",
    )

    result = gpd.read_file(processed[0])
    assert result["year"].tolist() == ["2021"]
    assert result["population"].tolist() == [11]


def test_geospatial_data_retrieval_direct_census_county_population_workflow(monkeypatch, tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    boundary_gdf = gpd.GeoDataFrame(
        {
            "STATEFP": ["01", "02", "06"],
            "COUNTYFP": ["001", "001", "001"],
            "GEOID": ["01001", "02001", "06001"],
            "NAME": ["Autauga", "Aleutians East", "Alameda"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(3)],
        },
        crs="EPSG:4326",
    )

    original_read_file = gpd.read_file

    def fake_read_file(path):
        if "cb_2021_us_county_500k.zip" in str(path):
            return boundary_gdf
        return original_read_file(path)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                ["NAME", "B01001_001E", "state", "county"],
                ["Autauga County, Alabama", "1000", "01", "001"],
                ["Aleutians East Borough, Alaska", "2000", "02", "001"],
                ["Alameda County, California", "3000", "06", "001"],
            ]

    monkeypatch.setattr("gas_server.agents.geospatial_data_retrieval_agent.gpd.read_file", fake_read_file)
    monkeypatch.setattr(
        "gas_server.agents.geospatial_data_retrieval_agent.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    agent.set_request_parameters(
        {
            "source_credentials": {
                "US_Census_demography": {
                    "key": "test-census-key",
                }
            }
        }
    )

    direct = agent.try_direct_census_county_population_download(
        "Download county-level 2021 population for the contiguous United States",
        "US Census Bureau demography",
        "county_population",
    )
    processed = agent.apply_request_postprocessing(
        [direct["path"]],
        "Download county-level 2021 population for the contiguous United States",
        "county_population",
        "US Census Bureau demography",
    )

    result = gpd.read_file(processed[0])
    assert set(result["GEOID"]) == {"01001", "06001"}
    assert result["B01001_001E:Total:"].tolist() == [1000, 3000]
    assert set(result["year"]) == {"2021"}


def test_geospatial_data_retrieval_direct_census_population_respects_csv_request(monkeypatch, tmp_path):
    gpd = pytest.importorskip("geopandas")
    pd = pytest.importorskip("pandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    boundary_gdf = gpd.GeoDataFrame(
        {
            "STATEFP": ["42", "42"],
            "COUNTYFP": ["001", "003"],
            "GEOID": ["42001", "42003"],
            "NAME": ["Adams", "Allegheny"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(2)],
        },
        crs="EPSG:4326",
    )

    original_read_file = gpd.read_file

    def fake_read_file(path):
        if "cb_2021_us_county_500k.zip" in str(path):
            return boundary_gdf
        return original_read_file(path)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                ["NAME", "B01001_001E", "state", "county"],
                ["Adams County, Pennsylvania", "1000", "42", "001"],
                ["Allegheny County, Pennsylvania", "2000", "42", "003"],
            ]

    monkeypatch.setattr("gas_server.agents.geospatial_data_retrieval_agent.gpd.read_file", fake_read_file)
    monkeypatch.setattr(
        "gas_server.agents.geospatial_data_retrieval_agent.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)

    direct = agent.try_direct_census_county_population_download(
        "Download PA county population data for 2021 as CSV",
        "US Census Bureau demography",
        "county_population",
    )

    assert direct["path"].endswith(".csv")
    result = pd.read_csv(direct["path"])
    assert result["GEOID"].astype(str).tolist() == ["42001", "42003"]
    assert result["B01001_001E:Total:"].tolist() == [1000, 2000]
    assert "geometry" not in result.columns
    assert "geometry_wkt" in result.columns
    assert "saved CSV" in direct["script"]

    validation = agent.self_validate_result(
        "Download PA county population data for 2021 as CSV",
        direct["path"],
        [direct["path"]],
    )
    assert validation["status"] == "passed"
    assert any(
        check["name"] == "requested_format" and check["status"] == "passed"
        for check in validation["checks"]
    )


def test_geospatial_data_retrieval_normalizes_broad_census_population_source():
    agent = GeospatialDataRetrievalAgent(api_key=None)
    data_source_dict = {
        "US Census Bureau boundary": {"ID": "US_Census_boundary"},
        "US Census Bureau demography": {"ID": "US_Census_demography"},
    }

    selected = agent.normalize_selected_data_source(
        "US Census Bureau",
        "Download county-level 2021 population for the contiguous United States",
        data_source_dict,
    )

    assert selected == "US Census Bureau demography"


def test_geospatial_data_retrieval_normalizes_broad_census_boundary_source():
    agent = GeospatialDataRetrievalAgent(api_key=None)
    data_source_dict = {
        "US Census Bureau boundary": {"ID": "US_Census_boundary"},
        "US Census Bureau demography": {"ID": "US_Census_demography"},
    }

    selected = agent.normalize_selected_data_source(
        "US Census Bureau",
        "Download Pennsylvania county boundaries",
        data_source_dict,
    )

    assert selected == "US Census Bureau boundary"


def test_vector_analysis_attribute_join_uses_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    pd = pytest.importorskip("pandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    counties_path = tmp_path / "counties.gpkg"
    obesity_path = tmp_path / "obesity.csv"
    counties = gpd.GeoDataFrame(
        {
            "GEOID": ["01001", "01003", "01005"],
            "county_name": ["A", "B", "C"],
            "geometry": [shapely_geometry.box(i, 0, i + 1, 1) for i in range(3)],
        },
        crs="EPSG:4326",
    )
    counties.to_file(counties_path, driver="GPKG")
    pd.DataFrame(
        {
            "county_fips": [1001, 1003, 1005],
            "obesity_rate": [31.2, 28.5, 35.1],
        }
    ).to_csv(obesity_path, index=False)

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    events = []
    result = agent.run(
        "Join this county GeoPackage to the obesity CSV using county FIPS/GEOID and return a mapping-ready dataset.",
        [str(counties_path), str(obesity_path)],
        progress_callback=events.append,
    )

    assert result["metrics"]["llm_calls"] == 0
    assert result["outputs"]["dataset_path"].endswith(".gpkg")
    joined = gpd.read_file(result["outputs"]["dataset_path"])
    assert len(joined) == 3
    assert joined["obesity_rate"].tolist() == [31.2, 28.5, 35.1]
    assert any(event.get("stage") == "input_inspection" for event in events)


def test_vector_analysis_buffer_uses_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    points_path = tmp_path / "points.geojson"
    points = gpd.GeoDataFrame(
        {"id": [1], "geometry": [shapely_geometry.Point(-77, 40)]},
        crs="EPSG:4326",
    )
    points.to_file(points_path, driver="GeoJSON")

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    result = agent.run("Create a 5-mile buffer around every point.", [str(points_path)])

    assert result["metrics"]["llm_calls"] == 0
    buffered = gpd.read_file(result["outputs"]["dataset_path"])
    assert len(buffered) == 1
    assert buffered.geometry.iloc[0].area > 0


def test_vector_analysis_point_count_by_polygon_uses_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    counties_path = tmp_path / "counties.gpkg"
    hospitals_path = tmp_path / "hospitals.geojson"

    counties = gpd.GeoDataFrame(
        {
            "county_name": ["A", "B", "C"],
            "geometry": [
                shapely_geometry.box(0, 0, 1, 1),
                shapely_geometry.box(1, 0, 2, 1),
                shapely_geometry.box(2, 0, 3, 1),
            ],
        },
        crs="EPSG:4326",
    )
    counties.to_file(counties_path, driver="GPKG")

    hospitals = gpd.GeoDataFrame(
        {
            "hospital_name": ["H1", "H2", "H3"],
            "geometry": [
                shapely_geometry.Point(0.25, 0.25),
                shapely_geometry.Point(0.75, 0.75),
                shapely_geometry.Point(1.25, 0.25),
            ],
        },
        crs="EPSG:4326",
    )
    hospitals.to_file(hospitals_path, driver="GeoJSON")

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    events = []
    result = agent.run(
        "Count the number of hospital points in each county and return hospital_count.",
        [str(counties_path), str(hospitals_path)],
        progress_callback=events.append,
    )

    assert result["metrics"]["llm_calls"] == 0
    output = gpd.read_file(result["outputs"]["dataset_path"])
    assert output["hospital_count"].tolist() == [2, 1, 0]
    assert any(event.get("data", {}).get("operation") == "point_count_by_polygon" for event in events)


def test_vector_analysis_dissolve_uses_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "counties.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "region": ["west", "west", "east"],
            "population": [10, 20, 30],
            "geometry": [
                shapely_geometry.box(0, 0, 1, 1),
                shapely_geometry.box(1, 0, 2, 1),
                shapely_geometry.box(3, 0, 4, 1),
            ],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    result = agent.run("Dissolve these polygons by region.", [str(dataset_path)])

    assert result["metrics"]["llm_calls"] == 0
    output = gpd.read_file(result["outputs"]["dataset_path"])
    assert sorted(output["region"].tolist()) == ["east", "west"]
    assert output.loc[output["region"] == "west", "population"].iloc[0] == 30


def test_vector_analysis_geometry_measurements_use_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "polygons.geojson"
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [shapely_geometry.box(-77.1, 40.0, -77.0, 40.1)]},
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    result = agent.run("Calculate area and centroid fields for this dataset.", [str(dataset_path)])

    assert result["metrics"]["llm_calls"] == 0
    output = gpd.read_file(result["outputs"]["dataset_path"])
    assert {"area_sq_m", "area_sq_km", "centroid_lon", "centroid_lat"}.issubset(output.columns)
    assert output["area_sq_m"].iloc[0] > 0


def test_vector_analysis_attribute_filter_uses_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "counties.geojson"
    gdf = gpd.GeoDataFrame(
        {
            "county_name": ["A", "B", "C"],
            "population": [100, 250, 300],
            "geometry": [shapely_geometry.Point(i, 0) for i in range(3)],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    result = agent.run("Filter where population >= 250.", [str(dataset_path)])

    assert result["metrics"]["llm_calls"] == 0
    output = gpd.read_file(result["outputs"]["dataset_path"])
    assert output["county_name"].tolist() == ["B", "C"]


def test_vector_analysis_nearest_distance_uses_deterministic_fast_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    origins_path = tmp_path / "origins.geojson"
    targets_path = tmp_path / "targets.geojson"
    origins = gpd.GeoDataFrame(
        {"id": [1, 2], "geometry": [shapely_geometry.Point(-77.0, 40.0), shapely_geometry.Point(-77.2, 40.0)]},
        crs="EPSG:4326",
    )
    targets = gpd.GeoDataFrame(
        {"target_id": [10], "geometry": [shapely_geometry.Point(-77.01, 40.0)]},
        crs="EPSG:4326",
    )
    origins.to_file(origins_path, driver="GeoJSON")
    targets.to_file(targets_path, driver="GeoJSON")

    agent = VectorAnalysisAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    result = agent.run("Calculate nearest distance from each origin to the closest target.", [str(origins_path), str(targets_path)])

    assert result["metrics"]["llm_calls"] == 0
    output = gpd.read_file(result["outputs"]["dataset_path"])
    assert "nearest_distance_m" in output.columns
    assert output["nearest_distance_m"].notna().all()


def test_map_projection_explicit_epsg_uses_deterministic_path(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "points.geojson"
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [shapely_geometry.Point(-77, 40)]},
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = MapProjectionAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    result = agent.run("Reproject this dataset to EPSG:3857.", [str(dataset_path)])

    assert result["metrics"]["llm_calls"] == 0
    assert result["outputs"]["dataset_path"].endswith(".gpkg")
    projected = gpd.read_file(result["outputs"]["dataset_path"])
    assert projected.crs.to_epsg() == 3857


def test_map_projection_infers_local_utm_without_api_keys(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "points.geojson"
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [shapely_geometry.Point(-77, 40)]},
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    agent = MapProjectionAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    events = []
    result = agent.run(
        "Choose a suitable UTM projection for distance analysis.",
        [str(dataset_path)],
        progress_callback=events.append,
    )

    assert result["metrics"]["llm_calls"] == 0
    assert result["agent_name"] == "Map Projection Agent"
    projected = gpd.read_file(result["outputs"]["dataset_path"])
    assert projected.crs is not None
    assert projected.crs.to_epsg() in {32618, 32718}
    assert any(event.get("data", {}).get("uses_external_crs_api") is False for event in events)


def test_map_projection_uses_llm_fallback_only_for_ambiguous_choice(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    dataset_path = tmp_path / "points.geojson"
    gdf = gpd.GeoDataFrame(
        {"id": [1], "geometry": [shapely_geometry.Point(-77, 40)]},
        crs="EPSG:4326",
    )
    gdf.to_file(dataset_path, driver="GeoJSON")

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=6),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "crs_code": "EPSG:5070",
                                    "justification": "Best local candidate for the requested thematic workflow.",
                                }
                            )
                        ),
                    )
                ],
            )

    agent = MapProjectionAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    result = agent.run("Choose the best projection for this thematic map.", [str(dataset_path)])

    assert result["metrics"]["llm_calls"] == 1
    assert result["total_input_tokens"] == 10
    assert result["total_output_tokens"] == 6
    projected = gpd.read_file(result["outputs"]["dataset_path"])
    assert projected.crs.to_epsg() == 5070


def test_geospatial_data_retrieval_fallback_selects_census_demography_source():
    agent = GeospatialDataRetrievalAgent(api_key=None)
    handbook_files = agent.collect_handbook_files(source_dir=agent.handbook_dir)
    _, data_source_dict = agent.assemble_handbook_description(handbook_files)

    selection = agent.fallback_source_selection(
        "Retrieve county-level 2021 population for the contiguous United States",
        data_source_dict,
    )

    assert selection["Selected data source"] == "US Census Bureau demography"


def test_geospatial_data_retrieval_run_falls_back_from_unsupported_source(monkeypatch, tmp_path):
    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.output_dir = str(tmp_path)
    output_dir = str(tmp_path).replace("\\", "/")

    monkeypatch.setattr(agent, "generate_output_stem", lambda task: "review_test")
    monkeypatch.setattr(
        agent,
        "select_source",
        lambda select_prompt_str: "{'Explanation': 'broad answer', 'Selected data source': 'Federal statistics portal'}",
    )
    monkeypatch.setattr(
        agent,
        "generate_data_fetching_code",
        lambda download_prompt_str: f"""
```python
import json
import os

def download_data():
    path = os.path.join(r"{output_dir}", "review_test.geojson")
    payload = {{
        "type": "FeatureCollection",
        "features": [
            {{
                "type": "Feature",
                "properties": {{"population": 1}},
                "geometry": {{"type": "Point", "coordinates": [0, 0]}},
            }}
        ],
    }}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)

download_data()
```
""",
    )
    monkeypatch.setattr(
        agent,
        "generate_download_summary",
        lambda data_request, selected_data_source, dataset_size: f"Downloaded from {selected_data_source}.",
    )
    monkeypatch.setattr(agent, "try_direct_census_county_population_download", lambda *args, **kwargs: None)

    result = agent.run("Retrieve county-level 2021 population for the contiguous United States")

    assert result["outputs"]["dataset_path"].endswith("review_test.gpkg")
    assert result["outputs"]["dataset_size"]["feature_count"] == 1
    assert result["outputs"]["text"] == "Downloaded from US Census Bureau demography."


def test_geospatial_data_retrieval_defaults_vectors_to_geopackage():
    agent = GeospatialDataRetrievalAgent(api_key=None)

    assert agent.preferred_vector_output("Download contiguous US county boundaries")[0] == ".gpkg"
    assert (
        agent.preferred_vector_output(
            "Download counties. A previous example used geojson, but no output format is required."
        )[0]
        == ".gpkg"
    )


def test_geospatial_data_retrieval_honors_explicit_geojson_request():
    agent = GeospatialDataRetrievalAgent(api_key=None)

    assert agent.preferred_vector_output("Download county boundaries and return as GeoJSON")[0] == ".geojson"
    assert agent.preferred_vector_output("Please save the output in GeoJSON format")[0] == ".geojson"


def test_geospatial_data_retrieval_geopackage_request_overrides_geojson_mentions():
    agent = GeospatialDataRetrievalAgent(api_key=None)

    assert (
        agent.preferred_vector_output(
            "Download counties as a GeoPackage. Do not use the older GeoJSON example."
        )[0]
        == ".gpkg"
    )


def test_geospatial_data_retrieval_surfaces_auth_errors_in_source_selection(monkeypatch):
    agent = GeospatialDataRetrievalAgent(api_key=None)

    def raise_auth_error(*args, **kwargs):
        raise RuntimeError("401 Unauthorized: incorrect API key provided")

    monkeypatch.setattr(agent, "_create_chat_completion", raise_auth_error)

    with pytest.raises(ValueError, match="OpenAI authentication failed"):
        agent.select_source("Select a data source")


def test_geospatial_data_retrieval_surfaces_auth_errors_in_code_generation(monkeypatch):
    agent = GeospatialDataRetrievalAgent(api_key=None)

    def raise_auth_error(*args, **kwargs):
        raise RuntimeError("Authentication failed because the API key is invalid")

    monkeypatch.setattr(agent, "_create_chat_completion", raise_auth_error)

    with pytest.raises(ValueError, match="OpenAI authentication failed"):
        agent.generate_data_fetching_code("Generate code")


def test_geospatial_data_retrieval_uses_request_source_credentials():
    agent = GeospatialDataRetrievalAgent(api_key=None)
    agent.set_request_parameters(
        {
            "source_credentials": {
                "EPA_AQS": {
                    "email": "user@example.com",
                    "key": "aqs-test-key",
                }
            }
        }
    )

    handbook = agent.collect_a_handbook(
        source_ID="EPA_AQS",
        source_dir=agent.handbook_dir,
    )

    assert "user@example.com" in handbook
    assert "aqs-test-key" in handbook
    assert "{EPA_AQS_email}" not in handbook
    assert "{EPA_AQS_key}" not in handbook


def test_pasda_agent_directly_matches_county_boundary_requests(monkeypatch):
    agent = PasdaAgent(api_key=None)
    calls = []
    events = []

    def fake_download_data(service_name, layer_id, user_query):
        calls.append((service_name, layer_id, user_query))
        agent.downloaded.append("Data/pasda/pa_counties.geojson")
        agent.feature_counts["Data/pasda/pa_counties.geojson"] = 67
        agent.summary = "Pennsylvania county boundaries."
        return '{"status": "success", "feature_count": 67}'

    monkeypatch.setattr(agent, "download_data", fake_download_data)

    matched = agent._try_direct_pasda_download(
        "Download PA county boundaries",
        progress_callback=events.append,
    )

    assert matched is True
    assert calls == [("PennDOT", 7, "Download PA county boundaries")]
    assert events[0]["stage"] == "source_selection"
    assert events[-1]["stage"] == "download_complete"


def test_pasda_agent_directly_matches_hospital_requests(monkeypatch):
    agent = PasdaAgent(api_key=None)
    calls = []

    def fake_download_data(service_name, layer_id, user_query):
        calls.append((service_name, layer_id, user_query))
        agent.downloaded.append("Data/pasda/hospitals.geojson")
        agent.feature_counts["Data/pasda/hospitals.geojson"] = 226
        agent.summary = "Pennsylvania hospitals."
        return '{"status": "success", "feature_count": 226}'

    monkeypatch.setattr(agent, "download_data", fake_download_data)

    matched = agent._try_direct_pasda_download("Download Pennsylvania hospitals from PASDA")

    assert matched is True
    assert calls == [("pasda/DepHealth", 6, "Download Pennsylvania hospitals from PASDA")]


def test_pasda_agent_caches_repeated_service_metadata(monkeypatch):
    agent = PasdaAgent(api_key=None)
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "layers": [
                    {"id": 6, "name": "Hospitals"},
                ],
                "description": "Department of Health layers",
            }

    def fake_get(url, timeout):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("gas_server.agents.pasda_agent.requests.get", fake_get)

    first = json.loads(agent.get_service_metadata("pasda/DepHealth"))
    second = json.loads(agent.get_service_metadata("PASDA/DepHealth "))

    assert len(calls) == 1
    assert first["layers"][0]["name"] == "Hospitals"
    assert second["cached"] is True

