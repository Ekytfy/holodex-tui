#!/usr/bin/env python3
"""
holodex-tui - Minimal TUI for Holodex livestreams
Usage: python holodex-tui.py [--org ORG] [--hours HOURS]

Keys (main):
  ↑/↓ or j/k    Navigate
  Enter          Open in mpv
  o              Change organization
  r              Refresh streams
  ?              Toggle help
  q              Quit

Keys (org picker):
  ↑/↓ or j/k    Navigate
  Enter          Pick org
  /              Search/filter
  f              Toggle favorite
  R              Refresh org list
  Esc            Clear search / cancel
  g/G              Jump to top/bottom
  q              Cancel
"""

import os
import sys
import json
import argparse
import subprocess
import select
import time
import termios
import tty
from pathlib import Path
from datetime import datetime, timezone

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.align import Align
from rich import box

console = Console()
API_URL = "https://holodex.net/api/v2/live"
CHANNELS_URL = "https://holodex.net/api/v2/channels"
REFRESH_INTERVAL = 60
MAX_CHANNELS = 10000
PAGE_SIZE = 20

FALLBACK_ORGS = [
    "Hololive", "Holostars", "Nijisanji", "Nijisanji EN",
    "Phase-Connect", "VShojo", "idol Corp", "PRISM Project", "Independent",
]


def get_config_path():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    config_dir = base / "holodex-tui"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"


def load_config():
    path = get_config_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config):
    path = get_config_path()
    path.write_text(json.dumps(config, indent=2))
    os.chmod(path, 0o600)


def flush_input():
    try:
        fd = sys.stdin.fileno()
        termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:
        pass


