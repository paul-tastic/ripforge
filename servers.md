# RipForge Server Deployment

## Claude Instructions

**IMPORTANT: Always read this file when working on this project.**

- **NEVER deploy to the server without explicit user permission**
- **NEVER restart services without explicit user permission**
- **NEVER run commands on the server** unless the user specifically asks
- You may SSH to check status/logs if asked, but do NOT make changes
- All code changes should be committed to GitHub only - the user will deploy when ready

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
