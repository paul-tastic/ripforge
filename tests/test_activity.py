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


class TestCheckForDuplicate:
    """Tests for check_for_duplicate function"""

    def test_no_duplicate_when_empty_history(self, tmp_path):
        """Test returns no duplicate when history is empty"""
        from app import activity

        # Create empty history file
        history_file = tmp_path / "rip_history.json"
        history_file.write_text("[]")

        with patch.object(activity, 'HISTORY_FILE', history_file):
            with patch.object(activity, 'DISC_CAPTURES_FILE', tmp_path / "nonexistent.jsonl"):
                result = activity.check_for_duplicate(
                    title="Test Movie",
                    year=2024,
                    tmdb_id=12345,
                    disc_label="TEST_DISC",
                    disc_type="dvd",
                    movies_path=str(tmp_path)
                )
                assert result['is_duplicate'] is False
                assert result['match_type'] is None

    def test_duplicate_by_folder_exists(self, tmp_path):
        """Test detects duplicate when destination folder exists in library"""
        from app import activity

        # Create existing movie folder
        movies_path = tmp_path / "movies"
        movies_path.mkdir()
        existing_folder = movies_path / "Test Movie (2024)"
        existing_folder.mkdir()
        # Add a fake MKV file
        (existing_folder / "Test Movie (2024).mkv").write_bytes(b"x" * 1000)

        with patch.object(activity, 'log_info'):
            result = activity.check_for_duplicate(
                title="Test Movie",
                year=2024,
                tmdb_id=99999,
                disc_label="TEST_DISC",
                disc_type="dvd",
                movies_path=str(movies_path)
            )
            assert result['is_duplicate'] is True
            assert result['match_type'] == 'folder'
            assert result['existing_info']['title'] == 'Test Movie'
            assert 'path' in result['existing_info']

    def test_no_duplicate_when_folder_missing(self, tmp_path):
        """Test no duplicate when folder doesn't exist in library"""
        from app import activity

        movies_path = tmp_path / "movies"
        movies_path.mkdir()

        with patch.object(activity, 'log_info'):
            result = activity.check_for_duplicate(
                title="Test Movie",
                year=2024,
                tmdb_id=22222,
                disc_label="NEW_DISC",
                disc_type="dvd",
                movies_path=str(movies_path)
            )
            assert result['is_duplicate'] is False

    def test_duplicate_by_folder_without_year(self, tmp_path):
        """Test detects duplicate when folder exists without year"""
        from app import activity

        movies_path = tmp_path / "movies"
        movies_path.mkdir()
        # Folder without year
        existing_folder = movies_path / "Test Movie"
        existing_folder.mkdir()
        (existing_folder / "Test Movie.mkv").write_bytes(b"x" * 2000)

        with patch.object(activity, 'log_info'):
            result = activity.check_for_duplicate(
                title="Test Movie",
                year=None,  # No year
                tmdb_id=99999,
                disc_label="TEST_DISC",
                disc_type="dvd",
                movies_path=str(movies_path)
            )
            assert result['is_duplicate'] is True
            assert result['match_type'] == 'folder'

    def test_no_duplicate_when_no_movies_path(self):
        """Test returns no duplicate when movies_path is None"""
        from app import activity

        result = activity.check_for_duplicate(
            title="Test Movie",
            year=2024,
            tmdb_id=12345,
            disc_label="TEST_DISC",
            disc_type="dvd",
            movies_path=None
        )
        assert result['is_duplicate'] is False


class TestClearActivityLog:
    """Tests for clear_activity_log function"""

    def test_clear_activity_log_when_exists(self, tmp_path):
        """Test clearing activity log when file exists"""
        from app import activity

        log_file = tmp_path / "activity.log"
        log_file.write_text("some log content")

        with patch.object(activity, 'ACTIVITY_LOG', log_file):
            with patch.object(activity, 'log_info'):
                activity.clear_activity_log()
                assert not log_file.exists()

    def test_clear_activity_log_when_not_exists(self, tmp_path):
        """Test clearing activity log when file doesn't exist"""
        from app import activity

        log_file = tmp_path / "nonexistent.log"

        with patch.object(activity, 'ACTIVITY_LOG', log_file):
            with patch.object(activity, 'log_info'):
                # Should not raise
                activity.clear_activity_log()


