#!/usr/bin/env python
"""Combine prioritised OpenGIRA road extracts into one GeoParquet dataset."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from utils_new import load_config


# Earlier extracts win when the same OSM way occurs more than once.
# Country-specific extracts are therefore placed before the broad Asia extract.



def get_paths(config: dict) -> tuple[Path, Path, Path, Path]:
    """Return OpenGIRA input, output, combined file and audit file paths."""
    paths = config["paths"]
    processed_data = Path(paths["processed_data"])

    # Optional config keys make this usable with OpenGIRA stored outside the
    # repository. The defaults match the project's processed-data layout.
    results_dir = Path(paths.get("open_gira_results", processed_data / "open_gira" / "results" / "road"))
    output_dir = Path(paths.get("open_gira_combined", results_dir / "combined"))

    return (
        results_dir,
        output_dir,
        output_dir / "asia_pacific_road_edges_deduplicated.gpq",
        output_dir / "osm_way_duplicate_audit.csv",
    )


def load_dataset(results_dir: Path, folder_name: str) -> gpd.GeoDataFrame:
    """Load and label one OpenGIRA road edges dataset."""
    edges_file = results_dir / folder_name / "edges.gpq"
    if not edges_file.exists():
        raise FileNotFoundError(f"Could not find edges.gpq in: {edges_file.parent}")

    print(f"\nReading: {edges_file}")
    gdf = gpd.read_parquet(edges_file)
    if "osm_way_id" not in gdf.columns:
        raise ValueError(f"'osm_way_id' column not found in {edges_file}")

    gdf["osm_way_id"] = gdf["osm_way_id"].astype("string")
    gdf["source_extract"] = folder_name
    print(f"  Rows: {len(gdf):,}")
    print(f"  Unique osm_way_id: {gdf['osm_way_id'].nunique():,}")
    return gdf


def combine_datasets(PRIORITY, config: dict) -> tuple[Path, Path]:
    """Combine available extracts and write the dataset and duplicate audit."""
    results_dir, output_dir, output_file, audit_file = get_paths(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("OpenGIRA road merge using osm_way_id")
    print("=" * 70)

    datasets = []
    for folder_name in PRIORITY:
        if not (results_dir / folder_name).exists():
            print(f"\nSkipping missing dataset: {folder_name}")
            continue
        datasets.append((folder_name, load_dataset(results_dir, folder_name)))

    if not datasets:
        raise RuntimeError(f"No OpenGIRA road datasets were found in {results_dir}")

    seen_ids: set[str] = set()
    kept_parts = []
    audit_records = []

    for source_name, gdf in datasets:
        current_ids = set(gdf["osm_way_id"].dropna().astype(str))
        duplicate_ids = current_ids & seen_ids
        keep_ids = current_ids - seen_ids
        print(f"\n{source_name}: {len(gdf):,} rows, {len(keep_ids):,} new IDs, "
              f"{len(duplicate_ids):,} duplicate IDs removed")

        audit_records.extend(
            {"osm_way_id": osm_way_id,
             "kept_source": "previous higher-priority extract",
             "removed_source": source_name}
            for osm_way_id in sorted(duplicate_ids)
        )

        kept = gdf[gdf["osm_way_id"].isin(keep_ids)].copy()
        if not kept.empty:
            kept_parts.append(kept)
        seen_ids.update(current_ids)

    if not kept_parts:
        raise RuntimeError("No rows remained after deduplication")

    combined = gpd.GeoDataFrame(
        pd.concat(kept_parts, ignore_index=True),
        geometry=kept_parts[0].geometry.name,
        crs=kept_parts[0].crs,
    )
    combined.to_parquet(output_file, index=False)

    audit = pd.DataFrame(audit_records, columns=["osm_way_id", "kept_source", "removed_source"])
    audit.to_csv(audit_file, index=False)

    print(f"\nFinal rows: {len(combined):,}")
    print(f"Final unique osm_way_id: {combined['osm_way_id'].nunique():,}")
    print(f"Output: {output_file}")
    print(f"Audit: {audit_file}")
    return output_file, audit_file


def main(config: dict) -> None:
    priority = (
        "asia-latest_filter-road-primary",
        "australia-oceania-latest_filter-road-primary",
        "russia-latest_filter-road-primary",
        "turkey-latest_filter-road-primary",
        "cyprus-latest_filter-road-primary",
    )
    combine_datasets(priority, config)


if __name__ == "__main__":
    CONFIG = load_config()
    main(CONFIG)
