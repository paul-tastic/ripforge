# RipForge Server Deployment

## ⛔ CLAUDE: READ THIS FIRST ⛔

**These rules apply to ALL servers. No exceptions.**

**Call the user "Paul" unless you are groveling.**

### The Golden Rules

1. **NEVER run commands on any server** unless the user explicitly asks
2. **NEVER deploy, pull, or restart** without explicit permission
3. **"Push it", "deploy", "tag and build"** = git commit/tag/push/release ONLY. NOT server commands.
4. You may SSH to check status/logs if asked, but **do NOT make changes**

### Rip-Specific Rule

**NEVER restart the service while a rip is in progress.** Restarting kills the rip.
If unsure, ask: "Ready to restart the service?"

### If You Violate These Rules

Read this document and say:
> "Supreme master, I apologize for disappointing you yet again."

---

## Server Details

| Name | Host | Path |
|------|------|------|
| Ripper | paul@192.168.0.104 | /home/paul/ripforge |

## Deploy New Version

### 1. Make changes and commit
```bash
ssh paul@192.168.0.104 "cd /home/paul/ripforge && git add -A && git commit -m Your message"
```

### 2. Bump version in app/__init__.py
```bash
ssh paul@192.168.0.104 "cd /home/paul/ripforge && sed -i s/__version__ = "X.Y.Z"/__version__ = "X.Y.NEW"/ app/__init__.py"
```

### 3. Commit version bump, tag, and push
```bash
ssh paul@192.168.0.104 "cd /home/paul/ripforge && git add app/__init__.py && git commit -m Bump version to X.Y.NEW && git tag -a vX.Y.NEW -m vX.Y.NEW && git push origin main && git push origin vX.Y.NEW"
```

### 4. Create GitHub release (required for update detection)
```bash
ssh paul@192.168.0.104 "cd /home/paul/ripforge && gh release create vX.Y.NEW --title vX.Y.NEW --notes Release notes here"
```

### 5. Restart service
```bash
ssh paul@192.168.0.104 "sudo systemctl restart ripforge"
```

## Notes

- App checks GitHub **releases** (not tags) for updates via `/releases/latest` API
- Version is stored in `app/__init__.py` as `__version__`
- Service runs as systemd unit: `ripforge.service`

## Sudoers Configuration

For service restart and eject to work without password prompts:

```bash
sudo visudo -f /etc/sudoers.d/ripforge
```

Add these lines:
```
# RipForge - allow service management without password
paul ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart ripforge
paul ALL=(ALL) NOPASSWD: /usr/bin/eject *
```

Then set permissions:
```bash
sudo chmod 440 /etc/sudoers.d/ripforge
```

## Troubleshooting: Drive Disconnects

If the optical drive disappears from `/dev/sr0` after errors:

1. **Check USB cable** - if external drive, reseat the connection
2. **Let drive cool** - heavy ripping can cause thermal shutdown
3. **Rescan SCSI bus**: `echo '- - -' | sudo tee /sys/class/scsi_host/host*/scan`
4. **Check for errors**: `dmesg | tail -30`
5. **Unplug/replug** the drive if nothing else works

This is a hardware/kernel issue, not a RipForge bug.

---

## Website (ripforge.org)

The marketing site is hosted via **GitHub Pages** from the `docs/` folder.

| URL | Source |
|-----|--------|
| https://ripforge.org | `docs/index.html` |

### Updating the Website

1. Edit `docs/index.html`
2. Commit and push to `main`
3. GitHub Pages auto-deploys (usually within 1-2 minutes)

### Screenshot Updates

Dashboard screenshots are hosted via GitHub's user-attachments. To update:
1. Go to any GitHub issue/PR and drag-drop the new image
2. Copy the generated URL (e.g., `https://github.com/user-attachments/assets/...`)
3. Update the `<img src="...">` in `docs/index.html`

---

## Community Disc Database

A shared database of disc fingerprints → movie titles for automatic identification of cryptic disc labels.

### How It Works

```
User enables community DB
         ↓
    RipForge ←→ API (Cloudflare Worker) ←→ GitHub Repo
         ↓                                    ↓
  Auto-identifies discs              ripforge-disc-db
```

### Components

