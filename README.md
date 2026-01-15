# RipForge

A modern, self-hosted disc ripping solution with smart identification and media server integration.

![RipForge Dashboard](docs/screenshot.png)

## Features

- **Smart Disc Identification** - Uses disc label parsing + runtime matching against TMDB (not unreliable CRC lookups)
- **Media Server Integration** - Connects with Radarr, Sonarr, Overseerr, and Plex
- **Overseerr Matching** - Automatically matches ripped discs against your requested movies
- **Clean Web UI** - Modern dashboard for monitoring rips, viewing history, and configuring settings
- **Auto-Detection** - Scans for existing Docker containers and imports API keys
- **Email Notifications** - Get notified when rips complete or fail
- **Main Feature Mode** - Skip extras and bonus content, rip only the main movie

## Requirements

- Linux (tested on Ubuntu 22.04+)
- Python 3.10+
- MakeMKV (for disc ripping)
- Optical drive (Blu-ray or DVD)
- Docker (optional, for running alongside existing media stack)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/paul-tastic/ripforge.git
cd ripforge

# Install dependencies
pip install -r requirements.txt

# Run the application
python run.py
```

Open http://localhost:8081 in your browser.

## Configuration

On first run, click **Auto-Detect** to scan for existing services (Radarr, Sonarr, Plex, etc.) and import API keys from existing scripts.

Configuration is stored in `config/settings.yaml`.

### Integrations

| Service | Purpose |
|---------|---------|
| Radarr | Movie library management, wanted list matching |
| Sonarr | TV show library management |
| Overseerr | Request matching - match rips against user requests |
| Plex | Media server, library scanning |
| Tautulli | Plex monitoring (optional) |

### Ripping Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `min_length` | 2700 | Minimum track length in seconds (45 min) |
| `main_feature_only` | true | Only rip the longest track |
| `skip_transcode` | true | Keep original MakeMKV quality |

## How It Works

1. **Disc Detection** - udev rules detect when a disc is inserted
2. **Identification** - Parse disc label, get runtime via ffprobe, match against TMDB
3. **Overseerr Check** - See if the disc matches any pending requests
4. **Rip** - MakeMKV extracts the main feature
5. **Post-Processing** - Add to Radarr/Sonarr, move to library, trigger Plex scan
6. **Notification** - Email summary of the rip

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Current system status |
| `/api/settings` | GET/POST | Configuration management |
| `/api/auto-detect` | POST | Scan for services |
| `/api/test-connection` | POST | Test service connection |
| `/api/activity-log` | GET | Recent activity log |

## Project Structure

```
ripforge/
├── app/
│   ├── __init__.py
│   ├── config.py      # Configuration management
│   ├── routes.py      # Web routes and API
│   ├── ripper.py      # Disc ripping engine (TODO)
│   └── identify.py    # Smart identification (TODO)
├── config/
│   └── default.yaml   # Default configuration
├── static/
│   ├── css/
│   └── js/
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── settings.html
│   └── history.html
├── requirements.txt
└── run.py
```

## Comparison to ARM

RipForge was created as a cleaner alternative to [Automatic Ripping Machine (ARM)](https://github.com/automatic-ripping-machine/automatic-ripping-machine).

| Feature | ARM | RipForge |
|---------|-----|----------|
| Disc identification | CRC64 lookup (often wrong) | Label parsing + runtime matching |
| Overseerr integration | No | Yes |
| Radarr wanted list | No | Yes |
| Web UI | Functional but dated | Modern, clean |
| Configuration | Scattered config files | Single YAML file |
| Dependencies | Heavy (many services) | Lightweight |

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Credits

Built with:
- [Flask](https://flask.palletsprojects.com/)
- [MakeMKV](https://www.makemkv.com/)
- [PyYAML](https://pyyaml.org/)
