"""
Tests for RipForge activity logging module
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLogFormatting:
    """Tests for log message formatting"""

    @patch('app.activity.ACTIVITY_LOG')
    def test_log_creates_proper_format(self, mock_log_path):
        """Test log entries have correct format"""
        from app import activity

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            mock_log_path.__str__ = MagicMock(return_value=f.name)

            # Patch open to write to our temp file
            with patch('builtins.open', create=True) as mock_open:
                mock_file = MagicMock()
                mock_open.return_value.__enter__.return_value = mock_file

                activity.log("Test message", "INFO")

                # Verify write was called
                mock_file.write.assert_called_once()
                written = mock_file.write.call_args[0][0]

                # Check format: "YYYY-MM-DD HH:MM:SS | LEVEL | message\n"
                assert " | INFO | Test message\n" in written
                assert len(written.split(" | ")) == 3

    def test_log_info_uses_info_level(self):
        """Test log_info uses INFO level"""
        from app import activity

        with patch.object(activity, 'log') as mock_log:
            activity.log_info("Test info")
            mock_log.assert_called_once_with("Test info", "INFO")

    def test_log_success_uses_success_level(self):
        """Test log_success uses SUCCESS level"""
        from app import activity

        with patch.object(activity, 'log') as mock_log:
            activity.log_success("Test success")
            mock_log.assert_called_once_with("Test success", "SUCCESS")

    def test_log_error_uses_error_level(self):
        """Test log_error uses ERROR level"""
        from app import activity

        with patch.object(activity, 'log') as mock_log:
            activity.log_error("Test error")
            mock_log.assert_called_once_with("Test error", "ERROR")

    def test_log_warning_uses_warn_level(self):
        """Test log_warning uses WARN level"""
        from app import activity

        with patch.object(activity, 'log') as mock_log:
            activity.log_warning("Test warning")
            mock_log.assert_called_once_with("Test warning", "WARN")


class TestActivityEvents:
    """Tests for specific activity event functions"""

    def test_disc_inserted(self):
        """Test disc_inserted event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.disc_inserted("/dev/sr1")
            mock_log.assert_called_once_with("Disc inserted in /dev/sr1")

    def test_disc_detected(self):
        """Test disc_detected event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.disc_detected("bluray", "GUARDIANS_3")
            mock_log.assert_called_once_with("Disc detected: GUARDIANS_3 (bluray)")

    def test_scan_completed_without_runtime(self):
        """Test scan_completed without runtime"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.scan_completed("MATRIX", "BLURAY")
            mock_log.assert_called_once_with("Scan completed: MATRIX (BLURAY)")

    def test_scan_completed_with_runtime(self):
        """Test scan_completed with runtime"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.scan_completed("MATRIX", "BLURAY", "2h 16m")
            mock_log.assert_called_once_with("Scan completed: MATRIX (BLURAY) - 2h 16m")

    def test_rip_started(self):
        """Test rip_started event"""
        from app import activity

        with patch.object(activity, 'log') as mock_log:
            activity.rip_started("The Matrix (1999)", "main feature only")
            mock_log.assert_called_once_with("Rip started: The Matrix (1999) (main feature only)", "START")

    def test_rip_progress_at_milestone(self):
        """Test rip_progress logs at milestones"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.rip_progress("The Matrix", 50)
            mock_log.assert_called_once()
            assert "50%" in mock_log.call_args[0][0]

    def test_rip_progress_not_at_milestone(self):
        """Test rip_progress doesn't log at non-milestones"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.rip_progress("The Matrix", 33)
            mock_log.assert_not_called()

    def test_rip_identified(self):
        """Test rip_identified event"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.rip_identified("MATRIX_1999", "The Matrix (1999)", 95)
            assert "95% confidence" in mock_log.call_args[0][0]

    def test_rip_completed_with_duration(self):
        """Test rip_completed with duration"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.rip_completed("The Matrix (1999)", "0:45:32")
            mock_log.assert_called_once_with("Rip completed: The Matrix (1999) (0:45:32)")

    def test_rip_cancelled_with_reason(self):
        """Test rip_cancelled with reason"""
        from app import activity

        with patch.object(activity, 'log_warning') as mock_log:
            activity.rip_cancelled("The Matrix", "User requested stop")
            assert "User requested stop" in mock_log.call_args[0][0]

    def test_rip_cancelled_without_reason(self):
        """Test rip_cancelled without reason"""
        from app import activity

        with patch.object(activity, 'log_warning') as mock_log:
            activity.rip_cancelled("The Matrix")
            mock_log.assert_called_once_with("Rip cancelled: The Matrix")

    def test_email_sent(self):
        """Test email_sent event"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.email_sent("Test", ["a@b.com", "c@d.com"])
            assert "2 recipient(s)" in mock_log.call_args[0][0]

    def test_plex_scan_triggered_with_library(self):
        """Test plex_scan_triggered with library name"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.plex_scan_triggered("Movies")
            mock_log.assert_called_once_with("Plex library scan triggered: Movies")

    def test_plex_scan_triggered_without_library(self):
        """Test plex_scan_triggered without library name"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.plex_scan_triggered()
            mock_log.assert_called_once_with("Plex library scan triggered")


