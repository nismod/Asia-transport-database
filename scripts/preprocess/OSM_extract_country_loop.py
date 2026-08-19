#!/usr/bin/env python
# coding: utf-8
import sys
import os
import re
import json
from urllib.request import urlretrieve
from urllib.error import HTTPError, URLError
import pandas as pd
import igraph as ig
import geopandas as gpd
from tqdm import tqdm
tqdm.pandas()
#from pyrosm import OSM # ds package wasnt working locally for me, so commented out temparorarily
import quackosm as qo

def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__)) # ds extract the directory of the current script (removing the filename)

    config_path = os.path.join(script_dir,'..','..', 'config.json') #ds construct the path to the config.json, .. means go up one directory level 

    with open(config_path, 'r') as config_fh: #ds r is for read mode, config_fh is the file handle
        config = json.load(config_fh) # ds config = contains the conents of the config file in python dictionary format
    return config


def normalize_country_slug(country_name): # ds function to convert a country name into the slug format used by Geofabrik extracts
    #Convert a country name into the slug format used by Geofabrik extracts.

    slug = re.sub(r"[^a-z0-9]+", "-", str(country_name).strip().lower()) # ds convert the country name to lowercase, remove leading/trailing whitespace, and replace any non-alphanumeric characters with a hyphen
    slug = slug.strip("-") # ds remove any leading/trailing hyphens
    return slug


def load_country_records(config): # ds function to read the Countries_list_osm.xlsx file and return a list of the geofabrik name for each country and the corresponding region   
    #Read Countries_list_osm.xlsx and return country/region records.

    incoming_data_path = config['paths']['incoming_data']
    countries_path = os.path.join(incoming_data_path, "Countries_list_osm.xlsx")
    country_df = pd.read_excel(countries_path) # ds read the excel file into a pandas dataframe

    required_columns = {"Tier region", "Geofabrik extract"} # ds Tier region is the region of the country (e.g. asia, europe, etc.), Geofabrik extract is the name that extracts a given country in osm
    missing_columns = sorted(required_columns.difference(country_df.columns)) # ds check if required columns are present, if not raise an error
    if missing_columns:
        raise ValueError(
            "Countries_list_osm.xlsx is missing required columns: " + ", ".join(missing_columns)
        )

    records = [] # ds initiate an empty list to store the country records
    for _, row in country_df.iterrows(): 
        extract_name = str(row["Geofabrik extract"]).strip() # ds extract a column of all the geofabrik names for each country and remove leading/trailing whitespace
        region = str(row["Tier region"]).strip()
        if not extract_name: # skip the rest of the iteration if not present
            continue
        records.append({"country_name": extract_name, "region": region}) 

    return records


def build_tag_label(tags_filter):
    #Use the first tag key/value pair as the output label.

    if not tags_filter:
        return "unspecified_tag"

    first_key, first_values = next(iter(tags_filter.items())) #breaks tags into key and values
    if isinstance(first_values, list):
        first_value = first_values[0] if first_values else "value" # ds if the list is empty, use "value" as the default
    else:
        first_value = first_values # ds if the value is not a list, use it directly

    return f"{first_key}_{first_value}"


def download_country_pbf(config, country_name, region):
    #Download a country extract into incoming_data/osm and return its local path.

    incoming_data_path = config['paths']['incoming_data']
    osm_dir = os.path.join(incoming_data_path, "osm")
    

    slug = normalize_country_slug(country_name)
    download_url = f"https://download.geofabrik.de/{region}/{slug}-latest.osm.pbf"
    local_path = os.path.join(osm_dir, f"{slug}-latest.osm.pbf")

    if os.path.exists(local_path): # check whether the file already exists in the local path, if it does, print a message and return the local path
        print(f"Using existing country extract: {local_path}")
        return local_path

    print(f"Downloading {country_name} from {download_url}") # ds lists the country name and the download url for the pbf file
    try: # ds download the file from the url and save it to the local path, if there is an error, print a message and return None
        urlretrieve(download_url, local_path)
    except (HTTPError, URLError) as exc:
        print(f"Skipping {country_name}: {exc}")
        return None

    return local_path # local path is location of the downloaded file


def process_country_pbf(config, country_name, tags_filter, region):
    #Download one country PBF, save it into incoming_data/osm, and convert it to parquet.

    processed_data_path = config["paths"]["processed_data"]
    slug = normalize_country_slug(country_name)
    tag_label = build_tag_label(tags_filter)

    in_path = download_country_pbf(config, country_name, region)
    if in_path is None:
        return None

    out_path = os.path.join(
        processed_data_path,
        "infrastructure",
        "osm_filter",
        tag_label,
        f"{tag_label}_{slug}.parquet",
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True) # ds create the directory for the output path if it does not exist

    # needed for storage issues on ARC ( stop using home temporay storage)
    temp_root = os.environ.get("SLURM_TMPDIR", "/data/cenv-opsis-astdb/mans4968/asia-transport-database/files")
    temp_work_dir = os.path.join(temp_root, "quackosm_tmp")
    os.makedirs(temp_work_dir, exist_ok=True)

    old_cwd = os.getcwd()
    os.chdir(temp_work_dir)

    try:
        qo.convert_pbf_to_parquet(
            pbf_path=in_path,
            result_file_path=out_path,
            tags_filter=tags_filter,
            keep_all_tags=True,
            explode_tags=True,
        )

        # Read the extracted OSM data
        gdf = gpd.read_parquet(out_path)

        # Only keep the attributes needed in the final dataset
        wanted_columns = [
            "feature_id",
            "geometry",
            "name",
            "name:en",
            "iata",
            "icao",
        ]

        # Add an empty column if a tag does not exist in this country
        for column in wanted_columns:
            if column not in gdf.columns:
                gdf[column] = None

        # Keep only the required columns
        gdf = gdf[wanted_columns]

        # Save over the original parquet
        gdf.to_parquet(out_path, index=False)

    except Exception as e:
        print(f"Failed to process {country_name}: {e}")
        return None

    finally:
        os.chdir(old_cwd)

    return out_path  

