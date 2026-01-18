# RipForge Server Deployment

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
