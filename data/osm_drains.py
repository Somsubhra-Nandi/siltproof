"""Pull ward drain geometry from OpenStreetMap and buffer it into polygons.

Plan section 6: Overpass query for waterway=drain|canal|ditch inside the ward
bounding box, keep 18 sections, buffer each line by 30 m, write GeoJSON that
both the frontend and the R1 geofence check read.

Output: data/simulated/drains.geojson
"""

# TODO Day 1 (role C): Overpass query -> 18 line sections -> 30 m buffers.
