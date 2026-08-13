#!/usr/bin/env python3
"""
Download every unique ArcGIS layer found in a HAR file exported from browser DevTools.

What it does:
- reads a HAR file
- extracts unique FeatureServer/MapServer layer URLs
- saves service metadata JSON
- saves layer metadata JSON
- downloads features in chunks as GeoJSON when possible
- also saves raw ArcGIS JSON for each chunk

Install:
    pip install requests

Run:
    python download_arataki_from_har.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import requests


# ----------------------------
# CONFIG
# ----------------------------
HAR_PATH = Path(
    r"C:\Users\darce\OneDrive - Nexus365\ECI\Asia\found GIS data\multimodal country packages\New Zealand\arataki.har"
)
OUT_DIR = Path(
    r"C:\Users\darce\OneDrive - Nexus365\ECI\Asia\found GIS data\multimodal country packages\New Zealand"
)

TIMEOUT = 90
CHUNK_SIZE = 1000
VERIFY_SSL = True  # set False only if you absolutely need to


# ----------------------------
# HELPERS
# ----------------------------
def safe_name(name: str) -> str:
    name = name.strip() if name else "unnamed"
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("_") or "unnamed"


def read_har_urls(har_path: Path) -> List[str]:
    with har_path.open("r", encoding="utf-8") as f:
        har = json.load(f)

    entries = har.get("log", {}).get("entries", [])
    urls = []
    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url")
        if url:
            urls.append(url)
    return urls


def normalize_layer_url(url: str) -> Optional[str]:
    """
    Keep only actual layer URLs like:
      .../FeatureServer/0
      .../MapServer/3
    Remove query strings and /query suffix.
    """
    if not url:
        return None

    u = url.strip()
    u = re.sub(r"\?.*$", "", u)
    u = re.sub(r"/query$", "", u)

    if re.search(r"/(FeatureServer|MapServer)/\d+$", u):
        return u

    return None


def extract_layer_urls(urls: Iterable[str]) -> List[str]:
    found = set()
    for u in urls:
        if "arcgis" not in u.lower():
            continue
        if "featureserver" not in u.lower() and "mapserver" not in u.lower():
            continue
        norm = normalize_layer_url(u)
        if norm:
            found.add(norm)
    return sorted(found)


def get_json(session: requests.Session, url: str) -> dict:
    r = session.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.json()


def post_bytes(session: requests.Session, url: str, data: dict) -> bytes:
    r = session.post(url, data=data, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.content


def save_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def chunked(seq: List[int], size: int) -> Iterable[List[int]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def layer_name_from_url(layer_url: str) -> str:
    # .../FeatureServer/0 -> 0
    layer_id = layer_url.rstrip("/").split("/")[-1]
    return f"layer_{layer_id}"


def service_url_from_layer_url(layer_url: str) -> str:
    # .../FeatureServer/0 -> .../FeatureServer
    return re.sub(r"/\d+$", "", layer_url.rstrip("/"))


def query_object_ids(session: requests.Session, layer_url: str) -> List[int]:
    url = f"{layer_url}/query?where=1=1&returnIdsOnly=true&f=json"
    data = get_json(session, url)
    ids = data.get("objectIds") or []
    ids = [int(x) for x in ids]
    ids.sort()
    return ids


def download_chunk(
    session: requests.Session,
    layer_url: str,
    object_ids: List[int],
    out_dir: Path,
    part_num: int,
) -> None:
    id_string = ",".join(str(i) for i in object_ids)

    geojson_path = out_dir / f"part{part_num}.geojson"
    json_path = out_dir / f"part{part_num}.json"

    # Prefer GeoJSON
    geo_params = {
        "objectIds": id_string,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    raw_params = {
        "objectIds": id_string,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }

    # GeoJSON
    try:
        content = post_bytes(session, f"{layer_url}/query", geo_params)
        save_bytes(geojson_path, content)
    except Exception:
        # Some layers may not support geojson cleanly
        pass

    # Raw ArcGIS JSON
    content = post_bytes(session, f"{layer_url}/query", raw_params)
    save_bytes(json_path, content)


def download_layer(session: requests.Session, layer_url: str, base_out: Path) -> None:
    service_url = service_url_from_layer_url(layer_url)
    service_name = safe_name(Path(urlparse(service_url).path).name)
    layer_id = layer_url.rstrip("/").split("/")[-1]

    service_dir = base_out / service_name
    layer_dir = service_dir / f"layer_{layer_id}"

    service_dir.mkdir(parents=True, exist_ok=True)
    layer_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== {layer_url} ===")

    # Save metadata
    try:
        save_json(service_dir / "_service.json", get_json(session, f"{service_url}?f=json"))
    except Exception as e:
        print(f"  Could not save service JSON: {e}")

    try:
        layer_meta = get_json(session, f"{layer_url}?f=json")
        save_json(layer_dir / "_layer.json", layer_meta)
    except Exception as e:
        print(f"  Could not save layer JSON: {e}")
        return

    # Get feature IDs
    try:
        ids = query_object_ids(session, layer_url)
    except Exception as e:
        print(f"  Could not get feature IDs: {e}")
        return

    if not ids:
        print("  No features found.")
        return

    print(f"  {len(ids)} features found.")

    # Download in chunks
    part = 1
    for group in chunked(ids, CHUNK_SIZE):
        print(f"  Downloading part {part}...")
        try:
            download_chunk(session, layer_url, group, layer_dir, part)
        except Exception as e:
            print(f"  Failed part {part}: {e}")
        part += 1


# ----------------------------
# MAIN
# ----------------------------
def main() -> None:
    if not HAR_PATH.exists():
        raise FileNotFoundError(f"HAR file not found: {HAR_PATH}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading HAR: {HAR_PATH}")
    urls = read_har_urls(HAR_PATH)
    layer_urls = extract_layer_urls(urls)

    print(f"Found {len(layer_urls)} unique ArcGIS layers.")

    # Save discovered URLs
    (OUT_DIR / "discovered_layer_urls.txt").write_text(
        "\n".join(layer_urls) + ("\n" if layer_urls else ""),
        encoding="utf-8",
    )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        }
    )

    for layer_url in layer_urls:
        download_layer(session, layer_url, OUT_DIR)

    print("\nDONE")
    print(f"Saved under: {OUT_DIR}")


if __name__ == "__main__":
    main()