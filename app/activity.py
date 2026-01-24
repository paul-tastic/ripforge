"""
RipForge Activity Logger
Logs user-facing events to activity log file
"""

import os
import re
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

LOG_DIR = Path(__file__).parent.parent / "logs"
ACTIVITY_LOG = LOG_DIR / "activity.log"
HISTORY_FILE = LOG_DIR / "rip_history.json"
DISC_CAPTURES_FILE = LOG_DIR / "disc_captures.jsonl"

# Ensure log directory exists
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(message: str, level: str = "INFO"):
    """Log an activity event"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level = level.upper()

    line = f"{timestamp} | {level} | {message}\n"

    try:
        with open(ACTIVITY_LOG, "a") as f:
            f.write(line)
    except Exception as e:
        print(f"Failed to write activity log: {e}")


def log_info(message: str):
    """Log an info event"""
    log(message, "INFO")


def log_success(message: str):
    """Log a success event"""
    log(message, "SUCCESS")


def log_error(message: str):
    """Log an error event"""
    log(message, "ERROR")


def log_warning(message: str):
    """Log a warning event"""
    log(message, "WARN")


# Convenience functions for specific events
def disc_inserted(device: str = "/dev/sr0"):
    log_info(f"Disc inserted in {device}")


def disc_detected(disc_type: str, label: str):
    log_info(f"Disc detected: {label} ({disc_type})")


def scan_started(device: str = "/dev/sr0"):
    log_info(f"Scan started on {device}")


def scan_completed(label: str, disc_type: str, runtime: str = None):
    msg = f"Scan completed: {label} ({disc_type})"
    if runtime:
        msg += f" - {runtime}"
    log_success(msg)


def scan_failed(error: str):
    log_error(f"Scan failed: {error}")


def rip_started(title: str, mode: str = "main feature only"):
    log(f"Rip started: {title} ({mode})", "START")


def rip_progress(title: str, percent: int):
    # Only log at 25%, 50%, 75% milestones to avoid spam
    if percent in [25, 50, 75]:
        log_info(f"Rip progress: {title} - {percent}%")


def rip_identified(original: str, identified: str, confidence: int):
    display_conf = min(confidence, 100)  # Cap at 100% for display
    log_success(f"Identified: {original} -> {identified} ({display_conf}% confidence)")


def id_method_result(method: str, result: str, confidence: int, details: str = None):
    """Log an identification method's guess"""
    display_conf = min(confidence, 100)  # Cap at 100% for display
    msg = f"ID Method: {method} -> \"{result}\" ({display_conf}%)"
    if details:
        msg += f" [{details}]"
    log_info(msg)


def rip_completed(title: str, duration: str = None):
    msg = f"Rip completed: {title}"
    if duration:
        msg += f" ({duration})"
    log_success(msg)


def rip_failed(title: str, error: str):
    log_error(f"Rip failed: {title} - {error}")


def rip_cancelled(title: str, reason: str = None):
    if reason:
        log_warning(f"Rip cancelled: {title} - {reason}")
    else:
        log_warning(f"Rip cancelled: {title}")


def file_moved(filename: str, destination: str):
    log_info(f"Moved: {filename} -> {destination}")


def library_added(title: str, library: str):
    log_success(f"Added to {library}: {title}")


def plex_scan_triggered(library: str = None):
    msg = "Plex library scan triggered"
    if library:
        msg += f": {library}"
    log_info(msg)


def email_sent(email_type: str, recipients: list):
    count = len(recipients)
    log_success(f"{email_type} email sent to {count} recipient(s)")


def email_failed(email_type: str, error: str):
    log_error(f"{email_type} email failed: {error}")


def test_email_requested(recipients: list):
    log_info(f"Test email requested for: {', '.join(recipients)}")


def weekly_recap_sent(recipients: list):
    log_success(f"Weekly recap sent to {len(recipients)} recipient(s)")


def service_started():
    log_info("RipForge service started")


def service_stopped():
    log_info("RipForge service stopped")


