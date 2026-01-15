# RipForge

A modern, self-hosted disc ripping solution with smart identification and media server integration.

## Features

- **Smart Disc Identification** - Parses disc labels + matches runtime against Radarr/TMDB
- **Editable Title** - Scan disc first, verify/edit the title, then rip with confidence
- **Media Server Integration** - Connects with Radarr, Sonarr, Overseerr, and Plex
- **Real-time Progress** - Checklist UI shows each step as it completes
- **Hardware Dashboard** - Shows CPU, RAM, storage (with SSD/HDD/Pool detection), optical drive
- **Auto-Detection** - Scans for Docker containers and imports API keys automatically
- **Systemd Service** - Runs on boot, survives reboots

## Requirements

- Linux (tested on Ubuntu 24.04)
- Python 3.10+
- MakeMKV (for disc ripping)
- Optical drive (Blu-ray or DVD)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/paul-tastic/ripforge.git
cd ripforge

# Run setup script
./scripts/setup.sh

# Or manual setup:
# Install MakeMKV (Ubuntu/Debian)
sudo add-apt-repository ppa:heyarje/makemkv-beta
sudo apt update
sudo apt install makemkv-bin makemkv-oss

# Add user to cdrom group
sudo usermod -aG cdrom $USER

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
python run.py
```

Open http://localhost:8081 in your browser.

## Install as Service

```bash
# Copy service file
sudo cp ripforge.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ripforge
sudo systemctl start ripforge

# Check status
sudo systemctl status ripforge
```

## Configuration

On first run, click **Auto-Detect** to scan for existing services and import API keys.

Configuration is stored in `config/settings.yaml`.

### Integrations

| Service | Purpose |
|---------|---------|
| Radarr | Movie identification via TMDB, library management |
| Sonarr | TV show library management |
| Overseerr | Request matching |
| Plex | Media server, library scanning |
| Tautulli | Plex monitoring (optional) |

## How It Works

1. **Scan Disc** - Click "Scan Disc" to read disc info and identify content
2. **Verify Title** - Edit the suggested title if needed (e.g., "Nacho Libre (2006)")
3. **Start Rip** - MakeMKV extracts the main feature using your title
4. **Auto-Processing** - Adds to Radarr, moves to library, triggers Plex scan
5. **Done** - Movie appears in Plex

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | System status and integrations |
| `/api/disc/scan-identify` | GET | Scan disc and identify content |
| `/api/rip/start` | POST | Start ripping (accepts custom_title) |
| `/api/rip/status` | GET | Current rip progress |
| `/api/rip/reset` | POST | Cancel/reset current job |
| `/api/hardware` | GET | System hardware info |
| `/api/settings` | GET/POST | Configuration |
| `/api/auto-detect` | POST | Scan for services |

## Project Structure

```
ripforge/
├── app/
│   ├── __init__.py
│   ├── config.py      # Configuration and hardware detection
│   ├── routes.py      # Web routes and API
│   ├── ripper.py      # MakeMKV wrapper and rip engine
│   └── identify.py    # Smart identification (label parsing + TMDB)
├── config/
│   └── settings.yaml  # User configuration
├── static/css/
│   └── style.css      # Dark theme UI
├── templates/
│   ├── base.html
│   ├── index.html     # Dashboard with rip checklist
│   ├── settings.html  # Integration configuration
│   └── history.html   # Rip history
├── scripts/
│   └── setup.sh       # Installation script
├── ripforge.service   # Systemd service file
├── requirements.txt
└── run.py
```

## Storage Support

RipForge detects and displays:
- **SSD** - Solid state drives
- **HDD** - Hard disk drives
- **Pool** - MergerFS/union filesystem pools

## Comparison to ARM

| Feature | ARM | RipForge |
|---------|-----|----------|
| Disc identification | CRC64 lookup (often wrong) | Label parsing + runtime matching |
| Pre-rip verification | No | Yes - scan and edit title first |
| Web UI | Dated | Modern dark theme |
| Configuration | Multiple files | Single YAML |
| Service management | Complex | Simple systemd |

## License

MIT License
