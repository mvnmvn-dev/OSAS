# Self-hosted OSRM for BCP campus pathways

The predictive patrol planner calls OSRM Trip/Route APIs configured by `OSRM_BASE_URL`.

## Quick start (Docker)

1. Build a campus walkway extract in JOSM (or download a clipped OSM rectangle around BCP) and save as `campus.osm`.
2. Prepare the graph:

```bash
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/campus.osm
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-partition /data/campus.osrm
docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-customize /data/campus.osrm
docker run -t -p 5001:5000 -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/campus.osrm
```

3. Set in `.env`:

```
OSRM_BASE_URL=http://127.0.0.1:5001
OSRM_PROFILE=foot
```

If OSRM is unreachable, `services/osrm_client.py` falls back to a nearest-neighbor heuristic so patrol generation still works offline, while still ordering stops by the risk-scoring engine.
