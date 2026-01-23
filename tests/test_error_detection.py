"""Tests for error detection module"""

import pytest
from unittest.mock import patch, MagicMock
from app.error_detection import (
    ErrorCategory,
    ErrorCode,
    RipError,
    check_disc_present,
    check_disk_space,
    parse_makemkv_output,
    parse_kernel_errors,
    classify_makemkv_return_code,
    detect_error,
    format_error_message,
    _code_to_category,
    _is_recoverable,
    _get_suggestion,
)


class TestErrorCategories:
    """Tests for error category and code enums"""

    def test_error_categories_exist(self):
        """Test all error categories are defined"""
        assert ErrorCategory.DISC.value == "disc"
        assert ErrorCategory.COPY_PROTECTION.value == "protection"
        assert ErrorCategory.DRIVE.value == "drive"
        assert ErrorCategory.IO.value == "io"
        assert ErrorCategory.SPACE.value == "space"
        assert ErrorCategory.PROCESS.value == "process"
        assert ErrorCategory.NETWORK.value == "network"
        assert ErrorCategory.UNKNOWN.value == "unknown"

    def test_error_codes_in_ranges(self):
        """Test error codes are in expected ranges"""
        # Disc errors: 1xx
        assert 100 <= ErrorCode.DISC_EJECTED.value < 200
        assert 100 <= ErrorCode.DISC_NOT_FOUND.value < 200

        # Copy protection: 2xx
        assert 200 <= ErrorCode.AACS_DECRYPT_FAILED.value < 300
        assert 200 <= ErrorCode.CSS_DECRYPT_FAILED.value < 300

        # Drive errors: 3xx
        assert 300 <= ErrorCode.DRIVE_NOT_FOUND.value < 400
        assert 300 <= ErrorCode.SCSI_ERROR.value < 400

        # I/O errors: 4xx
        assert 400 <= ErrorCode.READ_ERROR.value < 500
        assert 400 <= ErrorCode.BAD_SECTOR.value < 500

        # Space errors: 5xx
        assert 500 <= ErrorCode.DISK_FULL.value < 600

        # Process errors: 6xx
        assert 600 <= ErrorCode.MAKEMKV_CRASH.value < 700


class TestRipError:
    """Tests for RipError dataclass"""

    def test_rip_error_creation(self):
        """Test creating a RipError"""
        error = RipError(
            category=ErrorCategory.DISC,
            code=ErrorCode.DISC_EJECTED,
            message="Disc was ejected",
            details="Additional info",
            recoverable=True,
            suggestion="Re-insert disc"
        )
        assert error.category == ErrorCategory.DISC
        assert error.code == ErrorCode.DISC_EJECTED
        assert error.message == "Disc was ejected"
        assert error.recoverable is True

    def test_rip_error_to_dict(self):
        """Test RipError serialization"""
        error = RipError(
            category=ErrorCategory.IO,
            code=ErrorCode.READ_ERROR,
            message="Read failed"
        )
        d = error.to_dict()
        assert d['category'] == "io"
        assert d['code'] == ErrorCode.READ_ERROR.value
        assert d['message'] == "Read failed"
        assert d['recoverable'] is False


class TestCheckDiscPresent:
    """Tests for disc presence detection"""

    @patch('os.path.exists')
    def test_disc_present(self, mock_exists):
        """Test when disc is present"""
        mock_exists.return_value = True
        assert check_disc_present("/dev/sr0") is True
        mock_exists.assert_called_with("/dev/sr0")

    @patch('os.path.exists')
    def test_disc_not_present(self, mock_exists):
        """Test when disc is not present"""
        mock_exists.return_value = False
        assert check_disc_present("/dev/sr0") is False


class TestCheckDiskSpace:
    """Tests for disk space checking"""

    @patch('os.statvfs')
    def test_enough_space(self, mock_statvfs):
        """Test when there's enough space"""
        mock_stat = MagicMock()
        mock_stat.f_frsize = 4096
        mock_stat.f_bavail = 15000000  # ~60GB
        mock_statvfs.return_value = mock_stat

        has_space, available = check_disk_space("/mnt/media", required_gb=50)
        assert has_space is True
        assert available > 50

    @patch('os.statvfs')
    def test_not_enough_space(self, mock_statvfs):
        """Test when space is insufficient"""
        mock_stat = MagicMock()
        mock_stat.f_frsize = 4096
        mock_stat.f_bavail = 1000000  # ~4GB
        mock_statvfs.return_value = mock_stat

        has_space, available = check_disk_space("/mnt/media", required_gb=50)
        assert has_space is False
        assert available < 50

    @patch('os.statvfs')
    def test_statvfs_error(self, mock_statvfs):
        """Test graceful handling of statvfs errors"""
        mock_statvfs.side_effect = OSError("Permission denied")
        has_space, available = check_disk_space("/mnt/media")
        # Should assume OK on error
        assert has_space is True


