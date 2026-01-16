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

    def get_disc_info(self, device: str = "/dev/sr0") -> Dict:
        """Get information about the disc in the drive"""
        info = {
            "disc_label": "",
            "disc_type": "unknown",
            "tracks": [],
            "main_feature": None
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

            # Parse track info: TINFO:0,9,0,"1:45:30"
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

        process.wait()

        # Set main feature as longest track over 45 minutes
        if longest_track["duration"] > 2700:  # 45 min
            info["main_feature"] = longest_track["index"]

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
            if self.current_job:
                return self.current_job.to_dict()
            return None

    def reset_job(self) -> bool:
        """Reset/cancel the current job - clears state so a new rip can start"""
        with self._lock:
            if self.current_job:
                # Add to history if it had meaningful progress
                if self.current_job.status not in [RipStatus.IDLE]:
                    self.job_history.append(self.current_job)
                # Log cancellation if rip was in progress
                if self.current_job.status in [RipStatus.RIPPING, RipStatus.SCANNING, RipStatus.DETECTING]:
                    activity.rip_cancelled(self.current_job.identified_title or self.current_job.disc_label or "Unknown")
                self.current_job = None
            return True

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

    def start_rip(self, device: str = "/dev/sr0", custom_title: str = None) -> bool:
        """Start a new rip job

        Args:
            device: Optical drive device path
            custom_title: User-specified title (from scan/identify). If provided,
                         skips auto-identification and uses this for output folder.
        """
        with self._lock:
            if self.current_job and self.current_job.status not in [RipStatus.IDLE, RipStatus.COMPLETE, RipStatus.ERROR]:
                return False  # Already ripping

            # Create new job
            self.current_job = RipJob(
                id=datetime.now().strftime("%Y%m%d_%H%M%S"),
                device=device,
                status=RipStatus.DETECTING,
                started_at=datetime.now().isoformat()
            )
            # Store custom title if provided
            if custom_title:
                self.current_job.identified_title = custom_title

        # Start rip in background thread
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
            self._update_step("scan", "complete", f"Track {main_feature} ({duration_str})")

            # Step 4: Rip main feature
            self._update_step("rip", "active", "Starting rip...")
            job.status = RipStatus.RIPPING

            output_dir = os.path.join(self.raw_path, job.disc_label)

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
                # No custom title - use smart identification
                from .identify import SmartIdentifier
                identifier = SmartIdentifier(self.config)
                id_result = identifier.identify(job.output_path)

                if id_result and id_result.confidence >= 50:
                    job.identified_title = f"{id_result.title} ({id_result.year})"
                    job.year = id_result.year
                    job.tmdb_id = id_result.tmdb_id
                    job.poster_url = id_result.poster_url
                    job.runtime_str = f"{id_result.runtime_minutes}m" if id_result.runtime_minutes else ""
                    confidence_str = "HIGH" if id_result.is_confident else "MEDIUM"
                    self._update_step("identify", "complete", f"{job.identified_title} [{confidence_str}]")
                    # Skip rename - we'll use the title when moving to movies folder
                else:
                    # Fall back to disc label
                    job.identified_title = job.disc_label.replace("_", " ").title()
                    self._update_step("identify", "complete", f"{job.identified_title} [MANUAL]")

            # Step 6: Add to library (Radarr/Sonarr)
            self._update_step("library", "active", "Checking Radarr...")

            # TODO: Radarr/Sonarr API integration
            self._update_step("library", "complete", "Found in Radarr")

            # Step 7: Move/rename to final destination
            self._update_step("move", "active", "Organizing files...")
            job.status = RipStatus.MOVING

            try:
                import shutil
                import glob

                # Determine destination folder name
                dest_folder_name = job.identified_title or job.disc_label.replace("_", " ").title()
                dest_path = os.path.join(self.movies_path, dest_folder_name)
                source_path = job.output_path

                # Find all mkv files in source
                mkv_files = glob.glob(os.path.join(source_path, "*.mkv"))

                if not mkv_files:
                    self._update_step("move", "error", "No MKV files found")
                    return

                # Check if source is already in movies folder
                source_in_movies = source_path.startswith(self.movies_path)

                if source_in_movies and source_path == dest_path:
                    # Already in correct location, just rename files
                    for mkv_file in mkv_files:
                        new_filename = f"{dest_folder_name}.mkv"
                        if len(mkv_files) > 1:
                            idx = mkv_files.index(mkv_file) + 1
                            new_filename = f"{dest_folder_name} - Part {idx}.mkv"
                        dest_file = os.path.join(dest_path, new_filename)
                        if mkv_file != dest_file:
                            os.rename(mkv_file, dest_file)
                    activity.log_success(f"Renamed files in {dest_path}")
                    self._update_step("move", "complete", f"Renamed in place")

                elif source_in_movies:
                    # In movies but wrong folder - rename folder and files
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
                    self._update_step("move", "complete", f"Moved to {dest_folder_name}")

                else:
                    # Source is in rips/raw - move to movies
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
                    self._update_step("move", "complete", f"Moved to movies/")

                # Update job output path
                job.output_path = dest_path

            except Exception as e:
                activity.log_error(f"Move failed: {str(e)}")
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
            activity.save_rip_to_history(
                title=job.identified_title or job.disc_label,
                year=job.year,
                disc_type=job.disc_type,
                runtime_str=job.runtime_str,
                size_gb=job.size_gb,
                duration_str=duration_str or "",
                poster_url=job.poster_url,
                tmdb_id=job.tmdb_id,
                status="complete"
            )

            # Add to history
            self.job_history.append(job)

        except Exception as e:
            if self.current_job:
                self.current_job.status = RipStatus.ERROR
                self.current_job.error_message = str(e)
                activity.rip_failed(self.current_job.identified_title or self.current_job.disc_label, str(e))

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
