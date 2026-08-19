#!/usr/bin/env python
# coding: utf-8

import os
import json
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point

def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "..", "..", "config.json")

    with open(config_path, "r") as config_fh:
        config = json.load(config_fh)

    return config


def filter_and_convert_airports(incoming_airport_path, incoming_data_path, output_path, airport_types):
    #Filter OurAirports data and write both CSV and GeoPackage outputs.

    airports_file = os.path.join(incoming_airport_path, "Ourairports.csv")
    countries_file = os.path.join(incoming_data_path, "Countries_list.xlsx")
    filtered_csv = os.path.join(output_path, "filtered_Ourairports.csv")
    filtered_gpkg = os.path.join(output_path, "filtered_Ourairports.gpkg")

    countries = pd.read_excel(countries_file, usecols="B", engine="openpyxl") # ds read column B (ISO2 country codes) from the countires in the study area, saved in countires_list 
    study_country_codes = {
        code
        for code in countries.iloc[:, 0].dropna().astype(str).str.strip().str.upper()
        if len(code) == 2 and code.isalpha()
    } # ds clean the country codes to be 2 letter ISO codes and upper case

    airports = pd.read_csv(airports_file)
    required = {"type", "iso_country", "latitude_deg", "longitude_deg"}
    missing = required - set(airports.columns)
    if missing:
        raise ValueError(f"airports.csv is missing required columns: {sorted(missing)}")

    # ds clean ISO2 Country codes in OurAiport dataset, and filter airport types
    filtered = airports[
        airports["iso_country"].astype(str).str.strip().str.upper().isin(study_country_codes)
        & airports["type"].isin(airport_types)
    ].copy()
    filtered.to_csv(filtered_csv, index=False) 


    points = filtered.dropna(subset=["latitude_deg", "longitude_deg"]).copy() # remove any rows with missing coordinates
    gdf = gpd.GeoDataFrame(
        points,
        geometry=gpd.points_from_xy(points["longitude_deg"], points["latitude_deg"]),
        crs="EPSG:4326",
    )
    if os.path.exists(filtered_gpkg): # ds remove the file if it already exists 
        os.remove(filtered_gpkg)
    gdf.to_file(filtered_gpkg, layer="airports", driver="GPKG", index=False)

    print(f"Read {len(airports):,} airport rows")
    print(f"Wrote {len(filtered):,} filtered rows to: {filtered_csv}")
    print(f"Wrote {len(gdf):,} airport points to: {filtered_gpkg}")


def merge_world_bank_with_ourairports(world_bank, ourairports, output_path, airport_path, crs):
    #Merge World Bank and OurAirports data, keeping world bank rows and adding OurAirports coordinates where available. 
    
    wb_code_col = "Orig"  # ds wb = world bank
    oa_code_col = "iata_code"  # ds oa= ourairport

    oa_lon_col = "longitude_deg"
    oa_lat_col = "latitude_deg"
    
    wb_lon_col = "Airport1Longitude"
    wb_lat_col = "Airport1Latitude"

    # clean airport codes 
    world_bank[wb_code_col] = world_bank[wb_code_col].astype(str).str.upper().str.strip()
    ourairports[oa_code_col] = ourairports[oa_code_col].astype(str).str.upper().str.strip()

    # Keep one row per code and only the columns we need
    oa_coords = (
        ourairports[[oa_code_col, oa_lon_col, oa_lat_col]]  # ds keep only these columns 
        .dropna(subset=[oa_code_col, oa_lon_col, oa_lat_col])  # ds drop rows with missing values in these columns
        .drop_duplicates(subset=[oa_code_col]) 
        .rename(
            columns={
                oa_lon_col: "oa_lon",
                oa_lat_col: "oa_lat",
            }
        )
    )

    # Create an audit table to track matches and mismatches between the two datasets
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
        left_on=wb_code_col,  # ds left, the dataset that determines the rows to keep (world_bank) and the column to match on (wb_code_col)
        right_on=oa_code_col,  # ds right, the dataset you match to the left, using iata codes
        how="left",  # ds keep everything from the world bank and add oa coordinates 
    )

    
    if wb_lon_col is not None and wb_lat_col is not None:  # only if no data sets are missing
        merged[wb_lon_col] = merged["oa_lon"].combine_first(merged[wb_lon_col])  # ds replace the world bank coordinates with ourairports, if airport data is missing keep the original world bank data
        merged[wb_lat_col] = merged["oa_lat"].combine_first(merged[wb_lat_col])

        # Rebuild geometry if needed
        if merged.geometry.name in merged.columns:
            merged = gpd.GeoDataFrame(
                merged,
                geometry=gpd.points_from_xy(merged[wb_lon_col], merged[wb_lat_col]),  # ds turn data into a geometry column
                crs=world_bank.crs if world_bank.crs is not None else ourairports.crs,  # ds 
            )
    else:
        # If the World Bank file has no coordinate columns, create a geometry from OurAirports coords
        merged = gpd.GeoDataFrame(
            merged,
            geometry=gpd.points_from_xy(merged["oa_lon"], merged["oa_lat"]),
            crs=crs
        )

    
    merged = merged.drop(columns=[oa_code_col, "oa_lon", "oa_lat"], errors="ignore")  # ds removes oa data

    # save output to a GeoPackage file
    out_file = os.path.join(output_path, "world_bank_airports_corrected.gpkg")
    merged.to_file(out_file, layer="airports_corrected", driver="GPKG")

    print(f"Saved corrected airports file to: {out_file}")

    return audit, merged, wb_code_col, oa_code_col, oa_lat_col, oa_lon_col


