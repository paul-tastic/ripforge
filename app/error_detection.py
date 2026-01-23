"""
RipForge Error Detection Module

Provides granular error categorization and detection for disc ripping operations.
Detects: disc ejection, I/O errors, copy protection, drive issues, space problems, etc.
"""

import os
import re
import subprocess
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict
from pathlib import Path


class ErrorCategory(Enum):
    """High-level error categories"""
    DISC = "disc"                    # Disc-related (ejected, not found, dirty)
    COPY_PROTECTION = "protection"   # AACS, CSS, fake playlists
    DRIVE = "drive"                  # Hardware/driver issues
    IO = "io"                        # Read/write failures
    SPACE = "space"                  # Disk full
    PROCESS = "process"              # MakeMKV crash, timeout, killed
    NETWORK = "network"              # API failures
    UNKNOWN = "unknown"              # Unclassified


class ErrorCode(Enum):
    """Specific error codes for granular tracking"""
    # Disc errors (1xx)
    DISC_EJECTED = 101
    DISC_NOT_FOUND = 102
    DISC_DIRTY = 103
    DISC_SCRATCHED = 104
    DISC_UNREADABLE = 105

    # Copy protection errors (2xx)
    AACS_DECRYPT_FAILED = 201
    CSS_DECRYPT_FAILED = 202
    FAKE_PLAYLIST = 203
    BDPLUS_FAILED = 204
    CINAVIA = 205

    # Drive errors (3xx)
    DRIVE_NOT_FOUND = 301
    DRIVE_BUSY = 302
    DRIVE_LOCKED = 303
    DRIVE_HARDWARE = 304
    SCSI_ERROR = 305

    # I/O errors (4xx)
    READ_ERROR = 401
    WRITE_ERROR = 402
    BAD_SECTOR = 403
    TIMEOUT = 404
    REMOTE_IO = 405

    # Space errors (5xx)
    DISK_FULL = 501
    QUOTA_EXCEEDED = 502

    # Process errors (6xx)
    MAKEMKV_CRASH = 601
    MAKEMKV_TIMEOUT = 602
    MAKEMKV_KILLED = 603
    NO_OUTPUT = 604

    # Network errors (7xx)
    API_TIMEOUT = 701
    API_ERROR = 702
    CONNECTION_FAILED = 703

    # Unknown (9xx)
    UNKNOWN = 999


@dataclass
class RipError:
    """Structured error information"""
    category: ErrorCategory
    code: ErrorCode
    message: str
    details: Optional[str] = None
    recoverable: bool = False
    suggestion: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            'category': self.category.value,
            'code': self.code.value,
            'message': self.message,
            'details': self.details,
            'recoverable': self.recoverable,
            'suggestion': self.suggestion
        }


# MakeMKV return code mapping
MAKEMKV_ERROR_MAP = {
    0: None,  # Success
    1: (ErrorCode.UNKNOWN, "General MakeMKV error"),
    2: (ErrorCode.UNKNOWN, "Invalid argument"),
    12: (ErrorCode.BAD_SECTOR, "Disc read error - disc may be damaged or dirty"),
    13: (ErrorCode.DRIVE_HARDWARE, "Drive hardware error"),
    15: (ErrorCode.AACS_DECRYPT_FAILED, "Copy protection decryption failed"),
    -9: (ErrorCode.MAKEMKV_KILLED, "MakeMKV was killed (signal 9)"),
    -15: (ErrorCode.MAKEMKV_KILLED, "MakeMKV was terminated (signal 15)"),
}

# Patterns for parsing MakeMKV output
ERROR_PATTERNS = [
    (r'AACS.*error|libaacs', ErrorCode.AACS_DECRYPT_FAILED, "AACS decryption failed"),
    (r'CSS.*error|libdvdcss', ErrorCode.CSS_DECRYPT_FAILED, "CSS decryption failed"),
    (r'fake.*playlist|playlist.*obfuscation', ErrorCode.FAKE_PLAYLIST, "Fake playlist protection detected"),
    (r'BD\+|bdplus', ErrorCode.BDPLUS_FAILED, "BD+ protection failed"),
    (r'Hash check failed', ErrorCode.AACS_DECRYPT_FAILED, "AACS hash check failed - keys may be outdated"),
    (r'Scsi error|SCSI command', ErrorCode.SCSI_ERROR, "SCSI communication error"),
    (r'I/O error|Input/output error', ErrorCode.READ_ERROR, "Disc I/O error"),
    (r'medium not present|no medium', ErrorCode.DISC_NOT_FOUND, "No disc in drive"),
    (r'drive is busy|resource busy', ErrorCode.DRIVE_BUSY, "Drive is busy"),
    (r'No space left|disk full', ErrorCode.DISK_FULL, "Output disk is full"),
    (r'timed? ?out', ErrorCode.TIMEOUT, "Operation timed out"),
]

