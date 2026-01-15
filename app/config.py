"""
RipForge Configuration Management
Handles loading, saving, and auto-detection of services
"""

import os
import yaml
import subprocess
import requests
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "settings.yaml"
DEFAULT_CONFIG = CONFIG_DIR / "default.yaml"


def load_config() -> dict:
    """Load configuration from file, or defaults if not exists"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f)
    elif DEFAULT_CONFIG.exists():
        with open(DEFAULT_CONFIG) as f:
            return yaml.safe_load(f)
    return {}


def save_config(config: dict):
    """Save configuration to file"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def test_connection(service: str, url: str, api_key: str = "", token: str = "") -> dict:
    """Test connection to a service and return status"""
    result = {"connected": False, "error": None, "version": None}

    try:
        if service == "radarr":
            r = requests.get(f"{url}/api/v3/system/status",
                           headers={"X-Api-Key": api_key}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                result["connected"] = True
                result["version"] = data.get("version")

        elif service == "sonarr":
            r = requests.get(f"{url}/api/v3/system/status",
                           headers={"X-Api-Key": api_key}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                result["connected"] = True
                result["version"] = data.get("version")

        elif service == "overseerr":
            r = requests.get(f"{url}/api/v1/status",
                           headers={"X-Api-Key": api_key}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                result["connected"] = True
                result["version"] = data.get("version")

        elif service == "plex":
            r = requests.get(f"{url}/identity",
                           headers={"X-Plex-Token": token}, timeout=5)
            if r.status_code == 200:
                result["connected"] = True

        elif service == "tautulli":
            r = requests.get(f"{url}/api/v2?apikey={api_key}&cmd=arnold", timeout=5)
            if r.status_code == 200:
                result["connected"] = True

    except requests.exceptions.RequestException as e:
        result["error"] = str(e)

    return result


def detect_docker_services() -> dict:
    """Auto-detect services running in Docker containers"""
    services = {}

    try:
        # Get running containers
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Ports}}"],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('|')
                if len(parts) >= 2:
                    name, image = parts[0], parts[1]

                    # Detect known services
                    if 'radarr' in image.lower() or 'radarr' in name.lower():
                        services['radarr'] = {
                            'detected': True,
                            'container': name,
                            'url': 'http://localhost:7878'
                        }
                    elif 'sonarr' in image.lower() or 'sonarr' in name.lower():
                        services['sonarr'] = {
                            'detected': True,
                            'container': name,
                            'url': 'http://localhost:8989'
                        }
                    elif 'overseerr' in image.lower() or 'overseerr' in name.lower():
                        services['overseerr'] = {
                            'detected': True,
                            'container': name,
                            'url': 'http://localhost:5055'
                        }
                    elif 'plex' in image.lower() or 'plex' in name.lower():
                        services['plex'] = {
                            'detected': True,
                            'container': name,
                            'url': 'http://localhost:32400'
                        }
                    elif 'tautulli' in image.lower() or 'tautulli' in name.lower():
                        services['tautulli'] = {
                            'detected': True,
                            'container': name,
                            'url': 'http://localhost:8181'
                        }

    except Exception as e:
        print(f"Error detecting Docker services: {e}")

    return services


def detect_optical_drives() -> list:
    """Detect optical drives on the system"""
    drives = []

    try:
        # Check /dev/sr* devices
        result = subprocess.run(
            ["lsblk", "-o", "NAME,TYPE,MODEL", "-n"],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if 'rom' in line.lower():
                    parts = line.split()
                    if parts:
                        device = f"/dev/{parts[0]}"
                        model = ' '.join(parts[2:]) if len(parts) > 2 else "Unknown"
                        drives.append({
                            'device': device,
                            'model': model.strip()
                        })

    except Exception as e:
        print(f"Error detecting optical drives: {e}")

    return drives


def import_existing_api_keys() -> dict:
    """Try to import API keys from existing scripts"""
    keys = {}

    # Known locations for API keys
    files_to_check = [
        "/mnt/media/docker/arm/smart-identify.sh",
        "/mnt/media/docker/arm/move-completed.sh",
        "/mnt/media/docker/plex-newsletter.sh",
    ]

    for filepath in files_to_check:
        try:
            with open(filepath) as f:
                content = f.read()

                # Look for Radarr API key
                if 'RADARR_API' in content:
                    for line in content.split('\n'):
                        if 'RADARR_API=' in line and '#' not in line.split('RADARR_API')[0]:
                            key = line.split('=')[1].strip().strip('"\'')
                            if key and len(key) > 10:
                                keys['radarr_api'] = key

                # Look for Sonarr API key
                if 'SONARR_API' in content:
                    for line in content.split('\n'):
                        if 'SONARR_API=' in line and '#' not in line.split('SONARR_API')[0]:
                            key = line.split('=')[1].strip().strip('"\'')
                            if key and len(key) > 10:
                                keys['sonarr_api'] = key

                # Look for Plex token
                if 'PLEX_TOKEN' in content:
                    for line in content.split('\n'):
                        if 'PLEX_TOKEN=' in line and '#' not in line.split('PLEX_TOKEN')[0]:
                            key = line.split('=')[1].strip().strip('"\'')
                            if key and len(key) > 5:
                                keys['plex_token'] = key

                # Look for Tautulli API key
                if 'TAUTULLI_API' in content:
                    for line in content.split('\n'):
                        if 'TAUTULLI_API=' in line and '#' not in line.split('TAUTULLI_API')[0]:
                            key = line.split('=')[1].strip().strip('"\'')
                            if key and len(key) > 10:
                                keys['tautulli_api'] = key

        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"Error reading {filepath}: {e}")

    return keys


def run_auto_setup() -> dict:
    """Run full auto-detection and return discovered configuration"""
    print("Running auto-detection...")

    # Detect Docker services
    print("  Scanning Docker containers...")
    services = detect_docker_services()

    # Detect optical drives
    print("  Scanning optical drives...")
    drives = detect_optical_drives()

    # Import existing API keys
    print("  Looking for existing API keys...")
    keys = import_existing_api_keys()

    return {
        'services': services,
        'drives': drives,
        'keys': keys
    }
