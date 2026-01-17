# RipForge

A modern, self-hosted disc ripping solution with smart identification and media server integration.

## Features

- **Smart Disc Identification** - Parses disc labels + matches runtime against Radarr/TMDB
- **TV Show Auto-Detection** - Detects episode-length tracks and rips all episodes automatically
- **Hands-Free Mode** - Insert disc, walk away - auto-detects movies vs TV, rips everything
- **Editable Title** - Scan disc, verify/edit title, then rip with confidence
- **Auto-Scan on Insert** - Detects disc insertion and automatically scans
- **Auto-Rip Countdown** - 20-second countdown after scan, auto-starts rip (cancellable)
- **Uncertain ID Handling** - Email notification only if you don't correct the title
- **Resilient Ripping** - Job state persists to disk, survives service restarts mid-rip
- **Live Progress** - File size display during rip (e.g., "2.1 / 5.0 GB"), progress bar
- **Smart Recovery** - Detects incomplete rips (<90% complete) and won't auto-process them
- **Media Server Integration** - Radarr, Sonarr, Overseerr, Plex
- **Real-time Progress** - Checklist UI shows each step with spinner animations
- **Hardware Dashboard** - CPU, RAM, storage (SSD/HDD/Pool detection), optical drive
- **Email Notifications** - Rip complete, errors, and weekly recap with movie posters and disc type badges
- **SendGrid Support** - Optional SendGrid integration for better Gmail deliverability
- **Activity Logging** - Detailed activity log with identification method tracking
- **Rip Statistics** - Average rip times by disc type, weekly/daily counts
- **IMDB Search** - Quick link to search IMDB when identification is uncertain
- **Auto-Detection** - Scans for Docker containers and imports API keys
- **Systemd Service** - Runs on boot, survives reboots
- **Auto-Reset on Eject** - UI resets to ready state when disc is ejected

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

### Standard Mode (hands_free: false)
1. **Insert disc** - Auto-detected and scan begins automatically
2. **Identification** - Smart ID parses label + matches runtime against TMDB
3. **Review** - Title shown with confidence badge (HIGH/MEDIUM/LOW) and expected file size
   - **HIGH confidence**: 20-second countdown starts automatically
   - **LOW confidence**: Waits for manual review (email only sent if you don't correct it)
4. **Edit if needed** - Fix title or use IMDB button to search
5. **Rip** - Button shows exactly what will be ripped: `Rip "Movie Title (Year)"`
6. **Progress** - Live file size (e.g., "2.1 / 5.0 GB"), checklist shows each step
7. **Resilient** - Service restart mid-rip? No problem - recovers and continues
8. **Eject** - UI auto-resets to ready state, waiting for next disc

### Hands-Free Mode (hands_free: true)
1. **Insert disc** - Auto-detected, quick scan determines movie vs TV
2. **Auto-detect** - Movies rip main feature; TV shows rip all episode tracks
3. **Rip** - Progress shown with file size, checklist updates in real-time
4. **Identification** - After rip completes, uses ffprobe to get actual file runtime
5. **Smart Match** - Searches Radarr/Sonarr/TMDB with actual runtime for better accuracy
6. **Move** - Files moved to final location with identified title
7. **Eject** - UI auto-resets, ready for next disc

Hands-free mode is ideal for batch ripping - just swap discs without touching the keyboard. Works with both movies and TV shows.

## Configuration

Settings stored in `config/settings.yaml`. Edit via web UI or directly.

### Ripping Settings

```yaml
ripping:
  min_length: 2700              # 45 min - skip short tracks
  main_feature_only: true       # Only rip longest track (movies)
  skip_transcode: true          # Keep original quality
  auto_scan_on_insert: true     # Auto-scan when disc inserted
  auto_rip: true                # Auto-start after countdown
  auto_rip_delay: 20            # Countdown seconds
  hands_free: false             # Skip scan/preview, rip immediately, identify after
  confidence_threshold: 75      # Below this = needs manual review
  notify_uncertain: true        # Email when ID confidence is low
  tv_min_episode_length: 1200   # 20 min - minimum track length for TV detection
```

### Email Notifications

```yaml
notifications:
  email:
    provider: sendgrid    # or "msmtp" for system mail
    sendgrid_api_key: "SG.xxxxx"  # Get free key at sendgrid.com
    recipients:
      - "you@example.com"
    on_complete: true     # Email when rip finishes
    on_error: true        # Email on failures
    weekly_recap: true    # Weekly summary email
```

**Email Providers:**
- **SendGrid (recommended)** - Better Gmail/spam deliverability, 100 free emails/day
- **msmtp** - System mail, requires server configuration

Configure from the Notifications page. Weekly recap includes movie posters from TMDB, disc type badges (Blu-ray blue / DVD orange), and rip statistics.

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
| `/api/rip-stats` | GET | Rip statistics (avg time by disc type, counts) |
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
│   ├── email.py       # Email notifications (SendGrid + msmtp)
│   └── activity.py    # Activity logging and rip history
├── config/
│   ├── settings.yaml    # Configuration
│   └── current_job.json # Persisted job state (auto-managed)
├── logs/
│   ├── activity.log     # Activity log
│   └── rip_history.json # Rip history for weekly digest
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
- Aspect ratios (4X3, 16X9, WS, FS)
- Format codes (NTSC, PAL)

Examples:
- `MARVEL_STUDIOS_GUARDIANS_3` → "Guardians of the Galaxy Vol 3"
- `NACHO_LIBRE_PS` → "Nacho Libre"
- `SCHOOL_OF_ROCK_4X3` → "School of Rock"

## Smart Title Matching

Identification uses weighted scoring to find the best match:
- **Exact title match** - Strong signal, prevents sequel confusion
- **Runtime matching** - Compares disc runtime to TMDB data
- **Year proximity** - Prefers closer release years
- **TMDB ID lookup** - Once identified, poster/metadata fetched by ID (not title search)

This prevents issues like "The Transporter" matching "The Transporter Refueled" or "Spider-Man" matching a newer reboot. The TMDB ID lookup ensures posters are always correct even when disc labels are truncated.

## Comparison to ARM

| Feature | ARM | RipForge |
|---------|-----|----------|
| Identification | CRC64 lookup (unreliable) | Label parsing + runtime matching |
| TV Show Support | Limited | Auto-detects episodes, rips all tracks |
| Pre-rip verification | No | Yes - scan, edit, confirm |
| Hands-free mode | No | Yes - rip first, identify after with actual runtime |
| Auto-scan on insert | No | Yes - configurable |
| Auto-rip | Immediate | 20s countdown (configurable) |
| Low confidence handling | None | Email alert + manual review |
| Web UI | Dated | Modern dark theme |
| Email | Basic | HTML with posters, SendGrid, weekly recap |

## License

MIT License
