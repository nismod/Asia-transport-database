
**Overview of all script**

Airports.py:
    -uses airport data from: incoming_data/infrastructure/Airport
        -OurAirports, OurAirport_filtered_airports.gpkg
        -World Bank, worldbank_filtered_airport_flows.gpkg worldbank_filtered_airport_volume.gpkg
    -corrects the World Bank airport coordinates using the OurAirports coordinates where a matching IATA code is available
    -keeps the original World Bank coordinates where an OurAirports match is not available
    -corrects the origin and destination coordinates of World Bank flight connections (flows) using OurAirports
    -file output names:
        -corrected airport points, world_bank_airports_corrected.gpkg
        -corrected airport flight connections, airport_flows_world_bank_corrected.gpkg
        -Excel audit table showing matched and unmatched airports and the original and corrected coordinates, airport_coordinate_audit.xlsx

OSM_extract_country_loop.py:
    - extracts an osm (or multiple) tags from a pbf files
    - currently circles through all countires in asian pacific study area (list in country_list_osm, modified for the the osm extractoin name)
    - outputs a parquet file for each country, and a combined asia-pacific file
    -currently russia pbf file doesnt work

port_shapefile_nearest:
    - matches port_landuse.gpkg to the nearest node in global_maritime_network.gpkg, both from here https://data.mendeley.com/datasets/kdyt24tsh5/1 , Jasper Verschuur 
      and attaches the port ID to the shapefile.
    -file output names
        port_landuse_nearest_port_id_lookup - excel spread sheet and gpkg

ports.py:
    -Code initally copied from the African-transport-dataset/scripts/preprocess/ "ports_new_merge.py"
    -produces network digrams for:
        -the world
        -asia and pacific (all countries in the study area are labled under this)
        -3 clusters (northern russia, pacific region, larger cluster (the rest of the nodes, making up most of Asia)
        -attributes from portwatch are also added to the nodes
        -output is diveded into 2 nodes (ports and maritime (networking) nodes, and edges the connections between)
        -file output names
            global_maritime_network_PROVA_NEW1
            asia & pacific_maritime_network_PROVA_NEW1
            northern_russia_network_maritime_network
            large_cluster_maritime_network
            pacific_network_maritime_network

untils_new.py:
    untils_new.py copied from the African-transport-dataset/scripts/preprocess/
    includes relevant functions