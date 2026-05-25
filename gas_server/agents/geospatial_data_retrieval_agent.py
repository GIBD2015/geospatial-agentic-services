import ast
import configparser
import datetime
from glob import glob
import logging
import os
import random
import re
import sys
import time
import traceback
import geopandas as gpd
import pandas as pd
import requests
import tomli
from dotenv import load_dotenv
from openai import OpenAI
from gas_server.core.file_naming import build_output_filename
from gas_server.core.geo_agent import GeoAgent
from gas_server.core.llm_client import build_llm_client, format_service_name
from gas_server.core.config import DATA_DIR, PROJECT_ROOT, ensure_runtime_dirs

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Load environment variables from .env file
load_dotenv()
ensure_runtime_dirs()

BASE_DIR = str(PROJECT_ROOT)

class GeospatialDataRetrievalAgent(GeoAgent):
    agent_id = "geospatial_data_retrieval_agent"
    agent_name = "Geospatial Data Retrieval Agent"
    agent_version = "1.0.0"
    agent_description = "Selects a supported source, generates download code, and returns geospatial data."
    DEFAULT_OUTPUT_DIR = str(DATA_DIR / "geospatial_data_retrieval_agent")
    DOMAIN_LIBRARIES = [
        "geopandas",
        "tomli",
        "openai",
        "python-dotenv",
        "rasterio",
    ]
    VECTOR_EXTENSIONS = {".geojson", ".gpkg", ".shp", ".gdb", ".kml", ".gml", ".json"}
    RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".vrt"}
    TABLE_EXTENSIONS = {".csv"}
    SINGLE_FILE_EXTENSIONS = {".geojson", ".gml", ".gpkg", ".img", ".json", ".kml", ".tif", ".tiff", ".vrt"}
    SIDECAR_EXTENSIONS = {
        ".aux",
        ".cpg",
        ".dbf",
        ".fix",
        ".ovr",
        ".prj",
        ".qix",
        ".sbn",
        ".sbx",
        ".shx",
        ".xml",
    }

    def __init__(self, api_key=None, model: str | None = None):
        super().__init__(api_key=api_key, model=model or "gpt-4o", output_dir=self.DEFAULT_OUTPUT_DIR)
        self.service_name = format_service_name(self.agent_name)
        self.handbook_dir = os.path.join(BASE_DIR, "gas_server", "agents", "geospatial_data_retrieval_handbooks")
        self.client = build_llm_client(
            service_name=self.service_name,
            openai_api_key=self.api_key,
        )

    def run(self, query, input_dataset_paths=None, progress_callback=None):
        
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        overall_start_time = time.perf_counter()

        sub_requests = self.decompose_request(query, progress_callback=progress_callback)

        if len(sub_requests) <= 1:
            single_query = sub_requests[0] if sub_requests else query
            return self._execute_one_request(
                single_query,
                input_dataset_paths=input_dataset_paths,
                progress_callback=progress_callback,
                start_time=overall_start_time,
            )

        self._emit_progress(
            progress_callback,
            stage="planning",
            message=(
                f"I detected {len(sub_requests)} datasets in your request. "
                "I will download each one as a separate sub-task and return all artifacts."
            ),
            data={"sub_request_count": len(sub_requests), "sub_requests": sub_requests},
        )

        sub_responses = []
        for idx, sub_query in enumerate(sub_requests, start=1):
            self._emit_progress(
                progress_callback,
                stage="planning",
                message=f"Starting sub-task {idx} of {len(sub_requests)}: {sub_query}",
                data={
                    "sub_request_index": idx,
                    "sub_request_total": len(sub_requests),
                    "sub_request": sub_query,
                },
            )
            sub_start = time.perf_counter()
            try:
                sub_response = self._execute_one_request(
                    sub_query,
                    input_dataset_paths=input_dataset_paths,
                    progress_callback=progress_callback,
                    start_time=sub_start,
                )
            except Exception as exc:
                logging.exception(f"Sub-request {idx} failed unexpectedly.")
                sub_response = {
                    "outputs": {
                        "text": f"Sub-request failed: {exc}",
                        "dataset_path": None,
                        "dataset_paths": [],
                        "dataset_size": self.empty_dataset_size(),
                    },
                    "script": "",
                    "duration": f"{time.perf_counter() - sub_start:.2f}s",
                }
            sub_responses.append(sub_response)

        return self._build_multi_response(
            start_time=overall_start_time,
            text_input=query,
            input_dataset_path=(input_dataset_paths[0] if input_dataset_paths else None),
            sub_requests=sub_requests,
            sub_responses=sub_responses,
            progress_callback=progress_callback,
        )

    def _execute_one_request(self, query, input_dataset_paths=None, progress_callback=None, start_time=None):
        dataset_paths = self.normalize_dataset_paths(input_dataset_paths)
        dataset_path = dataset_paths[0] if dataset_paths else None
        data_request = query
        start_time = start_time if start_time is not None else time.perf_counter()
        selected_data_source = "Unknown"
        handbook_str = ""
        code = ""
        downloaded_files = []
        output_dataset_path = None
        dataset_size = self.empty_dataset_size()
        output_stem = self.generate_output_stem(data_request)

        try:
            self.prepare_output_dir()
            self._emit_progress(
                progress_callback,
                stage="start",
                message="I will identify the most suitable supported data source, then generate source-specific download logic.",
                data={"has_input_dataset": dataset_path is not None},
            )

            logging.info("Starting data retrieval process, AI is selecting data source...")
            self._emit_progress(
                progress_callback,
                stage="source_selection",
                message="I am comparing the request against the supported data-source handbooks to choose where the dataset should come from.",
            )
            handbook_files = self.collect_handbook_files(source_dir=self.handbook_dir)
            _, data_source_dict = self.assemble_handbook_description(handbook_files)
            source_select_prompt_str = self.create_select_prompt(task=data_request)
            select_source_reply = self.select_source(select_prompt_str=source_select_prompt_str)
            if select_source_reply:
                self._emit_progress(
                    progress_callback,
                    stage="source_selection",
                    message="The source-selection model replied successfully, so I will parse its recommendation.",
                )
            select_source = self.parse_source_selection(select_source_reply)
            if not select_source:
                select_source = self.fallback_source_selection(data_request, data_source_dict)
                self._emit_progress(
                    progress_callback,
                    stage="fallback_start",
                    message="The model did not return a usable source selection, so I used deterministic source-matching rules from the supported handbooks.",
                    data={"selected_data_source": select_source.get("Selected data source")},
                )

            logging.info(f"Source selection: {select_source}")
            source_explanation = select_source.get("Explanation")
            selected_data_source = select_source.get("Selected data source", "Unknown")
            selected_data_source = self.normalize_selected_data_source(
                selected_data_source,
                data_request,
                data_source_dict,
            )
            logging.info(f"LLM selected data source: {selected_data_source}")
            self._emit_progress(
                progress_callback,
                stage="source_validation",
                message=self.format_source_selection_message(selected_data_source, source_explanation),
                data={
                    "selected_data_source": selected_data_source,
                    "source_selection": select_source,
                },
            )

            selected_data_source_ID = data_source_dict.get(selected_data_source, {}).get("ID", "Unknown")
            if selected_data_source_ID == "Unknown":
                fallback_source = self.fallback_source_selection(data_request, data_source_dict)
                fallback_data_source = fallback_source.get("Selected data source", "Unknown")
                fallback_data_source = self.normalize_selected_data_source(
                    fallback_data_source,
                    data_request,
                    data_source_dict,
                )
                fallback_data_source_ID = data_source_dict.get(fallback_data_source, {}).get("ID", "Unknown")
                if fallback_data_source_ID != "Unknown":
                    self._emit_progress(
                        progress_callback,
                        stage="fallback_start",
                        message="The selected source did not match a supported handbook, so I used deterministic source-matching rules from the supported handbooks.",
                        data={
                            "original_selected_data_source": selected_data_source,
                            "selected_data_source": fallback_data_source,
                        },
                    )
                    selected_data_source = fallback_data_source
                    selected_data_source_ID = fallback_data_source_ID

            if selected_data_source_ID == "Unknown":
                self._emit_progress(
                    progress_callback,
                    stage="warning",
                    message="I could not match the selected source to a supported handbook, so I will return a clear unsupported-source result.",
                    data={"selected_data_source": selected_data_source},
                )
                return self.build_response(
                    start_time=start_time,
                    text_input=data_request,
                    input_dataset_path=dataset_path,
                    output_text=f"No supported handbook was found for the selected data source '{selected_data_source}'.",
                    output_dataset_path=None,
                    dataset_size=dataset_size,
                    script=code,
                    downloaded_files=downloaded_files,
                    progress_callback=progress_callback,
                )

            handbook_str = self.collect_a_handbook(
                source_ID=selected_data_source_ID,
                source_dir=self.handbook_dir,
            )
            if handbook_str is None:
                logging.warning(
                    f"Could not load handbook for data source '{selected_data_source}'. No handbook file found or error in loading."
                )
                handbook_str = ""
            else:
                logging.info(f"Successfully loaded handbook for data source '{selected_data_source}'.")
                self._emit_progress(
                    progress_callback,
                    stage="source_validation",
                    message=f"I loaded the {selected_data_source} handbook and will use its source-specific download rules.",
                    data={"selected_data_source": selected_data_source},
                )

            try:
                direct_download = self.try_direct_census_county_population_download(
                    data_request=data_request,
                    selected_data_source=selected_data_source,
                    output_stem=output_stem,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                logging.warning(f"Deterministic Census county population workflow failed; falling back to generated code: {exc}")
                self._emit_progress(
                    progress_callback,
                    stage="warning",
                    message=(
                        "The deterministic Census county population workflow failed, so I will fall back to "
                        "the handbook-guided generated-code workflow."
                    ),
                    data={"error": str(exc)},
                )
                direct_download = None
            if direct_download:
                code = direct_download["script"]
                downloaded_files = self.apply_request_postprocessing(
                    [direct_download["path"]],
                    data_request,
                    output_stem,
                    selected_data_source,
                    progress_callback=progress_callback,
                )
                output_dataset_path = self.pick_primary_output(downloaded_files, output_stem)
                dataset_size = self.describe_dataset(output_dataset_path)
                self._emit_progress(
                    progress_callback,
                    stage="download_complete",
                    message=self.format_dataset_created_message(dataset_size, len(downloaded_files)),
                    data={"file_count": len(downloaded_files), "dataset_size": dataset_size},
                )
                output_text = self.generate_download_summary(
                    data_request=data_request,
                    selected_data_source=selected_data_source,
                    dataset_size=dataset_size,
                )
                return self.build_response(
                    start_time=start_time,
                    text_input=data_request,
                    input_dataset_path=dataset_path,
                    output_text=output_text,
                    output_dataset_path=output_dataset_path,
                    dataset_size=dataset_size,
                    script=code,
                    downloaded_files=downloaded_files,
                    progress_callback=progress_callback,
                )

            self._emit_progress(
                progress_callback,
                stage="llm_generation",
                message="I am generating a complete Python download program for the selected source and requested dataset.",
            )
            download_prompt_str = self.create_download_prompt(
                data_request,
                selected_data_source,
                handbook_str,
                output_stem,
            )
            data_fetching_code_str = self.generate_data_fetching_code(download_prompt_str=download_prompt_str)
            if not data_fetching_code_str:
                raise ValueError("The code-generation model did not return a response.")
            self._emit_progress(
                progress_callback,
                stage="llm_generation",
                message="The code-generation model replied successfully, so I will extract and run the Python program.",
            )

            code = self.extract_code_from_str(data_fetching_code_str)
            if not code:
                raise ValueError("No Python code block was found in the generated response.")

            before = self.list_output_files()
            code = self.normalize_generated_code(code)
            self._emit_progress(
                progress_callback,
                stage="download_start",
                message="I generated the download program and will execute it with automatic repair attempts if the source response or code fails.",
                data={"max_attempts": 10},
            )
            code = self.execute_complete_program(
                code=code,
                try_cnt=10,
                task=data_request,
                model_name=self.model,
                handbook_str=handbook_str,
                output_stem=output_stem,
                progress_callback=progress_callback,
            )
            code = self.normalize_generated_code(code)
            after = self.list_output_files()

            downloaded_files = self.discover_output_files(before, after, output_stem)
            if not downloaded_files:
                self._emit_progress(
                    progress_callback,
                    stage="error",
                    message="The download code finished, but I did not find any new output files to package.",
                )
                return self.build_response(
                    start_time=start_time,
                    text_input=data_request,
                    input_dataset_path=dataset_path,
                    output_text="No files were downloaded.",
                    output_dataset_path=None,
                    dataset_size=dataset_size,
                    script=code,
                    downloaded_files=[],
                    progress_callback=progress_callback,
                )

            downloaded_files = [
                self.ensure_preferred_vector_output(
                    path,
                    output_stem,
                    data_request,
                    progress_callback=progress_callback,
                )
                for path in downloaded_files
            ]
            downloaded_files = [path for path in downloaded_files if path]
            downloaded_files = self.apply_request_postprocessing(
                downloaded_files,
                data_request,
                output_stem,
                selected_data_source,
                progress_callback=progress_callback,
            )
            output_dataset_path = self.pick_primary_output(downloaded_files, output_stem)
            dataset_size = self.describe_dataset(output_dataset_path)
            self._emit_progress(
                progress_callback,
                stage="download_complete",
                message=self.format_dataset_created_message(dataset_size, len(downloaded_files)),
                data={"file_count": len(downloaded_files), "dataset_size": dataset_size},
            )

            self._emit_progress(
                progress_callback,
                stage="response_preparation",
                message="I am writing the final summary to explain what was downloaded and what the dataset contains.",
            )
            output_text = self.generate_download_summary(
                data_request=data_request,
                selected_data_source=selected_data_source,
                dataset_size=dataset_size,
            )
            return self.build_response(
                start_time=start_time,
                text_input=data_request,
                input_dataset_path=dataset_path,
                output_text=output_text,
                output_dataset_path=output_dataset_path,
                dataset_size=dataset_size,
                script=code,
                downloaded_files=downloaded_files,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            logging.exception("Data retrieval failed.")
            traceback_str = traceback.format_exc()
            self._emit_progress(
                progress_callback,
                stage="error",
                message=(
                    "The data retrieval workflow hit an error, so I will package any partial "
                    f"outputs and return diagnostics: {exc}"
                ),
                data={"error_type": type(exc).__name__, "traceback": traceback_str},
            )
            downloaded_files = self.discover_output_files(set(), self.list_output_files(), output_stem)
            output_dataset_path = self.pick_primary_output(downloaded_files, output_stem)
            if output_dataset_path:
                dataset_size = self.describe_dataset(output_dataset_path)

            return self.build_response(
                start_time=start_time,
                text_input=data_request,
                input_dataset_path=dataset_path,
                output_text=f"Data retrieval failed: {exc}\n\nTraceback:\n{traceback_str}",
                output_dataset_path=output_dataset_path,
                dataset_size=dataset_size,
                script=code,
                downloaded_files=downloaded_files,
                progress_callback=progress_callback,
            )

    def LLM_Find(self, data_request, dataset_path=None):
        input_dataset_paths = [dataset_path] if dataset_path else None
        return self.run(data_request, input_dataset_paths=input_dataset_paths)
        


##=========================MAIN FUNCTIONS====================================================
    def create_select_prompt(self, task):
        select_role = r'''A professional Python programmer in geographic information science (GIScience). You have worked on GIScience for more than 20 years and know every detail and pitfall when collecting data and coding. You know which websites you can get suitable spatial data and know the methods or tricks to download data, such as OpenStreetMap, Census Bureau, or various APIs. You are also experienced in processing the downloaded data, including saving them in suitable formats, map projections, and creating detailed and useful meta-data.
        '''
        select_task_prefix = """select a suitable data source from the given list to download the requested geo-spatial data for this task"""
        selection_reply_example = """
        {'Explanation': "According to the use requests of US state administrative boundary from OpenStreetMap, I should download data from OpenStreetMap.", "Selected data source": 'OpenStreetMap'}
        """
        select_requirements = [
            "Return the exact name of the data source as the given names.",
            "If a data source family is given in the task, e.g., Census Bureau, choose the exact supported source name from the list that matches the requested data type.",
            "If you need to download the administrative boundary of a place without mentioning the data sources, you can get data from OpenStreetMap.",
            "If you need to download the US Census tract and block group boundaries, download them from Census Bureau.",
            "Follow the given JSON format.",
            "If you cannot find a suitable data source in the given sources, return a data source you think is most appropriate.",
            "DO NOT make fake data source. If you cannot find any suitable data source, return 'Unknown' as for the 'Selected data source' key in the reply JSON format. DO NOT use ```json and ```",
        ]


        select_requirement_str = '\n'.join([f"{idx + 1}. {line}" for idx, line in enumerate(select_requirements)])
        handbook_files = self.collect_handbook_files(source_dir=self.handbook_dir) # NEW CHANGE
        descriptions_str, data_source_dict = self.assemble_handbook_description(handbook_files) #NEW CHANGE
        prompt = f"Your role: {select_role} \n" + \
                 f"Your mission: {select_task_prefix}: " + f"{task}\n\n" + \
                 f"Requirements: \n{select_requirement_str} \n\n" + \
                 f"Data sources:{descriptions_str} \n" + \
                 f'Your reply example: {selection_reply_example}'
        return prompt
    
    
    
    def create_download_prompt(self, task, selected_data_source, handbook_str, output_stem):
        # select_requirement_str = '\n'.join([f"{idx + 1}. {line}" for idx, line in enumerate(constants.select_requirements)])
        current_datetime = datetime.datetime.now()  # NEW CHANGE
        formatted_datetime = current_datetime.strftime("%Y-%m-%d %H:%M") # NEW CHANGE
        
        download_role = r'''A professional Python programmer in geographic information science (GIScience). You have worked on GIScience for more than 20 years and know every detail and pitfall when collecting data and coding. You know which websites you can get suitable spatial data and know the methods or tricks to download data, such as OpenStreetMap, Census Bureau, or various APIs. You are also experienced in processing the downloaded data, including saving them in suitable formats, map projections, and creating detailed and useful meta-data. When downloading geo-spatial data, the technical handbook for a particular data source is provided; you can follow it, and write Python code carefully to download the data. 
        '''

        download_task_prefix = r'download geo-spatial data from the given data source for this task'

        download_reply_example = f"""
        ```python
        import geopandas as gpd
        import osmnx as ox
        def download_data():
            gdf = ox.geocode_to_gdf("Pennsylvania, USA")
            gdf.to_file(r"{self.output_dir}/state_data_123456.gpkg", driver="GPKG")
        download_data()
        ```
        """

        """
        1. Think step by step.
        2. If you need to download the administrative boundary of a place and without mentioning the data sources, you can get data from OSM using OSM package by `ox.geocode_to_gdf(query, which_result=None, by_osmid=False, buffer_dist=None)`. This method is fast. 
        3.If the place of boundaries request is in the USA, you can download boundaries from Census Bureau, which is official and better than OSM. An example link is: https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_{year}_{extend}_{level}_500k.zip. You can change the year and administrative level (state/county) in link accordingly. "year" is 4-digit. 'extend' can be 'us' or 2-digit state FIPS; when 'extend' = 'us', 'level' can be 'state' and 'county' only, and the downloaded data is national. When 'extend' is 2-digit state FIPS, 'level' can be 'tract' and 'bg' only. 'bg' refers to block groups. E.g., do not set 'extend' to 2-digit FIPS code when download county boundaries for a state. If you need to download counties boundaries, 'extend' must be 'us'.
        4. If the user does not mention the saving file format, save vector geospatial data in GeoPackage format. If the user explicitly requests GeoJSON, save vector geospatial data in GeoJSON format.
        5. You need to create Python code to download and save the data. Another program will execute your code directly.
        6. You can use various technical ways to download the data, such as Overpass QL, Overpass API, OSMnx Python package, Census file downloading link, or Census Python packages.
        7. Put your reply into a Python code block, Explanation or conversation can be Python comments at the begining of the code block(enclosed by ```python and ```).
        8. The download code is only in a function named 'download_data()'. The last line is to execute this function.
        9. When downloading OSM data, no need to use 'building' tags if it is not asked for.
        10. If using GeoPandas to load a zipped ESRI shapefile from a URL, the correct method is "gpd.read_file(URL)". DO NOT download and unzip the file.
        11. Note Python package 'pandas' has no attribute or method of 'StringIO'.,
        12. If a data source is given in the task, e.g., OSM or Census Bureau, you need to download data from that data source.

        """
        

        filename_rules = [
            f"Save the final dataset in exactly one file inside this directory: {self.output_dir}",
            f"Use this exact output basename for the final dataset file: {output_stem}",
            "The output filename must contain at most two words before the 6-digit suffix. Do not add extra words before the suffix.",
            "The final dataset path must be stored in the required output directory, not elsewhere.",
            "Use a single self-contained output artifact whenever possible.",
            "For vector geospatial data, the default final output must be a single .gpkg GeoPackage file. If the user explicitly requests GeoJSON, save a single .geojson file instead. If the user explicitly requests CSV, save a single .csv file instead; include useful identifier and attribute fields, and include geometry as WKT only when geometry is needed.",
            "For raster data, keep the appropriate raster format.",
            "After downloading, filter or transform the dataset so the saved final file matches every user-requested geography, time period, attribute, and format constraint.",
            "If the source only provides a broader dataset, post-process it before saving. For example, contiguous or conterminous US county requests must exclude Alaska, Hawaii, Puerto Rico, and other territories.",
            "Before saving, verify that the final dataset is not just the raw source response when the user asked for a subset.",
        ]
        filename_rule_str = "\n".join([f"{idx + 1}. {line}" for idx, line in enumerate(filename_rules)])

        prompt =    f"Your role: {download_role} \n" + \
                    f"Your mission: {download_task_prefix}: " + f"{task}" + "\n\n" + \
                    f"Current date-time: {formatted_datetime} \n\n" + \
                    f"Data source:{selected_data_source} \n" + \
                    f"Output requirements:\n{filename_rule_str}\n\n" + \
                    f'Your reply example: {download_reply_example}\n' + \
                    f"Technical handbook: \n{handbook_str}"

        return prompt
    
    
    def get_debug_prompt(self, exception, code, task, handbook_str, output_stem):
        
        debug_role = r'''A professional geo-information scientist and programmer who is good at Python. You have worked on Geographic information science for over 20 years and know every detail and pitfall when processing spatial data and coding. You have significant experience in code debugging. You like to find out debugs and fix code. Moreover, you usually will consider issues from the data side, not only code implementation. Your current job is to debug the code for map generation.
        '''
        debug_task_prefix = r"You need to correct a program's code based on the given error information and then return the complete corrected code."

        debug_requirement = [
            'Think step by step. Elaborate your reasons for revision before returning the code. E.g., Explaination for the revision: xxxx \n The reivsed code is: ```pyhon xxxx  ```.',
            'Correct the code. Revise the buggy parts, but need to keep program structure, i.e., the function name, its arguments, and returns.',
            'You must return the entire corrected program in only one Python code block(enclosed by ```python and ```); DO NOT return the revised part only.',
            'If using GeoPandas to load a zipped ESRI shapefile from a URL, the correct method is "gpd.read_file(URL)". DO NOT download and unzip the file.',
            'Make necessary revisions only. Do not change the structure of the given code or program; keep all functions.',
            "Note module 'pandas' has no attribute or method of 'StringIO'",
            "When doing spatial analysis, convert the involved spatial layers into the same map projection, if they are not in the same projection.",
            "DO NOT reproject or set spatial data(e.g., GeoPandas Dataframe) if only one layer involved.",
            "Map projection conversion is only conducted for spatial data layers such as GeoDataFrame. DataFrame loaded from a CSV file does not have map projection information.",
            "If join DataFrame and GeoDataFrame, using common columns, DO NOT convert DataFrame to GeoDataFrame.",
            "Remember the variable, column, and file names used in ancestor functions when using them, such as joining tables or calculating.",
            "You can use OSMnx Python package to download a city, neighborhood, borough, county, state, or country. The code is: `gdf = ox.geocode_to_gdf(place)`. The Overpass API `area['name'='target_placename']` might return empty results.",
            'If the error is a ModuleNotFoundError / ImportError because a required Python package is not installed, install it programmatically at the very top of the revised code BEFORE the failing import. Do not install packages for "cannot import name" errors; those usually mean the import path is wrong and should be corrected instead. Use this exact pattern only for missing packages (do NOT use shell commands like "!pip" or "pip install", because the code is run via exec() and shell syntax will fail):\n'
            '    import subprocess, sys\n'
            '    subprocess.check_call([sys.executable, "-m", "pip", "install", "<pypi_package_name>"])\n'
            'Map the import name to the correct PyPI name when they differ (e.g., cv2 -> opencv-python, sklearn -> scikit-learn, skimage -> scikit-image, PIL -> Pillow, bs4 -> beautifulsoup4, yaml -> PyYAML, dotenv -> python-dotenv, osgeo -> GDAL). After the install call, keep the original import statement so the module is available for the rest of the program.',
            "If the error says `cannot import name 'MultiPolygon' from 'shapely.ops'`, fix the import to `from shapely.geometry import MultiPolygon`; similarly import Point, LineString, Polygon, and shape from shapely.geometry, not shapely.ops.",
            "If using GeoPandas for spatial analysis, when doing overlay analysis, carefully think about use Geopandas.GeoSeries.intersects() or geopandas.sjoin(). ",
            "Geopandas.GeoSeries.intersects(other, align=True) returns a Series of dtype('bool') with value True for each aligned geometry that intersects others. other:GeoSeries or geometric object. ",
            "If using GeoPandas for spatial joining, the arguements are: geopandas.sjoin(left_df, right_df, how='inner', predicate='intersects', lsuffix='left', rsuffix='right', **kwargs), how: the type of join, default ‘inner’, means use intersection of keys from both dfs while retain only left_df geometry column. If 'how' is 'left': use keys from left_df; retain only left_df geometry column, and similarly when 'how' is 'right'. ",
            "Note geopandas.sjoin() returns all joined pairs, i.e., the return could be one-to-many. E.g., the intersection result of a polygon with two points inside it contains two rows; in each row, the polygon attribute is the same. If you need of extract the polygons intersecting with the points, please remember to remove the duplicated rows in the results.",
            "FIPS or GEOID columns may be str type with leading zeros (digits: state: 2, county: 5, tract: 11, block group: 12), or integer type without leading zeros. Thus, when joining using they, you can convert the integer colum to str type with leading zeros to ensure the success.",
            "If you use `ox.geocode_to_gdf(place_name)` to a place's boundary and get a type error of 'Nominatim could not geocode query place_name to a geometry of type (Multi)Polygon'; it is caused by a place name not in OpenStreetMap; you need to change the place name to address this error. E.g., using 'Penn State University' instead of 'Penn State University, State College, PA'.",
            "Carefully check whether the Overpass query is using `relation({osm_id}); map_to_area->.rel;` to get the filtering area. `area(osm_id)->.rel` is wrong",
            "NEVER using `area(osm_id)->.rel` to filter data in Overpass queries.",
            "You must replace 'area({osm_id})->.rel;' by 'relation({osm_id}); map_to_area->.rel;'. Only the latter is correct!",
            "If Overpass returns 406 Not Acceptable, retry once with `https://overpass.kumi.systems/api/interpreter`; if it still fails, revise the Overpass QL to be narrower or syntactically correct and include the response text in the raised error.",
            "If Overpass returns 504 timeout for a statewide POI request such as hospitals, narrow the query to exact tags like `amenity=hospital` and `healthcare=hospital`, use `out center tags;` or `out body center;`, and avoid `out geom;` unless full polygon geometry is required.",
            f"Keep the final saved output inside this directory only: {self.output_dir}",
            f"Use the exact output basename '{output_stem}' with the appropriate extension. Do not create extra final dataset files.",
            "For vector geospatial data, the default final saved output must be a single .gpkg GeoPackage file. If the user explicitly requests GeoJSON, save a single .geojson file instead. If the user explicitly requests CSV, save a single .csv file instead; include useful identifier and attribute fields, and include geometry as WKT only when geometry is needed.",
        ]
                
        etype, exc, tb = sys.exc_info()
        exttb = traceback.extract_tb(tb)  # Do not quite understand this part.
        # https://stackoverflow.com/questions/39625465/how-do-i-retain-source-lines-in-tracebacks-when-running-dynamically-compiled-cod/39626362#39626362

      
        # print("code in get_debug_prompt:", code)
        ## Fill the missing data:
        exttb2 = [(fn, lnnr, funcname,
                   (code.splitlines()[lnnr - 1] if fn == 'Complete program'
                    else line))
                  for fn, lnnr, funcname, line in exttb]

        # Print:
        error_info_str = 'Traceback (most recent call last):\n'
        for line in traceback.format_list(exttb2[1:]):
            error_info_str += line
        for line in traceback.format_exception_only(etype, exc):
            error_info_str += line

        # print(f"Error_info_str: \n{error_info_str}")

        debug_requirement_str = '\n'.join([f"{idx + 1}. {line}" for idx, line in enumerate(debug_requirement)])

        debug_prompt = f"Your role: {debug_role} \n" + \
                          f"Your task: correct the code of a program according to the error information, then return the corrected and completed program. \n\n" + \
                          f"Requirement: \n {debug_requirement_str} \n\n" + \
                          f"The given code is used for this task: {task} \n\n" + \
                          f"The technical guidelines for the code: \n {handbook_str} \n\n" + \
                          f"The error information for the code is: \n{str(error_info_str)} \n\n" + \
                          f"The code is: \n{code}"
        return debug_prompt


    ##=========================SUPPORTING FUNCTIONS====================================================

    def decompose_request(self, query, progress_callback=None):
        """Split a natural-language request into one or more independent
        single-dataset download sub-requests.

        Returns a list of self-contained request strings, each describing
        exactly one dataset to download. Single-dataset requests return a
        one-item list (the original query, lightly normalized).
        """
        text = (query or "").strip()
        if not text:
            return [query]

        self._emit_progress(
            progress_callback,
            stage="planning",
            message="I am checking whether the request asks for one dataset or several.",
        )

        decomposition_prompt = (
            "You are an expert geospatial data analyst. Read the user's data-retrieval "
            "request below and decide whether it asks for ONE dataset or SEVERAL "
            "independent datasets (for example: county boundaries + earthquake events, "
            "or DEM + land cover for the same area). "
            "If the request is for a single dataset, return a JSON object: "
            "{\"sub_requests\": [\"<the original request, lightly cleaned>\"]}. "
            "If the request contains multiple datasets, return one self-contained "
            "sub-request string per dataset, each fully describing the data source "
            "(if specified), geography, time period, attributes, and format. Do not "
            "split a single dataset into multiple steps (e.g., 'download X and then "
            "filter X' is ONE sub-request). Do not add commentary; return only the "
            "JSON object. DO NOT use ```json fences.\n\n"
            f"User request:\n{text}\n\n"
            "Reply example for one dataset:\n"
            "{\"sub_requests\": [\"Download US county boundaries from the US Census "
            "Bureau as a GeoPackage.\"]}\n\n"
            "Reply example for two datasets:\n"
            "{\"sub_requests\": ["
            "\"Download 2020 US county boundaries from the US Census Bureau as a GeoPackage.\", "
            "\"Download recent USGS earthquake events with magnitude 2.5+ from the last 30 days as GeoJSON.\""
            "]}"
        )

        try:
            response = self._create_chat_completion(
                messages=[{"role": "system", "content": decomposition_prompt}],
            )
            reply = response.choices[0].message.content
        except Exception as exc:
            if self._is_auth_error(exc):
                self._raise_auth_error(exc)
            logging.warning(f"Request decomposition failed; treating as a single request: {exc}")
            return [text]

        sub_requests = self._parse_decomposition_reply(reply)
        if not sub_requests:
            return [text]

        sub_requests = [s.strip() for s in sub_requests if isinstance(s, str) and s.strip()]
        if not sub_requests:
            return [text]

        self._emit_progress(
            progress_callback,
            stage="planning",
            message=(
                f"The request was decomposed into {len(sub_requests)} sub-request(s)."
            ),
            data={"sub_request_count": len(sub_requests), "sub_requests": sub_requests},
        )
        return sub_requests

    def _parse_decomposition_reply(self, reply_content):
        if not reply_content or not isinstance(reply_content, str):
            return []
        cleaned = reply_content.strip()
        cleaned = re.sub(r"^```(?:json|python)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            import json
            parsed = json.loads(cleaned)
        except Exception:
            try:
                parsed = ast.literal_eval(cleaned)
            except Exception as exc:
                logging.warning(f"Could not parse decomposition reply: {exc}")
                return []
        if isinstance(parsed, dict):
            value = parsed.get("sub_requests")
            if isinstance(value, list):
                return value
        if isinstance(parsed, list):
            return parsed
        return []

    def _build_multi_response(
        self,
        start_time,
        text_input,
        input_dataset_path,
        sub_requests,
        sub_responses,
        progress_callback=None,
    ):
        # Paths are placed in exactly ONE field (outputs.dataset_paths) so the
        # server's transformer relocates each file once. Per-sub-task records
        # carry only the basename plus metadata (no path strings) so they do
        # not collide with the relocation pass.
        all_artifacts = []
        sub_task_records = []
        successful = 0
        failed = 0
        combined_script_parts = []

        for idx, (req, resp) in enumerate(zip(sub_requests, sub_responses), start=1):
            outputs = (resp or {}).get("outputs", {}) or {}
            artifacts = [p for p in (outputs.get("dataset_paths") or []) if p]
            all_artifacts.extend(artifacts)
            if artifacts:
                successful += 1
            else:
                failed += 1

            sub_task_records.append({
                "sub_request_index": idx,
                "sub_request": req,
                "summary": outputs.get("text"),
                "artifact_count": len(artifacts),
                "artifact_basenames": [os.path.basename(p) for p in artifacts],
                "dataset_size": outputs.get("dataset_size") or self.empty_dataset_size(),
                "duration": (resp or {}).get("duration"),
                "validation": ((resp or {}).get("complementary", {}) or {}).get("Validation"),
            })

            script = (resp or {}).get("script") or ""
            if script:
                combined_script_parts.append(
                    f"# === Sub-request {idx}: {req} ===\n{script}"
                )

        overall_text = (
            f"Multi-dataset retrieval: {successful} of {len(sub_requests)} sub-requests "
            f"produced artifacts ({failed} failed). "
            f"Total artifacts: {len(all_artifacts)}."
        )

        self._emit_progress(
            progress_callback,
            stage="response_preparation",
            message=(
                f"All sub-tasks finished. {successful} succeeded, {failed} failed, "
                f"{len(all_artifacts)} artifact(s) packaged."
            ),
            data={
                "successful_sub_tasks": successful,
                "failed_sub_tasks": failed,
                "artifact_count": len(all_artifacts),
            },
        )

        outputs = {
            "text": overall_text,
            "dataset_paths": all_artifacts,
            "dataset_size": self.empty_dataset_size(),
            "sub_tasks": sub_task_records,
        }

        combined_script = "\n\n".join(combined_script_parts)

        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": f"{time.perf_counter() - start_time:.2f}s",
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "inputs": {
                "text": text_input,
                "dataset_path": input_dataset_path,
            },
            "outputs": outputs,
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": 0,
                "number_of_artifacts": len(all_artifacts),
                "sub_task_count": len(sub_requests),
                "successful_sub_tasks": successful,
                "failed_sub_tasks": failed,
            },
            "environment": {
                "python_version": sys.version.split()[0],
                "domain-specific libraries": self.DOMAIN_LIBRARIES,
            },
            "script": combined_script,
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "text": text_input,
                        "dataset_path": input_dataset_path,
                    },
                    "Outputs": {
                        "text": overall_text,
                        "artifact_count": len(all_artifacts),
                        "sub_tasks": sub_task_records,
                    },
                },
                "Provenance": {
                    "Lineage": {
                        "steps": [
                            "Request decomposition via LLM",
                            "Per-sub-request: source selection, handbook loading, code generation, code execution and debugging",
                            "Per-sub-request: output normalization",
                            "Aggregation of all sub-task artifacts and diagnostics",
                        ],
                        "note": (
                            f"Decomposed into {len(sub_requests)} sub-request(s); "
                            "each ran the standard single-download pipeline independently."
                        ),
                    },
                    "Tool Calls": {},
                    "LLM Calls": self.llm_calls,
                },
                "Sub-Tasks": sub_task_records,
            },
        }

    def _create_chat_completion(self, messages, model_name=None):
        self.llm_calls += 1
        response = self.client.chat.completions.create(
            model=model_name or self.model,
            messages=messages,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            self.input_tokens += prompt_tokens
            self.output_tokens += completion_tokens
        return response

    def _is_auth_error(self, error):
        text = str(error).lower()
        indicators = (
            "api key",
            "apikey",
            "authentication",
            "auth",
            "unauthorized",
            "invalid_request_error",
            "invalid api",
            "incorrect api",
            "401",
        )
        return any(indicator in text for indicator in indicators)

    def _raise_auth_error(self, error):
        raise ValueError(
            "OpenAI authentication failed. Check credentials.OPENAI_API_KEY, "
            "or provide credentials.GIBD_API_KEY."
        ) from error

    def select_source(self, select_prompt_str):
        
        message = [{"role": "system", "content": select_prompt_str}]
        
        try:
            response = self._create_chat_completion(messages=message, model_name=self.model)
            reply_content = response.choices[0].message.content
            logging.info("Successfully got the reply from LLM.")
            return reply_content
        except Exception as e:
            if self._is_auth_error(e):
                self._raise_auth_error(e)
            logging.error(f"Error in LLM response: {e}")
            return None

    def parse_source_selection(self, reply_content):
        if not reply_content or not isinstance(reply_content, str):
            return {}

        cleaned = reply_content.strip()
        cleaned = re.sub(r"^```(?:json|python)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = ast.literal_eval(cleaned)
        except Exception:
            try:
                import json

                parsed = json.loads(cleaned)
            except Exception as exc:
                logging.warning(f"Could not parse source-selection response: {exc}")
                return {}

        if not isinstance(parsed, dict):
            return {}
        return parsed

    def fallback_source_selection(self, data_request, data_source_dict):
        request = (data_request or "").lower()
        available_names = set(data_source_dict)

        candidates = [
            (
                "US Census Bureau boundary",
                [
                    "census bureau",
                    "census boundary",
                    "county boundary",
                    "county boundaries",
                    "state boundary",
                    "state boundaries",
                    "tract boundary",
                    "tract boundaries",
                    "block group",
                    "tiger",
                ],
            ),
            (
                "US Census Bureau demography",
                [
                    "acs",
                    "census demography",
                    "census population",
                    "population",
                    "income",
                    "education",
                    "demographic",
                ],
            ),
            ("OpenStreetMap", ["openstreetmap", "osm", "road", "building", "poi", "amenity"]),
            ("OpenTopography", ["dem", "elevation", "topography", "terrain"]),
            ("ESRI World Imagery (for Export)", ["imagery", "satellite", "aerial"]),
            ("USGS_Earthquake", ["earthquake", "seismic"]),
            ("CDC PLACES", ["cdc places", "chronic disease", "health outcome"]),
            ("EPA Air Quality System (AQS)", ["air quality", "aqs", "pm2.5", "ozone"]),
            ("OpenWeather", ["weather", "temperature", "precipitation"]),
            ("US COVID-19 data by New York Times", ["covid", "coronavirus"]),
            ("USDA ERS Geospatial (ArcGIS REST)", ["usda", "food access", "rural atlas", "ers"]),
        ]

        for source_name, hints in candidates:
            if source_name in available_names and any(hint in request for hint in hints):
                return {
                    "Explanation": f"Matched request keywords to the supported {source_name} handbook.",
                    "Selected data source": source_name,
                }

        return {
            "Explanation": "No deterministic source rule matched the request.",
            "Selected data source": "Unknown",
        }

    def normalize_selected_data_source(self, selected_data_source, data_request, data_source_dict):
        if isinstance(selected_data_source, (list, tuple)):
            for candidate in selected_data_source:
                if isinstance(candidate, str) and candidate in data_source_dict:
                    return candidate
            selected_data_source = next(
                (str(item) for item in selected_data_source if isinstance(item, str) and item.strip()),
                "",
            )

        if not isinstance(selected_data_source, str):
            selected_data_source = str(selected_data_source) if selected_data_source is not None else ""

        if selected_data_source in data_source_dict:
            return selected_data_source

        selected = (selected_data_source or "").strip().lower()
        request = (data_request or "").lower()

        if "census" in selected:
            demography_hints = ("population", "demograph", "acs", "income", "education")
            boundary_hints = ("boundary", "boundaries", "tract", "county", "state", "tiger")

            if any(word in request for word in demography_hints):
                if "US Census Bureau demography" in data_source_dict:
                    return "US Census Bureau demography"
            if any(word in request for word in boundary_hints):
                if "US Census Bureau boundary" in data_source_dict:
                    return "US Census Bureau boundary"

            if "US Census Bureau demography" in data_source_dict:
                return "US Census Bureau demography"

        return selected_data_source

    def try_direct_census_county_population_download(
        self,
        data_request,
        selected_data_source,
        output_stem,
        progress_callback=None,
    ):
        if selected_data_source != "US Census Bureau demography":
            return None
        if not self.is_county_population_request(data_request):
            return None

        years = self.extract_requested_years(data_request)
        year = years[0] if years else "2021"
        census_key = self.load_source_credentials("US_Census_demography").get("US_Census_demography_key")

        self._emit_progress(
            progress_callback,
            stage="download_start",
            message=(
                "This is a common Census county population request, so I will use a deterministic "
                "download-and-join workflow instead of generated download code."
            ),
            data={"year": year, "has_census_key": bool(census_key)},
        )

        boundary_url = f"https://www2.census.gov/geo/tiger/GENZ{year}/shp/cb_{year}_us_county_500k.zip"
        population_url = f"https://api.census.gov/data/{year}/acs/acs5"
        population_params = {
            "get": "NAME,B01001_001E",
            "for": "county:*",
        }
        if census_key:
            population_params["key"] = census_key

        boundaries = gpd.read_file(boundary_url)
        response = requests.get(population_url, params=population_params, timeout=60)
        response.raise_for_status()
        population_rows = response.json()
        if not isinstance(population_rows, list) or len(population_rows) < 2:
            raise ValueError("Census population response did not contain tabular rows.")

        header = population_rows[0]
        rows = population_rows[1:]
        population = pd.DataFrame(rows, columns=header)
        required_columns = {"state", "county", "B01001_001E"}
        if not required_columns <= set(population.columns):
            raise ValueError(
                f"Census population response missing required columns: {sorted(required_columns - set(population.columns))}"
            )

        population["state"] = population["state"].astype(str).str.zfill(2)
        population["county"] = population["county"].astype(str).str.zfill(3)
        population["GEOID"] = population["state"] + population["county"]
        population["B01001_001E:Total:"] = pd.to_numeric(population["B01001_001E"], errors="coerce")
        population = population.rename(
            columns={
                "NAME": "county_name",
                "state": "state_fips",
                "county": "county_fips",
            }
        )
        population["year"] = year
        population["source"] = f"ACS {year}"

        if "GEOID" not in boundaries.columns:
            statefp = self.find_first_column(boundaries, ("STATEFP", "STATEFP20", "STATEFP10"))
            countyfp = self.find_first_column(boundaries, ("COUNTYFP", "COUNTYFP20", "COUNTYFP10"))
            if not statefp or not countyfp:
                raise ValueError("County boundary file does not include GEOID or STATEFP/COUNTYFP columns.")
            boundaries["GEOID"] = boundaries[statefp].astype(str).str.zfill(2) + boundaries[countyfp].astype(str).str.zfill(3)

        merged = boundaries.merge(
            population[
                [
                    "GEOID",
                    "county_name",
                    "state_fips",
                    "county_fips",
                    "B01001_001E:Total:",
                    "year",
                    "source",
                ]
            ],
            on="GEOID",
            how="inner",
        )
        if merged.empty:
            raise ValueError("Census county boundaries and population rows did not join on GEOID.")

        filtered, _applied_rules = self.apply_vector_request_filters(merged, data_request)
        if filtered is not None and not filtered.empty:
            merged = filtered

        if self.is_explicit_csv_request(data_request):
            output_path = os.path.join(self.output_dir, f"{output_stem}.csv")
            self.save_geodataframe_as_csv(merged, output_path)
            output_label = "CSV"
        else:
            output_path = os.path.join(self.output_dir, f"{output_stem}.gpkg")
            merged.to_file(output_path, driver="GPKG")
            output_label = "GeoPackage"

        return {
            "path": output_path,
            "script": self.direct_census_county_population_script(year, bool(census_key), output_label),
        }

    def is_county_population_request(self, data_request):
        request = (data_request or "").lower()
        county_terms = ("county", "counties", "county-level")
        population_terms = ("population", "total population", "people")
        return any(term in request for term in county_terms) and any(term in request for term in population_terms)

    def direct_census_county_population_script(self, year, used_key, output_label="GeoPackage"):
        key_note = "with a Census API key" if used_key else "without a Census API key"
        return (
            "# deterministic Census county population workflow\n"
            f"# Downloaded {year} county boundaries from Census GENZ shapefile.\n"
            f"# Downloaded ACS {year} B01001_001E county population {key_note}.\n"
            f"# Joined boundaries and population by GEOID, then saved {output_label}.\n"
        )

    def generate_download_summary(
        self,
        data_request,
        selected_data_source,
        dataset_size,
    ):
        fallback = (
            f"The requested data was downloaded from {selected_data_source}.\n"
            f"The dataset summary is: {dataset_size}."
        )
        prompt = (
            "Write a final response for a geospatial data retrieval task.\n"
            "The response must be 4 or 5 lines.\n"
            "Describe how the data was downloaded and what the data is about.\n"
            "Do not include code. Do not invent details beyond the provided information.\n\n"
            f"User request: {data_request}\n"
            f"Selected data source: {selected_data_source}\n"
            f"Dataset summary: {dataset_size}"
        )
        messages = [
            {"role": "system", "content": "You summarize completed geospatial data downloads clearly and accurately."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self._create_chat_completion(messages=messages, model_name=self.model)
            reply_content = response.choices[0].message.content
            if reply_content and reply_content.strip():
                return reply_content.strip()
        except Exception as e:
            logging.error(f"Error generating download summary: {e}")
        return fallback
    
        
        
        
    def collect_handbook_files(self, source_dir=None):
        handbooks = glob(os.path.join(source_dir, "*.toml"))
        try:
            handbooks.remove(os.path.join(source_dir, "template.toml"))
        except:
            pass
        # logging.info(f"Successfully collected {len(handbooks)}")
        return handbooks


    def assemble_handbook_description(self, handbook_files ):
        descriptions = []
        data_source_dict = {}
        for idx, book in enumerate(handbook_files):
            with open(book, "rb") as f:
                handbook = tomli.load(f)
            data_source_ID = os.path.basename(book)[:-5]  # data_source_ID is the name of .toml file
            data_source_name = handbook['data_source_name'].strip()
            description = f"{idx + 1}. {data_source_name}. {handbook['brief_description'].strip()}"
            # print(description)
            descriptions.append(description)
            data_source_dict[data_source_name] = {"ID": data_source_ID}
        data_source_dict['Unknown'] = {"ID": "Unknown"}
        descriptions_str = "\n".join(descriptions)
        return descriptions_str, data_source_dict
    
    
    def collect_a_handbook(self, source_ID, source_dir=None, keys_dir=None):
        handbook_file = os.path.join(source_dir, f'{source_ID}.toml')
        
        # Check if handbook file exists
        if not os.path.exists(handbook_file):
            logging.warning(f"Warning: Handbook file not found: {handbook_file}")
            return None

        try:
            with open(handbook_file, "rb") as f:
                handbook = tomli.load(f)
            handbook_total_str = handbook['handbook']
        except Exception as e:
            logging.error(f"Error loading handbook for {source_ID}: {e}")
            return None

        for key, value in self.load_source_credentials(source_ID).items():
            handbook_total_str = handbook_total_str.replace(f"{{{key}}}", value)

        handbook_lines = handbook_total_str.strip().split('\n')
        numbered_handbook_str = ''
        for idx, line in enumerate(handbook_lines):
            line = line.strip(' ')
            numbered_handbook_str += f"{idx + 1}. {line}\n"

        for variable in handbook.keys():
            numbered_handbook_str = numbered_handbook_str.replace(f"{{{variable}}}",
                                                                handbook[variable])
        # print(handbook['code_example'])
        return numbered_handbook_str
    
    
    def load_source_credentials(self, source_ID):
        params = getattr(self, "request_parameters", {}) or {}
        credentials = {}

        for field_name in ("source_credentials", "data_source_credentials"):
            value = params.get(field_name)
            if isinstance(value, dict):
                credentials.update(self._flatten_source_credentials(value, source_ID))

        credentials.update(
            {
                key: value
                for key, value in params.items()
                if isinstance(key, str)
                and isinstance(value, str)
                and key.startswith(f"{source_ID}_")
            }
        )

        return {
            key: value
            for key, value in credentials.items()
            if value and value != "XXXX"
        }

    def _flatten_source_credentials(self, credentials, source_ID):
        flattened = {}
        source_specific = credentials.get(source_ID)
        if isinstance(source_specific, dict):
            for key, value in source_specific.items():
                if isinstance(value, str):
                    normalized_key = key if key.startswith(f"{source_ID}_") else f"{source_ID}_{key}"
                    flattened[normalized_key] = value

        for key, value in credentials.items():
            if isinstance(key, str) and isinstance(value, str) and key.startswith(f"{source_ID}_"):
                flattened[key] = value

        return flattened
    
    
    def generate_data_fetching_code(self, download_prompt_str):
        message = [{"role": "system", "content": download_prompt_str}]
        
        try:
            response = self._create_chat_completion(messages=message, model_name=self.model)
            reply_content = response.choices[0].message.content
            logging.info("Successfully got the reply from LLM.")
            return reply_content
        except Exception as e:
            if self._is_auth_error(e):
                self._raise_auth_error(e)
            logging.error(f"Error in LLM response: {e}")
            return None
        
    def extract_code_from_str(self, code_str):
        python_code = ""
        python_code_match = re.search(r"```(?:python)?(.*?)```", code_str, re.DOTALL)
        if python_code_match:
            python_code = python_code_match.group(1).strip()
        return python_code

    def prepare_output_dir(self):
        os.makedirs(self.output_dir, exist_ok=True)

    def list_output_files(self):
        if not os.path.exists(self.output_dir):
            return set()
        return {
            path
            for path in glob(os.path.join(self.output_dir, "**/*"), recursive=True)
            if os.path.isfile(path)
        }

    def generate_output_stem(self, task):
        return build_output_filename(
            task,
            extension="",
            fallback="retrieved_data",
        )

    def normalize_generated_code(self, code):
        normalized_code = code or ""
        replacements = {
            "area({osm_id})->.searchArea;": "relation({osm_id}); map_to_area->.searchArea;",
            "area({osm_id})->.rel;": "relation({osm_id}); map_to_area->.rel;",
            "response.status_code in (504, 429, 503)": "response.status_code in (406, 504, 429, 503)",
            "response.status_code in (504, 429)": "response.status_code in (406, 504, 429, 503)",
            "response.status_code == 504": "response.status_code in (406, 504, 429, 503)",
            "response.status_code == 429": "response.status_code in (406, 504, 429, 503)",
        }
        for wrong_text, correct_text in replacements.items():
            normalized_code = normalized_code.replace(wrong_text, correct_text)
        normalized_code = self._normalize_shapely_imports(normalized_code)
        return normalized_code

    def _normalize_shapely_imports(self, code):
        geometry_names = {"Point", "LineString", "Polygon", "MultiPolygon", "MultiLineString", "shape"}
        lines = []
        geometry_imports = []

        for line in code.splitlines():
            match = re.match(r"^(\s*)from\s+shapely\.ops\s+import\s+(.+)$", line)
            if not match:
                lines.append(line)
                continue

            indent, imported_text = match.groups()
            names = [name.strip() for name in imported_text.split(",") if name.strip()]
            ops_names = []
            moved_names = []
            for name in names:
                bare_name = name.split(" as ", 1)[0].strip()
                if bare_name in geometry_names:
                    moved_names.append(name)
                else:
                    ops_names.append(name)

            if ops_names:
                lines.append(f"{indent}from shapely.ops import {', '.join(ops_names)}")
            if moved_names:
                geometry_imports.extend(moved_names)

        if geometry_imports:
            unique_imports = list(dict.fromkeys(geometry_imports))
            lines.insert(0, f"from shapely.geometry import {', '.join(unique_imports)}")

        return "\n".join(lines)

    def is_primary_dataset_file(self, path):
        return os.path.splitext(path)[1].lower() not in self.SIDECAR_EXTENSIONS

    def discover_output_files(self, before, after, output_stem):
        new_files = sorted(
            path for path in (after - before)
            if self.is_primary_dataset_file(path)
        )
        if new_files:
            matching_files = [
                path for path in new_files
                if os.path.splitext(os.path.basename(path))[0] == output_stem
            ]
            return self.enforce_output_filename(matching_files or new_files, output_stem)

        existing_matches = sorted(
            path for path in glob(os.path.join(self.output_dir, f"{output_stem}.*"))
            if os.path.isfile(path) and self.is_primary_dataset_file(path)
        )
        return existing_matches

    def enforce_output_filename(self, output_files, output_stem):
        if not output_files:
            return []

        matching_files = [
            path for path in output_files
            if os.path.splitext(os.path.basename(path))[0] == output_stem
        ]
        if matching_files:
            return matching_files

        if len(output_files) != 1:
            return []

        original_path = output_files[0]
        extension = os.path.splitext(original_path)[1].lower()
        if extension not in self.SINGLE_FILE_EXTENSIONS:
            return []

        renamed_path = os.path.join(self.output_dir, f"{output_stem}{extension}")
        if os.path.abspath(original_path) == os.path.abspath(renamed_path):
            return [original_path]

        os.replace(original_path, renamed_path)
        return [renamed_path]

    def pick_primary_output(self, output_files, output_stem):
        if not output_files:
            return None

        matching_files = [
            path for path in output_files
            if os.path.splitext(os.path.basename(path))[0] == output_stem
        ]
        candidates = matching_files or output_files
        return max(candidates, key=os.path.getmtime)

    def preferred_vector_output(self, data_request):
        if self.is_explicit_geopackage_request(data_request):
            return ".gpkg", "GPKG", "GeoPackage"
        if self.is_explicit_geojson_request(data_request):
            return ".geojson", "GeoJSON", "GeoJSON"
        return ".gpkg", "GPKG", "GeoPackage"

    def is_explicit_csv_request(self, data_request):
        request = (data_request or "").lower()
        if "csv" not in request and ".csv" not in request:
            return False

        format_terms = r"(save|saved|return|returned|output|export|write|written|deliver|delivered|generate|generated|create|created|download|downloaded)"
        csv_terms = r"(\.csv|csv)"
        before_pattern = rf"\b{format_terms}\b[\w\s,.;:'\"()/\\-]{{0,100}}\b{csv_terms}\b"
        as_pattern = rf"\b(as|in|to|into)\s+(a\s+)?{csv_terms}(\s+(format|file|artifact|dataset))?\b"
        after_pattern = rf"\b{csv_terms}\s+(format|file|output|artifact|delivery|dataset)\b"
        return bool(
            re.search(before_pattern, request)
            or re.search(as_pattern, request)
            or re.search(after_pattern, request)
        )

    def save_geodataframe_as_csv(self, gdf, target_path):
        table = pd.DataFrame(gdf.copy())
        geometry_column = getattr(gdf, "geometry", None)
        geometry_name = getattr(geometry_column, "name", None)
        if geometry_name and geometry_name in table.columns:
            table["geometry_wkt"] = gdf.geometry.to_wkt()
            table = table.drop(columns=[geometry_name])
        table.to_csv(target_path, index=False)

    def is_explicit_geopackage_request(self, data_request):
        request = (data_request or "").lower()
        return bool(
            re.search(r"\b(geopackage|gpkg|\.gpkg)\b", request)
            and re.search(
                r"\b(save|saved|return|returned|output|export|write|written|deliver|delivered|generate|generated|create|created|format|file|artifact|as|in|to|into)\b",
                request,
            )
        )

    def is_explicit_geojson_request(self, data_request):
        request = (data_request or "").lower()
        if "geojson" not in request and ".geojson" not in request:
            return False

        format_terms = r"(save|saved|return|returned|output|export|write|written|deliver|delivered|generate|generated|create|created)"
        geojson_terms = r"(\.geojson|geojson)"
        before_pattern = rf"\b{format_terms}\b[\w\s,.;:'\"()/\\-]{{0,80}}\b{geojson_terms}\b"
        as_pattern = rf"\b(as|in|to|into)\s+(a\s+)?{geojson_terms}(\s+(format|file|artifact))?\b"
        after_pattern = rf"\b{geojson_terms}\s+(format|file|output|artifact|delivery)\b"
        return bool(
            re.search(before_pattern, request)
            or re.search(as_pattern, request)
            or re.search(after_pattern, request)
        )

    def ensure_preferred_vector_output(self, file_path, output_stem, data_request=None, progress_callback=None):
        if not file_path or not os.path.exists(file_path):
            return file_path

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.VECTOR_EXTENSIONS:
            return file_path

        if self.is_explicit_csv_request(data_request):
            target_path = os.path.join(self.output_dir, f"{output_stem}.csv")
            try:
                gdf = gpd.read_file(file_path)
                self.save_geodataframe_as_csv(gdf, target_path)
                self._emit_progress(
                    progress_callback,
                    stage="normalization",
                    message="I converted the vector output to CSV because the request explicitly asked for CSV.",
                    data={"input_path": file_path, "output_path": target_path, "target_format": "CSV"},
                )
                return target_path
            except Exception as exc:
                warning_message = "I could not convert the vector output to CSV, so I will keep the original file."
                logging.warning(f"Could not convert vector output '{file_path}' to CSV: {exc}")
                self._emit_progress(
                    progress_callback,
                    stage="warning",
                    message=warning_message,
                    data={"path": file_path, "target_format": "CSV", "error": str(exc)},
                )
                return file_path

        preferred_ext, preferred_driver, preferred_label = self.preferred_vector_output(data_request)
        target_path = os.path.join(self.output_dir, f"{output_stem}{preferred_ext}")

        if ext == preferred_ext and ext not in {".shp", ".gdb"}:
            if os.path.abspath(file_path) == os.path.abspath(target_path):
                return file_path
            os.replace(file_path, target_path)
            return target_path

        try:
            gdf = gpd.read_file(file_path)
            if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
                warning_message = (
                    f"The vector output is not EPSG:4326. I will still save it as {preferred_label} "
                    "without changing its projection."
                )
                logging.warning(
                    f"Vector output '{file_path}' is not EPSG:4326. It will still be saved as {preferred_label} without reprojection."
                )
                self._emit_progress(
                    progress_callback,
                    stage="warning",
                    message=warning_message,
                    data={
                        "path": file_path,
                        "target_format": preferred_label,
                        "crs": str(gdf.crs),
                    },
                )
            gdf.to_file(target_path, driver=preferred_driver)
            self._emit_progress(
                progress_callback,
                stage="normalization",
                message=f"I normalized the vector output and saved it as {preferred_label}.",
                data={"input_path": file_path, "output_path": target_path, "target_format": preferred_label},
            )
            return target_path
        except Exception as exc:
            warning_message = f"I could not convert the vector output to {preferred_label}, so I will keep the original file."
            logging.warning(f"Could not convert vector output '{file_path}' to {preferred_label}: {exc}")
            self._emit_progress(
                progress_callback,
                stage="warning",
                message=warning_message,
                data={"path": file_path, "target_format": preferred_label, "error": str(exc)},
            )
        return file_path

    def apply_request_postprocessing(
        self,
        downloaded_files,
        data_request,
        output_stem,
        selected_data_source,
        progress_callback=None,
    ):
        processed_files = []
        for file_path in downloaded_files:
            processed_path = self.postprocess_vector_output(
                file_path,
                data_request,
                output_stem,
                selected_data_source,
                progress_callback=progress_callback,
            )
            if processed_path:
                processed_files.append(processed_path)
        return processed_files

    def postprocess_vector_output(
        self,
        file_path,
        data_request,
        output_stem,
        selected_data_source,
        progress_callback=None,
    ):
        if not file_path or not os.path.exists(file_path):
            return file_path

        extension = os.path.splitext(file_path)[1].lower()
        if extension not in self.VECTOR_EXTENSIONS:
            return file_path

        try:
            gdf = gpd.read_file(file_path)
        except Exception as exc:
            logging.warning(f"Could not read vector output for post-processing '{file_path}': {exc}")
            return file_path

        if gdf.empty:
            return file_path

        filtered, applied_rules = self.apply_vector_request_filters(gdf, data_request)
        if filtered is None or filtered.empty or not applied_rules:
            if self.is_explicit_csv_request(data_request):
                target_path = os.path.join(self.output_dir, f"{output_stem}.csv")
                self.save_geodataframe_as_csv(gdf, target_path)
                self._emit_progress(
                    progress_callback,
                    stage="normalization",
                    message="I saved the vector dataset as CSV because the request explicitly asked for CSV.",
                    data={
                        "selected_data_source": selected_data_source,
                        "feature_count": int(len(gdf)),
                        "output_path": target_path,
                    },
                )
                return target_path
            return file_path

        if self.is_explicit_csv_request(data_request):
            target_path = os.path.join(self.output_dir, f"{output_stem}.csv")
            self.save_geodataframe_as_csv(filtered, target_path)
            self._emit_progress(
                progress_callback,
                stage="normalization",
                message="I post-processed the downloaded vector data and saved the refined result as CSV.",
                data={
                    "selected_data_source": selected_data_source,
                    "before_feature_count": int(len(gdf)),
                    "after_feature_count": int(len(filtered)),
                    "applied_rules": applied_rules,
                    "output_path": target_path,
                },
            )
            return target_path

        preferred_ext, preferred_driver, preferred_label = self.preferred_vector_output(data_request)
        target_path = os.path.join(self.output_dir, f"{output_stem}{preferred_ext}")
        filtered.to_file(target_path, driver=preferred_driver)
        self._emit_progress(
            progress_callback,
            stage="normalization",
            message=(
                f"I post-processed the downloaded vector data and saved the refined result as {preferred_label}."
            ),
            data={
                "selected_data_source": selected_data_source,
                "before_feature_count": int(len(gdf)),
                "after_feature_count": int(len(filtered)),
                "applied_rules": applied_rules,
                "output_path": target_path,
            },
        )
        return target_path

    def apply_vector_request_filters(self, gdf, data_request):
        request_text = data_request or ""
        request = request_text.lower()
        filtered = gdf.copy()
        applied_rules = []

        if self.is_contiguous_us_request(request):
            next_gdf = self.filter_contiguous_us_features(filtered)
            filtered, applied = self.accept_non_empty_filter(
                filtered,
                next_gdf,
                "contiguous_us_extent",
            )
            if applied:
                applied_rules.append(applied)

        requested_states = self.extract_requested_us_states(request_text)
        if requested_states:
            next_gdf = self.filter_us_states(filtered, requested_states)
            filtered, applied = self.accept_non_empty_filter(
                filtered,
                next_gdf,
                "requested_us_state",
            )
            if applied:
                applied_rules.append(applied)

        requested_counties = self.extract_requested_county_names(request_text)
        if requested_counties:
            next_gdf = self.filter_county_names(filtered, requested_counties)
            filtered, applied = self.accept_non_empty_filter(
                filtered,
                next_gdf,
                "requested_county_name",
            )
            if applied:
                applied_rules.append(applied)

        requested_years = self.extract_requested_years(request_text)
        if requested_years:
            next_gdf = self.filter_years(filtered, requested_years)
            filtered, applied = self.accept_non_empty_filter(
                filtered,
                next_gdf,
                "requested_year",
            )
            if applied:
                applied_rules.append(applied)

        return filtered, applied_rules

    def accept_non_empty_filter(self, current_gdf, next_gdf, rule_name):
        if next_gdf is None or next_gdf.empty or len(next_gdf) == len(current_gdf):
            return current_gdf, None
        return next_gdf.copy(), rule_name

    def is_contiguous_us_request(self, request):
        contiguous_terms = (
            "contiguous united states",
            "contiguous us",
            "contiguous u.s.",
            "conterminous united states",
            "conterminous us",
            "conterminous u.s.",
            "lower 48",
            "lower forty-eight",
            "lower forty eight",
        )
        continental_terms = ("continental united states", "continental us", "continental u.s.")
        return any(term in request for term in contiguous_terms + continental_terms)

    def filter_contiguous_us_features(self, gdf):
        excluded_state_fips = {"02", "15", "60", "66", "69", "72", "78"}
        excluded_state_abbr = {"AK", "HI", "AS", "GU", "MP", "PR", "VI"}
        excluded_state_names = {
            "alaska",
            "hawaii",
            "american samoa",
            "guam",
            "northern mariana islands",
            "puerto rico",
            "u.s. virgin islands",
            "united states virgin islands",
            "virgin islands",
        }

        state_fips_column = self.find_first_column(
            gdf,
            ("STATEFP", "STATEFP10", "STATEFP20", "state_fips", "statefp", "STATE"),
        )
        if state_fips_column:
            values = gdf[state_fips_column].astype(str).str.zfill(2)
            return gdf[~values.isin(excluded_state_fips)].copy()

        geoid_column = self.find_first_column(gdf, ("GEOID", "GEOID10", "GEOID20", "geoid", "fips"))
        if geoid_column:
            values = gdf[geoid_column].astype(str).str[:2].str.zfill(2)
            return gdf[~values.isin(excluded_state_fips)].copy()

        state_abbr_column = self.find_first_column(gdf, ("STUSPS", "state_abbr", "state_code", "postal"))
        if state_abbr_column:
            values = gdf[state_abbr_column].astype(str).str.upper()
            return gdf[~values.isin(excluded_state_abbr)].copy()

        state_name_column = self.find_first_column(gdf, ("STATE_NAME", "state_name", "state", "NAME_STATE"))
        if state_name_column:
            values = gdf[state_name_column].astype(str).str.lower()
            return gdf[~values.isin(excluded_state_names)].copy()

        return None

    def extract_requested_us_states(self, request_text):
        lower_request = (request_text or "").lower()
        states = self.us_state_lookup()
        requested = []

        for state in states:
            if re.search(rf"\b{re.escape(state['name'].lower())}\b", lower_request):
                requested.append(state)

        uppercase_tokens = set(re.findall(r"\b[A-Z]{2}\b", request_text or ""))
        for state in states:
            if state["abbr"] in uppercase_tokens and state not in requested:
                requested.append(state)

        return requested

    def filter_us_states(self, gdf, states):
        if not states:
            return None

        fips_values = {state["fips"] for state in states}
        abbr_values = {state["abbr"] for state in states}
        name_values = {state["name"].lower() for state in states}

        state_fips_column = self.find_first_column(
            gdf,
            ("STATEFP", "STATEFP10", "STATEFP20", "state_fips", "statefp", "STATE"),
        )
        if state_fips_column:
            values = gdf[state_fips_column].astype(str).str.zfill(2)
            return gdf[values.isin(fips_values)].copy()

        geoid_column = self.find_first_column(gdf, ("GEOID", "GEOID10", "GEOID20", "geoid", "fips"))
        if geoid_column:
            values = gdf[geoid_column].astype(str).str[:2].str.zfill(2)
            return gdf[values.isin(fips_values)].copy()

        state_abbr_column = self.find_first_column(gdf, ("STUSPS", "state_abbr", "state_code", "postal"))
        if state_abbr_column:
            values = gdf[state_abbr_column].astype(str).str.upper()
            return gdf[values.isin(abbr_values)].copy()

        state_name_column = self.find_first_column(gdf, ("STATE_NAME", "state_name", "state", "NAME_STATE"))
        if state_name_column:
            values = gdf[state_name_column].astype(str).str.lower()
            return gdf[values.isin(name_values)].copy()

        return None

    def extract_requested_county_names(self, request_text):
        matches = re.findall(
            r"\b([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,3})\s+County\b",
            request_text or "",
        )
        generic_words = {"US", "U.S", "United States", "Contiguous US", "Conterminous US"}
        action_words = {"Download", "Retrieve", "Get", "Find", "Map", "Create", "Show", "Use"}
        county_names = []
        for match in matches:
            words = match.strip().split()
            while words and words[0] in action_words:
                words = words[1:]
            cleaned = " ".join(words)
            if cleaned and cleaned not in generic_words:
                county_names.append(cleaned)
        return county_names

    def filter_county_names(self, gdf, county_names):
        county_column = self.find_first_column(
            gdf,
            ("COUNTY_NAME", "county_name", "county", "NAMELSAD", "NAME", "name"),
        )
        if not county_column or not county_names:
            return None

        requested = {self.normalize_place_name(name) for name in county_names}
        values = gdf[county_column].astype(str).map(self.normalize_place_name)
        return gdf[values.isin(requested)].copy()

    def extract_requested_years(self, request_text):
        years = []
        for value in re.findall(r"\b(19\d{2}|20\d{2})\b", request_text or ""):
            if value not in years:
                years.append(value)
        return years

    def filter_years(self, gdf, years):
        year_column = self.find_first_column(gdf, ("year", "YEAR", "data_year", "survey_year", "DATE_YEAR"))
        if not year_column or not years:
            return None

        values = gdf[year_column].astype(str).str.extract(r"(19\d{2}|20\d{2})", expand=False)
        return gdf[values.isin(set(years))].copy()

    def normalize_place_name(self, value):
        text = str(value or "").lower()
        text = re.sub(r"\b(county|parish|borough|municipio|census area)\b", "", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    def us_state_lookup(self):
        return [
            {"name": "Alabama", "abbr": "AL", "fips": "01"},
            {"name": "Alaska", "abbr": "AK", "fips": "02"},
            {"name": "Arizona", "abbr": "AZ", "fips": "04"},
            {"name": "Arkansas", "abbr": "AR", "fips": "05"},
            {"name": "California", "abbr": "CA", "fips": "06"},
            {"name": "Colorado", "abbr": "CO", "fips": "08"},
            {"name": "Connecticut", "abbr": "CT", "fips": "09"},
            {"name": "Delaware", "abbr": "DE", "fips": "10"},
            {"name": "District of Columbia", "abbr": "DC", "fips": "11"},
            {"name": "Florida", "abbr": "FL", "fips": "12"},
            {"name": "Georgia", "abbr": "GA", "fips": "13"},
            {"name": "Hawaii", "abbr": "HI", "fips": "15"},
            {"name": "Idaho", "abbr": "ID", "fips": "16"},
            {"name": "Illinois", "abbr": "IL", "fips": "17"},
            {"name": "Indiana", "abbr": "IN", "fips": "18"},
            {"name": "Iowa", "abbr": "IA", "fips": "19"},
            {"name": "Kansas", "abbr": "KS", "fips": "20"},
            {"name": "Kentucky", "abbr": "KY", "fips": "21"},
            {"name": "Louisiana", "abbr": "LA", "fips": "22"},
            {"name": "Maine", "abbr": "ME", "fips": "23"},
            {"name": "Maryland", "abbr": "MD", "fips": "24"},
            {"name": "Massachusetts", "abbr": "MA", "fips": "25"},
            {"name": "Michigan", "abbr": "MI", "fips": "26"},
            {"name": "Minnesota", "abbr": "MN", "fips": "27"},
            {"name": "Mississippi", "abbr": "MS", "fips": "28"},
            {"name": "Missouri", "abbr": "MO", "fips": "29"},
            {"name": "Montana", "abbr": "MT", "fips": "30"},
            {"name": "Nebraska", "abbr": "NE", "fips": "31"},
            {"name": "Nevada", "abbr": "NV", "fips": "32"},
            {"name": "New Hampshire", "abbr": "NH", "fips": "33"},
            {"name": "New Jersey", "abbr": "NJ", "fips": "34"},
            {"name": "New Mexico", "abbr": "NM", "fips": "35"},
            {"name": "New York", "abbr": "NY", "fips": "36"},
            {"name": "North Carolina", "abbr": "NC", "fips": "37"},
            {"name": "North Dakota", "abbr": "ND", "fips": "38"},
            {"name": "Ohio", "abbr": "OH", "fips": "39"},
            {"name": "Oklahoma", "abbr": "OK", "fips": "40"},
            {"name": "Oregon", "abbr": "OR", "fips": "41"},
            {"name": "Pennsylvania", "abbr": "PA", "fips": "42"},
            {"name": "Rhode Island", "abbr": "RI", "fips": "44"},
            {"name": "South Carolina", "abbr": "SC", "fips": "45"},
            {"name": "South Dakota", "abbr": "SD", "fips": "46"},
            {"name": "Tennessee", "abbr": "TN", "fips": "47"},
            {"name": "Texas", "abbr": "TX", "fips": "48"},
            {"name": "Utah", "abbr": "UT", "fips": "49"},
            {"name": "Vermont", "abbr": "VT", "fips": "50"},
            {"name": "Virginia", "abbr": "VA", "fips": "51"},
            {"name": "Washington", "abbr": "WA", "fips": "53"},
            {"name": "West Virginia", "abbr": "WV", "fips": "54"},
            {"name": "Wisconsin", "abbr": "WI", "fips": "55"},
            {"name": "Wyoming", "abbr": "WY", "fips": "56"},
        ]

    def find_first_column(self, gdf, candidates):
        by_lower = {str(column).lower(): column for column in gdf.columns}
        for candidate in candidates:
            column = by_lower.get(str(candidate).lower())
            if column is not None:
                return column
        return None

    def format_source_selection_message(self, selected_data_source, explanation):
        if explanation:
            cleaned = " ".join(str(explanation).split())
            return f"I selected {selected_data_source} as the best available source. Reason: {cleaned}"
        return f"I selected {selected_data_source} as the best available source and will load its download instructions."

    def format_raw_source_selection_message(self, source_selection):
        selected = source_selection.get("Selected data source", "Unknown")
        explanation = source_selection.get("Explanation")
        if explanation:
            cleaned = " ".join(str(explanation).split())
            return f"Source selection: {selected}. Explanation: {cleaned}"
        return f"Source selection: {selected}."

    def format_dataset_created_message(self, dataset_size, artifact_count):
        dataset_type = (dataset_size or {}).get("type")
        feature_count = (dataset_size or {}).get("feature_count")
        dimensions = (dataset_size or {}).get("dimensions")

        if feature_count is not None:
            return f"I created {int(feature_count):,} records and packaged {artifact_count} artifact(s) for the final response."

        if dimensions:
            return f"I created a {dataset_type or 'dataset'} artifact with dimensions {dimensions} and packaged it for the final response."

        return f"I created and packaged {artifact_count} artifact(s) for the final response."

    def empty_dataset_size(self):
        return {
            "type": None,
            "dimensions": None,
            "feature_count": None,
        }

    def describe_dataset(self, dataset_path):
        if not dataset_path or not os.path.exists(dataset_path):
            return self.empty_dataset_size()

        extension = os.path.splitext(dataset_path)[1].lower()
        if extension in self.VECTOR_EXTENSIONS:
            try:
                gdf = gpd.read_file(dataset_path)
                return {
                    "type": "Vector",
                    "dimensions": None,
                    "feature_count": len(gdf),
                }
            except Exception as exc:
                logging.warning(f"Could not inspect vector dataset '{dataset_path}': {exc}")

        if extension in self.RASTER_EXTENSIONS:
            try:
                import rasterio

                with rasterio.open(dataset_path) as src:
                    return {
                        "type": "Raster",
                        "dimensions": [src.width, src.height, src.count],
                        "feature_count": None,
                    }
            except Exception as exc:
                logging.warning(f"Could not inspect raster dataset '{dataset_path}': {exc}")

        return self.empty_dataset_size()

    def validation_check(self, name, status, message, **data):
        return {"name": name, "status": status, "message": message, **data}

    def validation_status(self, checks):
        statuses = {check.get("status") for check in checks}
        if "failed" in statuses:
            return "failed"
        if "warning" in statuses:
            return "warning"
        return "passed"

    def self_validate_result(self, text_input, output_dataset_path, downloaded_files):
        checks = []
        artifacts = [path for path in downloaded_files if path]

        if artifacts:
            checks.append(
                self.validation_check(
                    "artifact_created",
                    "passed",
                    f"{len(artifacts)} artifact(s) were created.",
                    artifact_count=len(artifacts),
                )
            )
        else:
            checks.append(self.validation_check("artifact_created", "failed", "No output artifact was created."))

        if output_dataset_path:
            checks.extend(self.validate_single_artifact(text_input, output_dataset_path))
        elif artifacts:
            checks.extend(self.validate_single_artifact(text_input, artifacts[0]))
        else:
            checks.append(self.validation_check("primary_artifact", "failed", "No primary output artifact was selected."))

        return {
            "status": self.validation_status(checks),
            "checks": checks,
        }

    def validate_single_artifact(self, text_input, path):
        checks = []
        extension = os.path.splitext(path or "")[1].lower()

        if not path or not os.path.exists(path):
            return [self.validation_check("file_exists", "failed", "The selected output file does not exist.", path=path)]

        checks.append(self.validation_check("file_exists", "passed", "The selected output file exists.", path=path))

        try:
            size_bytes = os.path.getsize(path)
        except OSError as exc:
            size_bytes = 0
            checks.append(self.validation_check("file_size", "failed", f"Could not read output file size: {exc}", path=path))
        else:
            if size_bytes > 0:
                checks.append(
                    self.validation_check(
                        "file_size",
                        "passed",
                        f"Output file is non-empty ({size_bytes:,} bytes).",
                        size_bytes=size_bytes,
                    )
                )
            else:
                checks.append(self.validation_check("file_size", "failed", "Output file is empty.", path=path))

        checks.append(self.validate_requested_format(text_input, extension, path))

        if extension in self.VECTOR_EXTENSIONS:
            checks.extend(self.validate_vector_artifact(text_input, path))
        elif extension in self.RASTER_EXTENSIONS:
            checks.extend(self.validate_raster_artifact(path))
        elif extension in self.TABLE_EXTENSIONS:
            checks.extend(self.validate_table_artifact(text_input, path))
        else:
            checks.append(
                self.validation_check(
                    "artifact_readability",
                    "warning",
                    f"No built-in readability check is available for '{extension or 'unknown'}' files.",
                )
            )

        return checks

    def validate_requested_format(self, text_input, extension, path):
        if self.is_explicit_csv_request(text_input):
            expected = ".csv"
            label = "CSV"
        elif self.is_explicit_geojson_request(text_input):
            expected = ".geojson"
            label = "GeoJSON"
        elif self.is_explicit_geopackage_request(text_input):
            expected = ".gpkg"
            label = "GeoPackage"
        else:
            return self.validation_check(
                "requested_format",
                "passed",
                "No explicit output format was requested; the agent default format policy applies.",
                actual_extension=extension,
            )

        status = "passed" if extension == expected else "failed"
        message = (
            f"Output format matches the explicit {label} request."
            if status == "passed"
            else f"Output format does not match the explicit {label} request."
        )
        return self.validation_check(
            "requested_format",
            status,
            message,
            expected_extension=expected,
            actual_extension=extension,
            path=path,
        )

    def validate_vector_artifact(self, text_input, path):
        checks = []
        try:
            gdf = gpd.read_file(path)
        except Exception as exc:
            return [self.validation_check("artifact_readability", "failed", f"Vector artifact is not readable: {exc}")]

        checks.append(
            self.validation_check(
                "artifact_readability",
                "passed",
                "Vector artifact is readable with GeoPandas.",
            )
        )
        checks.extend(self.validate_dataframe_content(text_input, gdf, geometry_expected=True))
        return checks

    def validate_table_artifact(self, text_input, path):
        checks = []
        try:
            df = pd.read_csv(path)
        except Exception as exc:
            return [self.validation_check("artifact_readability", "failed", f"CSV artifact is not readable: {exc}")]

        checks.append(self.validation_check("artifact_readability", "passed", "CSV artifact is readable with pandas."))
        checks.extend(self.validate_dataframe_content(text_input, df, geometry_expected=False))
        return checks

    def validate_raster_artifact(self, path):
        try:
            import rasterio

            with rasterio.open(path) as src:
                width = src.width
                height = src.height
                band_count = src.count
        except Exception as exc:
            return [self.validation_check("artifact_readability", "failed", f"Raster artifact is not readable: {exc}")]

        status = "passed" if width > 0 and height > 0 and band_count > 0 else "failed"
        return [
            self.validation_check(
                "artifact_readability",
                "passed",
                "Raster artifact is readable with Rasterio.",
                width=width,
                height=height,
                band_count=band_count,
            ),
            self.validation_check(
                "raster_dimensions",
                status,
                "Raster has positive width, height, and band count." if status == "passed" else "Raster dimensions are invalid.",
                width=width,
                height=height,
                band_count=band_count,
            ),
        ]

    def validate_dataframe_content(self, text_input, df, geometry_expected):
        checks = []
        row_count = int(len(df))
        if row_count:
            checks.append(self.validation_check("record_count", "passed", f"Artifact contains {row_count:,} record(s).", record_count=row_count))
        else:
            checks.append(self.validation_check("record_count", "failed", "Artifact contains no records.", record_count=0))

        column_names = [str(column) for column in df.columns]
        if column_names:
            checks.append(
                self.validation_check(
                    "schema_present",
                    "passed",
                    f"Artifact contains {len(column_names)} column(s).",
                    columns=column_names,
                )
            )
        else:
            checks.append(self.validation_check("schema_present", "failed", "Artifact has no columns."))

        checks.extend(self.validate_requested_years(text_input, df))
        checks.extend(self.validate_requested_states(text_input, df))
        checks.extend(self.validate_contiguous_us_constraint(text_input, df))

        if geometry_expected and hasattr(df, "geometry"):
            try:
                null_geometry_count = int(df.geometry.isna().sum())
            except Exception:
                null_geometry_count = 0
            status = "warning" if null_geometry_count else "passed"
            message = (
                f"{null_geometry_count} record(s) have missing geometry."
                if null_geometry_count
                else "All vector records have geometry."
            )
            checks.append(self.validation_check("geometry_presence", status, message, null_geometry_count=null_geometry_count))

        return checks

    def validate_requested_years(self, text_input, df):
        years = self.extract_requested_years(text_input)
        if not years:
            return []

        year_column = self.find_first_column(df, ("year", "YEAR", "Year"))
        if not year_column:
            return [
                self.validation_check(
                    "requested_year",
                    "warning",
                    "A year was requested, but no obvious year column was found for validation.",
                    requested_years=years,
                )
            ]

        observed_years = sorted({str(value) for value in df[year_column].dropna().astype(str)})
        missing_years = [year for year in years if year not in observed_years]
        status = "failed" if missing_years else "passed"
        message = (
            "All requested years are present in the output."
            if status == "passed"
            else "One or more requested years are missing from the output."
        )
        return [
            self.validation_check(
                "requested_year",
                status,
                message,
                requested_years=years,
                observed_years=observed_years,
                missing_years=missing_years,
            )
        ]

    def validate_requested_states(self, text_input, df):
        requested_states = self.extract_requested_us_states(text_input)
        if not requested_states:
            return []

        state_column = self.find_first_column(
            df,
            ("STATEFP", "state_fips", "STATE", "state", "STUSPS", "state_abbr", "state_name", "NAME"),
        )
        if not state_column:
            return [
                self.validation_check(
                    "requested_state",
                    "warning",
                    "A state was requested, but no obvious state column was found for validation.",
                    requested_states=requested_states,
                )
            ]

        observed = {str(value).strip().lower() for value in df[state_column].dropna().astype(str)}
        missing = []
        for state in requested_states:
            values = {state["name"].lower(), state["abbr"].lower(), state["fips"].lower()}
            if not (observed & values):
                missing.append(state["abbr"])

        status = "failed" if missing else "passed"
        message = (
            "Requested state filter appears to be represented in the output."
            if status == "passed"
            else "One or more requested states were not found in the output."
        )
        return [
            self.validation_check(
                "requested_state",
                status,
                message,
                requested_states=[state["abbr"] for state in requested_states],
                missing_states=missing,
                validated_column=str(state_column),
            )
        ]

    def validate_contiguous_us_constraint(self, text_input, df):
        if not self.is_contiguous_us_request(text_input):
            return []

        state_column = self.find_first_column(df, ("STATEFP", "state_fips", "STATE", "state"))
        if not state_column:
            return [
                self.validation_check(
                    "contiguous_us_filter",
                    "warning",
                    "Contiguous US was requested, but no state FIPS column was found for validation.",
                )
            ]

        excluded_fips = {"02", "15", "60", "66", "69", "72", "78"}
        observed_excluded = sorted(
            {
                str(value).strip().zfill(2)
                for value in df[state_column].dropna()
                if str(value).strip().zfill(2) in excluded_fips
            }
        )
        status = "failed" if observed_excluded else "passed"
        message = (
            "Excluded non-contiguous states and territories were not found in the output."
            if status == "passed"
            else "The output still contains non-contiguous state or territory FIPS codes."
        )
        return [
            self.validation_check(
                "contiguous_us_filter",
                status,
                message,
                excluded_fips_present=observed_excluded,
                validated_column=str(state_column),
            )
        ]

    def build_response(
        self,
        start_time,
        text_input,
        input_dataset_path,
        output_text,
        output_dataset_path,
        dataset_size,
        script,
        downloaded_files,
        progress_callback=None,
    ):
        persisted_artifacts = [path for path in downloaded_files if path]
        validation = self.self_validate_result(text_input, output_dataset_path, persisted_artifacts)
        self._emit_progress(
            progress_callback,
            stage="data_validation",
            message=f"I validated the output artifact before returning it. Validation status: {validation['status']}.",
            data=validation,
        )
        outputs = {
            "text": output_text,
            "dataset_path": output_dataset_path,
            "dataset_paths": persisted_artifacts,
            "dataset_size": dataset_size,
        }
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "model": self.model,
            "duration": f"{time.perf_counter() - start_time:.2f}s",
            "total_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "inputs": {
                "text": text_input,
                "dataset_path": input_dataset_path,
            },
            "outputs": outputs,
            "metrics": {
                "llm_calls": self.llm_calls,
                "tool_calls": 0,
                "number_of_artifacts": len(persisted_artifacts),
            },
            "environment": {
                "python_version": sys.version.split()[0],
                "domain-specific libraries": self.DOMAIN_LIBRARIES,
            },
            "script": script,
            "complementary": {
                "Execution": {
                    "Inputs": {
                        "text": text_input,
                        "dataset_path": input_dataset_path,
                    },
                    "Outputs": outputs,
                },
                "Provenance": {
                    "Lineage": {
                        "steps": [
                            "Source selection via LLM",
                            "Handbook loading",
                            "Download code generation",
                            "Code execution and debugging",
                            "Output normalization (GeoPackage conversion for vector data by default, if applicable)",
                        ],
                        "note": "Detailed steps not captured individually; see code for full process.",
                    },
                    "Tool Calls": {},
                    "LLM Calls": self.llm_calls,
                },
                "Artifacts and Logs": {
                    "Inline Artifacts": {
                        "generated_script": script,
                    },
                    "Persisted Artifacts": persisted_artifacts,
                },
                "Validation": validation,
            },
        }
    
    def execute_complete_program(
        self,
        code,
        try_cnt,
        task,
        model_name,
        handbook_str,
        output_stem,
        progress_callback=None,
    ):
        count = 0
        while count < try_cnt:
            logging.info(f"Execute the code (trial # {count + 1}/{try_cnt})...")
            self._emit_progress(
                progress_callback,
                stage="code_execution",
                message=f"I am executing the generated download code (attempt {count + 1} of {try_cnt}).",
                data={"attempt": count + 1, "max_attempts": try_cnt},
            )
            # print(f"\n\n-------------- Running code (trial # {count + 1}/{try_cnt}) --------------\n\n")
            try:
                count += 1
                compiled_code = compile(code, 'Complete program', 'exec')
                exec(compiled_code, {"__builtins__": __builtins__})
                logging.info("Done. Code executed successfully without error.")
                self._emit_progress(
                    progress_callback,
                    stage="code_execution",
                    message="The generated download code executed successfully.",
                    data={"attempt": count, "max_attempts": try_cnt},
                )
                # print("\n\n--------------- Done ---------------\n\n")
                return code
            except Exception as err:
                logging.error(f"Error on trial {count}/{try_cnt}: {err}")
                logging.warning("Error on trial %s/%s: %s", count, try_cnt, err)
                if count == try_cnt:
                    logging.error(f"Failed to execute and debug the code within {try_cnt} times.")
                    self._emit_progress(
                        progress_callback,
                        stage="error",
                        message=(
                            "The generated code still failed after all repair attempts. "
                            "I will return the latest diagnostics in the task response."
                        ),
                        data={"attempt": count, "max_attempts": try_cnt, "error": str(err)},
                    )
                    return code
                
                self._emit_progress(
                    progress_callback,
                    stage="retry",
                    message=(
                        f"The generated code failed on attempt {count} of {try_cnt}. "
                        "I will send the error back to the model for a targeted repair."
                    ),
                    data={"attempt": count, "max_attempts": try_cnt, "error": str(err)},
                )
                debug_prompt = self.get_debug_prompt(
                    exception=err,
                    code=code,
                    task=task,
                    handbook_str=handbook_str,
                    output_stem=output_stem,
                )
                logging.info("Sending error information to LLM for debugging...")
                debug_response = self._create_chat_completion(
                    messages=[{"role": "system", "content": debug_prompt}],
                    model_name=model_name,
                )
                code = debug_response.choices[0].message.content
                code = self.extract_code_from_str(code)
                code = self.normalize_generated_code(code)
                logging.info("Received debugged code from LLM, retrying execution...")
                self._emit_progress(
                    progress_callback,
                    stage="retry",
                    message="The model returned a revised download program; I will retry execution.",
                    data={"next_attempt": count + 1, "max_attempts": try_cnt},
                )
        return code  # Return the last version of code after exhausting all tries
    
    

class CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr):
        return optionstr
