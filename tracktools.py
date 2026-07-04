import os
import re
import sys
import json
import argparse
import secrets
import zipfile
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Sequence, TypedDict, cast, NotRequired

import gpxpy
import gpxpy.gpx
from fastkml import kml, Document, Folder, Placemark
from fastkml import geometry as kml_geometry
from fastkml.styles import StyleUrl
from fastkml.times import KmlDateTime, TimeSpan, TimeStamp
from shapely.geometry import Point, LineString
import geojson

DATA_DIR = "./tracks"


def generate_id() -> str:
    """Generate a 16-char lowercase hex ID (64 random bits)."""
    return secrets.token_hex(8)


class TrackPoint(TypedDict):
    latitude: float
    longitude: float
    time: NotRequired[str]


class FolderData(TypedDict):
    id: str
    name: str
    parent_id: NotRequired[str]


class PointData(TrackPoint):
    id: str
    name: str
    icon: str
    folder_id: NotRequired[str]
    time: NotRequired[str]


class TrackData(TypedDict):
    id: str
    name: str
    points: list[TrackPoint]
    folder_id: NotRequired[str]
    start_time: NotRequired[str]
    end_time: NotRequired[str]


class OutputData(TypedDict):
    folders: list[FolderData]
    points: list[PointData]
    tracks: list[TrackData]


Coordinate = Sequence[float]


def _track_kml_geometry(track: dict[str, Any]) -> Any | None:
    """Return a valid KML geometry for a track, or None for empty tracks."""
    coords = [(pt["longitude"], pt["latitude"]) for pt in track["points"]]
    if not coords:
        return None
    if len(coords) == 1:
        return kml_geometry.Point(geometry=Point(*coords[0]))
    return kml_geometry.LineString(geometry=LineString(coords))


def _kml_datetime_iso(value: KmlDateTime | None) -> str | None:
    return value.dt.isoformat() if value is not None else None


def _kml_own_time_fields(feature: Any) -> tuple[str | None, str | None, str | None]:
    """Return (point_time, span_begin, span_end) from a Placemark/Folder/Document's own TimeStamp/TimeSpan."""
    time_stamp = _kml_datetime_iso(feature.time_stamp)
    if time_stamp:
        return time_stamp, None, None
    begin = _kml_datetime_iso(feature.begin)
    end = _kml_datetime_iso(feature.end)
    if begin or end:
        return None, begin, end
    return None, None, None


def _kml_times_for_point(point: dict[str, Any]) -> TimeStamp | None:
    time_str = point.get("time")
    if not time_str:
        return None
    return TimeStamp(timestamp=KmlDateTime(datetime.fromisoformat(time_str)))


def _kml_times_for_track(track: dict[str, Any]) -> TimeSpan | TimeStamp | None:
    start = track.get("start_time")
    end = track.get("end_time")
    if start and end:
        return TimeSpan(begin=KmlDateTime(datetime.fromisoformat(start)), end=KmlDateTime(datetime.fromisoformat(end)))
    if start:
        return TimeStamp(timestamp=KmlDateTime(datetime.fromisoformat(start)))
    if end:
        return TimeStamp(timestamp=KmlDateTime(datetime.fromisoformat(end)))
    return None


def process_gpx_file(file_path: str, output: OutputData, folder_id: str | None = None) -> None:
    with open(file_path, 'r', encoding='utf-8') as f:
        gpx = gpxpy.parse(f)

    for track in gpx.tracks:
        for segment in track.segments:
            track_points: list[TrackPoint] = []
            for p in segment.points:
                pt: TrackPoint = {"latitude": p.latitude, "longitude": p.longitude}
                if p.time:
                    pt["time"] = p.time.isoformat()
                track_points.append(pt)
            
            track_data: TrackData = {
                "id": generate_id(),
                "name": track.name or os.path.basename(file_path),
                "points": track_points
            }
            if folder_id:
                track_data["folder_id"] = folder_id
            
            times = [p.time for p in segment.points if p.time]
            if times:
                track_data["start_time"] = min(times).isoformat()
                track_data["end_time"] = max(times).isoformat()
            
            output["tracks"].append(track_data)

    for wp in gpx.waypoints:
        point_data: PointData = {
            "id": generate_id(),
            "name": wp.name or "Unnamed",
            "latitude": wp.latitude,
            "longitude": wp.longitude,
            "icon": wp.type or "waypoint"
        }
        if wp.time:
            point_data["time"] = wp.time.isoformat()
        if folder_id:
            point_data["folder_id"] = folder_id
        output["points"].append(point_data)