def process_airport_flows(airport_path, output_path, ourairports, oa_lat_col, oa_lon_col, crs):
    #Move world bank airport flows to corrected coordiantes
    world_bank_file = os.path.join(airport_path, "worldbank_filtered_airport_flows.gpkg")

    flows = gpd.read_file(world_bank_file)

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
    iata_col = "Orig"
    ourairports[iata_col] = ourairports[iata_col].astype(str).str.upper().str.strip()

    # Build lookup tables from OurAirports
    oa_lookup = (
        ourairports[[iata_col, "Airport1Latitude", "Airport1Longitude"]]
        .dropna(subset=[iata_col])
        .drop_duplicates(subset=[iata_col])  # ds remove any with the same iata codes
        .set_index(iata_col)
    )

    lat_map = oa_lookup["Airport1Latitude"]  # ds seperate lat look up 
    lon_map = oa_lookup["Airport1Longitude"]

    # Replace World Bank coordinates with OurAirports coordinates
    flows[lat1_col] = flows[orig_col].map(lat_map)  # ds matches iata codes and replaces the wb data with oa
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
        crs=crs
    )

    out_file = os.path.join(output_path, "airport_flows_world_bank_corrected.gpkg")
    flows.to_file(out_file, layer="airport_flows_corrected", driver="GPKG")

    print(f"Saved corrected file to: {out_file}")


def label_audit_columns(frame, world_bank, ourairports):
    # add sources to columns names of the audit
    world_bank_columns = set(world_bank.columns)
    ourairports_columns = set(ourairports.columns)
    renamed = {}
    for column in frame.columns:
        if column in {"match_status", "_merge", "merged_lon", "merged_lat"}:
            renamed[column] = {
                "_merge": "join_result",
                "match_status": "match_status",
                "merged_lon": "merged_longitude",
                "merged_lat": "merged_latitude",
            }[column]
        elif column.endswith("_wb"):
            renamed[column] = f"WorldBank_{column[:-3]}"
        elif column.endswith("_oa"):
            renamed[column] = f"OurAirports_{column[:-3]}"
        elif column in world_bank_columns and column not in ourairports_columns:
            renamed[column] = f"WorldBank_{column}"
        elif column in ourairports_columns and column not in world_bank_columns:
            renamed[column] = f"OurAirports_{column}"
    return frame.rename(columns=renamed)