# Rip History Tracking
def save_rip_to_history(
    title: str,
    year: int = 0,
    disc_type: str = "unknown",
    runtime_str: str = "",
    size_gb: float = 0,
    duration_str: str = "",
    poster_url: str = "",
    tmdb_id: int = 0,
    overview: str = "",
    rt_rating: int = 0,
    imdb_rating: float = 0.0,
    status: str = "complete",
    content_type: str = "movie",
    rip_method: str = "direct"
):
    """Save completed rip to history for weekly digest

    rip_method can be: "direct", "backup", or "recovery"
    """
    history = load_rip_history()

    entry = {
        "title": title,
        "year": year,
        "disc_type": disc_type,
        "content_type": content_type,  # movie or tv
        "runtime": runtime_str,
        "size_gb": round(size_gb, 1),
        "rip_duration": duration_str,
        "poster_url": poster_url,
        "tmdb_id": tmdb_id,
        "overview": overview,
        "rt_rating": rt_rating,
        "imdb_rating": imdb_rating,
        "status": status,
        "rip_method": rip_method,
        "completed_at": datetime.now().isoformat()
    }

    history.append(entry)

    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
        log_info(f"Saved to rip history: {title} ({content_type})")
    except Exception as e:
        log_error(f"Error saving rip history: {e}")


def load_rip_history() -> list:
    """Load rip history from file"""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading rip history: {e}")
    return []


def get_recent_rips(days: int = 7, respect_digest_reset: bool = True) -> list:
    """Get rips from the last N days, optionally filtered by digest reset timestamp"""
    from datetime import timedelta
    from . import config

    history = load_rip_history()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    rips = [rip for rip in history if rip.get('completed_at', '') >= cutoff]

    # Also filter by digest reset time if set
    if respect_digest_reset:
        cfg = config.load_config()
        digest_reset = cfg.get('notifications', {}).get('email', {}).get('digest_reset_at')
        if digest_reset:
            rips = [rip for rip in rips if rip.get('completed_at', '') > digest_reset]

    return rips


def reset_digest_list():
    """Mark current time as digest reset - clears the "recently added" list for next digest"""
    from . import config

    cfg = config.load_config()
    if 'notifications' not in cfg:
        cfg['notifications'] = {}
    if 'email' not in cfg['notifications']:
        cfg['notifications']['email'] = {}

    cfg['notifications']['email']['digest_reset_at'] = datetime.now().isoformat()
    config.save_config(cfg)
    log_info("Digest list reset - recently added cleared")


def fetch_metadata_by_tmdb_id(tmdb_id: int) -> Dict:
    """
    Fetch metadata directly by TMDB ID - much more reliable than title search.
    Returns dict with year, poster_url, runtime_str, overview, and ratings.
    """
    from . import config

    result = {
        "year": 0,
        "poster_url": "",
        "runtime_str": "",
        "overview": "",
        "rt_rating": 0,
        "imdb_rating": 0.0
    }

    if not tmdb_id:
        return result

    try:
        cfg = config.load_config()
        radarr_url = cfg.get('integrations', {}).get('radarr', {}).get('url', 'http://localhost:7878')
        radarr_key = cfg.get('integrations', {}).get('radarr', {}).get('api_key', '')

        # Radarr lookup by TMDB ID - returns exact match
        resp = requests.get(
            f"{radarr_url}/api/v3/movie/lookup/tmdb",
            params={"tmdbId": tmdb_id},
            headers={"X-Api-Key": radarr_key},
            timeout=10
        )

        if resp.status_code == 200:
            movie = resp.json()
            if movie:
                result["year"] = movie.get("year", 0)
                result["overview"] = movie.get("overview", "")

                # Get runtime
                runtime_min = movie.get("runtime", 0)
                if runtime_min:
                    hours = runtime_min // 60
                    mins = runtime_min % 60
                    result["runtime_str"] = f"{hours}h {mins}m" if hours else f"{mins}m"

                # Get ratings
                ratings = movie.get("ratings", {})
                if "rottenTomatoes" in ratings:
                    result["rt_rating"] = int(ratings["rottenTomatoes"].get("value", 0))
                if "imdb" in ratings:
                    result["imdb_rating"] = float(ratings["imdb"].get("value", 0))

                # Get poster URL
                images = movie.get("images", [])
                for img in images:
                    if img.get("coverType") == "poster":
                        remote_url = img.get("remoteUrl", "")
                        result["poster_url"] = remote_url.replace("/original/", "/w500/")
                        break

                log_info(f"Fetched metadata by TMDB ID {tmdb_id}: {movie.get('title')} ({result['year']})")

    except Exception as e:
        log_warning(f"Failed to fetch metadata for TMDB {tmdb_id}: {e}")

    return result


