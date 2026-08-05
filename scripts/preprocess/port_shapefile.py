#!/usr/bin/env python
# coding: utf-8

import os
import re
import geopandas as gpd
import pandas as pd

from utils_new import load_config


COUNTRY_ALIASES = {
    "south korea": "Korea, Republic of",
    "korea republic of": "Korea, Republic of",
    "north korea": "Korea (Democratic People's Republic of)",
    "korea democratic people republic of": "Korea (Democratic People's Republic of)",
    "russia": "Russian Federation",
    "viet nam": "Viet Nam",
    "vietnam": "Viet Nam",
    "turkey": "Türkiye",
    "turkiye": "Türkiye",
    "taiwan": "Taiwan, Province of China",
    "hong kong": "Hong Kong",
    "macao": "Macao",
    "macau": "Macao",
    "brunei": "Brunei Darussalam",
    "east timor": "Timor-Leste",
    "timor leste": "Timor-Leste",
    "palestine": "Palestine, State of",
    "iran": "Iran (Islamic Republic of)",
    "iran islamic republic of": "Iran (Islamic Republic of)",
    "micronesia": "Micronesia (Federated States of)",
    "syrian arab republic": "Syrian Arab Republic",
}


def normalize_name(value):
    """Return a compact, case-insensitive string for name matching."""
    if pd.isna(value):
        return ""

    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def load_country_reference(countries_path):
    """Load the country workbook and build a normalized ISO3 lookup."""
    ref_df = pd.read_excel(countries_path)
    ref_df = ref_df.rename(columns={"Country/Territory": "country_name", "Column2": "iso2", "Column3": "iso3"})

    ref_df["country_name"] = ref_df["country_name"].astype(str).str.strip()
    ref_df["iso2"] = ref_df["iso2"].fillna("").astype(str).str.strip().str.upper()
    ref_df["iso3"] = ref_df["iso3"].fillna("").astype(str).str.strip().str.upper()

    allowed_iso3 = set(ref_df["iso3"].dropna().astype(str).str.upper())

    lookup = {}
    for row in ref_df.itertuples(index=False):
        for key in [row.country_name, row.iso2, row.iso3]:
            key_norm = normalize_name(key)
            if key_norm:
                lookup[key_norm] = row.iso3

    for alias, canonical_country in COUNTRY_ALIASES.items():
        canonical_norm = normalize_name(canonical_country)
        canonical_iso3 = lookup.get(canonical_norm)
        if canonical_iso3 is not None:
            lookup[normalize_name(alias)] = canonical_iso3

    return ref_df, lookup, allowed_iso3


def country_to_iso3(country_value, country_lookup):
    """Map a landuse country name to an ISO3 code using the allowed list."""
    if pd.isna(country_value):
        return pd.NA

    norm = normalize_name(country_value)
    if norm in country_lookup:
        return country_lookup[norm]

    return pd.NA


def build_port_lookup(network_ports):
    """Create a lookup table from the large-cluster network port nodes."""
    lookup = network_ports.loc[network_ports["infra"] == "port", ["id", "name", "country", "iso3"]].copy()
    lookup["name_norm"] = lookup["name"].apply(normalize_name)

    # Keep the first `id` per normalized name to avoid duplicate joins.
    lookup = lookup.drop_duplicates(subset=["name_norm"], keep="first")

    return lookup


def main(config):
    incoming_data_path = config["paths"]["incoming_data"]
    processed_data_path = config["paths"]["data"]

    landuse_gpkg = os.path.join(
        incoming_data_path,
        "infrastructure",
        "Port",
        "port_landuse.gpkg",
    )

    countries_list_path = os.path.join(incoming_data_path, "Countries_list.xlsx")

    network_gpkg = os.path.join(
        processed_data_path,
        "infrastructure",
        "large_cluster_maritime_network.gpkg",
    )

    output_dir = os.path.join(processed_data_path, "infrastructure")
    os.makedirs(output_dir, exist_ok=True)

    # Read the country allow-list workbook and the landuse polygons.
    country_ref, country_lookup, allowed_iso3 = load_country_reference(countries_list_path)
    landuse_gdf = gpd.read_file(landuse_gpkg)
    network_ports = gpd.read_file(network_gpkg, layer="port_nodes")

    # Standardize the port-name strings used for matching.
    landuse_gdf["port_name_norm"] = landuse_gdf["port_name"].apply(normalize_name)
    landuse_gdf["country_iso3"] = landuse_gdf["country"].apply(lambda x: country_to_iso3(x, country_lookup))
    landuse_gdf["country_in_allowed_list"] = landuse_gdf["country_iso3"].isin(allowed_iso3)

    # Only keep landuse entries that resolve to the workbook's allowed ISO country list.
    filtered_landuse = landuse_gdf.loc[landuse_gdf["country_in_allowed_list"]].copy()

    # Build a lookup table of all matched network port ids.
    port_lookup = build_port_lookup(network_ports)

    # Join landuse entries to the network by normalized port name.
    matched = filtered_landuse.merge(
        port_lookup[["id", "name_norm"]].rename(columns={"id": "port_id", "name_norm": "port_name_norm"}),
        on="port_name_norm",
        how="left",
    )

    # For visibility, keep the original network name next to the matched id.
    matched = matched.merge(
        port_lookup[["id", "name"]].rename(columns={"id": "port_id", "name": "matched_network_name"}),
        on="port_id",
        how="left",
    )

    # Save the enriched landuse layer with the new column.
    output_landuse = os.path.join(output_dir, "port_landuse_with_port_ids.gpkg")
    matched.to_file(output_landuse, layer="port_landuse", driver="GPKG")

    # Save a spreadsheet with all landuse entries and their associated port_id.
    lookup_export = matched[
        [
            "port_name",
            "country",
            "country_iso3",
            "continent",
            "area",
            "type",
            "sector",
            "land_use",
            "port_id",
            "matched_network_name",
        ]
    ].copy()

    lookup_csv = os.path.join(output_dir, "port_landuse_port_id_lookup.csv")
    lookup_export.to_csv(lookup_csv, index=False)

    # Report how many landuse entries could not be matched.
    unmatched_count = lookup_export["port_id"].isna().sum()
    print(f"Allowed country workbook rows: {len(country_ref)}")
    print(f"Landuse rows retained after country filter: {len(filtered_landuse)}")
    print(f"Matched {len(lookup_export) - unmatched_count} landuse records to a network port_id.")
    print(f"Unmatched landuse records: {unmatched_count}")
    print(f"Saved enriched landuse layer to: {output_landuse}")
    print(f"Saved spreadsheet lookup to: {lookup_csv}")

    return matched, lookup_export


if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)
