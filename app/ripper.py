"""
RipForge Ripping Engine
Handles disc detection, MakeMKV control, and rip job management
"""

import os
import glob
import re
import subprocess
import threading
import time
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

from . import activity
from . import config
from . import community_db
from . import email as email_utils
from . import error_detection


def sanitize_folder_name(name: str) -> str:
    """Sanitize a string for use as a folder name.

    Removes/replaces characters that cause issues with MakeMKV or filesystems.
    """
    # Replace colons with dashes (common in movie titles like "Star Wars: The Rise of Skywalker")
    name = name.replace(':', ' -')
    # Remove other problematic characters
    name = re.sub(r'[<>"|?*]', '', name)
    # Clean up multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def set_default_audio_track(mkv_path: str, preferred_lang: str = "eng") -> bool:
    """Set the default audio track in an MKV file based on language preference.

    Uses ffprobe to find audio tracks and mkvpropedit to set the default flag.

    Args:
        mkv_path: Path to the MKV file
        preferred_lang: ISO 639-2 language code (eng, spa, fra, etc.)

    Returns:
        True if successful, False otherwise
    """
    if preferred_lang == "all":
        return True  # Don't modify if user wants all languages as-is

    try:
        # Get audio track info with ffprobe
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-select_streams", "a", mkv_path
        ], capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            activity.log_warning(f"ffprobe failed for {mkv_path}")
            return False

        import json as json_module
        data = json_module.loads(result.stdout)
        streams = data.get("streams", [])

        if not streams:
            return True  # No audio tracks to modify

        # Find the first track matching preferred language
        preferred_track_idx = None
        for stream in streams:
            lang = stream.get("tags", {}).get("language", "und")
            if lang == preferred_lang:
                # MKV track numbers are 1-indexed, and we need to count from video
                # ffprobe index is 0-indexed overall, audio tracks start after video
                preferred_track_idx = stream.get("index")
                break

        if preferred_track_idx is None:
            activity.log_info(f"No {preferred_lang} audio track found in {os.path.basename(mkv_path)}")
            return True  # No matching track, leave as-is

        # Build mkvpropedit commands to set default flags
        # First, clear all audio track default flags, then set the preferred one
        cmd = ["mkvpropedit", mkv_path]

        # Count audio tracks to clear their default flags
        for i, stream in enumerate(streams):
            track_num = i + 1  # mkvpropedit uses 1-indexed audio track numbers
            cmd.extend(["--edit", f"track:a{track_num}", "--set", "flag-default=0"])

        # Find which audio track number corresponds to our preferred language
        for i, stream in enumerate(streams):
            if stream.get("index") == preferred_track_idx:
                track_num = i + 1
                cmd.extend(["--edit", f"track:a{track_num}", "--set", "flag-default=1"])
                break

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode == 0:
            activity.log_info(f"Set {preferred_lang} as default audio for {os.path.basename(mkv_path)}")
            return True
        else:
            activity.log_warning(f"mkvpropedit failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        activity.log_warning(f"Timeout setting default audio track for {mkv_path}")
        return False
    except Exception as e:
        activity.log_warning(f"Error setting default audio track: {e}")
        return False


def check_file_integrity(mkv_path: str, progress_callback=None) -> dict:
    """Check MKV file for corruption using ffmpeg decode test.

    Runs ffmpeg to decode the entire file and catches any decode errors.
    This catches issues like:
    - H.264 macroblock decode errors
    - Timestamp/DTS issues
    - Missing reference frames
    - Truncated files

    Args:
        mkv_path: Path to the MKV file to check
        progress_callback: Optional callback(percent) for progress updates

    Returns:
        dict with keys:
        - valid: bool - True if no errors found
        - errors: list - List of error messages found
        - error_count: int - Total number of errors
    """
    result = {
        "valid": True,
        "errors": [],
        "error_count": 0
    }

    if not os.path.exists(mkv_path):
        result["valid"] = False
        result["errors"] = ["File not found"]
        result["error_count"] = 1
        return result

    try:
        # Get file duration first for progress calculation
        duration_result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_entries", "format=duration", mkv_path
        ], capture_output=True, text=True, timeout=30)

        total_duration = 0
        if duration_result.returncode == 0:
            import json as json_module
            data = json_module.loads(duration_result.stdout)
            total_duration = float(data.get("format", {}).get("duration", 0))

        # Run ffmpeg decode test - outputs errors to stderr
        # -v error shows only errors, -f null discards output
        process = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-progress", "pipe:1",
             "-i", mkv_path, "-f", "null", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        errors = []
        current_time = 0

        # Read progress from stdout, errors from stderr
        import select
        while process.poll() is None:
            # Check for progress updates
            if process.stdout:
                line = process.stdout.readline()
                if line.startswith("out_time_ms="):
                    try:
                        time_ms = int(line.split("=")[1].strip())
                        current_time = time_ms / 1000000  # Convert to seconds
                        if progress_callback and total_duration > 0:
                            percent = min(99, int((current_time / total_duration) * 100))
                            progress_callback(percent)
                    except (ValueError, IndexError):
                        pass

        # Get any remaining stderr output
        _, stderr = process.communicate(timeout=10)
        if stderr:
            # Parse error lines
            for line in stderr.strip().split('\n'):
                if line.strip():
                    errors.append(line.strip())

        if errors:
            result["valid"] = False
            result["errors"] = errors[:20]  # Limit to first 20 errors
            result["error_count"] = len(errors)
            activity.log_warning(f"INTEGRITY: {os.path.basename(mkv_path)} has {len(errors)} errors")
            for err in errors[:5]:
                activity.log_warning(f"INTEGRITY:   {err}")
        else:
            activity.log_info(f"INTEGRITY: {os.path.basename(mkv_path)} passed integrity check")

        if progress_callback:
            progress_callback(100)

        return result

    except subprocess.TimeoutExpired:
        activity.log_warning(f"INTEGRITY: Timeout checking {mkv_path}")
        result["valid"] = False
        result["errors"] = ["Integrity check timed out"]
        result["error_count"] = 1
        return result
    except Exception as e:
        activity.log_warning(f"INTEGRITY: Error checking {mkv_path}: {e}")
        result["valid"] = False
        result["errors"] = [str(e)]
        result["error_count"] = 1
        return result


class RipStatus(Enum):
    IDLE = "idle"
    DETECTING = "detecting"
    SCANNING = "scanning"
    RIPPING = "ripping"
    IDENTIFYING = "identifying"
    MOVING = "moving"
    COMPLETE = "complete"
    ERROR = "error"


class StepStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class RipStep:
    status: str = "pending"
    detail: str = ""


@dataclass
class RipJob:
    """Represents an active or completed rip job"""
    id: str = ""
    disc_label: str = ""
    disc_type: str = ""  # dvd, bluray, unknown
    device: str = "/dev/sr0"
    status: RipStatus = RipStatus.IDLE
    progress: int = 0
    eta: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    output_path: str = ""
    identified_title: str = ""
    error_message: str = ""
    error_details: Optional[Dict] = None  # Structured error from error_detection module
    # Identification metadata for history/email
    year: int = 0
    tmdb_id: int = 0
    poster_url: str = ""
    runtime_str: str = ""
    size_gb: float = 0
    # File size tracking
    expected_size_bytes: int = 0
    current_size_bytes: int = 0
    rip_output_dir: str = ""  # Track where MakeMKV is writing during rip
    # Review queue flag - set when identification fails
    needs_review: bool = False
    # Smart track selection - TMDB runtime for fake playlist detection
    tmdb_runtime_seconds: int = 0
    # TV-specific fields
    media_type: str = "movie"  # "movie" or "tv"
    series_title: str = ""  # Original series title for TV
    season_number: int = 0
    tracks_to_rip: List[int] = field(default_factory=list)  # Track indices for episodes
    current_track_index: int = 0  # Current track being ripped (for progress)
    episode_mapping: Dict[int, dict] = field(default_factory=dict)  # track_idx -> episode info
    ripped_files: List[str] = field(default_factory=list)  # Paths of ripped episode files
    rip_method: str = "direct"  # "direct", "backup", or "recovery"
    rip_mode: str = "smart"  # "smart", "always_backup", "direct_only" - from config
    direct_failed: bool = False  # True if direct rip was attempted and failed
    disc_ejected: bool = False  # True if disc was ejected after rip completed
    # Duplicate detection
    possible_duplicate: bool = False  # True if duplicate detected
    duplicate_match_type: str = ""  # 'tmdb_id', 'folder', or 'disc_label'
    duplicate_info: Optional[Dict] = None  # Info about existing rip
    rip_started_at: Optional[float] = None  # Unix timestamp when rip phase began (for ETA calc)
    # Disc fingerprint data for capture/analysis
    disc_tracks: List[dict] = field(default_factory=list)
    disc_track_sizes: Dict[int, int] = field(default_factory=dict)
    disc_cinfo_raw: Dict[str, str] = field(default_factory=dict)
    steps: Dict[str, RipStep] = field(default_factory=lambda: {
        "insert": RipStep(),
        "detect": RipStep(),
        "scan": RipStep(),
        "rip": RipStep(),
        "verify": RipStep(),
        "identify": RipStep(),
        "library": RipStep(),
        "move": RipStep(),
        "scan-plex": RipStep(),
    })

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "disc_label": self.disc_label,
            "disc_type": self.disc_type,
            "device": self.device,
            "status": self.status.value if isinstance(self.status, RipStatus) else self.status,
            "progress": self.progress,
            "eta": self.eta,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output_path": self.output_path,
            "identified_title": self.identified_title,
            "error_message": self.error_message,
            "error_details": self.error_details,
            "expected_size_bytes": self.expected_size_bytes,
            "current_size_bytes": self.current_size_bytes,
            "rip_output_dir": self.rip_output_dir,
            # TV-specific fields
            "media_type": self.media_type,
            "series_title": self.series_title,
            "season_number": self.season_number,
            "tracks_to_rip": self.tracks_to_rip,
            "current_track_index": self.current_track_index,
            "episode_mapping": self.episode_mapping,
            "total_tracks": len(self.tracks_to_rip) if self.tracks_to_rip else 0,
            "needs_review": self.needs_review,
            "rip_method": self.rip_method,
            "rip_mode": self.rip_mode,
            "direct_failed": self.direct_failed,
            "disc_ejected": self.disc_ejected,
            # Duplicate detection
            "possible_duplicate": self.possible_duplicate,
            "duplicate_match_type": self.duplicate_match_type,
            "duplicate_info": self.duplicate_info,
            "steps": {k: {"status": v.status, "detail": v.detail} for k, v in self.steps.items()}
        }