def fetch_metadata_from_radarr(title: str, year: int = None) -> Dict:
    """
    Fetch metadata (poster, tmdb_id, runtime) from Radarr for a given title.
    Returns dict with year, tmdb_id, poster_url, runtime_str or empty values if not found.
    NOTE: This uses title search which can be unreliable. Prefer fetch_metadata_by_tmdb_id when possible.
    """
    from . import config

    result = {
        "year": year or 0,
        "tmdb_id": 0,
        "poster_url": "",
        "runtime_str": ""
    }

    try:
        cfg = config.load_config()
        radarr_url = cfg.get('radarr', {}).get('url', 'http://localhost:7878')
        radarr_key = cfg.get('radarr', {}).get('api_key', '92112d5454e04d18943743270139c330')

        # Parse year from title if present (e.g., "Footloose (1984)")
        year_match = re.search(r'\((\d{4})\)$', title)
        if year_match:
            result["year"] = int(year_match.group(1))
            search_title = title[:year_match.start()].strip()
        else:
            search_title = title

        # Search Radarr lookup API
        search_url = f"{radarr_url}/api/v3/movie/lookup"
        search_params = {"term": f"{search_title} {result['year']}" if result["year"] else search_title}

        resp = requests.get(
            search_url,
            params=search_params,
            headers={"X-Api-Key": radarr_key},
            timeout=10
        )

        if resp.status_code == 200:
            movies = resp.json()
            if movies:
                # Find best match - prefer exact year match
                best_match = movies[0]
                if result["year"]:
                    for movie in movies:
                        if movie.get("year") == result["year"]:
                            best_match = movie
                            break

                result["year"] = best_match.get("year", result["year"])
                result["tmdb_id"] = best_match.get("tmdbId", 0)

                # Get runtime
                runtime_min = best_match.get("runtime", 0)
                if runtime_min:
                    hours = runtime_min // 60
                    mins = runtime_min % 60
                    result["runtime_str"] = f"{hours}h {mins}m" if hours else f"{mins}m"

                # Get poster URL
                images = best_match.get("images", [])
                for img in images:
                    if img.get("coverType") == "poster":
                        remote_url = img.get("remoteUrl", "")
                        # Convert to w500 size for consistency
                        result["poster_url"] = remote_url.replace("/original/", "/w500/")
                        break

                log_info(f"Enriched metadata for '{title}': TMDB={result['tmdb_id']}, Year={result['year']}")

    except Exception as e:
        log_warning(f"Failed to fetch metadata for '{title}': {e}")

    return result