# Kernel error patterns (from dmesg)
KERNEL_ERROR_PATTERNS = [
    (r'ILLEGAL REQUEST.*INVALID FIELD IN CDB', ErrorCode.SCSI_ERROR, "SCSI protocol error"),
    (r'Remote I/O error', ErrorCode.REMOTE_IO, "Remote I/O error - disc may be damaged"),
    (r'Medium not present', ErrorCode.DISC_EJECTED, "Disc was ejected"),
    (r'I/O error.*sr\d+', ErrorCode.READ_ERROR, "Disc read error"),
    (r'sense: Medium Error', ErrorCode.BAD_SECTOR, "Bad sector detected"),
    (r'Unit Attention.*medium may have changed', ErrorCode.DISC_EJECTED, "Disc changed or ejected"),
]


def check_disc_present(device: str = "/dev/sr0") -> bool:
    """Check if a disc is present in the drive"""
    return os.path.exists(device)


def check_disk_space(path: str, required_gb: float = 50) -> tuple:
    """
    Check if there's enough disk space.
    Returns (has_space: bool, available_gb: float)
    """
    try:
        stat = os.statvfs(path)
        available_gb = (stat.f_frsize * stat.f_bavail) / (1024**3)
        return (available_gb >= required_gb, available_gb)
    except Exception:
        return (True, 0)  # Assume OK if check fails


