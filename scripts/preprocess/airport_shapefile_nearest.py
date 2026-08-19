#!/usr/bin/env python
# coding: utf-8

# Match OSM lines and polygons to World Bank airport points.

from pathlib import Path
import geopandas as gpd
import pandas as pd
from utils_new import load_config





def read_osm_features(osm_filter_directory, required_osm_columns, osm_geometry_types, output_crs):
    # Read OSM files in each tag folder and keep only lines and polygons.

    frames = []
    for tag_directory in sorted(Path(osm_filter_directory).iterdir()): #ds iterate through each tag folder in the OSM filter directory
        if not tag_directory.is_dir(): # if folder doesnt not exist, skip to next folder
            continue

        for parquet_file in sorted(tag_directory.glob("*.parquet")): # ds iterate through each parquet file in the tag folder

            features = gpd.read_parquet(parquet_file) # ds `read the parquet file into a GeoDataFrame`
            if features.empty:
                continue
            if "geometry" not in features.columns:
                raise ValueError(f"Missing geometry column in {parquet_file}")

            #check datasets are complete
            missing_columns = required_osm_columns - set(features.columns)
            if missing_columns:
                raise ValueError(
                    f"{parquet_file} is missing OSM columns: "
                    f"{', '.join(sorted(missing_columns))}"
                )


            if features.crs is None:
                features = features.set_crs(output_crs)

            #Only keep features that are lines or polygons
            features = features[
                features.geometry.geom_type.isin(osm_geometry_types)
            ].copy()
            if features.empty:
                continue

            features["osm_filter_folder"] = tag_directory.name
            features["osm_source_tag"] = tag_directory.name
            features["osm_source_file"] = parquet_file.name
            features["osm_source_path"] = str(parquet_file)
            frames.append(features) # ds store the filtered features in a list of dataframes

    if not frames:
        raise FileNotFoundError(
            f"No OSM line or polygon GeoParquet files found in {osm_filter_directory}"
        )

    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=frames[0].crs,
    )


def load_airports(airport_file, output_crs):
    # Read airport nodal data

    airports = gpd.read_file(airport_file)
    required_columns = {"Orig", "geometry"}
    
    if airports.crs is None:
        airports = airports.set_crs(output_crs)

    airports = airports[airports.geometry.notna()].copy() # ds keep only rows with valid geometry
    airports["airport_iata"] = (airports["Orig"].astype(str).str.strip().str.upper()) # ds create a new column "airport_iata" by cleaning the "Orig" column 

    airports = airports[
        airports["airport_iata"].notna()
        & airports["airport_iata"].ne("")
        & airports["airport_iata"].ne("NAN")
    ].copy() # ds keep only rows where "airport_iata" is not null
    airports["airport_record_id"] = airports.index.astype(str)
    return airports


def add_source_columns(matches, airports, osm_features):
    # Organise extra attribute data from airport nodal data and OSM features data into the matches dataframe.

    # creates a list of all extra columns from the airport nodal data 
    airport_node_columns = [ 
        column for column in airports.columns
        if column not in {"geometry", "airport_iata", "airport_record_id"} # ds 
    ]

    # creates a list of all extra columns from the OSM features data
    osm_columns = [
        column for column in osm_features.columns
        if column not in {
            "geometry",
            "osm_filter_folder",
            "osm_source_tag",
            "osm_source_file",
            "osm_source_path",
        }
    ]

    # create datafram only comtaining extra attributes from airport nodal data
    airport_node_attributes = airports.drop(
        columns=["geometry", "airport_iata", "airport_record_id"],
        errors="ignore", #ds bypasses errors if the columns do not exist
    ).rename(
        columns={column: f"airport_node_{column}" for column in airport_node_columns} # sources column name 
    )

    matches = matches.join(airport_node_attributes, on="index_right") # ds adds nodal data to each matched polygon 
    return matches.rename(
        columns={column: f"OSM_{column}" for column in osm_columns}
    )


