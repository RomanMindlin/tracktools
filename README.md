# Tools

## tracktools.py

Converts between GPX/KML track/waypoint files and a single JSON data file (plus optional GeoJSON export). All points, tracks, and folders get a stable random `id` so items can be referenced later (e.g. for delete/export).

```
tracktools.py extract --input-dir ./tracks --json-file data.json [--geojson-file data.geojson] [--filenames-folders]
tracktools.py json2gpx data.json out.gpx [--organized | --flat]
tracktools.py json2kml data.json out.kml [--compress] [--organized | --flat]   # --compress writes a .kmz
tracktools.py delete --json-file data.json --ids id1,id2,...
tracktools.py rename --json-file data.json --id id1 --name "New name"
tracktools.py move --json-file data.json --ids id1,id2,... (--destination folder_id | --root)
tracktools.py create-folder --json-file data.json --name "New folder" [--id parent_folder_id]
```

- `extract` walks `--input-dir` recursively, parses every `.gpx`/`.kml` file it finds, and writes the result to `--json-file`/`--geojson-file`. Subdirectories become folders in the output; pass `--filenames-folders` to also nest each source file's contents under a folder named after the file. If `--json-file` already exists, its folders/points/tracks are kept and the newly extracted ones are appended (merge) rather than overwriting the file — existing edits (renames, moves, manually added folders) are preserved. Re-running the same directory adds duplicate copies of its points/tracks (they get fresh IDs each time; there's no de-duplication against what's already in the file).
- `json2gpx` / `json2kml` regenerate a GPX or KML(Z) file from the JSON data (used e.g. to re-import into Google Earth or a GPS device). `output_file` becomes an output *folder* instead of a single file when `--organized` or `--flat` is given: `--organized` mirrors the full folder structure as a tree of files (a folder with no subfolders of its own becomes a single sibling file instead of a directory); `--flat` only groups by top-level folder, merging everything in a folder's subtree — however deeply nested — into that one top-level folder's file.
- `delete` removes points/tracks/folders by ID from the JSON file in place (deleting a folder also deletes its contents, recursively).
- `rename` sets a new `name` for a single point/track/folder by ID.
- `move` reparents points/tracks/folders to `--destination` (a folder ID) or `--root` (the top level). Moving a folder only changes its own parent; its contents stay nested inside it. Guards against moving a folder into itself or one of its own descendants, and silently skips items already at the destination or an unknown destination ID.
- `create-folder` adds a new folder named `--name` under `--id` (a parent folder ID), or at the top level if `--id` is omitted. Fails (no write) if `--id` doesn't match an existing folder.

The JSON schema is `{"folders": [...], "points": [...], "tracks": [...]}`; folders nest via `parent_id`, and points/tracks attach to a folder via `folder_id`. `export_selected_gpx`/`export_selected_kml` (used by `trackview.py`'s export feature) export a subset of items by ID rather than the whole file.

### Examples

Merge everything under `gpx/` and `kml/` into one JSON file (and a GeoJSON for mapping tools like geojson.io or Mapbox):

```
tracktools.py extract --input-dir . --json-file data.json --geojson-file data.geojson
```

Same, but also turn each source file into its own folder (handy when a directory holds many loosely related `.gpx`/`.kml` files instead of one folder per trip):

```
tracktools.py extract --input-dir ./gpx --json-file data.json --filenames-folders
```

Re-export the merged data back out for a GPS device or Google Earth:

```
tracktools.py json2gpx data.json export.gpx
tracktools.py json2kml data.json export.kml
tracktools.py json2kml data.json export.kmz --compress
```

Export as a folder tree instead of one file — full structure, or flattened to top-level folders only:

```
tracktools.py json2gpx data.json export_tree --organized
tracktools.py json2kml data.json export_tree --flat --compress
```

Remove a couple of stray waypoints and a whole folder (and everything nested in it) by ID — IDs come from the JSON file itself, or from `trackview.py`'s detail view:

```
tracktools.py delete --json-file data.json --ids 97a6575c46a9ac00,169aedef048377b1
```

Rename a point by ID:

```
tracktools.py rename --json-file data.json --id 97a6575c46a9ac00 --name "Trailhead"
```

Move a couple of items into another folder, or back out to the top level:

```
tracktools.py move --json-file data.json --ids 97a6575c46a9ac00,169aedef048377b1 --destination 5e1c2a9f0d3b4c71
tracktools.py move --json-file data.json --ids 97a6575c46a9ac00 --root
```

Pull in a newly-added batch of GPX/KML files without losing what's already in `data.json`:

```
tracktools.py extract --input-dir ./new-trip --json-file data.json --filenames-folders
```

Create a new top-level folder, then a subfolder inside it:

```
tracktools.py create-folder --json-file data.json --name "2025 Trips"
tracktools.py create-folder --json-file data.json --name "July" --id 5e1c2a9f0d3b4c71
```

## trackview.py

A terminal (curses) UI for browsing a `tracktools.py` JSON data file — a tree view of folders/points/tracks with details, multi-select, export, and delete.

```
trackview.py [data_file]   # defaults to ./data.json
```

If `data_file` doesn't exist, it offers to run `tracktools.py extract` against a directory you provide (with `--filenames-folders` behavior) to create it.

Keys:
- `↑/↓`, `PgUp/PgDn`, `Home/End` — navigate the list
- `←/→` — collapse/expand a folder
- `Enter` — expand/collapse a folder, or open detail view for a point/track
- `Space` — toggle multi-selection (selecting a folder implicitly selects its contents)
- `F2` — rename the item under the cursor (prompts for a new name), writing changes back to the JSON file
- `F4` — extract more data: prompts for a directory and merges its GPX/KML files into the current JSON file
- `F5` — move the current selection (or the item under the cursor, if nothing is selected) to a chosen destination folder (or the top level), writing changes back to the JSON file
- `F6` — export the current selection (or the item under the cursor, if nothing is selected) to GPX/KML/KMZ (prompts for format and filename; the "Organized"/"Flat" checkboxes produce a folder tree — full structure or top-level-folders-only — instead of a single file)
- `F7` — create a new folder: prompts you to pick a destination folder (or the top level) from the same kind of picker `F5` uses, then asks for a name
- `F8` — delete the current selection (or the item under the cursor, if nothing is selected), with confirmation, writing changes back to the JSON file
- `Esc`/`Backspace` — back out of detail view
- `q` — quit

Detail view shows coordinates and a Google Maps link for points; for tracks it shows point count, bounding box, start/end points, an approximate haversine distance, and the first 20 track points.