def apply_coordinate_corrections(incoming_data_path, output_path, world_bank_layer=None):
    """Apply manual airport corrections to the data layer when an audit file exists.
       The audit may contain rows marked as 'remove', 'moved', or 'correct', done through manually checking. 
    """
    audit_file = os.path.join(incoming_data_path, "infrastructure", "airport", "airport_coordinate_audit_corrected.xlsx")

    # Check for audit correrctions file 
    if not os.path.exists(audit_file):
        print(f"Skipping manual airport corrections: {audit_file} not found.")
        return world_bank_layer

    audit = pd.read_excel(audit_file)
    if "Check" not in audit.columns:
        raise ValueError(f"Manual audit file is missing the 'Check' column: {audit_file}")

    # Separate rows by correction status
    moved_rows = audit[audit["Check"].astype(str).str.lower() == "moved"].copy()
    remove_rows = audit[audit["Check"].astype(str).str.lower() == "remove"].copy()
    correct_rows = audit[audit["Check"].astype(str).str.lower() == "correct"].copy()

    # Save the corrected audit workbook as a record of the applied manual corrections.
    corrected_audit = pd.concat([moved_rows, correct_rows], ignore_index=True) 
    corrected_audit_file = os.path.join(output_path, "airport_coordinate_audit_applied.xlsx")
    corrected_audit.to_excel(corrected_audit_file, index=False)

    print(f"Moved rows: {len(moved_rows)}")
    print(f"Removed rows: {len(remove_rows)}")
    print(f"Correct rows: {len(correct_rows)}")
    print(f"Saved corrected audit to: {corrected_audit_file}")

    if world_bank_layer is None:
        return corrected_audit

    merged = world_bank_layer.copy()

    id_col = "Orig"
    if id_col not in merged.columns:
        raise KeyError(f"Missing required airport ID column: {id_col}")

    audit_id_candidates = [
        id_col,
        f"WorldBank_{id_col}",
        f"OurAirports_{id_col}",
    ]

    def find_audit_id_column(frame):
        for column in audit_id_candidates:
            if column in frame.columns:
                return column
        return None

    remove_id_col = find_audit_id_column(remove_rows)
    correction_rows = pd.concat([moved_rows, correct_rows], ignore_index=True)
    correction_id_col = find_audit_id_column(correction_rows)

    if remove_id_col is None and not remove_rows.empty:
        raise KeyError(f"Could not find an airport ID column in the removal audit. Expected one of: {audit_id_candidates}")
    if correction_id_col is None and not correction_rows.empty:
        raise KeyError(f"Could not find an airport ID column in the correction audit. Expected one of: {audit_id_candidates}")

    def normalize_code(value):
        return str(value).strip().upper() if pd.notna(value) else ""

    remove_ids = set()
    if remove_id_col is not None:
        remove_ids = {
            normalize_code(code)
            for code in remove_rows[remove_id_col].tolist()
            if pd.notna(code) and str(code).strip() != ""
        }
    if remove_ids:
        merged = merged[
            ~merged[id_col].astype(str).str.upper().str.strip().isin(remove_ids)
        ].copy()

    corrections = correction_rows
    if correction_id_col is not None and not corrections.empty and any(col in corrections.columns for col in ["corrected_lon", "corrected_lat"]):
        correction_map = corrections[[correction_id_col, "corrected_lon", "corrected_lat"]].dropna(subset=[correction_id_col, "corrected_lon", "corrected_lat"])
        if not correction_map.empty:
            correction_map[correction_id_col] = correction_map[correction_id_col].map(normalize_code)
            for _, row in correction_map.iterrows():
                key = normalize_code(row[correction_id_col])
                matches = merged[merged[id_col].astype(str).str.upper().str.strip() == key]
                for idx in matches.index:
                    merged.at[idx, "Airport1Longitude"] = float(row["corrected_lon"])
                    merged.at[idx, "Airport1Latitude"] = float(row["corrected_lat"])
                    if "geometry" in merged.columns:
                        merged.at[idx, "geometry"] = Point(float(row["corrected_lon"]), float(row["corrected_lat"]))

    out_file = os.path.join(output_path, "world_bank_airports_corrected.gpkg")
    merged.to_file(out_file, layer="airports_corrected", driver="GPKG")
    print(f"Saved final corrected airport layer to: {out_file}")

    return merged


def main(config):

    crs="EPSG:4326"

    incoming_data_path = config["paths"]["incoming_data"]
    processed_data_path = config["paths"]["processed_data"]

    airport_path = os.path.join(incoming_data_path, "infrastructure", "airport")
    output_path = os.path.join(processed_data_path, "infrastructure", "airport")
    os.makedirs(output_path, exist_ok=True)

    #Extract AIRPORT_TYPES from the the OurAirport dataset
    AIRPORT_TYPES = {"medium_airport", "large_airport", "small_airport"}
    filter_and_convert_airports(airport_path, incoming_data_path, output_path, AIRPORT_TYPES)

    # dataset [paths] (filtered to only countires in the study area)
    world_bank_file = os.path.join(airport_path, "worldbank_filtered_airport_volume.gpkg")
    ourairports_file = os.path.join(output_path, "filtered_Ourairports.gpkg")

    # Read the layers/files
    world_bank = gpd.read_file(world_bank_file)
    ourairports = gpd.read_file(ourairports_file)

    audit, merged, wb_code_col, oa_code_col, oa_lat_col, oa_lon_col = merge_world_bank_with_ourairports(
        world_bank, ourairports, output_path, airport_path, crs
    )

    # Apply any manual airport removals/corrections from the audit file, if present.
    merged = apply_coordinate_corrections(incoming_data_path, output_path, merged)

    process_airport_flows(airport_path, output_path, merged, oa_lat_col, oa_lon_col, crs)

    # excel file with oa, wb original data and the merged point coordinates and outlines any points that did not match up from both datasets
    excel_file = os.path.join(output_path, "airport_coordinate_audit.xlsx")
    match_summary = (
        audit["match_status"]
        .value_counts()
        .reindex(["matched", "ourairports_only", "world_bank_only"], fill_value=0)
        .rename_axis("match_status")
        .reset_index(name="count")
    )

    audit_export = label_audit_columns(audit, world_bank, ourairports)
    merged_export = merged.rename(
        columns={column: f"WorldBank_{column}" for column in merged.columns}
    )

    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        audit_export.to_excel(writer, sheet_name="all_airports_audit", index=False)
        merged_export.to_excel(writer, sheet_name="world_bank_corrected", index=False)
        match_summary.to_excel(writer, sheet_name="match_summary", index=False)

    print(f"Saved audit Excel file to: {excel_file}")

if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)