def build_audits(matches, airports):
    # create excel audit sheet
    """Create matched-shapes, airport-summary, and tag-summary tables."""

    matches["osm_geometry_type"] = matches.geometry.geom_type
    matched_shapes = matches[
        [column for column in matches.columns if column != "geometry"]
    ].copy()

    attached_counts = (
        matched_shapes.groupby("airport_iata", as_index=False)
        .agg(
            attached_shape_count=("OSM_feature_id", "count"),
            osm_filter_folders=(
                "osm_source_tag",
                lambda values: ", ".join(sorted(set(values))),
            ),
        )
    )

    airport_summary = airports[["airport_iata", "airport_record_id"]].copy()
    airport_summary = airport_summary.merge(
        attached_counts,
        on="airport_iata",
        how="left",
    )
    airport_summary["attached_shape_count"] = (
        airport_summary["attached_shape_count"].fillna(0).astype(int)
    )
    airport_summary["osm_filter_folders"] = airport_summary[
        "osm_filter_folders"
    ].fillna("")

    tag_counts = (
        matched_shapes.pivot_table(
            index="airport_iata",
            columns="osm_source_tag",
            values="OSM_feature_id",
            aggfunc="count",
            fill_value=0,
        )
        .add_prefix("OSM_tag_count_")
        .reset_index()
    )
    airport_summary = airport_summary.merge(
        tag_counts,
        on="airport_iata",
        how="left",
    )
    tag_count_columns = [
        column for column in airport_summary.columns
        if column.startswith("OSM_tag_count_")
    ]
    airport_summary[tag_count_columns] = airport_summary[tag_count_columns].fillna(0).astype(int)

    tag_summary = (
        matched_shapes.assign(
            feature_group=matched_shapes["osm_geometry_type"].map(
                lambda geometry_type: (
                    "line"
                    if "LineString" in geometry_type
                    else "polygon"
                    if "Polygon" in geometry_type
                    else "other"
                )
            )
        )
        .pivot_table(
            index="osm_source_tag",
            columns="feature_group",
            values="OSM_feature_id",
            aggfunc="count",
            fill_value=0,
        )
        .reset_index()
    )
    for column in ["line", "polygon", "other"]:
        if column not in tag_summary.columns:
            tag_summary[column] = 0
    tag_summary["total_features"] = (
        tag_summary["line"] + tag_summary["polygon"] + tag_summary["other"]
    )
    tag_summary = tag_summary.rename(
        columns={
            "line": "line_count",
            "polygon": "polygon_count",
            "other": "other_geometry_count",
        }
    )
    return matched_shapes, airport_summary, tag_summary


def write_outputs(
    matches,
    matched_shapes,
    airport_summary,
    tag_summary,
    output_directory,
    source_locations,
):
    
    #Write the matched GeoPackage and Excel audit workbook.
    output_directory.mkdir(parents=True, exist_ok=True)
    gpkg_file = output_directory / "airport_osm_features_within_2km.gpkg"
    excel_file = output_directory / "airport_osm_features_within_2km.xlsx"

    if gpkg_file.exists():
        gpkg_file.unlink()
    matches.to_file(
        gpkg_file,
        layer="airport_osm_features",
        driver="GPKG",
        index=False,
    )

    def add_source_location_to_headers(frame):
        # Add source location to column headers for Excel audit workbook.
        renamed = {}
        for column in frame.columns:
            if column.startswith("OSM_tag_count_"):
                location = source_locations["calculated"]
            elif column.startswith("WorldBank_") or column == "airport_iata":
                location = source_locations["airport_node"]
            elif column.startswith("OSM_") or column.startswith("osm_"):
                location = source_locations["osm"]
            elif column in {"osm_filter_folders", "osm_source_tag", "osm_source_file", "osm_source_path"}:
                location = source_locations["osm"]
            elif column in {"distance_m", "distance_km", "osm_geometry_type", "attached_shape_count"}:
                location = source_locations["calculated"]
            else:
                location = source_locations["calculated"]
            renamed[column] = f"{column} [source: {location}]"
        return frame.rename(columns=renamed)

    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        add_source_location_to_headers(matched_shapes).to_excel(
            writer, sheet_name="matched_shapes", index=False
        )
        add_source_location_to_headers(airport_summary).to_excel(
            writer, sheet_name="airport_summary", index=False
        )
        add_source_location_to_headers(tag_summary).to_excel(
            writer, sheet_name="tag_summary", index=False
        )

    print(f"Saved matched features: {gpkg_file}")
    print(f"Saved audit workbook: {excel_file}")