def process_kml_file(file_path: str, output: OutputData, folder_id: str | None = None) -> None:
    with open(file_path, 'rb') as f:
        kml_doc = f.read()

    k = kml.KML()
    parsed = k.from_string(kml_doc)
    if parsed is not None:
        k = parsed

    def get_features(feature: Any) -> list[Any]:
        features = getattr(feature, "features", [])
        if callable(features):
            features = features()
        return list(features or [])

    def point_from_coord(coord: Coordinate) -> TrackPoint:
        return {"latitude": coord[1], "longitude": coord[0]}

    def coords_from_kml_geometry(geom: Any) -> Iterable[Coordinate] | None:
        coordinates = getattr(geom, "kml_coordinates", None)
        coords = getattr(coordinates, "coords", None)
        return cast(Iterable[Coordinate] | None, coords)

    def add_track(
        name: str | None,
        coords: Iterable[Coordinate],
        kml_folder_name: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None
    ) -> None:
        track_points = [point_from_coord(coord) for coord in coords]
        if not track_points:
            return
        effective_name = name
        if not name or name.lower() == "path":
            effective_name = kml_folder_name or os.path.basename(file_path)
        track_data: TrackData = {
            "id": generate_id(),
            "name": effective_name,
            "points": track_points
        }
        if folder_id:
            track_data["folder_id"] = folder_id
        if start_time:
            track_data["start_time"] = start_time
        if end_time:
            track_data["end_time"] = end_time
        output["tracks"].append(track_data)

    def resolve_icon(style_url: Any, styles: Any) -> str | None:
        """Resolve a placemark's icon from a shared styleUrl or an inline IconStyle."""
        if style_url:
            return getattr(style_url, "url", None) or str(style_url)
        for style in styles or []:
            for sub_style in getattr(style, "styles", None) or []:
                href = getattr(getattr(sub_style, "icon", None), "href", None)
                if href:
                    return href
        return None

    def add_point(name: str | None, coord: Coordinate, icon: str | None = None, time: str | None = None) -> None:
        point_data: PointData = {
            "id": generate_id(),
            "name": name or "Unnamed",
            "latitude": coord[1],
            "longitude": coord[0],
            "icon": icon or "poi"
        }
        if time:
            point_data["time"] = time
        if folder_id:
            point_data["folder_id"] = folder_id
        output["points"].append(point_data)

    def geometry_children(geometry: Any) -> Iterable[Any]:
        inner_geometry = getattr(geometry, "geometry", None)
        if inner_geometry is not None and inner_geometry is not geometry:
            yield inner_geometry

        for attr in ("geoms", "geometries"):
            children = getattr(geometry, attr, None)
            if children:
                yield from children

    def extract_geometry(
        name: str | None,
        geometry: Any,
        icon: str | None = None,
        kml_folder_name: str | None = None,
        time_fields: tuple[str | None, str | None, str | None] = (None, None, None)
    ) -> None:
        geom_type = getattr(geometry, "geom_type", None) or type(geometry).__name__
        point_time, span_begin, span_end = time_fields

        if geom_type == "Point":
            coords = coords_from_kml_geometry(geometry)
            coord = next(iter(coords), None) if coords else None
            if coord is None and hasattr(geometry, "coords"):
                coord = next(iter(geometry.coords), None)
            if coord is None and hasattr(geometry, "x") and hasattr(geometry, "y"):
                coord = (geometry.x, geometry.y)
            if coord is not None:
                add_point(name, coord, icon, point_time or span_begin)
            return

        if geom_type == "LineString":
            coords = getattr(geometry, "coords", None) or coords_from_kml_geometry(geometry)
            add_track(name, coords or [], kml_folder_name, span_begin or point_time, span_end or point_time)
            return

        for child in geometry_children(geometry):
            extract_geometry(name, child, icon, kml_folder_name, time_fields)

    def extract_features(
        features: Iterable[Any],
        kml_folder_name: str | None = None,
        folder_time: tuple[str | None, str | None, str | None] = (None, None, None)
    ) -> None:
        for feature in features:
            if isinstance(feature, (Document, Folder)):
                new_folder_name = getattr(feature, "name", None) or kml_folder_name
                own_time = _kml_own_time_fields(feature)
                new_folder_time = own_time if any(own_time) else folder_time
                extract_features(get_features(feature), new_folder_name, new_folder_time)
            elif isinstance(feature, Placemark):
                geom = feature.geometry
                kml_geom = getattr(feature, "kml_geometry", None)
                if geom is None and kml_geom is None:
                    continue
                own_time = _kml_own_time_fields(feature)
                effective_time = own_time if any(own_time) else folder_time
                extract_geometry(
                    feature.name,
                    geom or kml_geom,
                    resolve_icon(feature.style_url, feature.styles),
                    kml_folder_name,
                    effective_time
                )

    try:
        extract_features(get_features(k), None)
    except Exception as e:
        print(f"⚠️  Could not parse {file_path}: {e}")


