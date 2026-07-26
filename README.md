# holodex-tui

A minimal terminal UI for browsing live and upcoming VTuber streams via [Holodex](https://holodex.net). Open streams directly in [mpv](https://mpv.io) with [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Features

- **Minimal setup** prompts for API key on first run, saves it securely
- **Live + upcoming** streams in one list
- **Auto-refresh** every 60 seconds
- **Organization picker** with search, favorites, and live API fetching
- **Navigate** with arrow keys or vim bindings (`j`/`k`)
- **Open** any stream in mpv with `Enter`
- **Filter** by any organization Holodex tracks

## Requirements

- Python 3.10+
- [mpv](https://mpv.io) (with [yt-dlp](https://github.com/yt-dlp/yt-dlp) installed)
- A [Holodex](https://holodex.net) account (For API)

## Installation

### From source

```bash
git clone https://github.com/Ekytfy/holodex-tui.git
cd holodex-tui
pip install -e .
```

Then run:
```bash
holodex-tui
```

### Manual (no install)

```bash
git clone https://github.com/YOURNAME/holodex-tui.git
cd holodex-tui
pip install -r requirements.txt
python -m holodex_tui
```

## First run

On first launch, the app will ask for your Holodex API key:

```
┌─────────────────────────────────────────────────────────────┐
│ Holodex API Key Required                                    │
│                                                             │
│ 1. Go to https://holodex.net and log in                     │
│ 2. Open Account Settings                                    │
│ 3. Copy your API Key                                        │
│                                                             │
│ Paste it below and press Enter:                             │
└─────────────────────────────────────────────────────────────┘

API Key: ████████████████████
✓ Key saved!
```

Your key is saved to `~/.config/holodex-tui/config.json` with `600` permissions (owner-only read/write).

You can also set it via environment variable:

```bash
export HOLODEX_API_KEY="your-key-here"
```

## Usage

```bash
# Default: Hololive, 24h upcoming window
holodex-tui

# Watch a specific org
holodex-tui --org Nijisanji

# Show upcoming streams for the next 48 hours
holodex-tui --hours 48
```

## Planned Features

- [ ] Watch clips from different orgs


### Keys (main view)

| Key | Action |
|-----|--------|
| `↑`/`↓` or `j`/`k` | Navigate streams |
| `Enter` | Open in mpv |
| `o` | Change organization |
| `r` | Refresh now |
| `?` | Toggle help |
| `q` | Quit |

### Keys (org picker)

| Key | Action |
|-----|--------|
| `↑`/`↓` or `j`/`k` | Navigate orgs |
| `Enter` | Pick org |
| `/` | Search/filter |
| `f` | Toggle favorite |
| `R` | Refresh org list from API |
| `Esc` | Clear search / cancel |
| `q` | Cancel |

## License

MIT