class TestRipHistory:
    """Tests for rip history save/load functions"""

    def test_load_rip_history_empty(self):
        """Test loading history when file doesn't exist"""
        from app import activity

        with patch.object(activity, 'HISTORY_FILE') as mock_path:
            mock_path.exists.return_value = False

            result = activity.load_rip_history()
            assert result == []

    def test_load_rip_history_valid_json(self):
        """Test loading valid history file"""
        from app import activity

        test_history = [{"title": "Test Movie", "year": 2023}]

        with patch.object(activity, 'HISTORY_FILE') as mock_path:
            mock_path.exists.return_value = True

            with patch('builtins.open', create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(test_history)

                with patch('json.load', return_value=test_history):
                    result = activity.load_rip_history()
                    assert result == test_history

    def test_save_rip_to_history_creates_entry(self):
        """Test save_rip_to_history creates proper entry"""
        from app import activity

        with patch.object(activity, 'load_rip_history', return_value=[]):
            with patch('builtins.open', create=True) as mock_open:
                with patch.object(activity, 'log_info'):
                    activity.save_rip_to_history(
                        title="Test Movie (2023)",
                        year=2023,
                        disc_type="bluray",
                        runtime_str="2h 0m",
                        size_gb=25.5,
                        duration_str="0:45:00",
                        poster_url="https://example.com/poster.jpg",
                        tmdb_id=12345,
                        content_type="movie",
                        rip_method="direct"
                    )

                    # Verify json.dump was called
                    mock_open.assert_called()


class TestIdMethodResult:
    """Tests for identification method logging"""

    def test_id_method_result_basic(self):
        """Test id_method_result without details"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.id_method_result("Disc Label Parsing", "The Matrix", 50)
            logged = mock_log.call_args[0][0]
            assert "Disc Label Parsing" in logged
            assert "The Matrix" in logged
            assert "50%" in logged

    def test_id_method_result_with_details(self):
        """Test id_method_result with details"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.id_method_result("Radarr", "The Matrix (1999)", 95, "Runtime match: 2m diff")
            logged = mock_log.call_args[0][0]
            assert "Runtime match: 2m diff" in logged


class TestServiceEvents:
    """Tests for service lifecycle events"""

    def test_service_started(self):
        """Test service_started event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.service_started()
            mock_log.assert_called_once_with("RipForge service started")

    def test_service_stopped(self):
        """Test service_stopped event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.service_stopped()
            mock_log.assert_called_once_with("RipForge service stopped")


class TestFileEvents:
    """Tests for file-related activity events"""

    def test_file_moved(self):
        """Test file_moved event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.file_moved("movie.mkv", "/movies/Movie (2023)")
            mock_log.assert_called_once()
            logged = mock_log.call_args[0][0]
            assert "movie.mkv" in logged
            assert "/movies/Movie (2023)" in logged

    def test_library_added(self):
        """Test library_added event"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.library_added("The Matrix (1999)", "Movies")
            mock_log.assert_called_once()
            logged = mock_log.call_args[0][0]
            assert "The Matrix (1999)" in logged
            assert "Movies" in logged


class TestEmailEvents:
    """Tests for email-related activity events"""

    def test_email_failed(self):
        """Test email_failed event"""
        from app import activity

        with patch.object(activity, 'log_error') as mock_log:
            activity.email_failed("Weekly Recap", "Connection timeout")
            logged = mock_log.call_args[0][0]
            assert "Weekly Recap" in logged
            assert "Connection timeout" in logged

    def test_test_email_requested(self):
        """Test test_email_requested event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.test_email_requested(["user1@example.com", "user2@example.com"])
            logged = mock_log.call_args[0][0]
            assert "user1@example.com" in logged
            assert "user2@example.com" in logged

    def test_weekly_recap_sent(self):
        """Test weekly_recap_sent event"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.weekly_recap_sent(["a@b.com", "c@d.com", "e@f.com"])
            logged = mock_log.call_args[0][0]
            assert "3 recipient(s)" in logged


class TestScanEvents:
    """Tests for scan-related activity events"""

    def test_scan_started(self):
        """Test scan_started event"""
        from app import activity

        with patch.object(activity, 'log_info') as mock_log:
            activity.scan_started("/dev/sr1")
            mock_log.assert_called_once_with("Scan started on /dev/sr1")

    def test_scan_failed(self):
        """Test scan_failed event"""
        from app import activity

        with patch.object(activity, 'log_error') as mock_log:
            activity.scan_failed("No disc in drive")
            mock_log.assert_called_once_with("Scan failed: No disc in drive")

    def test_rip_failed(self):
        """Test rip_failed event"""
        from app import activity

        with patch.object(activity, 'log_error') as mock_log:
            activity.rip_failed("The Matrix", "Copy protection detected")
            logged = mock_log.call_args[0][0]
            assert "The Matrix" in logged
            assert "Copy protection detected" in logged


class TestRecentRips:
    """Tests for get_recent_rips function"""

    def test_get_recent_rips_empty(self):
        """Test get_recent_rips when no history"""
        from app import activity

        with patch.object(activity, 'load_rip_history', return_value=[]):
            result = activity.get_recent_rips(days=7)
            assert result == []

    def test_get_recent_rips_filters_by_date(self):
        """Test get_recent_rips filters by date correctly"""
        from app import activity
        from datetime import datetime, timedelta

        recent_date = (datetime.now() - timedelta(days=1)).isoformat()
        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        history = [
            {'title': 'Recent Movie', 'completed_at': recent_date},
            {'title': 'Old Movie', 'completed_at': old_date}
        ]

        # Mock load_rip_history and load_config
        with patch.object(activity, 'load_rip_history', return_value=history):
            from app import config
            with patch.object(config, 'load_config', return_value={'notifications': {'email': {}}}):
                result = activity.get_recent_rips(days=7, respect_digest_reset=False)
                # Should only include recent movie
                assert len(result) == 1
                assert result[0]['title'] == 'Recent Movie'


class TestResetDigestList:
    """Tests for reset_digest_list function"""

    def test_reset_digest_calls_save(self):
        """Test reset updates config"""
        from app import activity
        from app import config

        with patch.object(config, 'load_config', return_value={'notifications': {'email': {}}}):
            with patch.object(config, 'save_config') as mock_save:
                with patch.object(activity, 'log_info'):
                    activity.reset_digest_list()
                    mock_save.assert_called_once()