def extract_data(input_dir: str, json_file: str, geojson_file: str, use_filenames: bool = False) -> None:
    output: OutputData = {"folders": [], "points": [], "tracks": []}
    
    folder_registry: dict[str, str] = {}
    folder_objects: list[FolderData] = []
    
    def get_or_create_folder(folder_path: str, parent_id: str | None = None) -> str:
        """Get or create folder hierarchy, return the deepest folder's ID."""
        if folder_path in folder_registry:
            return folder_registry[folder_path]
        
        parts = folder_path.split(os.sep)
        current_path = ""
        current_parent = parent_id
        
        for part in parts:
            current_path = os.path.join(current_path, part) if current_path else part
            if current_path not in folder_registry:
                new_folder_id = generate_id()
                folder_registry[current_path] = new_folder_id
                folder_data: FolderData = {"id": new_folder_id, "name": part}
                if current_parent:
                    folder_data["parent_id"] = current_parent
                folder_objects.append(folder_data)
            current_parent = folder_registry[current_path]
        
        return folder_registry[folder_path]
    
    for root, dirs, files in os.walk(input_dir):
        rel_path = os.path.relpath(root, input_dir)
        dir_folder_id: str | None = None
        if rel_path != ".":
            dir_folder_id = get_or_create_folder(rel_path)
        
        for file_name in files:
            file_path = os.path.join(root, file_name)
            is_gpx = file_name.lower().endswith(".gpx")
            is_kml = file_name.lower().endswith(".kml")
            
            if not (is_gpx or is_kml):
                continue
            
            if use_filenames:
                base_name = os.path.splitext(file_name)[0]
                file_folder_path = os.path.join(rel_path, base_name) if rel_path != "." else base_name
                file_folder_id: str | None = get_or_create_folder(file_folder_path, dir_folder_id)
            else:
                file_folder_id = dir_folder_id
            
            if is_gpx:
                process_gpx_file(file_path, output, file_folder_id)
            else:
                process_kml_file(file_path, output, file_folder_id)
    
    output["folders"] = folder_objects

    if json_file:
        with open(json_file, "w", encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ Data saved to {json_file}")

    if geojson_file:
        geojson_features: list[Any] = []
        for point in output["points"]:
            feature = geojson.Feature(
                geometry=geojson.Point((point["longitude"], point["latitude"])),
                properties={"name": point["name"], "icon": point["icon"]}
            )
            geojson_features.append(feature)

        for track in output["tracks"]:
            coords = [(pt["longitude"], pt["latitude"]) for pt in track["points"]]
            if not coords:
                continue
            geometry = geojson.Point(coords[0]) if len(coords) == 1 else geojson.LineString(coords)
            feature = geojson.Feature(
                geometry=geometry,
                properties={"name": track["name"]}
            )
            geojson_features.append(feature)

        geojson_collection = geojson.FeatureCollection(geojson_features)

        with open(geojson_file, "w", encoding='utf-8') as f:
            geojson.dump(geojson_collection, f, ensure_ascii=False, indent=2)
        print(f"✅ GeoJSON saved to {geojson_file}")


def json_to_gpx(json_file: str, output_file: str) -> None:
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    gpx = gpxpy.gpx.GPX()

    for point in data.get("points", []):
        wp = gpxpy.gpx.GPXWaypoint(
            latitude=point["latitude"],
            longitude=point["longitude"],
            name=point["name"],
            type=point.get("icon")
        )
        if point.get("time"):
            from datetime import datetime
            wp.time = datetime.fromisoformat(point["time"])
        gpx.waypoints.append(wp)

    for track in data.get("tracks", []):
        gpx_track = gpxpy.gpx.GPXTrack(name=track["name"])
        segment = gpxpy.gpx.GPXTrackSegment()
        for pt in track["points"]:
            gpx_pt = gpxpy.gpx.GPXTrackPoint(
                latitude=pt["latitude"],
                longitude=pt["longitude"]
            )
            if pt.get("time"):
                from datetime import datetime
                gpx_pt.time = datetime.fromisoformat(pt["time"])
            segment.points.append(gpx_pt)
        gpx_track.segments.append(segment)
        gpx.tracks.append(gpx_track)

    with open(output_file, "w", encoding='utf-8') as f:
        f.write(gpx.to_xml())
    print(f"✅ GPX saved to {output_file}")


def _safe_folder_name(name: str) -> str:
    """Make a folder name safe for use as a filesystem path component."""
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    return cleaned or "untitled"


def json_to_gpx_organized(json_file: str, output_dir: str) -> None:
    """Export JSON data as a tree of GPX files mirroring the data's folder structure.

    Creates output_dir plus one subfolder per folder in the data (nested to match
    parent_id relationships), and writes an export.gpx file in each folder that has
    points or tracks directly assigned to it.
    """
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    folder_by_id: dict[str, dict] = {f["id"]: f for f in data.get("folders", [])}

    points_by_folder: dict[str | None, list] = defaultdict(list)
    for point in data.get("points", []):
        points_by_folder[point.get("folder_id")].append(point)

    tracks_by_folder: dict[str | None, list] = defaultdict(list)
    for track in data.get("tracks", []):
        tracks_by_folder[track.get("folder_id")].append(track)

    dir_by_folder_id: dict[str | None, str] = {None: output_dir}

    def dir_for_folder(folder_id: str | None) -> str:
        if folder_id in dir_by_folder_id:
            return dir_by_folder_id[folder_id]
        folder_data = folder_by_id[folder_id]
        parent_dir = dir_for_folder(folder_data.get("parent_id"))
        folder_dir = os.path.join(parent_dir, _safe_folder_name(folder_data["name"]))
        dir_by_folder_id[folder_id] = folder_dir
        return folder_dir

    def write_gpx(folder_id: str | None) -> int:
        points = points_by_folder.get(folder_id, [])
        tracks = tracks_by_folder.get(folder_id, [])
        if not points and not tracks:
            return 0

        gpx = gpxpy.gpx.GPX()
        for point in points:
            wp = gpxpy.gpx.GPXWaypoint(
                latitude=point["latitude"],
                longitude=point["longitude"],
                name=point["name"],
                type=point.get("icon")
            )
            if point.get("time"):
                wp.time = datetime.fromisoformat(point["time"])
            gpx.waypoints.append(wp)

        for track in tracks:
            gpx_track = gpxpy.gpx.GPXTrack(name=track["name"])
            segment = gpxpy.gpx.GPXTrackSegment()
            for pt in track["points"]:
                gpx_pt = gpxpy.gpx.GPXTrackPoint(
                    latitude=pt["latitude"],
                    longitude=pt["longitude"]
                )
                if pt.get("time"):
                    gpx_pt.time = datetime.fromisoformat(pt["time"])
                segment.points.append(gpx_pt)
            gpx_track.segments.append(segment)
            gpx.tracks.append(gpx_track)

        folder_dir = dir_for_folder(folder_id)
        os.makedirs(folder_dir, exist_ok=True)
        with open(os.path.join(folder_dir, "export.gpx"), "w", encoding='utf-8') as f:
            f.write(gpx.to_xml())
        return len(points) + len(tracks)

    os.makedirs(output_dir, exist_ok=True)
    for folder_id in folder_by_id:
        os.makedirs(dir_for_folder(folder_id), exist_ok=True)

    files_written = 0
    files_written += 1 if write_gpx(None) else 0
    for folder_id in folder_by_id:
        files_written += 1 if write_gpx(folder_id) else 0

    print(f"✅ Organized GPX export saved to {output_dir} ({files_written} export.gpx files)")


def json_to_kml(json_file: str, output_file: str, compress: bool = False) -> None:
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    k = kml.KML()
    doc = kml.Document()
    k.append(doc)

    kml_folders: dict[str, Folder] = {}
    
    folders_data = data.get("folders", [])
    folder_by_id: dict[str, dict] = {f["id"]: f for f in folders_data}
    
    def get_kml_folder(folder_id: str | None) -> Document | Folder:
        """Get or create KML folder from folder ID."""
        if not folder_id:
            return doc
        if folder_id in kml_folders:
            return kml_folders[folder_id]
        
        folder_data = folder_by_id.get(folder_id)
        if not folder_data:
            return doc
        
        parent_id = folder_data.get("parent_id")
        parent = get_kml_folder(parent_id)
        
        new_folder = Folder(name=folder_data["name"])
        parent.features.append(new_folder)
        kml_folders[folder_id] = new_folder
        return new_folder

    for point in data.get("points", []):
        icon = point.get("icon")
        placemark = Placemark(
            name=point["name"],
            style_url=StyleUrl(url=icon) if icon else None,
            times=_kml_times_for_point(point),
            kml_geometry=kml_geometry.Point(
                geometry=Point(point["longitude"], point["latitude"])
            ),
        )
        folder = get_kml_folder(point.get("folder_id"))
        folder.features.append(placemark)

    for track in data.get("tracks", []):
        track_geometry = _track_kml_geometry(track)
        if track_geometry is None:
            continue
        placemark = Placemark(
            name=track["name"],
            times=_kml_times_for_track(track),
            kml_geometry=track_geometry,
        )
        folder = get_kml_folder(track.get("folder_id"))
        folder.features.append(placemark)

    kml_content = k.to_string()

    if compress:
        with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('doc.kml', kml_content)
        print(f"✅ KMZ saved to {output_file}")
    else:
        with open(output_file, "w", encoding='utf-8') as f:
            f.write(kml_content)
        print(f"✅ KML saved to {output_file}")


def json_to_kml_organized(json_file: str, output_dir: str, compress: bool = False) -> None:
    """Export JSON data as a tree of KML/KMZ files mirroring the data's folder structure.

    Creates output_dir plus one subfolder per folder in the data (nested to match
    parent_id relationships), and writes an export.kml/export.kmz file in each folder
    that has points or tracks directly assigned to it.
    """
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    folder_by_id: dict[str, dict] = {f["id"]: f for f in data.get("folders", [])}

    points_by_folder: dict[str | None, list] = defaultdict(list)
    for point in data.get("points", []):
        points_by_folder[point.get("folder_id")].append(point)

    tracks_by_folder: dict[str | None, list] = defaultdict(list)
    for track in data.get("tracks", []):
        tracks_by_folder[track.get("folder_id")].append(track)

    dir_by_folder_id: dict[str | None, str] = {None: output_dir}

    def dir_for_folder(folder_id: str | None) -> str:
        if folder_id in dir_by_folder_id:
            return dir_by_folder_id[folder_id]
        folder_data = folder_by_id[folder_id]
        parent_dir = dir_for_folder(folder_data.get("parent_id"))
        folder_dir = os.path.join(parent_dir, _safe_folder_name(folder_data["name"]))
        dir_by_folder_id[folder_id] = folder_dir
        return folder_dir

    def write_kml(folder_id: str | None) -> int:
        folder_points = points_by_folder.get(folder_id, [])
        folder_tracks = tracks_by_folder.get(folder_id, [])
        if not folder_points and not folder_tracks:
            return 0

        k = kml.KML()
        doc = kml.Document()
        k.append(doc)

        for point in folder_points:
            icon = point.get("icon")
            placemark = Placemark(
                name=point["name"],
                style_url=StyleUrl(url=icon) if icon else None,
                times=_kml_times_for_point(point),
                kml_geometry=kml_geometry.Point(
                    geometry=Point(point["longitude"], point["latitude"])
                ),
            )
            doc.features.append(placemark)

        exported_tracks = 0
        for track in folder_tracks:
            track_geometry = _track_kml_geometry(track)
            if track_geometry is None:
                continue
            placemark = Placemark(
                name=track["name"],
                times=_kml_times_for_track(track),
                kml_geometry=track_geometry,
            )
            doc.features.append(placemark)
            exported_tracks += 1

        folder_dir = dir_for_folder(folder_id)
        os.makedirs(folder_dir, exist_ok=True)
        kml_content = k.to_string()
        if compress:
            output_path = os.path.join(folder_dir, "export.kmz")
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('doc.kml', kml_content)
        else:
            output_path = os.path.join(folder_dir, "export.kml")
            with open(output_path, "w", encoding='utf-8') as f:
                f.write(kml_content)

        return len(folder_points) + exported_tracks

    os.makedirs(output_dir, exist_ok=True)
    for folder_id in folder_by_id:
        os.makedirs(dir_for_folder(folder_id), exist_ok=True)

    ext = "kmz" if compress else "kml"
    files_written = 0
    files_written += 1 if write_kml(None) else 0
    for folder_id in folder_by_id:
        files_written += 1 if write_kml(folder_id) else 0

    print(f"✅ Organized {ext.upper()} export saved to {output_dir} ({files_written} export.{ext} files)")


def delete_items(json_file: str, ids: list[str]) -> int:
    """Delete items (folders, points, tracks) by their IDs. Returns count of deleted items."""
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)
    
    ids_set = set(ids)
    deleted = 0
    
    folder_ids_to_delete: set[str] = set()
    folders_by_id = {f["id"]: f for f in data.get("folders", [])}
    
    def mark_folder_and_children(folder_id: str) -> None:
        folder_ids_to_delete.add(folder_id)
        for f in data.get("folders", []):
            if f.get("parent_id") == folder_id:
                mark_folder_and_children(f["id"])
    
    for folder in data.get("folders", []):
        if folder["id"] in ids_set:
            mark_folder_and_children(folder["id"])
    
    original_folders = len(data.get("folders", []))
    data["folders"] = [f for f in data.get("folders", []) if f["id"] not in folder_ids_to_delete]
    deleted += original_folders - len(data["folders"])
    
    original_points = len(data.get("points", []))
    data["points"] = [
        p for p in data.get("points", [])
        if p["id"] not in ids_set and p.get("folder_id") not in folder_ids_to_delete
    ]
    deleted += original_points - len(data["points"])
    
    original_tracks = len(data.get("tracks", []))
    data["tracks"] = [
        t for t in data.get("tracks", [])
        if t["id"] not in ids_set and t.get("folder_id") not in folder_ids_to_delete
    ]
    deleted += original_tracks - len(data["tracks"])
    
    with open(json_file, "w", encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return deleted


def _collect_items_by_ids(data: dict[str, Any], ids: list[str]) -> tuple[list, list, set[str]]:
    """
    Collect points and tracks matching the given IDs (including folder contents).
    Returns (points, tracks, used_folder_ids).
    """
    ids_set = set(ids)
    
    folder_ids_to_include: set[str] = set()
    
    def mark_folder_and_children(folder_id: str) -> None:
        folder_ids_to_include.add(folder_id)
        for f in data.get("folders", []):
            if f.get("parent_id") == folder_id:
                mark_folder_and_children(f["id"])
    
    for folder in data.get("folders", []):
        if folder["id"] in ids_set:
            mark_folder_and_children(folder["id"])
    
    points = [
        p for p in data.get("points", [])
        if p["id"] in ids_set or p.get("folder_id") in folder_ids_to_include
    ]
    
    tracks = [
        t for t in data.get("tracks", [])
        if t["id"] in ids_set or t.get("folder_id") in folder_ids_to_include
    ]
    
    used_folder_ids: set[str] = set()
    for p in points:
        if p.get("folder_id"):
            used_folder_ids.add(p["folder_id"])
    for t in tracks:
        if t.get("folder_id"):
            used_folder_ids.add(t["folder_id"])
    
    return points, tracks, used_folder_ids


def export_selected_gpx(json_file: str, output_file: str, ids: list[str]) -> int:
    """Export selected items to GPX. Returns count of exported items."""
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)
    
    points, tracks, _ = _collect_items_by_ids(data, ids)
    
    gpx = gpxpy.gpx.GPX()
    
    for point in points:
        wp = gpxpy.gpx.GPXWaypoint(
            latitude=point["latitude"],
            longitude=point["longitude"],
            name=point["name"],
            type=point.get("icon")
        )
        if point.get("time"):
            from datetime import datetime
            wp.time = datetime.fromisoformat(point["time"])
        gpx.waypoints.append(wp)
    
    for track in tracks:
        gpx_track = gpxpy.gpx.GPXTrack(name=track["name"])
        segment = gpxpy.gpx.GPXTrackSegment()
        for pt in track["points"]:
            gpx_pt = gpxpy.gpx.GPXTrackPoint(
                latitude=pt["latitude"],
                longitude=pt["longitude"]
            )
            if pt.get("time"):
                from datetime import datetime
                gpx_pt.time = datetime.fromisoformat(pt["time"])
            segment.points.append(gpx_pt)
        gpx_track.segments.append(segment)
        gpx.tracks.append(gpx_track)
    
    with open(output_file, "w", encoding='utf-8') as f:
        f.write(gpx.to_xml())

    return len(points) + len(tracks)


