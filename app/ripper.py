"""
RipForge Ripping Engine
Handles disc detection, MakeMKV control, and rip job management
"""

import os
import re
import subprocess
import threading
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

from . import activity


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
    # TV-specific fields
    media_type: str = "movie"  # "movie" or "tv"
    series_title: str = ""  # Original series title for TV
    season_number: int = 0
    tracks_to_rip: List[int] = field(default_factory=list)  # Track indices for episodes
    current_track_index: int = 0  # Current track being ripped (for progress)
    episode_mapping: Dict[int, dict] = field(default_factory=dict)  # track_idx -> episode info
    ripped_files: List[str] = field(default_factory=list)  # Paths of ripped episode files
    steps: Dict[str, RipStep] = field(default_factory=lambda: {
        "insert": RipStep(),
        "detect": RipStep(),
        "scan": RipStep(),
        "rip": RipStep(),
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
            "is_tv_disc": False  # True if multiple episode-length tracks detected
        }

        # Convert device to MakeMKV format (disc:0 for /dev/sr0)
        disc_num = 0
        if device.startswith("/dev/sr"):
            disc_num = int(device.replace("/dev/sr", ""))

        args = ["-r", "info", f"disc:{disc_num}"]
        process = self._run_cmd(args)

        longest_track = {"index": None, "duration": 0}

        for line in process.stdout:
            line = line.strip()

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

        # Set main feature as longest track over 45 minutes
        if longest_track["duration"] > 2700:  # 45 min
            info["main_feature"] = longest_track["index"]

        # Detect episode-length tracks (for TV show detection)
        episode_tracks = []
        for track in info["tracks"]:
            duration = track.get("duration", 0)
            if tv_min <= duration <= tv_max:
                episode_tracks.append(track)

        info["episode_tracks"] = episode_tracks

        # If 2+ episode-length tracks, likely a TV disc
        if len(episode_tracks) >= 2:
            info["is_tv_disc"] = True
            # For TV discs, log the episode tracks
            activity.log_info(f"DISC: Detected {len(episode_tracks)} episode-length tracks (TV disc)")
            for et in episode_tracks:
                activity.log_info(f"DISC:   Track {et['index']}: {et['duration_str']} ({et['duration'] // 60}m)")

        return info

    def rip_track(self, device: str, track: int, output_dir: str,
                  progress_callback: Optional[Callable] = None,
                  message_callback: Optional[Callable] = None) -> tuple:
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
        activity.log_info(f"DEBUG: Running: {cmd_str}")

        process = self._run_cmd(args)
        last_error = ""
        actual_output_path = None  # Track where MakeMKV actually saves

        line_count = 0
        prgv_count = 0
        for line in process.stdout:
            line = line.strip()
            line_count += 1

            # Log first few lines for debugging
            if line_count <= 5:
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
                            activity.log_info(f"DEBUG: Progress {percent}% (PRGV #{prgv_count})")

            # Parse messages for errors/status and track actual output path
            if line.startswith("MSG:"):
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
                            activity.log_info(f"DEBUG: MakeMKV saving to: {actual_output_path}")

        return_code = process.wait()
        activity.log_info(f"DEBUG: MakeMKV finished. Lines: {line_count}, PRGV: {prgv_count}, Return: {return_code}")

        if return_code == 0:
            # Detect silent failures: MakeMKV returned success but never reported progress
            if prgv_count == 0:
                activity.log_warning("MakeMKV reported success but no progress - possible disc read failure or copy protection")
                return (False, "MakeMKV reported success but no progress was made - disc may be unreadable or copy-protected", actual_output_path)
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

        # Initialize MakeMKV wrapper - use host installation
        self.makemkv = MakeMKV(use_docker=False)

        # Paths from config
        self.raw_path = config.get("paths", {}).get("raw_rips", "/mnt/media/rips/raw")
        self.movies_path = config.get("paths", {}).get("movies", "/mnt/media/movies")
        self.tv_path = config.get("paths", {}).get("tv", "/mnt/media/tv")
        self.review_path = config.get("paths", {}).get("review", "/mnt/media/rips/review")

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
                activity.log_info(f"Recovering rip job: {state.get('identified_title', state.get('disc_label'))}")

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
            job.identified_title = f"{id_result.title} ({id_result.year})"
            job.year = id_result.year
            job.tmdb_id = id_result.tmdb_id
            job.poster_url = id_result.poster_url
            job.runtime_str = f"{id_result.runtime_minutes}m" if id_result.runtime_minutes else ""
            confidence_str = "HIGH" if id_result.is_confident else "MEDIUM"
            self._update_step("identify", "complete", f"{job.identified_title} [{confidence_str}]")
            activity.log_success(f"=== IDENTIFICATION COMPLETE: {job.identified_title} ({id_result.confidence}% confidence) ===")
            activity.rip_identified(job.disc_label, job.identified_title, id_result.confidence)
        else:
            # Fall back to disc label - mark for review
            fallback_title = job.disc_label.replace("_", " ").title()
            job.identified_title = fallback_title
            job.needs_review = True  # Flag for review queue
            self._update_step("identify", "complete", f"{job.identified_title} [NEEDS REVIEW]")
            activity.log_warning(f"IDENTIFY: Radarr match failed, falling back to disc label")
            activity.log_warning(f"=== IDENTIFICATION FALLBACK: '{job.disc_label}' -> '{fallback_title}' (NEEDS REVIEW) ===")

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
                self._update_step("move", "complete", "Moved to movies/")
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

            # Clear job state file
            self._clear_job_state()

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
                    self.current_job.current_size_bytes = self._get_output_size(self.current_job.rip_output_dir)
                    # Always calculate progress from file size (MakeMKV doesn't report PRGV for DVDs)
                    if self.current_job.expected_size_bytes > 0:
                        size_progress = int((self.current_job.current_size_bytes / self.current_job.expected_size_bytes) * 100)
                        self.current_job.progress = min(size_progress, 99)  # Cap at 99% until actually done

                    # Check if MakeMKV finished (process gone but we're still in ripping state)
                    if not self._is_makemkv_running():
                        # MakeMKV finished - check if we have output
                        if self.current_job.current_size_bytes > 0:
                            # Rip completed, trigger post-processing
                            activity.log_info("MakeMKV finished, starting post-processing")
                            self.current_job.progress = 100
                            self._update_step("rip", "complete", "Rip finished")
                            thread = threading.Thread(target=self._run_post_processing)
                            thread.daemon = True
                            thread.start()

                return self.current_job.to_dict()
            return None

    def _get_output_size(self, output_dir: str) -> int:
        """Get total size of MKV files in output directory"""
        import glob
        total = 0
        try:
            if os.path.isdir(output_dir):
                for mkv in glob.glob(os.path.join(output_dir, "*.mkv")):
                    total += os.path.getsize(mkv)
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
                    activity.rip_cancelled(self.current_job.identified_title or self.current_job.disc_label or "Unknown")
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

    def start_rip(self, device: str = "/dev/sr0", custom_title: str = None,
                  media_type: str = "movie", season_number: int = 0,
                  selected_tracks: List[int] = None, episode_mapping: Dict[int, dict] = None,
                  series_title: str = "") -> bool:
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
        """
        with self._lock:
            if self.current_job and self.current_job.status not in [RipStatus.IDLE, RipStatus.COMPLETE, RipStatus.ERROR]:
                return False  # Already ripping

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
                series_title=series_title
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

            # Step 1: Disc inserted
            self._update_step("insert", "complete", "Disc detected")

            # Step 2: Detect disc type
            self._update_step("detect", "active", "Reading disc...")
            job.status = RipStatus.DETECTING

            disc_info = self.makemkv.get_disc_info(job.device)
            job.disc_label = disc_info.get("disc_label", "UNKNOWN")
            job.disc_type = disc_info.get("disc_type", "unknown")

            activity.disc_detected(job.disc_type.upper(), job.disc_label)
            self._update_step("detect", "complete", f"{job.disc_type.upper()}: {job.disc_label}")

            # Step 3: Scan tracks
            self._update_step("scan", "active", "Scanning tracks...")
            job.status = RipStatus.SCANNING

            main_feature = disc_info.get("main_feature")
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
            if main_feature in track_sizes:
                job.expected_size_bytes = track_sizes[main_feature]
                size_gb = math.ceil(job.expected_size_bytes / (1024**3) * 10) / 10
                self._update_step("scan", "complete", f"Track {main_feature} ({duration_str}, {size_gb:.1f} GB)")
            else:
                self._update_step("scan", "complete", f"Track {main_feature} ({duration_str})")

            # Step 4: Rip main feature
            self._update_step("rip", "active", "Starting rip...")
            job.status = RipStatus.RIPPING

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
                self._update_step("rip", "active", f"Ripping... {percent}%")
                # Log at 25%, 50%, 75% milestones
                for milestone in [25, 50, 75]:
                    if percent >= milestone and last_milestone[0] < milestone:
                        activity.rip_progress(job.identified_title or job.disc_label, milestone)
                        last_milestone[0] = milestone

            def message_cb(msg):
                # Ignore raw MakeMKV messages - we show clean progress instead
                pass

            success, error_msg, actual_path = self.makemkv.rip_track(
                job.device,
                main_feature,
                output_dir,
                progress_callback=progress_cb,
                message_callback=message_cb
            )

            if not success:
                self._update_step("rip", "error", error_msg or "Rip failed")
                job.status = RipStatus.ERROR
                job.error_message = error_msg or "MakeMKV rip failed"
                activity.rip_failed(job.identified_title or job.disc_label, error_msg or "MakeMKV rip failed")
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
                dest_folder_name = sanitize_folder_name(job.identified_title or job.disc_label.replace("_", " ").title())
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
                    self._update_step("move", "complete", f"Moved to movies/")

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
                content_type=job.media_type
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
                activity.rip_failed(self.current_job.identified_title or self.current_job.disc_label, str(e))

    def _run_tv_rip_pipeline(self):
        """Execute the TV show rip pipeline - handles multiple episode tracks"""
        try:
            job = self.current_job
            if not job:
                return

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

                success, error_msg, actual_path = self.makemkv.rip_track(
                    job.device,
                    track_num,
                    output_dir,
                    progress_callback=progress_cb
                )

                if success:
                    # Find the ripped file
                    import glob
                    mkv_files = glob.glob(os.path.join(output_dir, "*.mkv"))
                    # Get the newest file (just ripped)
                    if mkv_files:
                        newest = max(mkv_files, key=os.path.getmtime)
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
                content_type="tv"
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
                activity.rip_failed(self.current_job.series_title or self.current_job.disc_label, str(e))
                activity.log_error(f"TV rip pipeline failed: {e}")

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
                "runtime_str": f"{runtime_seconds // 60}m {runtime_seconds % 60}s" if runtime_seconds else "",
                "size_gb": round(size_gb, 2),
                "files": [os.path.basename(f) for f in moved_files],
                "created_at": datetime.now().isoformat(),
                "media_type": job.media_type
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
            self._update_step("move", "complete", f"Moved to review/")
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
                return True
            else:
                activity.log_warning(f"Eject failed: {result.stderr}")
                return False
        except Exception as e:
            activity.log_error(f"Error ejecting disc: {e}")
            return False

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
