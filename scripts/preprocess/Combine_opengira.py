#!/usr/bin/env python
"""Combine prioritised OpenGIRA extracts into one GeoParquet dataset."""

from __future__ import annotations
from pathlib import Path
import geopandas as gpd
import pandas as pd
from utils_new import load_config



def get_paths(config: dict, transport_mode: str) -> tuple[Path, Path, Path, Path]:
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
        output_dir / f"asia_pacific_{transport_mode}_nodes.gpq",
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


def load_nodes_dataset(results_dir: Path, folder_name: str, transport_mode: str) -> gpd.GeoDataFrame:
    #ds Load and label one OpenGIRA nodes dataset
    nodes_file = results_dir / folder_name / "nodes.gpq"
    if not nodes_file.exists():
        raise FileNotFoundError(f"Could not find nodes.gpq in: {nodes_file.parent}")

    print(f"\nReading: {nodes_file}")
    gdf = gpd.read_parquet(nodes_file)
    
    # Check if osm_node_id exists
    if "osm_node_id" in gdf.columns:
        gdf["osm_node_id"] = gdf["osm_node_id"].astype("string")
    else:
        # If osm_node_id doesn't exist and transport_mode is road_primary, the unique coordinates becomes the osm_node_id
        if transport_mode == "road_primary":
            gdf["osm_node_id"] = gdf.geometry.apply(lambda geom: f"{geom.x:.6f}_{geom.y:.6f}" if geom is not None else None).astype("string")
            print(f"  Note: osm_node_id not found, created from coordinates")
        else:
            raise ValueError(f"'osm_node_id' column not found in {nodes_file}")
    
    gdf["source_extract"] = folder_name
    return gdf


def combine_edges(priority: tuple[str, ...], config: dict, transport_mode: str) -> Path:
    #ds Combine edges from available opengira data and write the dataset.
    results_dir, output_dir, output_file, _ = get_paths(config, transport_mode)
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

    for source_name, gdf in datasets: # ds  loops through all pairs in the datasets 
        current_ids = set(gdf["osm_way_id"].dropna().astype(str)) # ds creates a set of the unique osm_way_ids and geopandas dataframe in the current region
        keep_ids = current_ids - seen_ids # removes any osm_way_ids that have already been seen in previous datasets from the set

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

    print(f"\nFinal rows: {len(combined):,}")
    print(f"Final unique osm_way_id: {combined['osm_way_id'].nunique():,}")
    print(f"Output: {output_file}")
    return output_file


def combine_nodes(priority: tuple[str, ...], config: dict, transport_mode: str) -> Path:
    #ds Combine nodes from available opengira data and write the dataset.
    results_dir, output_dir, _, nodes_output_file = get_paths(config, transport_mode)
    output_dir.mkdir(parents=True, exist_ok=True)

    datasets = []
    for folder_name in priority:
        if not (results_dir / folder_name).exists():
            print(f"\nSkipping missing dataset: {folder_name}")
            continue
        datasets.append((folder_name, load_nodes_dataset(results_dir, folder_name, transport_mode)))

    if not datasets:
        raise RuntimeError(f"No OpenGIRA {transport_mode} node datasets were found in {results_dir}")

    seen_ids: set[str] = set()
    kept_parts = []

    for source_name, gdf in datasets:
        current_ids = set(gdf["osm_node_id"].dropna().astype(str))
        keep_ids = current_ids - seen_ids

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

    print(f"\nFinal rows: {len(combined_nodes):,}")
    print(f"Final unique osm_node_id: {combined_nodes['osm_node_id'].nunique():,}")
    print(f"Output: {nodes_output_file}")
    return nodes_output_file


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
    combine_edges(priority, config, transport_mode)
    combine_nodes(priority, config, transport_mode)


if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)
