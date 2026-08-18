#!/usr/bin/env python
"""Combine prioritised OpenGIRA extracts into one GeoParquet dataset."""

from __future__ import annotations
from pathlib import Path
import geopandas as gpd
import pandas as pd
from utils_new import load_config



def get_paths(config: dict, transport_mode: str) -> tuple[Path, Path, Path, Path, Path, Path]:
    # ds gets paths for input and output files based on the transport mode and config
    paths = config["paths"]
    processed_data = Path(paths["processed_data"])

    # Optional config keys make this usable with OpenGIRA stored outside the
    # repository. The defaults match the project's processed-data layout.
    results_root = Path(paths.get("open_gira_results", processed_data / "open_gira" / "results"))
    results_dir = results_root / transport_mode
    output_dir = Path(paths.get("open_gira_combined", results_dir / "combined"))

    return (
        results_dir,
        output_dir,
        output_dir / f"asia_pacific_{transport_mode}_edges.gpq",
        output_dir / f"osm_way_duplicate_audit_{transport_mode}.csv",
        output_dir / f"asia_pacific_{transport_mode}_nodes.gpq",
        output_dir / f"osm_node_duplicate_audit_{transport_mode}.csv",
    )


def load_edges_dataset(results_dir: Path, folder_name: str) -> gpd.GeoDataFrame: # ds outputs the geopandas dataframe for the edges dataset
    #ds Load and label one OpenGIRA edges dataset
    edges_file = results_dir / folder_name / "edges.gpq"
    if not edges_file.exists():
        raise FileNotFoundError(f"Could not find edges.gpq in: {edges_file.parent}")

    print(f"\nReading: {edges_file}")
    gdf = gpd.read_parquet(edges_file) #ds reads the edges.gpq file into a geopandas dataframe
    if "osm_way_id" not in gdf.columns:
        raise ValueError(f"'osm_way_id' column not found in {edges_file}")

    gdf["osm_way_id"] = gdf["osm_way_id"].astype("string") # ds converts the osm_way_id column to string type
    gdf["source_extract"] = folder_name # ds adds a column to the dataframe indicating the source folder name (eg which opengira results this data is from, russia, asia, turkey ect)
    return gdf


def load_nodes_dataset(results_dir: Path, folder_name: str) -> gpd.GeoDataFrame:
    #ds Load and label one OpenGIRA nodes dataset
    nodes_file = results_dir / folder_name / "nodes.gpq"
    if not nodes_file.exists():
        raise FileNotFoundError(f"Could not find nodes.gpq in: {nodes_file.parent}")

    print(f"\nReading: {nodes_file}")
    gdf = gpd.read_parquet(nodes_file)
    node_id_col = next((col for col in ("osm_node_id") if col in gdf.columns), None) 
    if "osm_node_id" not in gdf.columns:
        raise ValueError(f"'osm_node_id' column not found in {nodes_file}")

    gdf = gdf.rename(columns={node_id_col: "osm_node_id"})
    gdf["osm_node_id"] = gdf["osm_node_id"].astype("string")
    gdf["source_extract"] = folder_name
    return gdf


def combine_edges(priority: tuple[str, ...], config: dict, transport_mode: str) -> tuple[Path, Path]:
    #ds Combine edges from available opengira data and write the dataset and duplicate audit.
    results_dir, output_dir, output_file, audit_file, _, _ = get_paths(config, transport_mode)
    output_dir.mkdir(parents=True, exist_ok=True) # ds creates the output directory if it does not exist

    datasets = [] # ds initializes dataset list
    for folder_name in priority: # goes through open gira prioirty list and loads the edges dataset for each folder if it exists, otherwise skips it
        if not (results_dir / folder_name).exists():
            print(f"\nSkipping missing dataset: {folder_name}")
            continue
        datasets.append((folder_name, load_edges_dataset(results_dir, folder_name))) # ds appends all datasets in the priority list to the datasets list as a tuple of (folder_name, geopandas dataframe)

    if not datasets:
        raise RuntimeError(f"No OpenGIRA {transport_mode} datasets were found in {results_dir}")

    seen_ids: set[str] = set() # ds initializes a set (which doesnt store duplicates) to keep track of seen osm_way_ids
    kept_parts = []
    audit_records = []

    for source_name, gdf in datasets: # ds  loops through all pairs in the datasets 
        current_ids = set(gdf["osm_way_id"].dropna().astype(str)) # ds creates a set of the unique osm_way_ids and geopandas dataframe in the current region
        duplicate_ids = current_ids & seen_ids # ds osm_way_ids that have already been seen in previous datasets
        keep_ids = current_ids - seen_ids # removes any osm_way_ids that have already been seen in previous datasets from the set

        if duplicate_ids:
            removed_rows = gdf[gdf["osm_way_id"].isin(sorted(duplicate_ids))].copy()
            removed_rows["removed_source"] = source_name
            audit_records.append(removed_rows)

        kept = gdf[gdf["osm_way_id"].isin(keep_ids)].copy() # ds creates a gdf of rows that should only be kept (not seen before), can ,multiple inputs (wit seperate component ids) with the same osm_way_id 
        if not kept.empty: # ds id kept is not empty, append it to the kept_parts list
            kept_parts.append(kept)
        seen_ids.update(current_ids)

    combined = gpd.GeoDataFrame(
        pd.concat(kept_parts, ignore_index=True), #ds concatinates all the kept eges and remove privous index values and creates a new index
        geometry=kept_parts[0].geometry.name, # defines the geomery of the dataset assuming all entries are the same
        crs=kept_parts[0].crs, # defines the coordinate reference system of the dataset assuming all entries are the same
    )
    combined.to_parquet(output_file, index=False) # convert combined geopandas dataframe to parquet file and save it to the output file

    audit = pd.concat(audit_records, ignore_index=True) if audit_records else pd.DataFrame(columns=[*gdf.columns, "removed_source", "kept_source"])
    audit.to_csv(audit_file, index=False)

    print(f"\nFinal rows: {len(combined):,}")
    print(f"Final unique osm_way_id: {combined['osm_way_id'].nunique():,}")
    print(f"Output: {output_file}")
    print(f"Audit: {audit_file}")
    return output_file, audit_file