class TestGetRipErrors:
    """Tests for get_rip_errors function"""

    def test_get_rip_errors_returns_errors(self, tmp_path):
        """Test extracting errors from activity log"""
        from app import activity

        log_file = tmp_path / "activity.log"
        log_file.write_text(
            "2024-01-15 10:30:00 | INFO | Rip started\n"
            "2024-01-15 10:45:00 | ERROR | Rip failed: Copy protection detected\n"
            "2024-01-15 11:00:00 | SUCCESS | Rip completed\n"
            "2024-01-15 11:30:00 | ERROR | Rip failed: Disc read error\n"
        )

        with patch.object(activity, 'ACTIVITY_LOG', log_file):
            errors = activity.get_rip_errors()
            assert len(errors) == 2
            # Newest first
            assert "Disc read error" in errors[0]['message']
            assert "Copy protection detected" in errors[1]['message']

    def test_get_rip_errors_empty_log(self, tmp_path):
        """Test get_rip_errors when log is empty"""
        from app import activity

        log_file = tmp_path / "activity.log"
        log_file.write_text("")

        with patch.object(activity, 'ACTIVITY_LOG', log_file):
            errors = activity.get_rip_errors()
            assert errors == []

    def test_get_rip_errors_no_log_file(self, tmp_path):
        """Test get_rip_errors when log doesn't exist"""
        from app import activity

        log_file = tmp_path / "nonexistent.log"

        with patch.object(activity, 'ACTIVITY_LOG', log_file):
            errors = activity.get_rip_errors()
            assert errors == []


class TestCaptureDiscData:
    """Tests for capture_disc_data function"""

    def test_capture_disc_data_writes_jsonl(self, tmp_path):
        """Test capture_disc_data writes to JSONL file"""
        from app import activity

        captures_file = tmp_path / "disc_captures.jsonl"

        with patch.object(activity, 'DISC_CAPTURES_FILE', captures_file):
            with patch.object(activity, 'log_info'):
                activity.capture_disc_data(
                    disc_label="TEST_DISC_2024",
                    disc_type="bluray",
                    tracks=[{"duration": 7200}, {"duration": 300}],
                    track_sizes={0: 25000000000, 1: 500000000},
                    identified_title="Test Movie",
                    year=2024,
                    tmdb_id=12345,
                    confidence=95,
                    resolution_source="radarr"
                )

                assert captures_file.exists()
                content = captures_file.read_text()
                data = json.loads(content.strip())
                assert data['disc_label'] == "TEST_DISC_2024"
                assert data['disc_type'] == "bluray"
                assert data['track_count'] == 2
                assert data['main_duration_secs'] == 7200
                assert data['identified_title'] == "Test Movie"
                assert data['confidence'] == 95

    def test_capture_disc_data_handles_empty_tracks(self, tmp_path):
        """Test capture_disc_data with empty tracks"""
        from app import activity

        captures_file = tmp_path / "disc_captures.jsonl"

        with patch.object(activity, 'DISC_CAPTURES_FILE', captures_file):
            with patch.object(activity, 'log_info'):
                activity.capture_disc_data(
                    disc_label="EMPTY_DISC",
                    disc_type="dvd",
                    tracks=[],
                    track_sizes={},
                    identified_title=None
                )

                content = captures_file.read_text()
                data = json.loads(content.strip())
                assert data['track_count'] == 0
                assert data['main_duration_secs'] == 0
                assert data['identified_title'] is None


class TestRipCompletedVariations:
    """Additional tests for rip_completed"""

    def test_rip_completed_without_duration(self):
        """Test rip_completed without duration"""
        from app import activity

        with patch.object(activity, 'log_success') as mock_log:
            activity.rip_completed("The Matrix (1999)")
            mock_log.assert_called_once_with("Rip completed: The Matrix (1999)")


class TestLogFailure:
    """Tests for log function error handling"""

    def test_log_handles_write_failure(self, capsys):
        """Test log function handles file write failure gracefully"""
        from app import activity

        with patch('builtins.open', side_effect=IOError("Permission denied")):
            # Should not raise, should print error
            activity.log("Test message", "INFO")
            captured = capsys.readouterr()
            assert "Failed to write activity log" in captured.out


class TestFetchMetadataByTmdbId:
    """Tests for fetch_metadata_by_tmdb_id function"""

    def test_fetch_metadata_returns_empty_when_no_id(self):
        """Test returns empty result when no tmdb_id provided"""
        from app import activity

        result = activity.fetch_metadata_by_tmdb_id(0)
        assert result['year'] == 0
        assert result['poster_url'] == ""
        assert result['runtime_str'] == ""

    def test_fetch_metadata_success(self):
        """Test successful metadata fetch"""
        from app import activity, config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'title': 'Test Movie',
            'year': 2024,
            'overview': 'A test movie',
            'runtime': 125,
            'ratings': {
                'rottenTomatoes': {'value': 85},
                'imdb': {'value': 7.5}
            },
            'images': [
                {'coverType': 'poster', 'remoteUrl': 'https://image.tmdb.org/original/abc.jpg'}
            ]
        }

        with patch.object(config, 'load_config', return_value={
            'integrations': {'radarr': {'url': 'http://localhost:7878', 'api_key': 'test'}}
        }):
            with patch('requests.get', return_value=mock_response):
                with patch.object(activity, 'log_info'):
                    result = activity.fetch_metadata_by_tmdb_id(12345)
                    assert result['year'] == 2024
                    assert result['runtime_str'] == "2h 5m"
                    assert result['rt_rating'] == 85
                    assert result['imdb_rating'] == 7.5
                    assert 'w500' in result['poster_url']

    def test_fetch_metadata_handles_api_error(self):
        """Test handles API errors gracefully"""
        from app import activity, config

        with patch.object(config, 'load_config', return_value={
            'integrations': {'radarr': {'url': 'http://localhost:7878', 'api_key': 'test'}}
        }):
            with patch('requests.get', side_effect=Exception("Connection error")):
                with patch.object(activity, 'log_warning'):
                    result = activity.fetch_metadata_by_tmdb_id(12345)
                    # Should return empty defaults, not raise
                    assert result['year'] == 0