def get_api_key():
    key = os.environ.get("HOLODEX_API_KEY", "").strip()
    if key:
        return key
    config = load_config()
    key = config.get("api_key", "").strip()
    if key:
        return key

    console.print()
    console.print(
        Panel(
            "[bold yellow]Holodex API Key Required[/bold yellow]\n\n"
            "1. Go to [link=https://holodex.net]https://holodex.net[/link] and log in\n"
            "2. Open Account Settings and copy your API Key\n\n"
            "Paste it below and press Enter:",
            border_style="yellow",
        )
    )
    console.print()
    while True:
        key = console.input("[bold]API Key:[/bold] ").strip()
        if not key:
            console.print("[red]Key cannot be empty.[/red]\n")
            continue
        console.print("[dim]Validating...[/dim]", end=" ")
        test_headers = {
            "User-Agent": "holodex-tui/1.0",
            "Accept": "application/json",
            "X-APIKEY": key,
        }
        try:
            resp = requests.get(
                API_URL, headers=test_headers,
                params={"org": "Hololive", "status": "live", "limit": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                save_config({"api_key": key})
                console.print("[green]✓ Saved![/green]\n")
                return key
            elif resp.status_code == 403:
                console.print("[red]✗ Invalid key. Try again.[/red]\n")
            else:
                console.print(f"[red]✗ Error {resp.status_code}. Try again.[/red]\n")
        except requests.RequestException as e:
            console.print(f"[red]✗ Network error: {e}. Try again.[/red]\n")


class HolodexTUI:
    def __init__(self, org="Hololive", upcoming_hours=24):
        self.org = org
        self.upcoming_hours = upcoming_hours
        self.streams = []
        self.selected = 0
        self.scroll_top = 0
        self.last_fetch = 0
        self.needs_redraw = True
        self.show_help = False
        self.error_msg = None
        self.mpv_ok = subprocess.run(["which", "mpv"], capture_output=True).returncode == 0

        self.mode = "main"
        self.org_selected = 0
        self.org_scroll_top = 0
        self.orgs = []
        self.org_error = None
        self.custom_buf = ""
        self.fetching_orgs = False

        # Search state
        self.search_query = ""
        self.search_mode = False

        # Favorites
        config = load_config()
        self.fav_orgs = set(config.get("fav_orgs", []))

        api_key = get_api_key()
        self.headers = {
            "User-Agent": "holodex-tui/1.0",
            "Accept": "application/json",
            "X-APIKEY": api_key,
        }

    def _load_cached_orgs(self):
        config = load_config()
        cached = config.get("orgs", [])
        if cached:
            return cached
        return None

    def _save_cached_orgs(self, orgs):
        config = load_config()
        config["orgs"] = orgs
        save_config(config)

    def _save_favs(self):
        config = load_config()
        config["fav_orgs"] = sorted(self.fav_orgs)
        save_config(config)

    def _sorted_orgs(self):
        """Return orgs sorted: favorites first, then alphabetically."""
        return sorted(self.orgs, key=lambda o: (o not in self.fav_orgs, o.lower()))

    def _filtered_orgs(self):
        """Return orgs filtered by search query."""
        sorted_orgs = self._sorted_orgs()
        if not self.search_query:
            return sorted_orgs
        q = self.search_query.lower()
        return [o for o in sorted_orgs if q in o.lower()]

    def fetch_orgs(self, force=False):
        if not force:
            cached = self._load_cached_orgs()
            if cached:
                self.orgs = cached
                self.org_error = None
                return

        self.fetching_orgs = True
        self.needs_redraw = True
        self._draw()
        sys.stdout.flush()

        orgs = set()
        try:
            offset = 0
            limit = 100
            while offset < MAX_CHANNELS:
                params = {"type": "vtuber", "limit": limit, "offset": offset}
                resp = requests.get(
                    CHANNELS_URL, headers=self.headers, params=params, timeout=15
                )
                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, list) or not data:
                    break

                for ch in data:
                    org = ch.get("org")
                    if org:
                        orgs.add(org)

                if len(data) < limit:
                    break
                offset += limit

            self.orgs = sorted(orgs)
            self._save_cached_orgs(self.orgs)
            self.org_error = None
        except requests.RequestException as e:
            self.org_error = str(e)
            config = load_config()
            fallback = config.get("orgs", [])
            self.orgs = fallback if fallback else FALLBACK_ORGS.copy()
        finally:
            self.fetching_orgs = False
            flush_input()

    def fetch(self):
        try:
            params = {
                "org": self.org,
                "status": "live,upcoming",
                "max_upcoming_hours": self.upcoming_hours,
                "include": "live_info",
            }
            resp = requests.get(API_URL, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            data.sort(key=lambda s: (
                0 if s.get("status") == "live" else 1,
                s.get("start_scheduled") or "",
            ))
            self.streams = data
            self.last_fetch = time.time()
            self.error_msg = None
            self.selected = 0
        except requests.RequestException as e:
            self.error_msg = str(e)

    def _fmt_viewers(self, n):
        if n is None:
            return ""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _fmt_time(self, iso_str):
        if not iso_str:
            return "?"
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = dt - now
            total = int(diff.total_seconds())
            if total <= 0:
                return "soon"
            h, m = total // 3600, (total % 3600) // 60
            clock = dt.astimezone().strftime("%H:%M")
            return f"in {h}h {m}m ({clock})" if h > 0 else f"in {m}m ({clock})"
        except Exception:
            return iso_str[11:16]

    def _draw_custom_org(self):
        console.clear()
        cursor = "█" if int(time.time() * 2) % 2 == 0 else " "
        content = (
            "[bold yellow]Custom Organization[/bold yellow]\n\n"
            "Type any org name and press Enter.\n"
            "Examples: [dim]774inc, Kawaii, Tsunderia[/dim]\n\n"
            f"[reverse] {self.custom_buf}{cursor} [/reverse]\n\n"
            "[dim]Enter = confirm | Esc = cancel | Backspace = delete[/dim]"
        )
        console.print(Align.center(Panel(content, border_style="yellow", width=70)))

    def _draw_org_picker(self):
        console.clear()

        if self.fetching_orgs:
            console.print(Align.center(Panel(
                "[bold yellow]Fetching organizations from Holodex...[/bold yellow]\n\n"
                "[dim]Scanning channels, please wait...[/dim]",
                border_style="yellow",
            )))
            return

        status = ""
        if self.org_error:
            status = "[red](API failed)"
        elif not self._load_cached_orgs():
            status = "[dim](fresh)"
        else:
            status = "[dim](cached)"

        filtered = self._filtered_orgs()
        total_items = len(filtered) + 1  # +1 for Custom

        search_info = f" | filter: '{self.search_query}'" if self.search_query else ""
        page_info = f" | {self.org_selected + 1}/{total_items} matched"
        console.print(Align.center(Panel(
            f"[bold]Select Organization[/bold] {status}{page_info}{search_info} | / search | f fav | R refresh | g/G jump | q cancel",
            border_style="yellow",
        )))
        console.print()

        if self.org_error and not self.orgs:
            console.print(f"[red]Error: {self.org_error}[/red]")
            console.print("[dim]Press q to go back[/dim]")
            return

        if not filtered and self.search_query:
            console.print(f"[yellow]No organizations match '{self.search_query}'.[/yellow]")
            console.print("[dim]Press Esc to clear filter[/dim]")
            return

        if not self.orgs:
            console.print("[yellow]No organizations found.[/yellow]")
            return

        # Viewport paging
        if self.org_selected < self.org_scroll_top:
            self.org_scroll_top = self.org_selected
        elif self.org_selected >= self.org_scroll_top + PAGE_SIZE:
            self.org_scroll_top = self.org_selected - PAGE_SIZE + 1

        visible_start = self.org_scroll_top
        visible_end = min(visible_start + PAGE_SIZE, total_items)

        table = Table(
            show_header=True, header_style="bold magenta",
            box=box.ROUNDED, expand=True, row_styles=["", "dim"], pad_edge=False,
        )
        table.add_column("#", style="cyan", width=3, justify="right")
        table.add_column("Organization", style="green")
        table.add_column("Current", style="bright_cyan", width=10, justify="center")

        for i in range(visible_start, visible_end):
            if i < len(filtered):
                org = filtered[i]
                name = f"★ {org}" if org in self.fav_orgs else f"  {org}"
                current = "[bold reverse] ✓ [/]" if org == self.org else ""
                if i == self.org_selected:
                    table.add_row(
                        f"> {i + 1}",
                        f"[bold reverse]{name}[/]",
                        f"[bold reverse]{current}[/]",
                    )
                else:
                    table.add_row(str(i + 1), name, current)
            else:
                # Custom row
                is_custom = self.org not in self.orgs
                current = "[bold reverse] ✓ [/]" if is_custom else ""
                if i == self.org_selected:
                    table.add_row(
                        f"> {i + 1}",
                        f"[bold reverse]  Custom...[/]",
                        f"[bold reverse]{current}[/]",
                    )
                else:
                    table.add_row(str(i + 1), "  Custom...", current)

        console.print(table)

        if visible_start > 0:
            console.print("[dim]  ▲ more above[/dim]")
        if visible_end < total_items:
            console.print("[dim]  ▼ more below[/dim]")

        if self.search_mode:
            cursor = "█" if int(time.time() * 2) % 2 == 0 else " "
            console.print(f"\n[dim]Search: {self.search_query}{cursor}[/dim]")

    def _draw_main(self):
        console.clear()
        live = sum(1 for s in self.streams if s.get("status") == "live")
        total = len(self.streams)
        age = int(time.time() - self.last_fetch)
        age_str = f"{age}s ago" if age < 60 else f"{age // 60}m ago"

        header = (
            f"[bold]Holodex TUI[/] | [cyan]{self.org}[/] | "
            f"[green]{live} live[/] [dim]{total - live} up[/] | "
            f"updated {age_str} | o org | ? help | q quit"
        )
        console.print(Align.center(Panel(header, border_style="blue")))
        console.print()

        if self.error_msg:
            console.print(f"[red]Error: {self.error_msg}[/red]")
            console.print("[dim]Press r to retry[/dim]")
        elif not self.streams:
            console.print("[yellow]No streams found.[/yellow]")
        else:
            # Calculate viewport boundaries
            visible_start = self.scroll_top
            visible_end = min(self.scroll_top + PAGE_SIZE, len(self.streams))

            # Show scroll hints
            if self.scroll_top > 0:
                console.print("[dim]▲ more above[/dim]")
            if visible_end < len(self.streams):
                console.print("[dim]▼ more below[/dim]")

            table = Table(
                show_header=True, header_style="bold magenta",
                box=box.ROUNDED, expand=True, row_styles=["", "dim"], pad_edge=False,
            )
            table.add_column("#", style="cyan", width=3, justify="right")
            table.add_column("", width=2, justify="center")
            table.add_column("Streamer", style="green", width=20, no_wrap=True)
            table.add_column("Title", style="white", ratio=2, no_wrap=True)
            table.add_column("Topic", style="yellow", width=12, no_wrap=True)
            table.add_column("Info", style="bright_cyan", width=18, justify="right")

            for i, s in enumerate(self.streams[visible_start:visible_end]):
                ch = s.get("channel", {})
                name = ch.get("english_name") or ch.get("name") or "Unknown"
                title = s.get("title", "Untitled")[:52]
                topic = s.get("topic_id", "-")
                is_live = s.get("status") == "live"
                indicator = "[red]●[/]" if is_live else "[dim]○[/]"
                info = self._fmt_viewers(s.get("live_viewers")) if is_live else self._fmt_time(s.get("start_scheduled"))

                actual_index = visible_start + i
                if actual_index == self.selected:
                    table.add_row(
                        f"> {actual_index + 1}",
                        f"[bold reverse]{indicator}[/]",
                        f"[bold reverse]{name}[/]",
                        f"[bold reverse]{title}[/]",
                        f"[bold reverse]{topic}[/]",
                        f"[bold reverse]{info}[/]",
                    )
                else:
                    table.add_row(str(actual_index + 1), indicator, name, title, topic, info)

            console.print(table)

        if self.show_help:
            console.print()
            console.print(Align.center(Panel(
                "[dim]↑/↓ j/k navigate | g/G top/bottom | Enter open | o change org | r refresh | q quit[/dim]",
                border_style="dim",
            )))

        if not self.mpv_ok:
            console.print("\n[red]Warning: mpv not found[/red]")

    def _draw(self):
        if self.mode == "org_picker":
            self._draw_org_picker()
        elif self.mode == "custom_org":
            self._draw_custom_org()
        else:
            self._draw_main()

    def _open(self):
        if not self.streams:
            return
        vid = self.streams[self.selected]["id"]
        url = f"https://www.youtube.com/watch?v={vid}"
        console.print(f"\n[green]▶ mpv {url}[/green]")
        if not self.mpv_ok:
            console.print("[red]mpv not installed[/red]")
            return

        cmd = [
            "mpv",
            "--force-seekable=no",
            url,
        ]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _pick_org(self, idx):
        filtered = self._filtered_orgs()
        if idx < len(filtered):
            self.org = filtered[idx]
            self.fetch()
            self.mode = "main"
            self.search_query = ""
            self.search_mode = False
        else:
            self.mode = "custom_org"
            self.custom_buf = ""

    def _toggle_fav(self):
        filtered = self._filtered_orgs()
        if self.org_selected < len(filtered):
            org = filtered[self.org_selected]
            if org in self.fav_orgs:
                self.fav_orgs.discard(org)
            else:
                self.fav_orgs.add(org)
            self._save_favs()
            # Re-clamp selection since sort order may change
            self.org_selected = 0
            self.org_scroll_top = 0
            self.needs_redraw = True

    def _submit_custom(self):
        text = self.custom_buf.strip()
        if text:
            self.org = text
            self.fetch()
        self.mode = "main"
        self.custom_buf = ""

    def _cancel_custom(self):
        self.mode = "main"
        self.custom_buf = ""

    def _handle_custom_key(self, key):
        if key == "\x7f" or key == "\x08":
            self.custom_buf = self.custom_buf[:-1]
            self.needs_redraw = True
            return True
        elif key in ("\r", "\n"):
            self._submit_custom()
            self.needs_redraw = True
            return True
        elif key == "\x1b":
            self._cancel_custom()
            self.needs_redraw = True
            return True
        elif key in ("\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"):
            return True
        elif len(key) == 1 and 32 <= ord(key) <= 126:
            self.custom_buf += key
            self.needs_redraw = True
            return True
        return False

    def _handle_search_key(self, key):
        """Handle key input while in search mode."""
        if key == "\x7f" or key == "\x08":
            self.search_query = self.search_query[:-1]
            self.org_selected = 0
            self.org_scroll_top = 0
            self.needs_redraw = True
            return True
        elif key in ("\r", "\n"):
            self.search_mode = False
            self.needs_redraw = True
            return True
        elif key == "\x1b":
            self.search_query = ""
            self.search_mode = False
            self.org_selected = 0
            self.org_scroll_top = 0
            self.needs_redraw = True
            return True
        elif key in ("\x1b[A", "\x1b[B"):
            # Arrow keys exit search mode and navigate
            self.search_mode = False
            return False  # let normal handler process the arrow
        elif len(key) == 1 and 32 <= ord(key) <= 126:
            self.search_query += key
            self.org_selected = 0
            self.org_scroll_top = 0
            self.needs_redraw = True
            return True
        return False

    def _getch(self, timeout=None):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        try:
            if timeout is not None:
                ready, _, _ = select.select([sys.stdin], [], [], timeout)
                if not ready:
                    return None
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                ch += seq
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def run(self):
        self.fetch()
        next_refresh = time.time() + REFRESH_INTERVAL

        try:
            while True:
                if self.needs_redraw:
                    self._draw()
                    self.needs_redraw = False

                wait = max(0, next_refresh - time.time())
                key = self._getch(timeout=wait)

                if key is None:
                    if self.mode == "main":
                        self.fetch()
                        self.needs_redraw = True
                    next_refresh = time.time() + REFRESH_INTERVAL
                    continue

                # ── Custom Org Input Mode ──
                if self.mode == "custom_org":
                    if self._handle_custom_key(key):
                        continue
                    continue

                # ── Org Picker Mode ──
                if self.mode == "org_picker":
                    if self.fetching_orgs:
                        continue

                    # Search mode takes priority for printable keys
                    if self.search_mode:
                        handled = self._handle_search_key(key)
                        if handled:
                            continue
                        # If search handler didn't consume it (e.g. arrow keys), fall through

                    filtered = self._filtered_orgs()
                    total_items = len(filtered) + 1

                    if key in ("\x1b[A", "k"):
                        self.org_selected = max(0, min(self.org_selected - 1, total_items - 1))
                        self.needs_redraw = True
                    elif key in ("\x1b[B", "j"):
                        self.org_selected = max(0, min(self.org_selected + 1, total_items - 1))
                        self.needs_redraw = True
                    elif key in ("\r", "\n"):
                        if total_items > 0:
                            self._pick_org(self.org_selected)
                            self.needs_redraw = True
                        next_refresh = time.time() + REFRESH_INTERVAL
                    elif key == "/":
                        self.search_mode = True
                        self.needs_redraw = True
                    elif key == "f":
                        self._toggle_fav()
                    elif key == "R":
                        self.fetch_orgs(force=True)
                        self.org_selected = 0
                        self.org_scroll_top = 0
                        self.search_query = ""
                        self.search_mode = False
                        self.needs_redraw = True
                    elif key == "q" or key == "\x1b":
                        if self.search_query:
                            self.search_query = ""
                            self.search_mode = False
                            self.org_selected = 0
                            self.org_scroll_top = 0
                            self.needs_redraw = True
                        else:
                            self.mode = "main"
                            self.needs_redraw = True
                    elif key == "g":
                        self.org_selected = 0
                        self.org_scroll_top = 0
                        self.needs_redraw = True
                    elif key == "G":
                        filtered = self._filtered_orgs()
                        if filtered:
                            self.org_selected = len(filtered) - 1
                            self.org_scroll_top = max(0, len(filtered) - PAGE_SIZE)
                            self.needs_redraw = True
                    continue

                # ── Main Mode ──
                if key in ("\x1b[A", "k"):
                    self.selected = max(0, self.selected - 1)
                    # Auto-scroll to keep selected item visible
                    if self.selected < self.scroll_top:
                        self.scroll_top = self.selected
                    self.needs_redraw = True
                elif key in ("\x1b[B", "j"):
                    self.selected = min(len(self.streams) - 1, self.selected + 1)
                    # Auto-scroll to keep selected item visible
                    if self.selected >= self.scroll_top + PAGE_SIZE:
                        self.scroll_top = self.selected - PAGE_SIZE + 1
                    self.needs_redraw = True
                elif key in ("\r", "\n"):
                    self._open()
                elif key == "o":
                    self.fetch_orgs()
                    self.mode = "org_picker"
                    self.org_scroll_top = 0
                    self.search_query = ""
                    self.search_mode = False
                    # Try to pre-select current org
                    filtered = self._filtered_orgs()
                    try:
                        self.org_selected = filtered.index(self.org)
                    except ValueError:
                        self.org_selected = len(filtered)
                    self.needs_redraw = True
                elif key == "r":
                    self.fetch()
                    self.needs_redraw = True
                    next_refresh = time.time() + REFRESH_INTERVAL
                elif key == "?":
                    self.show_help = not self.show_help
                    self.needs_redraw = True
                elif key == "g":
                    self.selected = 0
                    self.scroll_top = 0
                    self.needs_redraw = True
                elif key == "G":
                    self.selected = len(self.streams) - 1
                    self.scroll_top = max(0, len(self.streams) - PAGE_SIZE)
                    self.needs_redraw = True
                elif key == "q":
                    break
        finally:
            console.clear()


def main():
    parser = argparse.ArgumentParser(description="Holodex TUI")
    parser.add_argument("--org", default="Hololive", help="Organization (default: Hololive)")
    parser.add_argument("--hours", type=int, default=24, help="Upcoming window in hours")
    args = parser.parse_args()

    app = HolodexTUI(org=args.org, upcoming_hours=args.hours)
    app.run()


if __name__ == "__main__":
    main()
