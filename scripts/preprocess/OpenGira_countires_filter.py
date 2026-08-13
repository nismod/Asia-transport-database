#!/usr/bin/env python
"""Filter an OpenGIRA network to the countries in Countries_list.xlsx."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


def load_iso3_codes(workbook: Path, country: str | None = None) -> set[str]:
    """Read the ISO3 column from the project country-list workbook."""
    if not workbook.exists():
        raise FileNotFoundError(f"Countries workbook not found: {workbook}")

    countries = pd.read_excel(workbook)
    countries.columns = [str(column).strip() for column in countries.columns]

    # Countries_list.xlsx currently stores ISO3 in Column3.  Also support a
    # clearly named iso3 column and the three-column fallback used elsewhere.
    if "Column3" in countries.columns:
        iso3_column = "Column3"
    else:
        matches = [column for column in countries.columns if column.lower() == "iso3"]
        if matches:
            iso3_column = matches[0]
        elif len(countries.columns) >= 3:
            iso3_column = countries.columns[2]
        else:
            raise ValueError("Country workbook does not contain an ISO3 column")

    iso3_codes = {
        str(value).strip().upper()
        for value in countries[iso3_column].dropna()
        if str(value).strip()
    }

    if country is None:
        return iso3_codes

    requested = country.strip().upper()
    if requested in iso3_codes:
        return {requested}

    # Allow a country name as input when the workbook has a country-name field.
    name_columns = [column for column in countries.columns
                    if column.lower() in {"country", "country/territory", "country_name"}]
    for name_column in name_columns:
        matches = countries[countries[name_column].astype("string").str.strip().str.upper() == requested]
        if not matches.empty:
            return {str(matches.iloc[0][iso3_column]).strip().upper()}

    raise ValueError(f"Country or ISO3 code not found in {workbook}: {country}")


def filter_network(input_directory: Path, output_directory: Path, iso3_codes: set[str]) -> None:
    nodes_path = input_directory / "nodes.gpq"
    edges_path = input_directory / "edges.gpq"
    if not nodes_path.exists() or not edges_path.exists():
        raise FileNotFoundError(f"Expected nodes.gpq and edges.gpq in {input_directory}")

    nodes = gpd.read_parquet(nodes_path)
    edges = gpd.read_parquet(edges_path)

    required = {"iso_a3"}
    missing = required - set(nodes.columns)
    if missing:
        raise ValueError(f"nodes.gpq is missing columns: {', '.join(sorted(missing))}")
    required = {"from_iso_a3", "to_iso_a3"}
    missing = required - set(edges.columns)
    if missing:
        raise ValueError(f"edges.gpq is missing columns: {', '.join(sorted(missing))}")

    node_iso3 = nodes["iso_a3"].astype("string").str.strip().str.upper()
    nodes_filtered = nodes[node_iso3.isin(iso3_codes)].copy()

    edge_from = edges["from_iso_a3"].astype("string").str.strip().str.upper()
    edge_to = edges["to_iso_a3"].astype("string").str.strip().str.upper()
    edges_filtered = edges[edge_from.isin(iso3_codes) | edge_to.isin(iso3_codes)].copy()

    output_directory.mkdir(parents=True, exist_ok=True)
    nodes_filtered.to_parquet(output_directory / "nodes.gpq", index=False)
    edges_filtered.to_parquet(output_directory / "edges.gpq", index=False)
    print(f"ISO3 countries retained: {len(iso3_codes):,}")
    print(f"Nodes: {len(nodes):,} -> {len(nodes_filtered):,}")
    print(f"Edges: {len(edges):,} -> {len(edges_filtered):,}")
    print(f"Wrote filtered network to {output_directory}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "country",
        nargs="?",
        help="Country ISO3 code or name, for example TUR or Turkey",
    )
    parser.add_argument(
        "--filter",
        default="open_gira",
        help="Processed network folder name, for example open_gira",
    )
    parser.add_argument("--input", type=Path, help="Input network directory (overrides the country-based default)")
    parser.add_argument("--output", type=Path, help="Output directory (overrides the country-based default)")
    parser.add_argument("--countries", type=Path, default=Path("incoming_data/Countries_list.xlsx"))
    args = parser.parse_args()

    if not args.country:
        args.country = input("Enter country name or ISO3 code (for example Turkey or TUR): ").strip()
        if not args.country:
            parser.error("A country name or ISO3 code is required")

    country_folder = args.country.strip().lower().replace(" ", "-")
    filter_directory = Path("processed_data") / args.filter
    input_directory = args.input or filter_directory / f"{country_folder}-latest_filter-rail"
    output_directory = args.output or filter_directory / f"{country_folder}-latest_filter-rail_country-filtered"
    filter_network(input_directory, output_directory, load_iso3_codes(args.countries, args.country))


if __name__ == "__main__":
    main()
