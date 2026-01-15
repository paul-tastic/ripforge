# RipForge

A modern, self-hosted disc ripping solution with smart identification and media server integration.

## Features

- **Smart Disc Identification** - Parses disc labels + matches runtime against Radarr/TMDB
- **Editable Title** - Scan disc, verify/edit title, then rip with confidence
- **Auto-Rip Countdown** - 10-second countdown after scan, auto-starts rip (cancellable)
- **Media Server Integration** - Radarr, Sonarr, Overseerr, Plex
- **Real-time Progress** - Checklist UI shows each step with spinner animations
- **Hardware Dashboard** - CPU, RAM, storage (SSD/HDD/Pool detection), optical drive
- **Email Notifications** - Rip complete, errors, and weekly recap emails
- **IMDB Search** - Quick link to search IMDB when identification is uncertain
- **Auto-Detection** - Scans for Docker containers and imports API keys
- **Systemd Service** - Runs on boot, survives reboots

## Dashboard
<img width="1663" height="1114" alt="image" src="https://github.com/user-attachments/assets/8258c12f-05ef-48e4-8f82-b0f576f859e8" />


## Requirements

- Linux (tested on Ubuntu 24.04)
- Python 3.10+
- MakeMKV
- Optical drive (Blu-ray or DVD)
- msmtp (for email notifications)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/paul-tastic/ripforge.git
cd ripforge

# Run setup script
./scripts/setup.sh

# Or manual setup:
sudo add-apt-repository ppa:heyarje/makemkv-beta
sudo apt update
sudo apt install makemkv-bin makemkv-oss
sudo usermod -aG cdrom $USER

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python run.py
```

Open http://localhost:8081

## Install as Service

```bash
sudo cp ripforge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ripforge
```

## Workflow

1. **Insert disc** - Drive detects disc
2. **Click "Scan Disc"** - Spinner shows while MakeMKV reads disc
3. **Review identification** - Title shown with confidence badge (HIGH/MEDIUM/LOW)
4. **Edit if needed** - Fix title or use IMDB button to search
5. **Auto-rip countdown** - 10 seconds to cancel or edit (or click Start Rip)
6. **Rip progress** - Checklist shows: detect → scan → rip → identify → library → move → Plex scan
7. **Notification** - Email sent when complete

## Configuration

Settings stored in `config/settings.yaml`. Edit via web UI or directly.

### Ripping Settings

```yaml
ripping:
  min_length: 2700        # 45 min - skip short tracks
  main_feature_only: true # Only rip longest track
  skip_transcode: true    # Keep original quality
  auto_rip: true          # Auto-start after scan
  auto_rip_delay: 10      # Countdown seconds
```

### Email Notifications

```yaml
notifications:
  email:
    recipients:
      - "you@example.com"
    on_complete: true     # Email when rip finishes
    on_error: true        # Email on failures
    weekly_recap: true    # Weekly summary email
```

Uses system msmtp for sending. Test from Settings page.

### Integrations

| Service | Purpose |
|---------|---------|
| Radarr | Movie identification via TMDB, library management |
| Sonarr | TV show library management |
| Overseerr | Request matching |
| Plex | Media server, library scanning |
| Tautulli | Plex monitoring |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/disc/scan-identify` | GET | Scan disc and identify |
| `/api/rip/start` | POST | Start rip (with optional custom_title) |
| `/api/rip/status` | GET | Current rip progress |
| `/api/rip/reset` | POST | Cancel current job |
| `/api/hardware` | GET | System hardware info |
| `/api/email/test` | POST | Send test email |
| `/api/email/weekly-recap` | POST | Send weekly recap now |
| `/api/settings` | GET/POST | Configuration |
| `/api/auto-detect` | POST | Scan for services |

## Project Structure

```
ripforge/
├── app/
│   ├── config.py      # Configuration, hardware detection
│   ├── routes.py      # Web routes and API
│   ├── ripper.py      # MakeMKV wrapper, rip pipeline
│   ├── identify.py    # Smart identification
│   └── email.py       # Email notifications
├── config/
│   └── settings.yaml
├── static/css/
│   └── style.css
├── templates/
│   ├── index.html     # Dashboard
│   ├── settings.html  # Configuration
│   └── history.html   # Rip history
├── scripts/
│   ├── setup.sh
│   └── setup-udev.sh
├── ripforge.service
└── run.py
```

## Storage Support

Detects and displays:
- **SSD** - Solid state drives
- **HDD** - Hard disk drives
- **Pool** - MergerFS/union filesystem pools

## Disc Label Parsing

Automatically strips:
- Studio prefixes (MARVEL_STUDIOS_, DISNEY_, WARNER_, etc.)
- Region codes (PS, US, UK, EU, etc.)
- Disc numbers (DISC1, D2, etc.)

Examples:
- `MARVEL_STUDIOS_GUARDIANS_3` → "Guardians of the Galaxy Vol 3"
- `NACHO_LIBRE_PS` → "Nacho Libre"

## Comparison to ARM

| Feature | ARM | RipForge |
|---------|-----|----------|
| Identification | CRC64 lookup (unreliable) | Label parsing + runtime matching |
| Pre-rip verification | No | Yes - scan, edit, confirm |
| Auto-rip | Immediate | Countdown with cancel option |
| Web UI | Dated | Modern dark theme |
| Email | Basic | HTML emails with weekly recap |

## License

MIT License