def combine_nodes(priority: tuple[str, ...], config: dict, transport_mode: str) -> tuple[Path, Path]:
    #ds Combine nodes from available opengira data and write the dataset and duplicate audit.
    results_dir, output_dir, _, _, nodes_output_file, nodes_audit_file = get_paths(config, transport_mode)
    output_dir.mkdir(parents=True, exist_ok=True)

    datasets = []
    for folder_name in priority:
        if not (results_dir / folder_name).exists():
            print(f"\nSkipping missing dataset: {folder_name}")
            continue
        datasets.append((folder_name, load_nodes_dataset(results_dir, folder_name)))

    if not datasets:
        raise RuntimeError(f"No OpenGIRA {transport_mode} node datasets were found in {results_dir}")

    seen_ids: set[str] = set()
    kept_parts = []
    audit_records = []

    for source_name, gdf in datasets:
        current_ids = set(gdf["osm_node_id"].dropna().astype(str))
        duplicate_ids = current_ids & seen_ids
        keep_ids = current_ids - seen_ids

        if duplicate_ids: # ds create audit of duplicated items
            removed_rows = gdf[gdf["osm_node_id"].isin(sorted(duplicate_ids))].copy()
            removed_rows["removed_source"] = source_name
            audit_records.append(removed_rows)

        kept = gdf[gdf["osm_node_id"].isin(keep_ids)].copy()
        if not kept.empty:
            kept_parts.append(kept)
        seen_ids.update(current_ids)

    if not kept_parts:
        raise RuntimeError("No node rows remained after deduplication")

    combined_nodes = gpd.GeoDataFrame(
        pd.concat(kept_parts, ignore_index=True),
        geometry=kept_parts[0].geometry.name,
        crs=kept_parts[0].crs,
    )
    combined_nodes.to_parquet(nodes_output_file, index=False)

    node_audit = pd.concat(audit_records, ignore_index=True) if audit_records else pd.DataFrame(columns=[*gdf.columns, "removed_source"])
    node_audit.to_csv(nodes_audit_file, index=False)

    print(f"\nFinal rows: {len(combined_nodes):,}")
    print(f"Final unique osm_node_id: {combined_nodes['osm_node_id'].nunique():,}")
    print(f"Output: {nodes_output_file}")
    print(f"Audit: {nodes_audit_file}")
    return nodes_output_file, nodes_audit_file


def write_kept_summary_excel(edges_file: Path, nodes_file: Path, output_file: Path) -> Path:
    """Write a single Excel workbook with one sheet per dataset and one merged kept-data sheet."""
    edges = gpd.read_parquet(edges_file)
    nodes = gpd.read_parquet(nodes_file)

    edge_records = []
    for _, row in edges.iterrows():
        record = dict(row)
        record["record_type"] = "edge"
        record["osm_id"] = row.get("osm_way_id")
        edge_records.append(record)

    node_records = []
    for _, row in nodes.iterrows():
        record = dict(row)
        record["record_type"] = "node"
        record["osm_id"] = row.get("osm_node_id")
        node_records.append(record)

    all_kept = pd.DataFrame(edge_records + node_records)
    if "geometry" in all_kept.columns:
        all_kept["geometry"] = all_kept["geometry"].apply(lambda geom: geom.wkt if geom is not None else None)

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        all_kept.to_excel(writer, sheet_name="all_kept", index=False)

    print(f"\nWorkbook: {output_file}")
    return output_file


def main(config: dict) -> None:

    TRANSPORT_MODE = "rail" #ds or "road_primary"
    
    DEFAULT_PRIORITY_BY_MODE: dict[str, tuple[str, ...]] = { # ds create a dictinary with file to look at based on the transport mode
        "road_primary": (
            "asia-latest_filter-road-primary",
            "australia-oceania-latest_filter-road-primary",
            "russia-latest_filter-road-primary",
            "turkey-latest_filter-road-primary",
            "cyprus-latest_filter-road-primary",
        ),
        "rail": (
            "asia-latest_filter-rail",
            "australia-oceania-latest_filter-rail",
            "russia-latest_filter-rail",
            "turkey-latest_filter-rail",
            "cyprus-latest_filter-rail",
        ),
    }

    
    transport_mode = TRANSPORT_MODE.lower().strip()
    if transport_mode not in DEFAULT_PRIORITY_BY_MODE:
        valid_modes = ", ".join(sorted(DEFAULT_PRIORITY_BY_MODE))
        raise ValueError(f"Unsupported TRANSPORT_MODE '{transport_mode}'. Use one of: {valid_modes}")

    priority = DEFAULT_PRIORITY_BY_MODE[transport_mode]
    edges_output_file, _ = combine_edges(priority, config, transport_mode)
    nodes_output_file, _ = combine_nodes(priority, config, transport_mode)

    results_dir, output_dir, _, _, _, _ = get_paths(config, transport_mode)
    excel_output_file = output_dir / f"{transport_mode}_kept_summary.xlsx"
    write_kept_summary_excel(edges_output_file, nodes_output_file, excel_output_file)


if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)
