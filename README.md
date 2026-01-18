# RipForge

**[ripforge.org](https://ripforge.org)** · A modern, self-hosted disc ripping solution with smart identification and media server integration.

## Features

- **Smart Disc Identification** - Parses disc labels + matches runtime against Radarr/TMDB
- **Filename Sanitization** - Handles colons and special characters in titles (Star Wars: → Star Wars -)
- **TV Show Auto-Detection** - Detects episode-length tracks and rips all episodes automatically
- **Hands-Free Mode** - Insert disc, walk away - auto-detects movies vs TV, rips everything
- **Review Queue** - Failed identifications go to a review folder for manual matching with IMDB/TMDB verification links
- **Editable Title** - Scan disc, verify/edit title, then rip with confidence
- **Auto-Scan on Insert** - Detects disc insertion and automatically scans
- **Auto-Rip Countdown** - 20-second countdown after scan, auto-starts rip (cancellable)
- **Uncertain ID Handling** - Email notification only if you don't correct the title
- **Resilient Ripping** - Job state persists to disk, survives service restarts mid-rip
- **Live Progress** - File size display during rip (e.g., "2.1 / 5.0 GB"), progress bar
- **Smart Recovery** - Detects incomplete rips (<90% complete) and won't auto-process them
- **Silent Failure Detection** - Catches MakeMKV "success" with no actual progress (disc read issues)
- **Media Server Integration** - Radarr, Sonarr, Overseerr, Plex
- **Real-time Progress** - Checklist UI shows each step with spinner animations
- **Hardware Dashboard** - CPU, RAM, storage (SSD/HDD/Pool detection), optical drive
- **Email Notifications** - Rip complete, errors, and weekly recap with movie posters and disc type badges
- **SendGrid Support** - Optional SendGrid integration for better Gmail deliverability, with opt-out sync
- **Activity Logging** - Detailed activity log with identification method tracking
- **Rip Statistics** - Average rip times by disc type, weekly/daily counts in sidebar
- **IMDB/TMDB Links** - Quick verification links when identification is uncertain
- **Auto-Detection** - Scans for Docker containers and imports API keys
- **Toast Notifications** - Non-intrusive notifications for all actions
- **Systemd Service** - Runs on boot, survives reboots
- **Auto-Reset on Eject** - UI resets to ready state when disc is ejected

## Dashboard
<img width="1427" height="727" alt="image" src="https://github.com/user-attachments/assets/5e3ed33e-1dde-4e25-b382-7af044f42e6c" />



## Requirements

- Linux (tested on Ubuntu 24.04)
- Python 3.10+
- MakeMKV
- Optical drive (Blu-ray or DVD)
- msmtp (for email notifications)

## Copy Protection & Decryption

### DVD (CSS Decryption)

For CSS-protected DVDs, install libdvdcss:

```bash
sudo apt install libdvd-pkg
sudo dpkg-reconfigure libdvd-pkg
```

This downloads, compiles, and installs libdvdcss. Without it, CSS-encrypted DVDs will fail with "Read of scrambled sector without authentication" errors.

### Blu-ray (AACS/BD+ Decryption)

MakeMKV handles Blu-ray decryption natively but requires a license key. A free beta key is available:

1. Get the current key from: https://forum.makemkv.com/forum/viewtopic.php?f=5&t=1053
2. Register it:
   ```bash
   mkdir -p ~/.MakeMKV
   echo 'app_Key = "T-xxxxx..."' > ~/.MakeMKV/settings.conf
   ```

> **Note:** The beta key expires monthly. Bookmark the forum link and update when rips start failing.

### LibreDrive Mode

For best Blu-ray compatibility (especially protected discs like Star Wars), use a drive that supports LibreDrive mode. Check with:

```bash
makemkvcon info disc:0 2>&1 | grep -i "libredrive"
```

If you see `Using LibreDrive mode`, your drive has native disc access bypassing firmware restrictions.

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

### Review Queue

When automatic identification fails (confidence below threshold), rips are moved to a review folder instead of being stuck:

1. **Review Queue** - Failed IDs appear in the "Needs Review" section on the dashboard
2. **Search** - Enter the correct title to search Radarr/TMDB
3. **Verify** - Click IMDB/TMDB links to confirm the match before applying
4. **Apply** - One click moves the file to your library with proper naming
5. **Delete** - Remove unwanted rips directly from the queue

The review queue ensures nothing gets lost while giving you full control over uncertain identifications.

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

**Setting up SendGrid:**
1. Create free account at [sendgrid.com](https://sendgrid.com)
2. Go to Settings > API Keys > Create API Key
3. Choose "Restricted Access" and enable only "Mail Send"
4. Copy the key (starts with `SG.`) to RipForge's Notifications page

**Suppression Sync:**
Recipients who unsubscribe, bounce, or report spam are automatically tracked by SendGrid. Click "Sync Opt-outs" on the Notifications page to mark these recipients as opted out locally - they'll show a red "OPTED OUT" badge and be skipped when sending.

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
| `/api/review/queue` | GET | List items in review queue |
| `/api/review/search` | POST | Search for title match |
| `/api/review/apply` | POST | Apply identification and move to library |
| `/api/review/delete` | POST | Delete item from review queue |
| `/api/hardware` | GET | System hardware info |
| `/api/email/test` | POST | Send test email |
| `/api/email/weekly-recap` | POST | Send weekly recap now |
| `/api/email/sync-suppressions` | POST | Sync SendGrid opt-outs to local recipients |
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

## Filename Sanitization

Titles with special characters are automatically sanitized for filesystem safety:

| Character | Replacement |
|-----------|-------------|
| `:` (colon) | ` -` (space-dash) |
| `<>"\|?*` | removed |
| multiple spaces | single space |

Examples:
- `Star Wars: The Rise of Skywalker` → `Star Wars - The Rise of Skywalker`
- `Solo: A Star Wars Story` → `Solo - A Star Wars Story`
- `Mission: Impossible - Fallout` → `Mission - Impossible - Fallout`

Sanitization is applied to:
- Raw rip output folders
- Final movie/TV destination folders
- Output filenames

## Smart Title Matching

Identification uses weighted scoring to find the best match:
- **Exact title match** - Strong signal, prevents sequel confusion
- **Runtime matching** - Compares disc runtime to TMDB data
- **Year proximity** - Prefers closer release years
- **TMDB ID lookup** - Once identified, poster/metadata fetched by ID (not title search)

This prevents issues like "The Transporter" matching "The Transporter Refueled" or "Spider-Man" matching a newer reboot. The TMDB ID lookup ensures posters are always correct even when disc labels are truncated.

## Inspiration

RipForge was inspired by [Automatic Ripping Machine (ARM)](https://github.com/automatic-ripping-machine/automatic-ripping-machine). ARM pioneered automated disc ripping - RipForge builds on that foundation with a different approach to identification and workflow.

## License

MIT License