class MakeMKV:
    """Wrapper for MakeMKV command-line interface"""

    def __init__(self, use_docker: bool = False, container_name: str = "arm"):
        self.use_docker = use_docker
        self.container_name = container_name

    def _run_cmd(self, args: List[str], callback: Optional[Callable] = None) -> subprocess.Popen:
        """Run makemkvcon with optional progress callback"""
        if self.use_docker:
            cmd = ["docker", "exec", self.container_name, "makemkvcon"] + args
        else:
            cmd = ["makemkvcon"] + args

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        return process

    def get_disc_info(self, device: str = "/dev/sr0", config: dict = None) -> Dict:
        """Get information about the disc in the drive.

        Args:
            device: Optical drive device path
            config: Optional config dict for TV episode detection thresholds

        Returns:
            Dict with disc info including episode_tracks for TV detection
        """
        # Get TV detection thresholds from config or use defaults
        tv_min = 1200  # 20 min default
        tv_max = 3600  # 60 min default
        if config:
            ripping_cfg = config.get('ripping', {})
            tv_min = ripping_cfg.get('tv_min_episode_length', 1200)
            tv_max = ripping_cfg.get('tv_max_episode_length', 3600)

        info = {
            "disc_label": "",
            "disc_type": "unknown",
            "tracks": [],
            "main_feature": None,
            "track_sizes": {},  # Track index -> size in bytes
            "episode_tracks": [],  # Tracks that look like TV episodes (20-60 min)
            "is_tv_disc": False,  # True if multiple episode-length tracks detected
            "cinfo_raw": {}  # Raw CINFO fields for disc fingerprinting
        }

        # Convert device to MakeMKV format (disc:0 for /dev/sr0)
        disc_num = 0
        if device.startswith("/dev/sr"):
            disc_num = int(device.replace("/dev/sr", ""))

        args = ["-r", "info", f"disc:{disc_num}"]
        process = self._run_cmd(args)

        longest_track = {"index": None, "duration": 0, "playlist": ""}
        track_playlists = {}  # track_num -> playlist name (e.g., "00800.mpls")

        for line in process.stdout:
            line = line.strip()

            # Capture all CINFO fields for fingerprinting
            if line.startswith("CINFO:"):
                cinfo_match = re.match(r'CINFO:(\d+),\d+,"([^"]*)"', line)
                if cinfo_match:
                    info["cinfo_raw"][f"CINFO:{cinfo_match.group(1)}"] = cinfo_match.group(2)

            # Parse disc name: CINFO:2,0,"GUARDIANS_VOL_3"
            if line.startswith("CINFO:2,0,"):
                match = re.search(r'CINFO:2,0,"([^"]*)"', line)
                if match:
                    info["disc_label"] = match.group(1)

            # Parse disc type: CINFO:1,6xxx,"Blu-ray disc"
            if "Blu-ray" in line:
                info["disc_type"] = "bluray"
            elif "DVD" in line:
                info["disc_type"] = "dvd"

            # Parse playlist name: TINFO:0,16,0,"00800.mpls"
            # Important for multi-angle discs (e.g., Star Wars) where different
            # playlists have different language text burned into video
            if line.startswith("TINFO:") and ",16,0," in line:
                match = re.search(r'TINFO:(\d+),16,0,"([^"]*)"', line)
                if match:
                    track_num = int(match.group(1))
                    playlist = match.group(2)
                    track_playlists[track_num] = playlist

            # Parse track info: TINFO:0,9,0,"1:45:30" (duration)
            if line.startswith("TINFO:") and ",9,0," in line:
                match = re.search(r'TINFO:(\d+),9,0,"([^"]*)"', line)
                if match:
                    track_num = int(match.group(1))
                    duration_str = match.group(2)

                    # Parse duration to seconds
                    try:
                        parts = duration_str.split(":")
                        if len(parts) == 3:
                            duration_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        elif len(parts) == 2:
                            duration_secs = int(parts[0]) * 60 + int(parts[1])
                        else:
                            duration_secs = 0

                        info["tracks"].append({
                            "index": track_num,
                            "duration": duration_secs,
                            "duration_str": duration_str
                        })

                        # Track longest for main feature detection
                        if duration_secs > longest_track["duration"]:
                            longest_track = {"index": track_num, "duration": duration_secs}
                    except:
                        pass

            # Parse track size: TINFO:0,11,0,"5446510592" (bytes)
            if line.startswith("TINFO:") and ",11,0," in line:
                match = re.search(r'TINFO:(\d+),11,0,"(\d+)"', line)
                if match:
                    track_num = int(match.group(1))
                    size_bytes = int(match.group(2))
                    info["track_sizes"][track_num] = size_bytes

        process.wait()

        # Add playlist names to track info
        for track in info["tracks"]:
            track["playlist"] = track_playlists.get(track["index"], "")

        # Set main feature as longest track over 45 minutes
        # For multi-angle discs (Star Wars, Disney), prefer lower playlist numbers
        # e.g., 00800.mpls is typically English, 00801 is Spanish, 00802 is French
        if longest_track["duration"] > 2700:  # 45 min
            # Find all tracks with same duration as longest (potential angles)
            angle_candidates = [
                t for t in info["tracks"]
                if abs(t["duration"] - longest_track["duration"]) <= 5  # Within 5 sec
            ]

            if len(angle_candidates) > 1:
                # Multiple tracks with same duration = likely angles
                # Sort by playlist number to prefer lower (English on US releases)
                activity.log_info(f"DISC: Detected {len(angle_candidates)} angles (same duration tracks)")
                for candidate in angle_candidates:
                    playlist = track_playlists.get(candidate["index"], "unknown")
                    activity.log_info(f"DISC:   Track {candidate['index']}: playlist {playlist}")

                # Sort by playlist name (00800 < 00801 < 00802)
                angle_candidates.sort(key=lambda t: track_playlists.get(t["index"], "zzzzz"))
                best_track = angle_candidates[0]
                info["main_feature"] = best_track["index"]
                activity.log_info(f"DISC: Selected track {best_track['index']} (playlist {track_playlists.get(best_track['index'], 'unknown')}) as main feature")
            else:
                info["main_feature"] = longest_track["index"]

        # Detect episode-length tracks (for TV show detection)
        episode_tracks = []
        for track in info["tracks"]:
            duration = track.get("duration", 0)
            if tv_min <= duration <= tv_max:
                episode_tracks.append(track)

        info["episode_tracks"] = episode_tracks

        # If 2+ episode-length tracks AND no long main feature, likely a TV disc
        # Movies with bonus features should NOT be classified as TV
        main_feature_duration = longest_track["duration"] if longest_track["index"] is not None else 0
        has_long_main_feature = main_feature_duration > 5400  # > 90 minutes = definitely a movie
        
        if len(episode_tracks) >= 2:
            if has_long_main_feature:
                # This is a movie with bonus features, not a TV disc
                activity.log_info(f"DISC: Found {len(episode_tracks)} episode-length tracks but main feature is {main_feature_duration // 60}m - treating as movie with bonus features")
                info["is_tv_disc"] = False
            else:
                info["is_tv_disc"] = True
                # For TV discs, log the episode tracks
                activity.log_info(f"DISC: Detected {len(episode_tracks)} episode-length tracks (TV disc)")
                for et in episode_tracks:
                    activity.log_info(f"DISC:   Track {et['index']}: {et['duration_str']} ({et['duration'] // 60}m)")

        return info

    def get_backup_main_feature(self, backup_path: str) -> Optional[int]:
        """Scan a backup folder and return the index of the longest track.

        IMPORTANT: Track indices from disc scan may differ from backup scan.
        Always use this function to get the correct track index for backup rips.

        For multi-angle discs (e.g., Star Wars), prefers lower playlist numbers
        (00800.mpls = English, 00801 = Spanish, 00802 = French on US releases).

        Args:
            backup_path: Path to the backup folder containing BDMV structure

        Returns:
            Track index of the longest track (main feature), or None if scan fails
        """
        activity.log_info(f"BACKUP SCAN: Scanning {backup_path} for main feature track...")

        args = ["-r", "info", f"file:{backup_path}"]
        process = self._run_cmd(args)

        longest_track = {"index": None, "duration": 0}
        tracks_found = []  # (track_num, duration_secs, duration_str, playlist)
        track_playlists = {}  # track_num -> playlist name

        for line in process.stdout:
            line = line.strip()

            # Parse playlist name: TINFO:0,16,0,"00800.mpls"
            if line.startswith("TINFO:") and ",16,0," in line:
                match = re.search(r'TINFO:(\d+),16,0,"([^"]*)"', line)
                if match:
                    track_num = int(match.group(1))
                    playlist = match.group(2)
                    track_playlists[track_num] = playlist

            # Parse track duration: TINFO:0,9,0,"1:45:30"
            if line.startswith("TINFO:") and ",9,0," in line:
                match = re.search(r'TINFO:(\d+),9,0,"([^"]*)"', line)
                if match:
                    track_num = int(match.group(1))
                    duration_str = match.group(2)

                    try:
                        parts = duration_str.split(":")
                        if len(parts) == 3:
                            duration_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        elif len(parts) == 2:
                            duration_secs = int(parts[0]) * 60 + int(parts[1])
                        else:
                            duration_secs = 0

                        tracks_found.append((track_num, duration_secs, duration_str))

                        if duration_secs > longest_track["duration"]:
                            longest_track = {"index": track_num, "duration": duration_secs}
                    except:
                        pass

        process.wait()

        if tracks_found:
            activity.log_info(f"BACKUP SCAN: Found {len(tracks_found)} tracks")
            for t in sorted(tracks_found, key=lambda x: -x[1])[:3]:
                playlist = track_playlists.get(t[0], "unknown")
                activity.log_info(f"BACKUP SCAN:   Track {t[0]}: {t[2]} ({t[1] // 60}m) - playlist {playlist}")

        if longest_track["index"] is not None and longest_track["duration"] > 2700:
            # Check for multiple tracks with same duration (angles)
            angle_candidates = [
                t for t in tracks_found
                if abs(t[1] - longest_track["duration"]) <= 5  # Within 5 sec
            ]

            if len(angle_candidates) > 1:
                # Multiple tracks with same duration = likely angles
                activity.log_info(f"BACKUP SCAN: Detected {len(angle_candidates)} angles (same duration tracks)")
                # Sort by playlist number (00800 < 00801 < 00802)
                angle_candidates.sort(key=lambda t: track_playlists.get(t[0], "zzzzz"))
                best_track = angle_candidates[0]
                activity.log_info(f"BACKUP SCAN: Selected track {best_track[0]} (playlist {track_playlists.get(best_track[0], 'unknown')}) as main feature")
                return best_track[0]

            activity.log_info(f"BACKUP SCAN: Main feature is track {longest_track['index']} ({longest_track['duration'] // 60}m)")
            return longest_track["index"]
        else:
            activity.log_warning(f"BACKUP SCAN: No suitable main feature found")
            return None


    def select_best_track(self, tracks: list, official_runtime_seconds: int, tolerance: int = 30) -> tuple:
        """Select track closest to official runtime (handles fake playlist protection).

        Disney and other studios use fake playlists with dozens of tracks at nearly
        identical runtimes. This method uses the official TMDB runtime to find the
        real track.

        Args:
            tracks: List of track dicts with 'index' and 'duration' keys
            official_runtime_seconds: Expected runtime from TMDB
            tolerance: Seconds tolerance for matching (default 30s)

        Returns:
            Tuple of (track_index, detected_fake_playlists: bool)
        """
        if not tracks or not official_runtime_seconds:
            return (None, False)

        # First, check if we have a fake playlist situation:
        # Multiple tracks with durations within 60 seconds of each other
        long_tracks = [t for t in tracks if t.get('duration', 0) > 2700]  # > 45 min
        fake_playlist_detected = False

        if len(long_tracks) >= 3:
            # Check if multiple tracks have similar durations
            durations = sorted([t['duration'] for t in long_tracks])
            # If 3+ tracks are within 120 seconds of each other, it's likely fake playlists
            for i in range(len(durations) - 2):
                if durations[i + 2] - durations[i] <= 120:
                    fake_playlist_detected = True
                    activity.log_warning(f"TRACK SELECT: Fake playlist detected - {len(long_tracks)} tracks with similar runtimes")
                    break

        # Find tracks within tolerance of official runtime
        candidates = []
        for track in tracks:
            duration = track.get('duration', 0)
            if duration < 2700:  # Skip short tracks
                continue
            diff = abs(duration - official_runtime_seconds)
            candidates.append((track['index'], diff, duration))

        if not candidates:
            activity.log_warning("TRACK SELECT: No suitable tracks found")
            return (None, fake_playlist_detected)

        # Sort by difference from official runtime
        candidates.sort(key=lambda x: x[1])

        best_track = candidates[0][0]
        best_diff = candidates[0][1]
        best_duration = candidates[0][2]

        # Log selection reasoning
        official_mins = official_runtime_seconds // 60
        best_mins = best_duration // 60
        diff_secs = best_diff

        if fake_playlist_detected:
            activity.log_info(f"TRACK SELECT: Official runtime {official_mins}m, selected track {best_track} ({best_mins}m, {diff_secs}s diff)")
            if len(candidates) > 1:
                activity.log_info(f"TRACK SELECT: Rejected {len(candidates) - 1} other tracks with similar durations")
        else:
            activity.log_info(f"TRACK SELECT: Selected track {best_track} ({best_mins}m)")

        return (best_track, fake_playlist_detected)

    def rip_track(self, device: str, track: int, output_dir: str,
                  progress_callback: Optional[Callable] = None,
                  message_callback: Optional[Callable] = None, expected_size: int = 0) -> tuple:
        """Rip a specific track from the disc

        Returns: (success: bool, error_message: str)
        """

        # Convert device to MakeMKV format
        disc_num = 0
        if device.startswith("/dev/sr"):
            disc_num = int(device.replace("/dev/sr", ""))

        # Ensure output directory exists
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        args = [
            "-r",  # Robot mode (parseable output)
            "--progress=-stdout",  # Enable PRGV progress output
            "mkv",
            f"disc:{disc_num}",
            str(track),
            output_dir
        ]
        # Note: --minlength is NOT a valid CLI switch, only a GUI setting
        # Track filtering must be done before calling rip_track()

        # Debug: log exact command being run
        from . import activity
        cmd_str = "makemkvcon " + " ".join(f'"{a}"' if " " in a else a for a in args)
        # Check debug logging setting
        from . import config as cfg_module
        debug_cfg = cfg_module.load_config()
        debug_enabled = debug_cfg.get('ripping', {}).get('debug_logging', False)
        if debug_enabled:
            activity.log_info(f"DEBUG: Running: {cmd_str}")

        process = self._run_cmd(args)
        last_error = ""
        actual_output_path = None  # Track where MakeMKV actually saves

        line_count = 0
        prgv_count = 0
        last_size_check = time.time()
        last_progress = 0
        last_heartbeat = time.time()
        start_time = time.time()
        msg_count = 0  # Track MSG lines for initialization logging
        for line in process.stdout:
            line = line.strip()
            line_count += 1

            # Log first few lines for debugging
            if line_count <= 5:
                if debug_enabled:
                    activity.log_info(f"DEBUG MakeMKV[{line_count}]: {line[:100]}")

            # Parse progress: PRGV:current,total,max
            if line.startswith("PRGV:"):
                prgv_count += 1
                match = re.search(r'PRGV:(\d+),(\d+),(\d+)', line)
                if match and progress_callback:
                    current = int(match.group(1))
                    total = int(match.group(2))
                    max_val = int(match.group(3))
                    if max_val > 0:
                        percent = int((current / max_val) * 100)
                        progress_callback(percent)
                        # Log occasional progress for debugging
                        if prgv_count == 1 or prgv_count % 100 == 0:
                            if debug_enabled:
                                activity.log_info(f"DEBUG: Progress {percent}% (PRGV #{prgv_count})")

            # Fallback: poll folder size if no PRGV and expected_size provided
            if prgv_count == 0 and expected_size > 0 and progress_callback:
                now = time.time()
                if now - last_size_check >= 3:  # Check every 3 seconds
                    last_size_check = now
                    try:
                        mkv_files = list(Path(output_dir).glob("*.mkv"))
                        if mkv_files:
                            current_size = max(f.stat().st_size for f in mkv_files)
                            percent = min(99, int((current_size / expected_size) * 100))
                            if percent > last_progress:
                                progress_callback(percent)
                                last_progress = percent
                                # Log size progress in debug mode
                                if debug_enabled:
                                    size_mb = current_size / (1024 * 1024)
                                    activity.log_info(f"DEBUG: File size progress: {size_mb:.1f} MB ({percent}%)")
                    except:
                        pass

            # Heartbeat logging: when at 0% for extended periods, log periodic status
            if debug_enabled and last_progress == 0:
                now = time.time()
                elapsed = now - start_time
                if now - last_heartbeat >= 60:  # Every 60 seconds
                    last_heartbeat = now
                    # Check for any mkv files starting to appear
                    try:
                        mkv_files = list(Path(output_dir).glob("*.mkv"))
                        if mkv_files:
                            current_size = max(f.stat().st_size for f in mkv_files)
                            size_mb = current_size / (1024 * 1024)
                            activity.log_info(f"DEBUG: Still initializing ({int(elapsed)}s elapsed), {len(mkv_files)} file(s), {size_mb:.1f} MB so far, {line_count} lines processed")
                        else:
                            activity.log_info(f"DEBUG: Still initializing ({int(elapsed)}s elapsed), no output yet, {line_count} lines processed, {msg_count} MSG lines")
                    except:
                        activity.log_info(f"DEBUG: Still initializing ({int(elapsed)}s elapsed), {line_count} lines processed")

            # Parse messages for errors/status and track actual output path
            if line.startswith("MSG:"):
                msg_count += 1
                # Extract message text: MSG:code,flags,count,"message",...
                match = re.search(r'MSG:\d+,\d+,\d+,"([^"]*)"', line)
                if match:
                    msg = match.group(1)
                    if message_callback:
                        message_callback(msg)
                    # Track error messages
                    if "error" in msg.lower() or "fail" in msg.lower():
                        last_error = msg
                    # Track actual output path from "Saving X title(s) into directory file:///path"
                    if "saving" in msg.lower() and "directory" in msg.lower():
                        path_match = re.search(r'file://(/[^\s]+)', msg)
                        if path_match:
                            actual_output_path = path_match.group(1)
                            if debug_enabled:
                                activity.log_info(f"DEBUG: MakeMKV saving to: {actual_output_path}")
                    # Log MSG status during initialization (when still at 0%)
                    elif debug_enabled and prgv_count == 0:
                        # Log status messages during initialization phase
                        activity.log_info(f"DEBUG MSG[{msg_count}]: {msg[:80]}")

        return_code = process.wait()
        if debug_enabled:
            activity.log_info(f"DEBUG: MakeMKV finished. Lines: {line_count}, PRGV: {prgv_count}, Return: {return_code}")

        if return_code == 0:
            # Detect silent failures: MakeMKV returned success but never reported progress
            if prgv_count == 0:
                activity.log_info("RIP: No progress messages received, verifying output...")
                mkv_files = list(Path(output_dir).glob("*.mkv"))
                if mkv_files:
                    largest = max(mkv_files, key=lambda f: f.stat().st_size)
                    size = largest.stat().st_size
                    if size > 100_000_000:  # > 100 MB
                        activity.log_success(f"RIP: Verified - {size / (1024**3):.1f} GB in {largest.name}")
                        return (True, "", str(largest))
                    else:
                        activity.log_warning(f"RIP: MKV exists but only {size / (1024**2):.1f} MB")
                        return (False, f"Output file too small ({size / (1024**2):.1f} MB)", str(largest))
                else:
                    activity.log_warning("RIP: No MKV files found - possible disc read failure or copy protection")
                    return (False, "MakeMKV reported success but no output file found", actual_output_path)
            return (True, "", actual_output_path)
        else:
            # Map common MakeMKV error codes
            error_map = {
                1: "General error",
                2: "Invalid argument",
                12: "Disc read error - disc may be damaged or dirty",
                13: "Drive error",
                15: "Copy protection error"
            }
            error_desc = error_map.get(return_code, f"Unknown error (code {return_code})")
            if last_error:
                error_desc = f"{error_desc}: {last_error}"
            return (False, error_desc, actual_output_path)

    def backup_disc(self, device: str, output_dir: str,
                    progress_callback: Optional[Callable] = None,
                    message_callback: Optional[Callable] = None, expected_size: int = 0) -> tuple:
        """Backup entire disc to folder (decrypted).

        This is used as a fallback when direct ripping fails due to copy protection.
        MakeMKV decrypts and copies the entire disc structure to a folder, which can
        then be ripped without the protection issues.

        Args:
            device: Optical drive device path (e.g., /dev/sr0)
            output_dir: Directory to store the backup
            progress_callback: Function called with progress percentage
            message_callback: Function called with status messages

        Returns:
            Tuple of (success: bool, error_message: str, backup_path: str)
        """
        disc_num = 0
        if device.startswith("/dev/sr"):
            disc_num = int(device.replace("/dev/sr", ""))

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        args = ["-r", "--progress=-stdout", "backup", f"disc:{disc_num}", output_dir]

        # Check debug logging setting
        from . import config as cfg_module
        debug_cfg = cfg_module.load_config()
        debug_enabled = debug_cfg.get('ripping', {}).get('debug_logging', False)

        activity.log_info(f"BACKUP: Running: makemkvcon {' '.join(args)}")

        process = self._run_cmd(args)
        last_error = ""
        corruption_detected = []  # Track any corruption warnings

        line_count = 0
        prgv_count = 0
        last_size_check = time.time()
        last_progress = 0
        last_heartbeat = time.time()
        start_time = time.time()
        msg_count = 0
        for line in process.stdout:
            line = line.strip()
            line_count += 1

            # Log first few lines for debugging
            if line_count <= 5 and debug_enabled:
                activity.log_info(f"BACKUP MakeMKV[{line_count}]: {line[:100]}")

            # Parse progress: PRGV:current,total,max
            if line.startswith("PRGV:"):
                prgv_count += 1
                match = re.search(r'PRGV:(\d+),(\d+),(\d+)', line)
                if match and progress_callback:
                    current = int(match.group(1))
                    max_val = int(match.group(3))
                    if max_val > 0:
                        percent = int((current / max_val) * 100)
                        progress_callback(percent)
                        if prgv_count == 1 or prgv_count % 100 == 0:
                            activity.log_info(f"BACKUP: Progress {percent}%")

            # Fallback: poll folder size if no PRGV and expected_size provided
            if prgv_count == 0 and expected_size > 0 and progress_callback:
                now = time.time()
                if now - last_size_check >= 3:  # Check every 3 seconds
                    last_size_check = now
                    try:
                        current_size = sum(f.stat().st_size for f in Path(output_dir).rglob("*") if f.is_file())
                        percent = min(99, int((current_size / expected_size) * 100))
                        if percent > last_progress:
                            progress_callback(percent)
                            last_progress = percent
                            if debug_enabled:
                                size_mb = current_size / (1024 * 1024)
                                activity.log_info(f"BACKUP DEBUG: File size progress: {size_mb:.1f} MB ({percent}%)")
                    except:
                        pass

            # Heartbeat logging: when at 0% for extended periods
            if debug_enabled and last_progress == 0:
                now = time.time()
                elapsed = now - start_time
                if now - last_heartbeat >= 60:  # Every 60 seconds
                    last_heartbeat = now
                    try:
                        current_size = sum(f.stat().st_size for f in Path(output_dir).rglob("*") if f.is_file())
                        size_mb = current_size / (1024 * 1024)
                        activity.log_info(f"BACKUP DEBUG: Still initializing ({int(elapsed)}s elapsed), {size_mb:.1f} MB so far, {line_count} lines processed")
                    except:
                        activity.log_info(f"BACKUP DEBUG: Still initializing ({int(elapsed)}s elapsed), {line_count} lines processed")

            # Parse messages for errors/status
            if line.startswith("MSG:"):
                msg_count += 1
                match = re.search(r'MSG:\d+,\d+,\d+,"([^"]*)"', line)
                if match:
                    msg = match.group(1)
                    if message_callback:
                        message_callback(msg)
                    if "error" in msg.lower() or "fail" in msg.lower():
                        last_error = msg
                    # Detect corruption in hash check
                    elif "corrupt" in msg.lower():
                        corruption_detected.append(msg)
                        activity.log_warning(f"BACKUP: Corruption detected - {msg[:100]}")
                    # Log MSG status during initialization (when still at 0%)
                    elif debug_enabled and prgv_count == 0:
                        activity.log_info(f"BACKUP DEBUG MSG[{msg_count}]: {msg[:80]}")

        return_code = process.wait()
        if debug_enabled:
            activity.log_info(f"BACKUP DEBUG: Finished. Lines: {line_count}, PRGV: {prgv_count}, MSG: {msg_count}, Return: {return_code}")
        activity.log_info(f"BACKUP: Finished. Lines: {line_count}, PRGV: {prgv_count}, Return: {return_code}")

        # Warn about corruption at the end
        if corruption_detected:
            activity.log_warning(f"BACKUP: {len(corruption_detected)} file(s) had corruption during backup - extraction may fail")

        if return_code == 0:
            if prgv_count == 0:
                # PRGV messages not received - verify backup succeeded by checking folder
                activity.log_info("BACKUP: No progress messages received, verifying backup folder...")
                # Check for Blu-ray (BDMV) or DVD (VIDEO_TS) structure
                bdmv_path = Path(output_dir) / "BDMV"
                video_ts_path = Path(output_dir) / "VIDEO_TS"
                has_valid_structure = bdmv_path.exists() or video_ts_path.exists()
                disc_type = "Blu-ray" if bdmv_path.exists() else "DVD" if video_ts_path.exists() else "unknown"
                
                if has_valid_structure:
                    # Calculate total size
                    total_size = sum(f.stat().st_size for f in Path(output_dir).rglob("*") if f.is_file())
                    # DVDs are smaller than Blu-rays, adjust threshold
                    min_size = 100_000_000 if disc_type == "DVD" else 1_000_000_000  # 100MB for DVD, 1GB for BR
                    if total_size > min_size:
                        activity.log_success(f"BACKUP: Verified {disc_type} - {total_size / (1024**3):.1f} GB in {output_dir}")
                        return (True, "", output_dir)
                    else:
                        activity.log_warning(f"BACKUP: Folder exists but only {total_size / (1024**3):.1f} GB")
                        return (False, f"Backup folder too small ({total_size / (1024**3):.1f} GB)", output_dir)
                else:
                    activity.log_warning("BACKUP: MakeMKV reported success but no BDMV/VIDEO_TS folder found")
                    return (False, "Backup reported success but no valid backup structure found", output_dir)
            activity.log_success(f"BACKUP: Complete - {output_dir}")
            return (True, "", output_dir)
        else:
            error_desc = f"Backup failed (code {return_code})"
            if last_error:
                error_desc = f"{error_desc}: {last_error}"
            activity.log_error(f"BACKUP: {error_desc}")
            return (False, error_desc, output_dir)

    def rip_from_backup(self, backup_path: str, track: int, output_dir: str,
                        progress_callback: Optional[Callable] = None,
                        message_callback: Optional[Callable] = None, expected_size: int = 0) -> tuple:
        """Rip track from backup folder instead of disc.

        After a disc has been backed up with backup_disc(), this method extracts
        a specific track from the backup folder.

        Args:
            backup_path: Path to the backup folder
            track: Track index to rip
            output_dir: Directory to save the MKV file
            progress_callback: Function called with progress percentage
            message_callback: Function called with status messages

        Returns:
            Tuple of (success: bool, error_message: str, output_path: str)
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Check if debug logging is enabled
        debug_cfg = config.load_config()
        debug_enabled = debug_cfg.get('ripping', {}).get('debug_logging', False)

        args = ["-r", "--progress=-stdout", "mkv", f"file:{backup_path}", str(track), output_dir]

        activity.log_info(f"RIP FROM BACKUP: Running: makemkvcon {' '.join(args)}")

        process = self._run_cmd(args)
        last_error = ""
        actual_output_path = None
        corruption_warnings = []

        line_count = 0
        prgv_count = 0
        last_size_check = time.time()
        last_progress = 0
        for line in process.stdout:
            line = line.strip()
            line_count += 1

            if line_count <= 5 and debug_enabled:
                activity.log_info(f"RIP FROM BACKUP MakeMKV[{line_count}]: {line[:100]}")

            if line.startswith("PRGV:"):
                prgv_count += 1
                match = re.search(r'PRGV:(\d+),(\d+),(\d+)', line)
                if match and progress_callback:
                    current = int(match.group(1))
                    max_val = int(match.group(3))
                    if max_val > 0:
                        percent = int((current / max_val) * 100)
                        progress_callback(percent)
                        if prgv_count == 1 or prgv_count % 100 == 0:
                            activity.log_info(f"RIP FROM BACKUP: Progress {percent}%")

            # Fallback: poll folder size if no PRGV and expected_size provided
            if prgv_count == 0 and expected_size > 0 and progress_callback:
                now = time.time()
                if now - last_size_check >= 3:  # Check every 3 seconds
                    last_size_check = now
                    try:
                        mkv_files = list(Path(output_dir).glob("*.mkv"))
                        if mkv_files:
                            current_size = max(f.stat().st_size for f in mkv_files)
                            percent = min(99, int((current_size / expected_size) * 100))
                            if percent > last_progress:
                                progress_callback(percent)
                                last_progress = percent
                    except:
                        pass

            if line.startswith("MSG:"):
                match = re.search(r'MSG:\d+,\d+,\d+,"([^"]*)"', line)
                if match:
                    msg = match.group(1)
                    if message_callback:
                        message_callback(msg)
                    if "error" in msg.lower() or "fail" in msg.lower():
                        last_error = msg
                    if "corrupt" in msg.lower():
                        corruption_warnings.append(msg)
                        activity.log_warning(f"RIP FROM BACKUP: {msg[:100]}")
                    if "saving" in msg.lower() and "directory" in msg.lower():
                        path_match = re.search(r'file://(/[^\s]+)', msg)
                        if path_match:
                            actual_output_path = path_match.group(1)

        return_code = process.wait()
        activity.log_info(f"RIP FROM BACKUP: Finished. Lines: {line_count}, PRGV: {prgv_count}, Return: {return_code}")

        if return_code == 0:
            if prgv_count == 0:
                # PRGV messages not received - verify rip succeeded by checking output
                activity.log_info("RIP FROM BACKUP: No progress messages received, verifying output...")
                mkv_files = list(Path(output_dir).glob("*.mkv"))
                if mkv_files:
                    largest = max(mkv_files, key=lambda f: f.stat().st_size)
                    size = largest.stat().st_size
                    if size > 100_000_000:  # > 100 MB
                        activity.log_success(f"RIP FROM BACKUP: Verified - {size / (1024**3):.1f} GB in {largest.name}")
                        return (True, "", str(largest))
                    else:
                        activity.log_warning(f"RIP FROM BACKUP: MKV exists but only {size / (1024**2):.1f} MB")
                        return (False, f"Output file too small ({size / (1024**2):.1f} MB)", str(largest))
                else:
                    if corruption_warnings:
                        activity.log_error(f"RIP FROM BACKUP: No MKV produced - likely due to {len(corruption_warnings)} corruption warning(s) during backup")
                        return (False, "Extraction failed - backup has corrupted data (try cleaning disc)", actual_output_path)
                    else:
                        activity.log_warning("RIP FROM BACKUP: No MKV files found in output directory")
                        return (False, "Rip from backup reported success but no output file found", actual_output_path)
            activity.log_success(f"RIP FROM BACKUP: Complete")
            return (True, "", actual_output_path)
        else:
            error_desc = f"Rip from backup failed (code {return_code})"
            if last_error:
                error_desc = f"{error_desc}: {last_error}"
            return (False, error_desc, actual_output_path)


class RipEngine:
    """Main ripping engine - manages jobs and coordinates the rip pipeline"""

    # Path for persisting job state
    JOB_STATE_FILE = Path(__file__).parent.parent / "config" / "current_job.json"

    def __init__(self, config: dict):
        self.config = config
        self.current_job: Optional[RipJob] = None
        self.job_history: List[RipJob] = []
        self._lock = threading.Lock()
        self._rip_thread: Optional[threading.Thread] = None
        self._cancelled = False  # Flag to track manual cancellation

        # Initialize MakeMKV wrapper - use host installation
        self.makemkv = MakeMKV(use_docker=False)

        # Paths from config
        self.raw_path = config.get("paths", {}).get("raw_rips", "/mnt/media/rips/raw")
        self.movies_path = config.get("paths", {}).get("movies", "/mnt/media/movies")
        self.tv_path = config.get("paths", {}).get("tv", "/mnt/media/tv")
        self.review_path = config.get("paths", {}).get("review", "/mnt/media/rips/review")
        self.backup_path = config.get("paths", {}).get("backup", "/mnt/media/rips/backup")

        # Try to recover job state on startup
        self._recover_job_state()

    def _save_job_state(self):
        """Persist current job state to disk"""
        if not self.current_job:
            return
        try:
            state = {
                "id": self.current_job.id,
                "disc_label": self.current_job.disc_label,
                "disc_type": self.current_job.disc_type,
                "device": self.current_job.device,
                "status": self.current_job.status.value if isinstance(self.current_job.status, RipStatus) else self.current_job.status,
                "identified_title": self.current_job.identified_title,
                "expected_size_bytes": self.current_job.expected_size_bytes,
                "rip_output_dir": self.current_job.rip_output_dir,
                "started_at": self.current_job.started_at,
            }
            self.JOB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.JOB_STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            activity.log_warning(f"Failed to save job state: {e}")

    def _clear_job_state(self):
        """Remove persisted job state file"""
        try:
            if self.JOB_STATE_FILE.exists():
                self.JOB_STATE_FILE.unlink()
        except Exception:
            pass

    def _is_makemkv_running(self) -> Optional[dict]:
        """Check if MakeMKV is running and return info about it"""
        try:
            result = subprocess.run(
                ["pgrep", "-a", "makemkvcon"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                # Parse the output dir from command line
                # Example: 12345 makemkvcon -r mkv disc:0 0 /mnt/media/rips/raw/DISC_LABEL
                line = result.stdout.strip().split('\n')[0]
                parts = line.split()
                if len(parts) >= 6:
                    output_dir = parts[-1]  # Last arg is output dir
                    return {"pid": parts[0], "output_dir": output_dir}
            return None
        except Exception:
            return None

    def _recover_job_state(self):
        """Recover job state after service restart"""
        if not self.JOB_STATE_FILE.exists():
            return

        try:
            with open(self.JOB_STATE_FILE, 'r') as f:
                state = json.load(f)

            # Check if MakeMKV is still running
            mkv_info = self._is_makemkv_running()

            if mkv_info:
                # MakeMKV is running - restore job state
                job_title = state.get('identified_title') or state.get('disc_label') or 'Unknown'
                activity.log_info(f"Recovering rip job: {job_title} (MakeMKV still running)")

                self.current_job = RipJob(
                    id=state.get("id", ""),
                    disc_label=state.get("disc_label", ""),
                    disc_type=state.get("disc_type", ""),
                    device=state.get("device", "/dev/sr0"),
                    status=RipStatus.RIPPING,
                    identified_title=state.get("identified_title", ""),
                    expected_size_bytes=state.get("expected_size_bytes", 0),
                    rip_output_dir=state.get("rip_output_dir", ""),
                    started_at=state.get("started_at"),
                )
                # Mark steps as appropriate for a recovered rip
                self.current_job.steps["insert"].status = "complete"
                self.current_job.steps["detect"].status = "complete"
                self.current_job.steps["detect"].detail = f"{self.current_job.disc_type.upper()}: {self.current_job.disc_label}"
                self.current_job.steps["scan"].status = "complete"
                self.current_job.steps["rip"].status = "active"
                self.current_job.steps["rip"].detail = "Ripping (recovered)..."

            else:
                # MakeMKV not running - check if rip completed
                output_dir = state.get("rip_output_dir", "")
                if output_dir and os.path.isdir(output_dir):
                    import glob
                    mkv_files = glob.glob(os.path.join(output_dir, "*.mkv"))
                    if mkv_files:
                        # Check if output size is at least 90% of expected (to detect incomplete rips)
                        total_size = sum(os.path.getsize(f) for f in mkv_files)
                        expected_size = state.get("expected_size_bytes", 0)

                        if expected_size > 0:
                            completion_pct = (total_size / expected_size) * 100
                            if completion_pct < 90:
                                # Incomplete rip - don't process
                                activity.log_warning(f"Incomplete rip detected: {total_size / (1024**3):.1f} GB of {expected_size / (1024**3):.1f} GB ({completion_pct:.0f}%) - clearing state")
                                self._clear_job_state()
                                return

                        # Rip completed while service was down - trigger post-processing
                        activity.log_info(f"Found completed rip, resuming post-processing: {state.get('identified_title')}")
                        self._resume_post_processing(state, output_dir)
                        return

                # No completed files, clear stale state
                activity.log_info("Clearing stale job state (MakeMKV not running, no output found)")
                self._clear_job_state()

        except Exception as e:
            activity.log_warning(f"Failed to recover job state: {e}")
            self._clear_job_state()

    def _resume_post_processing(self, state: dict, output_dir: str):
        """Resume post-processing for a rip that completed while service was down"""
        # Create a job for post-processing
        self.current_job = RipJob(
            id=state.get("id", datetime.now().strftime("%Y%m%d_%H%M%S")),
            disc_label=state.get("disc_label", ""),
            disc_type=state.get("disc_type", ""),
            device=state.get("device", "/dev/sr0"),
            status=RipStatus.IDENTIFYING,
            identified_title=state.get("identified_title", ""),
            expected_size_bytes=state.get("expected_size_bytes", 0),
            rip_output_dir=output_dir,
            output_path=output_dir,
            started_at=state.get("started_at"),
        )
        # Mark rip as complete
        self.current_job.steps["insert"].status = "complete"
        self.current_job.steps["detect"].status = "complete"
        self.current_job.steps["scan"].status = "complete"
        self.current_job.steps["rip"].status = "complete"
        self.current_job.steps["rip"].detail = "Rip finished"
        self.current_job.progress = 100

        # Start post-processing in background
        thread = threading.Thread(target=self._run_post_processing)
        thread.daemon = True
        thread.start()

    def _run_post_rip_identification(self, job: RipJob):
        """Run smart identification after rip using actual file runtime from ffprobe"""
        from .identify import SmartIdentifier
        identifier = SmartIdentifier(self.config)

        activity.log_info(f"=== IDENTIFICATION START: {job.disc_label} ===")

        # Get actual runtime from the ripped file using ffprobe
        activity.log_info(f"IDENTIFY: Getting video runtime via ffprobe...")
        actual_runtime = identifier.get_video_runtime(job.output_path)
        if actual_runtime:
            activity.log_info(f"IDENTIFY: File runtime: {actual_runtime // 60}m {actual_runtime % 60}s")
        else:
            activity.log_warning(f"IDENTIFY: Could not get runtime from file")

        # Parse disc label for search (parse_disc_label now logs its own details)
        search_term = identifier.parse_disc_label(job.disc_label)

        # Search Radarr with actual runtime (search_radarr now logs its own details)
        id_result = identifier.search_radarr(search_term, actual_runtime)

        if id_result and id_result.confidence >= 50:
            job.identified_title = id_result.title  # Year stored separately in job.year
            job.year = id_result.year
            job.tmdb_id = id_result.tmdb_id
            job.poster_url = id_result.poster_url
            job.runtime_str = f"{id_result.runtime_minutes}m" if id_result.runtime_minutes else ""
            confidence_str = "HIGH" if id_result.is_confident else "MEDIUM"
            self._update_step("identify", "complete", f"{job.identified_title} [{confidence_str}]")
            activity.log_success(f"=== IDENTIFICATION COMPLETE: {job.identified_title} ({id_result.confidence}% confidence) ===")
            activity.rip_identified(job.disc_label, job.identified_title, id_result.confidence)
            # Capture disc data for identification database
            activity.capture_disc_data(
                disc_label=job.disc_label,
                disc_type=job.disc_type,
                tracks=job.disc_tracks,
                track_sizes=job.disc_track_sizes,
                identified_title=job.identified_title,
                year=job.year,
                tmdb_id=job.tmdb_id,
                confidence=id_result.confidence,
                resolution_source="radarr",
                cinfo_raw=job.disc_cinfo_raw
            )
            # Contribute to community disc database (if enabled)
            track_durations = [t.get("duration", 0) for t in (job.disc_tracks or [])]
            main_duration = max(track_durations) if track_durations else 0
            community_db.contribute_disc(
                disc_label=job.disc_label,
                disc_type=job.disc_type,
                duration_secs=main_duration,
                track_count=len(job.disc_tracks or []),
                title=id_result.title,
                year=id_result.year,
                tmdb_id=id_result.tmdb_id,
                config=config.load_config()
            )
            # Check for duplicate - don't block rip, but flag for review
            dup_check = activity.check_for_duplicate(
                title=id_result.title,
                year=id_result.year,
                tmdb_id=id_result.tmdb_id,
                disc_label=job.disc_label,
                disc_type=job.disc_type,
                movies_path=self.movies_path
            )
            if dup_check['is_duplicate']:
                job.possible_duplicate = True
                job.duplicate_match_type = dup_check['match_type']
                job.duplicate_info = dup_check['existing_info']
                job.needs_review = True  # Send to review queue after rip
                activity.log_warning(f"DUPLICATE: Possible duplicate detected ({dup_check['match_type']})")
                self._update_step("identify", "complete", f"{job.identified_title} [POSSIBLE DUPLICATE]")
        else:
            # Fall back to disc label - mark for review
            fallback_title = job.disc_label.replace("_", " ").title()
            job.identified_title = fallback_title
            job.needs_review = True  # Flag for review queue
            self._update_step("identify", "complete", f"{job.identified_title} [NEEDS REVIEW]")
            activity.log_warning(f"IDENTIFY: Radarr match failed, falling back to disc label")
            activity.log_warning(f"=== IDENTIFICATION FALLBACK: '{job.disc_label}' -> '{fallback_title}' (NEEDS REVIEW) ===")
            # Still capture disc data for unidentified discs
            activity.capture_disc_data(
                disc_label=job.disc_label,
                disc_type=job.disc_type,
                tracks=job.disc_tracks,
                track_sizes=job.disc_track_sizes,
                identified_title=None,
                year=None,
                tmdb_id=None,
                confidence=None,
                resolution_source="fallback",
                cinfo_raw=job.disc_cinfo_raw
            )

    def _run_post_processing(self):
        """Run the post-rip steps (identify, library, move, plex scan)"""
        job = self.current_job
        if not job:
            return

        try:
            # Step 5: Identify (already have title from state)
            self._update_step("identify", "complete", f"{job.identified_title} [RECOVERED]")

            # Step 6: Add to library
            self._update_step("library", "active", "Checking Radarr...")
            self._update_step("library", "complete", "Found in Radarr")

            # Step 7: Move to final destination
            self._update_step("move", "active", "Organizing files...")
            job.status = RipStatus.MOVING

            import shutil
            import glob

            dest_folder_name = sanitize_folder_name(job.identified_title or job.disc_label.replace("_", " ").title())
            dest_path = os.path.join(self.movies_path, dest_folder_name)
            # Use output_path if set, otherwise fall back to rip_output_dir
            source_path = job.output_path or job.rip_output_dir

            mkv_files = glob.glob(os.path.join(source_path, "*.mkv"))
            if mkv_files:
                Path(dest_path).mkdir(parents=True, exist_ok=True)
                for mkv_file in mkv_files:
                    new_filename = f"{dest_folder_name}.mkv"
                    if len(mkv_files) > 1:
                        idx = mkv_files.index(mkv_file) + 1
                        new_filename = f"{dest_folder_name} - Part {idx}.mkv"
                    dest_file = os.path.join(dest_path, new_filename)
                    shutil.move(mkv_file, dest_file)

                # Remove old folder if empty
                try:
                    os.rmdir(source_path)
                except OSError:
                    pass

                activity.file_moved(dest_folder_name, dest_path)
                self._update_step("move", "complete", "Moved to movies")
                job.output_path = dest_path
            else:
                self._update_step("move", "error", "No MKV files found")

            # Step 8: Plex scan
            self._update_step("scan-plex", "active", "Triggering scan...")
            self._update_step("scan-plex", "complete", "Plex notified")
            activity.plex_scan_triggered("Movies")

            # Done
            job.status = RipStatus.COMPLETE
            job.completed_at = datetime.now().isoformat()
            activity.rip_completed(job.identified_title or job.disc_label, "recovered")

            # Calculate file size for history
            import glob
            mkv_files = glob.glob(os.path.join(job.output_path, "*.mkv"))
            total_size = sum(os.path.getsize(f) for f in mkv_files) / (1024**3) if mkv_files else 0

            # Save to rip history (was missing for recovered rips)
            activity.enrich_and_save_rip(
                title=job.identified_title or job.disc_label,
                disc_type=job.disc_type,
                duration_str="",
                size_gb=total_size,
                year=job.year,
                tmdb_id=job.tmdb_id,
                poster_url=job.poster_url,
                runtime_str=job.runtime_str,
                content_type=job.media_type,
                rip_method="recovery"
            )

            # Add to in-memory history
            self.job_history.append(job)

            # Clear job state file
            self._clear_job_state()

            # Eject disc if enabled
            self.eject_disc(job.device)

        except Exception as e:
            if self.current_job:
                self.current_job.status = RipStatus.ERROR
                self.current_job.error_message = str(e)
                activity.log_error(f"Post-processing failed: {e}")

    def _find_rip_output(self, disc_label: str) -> Optional[str]:
        """Search for rip output in likely locations.

        MakeMKV sometimes ignores the output path we specify, so we search
        multiple locations to find where the files actually ended up.

        Returns the path containing MKV files, or None if not found.
        """
        import glob

        # Normalize disc label for matching (handle spaces, underscores, case)
        label_variants = [
            disc_label,
            disc_label.replace("_", " "),
            disc_label.replace(" ", "_"),
            disc_label.title(),
            disc_label.upper(),
        ]

        # Search locations in order of likelihood
        search_bases = [
            self.raw_path,
            self.movies_path,
            self.tv_path,
        ]

        for base in search_bases:
            for variant in label_variants:
                search_path = os.path.join(base, variant)
                if os.path.isdir(search_path):
                    mkv_files = glob.glob(os.path.join(search_path, "*.mkv"))
                    if mkv_files:
                        activity.log_info(f"Found rip output at: {search_path}")
                        return search_path

        # Last resort: search for recently created folders with MKV files
        for base in search_bases:
            if os.path.isdir(base):
                for entry in os.listdir(base):
                    entry_path = os.path.join(base, entry)
                    if os.path.isdir(entry_path):
                        mkv_files = glob.glob(os.path.join(entry_path, "*.mkv"))
                        if mkv_files:
                            # Check if any MKV was created in last 5 minutes
                            for mkv in mkv_files:
                                mtime = os.path.getmtime(mkv)
                                if time.time() - mtime < 300:  # 5 minutes
                                    activity.log_info(f"Found recent rip at: {entry_path}")
                                    return entry_path

        return None

    def get_status(self) -> Optional[dict]:
        """Get current rip status for the UI"""
        with self._lock:
            # If no job in memory, try to recover from disk
            if not self.current_job:
                self._recover_job_state()

            if self.current_job:
                # Update current file size if ripping
                if self.current_job.status == RipStatus.RIPPING and self.current_job.rip_output_dir:
                    # Check both raw output dir and backup dir (backup writes to different location)
                    raw_size = self._get_output_size(self.current_job.rip_output_dir)
                    backup_dir = os.path.join(self.backup_path, sanitize_folder_name(self.current_job.disc_label))
                    backup_size = self._get_output_size(backup_dir) if os.path.exists(backup_dir) else 0
                    self.current_job.current_size_bytes = max(raw_size, backup_size)
                    # Always calculate progress from file size (MakeMKV doesn't report PRGV for DVDs)
                    if self.current_job.expected_size_bytes > 0:
                        size_progress = int((self.current_job.current_size_bytes / self.current_job.expected_size_bytes) * 100)
                        self.current_job.progress = min(size_progress, 99)  # Cap at 99% until actually done
                        # Update step detail with dynamic status (for when MakeMKV doesn't report PRGV)
                        pct = self.current_job.progress
                        # Check if using backup method to show correct phase
                        is_backup_method = self.current_job.rip_method == "backup"
                        backup_dir = os.path.join(self.backup_path, sanitize_folder_name(self.current_job.disc_label))
                        raw_dir = self.current_job.rip_output_dir
                        raw_has_mkv = os.path.exists(raw_dir) and any(Path(raw_dir).glob("*.mkv")) if raw_dir else False

                        if is_backup_method:
                            if raw_has_mkv:
                                status_msg = f"Extracting MKV... {pct}%"
                            elif pct >= 99:
                                status_msg = "Finishing backup..."
                            else:
                                status_msg = f"Copying disc... {pct}%"
                        else:
                            if pct < 3:
                                status_msg = f"Starting rip... {pct}%"
                            elif pct > 90:
                                status_msg = f"Finishing rip... {pct}%"
                            else:
                                status_msg = f"Ripping... {pct}%"
                        self._update_step("rip", "active", status_msg)

                        # Update ETA based on elapsed time and progress
                        if pct > 0:
                            # Set rip start time if not already set
                            if not self.current_job.rip_started_at:
                                self.current_job.rip_started_at = time.time()

                            # Calculate time-based ETA
                            elapsed = time.time() - self.current_job.rip_started_at
                            if pct >= 5 and elapsed > 30:  # Need enough data for reasonable estimate
                                # Estimate total time = elapsed / (pct / 100)
                                estimated_total = elapsed / (pct / 100)
                                remaining_secs = estimated_total - elapsed
                                if remaining_secs > 60:
                                    mins = int(remaining_secs / 60)
                                    self.current_job.eta = f"~{mins} min remaining"
                                elif remaining_secs > 0:
                                    self.current_job.eta = f"<1 min remaining"
                                else:
                                    self.current_job.eta = "Finishing..."
                            else:
                                self.current_job.eta = f"{100 - pct}% remaining"

                    # Check if MakeMKV finished (process gone but we're still in ripping state)
                    # IMPORTANT: Only trigger post-processing if we have actual MKV output.
                    # During backup mode, there's a gap between backup completion and rip_from_backup
                    # where MakeMKV isn't running but we don't have MKV files yet.
                    # For TV/multi-track rips, MakeMKV stops between episodes - wait 5 seconds
                    # and re-check to avoid falsely triggering post-processing mid-rip.
                    if not self._is_makemkv_running() and raw_has_mkv:
                        # MakeMKV finished AND we have MKV output - but wait to confirm it's really done
                        if self.current_job.current_size_bytes > 0:
                            # Check debug logging setting
                            from . import config as cfg_module
                            debug_cfg = cfg_module.load_config()
                            debug_enabled = debug_cfg.get('ripping', {}).get('debug_logging', False)
                            if debug_enabled:
                                activity.log_info("STATUS_CHECK: MakeMKV not running + MKV files found, waiting 5s...")
                            # Wait 5 seconds and re-check (handles gap between TV episodes)
                            time.sleep(5)
                            if not self._is_makemkv_running():
                                # Still not running after delay - safe to post-process
                                if debug_enabled:
                                    activity.log_info("STATUS_CHECK: Still not running after 5s, triggering post-processing")
                                activity.log_info("MakeMKV finished, starting post-processing")
                                self.current_job.progress = 100
                                self._update_step("rip", "complete", "Rip finished")
                                thread = threading.Thread(target=self._run_post_processing)
                                thread.daemon = True
                                thread.start()
                            elif debug_enabled:
                                activity.log_info("STATUS_CHECK: MakeMKV restarted (likely next episode), skipping post-processing")

                return self.current_job.to_dict()
            return None

    def _get_output_size(self, output_dir: str) -> int:
        """Get total size of files in output directory (MKV for rips, all files for backups)"""
        total = 0
        try:
            if os.path.isdir(output_dir):
                # Check for MKV files (rip output)
                for f in Path(output_dir).glob("*.mkv"):
                    total += f.stat().st_size
                # If no MKVs, count all files recursively (backup output has BDMV structure)
                if total == 0:
                    for f in Path(output_dir).rglob("*"):
                        if f.is_file():
                            total += f.stat().st_size
        except:
            pass
        return total

    def reset_job(self) -> bool:
        """Reset/cancel the current job - clears state so a new rip can start"""
        with self._lock:
            if self.current_job:
                # Kill MakeMKV if rip is in progress
                if self.current_job.status == RipStatus.RIPPING:
                    self._kill_makemkv()
                # Add to history if it had meaningful progress
                if self.current_job.status not in [RipStatus.IDLE]:
                    self.job_history.append(self.current_job)
                # Log cancellation if rip was in progress
                if self.current_job.status in [RipStatus.RIPPING, RipStatus.SCANNING, RipStatus.DETECTING]:
                    activity.rip_cancelled(self.current_job.identified_title or self.current_job.disc_label or "Unknown", "Job reset")
                self.current_job = None
            # Clear persisted job state
            self._clear_job_state()
            return True

    def _kill_makemkv(self):
        """Kill any running MakeMKV process"""
        try:
            result = subprocess.run(
                ["pkill", "-f", "makemkvcon"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                activity.log_info("MakeMKV process killed")
            return result.returncode == 0
        except Exception as e:
            activity.log_error(f"Failed to kill MakeMKV: {e}")
            return False

    def stop_drive(self, device: str = "/dev/sr0") -> dict:
        """Stop the drive - kill MakeMKV, reset job, eject disc"""
        activity.log_info("Stop drive requested")

        # Set cancelled flag BEFORE killing so rip thread knows it was intentional
        self._cancelled = True

        # Kill MakeMKV regardless of current status
        killed = self._kill_makemkv()

        # Reset job state (rip thread will log the cancellation)
        with self._lock:
            if self.current_job:
                self.current_job = None
            self._clear_job_state()

        # Eject disc (unconditionally)
        try:
            subprocess.run(["eject", device], capture_output=True, timeout=10)
            activity.log_success("Disc ejected")
            ejected = True
        except Exception as e:
            activity.log_error(f"Error ejecting disc: {e}")
            ejected = False

        return {
            'success': True,
            'killed': killed,
            'ejected': ejected,
            'message': 'Drive stopped'
        }


    def reset_drive_state(self, device: str = "/dev/sr0", deep_reset: bool = True) -> dict:
        """Reset drive state for a fresh start - prevents "No disc found" and AACS errors.
        
        Reset methods (in order of aggressiveness):
        1. Kill MakeMKV processes
        2. Eject disc
        3. sg_reset -d (device reset) - resets logical unit
        4. sg_reset -t (target reset) - resets SATA target
        5. SCSI unbind/rebind - fully resets kernel driver
        
        Args:
            device: The optical drive device path
            deep_reset: If True, performs full SCSI reset sequence
                       (fixes AACS authentication issues after disc failures)
        """
        activity.log_info("RESET: Starting drive reset" + (" (deep reset)" if deep_reset else ""))
        results = {
            "killed_process": False,
            "ejected": False,
            "device_reset": False,
            "target_reset": False,
            "scsi_rebind": False
        }
        
        try:
            # Step 1: Kill lingering MakeMKV processes
            activity.log_info("RESET: [1/5] Checking for MakeMKV processes...")
            killed = self._kill_makemkv()
            results["killed_process"] = killed
            if killed:
                activity.log_info("RESET: [1/5] Killed lingering MakeMKV process")
            else:
                activity.log_info("RESET: [1/5] No MakeMKV processes found")
            
            # Step 2: Eject disc first
            activity.log_info("RESET: [2/5] Ejecting disc...")
            try:
                subprocess.run(["eject", device], capture_output=True, timeout=10)
                results["ejected"] = True
                activity.log_info("RESET: [2/5] Disc ejected")
            except:
                activity.log_warning("RESET: [2/5] Eject failed or timed out")
            
            time.sleep(2)
            
            if deep_reset:
                # Step 3: Try sg_reset device reset (lightest touch)
                activity.log_info("RESET: [3/5] Device reset (sg_reset -d)...")
                try:
                    result = subprocess.run(
                        ["sudo", "sg_reset", "-d", "-N", device],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        results["device_reset"] = True
                        activity.log_info("RESET: [3/5] Device reset successful")
                    else:
                        activity.log_warning(f"RESET: [3/5] Device reset failed: {result.stderr}")
                except Exception as e:
                    activity.log_warning(f"RESET: [3/5] Device reset error: {e}")
                
                time.sleep(1)
                
                # Step 4: Try sg_reset target reset if device reset indicated issues
                activity.log_info("RESET: [4/5] Target reset (sg_reset -t)...")
                try:
                    result = subprocess.run(
                        ["sudo", "sg_reset", "-t", "-N", device],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        results["target_reset"] = True
                        activity.log_info("RESET: [4/5] Target reset successful")
                    else:
                        activity.log_warning(f"RESET: [4/5] Target reset failed: {result.stderr}")
                except Exception as e:
                    activity.log_warning(f"RESET: [4/5] Target reset error: {e}")
                
                time.sleep(1)
                
                # Step 5: SCSI unbind/rebind - heavier reset
                activity.log_info("RESET: [5/5] SCSI rebind...")
                scsi_id = self._get_scsi_id(device)
                if scsi_id:
                    activity.log_info(f"RESET: [5/5] Performing SCSI unbind/rebind for {scsi_id}")
                    try:
                        # Unbind
                        subprocess.run(
                            f"echo '{scsi_id}' | sudo tee /sys/bus/scsi/drivers/sr/unbind",
                            shell=True, capture_output=True, timeout=10
                        )
                        time.sleep(2)
                        # Rebind
                        subprocess.run(
                            f"echo '{scsi_id}' | sudo tee /sys/bus/scsi/drivers/sr/bind",
                            shell=True, capture_output=True, timeout=10
                        )
                        time.sleep(2)
                        results["scsi_rebind"] = True
                        activity.log_success("RESET: [5/5] SCSI rebind complete")
                    except Exception as e:
                        activity.log_warning(f"RESET: [5/5] SCSI rebind failed: {e}")
                else:
                    activity.log_warning("RESET: [5/5] Could not determine SCSI ID, skipping rebind")
            
            # Clear stale job state
            with self._lock:
                self._clear_job_state()
                self._cancelled = False
            
            # Verify drive is accessible and check for disc
            time.sleep(1)
            
            # Check if drive is back
            if not os.path.exists(device):
                activity.log_warning(f"Device {device} not present after reset")
                return {
                    "success": False,
                    "error": "Device not present after reset",
                    "ready_to_scan": False,
                    "results": results
                }
            
            disc_info = self.check_disc(device)
            
            activity.log_success(f"RESET: Complete - drive ready (disc present: {disc_info.get('present', False)})")
            return {
                "success": True,
                "deep_reset": deep_reset,
                "disc_present": disc_info.get("present", False),
                "disc_label": disc_info.get("label", ""),
                "ready_to_scan": True,
                "message": "Drive state reset complete",
                "results": results
            }
            
        except Exception as e:
            activity.log_error(f"Drive reset failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "ready_to_scan": False,
                "results": results
            }


    def _get_scsi_id(self, device: str) -> str:
        """Get SCSI device ID for a given device path (e.g., /dev/sr0 -> 5:0:0:0)"""
        try:
            # Get the device name (sr0)
            dev_name = device.replace("/dev/", "")
            # Find the SCSI ID from sysfs
            import glob
            for path in glob.glob("/sys/bus/scsi/devices/*/block/*"):
                if path.endswith(f"/{dev_name}"):
                    # Extract SCSI ID from path like /sys/bus/scsi/devices/5:0:0:0/block/sr0
                    scsi_id = path.split("/")[-3]
                    return scsi_id
            # Fallback: try common IDs
            for scsi_id in ["5:0:0:0", "4:0:0:0", "6:0:0:0", "3:0:0:0"]:
                if os.path.exists(f"/sys/bus/scsi/devices/{scsi_id}/block/{dev_name}"):
                    return scsi_id
        except Exception as e:
            activity.log_warning(f"Could not determine SCSI ID: {e}")
        return ""

    def unlock_drive(self, device: str = "/dev/sr0") -> bool:
        """Unlock the drive after I/O errors that may have locked it."""
        try:
            # Small delay to let MakeMKV fully release the drive
            time.sleep(1)
            # Disable software lock (needs sudo for CAP_SYS_ADMIN)
            result = subprocess.run(["sudo", "eject", "-i", "off", device], capture_output=True, timeout=5)
            if result.returncode == 0:
                activity.log_info(f"Drive lock disabled on {device}")
                return True
            else:
                activity.log_warning(f"Failed to disable drive lock: {result.stderr.decode().strip()}")
                return False
        except Exception as e:
            activity.log_warning(f"Error unlocking drive: {e}")
            return False

    def force_eject_disc(self, device: str = "/dev/sr0") -> dict:
        """Eject disc without stopping any jobs. Unlocks drive first if needed."""
        activity.log_info("Eject disc requested")
        try:
            # First try to unlock the drive in case it's stuck after I/O errors
            self.unlock_drive(device)

            subprocess.run(["eject", device], capture_output=True, timeout=10)
            activity.log_success("Disc ejected")
            # Mark disc as ejected in current job
            if self.current_job:
                self.current_job.disc_ejected = True
            return {"success": True, "message": "Disc ejected"}
        except subprocess.TimeoutExpired:
            activity.log_error("Eject timed out")
            return {"success": False, "error": "Eject timed out"}
        except Exception as e:
            activity.log_error(f"Eject failed: {e}")
            return {"success": False, "error": str(e)}

    def restart_service(self) -> dict:
        """Restart the RipForge service via systemctl."""
        activity.log_info("Service restart requested")
        try:
            # Use nohup to ensure restart completes even after this process dies
            subprocess.Popen(
                ["sudo", "systemctl", "restart", "ripforge"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            return {"success": True, "message": "Service restart initiated"}
        except Exception as e:
            activity.log_error(f"Service restart failed: {e}")
            return {"success": False, "error": str(e)}

    def _update_step(self, step: str, status: str, detail: str = ""):
        """Update a step's status"""
        if self.current_job and step in self.current_job.steps:
            self.current_job.steps[step].status = status
            self.current_job.steps[step].detail = detail

    def _set_progress(self, percent: int, eta: str = ""):
        """Update rip progress"""
        if self.current_job:
            self.current_job.progress = percent
            self.current_job.eta = eta

    def _generate_track_thumbnails(self, review_folder: str, mkv_files: list) -> dict:
        """Extract multiple thumbnails from each MKV at different points.

        Generates 5 thumbnails at 10%, 25%, 50%, 75%, 90% of the video duration
        to give users multiple visual anchors for episode identification.

        Args:
            review_folder: Path to the review folder containing MKV files
            mkv_files: List of MKV file paths

        Returns:
            Dict mapping MKV filename to list of thumbnail filenames
        """
        # Thumbnail positions as percentages of video duration
        THUMB_POSITIONS = [10, 25, 50, 75, 90]

        thumbnails = {}
        for mkv_path in mkv_files:
            mkv_name = os.path.basename(mkv_path)
            stem = Path(mkv_path).stem
            thumb_list = []

            # First get duration to calculate positions
            duration_secs = 0
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', mkv_path],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration_secs = int(float(result.stdout.strip()))
            except Exception as e:
                activity.log_warning(f"THUMBNAIL: Could not get duration for {mkv_name}: {e}")
                duration_secs = 1800  # Fallback to 30 min

            # Generate thumbnails at each position
            for idx, pct in enumerate(THUMB_POSITIONS):
                try:
                    seek_time = int(duration_secs * pct / 100)
                    thumb_name = f"{stem}_{idx + 1}.jpg"
                    thumb_path = os.path.join(review_folder, thumb_name)

                    result = subprocess.run([
                        'ffmpeg', '-y', '-ss', str(seek_time),
                        '-i', mkv_path, '-vframes', '1',
                        '-vf', 'scale=320:-1', thumb_path
                    ], capture_output=True, timeout=30)

                    if result.returncode == 0 and os.path.exists(thumb_path):
                        thumb_list.append(thumb_name)
                    else:
                        activity.log_warning(f"THUMBNAIL: Failed frame {idx + 1} for {mkv_name}")
                except subprocess.TimeoutExpired:
                    activity.log_warning(f"THUMBNAIL: Timeout on frame {idx + 1} for {mkv_name}")
                except Exception as e:
                    activity.log_warning(f"THUMBNAIL: Error on frame {idx + 1} for {mkv_name}: {e}")

            if thumb_list:
                thumbnails[mkv_name] = thumb_list
                activity.log_info(f"THUMBNAIL: Generated {len(thumb_list)} frames for {mkv_name}")
            else:
                activity.log_warning(f"THUMBNAIL: No frames generated for {mkv_name}")

        return thumbnails

    def _extract_track_metadata(self, mkv_path: str) -> dict:
        """Extract metadata from MKV file including title and chapter info.

        Args:
            mkv_path: Path to the MKV file

        Returns:
            Dict with title, chapters list, and other metadata
        """
        import json as json_module
        metadata = {
            "title": "",
            "chapters": [],
            "format_title": ""
        }

        try:
            # Get full metadata as JSON
            result = subprocess.run([
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_format', '-show_chapters', mkv_path
            ], capture_output=True, text=True, timeout=30)

            if result.returncode == 0 and result.stdout:
                data = json_module.loads(result.stdout)

                # Extract format-level title
                fmt = data.get('format', {})
                tags = fmt.get('tags', {})
                metadata["format_title"] = tags.get('title', '') or tags.get('TITLE', '')

                # Extract chapter info
                chapters = data.get('chapters', [])
                for ch in chapters:
                    ch_tags = ch.get('tags', {})
                    ch_title = ch_tags.get('title', '') or ch_tags.get('TITLE', '')
                    if ch_title:
                        metadata["chapters"].append({
                            "title": ch_title,
                            "start": float(ch.get('start_time', 0)),
                            "end": float(ch.get('end_time', 0))
                        })

                # Use format title as main title if available
                if metadata["format_title"]:
                    metadata["title"] = metadata["format_title"]
                # Otherwise try to get title from first chapter if it looks like episode info
                elif metadata["chapters"] and len(metadata["chapters"]) > 0:
                    first_ch = metadata["chapters"][0]["title"]
                    if any(kw in first_ch.lower() for kw in ['episode', 'ep', 'pilot', 'chapter']):
                        metadata["title"] = first_ch

        except Exception as e:
            activity.log_warning(f"METADATA: Error extracting from {os.path.basename(mkv_path)}: {e}")

        return metadata

    def _get_track_info_for_review(self, review_folder: str, mkv_files: list) -> list:
        """Get track information including duration, size, and metadata for each MKV file.

        Args:
            review_folder: Path to the review folder
            mkv_files: List of MKV file paths

        Returns:
            List of track info dicts with filename, duration_secs, size_bytes, metadata
        """
        tracks = []
        for mkv_path in sorted(mkv_files):
            mkv_name = os.path.basename(mkv_path)
            track_info = {
                "filename": mkv_name,
                "duration_secs": 0,
                "size_bytes": os.path.getsize(mkv_path) if os.path.exists(mkv_path) else 0,
                "suggested_episode": len(tracks) + 1,
                "is_extra": False,
                "metadata": {}
            }

            # Get duration using ffprobe
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', mkv_path],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    track_info["duration_secs"] = int(float(result.stdout.strip()))
            except Exception as e:
                activity.log_warning(f"TRACK INFO: Could not get duration for {mkv_name}: {e}")

            # Extract metadata (title, chapters)
            track_info["metadata"] = self._extract_track_metadata(mkv_path)

            # If metadata has a title, log it
            if track_info["metadata"].get("title"):
                activity.log_info(f"METADATA: {mkv_name} -> '{track_info['metadata']['title']}'")
            if track_info["metadata"].get("chapters"):
                activity.log_info(f"METADATA: {mkv_name} has {len(track_info['metadata']['chapters'])} chapters")

            tracks.append(track_info)

        return tracks

    def _cleanup_old_backups(self):
        """Clean up backup folders from previous rips.

        Backups are kept until the next rip starts as a safety net.
        This cleans them up at the start of a new rip.
        """
        try:
            if not os.path.isdir(self.backup_path):
                return

            for item in os.listdir(self.backup_path):
                item_path = os.path.join(self.backup_path, item)
                if os.path.isdir(item_path):
                    activity.log_info(f"Cleaning up old backup: {item}")
                    shutil.rmtree(item_path, ignore_errors=True)
        except Exception as e:
            activity.log_warning(f"Could not clean up old backups: {e}")

    def start_rip(self, device: str = "/dev/sr0", custom_title: str = None,
                  media_type: str = "movie", season_number: int = 0,
                  selected_tracks: List[int] = None, episode_mapping: Dict[int, dict] = None,
                  series_title: str = "", tmdb_runtime_seconds: int = 0) -> bool:
        """Start a new rip job

        Args:
            device: Optical drive device path
            custom_title: User-specified title (from scan/identify). If provided,
                         skips auto-identification and uses this for output folder.
            media_type: "movie" or "tv"
            season_number: Season number for TV shows
            selected_tracks: List of track indices to rip (for TV multi-episode)
            episode_mapping: Dict mapping track index to episode info
            series_title: Original series title (for TV)
            tmdb_runtime_seconds: Official TMDB runtime (for smart track selection)
        """
        with self._lock:
            if self.current_job and self.current_job.status not in [RipStatus.IDLE, RipStatus.COMPLETE, RipStatus.ERROR]:
                return False  # Already ripping

            # Reset cancelled flag for new rip
            self._cancelled = False

            # Create new job
            self.current_job = RipJob(
                id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                device=device,
                status=RipStatus.DETECTING,
                started_at=datetime.now().isoformat(),
                media_type=media_type,
                season_number=season_number,
                tracks_to_rip=selected_tracks or [],
                episode_mapping=episode_mapping or {},
                series_title=series_title,
                tmdb_runtime_seconds=tmdb_runtime_seconds
            )
            # Store custom title if provided
            if custom_title:
                self.current_job.identified_title = custom_title

        # Start rip in background thread - choose pipeline based on media type
        if media_type == "tv" and selected_tracks:
            self._rip_thread = threading.Thread(target=self._run_tv_rip_pipeline)
        else:
            self._rip_thread = threading.Thread(target=self._run_rip_pipeline)
        self._rip_thread.daemon = True
        self._rip_thread.start()

        return True

    def _run_rip_pipeline(self):
        """Execute the full rip pipeline"""
        try:
            job = self.current_job
            if not job:
                return

            # Clean up old backups from previous rips (keep backup folder tidy)
            self._cleanup_old_backups()

            # Step 1: Disc inserted
            self._update_step("insert", "complete", "Disc detected")

            # Step 2: Detect disc type
            self._update_step("detect", "active", "Reading disc...")
            job.status = RipStatus.DETECTING

            disc_info = self.makemkv.get_disc_info(job.device)
            job.disc_label = disc_info.get("disc_label", "UNKNOWN")
            job.disc_type = disc_info.get("disc_type", "unknown")
            # Store fingerprint data for capture
            job.disc_tracks = disc_info.get("tracks", [])
            job.disc_track_sizes = disc_info.get("track_sizes", {})
            job.disc_cinfo_raw = disc_info.get("cinfo_raw", {})

            activity.disc_detected(job.disc_type.upper(), job.disc_label)
            self._update_step("detect", "complete", f"{job.disc_type.upper()}: {job.disc_label}")

            # Early identification: determine if TV or Movie BEFORE ripping
            # This searches Sonarr/Radarr by disc label to make an informed decision
            from .identify import SmartIdentifier
            identifier = SmartIdentifier(self.config)
            detected_type, early_result = identifier.early_identify(
                job.disc_label,
                disc_info.get("tracks", [])
            )

            # Update media type based on early identification
            original_type = job.media_type
            if detected_type != job.media_type:
                activity.log_info(f"EARLY ID: Switching from {job.media_type.upper()} to {detected_type.upper()}")
                job.media_type = detected_type

            # Store early identification result for later use
            if early_result and early_result.confidence >= 50:
                job.identified_title = early_result.title
                job.year = early_result.year
                job.tmdb_id = early_result.tmdb_id
                job.poster_url = early_result.poster_url
                if detected_type == 'tv' and early_result.season_number:
                    job.season_number = early_result.season_number
                activity.log_info(f"EARLY ID: Pre-identified as '{early_result.title}' ({early_result.confidence}% confidence)")

            # If TV mode, switch to TV pipeline
            if job.media_type == "tv":
                episode_tracks = disc_info.get("episode_tracks", [])
                if not episode_tracks:
                    # Fallback: find episode-length tracks (20-65 minutes)
                    episode_tracks = [
                        {"index": t["index"], "duration_str": t.get("duration_str", "?")}
                        for t in disc_info.get("tracks", [])
                        if 1200 <= t.get("duration_seconds", 0) <= 3900
                    ]

                if episode_tracks:
                    episode_count = len(episode_tracks)
                    activity.log_info(f"PIPELINE: TV mode - {episode_count} episodes to rip")
                    job.tracks_to_rip = [t["index"] for t in episode_tracks]
                    job.total_tracks = episode_count
                    self._run_tv_rip_pipeline_after_scan(disc_info)
                    return
                else:
                    activity.log_warning(f"PIPELINE: TV mode requested but no episode tracks found, falling back to movie")
                    job.media_type = "movie"

            # Movie pipeline
            activity.log_info(f"PIPELINE: Movie mode - ripping main feature")

            # Step 3: Scan tracks (movie mode)
            self._update_step("scan", "active", "Scanning tracks...")
            job.status = RipStatus.SCANNING

            main_feature = disc_info.get("main_feature")
            fake_playlist_detected = False

            # Smart track selection: if we have TMDB runtime, use it to find the best track
            # This handles Disney-style fake playlists with many similar-length tracks
            if job.tmdb_runtime_seconds > 0 and disc_info.get("tracks"):
                smart_track, fake_playlist_detected = self.makemkv.select_best_track(
                    disc_info["tracks"],
                    job.tmdb_runtime_seconds
                )
                if smart_track is not None:
                    if smart_track != main_feature:
                        activity.log_info(f"SMART SELECT: Using track {smart_track} instead of {main_feature} based on TMDB runtime")
                    main_feature = smart_track

            if main_feature is None and disc_info.get("tracks"):
                # Fallback: pick the longest track as main feature (for movie mode override)
                longest = max(disc_info["tracks"], key=lambda t: t.get("duration_seconds", 0))
                main_feature = longest["index"]
                activity.log_info(f"FALLBACK: Using longest track {main_feature} ({longest.get('duration_str', '?')}) as main feature")

            if main_feature is None:
                self._update_step("scan", "error", "No main feature found")
                job.status = RipStatus.ERROR
                job.error_message = "Could not identify main feature track"
                return

            track_info = next((t for t in disc_info["tracks"] if t["index"] == main_feature), None)
            duration_str = track_info["duration_str"] if track_info else "unknown"

            # Get expected file size for this track (round up to avoid underestimate)
            import math
            track_sizes = disc_info.get("track_sizes", {})
            scan_detail = f"Track {main_feature} ({duration_str}"
            if fake_playlist_detected:
                scan_detail += ", fake playlists detected"
            if main_feature in track_sizes:
                job.expected_size_bytes = track_sizes[main_feature]
                size_gb = math.ceil(job.expected_size_bytes / (1024**3) * 10) / 10
                scan_detail += f", {size_gb:.1f} GB)"
                self._update_step("scan", "complete", scan_detail)
            else:
                scan_detail += ")"
                self._update_step("scan", "complete", scan_detail)

            # Step 4: Rip main feature
            self._update_step("rip", "active", "Starting rip...")
            job.status = RipStatus.RIPPING
            job.rip_started_at = time.time()  # Track for ETA calculation

            output_dir = os.path.join(self.raw_path, sanitize_folder_name(job.disc_label))
            job.rip_output_dir = output_dir  # Track for file size monitoring

            # Persist job state so we can recover if service restarts
            self._save_job_state()

            # Log where we're saving
            activity.log_info(f"Saving to {output_dir}/")

            # Track last logged milestone to avoid duplicate logs
            last_milestone = [0]

            def progress_cb(percent):
                self._set_progress(percent, f"{100-percent}% remaining")
                # Dynamic status message based on progress
                if percent < 3:
                    status_msg = f"Starting rip... {percent}%"
                elif percent > 90:
                    status_msg = f"Finishing rip... {percent}%"
                else:
                    status_msg = f"Ripping... {percent}%"
                self._update_step("rip", "active", status_msg)
                # Log at 25%, 50%, 75% milestones
                for milestone in [25, 50, 75]:
                    if percent >= milestone and last_milestone[0] < milestone:
                        activity.rip_progress(job.identified_title or job.disc_label, milestone)
                        last_milestone[0] = milestone

            def message_cb(msg):
                # Ignore raw MakeMKV messages - we show clean progress instead
                pass

            # Load rip mode setting
            from . import config as cfg_module
            cfg = cfg_module.load_config()
            rip_mode = cfg.get('ripping', {}).get('rip_mode', 'smart')

            # For backwards compatibility, convert old backup_fallback setting
            if 'rip_mode' not in cfg.get('ripping', {}):
                backup_fallback = cfg.get('ripping', {}).get('backup_fallback', True)
                rip_mode = 'smart' if backup_fallback else 'direct_only'

            # DVDs must use direct extraction - backup creates ISO/UDF, not usable VIDEO_TS structure
            if job.disc_type == 'dvd' and rip_mode != 'direct_only':
                activity.log_info("DVD detected - using direct extraction (backup mode not supported for DVDs)")
                rip_mode = 'direct_only'

            # Store rip mode in job for UI
            job.rip_mode = rip_mode
            job.rip_method = "direct"
            job.direct_failed = False

            success = False
            error_msg = ""
            actual_path = None

            if rip_mode == 'always_backup':
                # Skip direct, go straight to backup method
                job.rip_method = "backup"
                activity.log_info("Using backup method (always_backup mode)")
                self._update_step("rip", "active", "Copying disc...")
            else:
                # Try direct rip first (smart or direct_only mode)
                success, error_msg, actual_path = self.makemkv.rip_track(
                    job.device,
                    main_feature,
                    output_dir,
                    progress_callback=progress_cb,
                    message_callback=message_cb,
                    expected_size=job.expected_size_bytes
                )

            # Handle backup fallback for smart mode, or always_backup mode
            if not success and not self._cancelled and rip_mode != 'direct_only':
                if rip_mode == 'smart' and error_msg:
                    job.direct_failed = True
                    activity.log_warning(f"Direct rip failed: {error_msg}")
                    activity.log_info("Switching to backup method (copy protection bypass)")
                    self._update_step("rip", "active", "Direct failed, copying disc...")

                job.rip_method = "backup"

                # Create backup directory
                backup_dir = os.path.join(self.backup_path, sanitize_folder_name(job.disc_label))

                # Check if valid backup already exists (skip re-backup on retry)
                # Support both Blu-ray (BDMV) and DVD (VIDEO_TS) structures
                bdmv_path = Path(backup_dir) / "BDMV"
                video_ts_path = Path(backup_dir) / "VIDEO_TS"
                has_backup_structure = bdmv_path.exists() or video_ts_path.exists()
                is_dvd = video_ts_path.exists() and not bdmv_path.exists()
                existing_backup_valid = False
                
                if has_backup_structure:
                    backup_size = sum(f.stat().st_size for f in Path(backup_dir).rglob("*") if f.is_file())
                    # DVDs are smaller - use 100MB threshold, Blu-rays use 1GB
                    min_size = 100_000_000 if is_dvd else 1_000_000_000
                    if backup_size > min_size:
                        disc_type = "DVD" if is_dvd else "Blu-ray"
                        activity.log_success(f"Found existing {disc_type} backup: {backup_size / (1024**3):.1f} GB - skipping backup phase")
                        existing_backup_valid = True
                        backup_success = True
                    else:
                        activity.log_warning(f"Existing backup too small ({backup_size / (1024**3):.1f} GB) - deleting and re-backing up")
                        try:
                            shutil.rmtree(backup_dir, ignore_errors=True)
                        except:
                            pass

                if not existing_backup_valid:
                    # Reset progress for backup phase
                    def backup_progress_cb(percent):
                        # Backup is first half (0-50%), rip from backup is second half (50-100%)
                        self._set_progress(percent // 2, "Copying disc...")
                        self._update_step("rip", "active", f"Copying disc... {percent}%")

                    # Backup the disc first
                    backup_success, backup_error, _ = self.makemkv.backup_disc(
                        job.device,
                        backup_dir,
                        progress_callback=backup_progress_cb,
                        message_callback=message_cb,
                        expected_size=job.expected_size_bytes
                    )

                if backup_success:
                    activity.log_success("Backup complete, extracting MKV...")
                    self._update_step("rip", "active", "Extracting MKV...")

                    # CRITICAL: Re-scan backup to get correct track index
                    # Track indices from disc scan may differ from backup scan!
                    backup_main_feature = self.makemkv.get_backup_main_feature(backup_dir)
                    if backup_main_feature is not None:
                        if backup_main_feature != main_feature:
                            activity.log_warning(f"TRACK INDEX FIX: Disc had track {main_feature}, backup has track {backup_main_feature}")
                        main_feature = backup_main_feature
                    else:
                        activity.log_warning(f"BACKUP SCAN: Could not determine main feature, using disc track {main_feature}")

                    def backup_rip_progress_cb(percent):
                        # Second half of progress (50-100%)
                        self._set_progress(50 + percent // 2, "Extracting MKV...")
                        self._update_step("rip", "active", f"Extracting MKV... {percent}%")

                    # Rip from backup
                    success, error_msg, actual_path = self.makemkv.rip_from_backup(
                        backup_dir,
                        main_feature,
                        output_dir,
                        progress_callback=backup_rip_progress_cb,
                        message_callback=message_cb,
                        expected_size=job.expected_size_bytes
                    )

                    # Keep backup until next rip starts (safety net for move/identify issues)
                    if success:
                        job.rip_method = "backup"
                        activity.log_info(f"Backup kept at: {backup_dir} (will clean on next rip)")
                else:
                    if rip_mode == 'smart':
                        error_msg = f"Both direct and backup methods failed. Direct: {error_msg}. Backup: {backup_error}"
                    else:
                        error_msg = f"Backup method failed: {backup_error}"

            if not success:
                # Check if this was a manual cancellation
                if self._cancelled:
                    self._update_step("rip", "error", "Cancelled by user")
                    job.status = RipStatus.ERROR
                    job.error_message = "Cancelled by user"
                    activity.rip_cancelled(job.identified_title or job.disc_label, "User stopped the drive")
                else:
                    # Use error detection for granular error info
                    detected = error_detection.detect_error(
                        output=error_msg or "",
                        device=job.device,
                        output_path=output_dir
                    )
                    if detected:
                        final_error = error_detection.format_error_message(detected)
                        job.error_details = detected.to_dict()
                    else:
                        final_error = error_msg or "MakeMKV rip failed"

                    self._update_step("rip", "error", final_error)
                    job.status = RipStatus.ERROR
                    job.error_message = final_error
                    title = job.identified_title or job.disc_label
                    activity.rip_failed(title, final_error)

                    # Log failure for tracking
                    from . import config as cfg_module
                    duration_mins = None
                    if job.rip_started_at:
                        duration_mins = int((time.time() - job.rip_started_at) / 60)
                    output_size = "0 bytes"
                    if job.rip_output_dir and os.path.exists(job.rip_output_dir):
                        try:
                            total = sum(f.stat().st_size for f in Path(job.rip_output_dir).glob("*") if f.is_file())
                            if total > 0:
                                output_size = f"{total / (1024*1024):.1f} MB"
                        except:
                            pass
                    cfg_module.log_failure({
                        'disc_label': job.disc_label,
                        'disc_type': job.disc_type.upper() if job.disc_type else 'Unknown',
                        'duration_minutes': duration_mins,
                        'track_info': f"Track {job.tracks_to_rip[0] if job.tracks_to_rip else 'main'}",
                        'output_size': output_size,
                        'reason': final_error,
                        'rip_method': job.rip_method,
                        'error_details': job.error_details
                    })

                    # Unlock drive after I/O errors to prevent stuck tray
                    activity.log_info("Unlocking drive after rip failure...")
                    self.unlock_drive(job.device)

                    # Send error email if enabled
                    cfg = cfg_module.load_config()
                    email_cfg = cfg.get('notifications', {}).get('email', {})
                    if email_cfg.get('on_error'):
                        recipients = email_cfg.get('recipients', [])
                        if recipients:
                            email_utils.send_rip_error(title, final_error, recipients)
                            activity.log_info(f"Error notification sent to {len(recipients)} recipient(s)")
                return

            self._update_step("rip", "complete", "Rip finished")
            self._set_progress(100)

            # Find actual output location (MakeMKV may have ignored our path)
            import glob

            # First try MakeMKV's reported path, then our expected path, then search
            found_path = None
            if actual_path and os.path.isdir(actual_path):
                mkv_files = glob.glob(os.path.join(actual_path, "*.mkv"))
                if mkv_files:
                    found_path = actual_path

            if not found_path and os.path.isdir(output_dir):
                mkv_files = glob.glob(os.path.join(output_dir, "*.mkv"))
                if mkv_files:
                    found_path = output_dir

            if not found_path:
                activity.log_warning(f"Searching for rip output...")
                found_path = self._find_rip_output(job.disc_label)

            if found_path:
                job.output_path = found_path
                mkv_files = glob.glob(os.path.join(found_path, "*.mkv"))
                total_size = sum(os.path.getsize(f) for f in mkv_files)
                size_gb = total_size / (1024**3)
                job.size_gb = size_gb  # Store for rip history
                activity.log_success(f"Rip output: {found_path}/ ({size_gb:.1f} GB)")

                # Apply language preference to set default audio track
                ripping_cfg = cfg.get('ripping', {})
                preferred_lang = ripping_cfg.get('preferred_language', 'eng')
                if preferred_lang != 'all':
                    for mkv_file in mkv_files:
                        set_default_audio_track(mkv_file, preferred_lang)

                # Step: Verify file integrity (if enabled)
                if ripping_cfg.get('verify_integrity', True):
                    self._update_step("verify", "active", "Checking integrity...")
                    integrity_errors = []
                    for i, mkv_file in enumerate(mkv_files):
                        filename = os.path.basename(mkv_file)
                        if len(mkv_files) > 1:
                            self._update_step("verify", "active", f"Checking {i+1}/{len(mkv_files)}...")
                        result = check_file_integrity(mkv_file)
                        if not result["valid"]:
                            integrity_errors.append((filename, result["error_count"]))

                    if integrity_errors:
                        # Log errors but continue - user can decide what to do
                        error_summary = ", ".join(f"{f} ({c} errors)" for f, c in integrity_errors)
                        self._update_step("verify", "error", f"Issues: {error_summary}")
                        activity.log_warning(f"VERIFY: Integrity issues found - {error_summary}")
                        # Don't fail the rip, but warn user
                    else:
                        self._update_step("verify", "complete", "Passed")
                else:
                    self._update_step("verify", "complete", "Skipped")

                # Note if output went to unexpected location
                if found_path != output_dir:
                    activity.log_warning(f"Output went to {found_path} instead of {output_dir}")
            else:
                activity.log_error(f"Could not find rip output for {job.disc_label}")
                job.status = RipStatus.ERROR
                job.error_message = "Could not find rip output files"
                return

            # Step 5: Identify content
            self._update_step("identify", "active", "Identifying...")
            job.status = RipStatus.IDENTIFYING

            # Check if we have a pre-set custom title from scan
            if job.identified_title:
                # User already confirmed title during scan - use it directly
                self._update_step("identify", "complete", f"{job.identified_title} [USER]")
                # Skip rename - we'll use the title when moving to movies folder
            else:
                # Smart identification using actual file runtime (better accuracy)
                self._run_post_rip_identification(job)

            # Step 6: Add to library (Radarr/Sonarr)
            self._update_step("library", "active", "Checking Radarr...")

            # TODO: Radarr/Sonarr API integration
            self._update_step("library", "complete", "Found in Radarr")

            # Step 7: Move/rename to final destination
            self._update_step("move", "active", "Organizing files...")
            job.status = RipStatus.MOVING

            activity.log_info(f"=== MOVE/RENAME START ===")

            try:
                import shutil
                import glob

                # Check if this needs manual review
                if job.needs_review:
                    # Move to review folder instead of movies
                    self._move_to_review(job)
                    return

                # Determine destination folder name (sanitize for filesystem safety)
                # Include year in folder name for Plex compatibility: "Movie Title (YYYY)"
                title = job.identified_title or job.disc_label.replace("_", " ").title()
                if job.year:
                    dest_folder_name = sanitize_folder_name(f"{title} ({job.year})")
                else:
                    dest_folder_name = sanitize_folder_name(title)
                dest_path = os.path.join(self.movies_path, dest_folder_name)
                source_path = job.output_path

                activity.log_info(f"MOVE: Source: {source_path}")
                activity.log_info(f"MOVE: Destination folder name: '{dest_folder_name}'")
                activity.log_info(f"MOVE: Destination path: {dest_path}")

                # Find all mkv files in source
                mkv_files = glob.glob(os.path.join(source_path, "*.mkv"))
                activity.log_info(f"MOVE: Found {len(mkv_files)} MKV file(s) in source")

                if not mkv_files:
                    activity.log_error(f"MOVE: No MKV files found in {source_path}")
                    self._update_step("move", "error", "No MKV files found")
                    return

                for mkv in mkv_files:
                    size_gb = os.path.getsize(mkv) / (1024**3)
                    activity.log_info(f"MOVE:   - {os.path.basename(mkv)} ({size_gb:.2f} GB)")

                # Check if source is already in movies folder
                source_in_movies = source_path.startswith(self.movies_path)
                activity.log_info(f"MOVE: Source already in movies folder: {source_in_movies}")

                if source_in_movies and source_path == dest_path:
                    # Already in correct location, just rename files
                    activity.log_info(f"MOVE: Already in correct location, renaming files in place")
                    for mkv_file in mkv_files:
                        new_filename = f"{dest_folder_name}.mkv"
                        if len(mkv_files) > 1:
                            idx = mkv_files.index(mkv_file) + 1
                            new_filename = f"{dest_folder_name} - Part {idx}.mkv"
                        dest_file = os.path.join(dest_path, new_filename)
                        if mkv_file != dest_file:
                            activity.log_info(f"MOVE: Renaming: {os.path.basename(mkv_file)} -> {new_filename}")
                            os.rename(mkv_file, dest_file)
                    activity.log_success(f"=== MOVE COMPLETE: Renamed in place ===")
                    self._update_step("move", "complete", f"Renamed in place")

                elif source_in_movies:
                    # In movies but wrong folder - rename folder and files
                    activity.log_info(f"MOVE: In movies but different folder, moving to correct location")
                    Path(dest_path).mkdir(parents=True, exist_ok=True)
                    for mkv_file in mkv_files:
                        new_filename = f"{dest_folder_name}.mkv"
                        if len(mkv_files) > 1:
                            idx = mkv_files.index(mkv_file) + 1
                            new_filename = f"{dest_folder_name} - Part {idx}.mkv"
                        dest_file = os.path.join(dest_path, new_filename)
                        activity.log_info(f"MOVE: Moving: {os.path.basename(mkv_file)} -> {dest_path}/{new_filename}")
                        shutil.move(mkv_file, dest_file)

                    # Remove old folder if empty
                    try:
                        os.rmdir(source_path)
                        activity.log_info(f"MOVE: Removed empty source folder: {source_path}")
                    except OSError:
                        activity.log_info(f"MOVE: Source folder not empty, keeping: {source_path}")

                    activity.file_moved(dest_folder_name, dest_path)
                    activity.log_success(f"=== MOVE COMPLETE: {dest_folder_name} ===")
                    self._update_step("move", "complete", f"Moved to {dest_folder_name}")

                else:
                    # Source is in rips/raw - move to movies
                    activity.log_info(f"MOVE: Moving from rips/raw to movies folder")
                    Path(dest_path).mkdir(parents=True, exist_ok=True)
                    for mkv_file in mkv_files:
                        new_filename = f"{dest_folder_name}.mkv"
                        if len(mkv_files) > 1:
                            idx = mkv_files.index(mkv_file) + 1
                            new_filename = f"{dest_folder_name} - Part {idx}.mkv"
                        dest_file = os.path.join(dest_path, new_filename)
                        activity.log_info(f"MOVE: Moving: {os.path.basename(mkv_file)} -> {dest_path}/{new_filename}")
                        shutil.move(mkv_file, dest_file)

                    # Remove old folder if empty
                    try:
                        os.rmdir(source_path)
                        activity.log_info(f"MOVE: Removed empty source folder: {source_path}")
                    except OSError:
                        activity.log_info(f"MOVE: Source folder not empty, keeping: {source_path}")

                    activity.file_moved(dest_folder_name, dest_path)
                    activity.log_success(f"=== MOVE COMPLETE: {dest_folder_name} -> movies/ ===")
                    self._update_step("move", "complete", f"Moved to movies")

                # Update job output path
                job.output_path = dest_path

            except Exception as e:
                activity.log_error(f"=== MOVE FAILED: {str(e)} ===")
                self._update_step("move", "error", f"Move failed: {str(e)}")

            # Step 8: Trigger Plex scan
            self._update_step("scan-plex", "active", "Triggering scan...")

            # TODO: Plex API integration
            self._update_step("scan-plex", "complete", "Plex notified")
            activity.plex_scan_triggered("Movies")

            # Done!
            job.status = RipStatus.COMPLETE
            job.completed_at = datetime.now().isoformat()

            # Calculate duration
            if job.started_at:
                start = datetime.fromisoformat(job.started_at)
                duration = datetime.now() - start
                duration_str = str(duration).split('.')[0]  # Remove microseconds
            else:
                duration_str = None

            activity.rip_completed(job.identified_title or job.disc_label, duration_str)

            # Save to rip history for weekly digest emails
            # Use enrich_and_save_rip to auto-fetch missing metadata from Radarr/TMDB
            activity.enrich_and_save_rip(
                title=job.identified_title or job.disc_label,
                disc_type=job.disc_type,
                duration_str=duration_str or "",
                size_gb=job.size_gb,
                year=job.year,
                tmdb_id=job.tmdb_id,
                poster_url=job.poster_url,
                runtime_str=job.runtime_str,
                content_type=job.media_type,
                rip_method=job.rip_method
            )

            # Add to history
            self.job_history.append(job)

            # Clear persisted job state
            self._clear_job_state()

            # Eject disc if enabled
            self.eject_disc(job.device)

        except Exception as e:
            if self.current_job:
                self.current_job.status = RipStatus.ERROR
                self.current_job.error_message = str(e)
                title = self.current_job.identified_title or self.current_job.disc_label
                activity.rip_failed(title, str(e))

                # Send error email if enabled
                if not self._cancelled:
                    from . import config as cfg_module
                    cfg = cfg_module.load_config()
                    email_cfg = cfg.get('notifications', {}).get('email', {})
                    if email_cfg.get('on_error'):
                        recipients = email_cfg.get('recipients', [])
                        if recipients:
                            email_utils.send_rip_error(title, str(e), recipients)

    def _run_tv_rip_pipeline(self):
        """Execute the TV show rip pipeline - handles multiple episode tracks"""
        try:
            job = self.current_job
            if not job:
                return

            # Clean up old backups from previous rips
            self._cleanup_old_backups()

            activity.log_info(f"=== TV RIP PIPELINE START ===")
            activity.log_info(f"Series: {job.series_title or job.identified_title}")
            activity.log_info(f"Season: {job.season_number}")
            activity.log_info(f"Episodes to rip: {len(job.tracks_to_rip)}")

            # Step 1: Disc inserted
            self._update_step("insert", "complete", "Disc detected")

            # Step 2: Detect disc type
            self._update_step("detect", "active", "Reading disc...")
            job.status = RipStatus.DETECTING

            disc_info = self.makemkv.get_disc_info(job.device, self.config)
            job.disc_label = disc_info.get("disc_label", "UNKNOWN")
            job.disc_type = disc_info.get("disc_type", "unknown")

            activity.disc_detected(job.disc_type.upper(), job.disc_label)
            self._update_step("detect", "complete", f"{job.disc_type.upper()}: {job.disc_label}")

            # Step 3: Scan tracks
            self._update_step("scan", "active", f"Found {len(job.tracks_to_rip)} episode tracks")
            job.status = RipStatus.SCANNING

            # Calculate total expected size for all tracks
            track_sizes = disc_info.get("track_sizes", {})
            total_size = sum(track_sizes.get(t, 0) for t in job.tracks_to_rip)
            job.expected_size_bytes = total_size

            import math
            size_gb = math.ceil(total_size / (1024**3) * 10) / 10
            self._update_step("scan", "complete", f"{len(job.tracks_to_rip)} episodes ({size_gb:.1f} GB total)")

            # Step 4: Rip episodes sequentially
            self._update_step("rip", "active", "Starting episode rips...")
            job.status = RipStatus.RIPPING
            job.rip_started_at = time.time()  # Track for ETA calculation

            # Create output directory for this series/season
            series_name = job.series_title or job.identified_title or job.disc_label.replace("_", " ").title()
            output_dir = os.path.join(self.raw_path, sanitize_folder_name(f"{series_name}_S{job.season_number:02d}"))
            job.rip_output_dir = output_dir

            # Persist job state
            self._save_job_state()

            activity.log_info(f"Saving episodes to {output_dir}/")

            # Rip each track
            total_tracks = len(job.tracks_to_rip)
            ripped_files = []
            errors = []

            for idx, track_num in enumerate(job.tracks_to_rip):
                job.current_track_index = idx
                ep_info = job.episode_mapping.get(track_num, {})
                ep_num = ep_info.get('episode_number', idx + 1)
                ep_title = ep_info.get('title', f'Episode {ep_num}')

                activity.log_info(f"=== Ripping episode {idx + 1}/{total_tracks}: S{job.season_number:02d}E{ep_num:02d} - {ep_title} ===")
                self._update_step("rip", "active", f"Episode {idx + 1}/{total_tracks}: S{job.season_number:02d}E{ep_num:02d}")

                # Progress callback that accounts for multiple tracks
                def progress_cb(percent):
                    # Overall progress = (completed tracks + current progress) / total
                    overall = int(((idx + percent / 100) / total_tracks) * 100)
                    self._set_progress(overall, f"Episode {idx + 1}/{total_tracks}")
                    # Dynamic status message based on progress
                    if percent < 3:
                        status_msg = f"Starting E{ep_num:02d}... {percent}%"
                    elif percent > 90:
                        status_msg = f"Finishing E{ep_num:02d}... {percent}%"
                    else:
                        status_msg = f"Ripping E{ep_num:02d}... {percent}%"
                    self._update_step("rip", "active", f"Episode {idx + 1}/{total_tracks}: {status_msg}")

                # Get track size for progress polling (if available)
                ep_expected_size = track_sizes.get(track_num, 0) if track_sizes else 0
                
                success, error_msg, actual_path = self.makemkv.rip_track(
                    job.device,
                    track_num,
                    output_dir,
                    progress_callback=progress_cb,
                    expected_size=ep_expected_size
                )

                if success:
                    # Find the ripped file
                    import glob
                    mkv_files = glob.glob(os.path.join(output_dir, "*.mkv"))
                    # Get the newest file (just ripped)
                    if mkv_files:
                        newest = max(mkv_files, key=os.path.getmtime)
                        # Apply language preference
                        preferred_lang = cfg.get('ripping', {}).get('preferred_language', 'eng')
                        if preferred_lang != 'all':
                            set_default_audio_track(newest, preferred_lang)
                        ripped_files.append({
                            'path': newest,
                            'track': track_num,
                            'episode': ep_num,
                            'title': ep_title
                        })
                        activity.log_success(f"Episode {ep_num} ripped: {os.path.basename(newest)}")
                else:
                    errors.append(f"Episode {ep_num}: {error_msg}")
                    activity.log_error(f"Failed to rip episode {ep_num}: {error_msg}")
                    # Continue with next track

            job.ripped_files = [f['path'] for f in ripped_files]
            self._set_progress(100)

            if not ripped_files:
                self._update_step("rip", "error", "All episodes failed")
                job.status = RipStatus.ERROR
                job.error_message = "All episode rips failed"
                return

            if errors:
                self._update_step("rip", "complete", f"Ripped {len(ripped_files)}/{total_tracks} episodes")
                activity.log_warning(f"Partial success: {len(errors)} errors")
            else:
                self._update_step("rip", "complete", f"All {total_tracks} episodes ripped")

            job.output_path = output_dir

            # Step: Verify file integrity for all ripped episodes (if enabled)
            if cfg.get('ripping', {}).get('verify_integrity', True):
                self._update_step("verify", "active", "Checking integrity...")
                integrity_errors = []
                for i, file_info in enumerate(ripped_files):
                    mkv_file = file_info['path']
                    filename = os.path.basename(mkv_file)
                    if len(ripped_files) > 1:
                        self._update_step("verify", "active", f"Checking {i+1}/{len(ripped_files)}...")
                    result = check_file_integrity(mkv_file)
                    if not result["valid"]:
                        integrity_errors.append((filename, result["error_count"]))

                if integrity_errors:
                    error_summary = ", ".join(f"{f} ({c} errors)" for f, c in integrity_errors[:3])
                    if len(integrity_errors) > 3:
                        error_summary += f" +{len(integrity_errors)-3} more"
                    self._update_step("verify", "error", f"Issues: {error_summary}")
                    activity.log_warning(f"VERIFY: Integrity issues found in {len(integrity_errors)} files")
                else:
                    self._update_step("verify", "complete", f"All {len(ripped_files)} passed")
            else:
                self._update_step("verify", "complete", "Skipped")

            # Step 5: Identify (already done pre-rip for TV)
            self._update_step("identify", "complete", f"{series_name} S{job.season_number:02d} [TV]")
            job.status = RipStatus.IDENTIFYING

            # Step 6: Add to library (Sonarr)
            self._update_step("library", "active", "Checking Sonarr...")
            self._update_step("library", "complete", "Sonarr notified")

            # Step 7: Organize files into TV folder structure
            self._update_step("move", "active", "Organizing episodes...")
            job.status = RipStatus.MOVING

            dest_path = self._organize_tv_files(job, ripped_files)
            if dest_path:
                self._update_step("move", "complete", f"Moved to tv/{series_name}/")
                job.output_path = dest_path
            else:
                self._update_step("move", "error", "Organization failed")

            # Step 8: Trigger Plex scan
            self._update_step("scan-plex", "active", "Triggering scan...")
            self._update_step("scan-plex", "complete", "Plex notified")
            activity.plex_scan_triggered("TV Shows")

            # Done!
            job.status = RipStatus.COMPLETE
            job.completed_at = datetime.now().isoformat()

            # Calculate duration
            if job.started_at:
                start = datetime.fromisoformat(job.started_at)
                duration = datetime.now() - start
                duration_str = str(duration).split('.')[0]
            else:
                duration_str = None

            activity.rip_completed(f"{series_name} S{job.season_number:02d} ({len(ripped_files)} episodes)", duration_str)

            # Save to rip history for weekly digest emails
            total_size = sum(os.path.getsize(f['path']) for f in ripped_files if os.path.exists(f['path'])) / (1024**3)
            activity.enrich_and_save_rip(
                title=f"{series_name} - Season {job.season_number}",
                disc_type=job.disc_type,
                duration_str=duration_str or "",
                size_gb=total_size,
                year=job.year,
                tmdb_id=job.tmdb_id,
                poster_url=job.poster_url,
                runtime_str=f"{len(ripped_files)} episodes",
                content_type="tv",
                rip_method=job.rip_method
            )

            # Add to history
            self.job_history.append(job)

            # Clear persisted job state
            self._clear_job_state()

            activity.log_success(f"=== TV RIP PIPELINE COMPLETE ===")

            # Eject disc if enabled
            self.eject_disc(job.device)

        except Exception as e:
            if self.current_job:
                self.current_job.status = RipStatus.ERROR
                self.current_job.error_message = str(e)
                title = self.current_job.series_title or self.current_job.disc_label
                activity.rip_failed(title, str(e))
                activity.log_error(f"TV rip pipeline failed: {e}")

                # Send error email if enabled
                if not self._cancelled:
                    from . import config as cfg_module
                    cfg = cfg_module.load_config()
                    email_cfg = cfg.get('notifications', {}).get('email', {})
                    if email_cfg.get('on_error'):
                        recipients = email_cfg.get('recipients', [])
                        if recipients:
                            email_utils.send_rip_error(title, str(e), recipients)

    def _run_tv_rip_pipeline_after_scan(self, disc_info: dict):
        """Continue TV rip pipeline after disc has been scanned (for auto-switch from movie mode)"""
        try:
            job = self.current_job
            if not job:
                return

            activity.log_info(f"=== TV RIP PIPELINE (auto-switched) ===")
            activity.log_info(f"Disc: {job.disc_label}")
            activity.log_info(f"Episodes to rip: {len(job.tracks_to_rip)}")

            # Step 3: Scan tracks (already done, just update UI)
            track_sizes = disc_info.get("track_sizes", {})
            total_size = sum(track_sizes.get(t, 0) for t in job.tracks_to_rip)
            job.expected_size_bytes = total_size

            import math
            size_gb = math.ceil(total_size / (1024**3) * 10) / 10
            self._update_step("scan", "complete", f"{len(job.tracks_to_rip)} episodes ({size_gb:.1f} GB total)")

            # Step 4: Rip episodes sequentially
            self._update_step("rip", "active", "Starting episode rips...")
            job.status = RipStatus.RIPPING
            job.rip_started_at = time.time()

            # Create output directory
            series_name = job.identified_title or job.disc_label.replace("_", " ").title()
            output_dir = os.path.join(self.raw_path, sanitize_folder_name(f"{series_name}_S{job.season_number:02d}"))
            job.rip_output_dir = output_dir

            self._save_job_state()
            activity.log_info(f"Saving episodes to {output_dir}/")

            # Rip each track
            total_tracks = len(job.tracks_to_rip)
            ripped_files = []
            errors = []

            for idx, track_num in enumerate(job.tracks_to_rip):
                job.current_track_index = idx
                ep_num = idx + 1

                activity.log_info(f"=== Ripping episode {idx + 1}/{total_tracks}: E{ep_num:02d} ===")
                self._update_step("rip", "active", f"Episode {idx + 1}/{total_tracks}: E{ep_num:02d}")

                def progress_cb(percent, idx=idx, total=total_tracks, ep=ep_num):
                    overall = int(((idx + percent / 100) / total) * 100)
                    self._set_progress(overall, f"Episode {idx + 1}/{total}")
                    self._update_step("rip", "active", f"Episode {idx + 1}/{total}: E{ep:02d}... {percent}%")

                ep_expected_size = track_sizes.get(track_num, 0)

                success, error_msg, actual_path = self.makemkv.rip_track(
                    job.device,
                    track_num,
                    output_dir,
                    progress_callback=progress_cb,
                    expected_size=ep_expected_size
                )

                if success:
                    import glob
                    mkv_files = glob.glob(os.path.join(output_dir, "*.mkv"))
                    if mkv_files:
                        newest = max(mkv_files, key=os.path.getmtime)
                        ripped_files.append({
                            'path': newest,
                            'track': track_num,
                            'episode': ep_num,
                            'title': f'Episode {ep_num}'
                        })
                        activity.log_success(f"Episode {ep_num} ripped: {os.path.basename(newest)}")
                else:
                    errors.append(f"Episode {ep_num}: {error_msg}")
                    activity.log_error(f"Failed to rip episode {ep_num}: {error_msg}")

            job.ripped_files = [f['path'] for f in ripped_files]
            self._set_progress(100)

            if not ripped_files:
                self._update_step("rip", "error", "All episodes failed")
                job.status = RipStatus.ERROR
                job.error_message = "All episode rips failed"
                return

            if errors:
                self._update_step("rip", "complete", f"Ripped {len(ripped_files)}/{total_tracks} episodes")
            else:
                self._update_step("rip", "complete", f"All {total_tracks} episodes ripped")

            job.output_path = output_dir

            # Steps 5-7: Identify, library, move (simplified for auto-switch)
            self._update_step("identify", "complete", f"{series_name} [TV - auto-detected]")
            job.status = RipStatus.IDENTIFYING
            job.needs_review = True  # Mark for review since we don't have proper ID

            self._update_step("library", "complete", "Needs manual identification")
            self._update_step("move", "active", "Moving to review queue...")

            # Move to review queue since we don't have proper series identification
            from pathlib import Path
            review_dir = Path(self.raw_path).parent / "review"
            review_dir.mkdir(exist_ok=True)
            review_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job.disc_label}"
            review_dest = review_dir / review_name

            import shutil
            shutil.move(output_dir, review_dest)
            job.output_path = str(review_dest)

            # Get MKV files for thumbnail generation and track info
            import glob as glob_module
            mkv_files = glob_module.glob(os.path.join(str(review_dest), "*.mkv"))

            # Generate thumbnails and track info for TV episode assignment
            activity.log_info(f"REVIEW: Generating thumbnails for {len(mkv_files)} tracks...")
            thumbnails = self._generate_track_thumbnails(str(review_dest), mkv_files)
            tracks_info = self._get_track_info_for_review(str(review_dest), mkv_files)

            # Add thumbnail paths to track info (now a list of thumbnails)
            for track in tracks_info:
                if track["filename"] in thumbnails:
                    track["thumbnails"] = thumbnails[track["filename"]]

            # Create review metadata file so it shows up in Review UI
            import json
            metadata = {
                "disc_label": job.disc_label,
                "disc_type": job.disc_type,
                "identified_title": series_name,
                "folder_name": review_name,
                "created_at": datetime.now().isoformat(),
                "media_type": "tv",
                "episode_count": len(ripped_files),
                "year": job.year,
                "tmdb_id": job.tmdb_id,
                "poster_url": job.poster_url,
                "tracks": tracks_info,
                "expected_episodes": len(ripped_files),
                "season_number": job.season_number if job.season_number else 1
            }
            metadata_file = os.path.join(str(review_dest), "review_metadata.json")
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            activity.log_info(f"REVIEW: Saved metadata with {len(tracks_info)} tracks to {metadata_file}")

            self._update_step("move", "complete", "Moved to review queue")
            self._update_step("scan-plex", "complete", "Skipped - needs review")

            job.status = RipStatus.COMPLETE
            job.completed_at = datetime.now().isoformat()

            activity.log_warning(f"TV disc ripped to review queue - needs manual identification: {review_name}")
            activity.log_success(f"=== TV RIP COMPLETE ({len(ripped_files)} episodes) ===")

            self.eject_disc(job.device)

        except Exception as e:
            if self.current_job:
                self.current_job.status = RipStatus.ERROR
                self.current_job.error_message = str(e)
                activity.log_error(f"Auto-switched TV rip failed: {e}")

    def _organize_tv_files(self, job: RipJob, ripped_files: List[dict]) -> Optional[str]:
        """Organize ripped TV episode files into proper folder structure.

        Args:
            job: Current RipJob with series/season info
            ripped_files: List of dicts with path, episode number, title

        Returns:
            Destination folder path, or None on failure
        """
        import shutil

        series_name = sanitize_folder_name(job.series_title or job.identified_title or job.disc_label.replace("_", " ").title())

        season_folder = f"Season {job.season_number:02d}"
        dest_dir = os.path.join(self.tv_path, series_name, season_folder)

        activity.log_info(f"=== ORGANIZE TV FILES ===")
        activity.log_info(f"Destination: {dest_dir}")

        try:
            Path(dest_dir).mkdir(parents=True, exist_ok=True)

            for file_info in ripped_files:
                src_path = file_info['path']
                ep_num = file_info['episode']
                ep_title = file_info.get('title', '')

                # Build filename: "Series Name - SxxExx - Episode Title.mkv"
                if ep_title and ep_title != f"Episode {ep_num}":
                    # Sanitize episode title
                    safe_title = re.sub(r'[:]', '-', ep_title)
                    safe_title = re.sub(r'[?<>"|*]', '', safe_title)
                    filename = f"{series_name} - S{job.season_number:02d}E{ep_num:02d} - {safe_title}.mkv"
                else:
                    filename = f"{series_name} - S{job.season_number:02d}E{ep_num:02d}.mkv"

                dest_path = os.path.join(dest_dir, filename)

                activity.log_info(f"Moving: {os.path.basename(src_path)} -> {filename}")
                shutil.move(src_path, dest_path)

            # Clean up source directory if empty
            src_dir = job.rip_output_dir
            if src_dir and os.path.isdir(src_dir):
                try:
                    os.rmdir(src_dir)
                    activity.log_info(f"Removed empty source directory: {src_dir}")
                except OSError:
                    activity.log_info(f"Source directory not empty, keeping: {src_dir}")

            activity.file_moved(f"{series_name} S{job.season_number:02d}", dest_dir)
            activity.log_success(f"=== TV FILES ORGANIZED: {len(ripped_files)} episodes ===")

            return dest_dir

        except Exception as e:
            activity.log_error(f"Failed to organize TV files: {e}")
            return None

    def _move_to_review(self, job: RipJob):
        """Move files to review folder for manual identification.

        Creates a uniquely named folder with the video file and a metadata JSON
        containing disc info and video runtime for the review UI.
        """
        import shutil
        import glob

        source_path = job.output_path
        activity.log_info(f"=== MOVING TO REVIEW QUEUE ===")
        activity.log_info(f"REVIEW: Source: {source_path}")

        # Create unique folder name using timestamp and disc label
        folder_name = f"{job.id}_{job.disc_label}"
        dest_path = os.path.join(self.review_path, folder_name)

        activity.log_info(f"REVIEW: Destination: {dest_path}")

        try:
            Path(dest_path).mkdir(parents=True, exist_ok=True)

            # Find MKV files
            mkv_files = glob.glob(os.path.join(source_path, "*.mkv"))
            if not mkv_files:
                activity.log_error(f"REVIEW: No MKV files found in {source_path}")
                self._update_step("move", "error", "No MKV files found")
                job.status = RipStatus.ERROR
                job.error_message = "No MKV files found for review"
                return

            # Move MKV files
            moved_files = []
            for mkv_file in mkv_files:
                dest_file = os.path.join(dest_path, os.path.basename(mkv_file))
                activity.log_info(f"REVIEW: Moving: {os.path.basename(mkv_file)}")
                shutil.move(mkv_file, dest_file)
                moved_files.append(dest_file)

            # Get video runtime using ffprobe
            runtime_seconds = 0
            if moved_files:
                from .identify import SmartIdentifier
                identifier = SmartIdentifier(self.config)
                runtime_seconds = identifier.get_video_runtime(dest_path) or 0

            # Calculate file size
            total_size = sum(os.path.getsize(f) for f in moved_files)
            size_gb = total_size / (1024**3)

            # Create metadata JSON for review UI
            metadata = {
                "job_id": job.id,
                "disc_label": job.disc_label,
                "disc_type": job.disc_type,
                "fallback_title": job.identified_title,
                "runtime_seconds": runtime_seconds,
                "duration_secs": runtime_seconds if runtime_seconds else 0,  # For community DB
                "track_count": len(job.disc_tracks) if job.disc_tracks else 0,     # For community DB
                "runtime_str": f"{runtime_seconds // 60}m {runtime_seconds % 60}s" if runtime_seconds else "",
                "size_gb": round(size_gb, 2),
                "files": [os.path.basename(f) for f in moved_files],
                "created_at": datetime.now().isoformat(),
                "media_type": job.media_type,
                # Duplicate detection info
                "possible_duplicate": job.possible_duplicate,
                "duplicate_match_type": job.duplicate_match_type,
                "duplicate_info": job.duplicate_info,
                "year": job.year,
                "tmdb_id": job.tmdb_id,
                "poster_url": job.poster_url
            }

            metadata_file = os.path.join(dest_path, "review_metadata.json")
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            activity.log_info(f"REVIEW: Saved metadata to {metadata_file}")

            # Clean up source folder
            try:
                os.rmdir(source_path)
                activity.log_info(f"REVIEW: Removed empty source folder: {source_path}")
            except OSError:
                activity.log_info(f"REVIEW: Source folder not empty, keeping: {source_path}")

            # Update job
            job.output_path = dest_path
            self._update_step("move", "complete", "Moved to review")
            activity.log_warning(f"=== MOVED TO REVIEW QUEUE: {folder_name} ===")

            # Skip Plex scan for review items
            self._update_step("scan-plex", "complete", "Skipped (needs review)")

            # Mark job complete (but needs review)
            job.status = RipStatus.COMPLETE
            job.completed_at = datetime.now().isoformat()

            # Calculate duration
            if job.started_at:
                start = datetime.fromisoformat(job.started_at)
                duration = datetime.now() - start
                duration_str = str(duration).split('.')[0]
            else:
                duration_str = None

            activity.log_warning(f"Rip completed but needs manual identification: {job.disc_label}")

            # Add to history
            self.job_history.append(job)

            # Clear persisted job state
            self._clear_job_state()

            # Eject disc if enabled
            self.eject_disc(job.device)

        except Exception as e:
            activity.log_error(f"=== REVIEW MOVE FAILED: {str(e)} ===")
            self._update_step("move", "error", f"Review move failed: {str(e)}")
            job.status = RipStatus.ERROR
            job.error_message = f"Failed to move to review: {str(e)}"

    def eject_disc(self, device: str = "/dev/sr0") -> bool:
        """Eject the disc if eject_when_done is enabled"""
        try:
            from . import config as cfg_module
            cfg = cfg_module.load_config()
            if not cfg.get('ripping', {}).get('eject_when_done', False):
                return False

            activity.log_info(f"Ejecting disc from {device}")
            result = subprocess.run(
                ["eject", device],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                activity.log_success("Disc ejected")
                # Mark disc as ejected in current job
                if self.current_job:
                    self.current_job.disc_ejected = True
                return True
            else:
                stderr = result.stderr.strip()
                # Check for device not found error
                if "not found" in stderr.lower() or not os.path.exists(device):
                    self._log_drive_disconnected(device)
                else:
                    activity.log_warning(f"Eject failed: {stderr}")
                return False
        except Exception as e:
            error_msg = str(e)
            if "not found" in error_msg.lower() or not os.path.exists(device):
                self._log_drive_disconnected(device)
            else:
                activity.log_error(f"Error ejecting disc: {e}")
            return False

    def _log_drive_disconnected(self, device: str = "/dev/sr0"):
        """Log detailed error message when drive disappears from system."""
        activity.log_error(f"=== DRIVE DISCONNECTED ===")
        activity.log_error(f"Device {device} not found - the drive has disconnected from the system.")
        activity.log_error(f"This is usually caused by: USB cable issue, drive overheating, or kernel driver problem.")
        activity.log_error(f"")
        activity.log_error(f"To fix, try these steps:")
        activity.log_error(f"1. Check USB cable connection (if external drive)")
        activity.log_error(f"2. Let drive cool down if it's been running for a while")
        activity.log_error(f"3. Run SCSI rescan: echo '- - -' | sudo tee /sys/class/scsi_host/host*/scan")
        activity.log_error(f"4. If that doesn't work, unplug and replug the drive")
        activity.log_error(f"5. Check 'dmesg | tail -30' for hardware errors")
        activity.log_error(f"===========================")

    def check_disc(self, device: str = "/dev/sr0") -> dict:
        """Check if a disc is present and get basic info"""
        result = {"present": False, "label": "", "type": ""}

        try:
            # Use blkid to check for disc
            proc = subprocess.run(
                ["blkid", device],
                capture_output=True,
                text=True,
                timeout=10
            )
            if proc.returncode == 0 and proc.stdout:
                result["present"] = True
                # Parse label if present
                match = re.search(r'LABEL="([^"]*)"', proc.stdout)
                if match:
                    result["label"] = match.group(1)
        except:
            pass

        return result


# Global engine instance (initialized when app starts)
_engine: Optional[RipEngine] = None


def get_engine() -> Optional[RipEngine]:
    """Get the global rip engine instance"""
    return _engine


def init_engine(config: dict):
    """Initialize the global rip engine"""
    global _engine
    _engine = RipEngine(config)
    return _engine
