from __future__ import annotations

import csv
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import requests


# ----------------------------
# Settings
# ----------------------------

OUT_DIR = Path(r"C:\Users\darce\OneDrive - Nexus365\ECI\Asia\found GIS data\data_gov_sg_transport_geojson_2020plus")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Broader transport-related terms used on collection names/descriptions
TRANSPORT_TERMS = [
    "transport", "road", "rail", "railway", "train", "metro", "mrt", "lrt",
    "cycling", "cycle", "bike", "bicycle",
    "maritime", "port", "harbour", "harbor", "shipping",
    "waterway", "inland", "river", "canal",
    "airport", "aviation", "aerodrome",
    "geospatial", "gis",
]

LIST_COLLECTIONS_API = "https://api-production.data.gov.sg/v2/public/api/collections"
COLLECTION_METADATA_API = "https://api-production.data.gov.sg/v2/public/api/collections/{collection_id}/metadata?withDatasetMetadata=true"
POLL_DOWNLOAD_API = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/poll-download"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; data-gov-sg-downloader/1.0)"
})


# ----------------------------
# Helpers
# ----------------------------

def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def latest_nonnull(*values: Optional[datetime]) -> Optional[datetime]:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def safe_filename(text: str, max_len: int = 140) -> str:
    text = re.sub(r"[^\w.\-]+", "_", text, flags=re.UNICODE).strip("_")
    return text[:max_len] if len(text) > max_len else text


def text_blob(*parts: Any) -> str:
    return " ".join("" if p is None else str(p) for p in parts).lower()


def looks_transport_related(*parts: Any) -> bool:
    blob = text_blob(*parts)
    return any(term in blob for term in TRANSPORT_TERMS)


def api_get_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_all_collections() -> Iterable[Dict[str, Any]]:
    page = 1
    while True:
        url = f"{LIST_COLLECTIONS_API}?page={page}"
        print(f"Reading collections page {page} ...")
        payload = api_get_json(url)

        data = payload.get("data") or {}
        collections = data.get("collections") or []
        if not collections:
            break

        for c in collections:
            yield c

        total_pages = data.get("pages") or page
        if page >= int(total_pages):
            break
        page += 1


def get_collection_metadata(collection_id: str) -> Dict[str, Any]:
    url = COLLECTION_METADATA_API.format(collection_id=collection_id)
    return api_get_json(url, timeout=120)


def download_geojson(dataset_id: str, dataset_name: str) -> Path:
    poll_url = POLL_DOWNLOAD_API.format(dataset_id=dataset_id)
    payload = api_get_json(poll_url, timeout=120)

    data = payload.get("data") or {}
    file_url = data.get("url")
    if not file_url:
        raise RuntimeError("No file URL returned by poll-download")

    safe_name = safe_filename(dataset_name) or dataset_id
    out_path = OUT_DIR / f"{safe_name}__{dataset_id}.geojson"

    with session.get(file_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    return out_path


# ----------------------------
# Main
# ----------------------------

seen_dataset_ids: Set[str] = set()
manifest_rows: List[Dict[str, Any]] = []

downloaded = 0
skipped = 0

for collection in get_all_collections():
    collection_id = collection.get("collectionId")
    collection_name = collection.get("name", "")
    collection_desc = collection.get("description", "")

    if not collection_id:
        continue

    # Only look at transport/geospatial-ish collections
    if not looks_transport_related(collection_name, collection_desc):
        continue

    print(f"\nCollection: {collection_name} ({collection_id})")

    try:
        meta = get_collection_metadata(str(collection_id))
    except Exception as e:
        print(f"  Could not read collection metadata: {e}")
        continue

    meta_data = meta.get("data") or {}
    collection_meta = meta_data.get("collectionMetadata") or {}
    datasets = meta_data.get("datasetMetadata") or []

    for ds in datasets:
        dataset_id = ds.get("datasetId")
        name = ds.get("name", "")
        fmt = str(ds.get("format") or "").upper()

        if not dataset_id or dataset_id in seen_dataset_ids:
            continue

        # keep only GEOJSON
        if fmt != "GEOJSON":
            continue

        # keep only 2020+ metadata
        last_updated = parse_dt(ds.get("lastUpdatedAt"))
        coverage_start = parse_dt(ds.get("coverageStart"))
        coverage_end = parse_dt(ds.get("coverageEnd"))
        latest_date = latest_nonnull(last_updated, coverage_start, coverage_end)

        if latest_date is None or latest_date < CUTOFF:
            continue

        # extra safety: also allow dataset-level transport matching
        if not looks_transport_related(name, ds.get("managedBy"), collection_name, collection_desc):
            # comment this out if you want every GEOJSON in matching collections
            continue

        try:
            saved_to = download_geojson(str(dataset_id), str(name))
            seen_dataset_ids.add(str(dataset_id))
            downloaded += 1

            manifest_rows.append({
                "collectionId": collection_id,
                "collectionName": collection_meta.get("name", collection_name),
                "datasetId": dataset_id,
                "name": name,
                "format": fmt,
                "managedBy": ds.get("managedBy", ""),
                "lastUpdatedAt": ds.get("lastUpdatedAt", ""),
                "coverageStart": ds.get("coverageStart", ""),
                "coverageEnd": ds.get("coverageEnd", ""),
                "savedTo": str(saved_to),
            })

            print(f"  Downloaded: {name}")
            time.sleep(0.15)

        except Exception as e:
            skipped += 1
            print(f"  Skipped: {name} ({dataset_id}) -> {e}")

manifest_path = OUT_DIR / "manifest.csv"
with open(manifest_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "collectionId", "collectionName", "datasetId", "name", "format",
            "managedBy", "lastUpdatedAt", "coverageStart", "coverageEnd", "savedTo"
        ],
    )
    writer.writeheader()
    writer.writerows(manifest_rows)

print()
print("Done.")
print(f"Downloaded: {downloaded}")
print(f"Skipped:    {skipped}")
print(f"Manifest:   {manifest_path}")
print(f"Folder:     {OUT_DIR}")