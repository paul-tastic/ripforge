"""
RipForge Community Disc Database Client

Opt-in system for sharing and using community disc identifications.
If you use it, you contribute. No freeloading.
"""

import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
from . import activity

# API endpoint (Cloudflare Worker)
API_URL = "https://ripforge-disc-db.paul-tastic.workers.dev"

# Local cache
CACHE_FILE = Path(__file__).parent.parent / "config" / "community_db_cache.json"
CACHE_MAX_AGE = 3600  # Refresh cache every hour


def is_enabled(config: dict) -> bool:
    """Check if community DB is enabled in config."""
    return config.get('community_db', {}).get('enabled', True)  # Default ON


def lookup_disc(disc_label: str, duration_secs: int, config: dict) -> Optional[Dict]:
    """
    Look up a disc in the community database.
    Returns the matched entry or None.
    """
    if not is_enabled(config):
        return None

    try:
        # Try local cache first
        cached = _check_cache(disc_label, duration_secs)
        if cached:
            activity.log_info(f"COMMUNITY DB: Cache hit for '{disc_label}'")
            return cached

        # Query the API
        response = requests.get(
            f"{API_URL}/lookup",
            params={'label': disc_label, 'duration': duration_secs},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('found'):
                entry = data['entry']
                activity.log_success(f"COMMUNITY DB: Found '{disc_label}' -> '{entry['title']}'")
                return entry
            else:
                activity.log_info(f"COMMUNITY DB: No match for '{disc_label}'")
        else:
            activity.log_warning(f"COMMUNITY DB: API error {response.status_code}")

    except requests.exceptions.RequestException as e:
        activity.log_warning(f"COMMUNITY DB: Connection error - {e}")
    except Exception as e:
        activity.log_warning(f"COMMUNITY DB: Error - {e}")

    return None


def contribute_disc(
    disc_label: str,
    disc_type: str,
    duration_secs: int,
    track_count: int,
    title: str,
    year: Optional[int],
    tmdb_id: Optional[int],
    config: dict,
    media_type: str = 'movie'
) -> bool:
    """
    Contribute a disc identification to the community database.
    Called after manual identification.
    """
    if not is_enabled(config):
        return False

    # === GUARDRAILS ===

    # 1. Skip TV shows - database is movie-focused
    if media_type == 'tv':
        activity.log_info("COMMUNITY DB: Skipping TV show contribution")
        return False

    # 2. Skip generic/useless disc labels
    generic_labels = {'DVD_VIDEO', 'DVDVIDEO', 'DISC1', 'DISC2', 'DISC_1', 'DISC_2',
                      'BDROM', 'BLURAY', 'BLU-RAY', 'LOGICAL_VOLUME_ID'}
    if disc_label.upper() in generic_labels:
        activity.log_info(f"COMMUNITY DB: Skipping generic disc label '{disc_label}'")
        return False

    # 3. Skip timestamp-prefixed labels (shouldn't happen anymore, but safety check)
    import re
    if re.match(r'^\d{8}_\d{6}_', disc_label):
        activity.log_info(f"COMMUNITY DB: Skipping timestamp-prefixed label '{disc_label}'")
        return False

    # 4. Validate duration - movies should be 60-300 minutes (3600-18000 secs)
    if duration_secs < 3600:
        activity.log_info(f"COMMUNITY DB: Skipping - duration too short ({duration_secs}s)")
        return False
    if duration_secs > 18000:  # 5 hours max
        activity.log_info(f"COMMUNITY DB: Skipping - duration too long ({duration_secs}s)")
        return False

    # 5. Require valid TMDB ID for quality assurance
    if not tmdb_id or tmdb_id <= 0:
        activity.log_info("COMMUNITY DB: Skipping - no valid TMDB ID")
        return False

    # 6. Require a year (helps with disambiguation)
    if not year or year < 1900 or year > 2100:
        activity.log_info("COMMUNITY DB: Skipping - invalid year")
        return False

    try:
        payload = {
            'disc_label': disc_label,
            'disc_type': disc_type,
            'duration_secs': duration_secs,
            'track_count': track_count,
            'title': title,
            'year': year,
            'tmdb_id': tmdb_id
        }

        response = requests.post(
            f"{API_URL}/contribute",
            json=payload,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                if data.get('duplicate'):
                    activity.log_info(f"COMMUNITY DB: '{disc_label}' already in database")
                else:
                    activity.log_success(f"COMMUNITY DB: Contributed '{disc_label}' -> '{title}'")
                return True
            else:
                activity.log_warning(f"COMMUNITY DB: Contribution failed - {data.get('error')}")
        else:
            activity.log_warning(f"COMMUNITY DB: API error {response.status_code}")

    except requests.exceptions.RequestException as e:
        activity.log_warning(f"COMMUNITY DB: Connection error - {e}")
    except Exception as e:
        activity.log_warning(f"COMMUNITY DB: Error - {e}")

    return False


def refresh_cache(config: dict) -> bool:
    """
    Refresh the local cache of the community database.
    Called on startup if enabled.
    """
    if not is_enabled(config):
        return False

    try:
        response = requests.get(f"{API_URL}/db", timeout=30)

        if response.status_code == 200:
            data = response.json()
            cache_data = {
                'updated_at': datetime.now().isoformat(),
                'count': data.get('count', 0),
                'entries': data.get('entries', [])
            }

            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump(cache_data, f, indent=2)

            activity.log_info(f"COMMUNITY DB: Cache refreshed ({data.get('count', 0)} entries)")
            return True
        else:
            activity.log_warning(f"COMMUNITY DB: Failed to refresh cache - {response.status_code}")

    except requests.exceptions.RequestException as e:
        activity.log_warning(f"COMMUNITY DB: Connection error - {e}")
    except Exception as e:
        activity.log_warning(f"COMMUNITY DB: Error refreshing cache - {e}")

    return False


def _check_cache(disc_label: str, duration_secs: int) -> Optional[Dict]:
    """Check local cache for a disc match."""
    try:
        if not CACHE_FILE.exists():
            return None

        with open(CACHE_FILE) as f:
            cache = json.load(f)

        # Check if cache is too old
        updated_at = datetime.fromisoformat(cache.get('updated_at', '2000-01-01'))
        age_seconds = (datetime.now() - updated_at).total_seconds()
        if age_seconds > CACHE_MAX_AGE:
            return None  # Cache too old, will trigger API call

        entries = cache.get('entries', [])

        # Exact label match
        for entry in entries:
            if entry.get('disc_label') == disc_label:
                return entry

        # Fuzzy match by duration (within 5%)
        if duration_secs > 0:
            tolerance = duration_secs * 0.05
            for entry in entries:
                if abs(entry.get('duration_secs', 0) - duration_secs) <= tolerance:
                    return entry

    except Exception:
        pass

    return None


def get_cache_stats() -> Dict:
    """Get stats about the local cache for display in UI."""
    try:
        if not CACHE_FILE.exists():
            return {'exists': False, 'count': 0, 'updated_at': None}

        with open(CACHE_FILE) as f:
            cache = json.load(f)

        return {
            'exists': True,
            'count': cache.get('count', 0),
            'updated_at': cache.get('updated_at')
        }
    except Exception:
        return {'exists': False, 'count': 0, 'updated_at': None}


def upload_pending_captures() -> Dict:
    """
    Upload any pending disc captures that have identifications.
    Called when community DB is enabled to upload backlog.
    Returns stats about what was uploaded.
    """
    from . import activity as act

    captures_file = Path(__file__).parent.parent / "logs" / "disc_captures.jsonl"

    if not captures_file.exists():
        return {'uploaded': 0, 'skipped': 0, 'errors': 0}

    uploaded = 0
    skipped = 0
    errors = 0

    try:
        with open(captures_file) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())

                    # Only upload entries with identified titles
                    if not entry.get('identified_title'):
                        skipped += 1
                        continue

                    # Build payload
                    payload = {
                        'disc_label': entry.get('disc_label'),
                        'disc_type': entry.get('disc_type', 'dvd'),
                        'duration_secs': entry.get('main_duration_secs', 0),
                        'track_count': entry.get('track_count', 0),
                        'title': entry.get('identified_title', '').split(' (')[0],  # Strip year
                        'year': entry.get('year'),
                        'tmdb_id': entry.get('tmdb_id')
                    }

                    # Skip if missing required fields
                    if not payload['disc_label'] or not payload['title']:
                        skipped += 1
                        continue

                    # Upload
                    response = requests.post(
                        f"{API_URL}/contribute",
                        json=payload,
                        timeout=15
                    )

                    if response.status_code == 200:
                        uploaded += 1
                    else:
                        errors += 1

                except (json.JSONDecodeError, KeyError):
                    errors += 1
                    continue

    except Exception as e:
        act.log_warning(f"COMMUNITY DB: Error uploading backlog - {e}")

    if uploaded > 0:
        act.log_success(f"COMMUNITY DB: Uploaded {uploaded} pending captures")

    return {'uploaded': uploaded, 'skipped': skipped, 'errors': errors}
