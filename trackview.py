#!/usr/bin/env python3
"""
Text UI viewer for data.json - displays points and tracks with folder hierarchy.
Press Enter to view details or expand/collapse folders, q to quit, Escape to go back.
"""

import argparse
import curses
import json
from pathlib import Path


def load_data(filepath: str = "data.json") -> dict:
    """Load the data.json file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def format_coordinate(lat: float, lon: float) -> str:
    """Format latitude/longitude for display."""
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{abs(lat):.6f}°{lat_dir}, {abs(lon):.6f}°{lon_dir}"


def truncate(text: str, max_len: int) -> str:
    """Truncate text to fit width."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


class TreeNode:
    """A node in the folder tree."""
    def __init__(self, name: str, is_folder: bool = False, node_id: str | None = None):
        self.name = name
        self.is_folder = is_folder
        self.expanded = False
        self.children: list['TreeNode'] = []
        self.item: dict | None = None  # For points/tracks
        self.item_type: str | None = None  # "point" or "track"
        self.depth = 0
        self.id = node_id  # Internal ID (never displayed)


def build_tree(data: dict) -> TreeNode:
    """Build a tree structure from data with folder IDs."""
    root = TreeNode("Root", is_folder=True)
    root.expanded = True  # Root should always be expanded to show top-level items
    
    # Build folder nodes from folders list
    folder_nodes: dict[str, TreeNode] = {}  # folder_id -> TreeNode
    folders_list = data.get("folders", [])
    folder_data_by_id: dict[str, dict] = {f["id"]: f for f in folders_list}
    
    def get_folder_node(folder_id: str | None) -> TreeNode:
        if not folder_id:
            return root
        if folder_id in folder_nodes:
            return folder_nodes[folder_id]
        
        folder_data = folder_data_by_id.get(folder_id)
        if not folder_data:
            return root
        
        # Get or create parent first
        parent_id = folder_data.get("parent_id")
        parent = get_folder_node(parent_id)
        
        # Create this folder node
        node = TreeNode(folder_data["name"], is_folder=True, node_id=folder_id)
        node.depth = parent.depth + 1 if parent != root else 1
        parent.children.append(node)
        folder_nodes[folder_id] = node
        return node
    
    # Add points
    for point in data.get("points", []):
        folder_id = point.get("folder_id")
        parent = get_folder_node(folder_id)
        node = TreeNode(point.get("name", "Unnamed"), node_id=point.get("id"))
        node.item = point
        node.item_type = "point"
        node.depth = parent.depth + 1 if parent != root else 0
        parent.children.append(node)
    
    # Add tracks
    for track in data.get("tracks", []):
        folder_id = track.get("folder_id")
        parent = get_folder_node(folder_id)
        node = TreeNode(track.get("name", "Unnamed"), node_id=track.get("id"))
        node.item = track
        node.item_type = "track"
        node.depth = parent.depth + 1 if parent != root else 0
        parent.children.append(node)
    
    # Sort children: folders first, then by name
    def sort_children(node: TreeNode):
        node.children.sort(key=lambda n: (not n.is_folder, n.name.lower()))
        for child in node.children:
            if child.is_folder:
                sort_children(child)
    
    sort_children(root)
    
    return root


def flatten_tree(node: TreeNode, depth: int = 0) -> list[tuple[TreeNode, int]]:
    """Flatten tree to visible items list with depth info."""
    result = []
    
    # Don't show root itself
    if node.name != "Root":
        result.append((node, depth))
    
    if node.is_folder and node.expanded:
        for child in node.children:
            child_depth = depth + 1 if node.name != "Root" else 0
            result.extend(flatten_tree(child, child_depth))
    
    return result