def export_selected_gpx_organized(json_file: str, output_dir: str, ids: list[str]) -> int:
    """Export selected items as a tree of GPX files mirroring folder structure. Returns count of exported items."""
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    points, tracks, used_folder_ids = _collect_items_by_ids(data, ids)

    folder_by_id: dict[str, dict] = {f["id"]: f for f in data.get("folders", [])}

    def add_folder_ancestry(folder_id: str) -> None:
        if folder_id in used_folder_ids:
            return
        used_folder_ids.add(folder_id)
        folder = folder_by_id.get(folder_id)
        if folder and folder.get("parent_id"):
            add_folder_ancestry(folder["parent_id"])

    for fid in list(used_folder_ids):
        folder = folder_by_id.get(fid)
        if folder and folder.get("parent_id"):
            add_folder_ancestry(folder["parent_id"])

    points_by_folder: dict[str | None, list] = defaultdict(list)
    for point in points:
        points_by_folder[point.get("folder_id")].append(point)

    tracks_by_folder: dict[str | None, list] = defaultdict(list)
    for track in tracks:
        tracks_by_folder[track.get("folder_id")].append(track)

    dir_by_folder_id: dict[str | None, str] = {None: output_dir}

    def dir_for_folder(folder_id: str | None) -> str:
        if folder_id in dir_by_folder_id:
            return dir_by_folder_id[folder_id]
        folder_data = folder_by_id[folder_id]
        parent_dir = dir_for_folder(folder_data.get("parent_id"))
        folder_dir = os.path.join(parent_dir, _safe_folder_name(folder_data["name"]))
        dir_by_folder_id[folder_id] = folder_dir
        return folder_dir

    def write_gpx(folder_id: str | None) -> int:
        folder_points = points_by_folder.get(folder_id, [])
        folder_tracks = tracks_by_folder.get(folder_id, [])
        if not folder_points and not folder_tracks:
            return 0

        gpx = gpxpy.gpx.GPX()
        for point in folder_points:
            wp = gpxpy.gpx.GPXWaypoint(
                latitude=point["latitude"],
                longitude=point["longitude"],
                name=point["name"],
                type=point.get("icon")
            )
            if point.get("time"):
                wp.time = datetime.fromisoformat(point["time"])
            gpx.waypoints.append(wp)

        for track in folder_tracks:
            gpx_track = gpxpy.gpx.GPXTrack(name=track["name"])
            segment = gpxpy.gpx.GPXTrackSegment()
            for pt in track["points"]:
                gpx_pt = gpxpy.gpx.GPXTrackPoint(
                    latitude=pt["latitude"],
                    longitude=pt["longitude"]
                )
                if pt.get("time"):
                    gpx_pt.time = datetime.fromisoformat(pt["time"])
                segment.points.append(gpx_pt)
            gpx_track.segments.append(segment)
            gpx.tracks.append(gpx_track)

        folder_dir = dir_for_folder(folder_id)
        os.makedirs(folder_dir, exist_ok=True)
        with open(os.path.join(folder_dir, "export.gpx"), "w", encoding='utf-8') as f:
            f.write(gpx.to_xml())
        return len(folder_points) + len(folder_tracks)

    os.makedirs(output_dir, exist_ok=True)
    total = write_gpx(None)
    for folder_id in used_folder_ids:
        total += write_gpx(folder_id)

    return total


