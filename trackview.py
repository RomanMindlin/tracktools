#!/usr/bin/env python3
"""
Text UI viewer for data.json - displays points and tracks with folder hierarchy.
Press Enter to view details or expand/collapse folders, q to quit, Escape to go back.
"""

import argparse
import curses
import json
from math import radians, sin, cos, sqrt, atan2
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

    # Create a node for every folder, even ones with no points/tracks/subfolders
    # directly in them (get_folder_node is a no-op for folders already created,
    # since it checks folder_nodes first).
    for folder_data in folders_list:
        get_folder_node(folder_data["id"])

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


def _box_origin(stdscr, box_width: int, box_height: int) -> tuple[int, int]:
    """Compute the top-left corner to center a box of the given size on screen."""
    height, width = stdscr.getmaxyx()
    start_x = max(0, (width - box_width) // 2)
    start_y = max(0, (height - box_height) // 2)
    return start_y, start_x


def _draw_box(stdscr, start_y: int, start_x: int, box_width: int, box_height: int) -> None:
    """Draw a bordered box at the given position."""
    try:
        stdscr.addstr(start_y, start_x, "┌" + "─" * (box_width - 2) + "┐")
        for i in range(box_height - 2):
            stdscr.addstr(start_y + 1 + i, start_x, "│" + " " * (box_width - 2) + "│")
        stdscr.addstr(start_y + box_height - 1, start_x, "└" + "─" * (box_width - 2) + "┘")
    except curses.error:
        pass


def prompt_text(stdscr, label: str, default: str | None = None) -> str | None:
    """Prompt for a line of text in a small centered box.

    Returns the trimmed input, `default` if input was empty and a default was
    given, or None if input was empty with no default.
    """
    height, width = stdscr.getmaxyx()

    suffix = f" [{default}]: " if default is not None else ": "
    min_input_width = 40  # always try to leave at least this much room to type
    max_box_width = max(20, width - 4)  # keep the box fully on screen

    # label may embed a long object name (e.g. rename's "Rename 'X' to"); if
    # showing it in full would crowd out room to type, truncate the displayed
    # label rather than shrinking the input field below a usable size.
    max_label_len = max(5, max_box_width - len(suffix) - min_input_width - 4)
    if len(label) > max_label_len:
        label = label[:max_label_len - 1] + "…"

    prompt = f"{label}{suffix}"
    box_width = min(max_box_width, max(50, len(prompt) + min_input_width + 4))
    box_height = 4
    start_y, start_x = _box_origin(stdscr, box_width, box_height)

    _draw_box(stdscr, start_y, start_x, box_width, box_height)
    try:
        stdscr.addstr(start_y + 1, start_x + 2, prompt)
    except curses.error:
        pass
    stdscr.refresh()

    curses.flushinp()  # Clear any buffered input
    stdscr.timeout(-1)  # Disable timeout for blocking input
    curses.curs_set(1)  # Show cursor
    curses.echo()

    try:
        input_win_x = start_x + 2 + len(prompt)
        stdscr.move(start_y + 1, input_win_x)
        # getstr's length argument caps captured input at n-1 *raw bytes* (it
        # reserves one slot for an internal NUL terminator, and counts encoded
        # bytes, not decoded characters). Sizing this off the on-screen
        # character width silently truncates any multi-byte text (Cyrillic,
        # etc.) well before the visual limit, since those characters take 2+
        # bytes each. Use a generous fixed byte budget instead, fully decoupled
        # from the box's visual width, so real-world names are never cut.
        user_input = stdscr.getstr(start_y + 1, input_win_x, 1024)
        text = user_input.decode('utf-8').strip()
    except curses.error:
        text = ""
    finally:
        curses.noecho()
        curses.curs_set(0)
        stdscr.timeout(100)  # Restore normal timeout

    return text or default


def _make_menu(stdscr, title: str, subtitle: str = ""):
    """Create a CursesMenu that reuses the app's already-initialized curses session.

    CursesMenu normally assumes it owns the whole curses lifecycle (it calls
    initscr()/endwin() itself). Setting stdscr and a non-None parent makes it
    skip that and just reuse the already-running session instead.
    """
    from cursesmenu import CursesMenu

    class _AppMenu(CursesMenu):
        def _set_up_colors(inner_self) -> None:
            # Reuse the viewer's existing cursor color pair (black on white)
            # instead of redefining pair 1, which the viewer uses for points.
            inner_self.highlight = curses.color_pair(3)

    menu = _AppMenu(title, subtitle, show_exit_item=False)
    CursesMenu.stdscr = stdscr
    menu.parent = True
    return menu


def confirm(stdscr, title: str, subtitle: str = "", yes_label: str = "Yes", no_label: str = "No") -> bool:
    """Show a Yes/No confirmation menu. Returns True if the user confirms."""
    from cursesmenu.items import MenuItem

    class ChoiceItem(MenuItem):
        def __init__(self, label: str, value: bool) -> None:
            super().__init__(text=label, should_exit=True)
            self.value = value

        def get_return(self) -> bool:
            return self.value

    menu = _make_menu(stdscr, title, subtitle)
    menu.items.append(ChoiceItem(yes_label, True))
    menu.items.append(ChoiceItem(no_label, False))

    def choose_yes(_: int = 0) -> None:
        menu.returned_value = True
        menu.should_exit = True

    def choose_no(_: int = 0) -> None:
        menu.returned_value = False
        menu.should_exit = True

    menu.user_input_handlers[ord('y')] = choose_yes
    menu.user_input_handlers[ord('Y')] = choose_yes
    menu.user_input_handlers[ord('n')] = choose_no
    menu.user_input_handlers[ord('N')] = choose_no
    menu.user_input_handlers[ord('q')] = choose_no
    menu.user_input_handlers[ord('Q')] = choose_no
    menu.user_input_handlers[27] = choose_no  # Esc

    return bool(menu.show())


def select_from_list(stdscr, title: str, subtitle: str, options: list[str]) -> int | None:
    """Show a single-select list menu. Returns the chosen index, or None if cancelled."""
    from cursesmenu.items import MenuItem

    class ChoiceItem(MenuItem):
        def __init__(self, label: str, index: int) -> None:
            super().__init__(text=label, should_exit=True)
            self.index = index

        def get_return(self) -> int:
            return self.index

    menu = _make_menu(stdscr, title, subtitle)
    for i, label in enumerate(options):
        menu.items.append(ChoiceItem(label, i))

    def cancel(_: int = 0) -> None:
        menu.returned_value = None
        menu.should_exit = True

    menu.user_input_handlers[ord('q')] = cancel
    menu.user_input_handlers[ord('Q')] = cancel
    menu.user_input_handlers[27] = cancel  # Esc

    return menu.show()


def _folder_destination_options(
    folders: list[dict], excluded: set[str] = frozenset()
) -> tuple[list[str], list[str | None]]:
    """Build an indented, name-sorted (options, destination_ids) pair covering the
    full folder hierarchy (regardless of expand/collapse state), skipping any
    folder id in `excluded`. Always starts with a "Root (top level)" entry (None).
    """
    children_by_parent: dict[str | None, list[dict]] = {}
    for f in folders:
        if f["id"] in excluded:
            continue
        children_by_parent.setdefault(f.get("parent_id"), []).append(f)
    for children in children_by_parent.values():
        children.sort(key=lambda f: f["name"].lower())

    options = ["Root (top level)"]
    destinations: list[str | None] = [None]

    def walk(parent_id: str | None, depth: int) -> None:
        for f in children_by_parent.get(parent_id, []):
            options.append("  " * depth + f["name"])
            destinations.append(f["id"])
            walk(f["id"], depth + 1)

    walk(None, 0)
    return options, destinations


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

    def _target_ids(self) -> list[str]:
        """IDs to act on: the current multi-selection, or the item under the
        cursor if nothing is selected."""
        if self.selected_ids:
            return list(self.selected_ids)
        if self.visible_items:
            node, _ = self.visible_items[self.cursor]
            if node.id:
                return [node.id]
        return []

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
        sel_suffix = f" ({sel_count} sel)" if sel_count > 0 else ""
        status = f" ↑↓:Nav  Space:Select  F2:Rename  F4:Extract  F5:Move  F6:Export  F7:NewFolder  F8:Delete  Enter:Details  q:Quit  [{self.cursor + 1}/{len(self.visible_items)}]{sel_suffix} "
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
        return confirm(
            self.stdscr,
            f"Delete {count} item{'s' if count > 1 else ''}?",
            yes_label="Yes, delete",
            no_label="No, cancel",
        )

    def delete_selected(self, ids: list[str]) -> None:
        """Delete the given items and reload data."""
        from tracktools import delete_items

        deleted = delete_items(str(self.data_path), ids)

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

    def prompt_rename(self, current_name: str) -> str | None:
        """Prompt user for a new name. Returns the new name, or None if cancelled/invalid."""
        new_name = prompt_text(self.stdscr, f"Rename '{current_name}' to")
        if not new_name:
            self.show_message("⚠️  Name cannot be empty")
            return None
        return new_name

    def rename_current(self) -> None:
        """Rename the item under the cursor and reload data."""
        if not self.visible_items:
            return

        node, _ = self.visible_items[self.cursor]
        if not node.id:
            return

        new_name = self.prompt_rename(node.name)
        if not new_name:
            return

        from tracktools import rename_item

        renamed = rename_item(str(self.data_path), node.id, new_name)

        # Reload data
        self.data = load_data(str(self.data_path))
        self.points = self.data.get("points", [])
        self.tracks = self.data.get("tracks", [])
        self.tree = build_tree(self.data)
        self.refresh_visible_items()

        # Adjust cursor if needed
        if self.cursor >= len(self.visible_items):
            self.cursor = max(0, len(self.visible_items) - 1)

        if renamed:
            self.show_message(f"Renamed to '{new_name}'")
        else:
            self.show_message("⚠️  Item not found")

    def extract_more(self) -> None:
        """Prompt for a directory and merge its GPX/KML files into the current data file."""
        input_dir = prompt_text(self.stdscr, "Input directory")
        if not input_dir:
            return

        if not Path(input_dir).is_dir():
            self.show_message(f"⚠️  Directory not found: {input_dir}")
            return

        remove_duplicates = confirm(
            self.stdscr,
            "Remove duplicates?",
            "Skip points/tracks whose coordinates already exist in the file",
            yes_label="Yes, remove duplicates",
            no_label="No, add everything",
        )

        from tracktools import extract_data

        new_points, new_tracks, new_folders = extract_data(
            input_dir, str(self.data_path), None, use_filenames=True, remove_duplicates=remove_duplicates
        )

        # Reload data
        self.data = load_data(str(self.data_path))
        self.points = self.data.get("points", [])
        self.tracks = self.data.get("tracks", [])
        self.tree = build_tree(self.data)
        self.refresh_visible_items()

        # Adjust cursor if needed
        if self.cursor >= len(self.visible_items):
            self.cursor = max(0, len(self.visible_items) - 1)

        self.show_message(f"Added {new_points} points, {new_tracks} tracks, {new_folders} folders")

    def create_folder_here(self) -> None:
        """Create a new folder under a chosen destination folder (or root) and reload data."""
        folders = self.data.get("folders", [])
        options, destinations = _folder_destination_options(folders)

        choice = select_from_list(self.stdscr, "Create new folder", "Choose where to create it", options)
        if choice is None:
            return
        parent_id = destinations[choice]

        name = prompt_text(self.stdscr, "New folder name")
        if not name:
            return

        from tracktools import create_folder

        new_id = create_folder(str(self.data_path), name, parent_id)

        # Reload data
        self.data = load_data(str(self.data_path))
        self.points = self.data.get("points", [])
        self.tracks = self.data.get("tracks", [])
        self.tree = build_tree(self.data)
        self.refresh_visible_items()

        # Adjust cursor if needed
        if self.cursor >= len(self.visible_items):
            self.cursor = max(0, len(self.visible_items) - 1)

        if new_id:
            self.show_message(f"Created folder '{name}'")
        else:
            self.show_message("⚠️  Could not create folder")

    def show_message(self, text: str) -> None:
        """Show a brief message overlay."""
        box_width = len(text) + 4
        box_height = 3
        start_y, start_x = _box_origin(self.stdscr, box_width, box_height)

        _draw_box(self.stdscr, start_y, start_x, box_width, box_height)
        try:
            self.stdscr.addstr(start_y + 1, start_x + 2, text)
        except curses.error:
            pass

        self.stdscr.refresh()
        self.stdscr.timeout(1500)  # Wait 1.5s
        self.stdscr.getch()  # Consume any key or timeout
        self.stdscr.timeout(100)  # Restore normal timeout

    def export_dialog(self) -> tuple[str, str, bool, bool, bool] | None:
        """Show export format dialog. Returns (format, filename, compress, organized, flat) or None if cancelled."""
        from cursesmenu.items import MenuItem

        formats = [("gpx", "GPX"), ("kml", "KML"), ("kmz", "KMZ")]
        selection = {"index": 0}
        toggles = {"organized": True, "flat": False}

        class RadioItem(MenuItem):
            """A format choice; selecting it toggles the radio mark instead of exiting."""

            def __init__(self, label: str, index: int) -> None:
                super().__init__(text=label, should_exit=False)
                self.index = index

            def show(self, index_text: str) -> str:
                mark = "(*)" if selection["index"] == self.index else "( )"
                return f"{index_text} - {mark} {self.text}"

            def action(self) -> None:
                selection["index"] = self.index

        class ToggleItem(MenuItem):
            """A checkbox item; selecting it flips the checkmark instead of exiting."""

            def __init__(self, label: str, key: str) -> None:
                super().__init__(text=label, should_exit=False)
                self.key = key

            def show(self, index_text: str) -> str:
                mark = "[x]" if toggles[self.key] else "[ ]"
                return f"{index_text} - {mark} {self.text}"

            def action(self) -> None:
                toggles[self.key] = not toggles[self.key]

        class ConfirmItem(MenuItem):
            def __init__(self) -> None:
                super().__init__(text="OK", should_exit=True)

            def get_return(self) -> tuple[int, bool, bool]:
                return (selection["index"], toggles["organized"], toggles["flat"])

        class CancelItem(MenuItem):
            def __init__(self) -> None:
                super().__init__(text="Cancel", should_exit=True)

            def get_return(self) -> None:
                return None

        menu = _make_menu(self.stdscr, "Export selected items", "Choose a format, then OK or Cancel")
        for i, (_, label) in enumerate(formats):
            menu.items.append(RadioItem(label, i))
        menu.items.append(ToggleItem("Organized", "organized"))
        menu.items.append(ToggleItem("Flat (top-level folders only)", "flat"))
        menu.items.append(ConfirmItem())
        menu.items.append(CancelItem())

        def cancel_on_escape(_: int = 0) -> None:
            menu.returned_value = None
            menu.should_exit = True

        menu.user_input_handlers[27] = cancel_on_escape  # Esc

        menu_result = menu.show()
        if menu_result is None:
            return None

        result_index, organized, flat = menu_result
        fmt, _ = formats[result_index]
        compress = fmt == "kmz"
        tree_mode = organized or flat  # Flat implies a folder tree output too

        if tree_mode:
            folder_name = self.prompt_foldername("export")
            if not folder_name:
                return None
            return (fmt, folder_name, compress, tree_mode, flat)

        default_name = f"export.{fmt}"
        result = self.prompt_filename(fmt, default_name)
        if not result:
            return None
        return (result[0], result[1], compress, tree_mode, flat)

    def prompt_filename(self, fmt: str, default: str) -> tuple[str, str] | None:
        """Prompt user for filename. Returns (format, filename) or None."""
        filename = prompt_text(self.stdscr, "Filename", default)
        if not filename:
            return None

        # Ensure correct extension
        if not filename.endswith(f".{fmt}"):
            filename = f"{filename}.{fmt}"

        return (fmt, filename)

    def prompt_foldername(self, default: str) -> str | None:
        """Prompt user for an output folder name. Returns the folder name/path or None."""
        return prompt_text(self.stdscr, "Folder name", default)

    def export_selected(self, ids: list[str]) -> None:
        """Export the given items to GPX or KML/KMZ."""
        result = self.export_dialog()
        if not result:
            return

        fmt, path, compress, organized, flat = result
        ids_to_export = ids

        from tracktools import (
            export_selected_gpx,
            export_selected_gpx_organized,
            export_selected_kml,
            export_selected_kml_organized,
        )

        if fmt == "gpx":
            if organized:
                count = export_selected_gpx_organized(str(self.data_path), path, ids_to_export, flat)
            else:
                count = export_selected_gpx(str(self.data_path), path, ids_to_export)
        else:
            if organized:
                count = export_selected_kml_organized(str(self.data_path), path, ids_to_export, compress, flat)
            else:
                count = export_selected_kml(str(self.data_path), path, ids_to_export, compress, organize=False)

        self.show_message(f"Exported {count} items to {path}")

    def move_selected(self, ids: list[str]) -> None:
        """Move the given items to a chosen destination folder (or root) and reload data."""
        folders = self.data.get("folders", [])
        folder_by_id = {f["id"]: f for f in folders}

        # Folders that would create a cycle if used as the destination: any
        # folder being moved itself, plus all of its descendants.
        excluded: set[str] = set()

        def mark_folder_and_children(folder_id: str) -> None:
            excluded.add(folder_id)
            for f in folders:
                if f.get("parent_id") == folder_id:
                    mark_folder_and_children(f["id"])

        for item_id in ids:
            if item_id in folder_by_id:
                mark_folder_and_children(item_id)

        options, destinations = _folder_destination_options(folders, excluded)

        choice = select_from_list(self.stdscr, "Move selected items", "Choose a destination", options)
        if choice is None:
            return
        destination_id = destinations[choice]

        from tracktools import move_items

        moved = move_items(str(self.data_path), ids, destination_id)

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

        self.show_message(f"Moved {moved} items")

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
                    # Delete selected items, or the item under the cursor if nothing is selected
                    target_ids = self._target_ids()
                    if target_ids:
                        if self.confirm_delete(len(target_ids)):
                            self.delete_selected(target_ids)
                elif key == curses.KEY_F6:
                    # Export selected items, or the item under the cursor if nothing is selected
                    target_ids = self._target_ids()
                    if target_ids:
                        self.export_selected(target_ids)
                elif key == curses.KEY_F5:
                    # Move selected items, or the item under the cursor if nothing is selected
                    target_ids = self._target_ids()
                    if target_ids:
                        self.move_selected(target_ids)
                elif key == curses.KEY_F2:
                    # Rename item under cursor
                    self.rename_current()
                elif key == curses.KEY_F4:
                    # Extract more data from a directory into the current file
                    self.extract_more()
                elif key == curses.KEY_F7:
                    # Create a new folder next to the item under the cursor
                    self.create_folder_here()
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

    stdscr.clear()
    stdscr.refresh()
    if not confirm(
        stdscr,
        f"File not found: {data_path}",
        "Extract data from GPX/KML files?",
        yes_label="Yes, extract",
        no_label="No, exit",
    ):
        return False

    # Ask for input directory
    stdscr.clear()
    input_dir = prompt_text(stdscr, "Input directory")

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