def main(config):
    processed_data = Path(config["paths"]["processed_data"])

    airport_directory = processed_data / "infrastructure" / "airport"
    osm_directory = processed_data / "infrastructure" / "osm_filter"
    output_directory = processed_data / "infrastructure" / "airport"

    SEARCH_DISTANCE_METRES = 2_000
    METRIC_CRS = "EPSG:3857"
    OUTPUT_CRS = "EPSG:4326"

    # Corrected Airport nodal data (World Bank corrected airport layer and column).
    AIRPORT_NODE_FILE = "world_bank_airports_corrected.gpkg"

    # OSM GeoParquet columns.
    osm_columns = {
        "feature_id",
        "name",
        "name:en",
        "iata",
        "icao",
    }

    # OSM data types to consider for matching (lines and polygons).
    OSM_GEOMETRY_TYPES = { 
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
    }

    airports = load_airports(
        airport_directory / AIRPORT_NODE_FILE,
        OUTPUT_CRS,
    )
    osm_features = read_osm_features(
        osm_directory,
        osm_columns,
        OSM_GEOMETRY_TYPES,
        OUTPUT_CRS,
    )

    # convert data to metric coordinate reference system for distance calculations
    airports_metric = airports.to_crs(METRIC_CRS) 
    osm_features_metric = osm_features.to_crs(METRIC_CRS)


    airport_buffers = airports_metric[["airport_iata", "geometry"]].copy() # ds create a new dataframe with only the airport_iata and geometry columns
    airport_buffers["geometry"] = airport_buffers.geometry.buffer(SEARCH_DISTANCE_METRES) # creates a 2km buffer around each data point

    # checks if OSM features lies within the airport buffers and returns a new dataframe with the matched features
    matches = gpd.sjoin( #ds matches contains all polgons and lines that intersect with the airport buffers, and the airport node this lies within
        osm_features_metric, # ds left dataframe
        airport_buffers, # ds right dataframe
        how="inner", # ds Keep only OSM features that have a spatial match with an airport buffer
        predicate="intersects", # ds specifies the spatial relationship to use for the join (intersects means that the geometries of the two dataframes must overlap in some way)
    )

    # adds nodal geomerty to matches df for distance calculation
    matched_airport_geometries = gpd.GeoSeries( 
        airports_metric.geometry.loc[matches["index_right"]].to_numpy(),
        index=matches.index,
        crs=airports_metric.crs,
    )
    #calculates the distance between the airport node and closest point on the matched OSM feature
    matches["distance_m"] = matches.geometry.distance(
        matched_airport_geometries,
        align=False,
    )

    matches = matches[matches["distance_m"] <= SEARCH_DISTANCE_METRES].copy() # ds keep only matches that are within 2km of the airport node (second check)
    matches["distance_km"] = matches["distance_m"] / 1_000
    matches = matches.to_crs(OUTPUT_CRS)

    # Add source columns to the matches dataframe and drop unnecessary index columns
    matches = add_source_columns(matches, airports, osm_features)
    matches = matches.drop(columns=["index_right", "index_left"], errors="ignore")

    matched_shapes, airport_summary, tag_summary = build_audits(matches, airports)
    write_outputs(
        matches,
        matched_shapes,
        airport_summary,
        tag_summary,
        output_directory,
        {
            "airport_node": str(airport_directory / AIRPORT_NODE_FILE),
            "osm": str(osm_directory),
            "calculated": "airport_shapefile_nearest.py",
        },
    )

    print(f"Read World Bank airports: {len(airports):,}")
    print(f"Read OSM lines and polygons: {len(osm_features):,}")
    print(f"Matched OSM features within 2 km: {len(matches):,}")


if __name__ == "__main__":
    main(load_config())