def export_selected_kml(json_file: str, output_file: str, ids: list[str], compress: bool = False, organize: bool = True) -> int:
    """Export selected items to KML/KMZ, optionally nested into folder structure. Returns count of exported items."""
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    points, tracks, used_folder_ids = _collect_items_by_ids(data, ids)

    folders_data = data.get("folders", [])
    folder_by_id: dict[str, dict] = {f["id"]: f for f in folders_data}

    k = kml.KML()
    doc = kml.Document()
    k.append(doc)

    if not organize:
        def get_kml_folder(folder_id: str | None) -> Document | Folder:
            return doc
    else:
        def add_folder_ancestry(folder_id: str) -> None:
            if folder_id in used_folder_ids:
                return
            used_folder_ids.add(folder_id)
            folder = folder_by_id.get(folder_id)
            if folder and folder.get("parent_id"):
                add_folder_ancestry(folder["parent_id"])

        for fid in list(used_folder_ids):
            folder = folder_by_id.get(fid)
            if folder and folder.get("parent_id"):
                add_folder_ancestry(folder["parent_id"])

        kml_folders: dict[str, Folder] = {}

        def get_kml_folder(folder_id: str | None) -> Document | Folder:
            if not folder_id:
                return doc
            if folder_id in kml_folders:
                return kml_folders[folder_id]

            folder_data = folder_by_id.get(folder_id)
            if not folder_data or folder_id not in used_folder_ids:
                return doc

            parent_id = folder_data.get("parent_id")
            parent = get_kml_folder(parent_id)

            new_folder = Folder(name=folder_data["name"])
            parent.features.append(new_folder)
            kml_folders[folder_id] = new_folder
            return new_folder
    
    for point in points:
        icon = point.get("icon")
        placemark = Placemark(
            name=point["name"],
            style_url=StyleUrl(url=icon) if icon else None,
            times=_kml_times_for_point(point),
            kml_geometry=kml_geometry.Point(
                geometry=Point(point["longitude"], point["latitude"])
            ),
        )
        folder = get_kml_folder(point.get("folder_id"))
        folder.features.append(placemark)

    exported_tracks = 0
    for track in tracks:
        track_geometry = _track_kml_geometry(track)
        if track_geometry is None:
            continue
        placemark = Placemark(
            name=track["name"],
            times=_kml_times_for_track(track),
            kml_geometry=track_geometry,
        )
        folder = get_kml_folder(track.get("folder_id"))
        folder.features.append(placemark)
        exported_tracks += 1
    
    kml_content = k.to_string()
    
    if compress:
        with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('doc.kml', kml_content)
    else:
        with open(output_file, "w", encoding='utf-8') as f:
            f.write(kml_content)

    return len(points) + exported_tracks


