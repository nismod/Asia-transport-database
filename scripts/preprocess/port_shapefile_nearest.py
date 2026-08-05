#!/usr/bin/env python
# coding: utf-8

import os
import re
import geopandas as gpd
import pandas as pd

from utils_new import load_config


EXTRA_ASIA_ISO3 = {"RUS", "TUR", "GEO", "ARM", "AZE", "CYP"}
REGION_COUNTRY_ALLOWLIST = {
    "northern_russia": {"RUS"},
    "pacific": None,
}

COUNTRY_ALIASES = {
    "south korea": "Korea, Republic of",
    "korea republic of": "Korea, Republic of",
    "korea": "Korea, Republic of",
    "republic of korea": "Korea, Republic of",
    "south korea republic of": "Korea, Republic of",
    "north korea": "Korea (Democratic People's Republic of)",
    "korea democratic people republic of": "Korea (Democratic People's Republic of)",
    "democratic peoples republic of korea": "Korea (Democratic People's Republic of)",
    "north korea democratic peoples republic of": "Korea (Democratic People's Republic of)",
    "russia": "Russian Federation",
    "russian federation": "Russian Federation",
    "ussr": "Russian Federation",
    "soviet union": "Russian Federation",
    "viet nam": "Viet Nam",
    "vietnam": "Viet Nam",
    "turkey": "Türkiye",
    "turkiye": "Türkiye",
    "turkish republic": "Türkiye",
    "türkiye": "Türkiye",
    "turk republic": "Türkiye",
    "turkey republic": "Türkiye",
    "taiwan": "Taiwan, Province of China",
    "taiwan province of china": "Taiwan, Province of China",
    "hong kong": "Hong Kong",
    "macao": "Macao",
    "macau": "Macao",
    "brunei": "Brunei Darussalam",
    "brunei darussalam": "Brunei Darussalam",
    "east timor": "Timor-Leste",
    "timor leste": "Timor-Leste",
    "palestine": "Palestine, State of",
    "palestine state of": "Palestine, State of",
    "iran": "Iran (Islamic Republic of)",
    "iran islamic republic of": "Iran (Islamic Republic of)",
    "micronesia": "Micronesia (Federated States of)",
    "micronesia federated states of": "Micronesia (Federated States of)",
    "syrian arab republic": "Syrian Arab Republic",
    "the netherlands": "Netherlands",
    "netherlands": "Netherlands",
    "holland": "Netherlands",
    "dutch republic": "Netherlands",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
}


def normalize_country(value):
    """Normalize a country name or ISO token for robust matching."""
    if pd.isna(value):
        return ""

    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def load_country_iso_allow_list(countries_xlsx):
    """Load the country workbook and return the ISO3 allow-list and name lookup."""
    if not os.path.exists(countries_xlsx):
        raise FileNotFoundError(f"Countries workbook not found: {countries_xlsx}")

    ref_df = pd.read_excel(countries_xlsx)

    # Normalise the workbook columns in case the sheet changes format.
    ref_df.columns = [str(col).strip() for col in ref_df.columns]
    if "Country/Territory" in ref_df.columns and "Column3" in ref_df.columns:
        ref_df = ref_df.rename(columns={"Country/Territory": "country_name", "Column3": "iso3"})
    elif "country" in ref_df.columns and "iso3" in ref_df.columns:
        ref_df = ref_df.rename(columns={"country": "country_name"})
    else:
        # Handle a generic workbook layout by taking the first string column as the country name
        # and the last ISO3-like column as the ISO3 code.
        ref_df = ref_df.copy()
        ref_df.columns = ["country_name", "iso2", "iso3"]

    ref_df["country_name"] = ref_df["country_name"].astype(str).str.strip()
    ref_df["iso3"] = ref_df["iso3"].fillna("").astype(str).str.strip().str.upper()

    allowed_iso3 = {iso for iso in ref_df["iso3"].dropna().astype(str).str.upper() if iso}

    country_lookup = {}
    for row in ref_df.itertuples(index=False):
        country_lookup[normalize_country(row.country_name)] = row.iso3

    # Add a small alias map to catch common label variations in the landuse source.
    for alias, canonical in COUNTRY_ALIASES.items():
        canonical_norm = normalize_country(canonical)
        if canonical_norm in country_lookup:
            country_lookup[normalize_country(alias)] = country_lookup[canonical_norm]

    return ref_df, country_lookup, allowed_iso3


def country_to_iso3(country_value, country_lookup):
    """Map a landuse country string to the workbook ISO3 code when possible."""
    if pd.isna(country_value):
        return pd.NA

    value = normalize_country(country_value)
    if not value:
        return pd.NA

    return country_lookup.get(value, pd.NA)


