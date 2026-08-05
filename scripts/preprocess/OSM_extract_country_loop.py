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
#from pyrosm import OSM
import quackosm as qo

def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__)) # ds extract the directory of the current script (removing the filename)

    config_path = os.path.join(script_dir,'..','..', 'config.json') #ds construct the path to the config.json, .. means go up one directory level 

    with open(config_path, 'r') as config_fh: #ds r is for read mode, config_fh is the file handle
        config = json.load(config_fh) # ds config = contains the conents of the config file in python dictionary format
    return config


def normalize_country_slug(country_name):
    """Convert a country name into the slug format used by Geofabrik extracts."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(country_name).strip().lower())
    slug = slug.strip("-")
    return slug


def load_country_records(config):
    """Read Countries_list_osm.xlsx and return country/region records."""
    incoming_data_path = config['paths']['incoming_data']
    countries_path = os.path.join(incoming_data_path, "Countries_list_osm.xlsx")
    country_df = pd.read_excel(countries_path)

    required_columns = {"Tier region", "Geofabrik extract"}
    missing_columns = sorted(required_columns.difference(country_df.columns))
    if missing_columns:
        raise ValueError(
            "Countries_list_osm.xlsx is missing required columns: " + ", ".join(missing_columns)
        )

    records = []
    for _, row in country_df.iterrows():
        extract_name = str(row["Geofabrik extract"]).strip()
        region = str(row["Tier region"]).strip()
        if not extract_name:
            continue
        records.append({"country_name": extract_name, "region": region})

    return records


def build_tag_label(tags_filter):
    """Use the first tag key/value pair as the output label."""
    if not tags_filter:
        return "unspecified_tag"

    first_key, first_values = next(iter(tags_filter.items()))
    if isinstance(first_values, list):
        first_value = first_values[0] if first_values else "value" # ds if the list is empty, use "value" as the default
    else:
        first_value = first_values # ds if the value is not a list, use it directly

    return f"{first_key}_{first_value}"


def download_country_pbf(config, country_name, region="asia"):
    """Download a country extract into incoming_data/osm and return its local path."""
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


def process_country_pbf(config, country_name, tags_filter, region="asia"):
    """Download one country PBF, save it into incoming_data/osm, and convert it to parquet."""
    processed_data_path = config['paths']['data']
    slug = normalize_country_slug(country_name)
    tag_label = build_tag_label(tags_filter)

    in_path = download_country_pbf(config, country_name, region=region)
    if in_path is None:
        return None

    out_path = os.path.join(
        processed_data_path,
        "infrastructure",
        "osm_filter",
        f"{tag_label}_{slug}.parquet",
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    qo.convert_pbf_to_parquet( # ds convert the downloaded pbf file to parquet format
        pbf_path=in_path,
        result_file_path=out_path,
        tags_filter=tags_filter,
        explode_tags=False,
    )

    return out_path


def main(config, tags_filter, countries=None):
    """Loop through a supplied country list, falling back to the workbook if needed."""
    if countries is None:
        countries = load_country_records(config)

    for country_record in tqdm(countries, desc="Country OSM extracts"):
        if isinstance(country_record, dict):
            country_name = str(country_record.get("country_name", "")).strip() # ds get the country name from the record, if it is not present, use an empty string
            region = str(country_record.get("region", "asia")).strip() or "asia" # ds get the region from the record, if it is not present, use "asia" as the default
        else:
            country_name = str(country_record).strip()
            region = "asia"

        if not country_name:
            continue

        process_country_pbf(
            config=config,
            country_name=country_name,
            tags_filter=tags_filter,
            region=region,
        )


def main_single_file(config, osm_name, tags_filter, output_name):
    """Keep the old single-file behaviour available for one-off manual runs."""
    incoming_data_path = config['paths']['incoming_data']
    processed_data_path = config['paths']['data']
    in_path = os.path.join(incoming_data_path, "osm", osm_name + ".osm.pbf")
    out_path = os.path.join(processed_data_path, "infrastructure", output_name + ".parquet")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    qo.convert_pbf_to_parquet(
        pbf_path=in_path,
        result_file_path=out_path,
        tags_filter=tags_filter,
        explode_tags=False,
    )
 
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

if __name__ == '__main__':
    CONFIG = load_config()
    main(
        CONFIG,
        tags_filter={
            "aeroway": ["aerodrome"],
        },
    )