def get_kernel_errors(since_secs: int = 300) -> List[str]:
    """
    Get recent kernel errors related to optical drive.
    Returns list of error messages from dmesg.
    """
    errors = []
    try:
        result = subprocess.run(
            ["dmesg", "--time-format=reltime", "-l", "err,warn"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                # Look for sr0/optical drive related errors
                if 'sr0' in line.lower() or 'cdrom' in line.lower() or 'optical' in line.lower():
                    errors.append(line.strip())
    except Exception:
        pass
    return errors[-10:]  # Last 10 relevant errors


def parse_makemkv_output(output: str) -> Optional[RipError]:
    """
    Parse MakeMKV output for specific errors.
    Returns RipError if error found, None otherwise.
    """
    output_lower = output.lower()

    for pattern, code, message in ERROR_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            category = _code_to_category(code)
            suggestion = _get_suggestion(code)
            return RipError(
                category=category,
                code=code,
                message=message,
                details=output[:200] if len(output) > 200 else output,
                recoverable=_is_recoverable(code),
                suggestion=suggestion
            )
    return None


def parse_kernel_errors(errors: List[str]) -> Optional[RipError]:
    """
    Parse kernel errors for disc-related issues.
    Returns RipError if relevant error found.
    """
    for error in errors:
        for pattern, code, message in KERNEL_ERROR_PATTERNS:
            if re.search(pattern, error, re.IGNORECASE):
                category = _code_to_category(code)
                suggestion = _get_suggestion(code)
                return RipError(
                    category=category,
                    code=code,
                    message=message,
                    details=error,
                    recoverable=_is_recoverable(code),
                    suggestion=suggestion
                )
    return None


def classify_makemkv_return_code(return_code: int) -> Optional[RipError]:
    """
    Classify MakeMKV return code into structured error.
    """
    if return_code == 0:
        return None

    error_info = MAKEMKV_ERROR_MAP.get(return_code)
    if error_info:
        code, message = error_info
        category = _code_to_category(code)
        suggestion = _get_suggestion(code)
        return RipError(
            category=category,
            code=code,
            message=message,
            details=f"MakeMKV exit code: {return_code}",
            recoverable=_is_recoverable(code),
            suggestion=suggestion
        )

    return RipError(
        category=ErrorCategory.UNKNOWN,
        code=ErrorCode.UNKNOWN,
        message=f"MakeMKV failed with code {return_code}",
        details=f"Exit code: {return_code}",
        recoverable=False
    )


def detect_error(
    return_code: int = 0,
    output: str = "",
    device: str = "/dev/sr0",
    output_path: str = ""
) -> Optional[RipError]:
    """
    Comprehensive error detection combining all sources.
    Call this after a rip failure to get detailed error info.

    Priority:
    1. Check if disc was ejected
    2. Check disk space
    3. Parse MakeMKV output for specific errors
    4. Check kernel errors
    5. Classify by return code
    """

    # 1. Check disc ejection
    if not check_disc_present(device):
        return RipError(
            category=ErrorCategory.DISC,
            code=ErrorCode.DISC_EJECTED,
            message="Disc was ejected during rip",
            details="The disc is no longer in the drive",
            recoverable=True,
            suggestion="Re-insert the disc and try again"
        )

    # 2. Check disk space
    if output_path:
        has_space, available = check_disk_space(output_path)
        if not has_space:
            return RipError(
                category=ErrorCategory.SPACE,
                code=ErrorCode.DISK_FULL,
                message="Insufficient disk space",
                details=f"Only {available:.1f} GB available",
                recoverable=True,
                suggestion="Free up disk space or change output directory"
            )

    # 3. Parse MakeMKV output
    if output:
        error = parse_makemkv_output(output)
        if error:
            return error

    # 4. Check kernel errors
    kernel_errors = get_kernel_errors()
    if kernel_errors:
        error = parse_kernel_errors(kernel_errors)
        if error:
            return error

    # 5. Classify by return code
    if return_code != 0:
        return classify_makemkv_return_code(return_code)

    return None


def _code_to_category(code: ErrorCode) -> ErrorCategory:
    """Map error code to category"""
    code_value = code.value
    if 100 <= code_value < 200:
        return ErrorCategory.DISC
    elif 200 <= code_value < 300:
        return ErrorCategory.COPY_PROTECTION
    elif 300 <= code_value < 400:
        return ErrorCategory.DRIVE
    elif 400 <= code_value < 500:
        return ErrorCategory.IO
    elif 500 <= code_value < 600:
        return ErrorCategory.SPACE
    elif 600 <= code_value < 700:
        return ErrorCategory.PROCESS
    elif 700 <= code_value < 800:
        return ErrorCategory.NETWORK
    return ErrorCategory.UNKNOWN


def _is_recoverable(code: ErrorCode) -> bool:
    """Determine if error is potentially recoverable"""
    recoverable_codes = {
        ErrorCode.DISC_EJECTED,
        ErrorCode.DISC_NOT_FOUND,
        ErrorCode.DISC_DIRTY,
        ErrorCode.DRIVE_BUSY,
        ErrorCode.DRIVE_LOCKED,
        ErrorCode.DISK_FULL,
        ErrorCode.TIMEOUT,
        ErrorCode.FAKE_PLAYLIST,  # Can retry with backup mode
        ErrorCode.API_TIMEOUT,
        ErrorCode.CONNECTION_FAILED,
    }
    return code in recoverable_codes


def _get_suggestion(code: ErrorCode) -> Optional[str]:
    """Get actionable suggestion for error code"""
    suggestions = {
        ErrorCode.DISC_EJECTED: "Re-insert the disc and try again",
        ErrorCode.DISC_NOT_FOUND: "Insert a disc and try again",
        ErrorCode.DISC_DIRTY: "Clean the disc with a soft cloth and try again",
        ErrorCode.DISC_SCRATCHED: "Try a disc repair kit or professional resurfacing",
        ErrorCode.DISC_UNREADABLE: "Disc may be too damaged - try a different copy",
        ErrorCode.AACS_DECRYPT_FAILED: "Update MakeMKV or check AACS keys are current",
        ErrorCode.CSS_DECRYPT_FAILED: "Ensure libdvdcss is installed",
        ErrorCode.FAKE_PLAYLIST: "Use backup mode (enabled by default for protected discs)",
        ErrorCode.BDPLUS_FAILED: "BD+ protection - try updating MakeMKV",
        ErrorCode.DRIVE_NOT_FOUND: "Check drive connection and power",
        ErrorCode.DRIVE_BUSY: "Wait for other operations to complete",
        ErrorCode.DRIVE_LOCKED: "Eject and re-insert disc, or restart drive",
        ErrorCode.DRIVE_HARDWARE: "Drive may need replacement",
        ErrorCode.SCSI_ERROR: "Try ejecting and reinserting disc. May need drive reset.",
        ErrorCode.READ_ERROR: "Disc may be dirty or damaged. Try cleaning.",
        ErrorCode.BAD_SECTOR: "Disc has physical damage. Try cleaning or different copy.",
        ErrorCode.REMOTE_IO: "Drive communication error. Try drive reset.",
        ErrorCode.DISK_FULL: "Free up disk space or change output directory",
        ErrorCode.MAKEMKV_CRASH: "Restart RipForge. If persists, check system resources.",
        ErrorCode.MAKEMKV_TIMEOUT: "Operation took too long. Disc may be damaged.",
        ErrorCode.MAKEMKV_KILLED: "Process was terminated. Check if manually stopped.",
        ErrorCode.NO_OUTPUT: "Rip completed but no files created. Check permissions.",
        ErrorCode.TIMEOUT: "Operation timed out. Try again or check disc.",
    }
    return suggestions.get(code)


def format_error_message(error: RipError) -> str:
    """Format error for display in logs/UI"""
    msg = f"[{error.category.value.upper()}] {error.message}"
    if error.suggestion:
        msg += f" - {error.suggestion}"
    return msg