def write_tag_summary(processed_data_path, tag_labels):
    #Write the list of tag labels used in this run.

    summary_path = os.path.join(
        processed_data_path,
        "infrastructure",
        "osm_filter",
        "tags_used.txt",
    )
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)

    with open(summary_path, "w", encoding="utf-8") as fh:
        for tag_label in tag_labels:
            fh.write(f"{tag_label}\n")

    print(f"Saved tag summary to {summary_path}")


def combine_country_parquets(config, tags_filter, append_existing=False, new_files=None):
    #Combine all country parquet files for one tag into a single continent-wide file.

    processed_data_path = config["paths"]["processed_data"]
    tag_label = build_tag_label(tags_filter)

    input_dir = os.path.join(
        processed_data_path,
        "infrastructure",
        "osm_filter",
        tag_label,
    )

    # ds create the tag directory if it does not already exist
    os.makedirs(input_dir, exist_ok=True)

    combined_name = f"{tag_label}_asia_pacific.parquet"

    if append_existing:
        # When processing a manually supplied country list, retain the existing
        # Asia-Pacific file and append only the files produced in this run.
        parquet_files = [
            path for path in (new_files or [])
            if os.path.isfile(path) and os.path.basename(path) != combined_name
        ]
    else:
        parquet_files = sorted(
            os.path.join(input_dir, f)
            for f in os.listdir(input_dir)
            if f.endswith(".parquet") and f != combined_name
        )

    existing_path = os.path.join(input_dir, combined_name)
    if append_existing and os.path.exists(existing_path):
        parquet_files.insert(0, existing_path)

    if not parquet_files:
        print(f"No parquet files found in {input_dir}")
        return

    gdfs = [gpd.read_parquet(f) for f in parquet_files]

    combined = gpd.GeoDataFrame(
        pd.concat(gdfs, ignore_index=True),
        geometry="geometry",
        crs=gdfs[0].crs,
    )

    if append_existing and "feature_id" in combined.columns:
        combined = combined.drop_duplicates(subset="feature_id", keep="last")

    output_path = os.path.join(input_dir, combined_name)
    combined.to_parquet(output_path)

    print(f"Saved combined parquet to {output_path}")

def main(config):
    #Loop through a supplied country list

    countries = None # if =None use spreadsheet countries_list, otherwise ["vietnam"]
    # ds list of OSM tag filters to extract (each tag is processed separately)
    
    tag_filters = [
        #{"aeroway": ["aerodrome"]},
        #{"aeroway": ["terminal"]},
        #{"aeroway": ["runway"]},
        {"aeroway": ["taxiway"]},
        
    ]

    if countries is None:
        countries = load_country_records(config)

    # ds loop through each tag filter separately so each tag has its own output folder
    for tags_filter in tag_filters:

        tag_label = build_tag_label(tags_filter)

        tag_output_dir = os.path.join(
            config["paths"]["processed_data"],
            "infrastructure",
            "osm_filter",
            tag_label,
        )

        # Preserve existing outputs and add/update files in the tag folder.
        os.makedirs(tag_output_dir, exist_ok=True)

        processed_files = []

        # ds loop through all countries for the current tag
        for country_record in tqdm(
            countries,
            desc=f"Country OSM extracts ({tag_label})"
        ):
            if isinstance(country_record, dict):
                country_name = str(
                    country_record.get("country_name", "")
                ).strip() # ds get the country name from the record, if it is not present, use an empty string

                region = str(
                    country_record.get("region", "asia")
                ).strip() or "asia" # ds get the region from the record, if it is not present, use "asia" as the default

            else:
                country_name = str(country_record).strip()
                region = "asia"

            if not country_name:
                continue

            output_path = process_country_pbf(
                config=config,
                country_name=country_name,
                tags_filter=tags_filter,
                region=region,
            )
            if output_path is not None:
                processed_files.append(output_path)

        # ds combine all country parquet files for the current tag
        combine_country_parquets(
            config,
            tags_filter,
            append_existing=countries is not None,
            new_files=processed_files,
        )

if __name__ == '__main__':
    CONFIG = load_config()
    main(CONFIG)

''' list of tags to filter for  OSM data
                #"aeroway": ["terminal"],
                #"building": ["terminal", "transportation"],
                # "landuse": ["industrial"],
                #"industrial": ["port"],
                #"port:type" : ["inland_port"],
                #"amenity": ["ferry_terminal"],
                #"building": ["ferry_terminal"],
                #"port": True,
                #"aeroway": ["aerodrome"],
'''