class TestParseMakemkvOutput:
    """Tests for MakeMKV output parsing"""

    def test_parse_aacs_error(self):
        """Test detecting AACS errors"""
        output = "Error: AACS decryption failed, keys may be outdated"
        error = parse_makemkv_output(output)
        assert error is not None
        assert error.code == ErrorCode.AACS_DECRYPT_FAILED
        assert error.category == ErrorCategory.COPY_PROTECTION

    def test_parse_css_error(self):
        """Test detecting CSS errors"""
        output = "libdvdcss: CSS decryption error"
        error = parse_makemkv_output(output)
        assert error is not None
        assert error.code == ErrorCode.CSS_DECRYPT_FAILED

    def test_parse_fake_playlist(self):
        """Test detecting fake playlist protection"""
        output = "Warning: Fake playlist detected"
        error = parse_makemkv_output(output)
        assert error is not None
        assert error.code == ErrorCode.FAKE_PLAYLIST

    def test_parse_scsi_error(self):
        """Test detecting SCSI errors"""
        output = "Scsi error - command failed"
        error = parse_makemkv_output(output)
        assert error is not None
        assert error.code == ErrorCode.SCSI_ERROR
        assert error.category == ErrorCategory.DRIVE

    def test_parse_io_error(self):
        """Test detecting I/O errors"""
        output = "Error: I/O error while reading disc"
        error = parse_makemkv_output(output)
        assert error is not None
        assert error.code == ErrorCode.READ_ERROR

    def test_parse_disk_full(self):
        """Test detecting disk full errors"""
        output = "Error: No space left on device"
        error = parse_makemkv_output(output)
        assert error is not None
        assert error.code == ErrorCode.DISK_FULL
        assert error.category == ErrorCategory.SPACE

    def test_parse_no_error(self):
        """Test when no error pattern matches"""
        output = "Operation completed successfully"
        error = parse_makemkv_output(output)
        assert error is None


class TestParseKernelErrors:
    """Tests for kernel error parsing"""

    def test_parse_illegal_request(self):
        """Test detecting SCSI illegal request"""
        errors = ["sr 1:0:0:0: ILLEGAL REQUEST: INVALID FIELD IN CDB"]
        error = parse_kernel_errors(errors)
        assert error is not None
        assert error.code == ErrorCode.SCSI_ERROR

    def test_parse_remote_io(self):
        """Test detecting remote I/O errors"""
        errors = ["sr 1:0:0:0: Remote I/O error"]
        error = parse_kernel_errors(errors)
        assert error is not None
        assert error.code == ErrorCode.REMOTE_IO

    def test_parse_medium_not_present(self):
        """Test detecting disc ejection"""
        errors = ["sr: Medium not present"]
        error = parse_kernel_errors(errors)
        assert error is not None
        assert error.code == ErrorCode.DISC_EJECTED

    def test_parse_bad_sector(self):
        """Test detecting bad sectors"""
        errors = ["sense: Medium Error - bad sector"]
        error = parse_kernel_errors(errors)
        assert error is not None
        assert error.code == ErrorCode.BAD_SECTOR

    def test_parse_no_errors(self):
        """Test when no relevant errors"""
        errors = ["Some unrelated kernel message"]
        error = parse_kernel_errors(errors)
        assert error is None