def export_selected_kml_organized(json_file: str, output_dir: str, ids: list[str], compress: bool = False) -> int:
    """Export selected items as a tree of KML/KMZ files mirroring folder structure. Returns count of exported items."""
    with open(json_file, "r", encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)

    points, tracks, used_folder_ids = _collect_items_by_ids(data, ids)

    folder_by_id: dict[str, dict] = {f["id"]: f for f in data.get("folders", [])}

    def add_folder_ancestry(folder_id: str) -> None:
        if folder_id in used_folder_ids:
            return
        used_folder_ids.add(folder_id)
        folder = folder_by_id.get(folder_id)
        if folder and folder.get("parent_id"):
            add_folder_ancestry(folder["parent_id"])

    for fid in list(used_folder_ids):
        folder = folder_by_id.get(fid)
        if folder and folder.get("parent_id"):
            add_folder_ancestry(folder["parent_id"])

    points_by_folder: dict[str | None, list] = defaultdict(list)
    for point in points:
        points_by_folder[point.get("folder_id")].append(point)

    tracks_by_folder: dict[str | None, list] = defaultdict(list)
    for track in tracks:
        tracks_by_folder[track.get("folder_id")].append(track)

    dir_by_folder_id: dict[str | None, str] = {None: output_dir}

    def dir_for_folder(folder_id: str | None) -> str:
        if folder_id in dir_by_folder_id:
            return dir_by_folder_id[folder_id]
        folder_data = folder_by_id[folder_id]
        parent_dir = dir_for_folder(folder_data.get("parent_id"))
        folder_dir = os.path.join(parent_dir, _safe_folder_name(folder_data["name"]))
        dir_by_folder_id[folder_id] = folder_dir
        return folder_dir

    def write_kml(folder_id: str | None) -> int:
        folder_points = points_by_folder.get(folder_id, [])
        folder_tracks = tracks_by_folder.get(folder_id, [])
        if not folder_points and not folder_tracks:
            return 0

        k = kml.KML()
        doc = kml.Document()
        k.append(doc)

        for point in folder_points:
            icon = point.get("icon")
            placemark = Placemark(
                name=point["name"],
                style_url=StyleUrl(url=icon) if icon else None,
                times=_kml_times_for_point(point),
                kml_geometry=kml_geometry.Point(
                    geometry=Point(point["longitude"], point["latitude"])
                ),
            )
            doc.features.append(placemark)

        exported_tracks = 0
        for track in folder_tracks:
            track_geometry = _track_kml_geometry(track)
            if track_geometry is None:
                continue
            placemark = Placemark(
                name=track["name"],
                times=_kml_times_for_track(track),
                kml_geometry=track_geometry,
            )
            doc.features.append(placemark)
            exported_tracks += 1

        folder_dir = dir_for_folder(folder_id)
        os.makedirs(folder_dir, exist_ok=True)
        kml_content = k.to_string()
        if compress:
            with zipfile.ZipFile(os.path.join(folder_dir, "export.kmz"), 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('doc.kml', kml_content)
        else:
            with open(os.path.join(folder_dir, "export.kml"), "w", encoding='utf-8') as f:
                f.write(kml_content)

        return len(folder_points) + exported_tracks

    os.makedirs(output_dir, exist_ok=True)
    total = write_kml(None)
    for folder_id in used_folder_ids:
        total += write_kml(folder_id)

    return total


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="GPX/KML processor tool")
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser("extract", help="Extract data from GPX and KML to JSON and GeoJSON")
    extract_parser.add_argument("--input-dir", type=str, default=DATA_DIR, help="Directory with GPX and KML files")
    extract_parser.add_argument("--json-file", type=str, help="Output JSON file")
    extract_parser.add_argument("--geojson-file", type=str, help="Output GeoJSON file")
    extract_parser.add_argument("--filenames-folders", action="store_true", help="Use source filenames as folder names")

    gpx_parser = subparsers.add_parser("json2gpx", help="Convert JSON data to GPX")
    gpx_parser.add_argument("json_file", type=str, help="Input JSON file")
    gpx_parser.add_argument("output_file", type=str, help="Output GPX file, or output folder if --organized is set")
    gpx_parser.add_argument("--organized", action="store_true", help="Export a folder tree mirroring the data's folder structure, with one export.gpx per folder")

    kml_parser = subparsers.add_parser("json2kml", help="Convert JSON data to KML")
    kml_parser.add_argument("json_file", type=str, help="Input JSON file")
    kml_parser.add_argument("output_file", type=str, help="Output KML/KMZ file, or output folder if --organized is set")
    kml_parser.add_argument("--compress", action="store_true", help="Create KMZ file instead of KML")
    kml_parser.add_argument("--organized", action="store_true", help="Export a folder tree mirroring the data's folder structure, with one export.kml/export.kmz per folder")

    delete_parser = subparsers.add_parser("delete", help="Delete items by ID")
    delete_parser.add_argument("--json-file", type=str, required=True, help="JSON data file")
    delete_parser.add_argument("--ids", type=str, required=True, help="Comma-separated list of IDs to delete")

    args = parser.parse_args()

    if args.command == "extract":
        if not (args.json_file or args.geojson_file):
            print("⚠️  --json-file or --geojson-file are required for extract command")
            return

        extract_data(args.input_dir, args.json_file, args.geojson_file, args.filenames_folders)
    elif args.command == "json2gpx":
        if args.organized:
            json_to_gpx_organized(args.json_file, args.output_file)
        else:
            json_to_gpx(args.json_file, args.output_file)
    elif args.command == "json2kml":
        if args.organized:
            json_to_kml_organized(args.json_file, args.output_file, args.compress)
        else:
            json_to_kml(args.json_file, args.output_file, args.compress)
    elif args.command == "delete":
        ids = [id.strip() for id in args.ids.split(",") if id.strip()]
        if not ids:
            print("⚠️  No IDs provided")
            return
        deleted = delete_items(args.json_file, ids)
        print(f"✅ Deleted {deleted} items")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
