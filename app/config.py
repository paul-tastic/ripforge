"""
RipForge Configuration Management
Handles loading, saving, and auto-detection of services
"""

import os
import re
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


def _simplify_gpu_name(gpu: str) -> str:
    """Clean up verbose GPU names from lspci"""
    import re

    # Remove revision info like "(rev c8)"
    gpu = re.sub(r'\s*\(rev [a-f0-9]+\)', '', gpu)

    # Handle AMD/ATI verbose names
    # "Advanced Micro Devices, Inc. [AMD/ATI] Cezanne [Radeon Vega Series / Radeon Vega Mobile Series]"
    # -> "AMD Radeon Vega (Cezanne)"
    if 'AMD' in gpu or 'ATI' in gpu:
        # Extract codename from brackets
        codename_match = re.search(r'\]\s*(\w+)\s*\[', gpu)
        codename = codename_match.group(1) if codename_match else ''

        # Extract product name
        product_match = re.search(r'\[([^\]]*Radeon[^\]]*)\]', gpu)
        if product_match:
            product = product_match.group(1)
            # Simplify "Radeon Vega Series / Radeon Vega Mobile Series" -> "Radeon Vega"
            product = re.sub(r'\s*/[^/]+$', '', product)  # Remove after slash
            product = re.sub(r'\s+Series$', '', product)  # Remove "Series"
            if codename:
                return f"AMD {product} ({codename})"
            return f"AMD {product}"

        # Fallback for AMD without Radeon
        if codename:
            return f"AMD {codename}"

    # Handle NVIDIA - usually cleaner but remove corp name
    if 'NVIDIA' in gpu:
        gpu = re.sub(r'NVIDIA Corporation\s*', 'NVIDIA ', gpu)
        gpu = re.sub(r'\s+', ' ', gpu).strip()
        return gpu

    # Handle Intel
    if 'Intel' in gpu:
        gpu = re.sub(r'Intel Corporation\s*', 'Intel ', gpu)
        gpu = re.sub(r'\s+', ' ', gpu).strip()
        return gpu

    return gpu