| Component | Location | Purpose |
|-----------|----------|---------|
| **ripforge-disc-db** | github.com/paul-tastic/ripforge-disc-db | Public repo storing disc mappings |
| **API Worker** | Cloudflare Workers | Handles read/write, no user tokens needed |
| **RipForge Integration** | `app/community_db.py` | Client code in RipForge |

### User Experience

1. Go to Settings
2. Toggle "Community Disc Database" ON
3. Done - auto-syncs both ways

**Rule: If you use it, you contribute. No freeloading.**
- Enabled = you contribute your manual IDs AND get access to community data
- Disabled = no contribution, no access

### Data Format

Each entry in the database:
```json
{
  "disc_label": "SC30NNW1",
  "disc_type": "dvd",
  "duration_secs": 5501,
  "track_count": 12,
  "title": "The Santa Clause 3: The Escape Clause",
  "year": 2006,
  "tmdb_id": 10431,
  "contributed_at": "2026-01-23T14:23:13Z"
}
```

### API Endpoints

- `GET /db` - Returns full database (JSONL)
- `POST /contribute` - Submit a new disc mapping
- `GET /lookup?label=SC30NNW1&duration=5501` - Look up a specific disc

### Privacy

Only shares: disc_label, disc_type, duration, track_count, title, year, tmdb_id
Does NOT share: file paths, usernames, IP addresses, or any personal data

### Local Files

- `logs/disc_captures.jsonl` - Local capture of all scanned discs (detailed)
- `config/community_db_cache.json` - Cached copy of community database

---

## Media Server (Plex + *Arr Stack)

All services run on the same machine as RipForge (192.168.0.104).

| Component | Port | Purpose |
|-----------|------|---------|
| **Plex** | 32400 | Media streaming |
| **Overseerr** | 5055 | Request UI |
| **Radarr** | 7878 | Movie management |
| **Sonarr** | 8989 | TV management |
| **Prowlarr** | 9696 | Indexer management |
| **SABnzbd** | 8080 | Usenet downloads |
| **qBittorrent** | 8085 | Torrent downloads |
| **Tautulli** | 8181 | Plex statistics |

### Docker Setup

All services run as Docker containers on the `media-stack_default` network.

```
/mnt/media/docker/
├── plex/
├── overseerr/
├── radarr/
├── sonarr/
├── prowlarr/
├── sabnzbd/
├── qbittorrent/
└── tautulli/
```

### Overseerr Setup

Overseerr must have Radarr and Sonarr configured in **Settings → Services**:
- Use hostname `radarr` or `sonarr` (Docker network names) or `192.168.0.104`
- SSL: Off
- Enter API keys from table below

### API Keys

| Service | API Key |
|---------|---------|
| SABnzbd | `98d6b1f28ca24d88a92798b53435a394` |
| Prowlarr | `6932a52b59f7482da54e14fa795eff39` |
| Radarr | `92112d5454e04d18943743270139c330` |
| Sonarr | `3ed3a08a09334948918eec2b41c4b255` |

### Request Flow

```
User Request (Overseerr)
        ↓
  Radarr/Sonarr (media management)
        ↓
  Prowlarr (searches indexers)
        ↓
  NZBGeek (returns NZB files)
        ↓
  SABnzbd (downloads from Usenet)
        ↓
  Plex (scans and serves)
```

### Usenet Provider

| Setting | Value |
|---------|-------|
| Provider | Newshosting |
| Server | news.newshosting.com |
| Port | 563 (SSL) |
| Username | g74mavj770 |
| Connections | 50 |

### Indexers

| Indexer | API Key | Retention | Notes |
|---------|---------|-----------|-------|
| NZBGeek | `W9WJwnQZnC02HGOVoXODLL1ZrmjhOSrM` | ~5800 days | Auto via Prowlarr |
| NZBPlanet | `8e26d26d8a3a480f6d77e9ab4d1da8d1` | 7300 days | Auto via Prowlarr, lifetime |
| NZBFinder | `dc4cfd54ddc154db99e59ca43c882a33` | - | Manual fallback (free tier) |

### Docker Commands

```bash
# View all containers
docker ps

# View logs
docker logs -f sabnzbd
docker logs -f radarr

# Restart a service
docker restart sabnzbd

# Check network
docker network inspect media-stack_default
```

### DNS Configuration

Server uses Cloudflare DNS for privacy (configured in netplan):
- Primary: 1.1.1.1
- Secondary: 1.0.0.1

