#!/usr/bin/env python
# coding: utf-8

import os
import json
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString


def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "..", "..", "config.json")

    with open(config_path, "r") as config_fh:
        config = json.load(config_fh)

    return config


def find_column(columns, candidates):
    """
    Return the first column name from `candidates` that exists in `columns`.
    """
    for c in candidates:
        if c in columns:
            return c
    raise KeyError(f"Could not find any of these columns: {candidates}")


def main(config):

    crs="EPSG:4326"

    incoming_data_path = config["paths"]["incoming_data"]
    processed_data_path = config["paths"]["processed_data"]

    airport_path = os.path.join(incoming_data_path, "infrastructure", "airport")
    output_path = os.path.join(processed_data_path, "infrastructure", "airport")
    os.makedirs(output_path, exist_ok=True)

    # ------------------------------------------------------------------
    # Update these filenames if yours are different
    # ------------------------------------------------------------------
    world_bank_file = os.path.join(airport_path, "worldbank_filtered_airport_volume.gpkg")
    ourairports_file = os.path.join(airport_path, "OurAirport_filtered_airports.gpkg")

    # Read the layers/files
    world_bank = gpd.read_file(world_bank_file)
    ourairports = gpd.read_file(ourairports_file)

    # ------------------------------------------------------------------
    # Find the code columns
    # ------------------------------------------------------------------
    wb_code_col = "Orig" # ds wb = world bank
    oa_code_col = "iata_code" # ds oa= ourairport

    # ------------------------------------------------------------------
    # Find the coordinate columns in OurAirports
    # ------------------------------------------------------------------
    oa_lon_col = "longitude_deg"
    oa_lat_col = "latitude_deg"
    # ------------------------------------------------------------------
    # If World Bank already has coordinate columns, try to find them
    # If not found, we'll rebuild geometry from the OurAirports match
    # ------------------------------------------------------------------
    wb_lon_col = "Airport1Longitude"
    wb_lat_col = "Airport1Latitude"

    # ------------------------------------------------------------------
    # Clean airport codes
    # ------------------------------------------------------------------
    world_bank[wb_code_col] = world_bank[wb_code_col].astype(str).str.upper().str.strip()
    ourairports[oa_code_col] = ourairports[oa_code_col].astype(str).str.upper().str.strip()

    # Keep one row per code and only the columns we need
    oa_coords = (
        ourairports[[oa_code_col, oa_lon_col, oa_lat_col]] #ds keep only these columns 
        .dropna(subset=[oa_code_col, oa_lon_col, oa_lat_col]) # ds drop rows with missing values in these columns
        .drop_duplicates(subset=[oa_code_col]) 
        .rename(
            columns={
                oa_lon_col: "oa_lon",
                oa_lat_col: "oa_lat",
            }
        )
    )
    # ------------------------------------------------------------------
    # Build audit table with all rows from both datasets
    # ------------------------------------------------------------------
    wb_audit = world_bank.copy()
    oa_audit = ourairports.copy()

    wb_audit = wb_audit.rename(
        columns={
            wb_lon_col: "wb_orig_lon",
            wb_lat_col: "wb_orig_lat",
        }
    )

    oa_audit = oa_audit.rename(
        columns={
            oa_lon_col: "oa_orig_lon",
            oa_lat_col: "oa_orig_lat",
        }
    )

    audit = wb_audit.merge(
        oa_audit,
        left_on=wb_code_col,
        right_on=oa_code_col,
        how="outer",
        indicator=True,
        suffixes=("_wb", "_oa"),
    )

    audit["match_status"] = audit["_merge"].map({
        "both": "matched",
        "left_only": "world_bank_only",
        "right_only": "ourairports_only",
    })

    audit["merged_lon"] = audit["oa_orig_lon"].combine_first(audit["wb_orig_lon"])
    audit["merged_lat"] = audit["oa_orig_lat"].combine_first(audit["wb_orig_lat"])

    # Merge OurAirports coordinates onto the World Bank data
    merged = world_bank.merge(
        oa_coords,
        left_on=wb_code_col, # ds left, the dataset that determines the rows to keep (world_bank) and the column to match on (wb_code_col)
        right_on=oa_code_col, #ds right, the dataset you match to the left, using iata codes
        how="left", # ds keep everyhting from the wrold band and add oa coordinates 
    )

    # ------------------------------------------------------------------
    # Replace World Bank coordinates where a match exists
    # ------------------------------------------------------------------
    if wb_lon_col is not None and wb_lat_col is not None: # only is no data sets are missing
        merged[wb_lon_col] = merged["oa_lon"].combine_first(merged[wb_lon_col]) # replace the world bacnk cooridates with ourairports, if airport data is missing keep the original world bank data
        merged[wb_lat_col] = merged["oa_lat"].combine_first(merged[wb_lat_col])

        # Rebuild geometry if needed
        if merged.geometry.name in merged.columns:
            merged = gpd.GeoDataFrame(
                merged,
                geometry=gpd.points_from_xy(merged[wb_lon_col], merged[wb_lat_col]), # ds turn data into a geometry column
                crs=world_bank.crs if world_bank.crs is not None else ourairports.crs, # ds 
            )
    else:
        # If the World Bank file has no coordinate columns, create a geometry from OurAirports coords
        merged = gpd.GeoDataFrame(
            merged,
            geometry=gpd.points_from_xy(merged["oa_lon"], merged["oa_lat"]),
            crs = crs
        )

    # ------------------------------------------------------------------
    # Cleanup helper columns
    # ------------------------------------------------------------------
    merged = merged.drop(columns=[oa_code_col, "oa_lon", "oa_lat"], errors="ignore") # ds removes oa data

    # ------------------------------------------------------------------
    # Save output
    # ------------------------------------------------------------------
    out_file = os.path.join(output_path, "world_bank_airports_corrected.gpkg")
    merged.to_file(out_file, layer="airports_corrected", driver="GPKG")

    print(f"Saved corrected airports file to: {out_file}")

    airport_path = os.path.join(incoming_data_path, "infrastructure", "airport")
    output_path = os.path.join(processed_data_path, "infrastructure", "airport")
    os.makedirs(output_path, exist_ok=True)

    world_bank_file = os.path.join(airport_path, "worldbank_filtered_airport_flows.gpkg")
    ourairports_file = os.path.join(airport_path, "OurAirport_filtered_airports.gpkg")

    flows = gpd.read_file(world_bank_file)
    ourairports = gpd.read_file(ourairports_file)

    def pick_col(columns, candidates):
        for c in candidates:
            if c in columns:
                return c
        raise KeyError(f"None of these columns were found: {candidates}")

    # Pick column names robustly
    orig_col = "Orig"
    dest_col = "Dest"

    lat1_col = "Airport1La"
    lon1_col = "Airport1Lo"
    lat2_col = "Airport2La"
    lon2_col = "Airport2Lo"

    # Clean codes
    flows[orig_col] = flows[orig_col].astype(str).str.upper().str.strip()
    flows[dest_col] = flows[dest_col].astype(str).str.upper().str.strip()
    iata_col = "iata_code" 
    ourairports[iata_col] = ourairports[iata_col].astype(str).str.upper().str.strip()

    # Build lookup tables from OurAirports
    oa_lookup = (
        ourairports[[iata_col, oa_lat_col, oa_lon_col]]
        .dropna(subset=[iata_col])
        .drop_duplicates(subset=[iata_col]) # ds remove any with the same iata codes
        .set_index(iata_col)
    )

    lat_map = oa_lookup[oa_lat_col] # seperate lat look up 
    lon_map = oa_lookup[oa_lon_col]

    # Replace World Bank coordinates with OurAirports coordinates
    flows[lat1_col] = flows[orig_col].map(lat_map) # ds matches iata codes and replaces the wb data with oa
    flows[lon1_col] = flows[orig_col].map(lon_map)
    flows[lat2_col] = flows[dest_col].map(lat_map)
    flows[lon2_col] = flows[dest_col].map(lon_map)


    # Rebuild the line geometry using the corrected coordinates
    flows["geometry"] = flows.apply(
        lambda row: LineString([
            (row[lon1_col], row[lat1_col]),
            (row[lon2_col], row[lat2_col])
        ]),
        axis=1
    )

    flows = gpd.GeoDataFrame(
        flows,
        geometry="geometry",
        crs = crs
        
)

    out_file = os.path.join(output_path, "airport_flows_world_bank_corrected.gpkg")
    flows.to_file(out_file, layer="airport_flows_corrected", driver="GPKG")

    print(f"Saved corrected file to: {out_file}")

    #excel file with oa, wb oringal data and the merged point cooridantes and outlines any points that did not match up from both datasets
    excel_file = os.path.join(output_path, "airport_coordinate_audit.xlsx")

    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        audit.to_excel(writer, sheet_name="all_airports_audit", index=False)
        merged.to_excel(writer, sheet_name="world_bank_corrected", index=False)

    print(f"Saved audit Excel file to: {excel_file}")

if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)