class DataViewer:
    def __init__(self, stdscr, data: dict, data_path: Path):
        self.stdscr = stdscr
        self.data = data
        self.data_path = data_path
        self.points = data.get("points", [])
        self.tracks = data.get("tracks", [])
        
        # Build tree structure
        self.tree = build_tree(data)
        self.visible_items: list[tuple[TreeNode, int]] = []
        self.refresh_visible_items()
        
        self.cursor = 0
        self.scroll_offset = 0
        self.mode = "list"  # "list" or "detail"
        self.detail_scroll = 0
        self.selected_node: TreeNode | None = None
        self.selected_ids: set[str] = set()  # Multi-selection by ID
        
        # Colors
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)      # Points
        curses.init_pair(2, curses.COLOR_GREEN, -1)     # Tracks
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Cursor
        curses.init_pair(4, curses.COLOR_YELLOW, -1)    # Header
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)   # Status bar
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)   # Folders
        curses.init_pair(7, curses.COLOR_RED, -1)       # Multi-selected
        
        curses.curs_set(0)  # Hide cursor
        self.stdscr.timeout(100)

    def refresh_visible_items(self):
        """Rebuild the visible items list from tree."""
        self.visible_items = flatten_tree(self.tree)

    def count_folder_contents(self, node: TreeNode) -> tuple[int, int]:
        """Count points and tracks in a folder recursively."""
        points = 0
        tracks = 0
        for child in node.children:
            if child.is_folder:
                p, t = self.count_folder_contents(child)
                points += p
                tracks += t
            elif child.item_type == "point":
                points += 1
            elif child.item_type == "track":
                tracks += 1
        return points, tracks

    def is_in_selected_folder(self, node: TreeNode) -> bool:
        """Check if node is inside a selected folder (inherited selection)."""
        # For points/tracks - check their folder_id
        item = node.item
        if item:
            folder_id = item.get("folder_id")
            while folder_id:
                if folder_id in self.selected_ids:
                    return True
                # Look up parent of this folder
                folder_id = self._get_folder_parent_id(folder_id)
            return False
        
        # For folders - check parent hierarchy
        if node.is_folder and node.id:
            parent_id = self._get_folder_parent_id(node.id)
            while parent_id:
                if parent_id in self.selected_ids:
                    return True
                parent_id = self._get_folder_parent_id(parent_id)
        return False

    def _get_folder_parent_id(self, folder_id: str) -> str | None:
        """Get parent_id of a folder from the data."""
        for folder in self.data.get("folders", []):
            if folder["id"] == folder_id:
                return folder.get("parent_id")
        return None

    def has_selected_descendants(self, node: TreeNode) -> bool:
        """Check if folder has any selected descendants (for partial selection marker)."""
        for child in node.children:
            if child.id and child.id in self.selected_ids:
                return True
            if child.is_folder and self.has_selected_descendants(child):
                return True
        return False

    def collect_descendant_ids(self, node: TreeNode) -> set[str]:
        """Collect all descendant IDs (folders, points, tracks) of a node."""
        ids: set[str] = set()
        for child in node.children:
            if child.id:
                ids.add(child.id)
            if child.is_folder:
                ids.update(self.collect_descendant_ids(child))
        return ids

    def get_list_height(self) -> int:
        """Get available height for the list."""
        height, _ = self.stdscr.getmaxyx()
        return height - 4  # Header + status bar + borders

    def draw_list(self):
        """Draw the main list view."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        list_height = self.get_list_height()
        
        # Header
        header = f" GPS Data Viewer - {len(self.points)} points, {len(self.tracks)} tracks "
        self.stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        self.stdscr.addstr(0, 0, header.center(width)[:width-1])
        self.stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        
        # Column headers
        self.stdscr.attron(curses.A_DIM)
        col_header = f"{'Type':<7} {'Name':<40} {'Info'}"
        self.stdscr.addstr(1, 0, col_header[:width-1])
        self.stdscr.attroff(curses.A_DIM)
        
        # Separator
        self.stdscr.addstr(2, 0, "─" * (width - 1))
        
        # Adjust scroll to keep cursor visible
        if self.cursor < self.scroll_offset:
            self.scroll_offset = self.cursor
        elif self.cursor >= self.scroll_offset + list_height:
            self.scroll_offset = self.cursor - list_height + 1
        
        # Draw items
        for i in range(list_height):
            idx = self.scroll_offset + i
            if idx >= len(self.visible_items):
                break
            
            node, depth = self.visible_items[idx]
            row = 3 + i
            
            # Prepare display text
            indent = "  " * depth
            
            if node.is_folder:
                arrow = "▼ " if node.expanded else "▶ "
                type_str = "[F]"
                name = f"{indent}{arrow}{node.name}"
                points, tracks = self.count_folder_contents(node)
                info = f"{points}P, {tracks}T"
                color = curses.color_pair(6)
            elif node.item_type == "point":
                type_str = "[P]"
                name = f"{indent}{node.name}"
                item = node.item
                info = format_coordinate(item["latitude"], item["longitude"])
                color = curses.color_pair(1)
            else:  # track
                type_str = "[T]"
                name = f"{indent}{node.name}"
                item = node.item
                info = f"{len(item.get('points', []))} points"
                color = curses.color_pair(2)
            
            # Truncate for display
            name_width = min(40, width - 15)
            name_display = truncate(name, name_width)
            info_width = max(10, width - name_width - 10)
            info_display = truncate(info, info_width)
            
            # Determine selection state and marker
            is_directly_selected = node.id and node.id in self.selected_ids
            is_inherited = self.is_in_selected_folder(node)
            is_partial = node.is_folder and not is_directly_selected and self.has_selected_descendants(node)
            
            if is_directly_selected or is_inherited:
                marker = "⦿ "
                show_selected_style = True
            elif is_partial:
                marker = "○ "
                show_selected_style = False
            else:
                marker = "  "
                show_selected_style = False
            
            line = f"{marker}{type_str:<5} {name_display:<{name_width}} {info_display}"
            line = line[:width-1]
            
            # Draw with appropriate style
            if idx == self.cursor:
                self.stdscr.attron(curses.color_pair(3))
                self.stdscr.addstr(row, 0, line.ljust(width-1))
                self.stdscr.attroff(curses.color_pair(3))
            elif show_selected_style:
                self.stdscr.attron(curses.color_pair(7) | curses.A_BOLD)
                self.stdscr.addstr(row, 0, line)
                self.stdscr.attroff(curses.color_pair(7) | curses.A_BOLD)
            else:
                self.stdscr.attron(color)
                self.stdscr.addstr(row, 0, line)
                self.stdscr.attroff(color)
        
        # Status bar
        sel_count = len(self.selected_ids)
        if sel_count > 0:
            status = f" ↑↓:Nav  Space:Select  F6:Export  F8:Delete  Enter:Details  q:Quit  [{self.cursor + 1}/{len(self.visible_items)}] ({sel_count} sel) "
        else:
            status = f" ↑↓:Nav  Space:Select  Enter:Details/Toggle  q:Quit  [{self.cursor + 1}/{len(self.visible_items)}] "
        self.stdscr.attron(curses.color_pair(5))
        try:
            self.stdscr.addstr(height - 1, 0, status.ljust(width - 1)[:width-1])
        except curses.error:
            pass
        self.stdscr.attroff(curses.color_pair(5))
        
        self.stdscr.refresh()

    def draw_detail(self):
        """Draw the detail view for selected item."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        
        node = self.selected_node
        item = node.item
        item_type = node.item_type
        
        # Header
        if item_type == "point":
            header = f" Point: {item.get('name', 'Unnamed')} "
        else:
            header = f" Track: {item.get('name', 'Unnamed')} "
        
        self.stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        self.stdscr.addstr(0, 0, header.center(width)[:width-1])
        self.stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
        
        self.stdscr.addstr(1, 0, "─" * (width - 1))
        
        # Build detail lines
        lines = []
        if item_type == "point":
            lines.append(f"Name: {item.get('name', 'Unnamed')}")
            if item.get('folder'):
                lines.append(f"Folder: {item['folder']}")
            if item.get('time'):
                lines.append(f"Time: {item['time']}")
            lines.append(f"Latitude: {item['latitude']}")
            lines.append(f"Longitude: {item['longitude']}")
            lines.append(f"Coordinates: {format_coordinate(item['latitude'], item['longitude'])}")
            icon = item.get('icon', '')
            if icon and not icon.startswith('<'):  # Skip XML-style icons
                lines.append(f"Icon: {icon}")
            lines.append("")
            lines.append("Google Maps Link:")
            lines.append(f"https://www.google.com/maps?q={item['latitude']},{item['longitude']}")
        else:
            track_points = item.get('points', [])
            lines.append(f"Name: {item.get('name', 'Unnamed')}")
            if item.get('folder'):
                lines.append(f"Folder: {item['folder']}")
            if item.get('start_time') or item.get('end_time'):
                lines.append(f"Start: {item.get('start_time', 'N/A')}")
                lines.append(f"End: {item.get('end_time', 'N/A')}")
            lines.append(f"Total Points: {len(track_points)}")
            
            if track_points:
                # Calculate bounds
                lats = [p['latitude'] for p in track_points]
                lons = [p['longitude'] for p in track_points]
                lines.append("")
                lines.append("Bounds:")
                lines.append(f"  Latitude:  {min(lats):.6f} to {max(lats):.6f}")
                lines.append(f"  Longitude: {min(lons):.6f} to {max(lons):.6f}")
                
                # Start and end points
                lines.append("")
                lines.append("Start Point:")
                lines.append(f"  {format_coordinate(track_points[0]['latitude'], track_points[0]['longitude'])}")
                lines.append("End Point:")
                lines.append(f"  {format_coordinate(track_points[-1]['latitude'], track_points[-1]['longitude'])}")
                
                # Calculate approximate distance
                try:
                    total_dist = 0
                    from math import radians, sin, cos, sqrt, atan2
                    for i in range(1, len(track_points)):
                        lat1, lon1 = radians(track_points[i-1]['latitude']), radians(track_points[i-1]['longitude'])
                        lat2, lon2 = radians(track_points[i]['latitude']), radians(track_points[i]['longitude'])
                        dlat = lat2 - lat1
                        dlon = lon2 - lon1
                        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                        c = 2 * atan2(sqrt(a), sqrt(1-a))
                        total_dist += 6371 * c  # Earth radius in km
                    
                    lines.append("")
                    lines.append(f"Approximate Distance: {total_dist:.2f} km")
                except:
                    pass
                
                lines.append("")
                lines.append("Track Points (first 20):")
                for i, p in enumerate(track_points[:20]):
                    coord = format_coordinate(p['latitude'], p['longitude'])
                    if p.get('time'):
                        lines.append(f"  {i+1:4d}. {coord}  [{p['time']}]")
                    else:
                        lines.append(f"  {i+1:4d}. {coord}")
                if len(track_points) > 20:
                    lines.append(f"  ... and {len(track_points) - 20} more points")
        
        # Draw detail lines with scrolling
        detail_height = height - 4
        max_scroll = max(0, len(lines) - detail_height)
        self.detail_scroll = max(0, min(self.detail_scroll, max_scroll))
        
        for i in range(detail_height):
            line_idx = self.detail_scroll + i
            if line_idx >= len(lines):
                break
            line = lines[line_idx][:width-1]
            try:
                self.stdscr.addstr(2 + i, 0, line)
            except curses.error:
                pass
        
        # Scroll indicator
        if len(lines) > detail_height:
            scroll_info = f" [{self.detail_scroll + 1}-{min(self.detail_scroll + detail_height, len(lines))}/{len(lines)}] "
            try:
                self.stdscr.addstr(height - 2, width - len(scroll_info) - 1, scroll_info)
            except curses.error:
                pass
        
        # Status bar
        status = " ↑↓:Scroll  Esc/Backspace:Back  q:Quit "
        self.stdscr.attron(curses.color_pair(5))
        try:
            self.stdscr.addstr(height - 1, 0, status.ljust(width - 1)[:width-1])
        except curses.error:
            pass
        self.stdscr.attroff(curses.color_pair(5))
        
        self.stdscr.refresh()

    def confirm_delete(self, count: int) -> bool:
        """Show delete confirmation dialog. Returns True if confirmed."""
        height, width = self.stdscr.getmaxyx()
        
        lines = [
            f"Delete {count} selected item{'s' if count > 1 else ''}?",
            "",
            "[Y] Yes, delete    [N] No, cancel"
        ]
        
        max_line_len = max(len(line) for line in lines)
        box_width = max_line_len + 4
        box_height = len(lines) + 2
        
        start_x = max(0, (width - box_width) // 2)
        start_y = max(0, (height - box_height) // 2)
        
        # Draw box
        try:
            self.stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
            for i in range(box_height - 2):
                self.stdscr.addstr(start_y + 1 + i, start_x, "│" + " " * (box_width - 2) + "│")
            self.stdscr.addstr(start_y + box_height - 1, start_x, "└" + "─" * (box_width - 2) + "┘")
        except curses.error:
            pass
        
        # Draw content
        for i, line in enumerate(lines):
            x = start_x + 2 + (max_line_len - len(line)) // 2
            try:
                self.stdscr.addstr(start_y + 1 + i, x, line)
            except curses.error:
                pass
        
        self.stdscr.refresh()
        
        while True:
            key = self.stdscr.getch()
            if key in (ord('y'), ord('Y')):
                return True
            elif key in (ord('n'), ord('N'), 27, ord('q'), ord('Q')):
                return False

    def delete_selected(self) -> None:
        """Delete selected items and reload data."""
        from tracktools import delete_items
        
        ids_to_delete = list(self.selected_ids)
        deleted = delete_items(str(self.data_path), ids_to_delete)
        
        # Reload data
        self.data = load_data(str(self.data_path))
        self.points = self.data.get("points", [])
        self.tracks = self.data.get("tracks", [])
        self.tree = build_tree(self.data)
        self.selected_ids.clear()
        self.refresh_visible_items()
        
        # Adjust cursor if needed
        if self.cursor >= len(self.visible_items):
            self.cursor = max(0, len(self.visible_items) - 1)
        
        # Show result
        self.show_message(f"Deleted {deleted} items")

    def show_message(self, text: str) -> None:
        """Show a brief message overlay."""
        height, width = self.stdscr.getmaxyx()
        
        box_width = len(text) + 4
        box_height = 3
        
        start_x = max(0, (width - box_width) // 2)
        start_y = max(0, (height - box_height) // 2)
        
        try:
            self.stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
            self.stdscr.addstr(start_y + 1, start_x, "│" + " " * (box_width - 2) + "│")
            self.stdscr.addstr(start_y + 2, start_x, "└" + "─" * (box_width - 2) + "┘")
            self.stdscr.addstr(start_y + 1, start_x + 2, text)
        except curses.error:
            pass
        
        self.stdscr.refresh()
        self.stdscr.timeout(1500)  # Wait 1.5s
        self.stdscr.getch()  # Consume any key or timeout
        self.stdscr.timeout(100)  # Restore normal timeout

    def export_dialog(self) -> tuple[str, str, bool] | None:
        """Show export format dialog. Returns (format, filename, compress) or None if cancelled."""
        height, width = self.stdscr.getmaxyx()
        
        lines = [
            "Export selected items",
            "",
            "[G] GPX    [K] KML    [Z] KMZ    [Esc] Cancel"
        ]
        
        max_line_len = max(len(line) for line in lines)
        box_width = max_line_len + 4
        box_height = len(lines) + 2
        
        start_x = max(0, (width - box_width) // 2)
        start_y = max(0, (height - box_height) // 2)
        
        # Draw box
        try:
            self.stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
            for i in range(box_height - 2):
                self.stdscr.addstr(start_y + 1 + i, start_x, "│" + " " * (box_width - 2) + "│")
            self.stdscr.addstr(start_y + box_height - 1, start_x, "└" + "─" * (box_width - 2) + "┘")
        except curses.error:
            pass
        
        for i, line in enumerate(lines):
            x = start_x + 2 + (max_line_len - len(line)) // 2
            try:
                self.stdscr.addstr(start_y + 1 + i, x, line)
            except curses.error:
                pass
        
        self.stdscr.refresh()
        
        # Get format choice
        compress = False
        while True:
            key = self.stdscr.getch()
            if key in (ord('g'), ord('G')):
                fmt = "gpx"
                break
            elif key in (ord('k'), ord('K')):
                fmt = "kml"
                break
            elif key in (ord('z'), ord('Z')):
                fmt = "kmz"
                compress = True
                break
            elif key in (27, ord('q'), ord('Q')):
                return None
        
        # Prompt for filename
        default_name = f"export.{fmt}"
        result = self.prompt_filename(fmt, default_name)
        if not result:
            return None
        return (result[0], result[1], compress)

    def prompt_filename(self, fmt: str, default: str) -> tuple[str, str] | None:
        """Prompt user for filename. Returns (format, filename) or None."""
        height, width = self.stdscr.getmaxyx()
        
        prompt = f"Filename [{default}]: "
        box_width = max(50, len(prompt) + 10)
        box_height = 4
        
        start_x = max(0, (width - box_width) // 2)
        start_y = max(0, (height - box_height) // 2)
        
        # Draw box
        try:
            self.stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
            for i in range(box_height - 2):
                self.stdscr.addstr(start_y + 1 + i, start_x, "│" + " " * (box_width - 2) + "│")
            self.stdscr.addstr(start_y + box_height - 1, start_x, "└" + "─" * (box_width - 2) + "┘")
            self.stdscr.addstr(start_y + 1, start_x + 2, prompt)
        except curses.error:
            pass
        
        self.stdscr.refresh()
        curses.flushinp()  # Clear any buffered input
        self.stdscr.timeout(-1)  # Disable timeout for blocking input
        curses.curs_set(1)  # Show cursor
        curses.echo()
        
        try:
            input_win_x = start_x + 2 + len(prompt)
            self.stdscr.move(start_y + 1, input_win_x)
            user_input = self.stdscr.getstr(start_y + 1, input_win_x, box_width - len(prompt) - 4)
            filename = user_input.decode('utf-8').strip() or default
        except curses.error:
            filename = default
        finally:
            curses.noecho()
            curses.curs_set(0)
            self.stdscr.timeout(100)  # Restore normal timeout
        
        if not filename:
            return None
        
        # Ensure correct extension
        if not filename.endswith(f".{fmt}"):
            filename = f"{filename}.{fmt}"
        
        return (fmt, filename)

    def export_selected(self) -> None:
        """Export selected items to GPX or KML/KMZ."""
        result = self.export_dialog()
        if not result:
            return
        
        fmt, filename, compress = result
        ids_to_export = list(self.selected_ids)
        
        from tracktools import export_selected_gpx, export_selected_kml
        
        if fmt == "gpx":
            count = export_selected_gpx(str(self.data_path), filename, ids_to_export)
        else:
            count = export_selected_kml(str(self.data_path), filename, ids_to_export, compress)
        
        self.show_message(f"Exported {count} items to {filename}")

    def run(self):
        """Main event loop."""
        while True:
            if self.mode == "list":
                self.draw_list()
            else:
                self.draw_detail()
            
            try:
                key = self.stdscr.getch()
            except:
                continue
            
            if key == -1:
                continue
            
            if key == ord('q') or key == ord('Q'):
                break
            
            if self.mode == "list":
                if key == curses.KEY_UP or key == ord('k'):
                    self.cursor = max(0, self.cursor - 1)
                elif key == curses.KEY_DOWN or key == ord('j'):
                    self.cursor = min(len(self.visible_items) - 1, self.cursor + 1)
                elif key == curses.KEY_PPAGE:  # Page Up
                    self.cursor = max(0, self.cursor - self.get_list_height())
                elif key == curses.KEY_NPAGE:  # Page Down
                    self.cursor = min(len(self.visible_items) - 1, self.cursor + self.get_list_height())
                elif key == curses.KEY_HOME:
                    self.cursor = 0
                elif key == curses.KEY_END:
                    self.cursor = len(self.visible_items) - 1
                elif key == curses.KEY_LEFT:
                    # Collapse current folder or parent
                    if self.visible_items:
                        node, _ = self.visible_items[self.cursor]
                        if node.is_folder and node.expanded:
                            node.expanded = False
                            self.refresh_visible_items()
                elif key == curses.KEY_RIGHT:
                    # Expand folder
                    if self.visible_items:
                        node, _ = self.visible_items[self.cursor]
                        if node.is_folder and not node.expanded:
                            node.expanded = True
                            self.refresh_visible_items()
                elif key == ord('\n') or key == curses.KEY_ENTER:
                    if self.visible_items:
                        node, _ = self.visible_items[self.cursor]
                        if node.is_folder:
                            node.expanded = not node.expanded
                            self.refresh_visible_items()
                        else:
                            self.selected_node = node
                            self.detail_scroll = 0
                            self.mode = "detail"
                elif key == ord(' '):
                    # Toggle selection
                    if self.visible_items:
                        node, _ = self.visible_items[self.cursor]
                        if node.id:
                            if node.id in self.selected_ids:
                                # Directly selected - unselect
                                self.selected_ids.discard(node.id)
                            elif node.is_folder and self.has_selected_descendants(node):
                                # Partially selected folder - unselect all descendants
                                descendant_ids = self.collect_descendant_ids(node)
                                self.selected_ids -= descendant_ids
                            else:
                                # Not selected - select
                                self.selected_ids.add(node.id)
                            # Move cursor down if not last item
                            if self.cursor < len(self.visible_items) - 1:
                                self.cursor += 1
                elif key == curses.KEY_F8:
                    # Delete selected items
                    if self.selected_ids:
                        if self.confirm_delete(len(self.selected_ids)):
                            self.delete_selected()
                elif key == curses.KEY_F6:
                    # Export selected items
                    if self.selected_ids:
                        self.export_selected()
            else:  # detail mode
                if key == 27 or key == curses.KEY_BACKSPACE or key == 127:  # Escape or Backspace
                    self.mode = "list"
                elif key == curses.KEY_UP or key == ord('k'):
                    self.detail_scroll = max(0, self.detail_scroll - 1)
                elif key == curses.KEY_DOWN or key == ord('j'):
                    self.detail_scroll += 1
                elif key == curses.KEY_PPAGE:
                    self.detail_scroll = max(0, self.detail_scroll - 10)
                elif key == curses.KEY_NPAGE:
                    self.detail_scroll += 10


def prompt_extract_data(stdscr, data_path: Path) -> bool:
    """Show a prompt asking to extract data. Returns True if extraction successful."""
    height, width = stdscr.getmaxyx()
    
    # First dialog: ask to extract
    lines = [
        f"File not found: {data_path}",
        "",
        "Extract data from GPX/KML files?",
        "",
        "[Y] Yes, extract    [N] No, exit"
    ]
    
    max_line_len = max(len(line) for line in lines)
    box_width = max_line_len + 4
    box_height = len(lines) + 2
    
    start_x = max(0, (width - box_width) // 2)
    start_y = max(0, (height - box_height) // 2)
    
    stdscr.clear()
    try:
        stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
        for i in range(box_height - 2):
            stdscr.addstr(start_y + 1 + i, start_x, "│" + " " * (box_width - 2) + "│")
        stdscr.addstr(start_y + box_height - 1, start_x, "└" + "─" * (box_width - 2) + "┘")
    except curses.error:
        pass
    
    for i, line in enumerate(lines):
        x = start_x + 2 + (max_line_len - len(line)) // 2
        try:
            stdscr.addstr(start_y + 1 + i, x, line)
        except curses.error:
            pass
    
    stdscr.refresh()
    
    while True:
        key = stdscr.getch()
        if key in (ord('y'), ord('Y')):
            break
        elif key in (ord('n'), ord('N'), 27, ord('q'), ord('Q')):
            return False
    
    # Second dialog: ask for input directory
    prompt = f"Input directory: "
    box_width = max(60, len(prompt) + 20)
    box_height = 4
    
    start_x = max(0, (width - box_width) // 2)
    start_y = max(0, (height - box_height) // 2)
    
    stdscr.clear()
    try:
        stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
        for i in range(box_height - 2):
            stdscr.addstr(start_y + 1 + i, start_x, "│" + " " * (box_width - 2) + "│")
        stdscr.addstr(start_y + box_height - 1, start_x, "└" + "─" * (box_width - 2) + "┘")
        stdscr.addstr(start_y + 1, start_x + 2, prompt)
    except curses.error:
        pass
    
    stdscr.refresh()
    curses.flushinp()
    stdscr.timeout(-1)
    curses.curs_set(1)
    curses.echo()
    
    try:
        input_x = start_x + 2 + len(prompt)
        stdscr.move(start_y + 1, input_x)
        user_input = stdscr.getstr(start_y + 1, input_x, box_width - len(prompt) - 4)
        input_dir = user_input.decode('utf-8').strip()
    except curses.error:
        input_dir = None
    finally:
        curses.noecho()
        curses.curs_set(0)
        stdscr.timeout(100)
    
    # Check if directory exists
    if not input_dir or not Path(input_dir).is_dir():
        stdscr.clear()
        msg = f"Directory not found: {input_dir}" if input_dir else "Incorrect input"
        try:
            stdscr.addstr(height // 2, (width - len(msg)) // 2, msg)
            stdscr.addstr(height // 2 + 2, (width - 20) // 2, "Press any key to exit")
        except curses.error:
            pass
        stdscr.refresh()
        stdscr.timeout(-1)
        stdscr.getch()
        return False
    
    # Show extracting message
    stdscr.clear()
    msg = f"Extracting from {input_dir}..."
    try:
        stdscr.addstr(height // 2, (width - len(msg)) // 2, msg)
    except curses.error:
        pass
    stdscr.refresh()
    
    # Run extraction
    from tracktools import extract_data
    extract_data(input_dir, str(data_path), None, use_filenames=True)
    
    return True


def main(stdscr, data_path: Path):
    """Main function wrapped for curses."""
    if not data_path.exists():
        if not prompt_extract_data(stdscr, data_path):
            return
    
    data = load_data(str(data_path))
    viewer = DataViewer(stdscr, data, data_path)
    viewer.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPS Data Viewer")
    parser.add_argument("data_file", nargs="?", default="data.json",
                        help="Path to JSON data file (default: data.json)")
    args = parser.parse_args()
    
    curses.wrapper(main, Path(args.data_file))