def detect_hardware() -> dict:
    """Detect system hardware for the flex card"""
    hardware = {
        'cpu': 'Unknown',
        'cpu_cores': 0,
        'ram_gb': 0,
        'ram_used_gb': 0,
        'ram_type': None,         # e.g., "DDR4"
        'ram_speed': None,        # e.g., "2666 MHz"
        'storage': [],
        'drives': [],             # Individual physical drives
        'disk_total': '',
        'disk_used': '',
        'disk_percent': 0,
        'os': 'Unknown',
        'hostname': 'Unknown',
        'ip_address': 'Unknown',
        'network_interface': None,  # e.g., "Ethernet" or "Wi-Fi"
        'uptime': 'Unknown',
        'gpu': None,
        'docker_version': None
    }

    try:
        # CPU info
        result = subprocess.run(
            ["lscpu"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Model name:' in line:
                    cpu_full = line.split(':')[1].strip()
                    # Split integrated GPU from CPU name (e.g., "AMD Ryzen 7 5700G with Radeon Graphics")
                    if ' with ' in cpu_full:
                        cpu_name, gpu_name = cpu_full.split(' with ', 1)
                        hardware['cpu'] = cpu_name.strip()
                        hardware['integrated_gpu'] = gpu_name.strip()
                    else:
                        hardware['cpu'] = cpu_full
                elif line.startswith('CPU(s):'):
                    hardware['cpu_cores'] = int(line.split(':')[1].strip())

        # RAM info
        result = subprocess.run(
            ["free", "-g"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 3:
                    hardware['ram_gb'] = int(parts[1])
                    hardware['ram_used_gb'] = int(parts[2])

        # RAM type and speed (requires sudo, may fail)
        try:
            result = subprocess.run(
                ["sudo", "dmidecode", "-t", "memory"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if line.startswith('Type:') and 'DDR' in line:
                        hardware['ram_type'] = line.split(':')[1].strip()
                    elif line.startswith('Speed:') and 'MT/s' in line:
                        speed = line.split(':')[1].strip()
                        # Convert "2666 MT/s" to "2666 MHz"
                        hardware['ram_speed'] = speed.replace('MT/s', 'MHz')
                    elif line.startswith('Configured Memory Speed:') and 'MT/s' in line:
                        # Prefer configured speed if available
                        speed = line.split(':')[1].strip()
                        hardware['ram_speed'] = speed.replace('MT/s', 'MHz')
        except Exception:
            pass  # dmidecode may require sudo, silently fail

        # Get drive types (SSD vs HDD) - ROTA=0 is SSD, ROTA=1 is HDD
        drive_types = {}
        result = subprocess.run(
            ["lsblk", "-d", "-o", "NAME,ROTA", "-n"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 2:
                    drive_types[parts[0]] = 'SSD' if parts[1] == '0' else 'HDD'

        # Individual physical drives with details (use pipe separator for reliable parsing)
        result = subprocess.run(
            ["lsblk", "-d", "-o", "NAME,SIZE,MODEL,TYPE", "-n", "-P"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                # Parse KEY="value" format
                fields = {}
                for match in re.finditer(r'(\w+)="([^"]*)"', line):
                    fields[match.group(1)] = match.group(2)
                name = fields.get('NAME', '')
                # Skip loop devices and optical drives
                if name.startswith('loop') or name.startswith('sr'):
                    continue
                dtype = fields.get('TYPE', '')
                if dtype != 'disk':
                    continue
                size = fields.get('SIZE', '')
                model = fields.get('MODEL', '').strip() or 'Unknown'
                drive_type = drive_types.get(name, 'Unknown')
                hardware['drives'].append({
                    'name': f'/dev/{name}',
                    'size': size,
                    'model': model,
                    'type': drive_type
                })

        # Storage info - get all mounted filesystems with usage
        result = subprocess.run(
            ["df", "-h", "--output=source,size,used,avail,pcent,target", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs", "-x", "overlay"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            for line in lines:
                parts = line.split()
                if len(parts) >= 6:
                    source = parts[0]
                    mount = parts[5]
                    # Skip boot partitions and small system partitions
                    if '/boot' in mount or mount == '/':
                        continue
                    # Only show meaningful mounts
                    if mount.startswith('/mnt') or mount.startswith('/media') or mount.startswith('/home'):
                        # Skip underlying mergerfs component disks (disk1, disk2, etc.)
                        if '/disk' in mount:
                            continue
                        # Determine drive type from source device
                        drive_name = source.replace('/dev/', '').rstrip('0123456789')
                        drive_type = drive_types.get(drive_name, '')
                        # For mergerfs, mark as "Pool"
                        if 'mergerfs' in source or source.startswith('/mnt/'):
                            drive_type = 'Pool'
                        hardware['storage'].append({
                            'mount': mount,
                            'size': parts[1],
                            'used': parts[2],
                            'avail': parts[3],
                            'percent': parts[4],
                            'source': source,
                            'type': drive_type
                        })

        # Disk usage for /mnt/media
        result = subprocess.run(
            ["df", "-h", "/mnt/media"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    hardware['disk_total'] = parts[1]
                    hardware['disk_used'] = parts[2]
                    hardware['disk_percent'] = int(parts[4].replace('%', ''))

        # OS info and hostname
        result = subprocess.run(
            ["hostnamectl"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Operating System:' in line:
                    hardware['os'] = line.split(':')[1].strip()
                elif 'Static hostname:' in line:
                    hardware['hostname'] = line.split(':')[1].strip()

        # IP address and network interface type
        result = subprocess.run(
            ["hostname", "-I"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            if ips:
                hardware['ip_address'] = ips[0]

        # Detect network interface type (Ethernet vs Wi-Fi)
        result = subprocess.run(
            ["ip", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                # Skip loopback and virtual interfaces
                if ' lo ' in line or 'docker' in line or 'br-' in line or 'veth' in line:
                    continue
                # Look for interface with our IP (handle subnet mask like /24)
                ip_with_mask = hardware['ip_address'] + '/'
                if ip_with_mask in line or hardware['ip_address'] + ' ' in line:
                    # Extract interface name (second field)
                    parts = line.split()
                    if len(parts) >= 2:
                        iface = parts[1].rstrip(':')
                        # Determine type from interface name
                        if iface.startswith('wl') or iface.startswith('wlan'):
                            hardware['network_interface'] = 'Wi-Fi'
                        elif iface.startswith('en') or iface.startswith('eth'):
                            hardware['network_interface'] = 'Ethernet'
                        else:
                            hardware['network_interface'] = iface
                        break

        # Uptime
        result = subprocess.run(
            ["uptime", "-p"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            hardware['uptime'] = result.stdout.strip().replace('up ', '')

        # GPU (try lspci for nvidia/amd)
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'VGA' in line or '3D' in line:
                    # Extract GPU name after the colon
                    if ':' in line:
                        gpu_part = line.split(':')[-1].strip()
                        # Clean up verbose GPU names
                        gpu_part = _simplify_gpu_name(gpu_part)
                        hardware['gpu'] = gpu_part
                        break

        # Docker version
        result = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # "Docker version 24.0.5, build ced0996"
            version_str = result.stdout.strip()
            if 'version' in version_str.lower():
                hardware['docker_version'] = version_str.split(',')[0].replace('Docker version ', '')

    except Exception as e:
        print(f"Error detecting hardware: {e}")

    return hardware


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


def get_plex_users() -> list:
    """Retrieve Plex users (owner and shared) with their emails from Plex.tv API"""
    users = []
    cfg = load_config()

    plex_cfg = cfg.get('integrations', {}).get('plex', {})
    token = plex_cfg.get('token', '')

    if not token:
        return users

    headers = {
        'X-Plex-Token': token,
        'X-Plex-Client-Identifier': 'ripforge',
        'Accept': 'application/json'
    }

    try:
        # Get main account owner
        r = requests.get('https://plex.tv/api/v2/user', headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get('email'):
                users.append({
                    'username': data.get('username', 'Owner'),
                    'email': data['email'],
                    'type': 'owner',
                    'thumb': data.get('thumb', '')
                })

        # Get home users
        r = requests.get('https://plex.tv/api/v2/home/users', headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            for user in data.get('users', []):
                # Skip if same as owner (already added)
                if user.get('email') and not any(u['email'] == user['email'] for u in users):
                    users.append({
                        'username': user.get('username', user.get('title', '')),
                        'email': user['email'],
                        'type': 'home',
                        'thumb': user.get('thumb', '')
                    })

        # Get friends/shared users
        r = requests.get('https://plex.tv/api/v2/friends', headers=headers, timeout=10)
        if r.status_code == 200:
            friends = r.json()
            for friend in friends:
                if friend.get('email') and not any(u['email'] == friend['email'] for u in users):
                    users.append({
                        'username': friend.get('username', friend.get('title', '')),
                        'email': friend['email'],
                        'type': 'friend',
                        'thumb': friend.get('thumb', '')
                    })

    except requests.exceptions.RequestException as e:
        print(f"Error fetching Plex users: {e}")

    return users


def run_auto_setup() -> dict:
    """Run full auto-detection and apply discovered configuration"""
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

    # Load current config and apply discovered settings
    cfg = load_config()
    if 'integrations' not in cfg:
        cfg['integrations'] = {}

    # Apply discovered services and keys
    for service_name, service_data in services.items():
        if service_name not in cfg['integrations']:
            cfg['integrations'][service_name] = {}
        cfg['integrations'][service_name]['enabled'] = True
        cfg['integrations'][service_name]['url'] = service_data['url']

    # Apply discovered API keys
    key_mapping = {
        'radarr_api': ('radarr', 'api_key'),
        'sonarr_api': ('sonarr', 'api_key'),
        'plex_token': ('plex', 'token'),
        'tautulli_api': ('tautulli', 'api_key'),
    }

    for key_name, (service, field) in key_mapping.items():
        if key_name in keys:
            if service not in cfg['integrations']:
                cfg['integrations'][service] = {'enabled': True}
            cfg['integrations'][service][field] = keys[key_name]

    # Apply discovered optical drive
    if drives and 'drive' not in cfg:
        cfg['drive'] = {'device': drives[0]['device']}

    # Save the updated config
    save_config(cfg)
    print("  Configuration saved!")

    return {
        'services': services,
        'drives': drives,
        'keys': keys,
        'applied': True
    }