class TestEnrichAndSaveRip:
    """Tests for enrich_and_save_rip function"""

    def test_enrich_and_save_with_tmdb_id(self):
        """Test enrich_and_save_rip fetches metadata by TMDB ID"""
        from app import activity

        mock_metadata = {
            'year': 2024,
            'poster_url': 'https://example.com/poster.jpg',
            'runtime_str': '2h 0m',
            'overview': 'Test overview',
            'rt_rating': 80,
            'imdb_rating': 7.0
        }

        with patch.object(activity, 'log_info'):
            with patch.object(activity, 'fetch_metadata_by_tmdb_id', return_value=mock_metadata):
                with patch.object(activity, 'save_rip_to_history') as mock_save:
                    activity.enrich_and_save_rip(
                        title="Test Movie",
                        disc_type="bluray",
                        tmdb_id=12345
                    )
                    mock_save.assert_called_once()
                    call_args = mock_save.call_args
                    assert call_args.kwargs['poster_url'] == 'https://example.com/poster.jpg'

    def test_enrich_and_save_falls_back_to_radarr_search(self):
        """Test falls back to Radarr search when no TMDB ID"""
        from app import activity

        empty_metadata = {
            'year': 0,
            'poster_url': '',
            'runtime_str': '',
            'overview': '',
            'rt_rating': 0,
            'imdb_rating': 0.0
        }
        radarr_metadata = {
            'year': 2024,
            'tmdb_id': 99999,
            'poster_url': 'https://example.com/radarr-poster.jpg',
            'runtime_str': '1h 45m'
        }

        with patch.object(activity, 'log_info'):
            with patch.object(activity, 'fetch_metadata_by_tmdb_id', return_value=empty_metadata):
                with patch.object(activity, 'fetch_metadata_from_radarr', return_value=radarr_metadata):
                    with patch.object(activity, 'save_rip_to_history') as mock_save:
                        activity.enrich_and_save_rip(
                            title="Test Movie",
                            disc_type="dvd",
                            tmdb_id=0,
                            content_type="movie"
                        )
                        mock_save.assert_called_once()


class TestFetchMetadataFromRadarr:
    """Tests for fetch_metadata_from_radarr function"""

    def test_fetch_from_radarr_parses_year_from_title(self):
        """Test extracts year from title like 'Movie (2024)'"""
        from app import activity, config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'title': 'Test Movie',
            'year': 2024,
            'tmdbId': 12345,
            'runtime': 90,
            'images': []
        }]

        with patch.object(config, 'load_config', return_value={
            'radarr': {'url': 'http://localhost:7878', 'api_key': 'test'}
        }):
            with patch('requests.get', return_value=mock_response):
                with patch.object(activity, 'log_info'):
                    result = activity.fetch_metadata_from_radarr("Test Movie (2024)")
                    assert result['year'] == 2024
                    assert result['tmdb_id'] == 12345
                    assert result['runtime_str'] == "1h 30m"

    def test_fetch_from_radarr_handles_no_results(self):
        """Test handles empty search results"""
        from app import activity, config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        with patch.object(config, 'load_config', return_value={
            'radarr': {'url': 'http://localhost:7878', 'api_key': 'test'}
        }):
            with patch('requests.get', return_value=mock_response):
                result = activity.fetch_metadata_from_radarr("Unknown Movie")
                assert result['tmdb_id'] == 0
                assert result['poster_url'] == ""


class TestGetRecentRipsWithDigestReset:
    """Tests for get_recent_rips with digest reset filtering"""

    def test_respects_digest_reset_timestamp(self):
        """Test filters rips after digest reset"""
        from app import activity, config
        from datetime import datetime, timedelta

        # Rip before digest reset
        before_reset = (datetime.now() - timedelta(days=2)).isoformat()
        # Rip after digest reset
        after_reset = (datetime.now() - timedelta(hours=12)).isoformat()
        # Digest reset was 1 day ago
        digest_reset = (datetime.now() - timedelta(days=1)).isoformat()

        history = [
            {'title': 'Old Movie', 'completed_at': before_reset},
            {'title': 'New Movie', 'completed_at': after_reset}
        ]

        with patch.object(activity, 'load_rip_history', return_value=history):
            with patch.object(config, 'load_config', return_value={
                'notifications': {'email': {'digest_reset_at': digest_reset}}
            }):
                result = activity.get_recent_rips(days=7, respect_digest_reset=True)
                assert len(result) == 1
                assert result[0]['title'] == 'New Movie'
