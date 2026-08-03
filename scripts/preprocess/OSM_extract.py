#!/usr/bin/env python
# coding: utf-8
import sys
import os
import re
import json
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

def main(config,osm_name,tags_filter,output_name):
    incoming_data_path = config['paths']['incoming_data'] #ds extract the incoming_data path from the config dictionary
    processed_data_path = config['paths']['data'] 
    in_path = os.path.join(incoming_data_path,"osm", osm_name + ".osm.pbf") # ds construct the path to the input pbf file using the incoming_data_path and osm_name 
    out_path = os.path.join(processed_data_path, "infrastructure", output_name + ".parquet")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    qo.convert_pbf_to_parquet( # ds convert the pbf file to parquet format using the quackosm library
        pbf_path=in_path, # ds path to the input pbf (Protocolbuffer Binary Format.) file
        result_file_path=out_path, # ds path to the output parquet file
        tags_filter=tags_filter, # ds filter the data based on specific tags in the OSM data (uses the quackosm library to filter the data)
        explode_tags=False, # ds keep the tags as a list in the output parquet file instead of creating separate rows for each tag
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
        osm_name='asia-latest',
        tags_filter={
            "aeroway": ["aerodrome"],
        },
        output_name='asia_osm_aeroway_aerodrome',
    )