def enrich_and_save_rip(
    title: str,
    disc_type: str = "unknown",
    duration_str: str = "",
    size_gb: float = 0,
    year: int = 0,
    tmdb_id: int = 0,
    poster_url: str = "",
    runtime_str: str = "",
    overview: str = "",
    rt_rating: int = 0,
    imdb_rating: float = 0.0,
    content_type: str = "movie",
    rip_method: str = "direct"
):
    """
    Save rip to history, enriching missing metadata from Radarr if needed.
    This should be called instead of save_rip_to_history for automatic enrichment.
    """
    log_info(f"enrich_and_save_rip called: {title} ({content_type})")

    try:
        # If we have TMDB ID but missing poster/runtime/overview, look up by ID (most reliable)
        if tmdb_id and (not poster_url or not runtime_str or not overview):
            metadata = fetch_metadata_by_tmdb_id(tmdb_id)
            if not year:
                year = metadata["year"]
            if not poster_url:
                poster_url = metadata["poster_url"]
            if not runtime_str:
                runtime_str = metadata["runtime_str"]
            if not overview:
                overview = metadata["overview"]
            if not rt_rating:
                rt_rating = metadata["rt_rating"]
            if not imdb_rating:
                imdb_rating = metadata["imdb_rating"]

        # If still missing metadata (no TMDB ID), fall back to title search (movies only)
        if content_type == "movie" and (not tmdb_id or not poster_url):
            metadata = fetch_metadata_from_radarr(title, year)
            if not year:
                year = metadata["year"]
            if not tmdb_id:
                tmdb_id = metadata["tmdb_id"]
            if not poster_url:
                poster_url = metadata["poster_url"]
            if not runtime_str:
                runtime_str = metadata["runtime_str"]
    except Exception as e:
        log_error(f"Error enriching metadata for {title}: {e}")

    # Save to history with enriched data
    save_rip_to_history(
        title=title,
        year=year,
        disc_type=disc_type.upper(),
        runtime_str=runtime_str,
        size_gb=size_gb,
        duration_str=duration_str,
        poster_url=poster_url,
        tmdb_id=tmdb_id,
        overview=overview,
        rt_rating=rt_rating,
        imdb_rating=imdb_rating,
        status="complete",
        content_type=content_type,
        rip_method=rip_method
    )


# ============== Disc Data Capture ==============
# Captures disc fingerprint data for building identification database

def capture_disc_data(
    disc_label: str,
    disc_type: str,
    tracks: list,
    track_sizes: dict,
    identified_title: str = None,
    year: int = None,
    tmdb_id: int = None,
    confidence: int = None,
    resolution_source: str = None,
    cinfo_raw: dict = None
):
    """
    Capture disc data for analysis and future identification database.
    Appends to JSONL file for easy processing.

    resolution_source: Where the identification came from (radarr, sonarr, manual, fallback, etc.)
    """
    # Build fingerprint from disc characteristics
    track_durations = [t.get("duration", 0) for t in tracks]
    main_duration = max(track_durations) if track_durations else 0

    capture = {
        "timestamp": datetime.now().isoformat(),
        "disc_label": disc_label,
        "disc_type": disc_type,
        "track_count": len(tracks),
        "main_duration_secs": main_duration,
        "track_durations": track_durations,
        "track_sizes": {str(k): v for k, v in track_sizes.items()},  # JSON needs string keys
        "total_size_bytes": sum(track_sizes.values()) if track_sizes else 0,
        # Identification result (if available)
        "identified_title": identified_title,
        "year": year,
        "tmdb_id": tmdb_id,
        "confidence": confidence,
        "resolution_source": resolution_source,
        # Raw CINFO for future analysis
        "cinfo_raw": cinfo_raw or {}
    }

    try:
        with open(DISC_CAPTURES_FILE, "a") as f:
            f.write(json.dumps(capture) + "\n")
        log_info(f"Disc data captured: {disc_label} -> {identified_title or 'unidentified'}")
    except Exception as e:
        log_warning(f"Failed to capture disc data: {e}")


def clear_activity_log():
    """Clear the activity log file"""
    if ACTIVITY_LOG.exists():
        ACTIVITY_LOG.unlink()
    log_info("Activity log cleared")


def get_rip_errors() -> list:
    """Get all rip errors from activity log with details"""
    errors = []
    error_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| ERROR \| Rip failed: (.+)$')

    try:
        if ACTIVITY_LOG.exists():
            with open(ACTIVITY_LOG) as f:
                for line in f:
                    match = error_pattern.match(line.strip())
                    if match:
                        errors.append({
                            'timestamp': match.group(1),
                            'message': match.group(2)
                        })
    except Exception:
        pass

    return errors[::-1]  # Newest first
