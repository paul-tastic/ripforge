"""
RipForge Activity Logger
Logs user-facing events to activity log file
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).parent.parent / "logs"
ACTIVITY_LOG = LOG_DIR / "activity.log"
HISTORY_FILE = LOG_DIR / "rip_history.json"

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
    log_info(f"Rip started: {title} ({mode})")


def rip_progress(title: str, percent: int):
    # Only log at 25%, 50%, 75% milestones to avoid spam
    if percent in [25, 50, 75]:
        log_info(f"Rip progress: {title} - {percent}%")


def rip_identified(original: str, identified: str, confidence: int):
    log_success(f"Identified: {original} -> {identified} ({confidence}% confidence)")


def id_method_result(method: str, result: str, confidence: int, details: str = None):
    """Log an identification method's guess"""
    msg = f"ID Method: {method} -> \"{result}\" ({confidence}%)"
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


def rip_cancelled(title: str):
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
    status: str = "complete"
):
    """Save completed rip to history for weekly digest"""
    history = load_rip_history()

    entry = {
        "title": title,
        "year": year,
        "disc_type": disc_type,
        "runtime": runtime_str,
        "size_gb": round(size_gb, 1),
        "rip_duration": duration_str,
        "poster_url": poster_url,
        "tmdb_id": tmdb_id,
        "status": status,
        "completed_at": datetime.now().isoformat()
    }

    history.append(entry)

    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error saving rip history: {e}")


def load_rip_history() -> list:
    """Load rip history from file"""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading rip history: {e}")
    return []


def get_recent_rips(days: int = 7) -> list:
    """Get rips from the last N days"""
    from datetime import timedelta

    history = load_rip_history()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    return [rip for rip in history if rip.get('completed_at', '') >= cutoff]
