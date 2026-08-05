#!/usr/bin/env python
# coding: utf-8

import os
import json
import pandas as pd
import geopandas as gpd


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
    incoming_data_path = config["paths"]["incoming_data"]
    processed_data_path = config["paths"]["data"]

    airport_path = os.path.join(incoming_data_path, "infrastructure", "airport")
    output_path = os.path.join(processed_data_path, "infrastructure")
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
    wb_code_col = find_column(world_bank.columns, ["Orig"])
    oa_code_col = find_column(ourairports.columns, ["iata_code"])

    # ------------------------------------------------------------------
    # Find the coordinate columns in OurAirports
    # ------------------------------------------------------------------
    oa_lon_col = find_column(
        ourairports.columns,
        ["longitude_deg", "lon", "longitude", "x", "LONGITUDE"]
    )
    oa_lat_col = find_column(
        ourairports.columns,
        ["latitude_deg", "lat", "latitude", "y", "LATITUDE"]
    )

    # ------------------------------------------------------------------
    # If World Bank already has coordinate columns, try to find them
    # If not found, we'll rebuild geometry from the OurAirports match
    # ------------------------------------------------------------------
    wb_lon_candidates = ["longitude", "lon", "x", "Longitude", "LONGITUDE"]
    wb_lat_candidates = ["latitude", "lat", "y", "Latitude", "LATITUDE"]

    wb_lon_col = next((c for c in wb_lon_candidates if c in world_bank.columns), None)
    wb_lat_col = next((c for c in wb_lat_candidates if c in world_bank.columns), None)

    # ------------------------------------------------------------------
    # Clean airport codes
    # ------------------------------------------------------------------
    world_bank[wb_code_col] = world_bank[wb_code_col].astype(str).str.upper().str.strip()
    ourairports[oa_code_col] = ourairports[oa_code_col].astype(str).str.upper().str.strip()

    # Keep one row per code and only the columns we need
    oa_coords = (
        ourairports[[oa_code_col, oa_lon_col, oa_lat_col]]
        .dropna(subset=[oa_code_col, oa_lon_col, oa_lat_col])
        .drop_duplicates(subset=[oa_code_col])
        .rename(
            columns={
                oa_lon_col: "oa_lon",
                oa_lat_col: "oa_lat",
            }
        )
    )

    # Merge OurAirports coordinates onto the World Bank data
    merged = world_bank.merge(
        oa_coords,
        left_on=wb_code_col,
        right_on=oa_code_col,
        how="left",
    )

    # ------------------------------------------------------------------
    # Replace World Bank coordinates where a match exists
    # ------------------------------------------------------------------
    if wb_lon_col is not None and wb_lat_col is not None:
        merged[wb_lon_col] = merged["oa_lon"].combine_first(merged[wb_lon_col])
        merged[wb_lat_col] = merged["oa_lat"].combine_first(merged[wb_lat_col])

        # Rebuild geometry if needed
        if merged.geometry.name in merged.columns:
            merged = gpd.GeoDataFrame(
                merged,
                geometry=gpd.points_from_xy(merged[wb_lon_col], merged[wb_lat_col]),
                crs=world_bank.crs if world_bank.crs is not None else ourairports.crs,
            )
    else:
        # If the World Bank file has no coordinate columns, create a geometry from OurAirports coords
        merged = gpd.GeoDataFrame(
            merged,
            geometry=gpd.points_from_xy(merged["oa_lon"], merged["oa_lat"]),
            crs=ourairports.crs if ourairports.crs is not None else world_bank.crs,
        )

    # ------------------------------------------------------------------
    # Cleanup helper columns
    # ------------------------------------------------------------------
    merged = merged.drop(columns=[oa_code_col, "oa_lon", "oa_lat"], errors="ignore")

    # ------------------------------------------------------------------
    # Save output
    # ------------------------------------------------------------------
    out_file = os.path.join(output_path, "world_bank_airports_corrected.gpkg")
    merged.to_file(out_file, layer="airports_corrected", driver="GPKG")

    print(f"Saved corrected airports file to: {out_file}")

    airport_path = os.path.join(incoming_data_path, "infrastructure", "airport")
    output_path = os.path.join(processed_data_path, "infrastructure")
    os.makedirs(output_path, exist_ok=True)

    world_bank_file = os.path.join(airport_path, "filtered_airport_flows.gpkg")
    ourairports_file = os.path.join(airport_path, "OurAirport_filtered_airports.gpkg")

    flows = gpd.read_file(world_bank_file)
    ourairports = gpd.read_file(ourairports_file)

    def pick_col(columns, candidates):
        for c in candidates:
            if c in columns:
                return c
        raise KeyError(f"None of these columns were found: {candidates}")

    # Pick column names robustly
    orig_col = pick_col(flows.columns, ["Orig"])
    dest_col = pick_col(flows.columns, ["Dest", "DEST", "dest"])

    lat1_col = pick_col(flows.columns, ["Airport1La", "Airport1Lat", "airport1la"])
    lon1_col = pick_col(flows.columns, ["Airport1Lo", "Airport1Lon", "airport1lo"])
    lat2_col = pick_col(flows.columns, ["Airport2La", "Airport2Lat", "airport2la"])
    lon2_col = pick_col(flows.columns, ["Airport2Lo", "Airport2Lon", "airport2lo"])

    iata_col = pick_col(ourairports.columns, ["iata_code"])
    oa_lat_col = pick_col(ourairports.columns, ["latitude_deg", "latitude", "lat", "Latitude"])
    oa_lon_col = pick_col(ourairports.columns, ["longitude_deg", "longitude", "lon", "Longitude"])

    # Clean codes
    flows[orig_col] = flows[orig_col].astype(str).str.upper().str.strip()
    flows[dest_col] = flows[dest_col].astype(str).str.upper().str.strip()
    ourairports[iata_col] = ourairports[iata_col].astype(str).str.upper().str.strip()

    # Build lookup tables from OurAirports
    oa_lookup = (
        ourairports[[iata_col, oa_lat_col, oa_lon_col]]
        .dropna(subset=[iata_col])
        .drop_duplicates(subset=[iata_col])
        .set_index(iata_col)
    )

    lat_map = oa_lookup[oa_lat_col]
    lon_map = oa_lookup[oa_lon_col]

    # Replace World Bank coordinates with OurAirports coordinates
    flows[lat1_col] = flows[orig_col].map(lat_map)
    flows[lon1_col] = flows[orig_col].map(lon_map)
    flows[lat2_col] = flows[dest_col].map(lat_map)
    flows[lon2_col] = flows[dest_col].map(lon_map)

    from shapely.geometry import LineString

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
        crs="EPSG:4326"
)

    out_file = os.path.join(output_path, "airport_flows_world_bank_corrected.gpkg")
    flows.to_file(out_file, layer="airport_flows_corrected", driver="GPKG")

    print(f"Saved corrected file to: {out_file}")


if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)