def apply_region_country_filter(landuse_gdf, region_name, allowed_iso3):
    """Keep only the ISO3 countries allowed for a specific block/region.

    This is an opt-in guard for block outputs. The global landuse export should
    continue to use the workbook allow-list alone unless a specific region is
    supplied and you want an extra country gate for that extract.
    """
    if region_name is None:
        return landuse_gdf.loc[landuse_gdf["country_iso3"].isin(allowed_iso3)].copy()

    region_allowed = REGION_COUNTRY_ALLOWLIST.get(region_name)
    if region_allowed is None:
        return landuse_gdf.loc[landuse_gdf["country_iso3"].isin(allowed_iso3)].copy()

    keep_iso3 = set(allowed_iso3).intersection(region_allowed)
    return landuse_gdf.loc[landuse_gdf["country_iso3"].isin(keep_iso3)].copy()


def build_nearest_port_lookup(landuse_gdf, network_ports):
    """Use nearest-point matching against the full processed network port node layer."""
    # Only keep port nodes for the nearest-port lookup.
    network_ports = network_ports.loc[network_ports["infra"] == "port"].copy()

    # Reproject both layers to a metric CRS so the nearest-distance calculation is meaningful.
    projected_landuse = landuse_gdf.to_crs("EPSG:3857")
    projected_network = network_ports.to_crs("EPSG:3857")

    candidate_ports = projected_network[
        ["id", "name", "country", "iso3", "geometry"]
    ].rename(
        columns={
            "id": "nearest_port_id",
            "name": "nearest_port_name",
            "country": "nearest_port_country",
            "iso3": "nearest_port_iso3",
        }
    )

    nearest = gpd.sjoin_nearest(
        projected_landuse,
        candidate_ports,
        how="left",
        distance_col="distance_m",
    )

    # Restore the original CRS on the output layer for writing back to disk.
    nearest = nearest.set_geometry("geometry")
    nearest = nearest.to_crs(landuse_gdf.crs)

    return nearest


def main(config):
    incoming_data_path = config["paths"]["incoming_data"]
    processed_data_path = config["paths"]["data"]

    landuse_gpkg = os.path.join(incoming_data_path, "infrastructure", "Port", "port_landuse.gpkg")
    countries_xlsx = os.path.join(incoming_data_path, "Countries_list.xlsx")
    network_gpkg = os.path.join(processed_data_path, "infrastructure", "global_maritime_network_PROVA_NEW1.gpkg")

    output_dir = os.path.join(processed_data_path, "infrastructure")
    os.makedirs(output_dir, exist_ok=True)

    landuse_gdf = gpd.read_file(landuse_gpkg)
    network_ports = gpd.read_file(network_gpkg, layer="nodes")

    # Load country ISO reference list.
    country_ref, country_lookup, allowed_iso3 = load_country_iso_allow_list(countries_xlsx)
    allowed_iso3 = allowed_iso3.union(EXTRA_ASIA_ISO3)

    # Map landuse country names into the workbook ISO3 set first.
    landuse_gdf["country_iso3"] = landuse_gdf["country"].apply(lambda x: country_to_iso3(x, country_lookup))
    landuse_gdf["country_in_allowed_list"] = landuse_gdf["country_iso3"].isin(allowed_iso3)

    # Filter the landuse records to the countries that appear in the workbook before any nearest match.
    landuse_gdf = landuse_gdf.loc[landuse_gdf["country_in_allowed_list"]].copy()

    # Keep the workbook ISO3 allow-list as the global inclusion gate for the
    # main export. The stricter block-level country filter is available via the
    # helper above and can be applied only when a named region extract is built.
    landuse_gdf = apply_region_country_filter(landuse_gdf, region_name=None, allowed_iso3=allowed_iso3)

    # Normalize the landuse country labels for reporting.
    landuse_gdf["country_norm"] = landuse_gdf["country"].apply(normalize_country)

    # Run the nearest-port lookup against the full network port node set.
    nearest = build_nearest_port_lookup(landuse_gdf, network_ports)

    # Keep a tidy export that contains every landuse row and its nearest port id.
    lookup_export = nearest[
        [
            "port_name",
            "country",
            "continent",
            "area",
            "type",
            "sector",
            "land_use",
            "nearest_port_id",
            "nearest_port_name",
            "nearest_port_country",
            "nearest_port_iso3",
            "distance_m",
        ]
    ].copy()

    output_landuse = os.path.join(output_dir, "port_landuse_nearest_port_ids.gpkg")
    nearest.to_file(output_landuse, layer="port_landuse", driver="GPKG")

    lookup_csv = os.path.join(output_dir, "port_landuse_nearest_port_id_lookup.csv")
    lookup_export.to_csv(lookup_csv, index=False)

    unmatched_count = lookup_export["nearest_port_id"].isna().sum()
    matched_count = len(lookup_export) - unmatched_count

    print(f"Allowed ISO3 countries in workbook: {len(country_ref)}")
    print(f"Nearest-port matches kept: {matched_count}")
    print(f"Nearest-port unmatched records: {unmatched_count}")
    print(f"Saved enriched landuse layer to: {output_landuse}")
    print(f"Saved spreadsheet lookup to: {lookup_csv}")

    return nearest, lookup_export


if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)