class TestClassifyReturnCode:
    """Tests for MakeMKV return code classification"""

    def test_success_code(self):
        """Test return code 0 (success)"""
        error = classify_makemkv_return_code(0)
        assert error is None

    def test_general_error(self):
        """Test return code 1 (general error)"""
        error = classify_makemkv_return_code(1)
        assert error is not None
        assert error.code == ErrorCode.UNKNOWN

    def test_read_error_code(self):
        """Test return code 12 (read error)"""
        error = classify_makemkv_return_code(12)
        assert error is not None
        assert error.code == ErrorCode.BAD_SECTOR

    def test_copy_protection_code(self):
        """Test return code 15 (copy protection)"""
        error = classify_makemkv_return_code(15)
        assert error is not None
        assert error.code == ErrorCode.AACS_DECRYPT_FAILED

    def test_killed_signal(self):
        """Test negative return code (killed)"""
        error = classify_makemkv_return_code(-9)
        assert error is not None
        assert error.code == ErrorCode.MAKEMKV_KILLED

    def test_unknown_code(self):
        """Test unknown return code"""
        error = classify_makemkv_return_code(99)
        assert error is not None
        assert error.code == ErrorCode.UNKNOWN


class TestDetectError:
    """Tests for comprehensive error detection"""

    @patch('app.error_detection.check_disc_present')
    def test_detect_disc_ejected(self, mock_check):
        """Test detecting disc ejection"""
        mock_check.return_value = False
        error = detect_error(device="/dev/sr0")
        assert error is not None
        assert error.code == ErrorCode.DISC_EJECTED
        assert error.recoverable is True

    @patch('app.error_detection.check_disc_present')
    @patch('app.error_detection.check_disk_space')
    def test_detect_disk_full(self, mock_space, mock_disc):
        """Test detecting disk full"""
        mock_disc.return_value = True
        mock_space.return_value = (False, 2.5)
        error = detect_error(output_path="/mnt/media")
        assert error is not None
        assert error.code == ErrorCode.DISK_FULL

    @patch('app.error_detection.check_disc_present')
    @patch('app.error_detection.check_disk_space')
    @patch('app.error_detection.get_kernel_errors')
    def test_detect_from_output(self, mock_kernel, mock_space, mock_disc):
        """Test detecting error from MakeMKV output"""
        mock_disc.return_value = True
        mock_space.return_value = (True, 100)
        mock_kernel.return_value = []
        error = detect_error(output="AACS decryption error")
        assert error is not None
        assert error.code == ErrorCode.AACS_DECRYPT_FAILED


class TestHelperFunctions:
    """Tests for helper functions"""

    def test_code_to_category_mapping(self):
        """Test error code to category mapping"""
        assert _code_to_category(ErrorCode.DISC_EJECTED) == ErrorCategory.DISC
        assert _code_to_category(ErrorCode.AACS_DECRYPT_FAILED) == ErrorCategory.COPY_PROTECTION
        assert _code_to_category(ErrorCode.DRIVE_BUSY) == ErrorCategory.DRIVE
        assert _code_to_category(ErrorCode.READ_ERROR) == ErrorCategory.IO
        assert _code_to_category(ErrorCode.DISK_FULL) == ErrorCategory.SPACE
        assert _code_to_category(ErrorCode.MAKEMKV_CRASH) == ErrorCategory.PROCESS
        assert _code_to_category(ErrorCode.API_TIMEOUT) == ErrorCategory.NETWORK

    def test_is_recoverable(self):
        """Test recoverable error detection"""
        assert _is_recoverable(ErrorCode.DISC_EJECTED) is True
        assert _is_recoverable(ErrorCode.DISK_FULL) is True
        assert _is_recoverable(ErrorCode.DRIVE_BUSY) is True
        assert _is_recoverable(ErrorCode.BAD_SECTOR) is False
        assert _is_recoverable(ErrorCode.DRIVE_HARDWARE) is False

    def test_get_suggestion(self):
        """Test getting suggestions for errors"""
        assert _get_suggestion(ErrorCode.DISC_EJECTED) is not None
        assert "Re-insert" in _get_suggestion(ErrorCode.DISC_EJECTED)
        assert _get_suggestion(ErrorCode.DISK_FULL) is not None
        assert "space" in _get_suggestion(ErrorCode.DISK_FULL).lower()

    def test_format_error_message(self):
        """Test error message formatting"""
        error = RipError(
            category=ErrorCategory.DISC,
            code=ErrorCode.DISC_EJECTED,
            message="Disc was ejected",
            suggestion="Re-insert the disc"
        )
        formatted = format_error_message(error)
        assert "[DISC]" in formatted
        assert "ejected" in formatted
        assert "Re-insert" in formatted
