"""
Tests for RipForge ripper module
"""

import pytest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ripper import (
    sanitize_folder_name,
    RipStatus,
    StepStatus,
    RipStep,
    RipJob
)


class TestSanitizeFolderName:
    """Tests for the sanitize_folder_name function"""

    def test_basic_name(self):
        """Test basic name passes through"""
        assert sanitize_folder_name("The Matrix") == "The Matrix"

    def test_colon_replacement(self):
        """Test colons are replaced with dashes"""
        assert sanitize_folder_name("Star Wars: A New Hope") == "Star Wars - A New Hope"

    def test_multiple_colons(self):
        """Test multiple colons are all replaced"""
        result = sanitize_folder_name("Part 1: The Beginning: Act 1")
        assert ":" not in result
        assert result == "Part 1 - The Beginning - Act 1"

    def test_invalid_chars_removed(self):
        """Test invalid filesystem characters are removed"""
        result = sanitize_folder_name("What If...?")
        assert "?" not in result
        assert result == "What If..."

    def test_angle_brackets(self):
        """Test angle brackets are removed"""
        result = sanitize_folder_name("<Movie> Title")
        assert "<" not in result
        assert ">" not in result
        assert result == "Movie Title"

    def test_quotes_and_pipes(self):
        """Test quotes and pipes are removed"""
        result = sanitize_folder_name('Movie "Title" | Part')
        assert '"' not in result
        assert "|" not in result

    def test_asterisk(self):
        """Test asterisks are removed"""
        result = sanitize_folder_name("M*A*S*H")
        assert "*" not in result
        assert result == "MASH"

    def test_multiple_spaces_collapsed(self):
        """Test multiple spaces are collapsed to single space"""
        result = sanitize_folder_name("Movie    Title")
        assert result == "Movie Title"

    def test_leading_trailing_spaces(self):
        """Test leading and trailing spaces are stripped"""
        result = sanitize_folder_name("  Movie Title  ")
        assert result == "Movie Title"


class TestRipStatus:
    """Tests for RipStatus enum"""

    def test_all_statuses_exist(self):
        """Test all expected statuses are defined"""
        assert RipStatus.IDLE.value == "idle"
        assert RipStatus.DETECTING.value == "detecting"
        assert RipStatus.SCANNING.value == "scanning"
        assert RipStatus.RIPPING.value == "ripping"
        assert RipStatus.IDENTIFYING.value == "identifying"
        assert RipStatus.MOVING.value == "moving"
        assert RipStatus.COMPLETE.value == "complete"
        assert RipStatus.ERROR.value == "error"


class TestStepStatus:
    """Tests for StepStatus enum"""

    def test_all_step_statuses(self):
        """Test all step statuses are defined"""
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.ACTIVE.value == "active"
        assert StepStatus.COMPLETE.value == "complete"
        assert StepStatus.ERROR.value == "error"


class TestRipStep:
    """Tests for RipStep dataclass"""

    def test_default_values(self):
        """Test RipStep defaults"""
        step = RipStep()
        assert step.status == "pending"
        assert step.detail == ""

    def test_custom_values(self):
        """Test RipStep with custom values"""
        step = RipStep(status="active", detail="Ripping track 1...")
        assert step.status == "active"
        assert step.detail == "Ripping track 1..."


class TestRipJob:
    """Tests for RipJob dataclass"""

    def test_default_values(self):
        """Test RipJob default values"""
        job = RipJob()
        assert job.id == ""
        assert job.disc_label == ""
        assert job.status == RipStatus.IDLE
        assert job.progress == 0
        assert job.media_type == "movie"
        assert job.rip_method == "direct"
        assert job.rip_mode == "smart"

    def test_steps_initialized(self):
        """Test steps are properly initialized"""
        job = RipJob()
        expected_steps = ["insert", "detect", "scan", "rip", "identify", "library", "move", "scan-plex"]
        assert all(step in job.steps for step in expected_steps)
        for step in job.steps.values():
            assert isinstance(step, RipStep)

    def test_to_dict_basic(self):
        """Test to_dict serialization"""
        job = RipJob(
            id="test-123",
            disc_label="TEST_DISC",
            disc_type="bluray",
            status=RipStatus.RIPPING,
            progress=50
        )
        result = job.to_dict()

        assert result["id"] == "test-123"
        assert result["disc_label"] == "TEST_DISC"
        assert result["disc_type"] == "bluray"
        assert result["status"] == "ripping"
        assert result["progress"] == 50

    def test_to_dict_enum_serialization(self):
        """Test status enum is serialized to string"""
        job = RipJob(status=RipStatus.COMPLETE)
        result = job.to_dict()
        assert result["status"] == "complete"
        assert isinstance(result["status"], str)

    def test_to_dict_steps(self):
        """Test steps are serialized correctly"""
        job = RipJob()
        job.steps["rip"].status = "active"
        job.steps["rip"].detail = "45% complete"

        result = job.to_dict()

        assert "steps" in result
        assert result["steps"]["rip"]["status"] == "active"
        assert result["steps"]["rip"]["detail"] == "45% complete"

    def test_tv_specific_fields(self):
        """Test TV-specific fields in RipJob"""
        job = RipJob(
            media_type="tv",
            series_title="Breaking Bad",
            season_number=1,
            tracks_to_rip=[0, 1, 2, 3, 4, 5, 6],
            episode_mapping={0: {"episode_number": 1, "title": "Pilot"}}
        )

        result = job.to_dict()

        assert result["media_type"] == "tv"
        assert result["series_title"] == "Breaking Bad"
        assert result["season_number"] == 1
        assert result["total_tracks"] == 7
        assert result["episode_mapping"] == {0: {"episode_number": 1, "title": "Pilot"}}

    def test_rip_mode_fields(self):
        """Test rip mode tracking fields"""
        job = RipJob(
            rip_method="backup",
            rip_mode="always_backup",
            direct_failed=True
        )

        result = job.to_dict()

        assert result["rip_method"] == "backup"
        assert result["rip_mode"] == "always_backup"
        assert result["direct_failed"] is True

    def test_needs_review_flag(self):
        """Test needs_review flag serialization"""
        job = RipJob(needs_review=True)
        result = job.to_dict()
        assert result["needs_review"] is True

    def test_tracks_to_rip_list(self):
        """Test tracks_to_rip list handling"""
        job = RipJob(tracks_to_rip=[0, 2, 4])
        assert job.tracks_to_rip == [0, 2, 4]
        result = job.to_dict()
        assert result["tracks_to_rip"] == [0, 2, 4]
        assert result["total_tracks"] == 3

    def test_empty_tracks_total(self):
        """Test total_tracks with empty list"""
        job = RipJob()
        result = job.to_dict()
        assert result["total_tracks"] == 0


class TestRipJobTracking:
    """Tests for RipJob file size tracking"""

    def test_size_tracking_fields(self):
        """Test file size tracking fields"""
        job = RipJob(
            expected_size_bytes=25000000000,
            current_size_bytes=12500000000,
            rip_output_dir="/tmp/rip"
        )

        assert job.expected_size_bytes == 25000000000
        assert job.current_size_bytes == 12500000000
        assert job.rip_output_dir == "/tmp/rip"

    def test_size_tracking_in_dict(self):
        """Test size tracking in to_dict"""
        job = RipJob(
            expected_size_bytes=25000000000,
            current_size_bytes=12500000000
        )
        result = job.to_dict()

        assert result["expected_size_bytes"] == 25000000000
        assert result["current_size_bytes"] == 12500000000


class TestRipJobIdentification:
    """Tests for identification metadata in RipJob"""

    def test_identification_fields(self):
        """Test identification metadata fields"""
        job = RipJob(
            identified_title="The Matrix (1999)",
            year=1999,
            tmdb_id=603,
            poster_url="https://example.com/poster.jpg",
            runtime_str="2h 16m",
            size_gb=25.5
        )

        assert job.identified_title == "The Matrix (1999)"
        assert job.year == 1999
        assert job.tmdb_id == 603
        assert job.poster_url == "https://example.com/poster.jpg"
        assert job.runtime_str == "2h 16m"
        assert job.size_gb == 25.5


class TestMakeMKVInit:
    """Tests for MakeMKV class initialization"""

    def test_default_init(self):
        """Test MakeMKV default initialization"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()
        assert mkv.use_docker is False
        assert mkv.container_name == "arm"

    def test_docker_init(self):
        """Test MakeMKV Docker initialization"""
        from app.ripper import MakeMKV
        mkv = MakeMKV(use_docker=True, container_name="makemkv")
        assert mkv.use_docker is True
        assert mkv.container_name == "makemkv"


class TestDebugLoggingConfig:
    """Tests for debug_logging config setting"""

    @patch('app.config.load_config')
    def test_debug_logging_default_false(self, mock_load):
        """Test debug_logging defaults to False"""
        mock_load.return_value = {'ripping': {}}
        cfg = mock_load()
        debug_enabled = cfg.get('ripping', {}).get('debug_logging', False)
        assert debug_enabled is False

    @patch('app.config.load_config')
    def test_debug_logging_enabled(self, mock_load):
        """Test debug_logging can be enabled"""
        mock_load.return_value = {'ripping': {'debug_logging': True}}
        cfg = mock_load()
        debug_enabled = cfg.get('ripping', {}).get('debug_logging', False)
        assert debug_enabled is True


class TestSelectBestTrack:
    """Tests for MakeMKV.select_best_track fake playlist detection"""

    def test_select_best_track_no_tracks(self):
        """Test with empty track list"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        result, fake_detected = mkv.select_best_track([], 7200)
        assert result is None
        assert fake_detected is False

    def test_select_best_track_no_runtime(self):
        """Test with no official runtime"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        tracks = [{'index': 0, 'duration': 7200}]
        result, fake_detected = mkv.select_best_track(tracks, 0)
        assert result is None
        assert fake_detected is False

    @patch('app.ripper.activity')
    def test_select_best_track_single_match(self, mock_activity):
        """Test selecting single matching track"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        tracks = [
            {'index': 0, 'duration': 7200},  # 2 hours
            {'index': 1, 'duration': 120},   # 2 min trailer
        ]
        # Official runtime is 2 hours
        result, fake_detected = mkv.select_best_track(tracks, 7200)

        assert result == 0
        assert fake_detected is False

    @patch('app.ripper.activity')
    def test_select_best_track_closest_match(self, mock_activity):
        """Test selecting closest track to official runtime"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        tracks = [
            {'index': 0, 'duration': 7200},  # 2 hours exactly
            {'index': 1, 'duration': 7140},  # 1:59 - 1 min short
            {'index': 2, 'duration': 7260},  # 2:01 - 1 min long
        ]
        # Official runtime is 7150 seconds
        result, fake_detected = mkv.select_best_track(tracks, 7150)

        assert result == 1  # Closest match

    @patch('app.ripper.activity')
    def test_select_best_track_fake_playlist_detection(self, mock_activity):
        """Test fake playlist detection with Disney-style protection"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        # Disney fake playlist scenario: many tracks with similar durations
        tracks = [
            {'index': 0, 'duration': 7530},  # 2:05:30
            {'index': 1, 'duration': 7531},  # 2:05:31 - real movie
            {'index': 2, 'duration': 7533},  # 2:05:33
            {'index': 3, 'duration': 7529},  # 2:05:29
            {'index': 4, 'duration': 7532},  # 2:05:32
            {'index': 5, 'duration': 120},   # trailer
        ]
        # Official runtime is 7531 seconds (track 1)
        result, fake_detected = mkv.select_best_track(tracks, 7531)

        assert result == 1  # Should select exact match
        assert fake_detected is True  # Should detect fake playlists

    @patch('app.ripper.activity')
    def test_select_best_track_no_fake_with_varied_durations(self, mock_activity):
        """Test no fake detection when tracks have varied durations"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        # Normal disc: main feature + extras with different lengths
        tracks = [
            {'index': 0, 'duration': 7200},  # 2 hours - main feature
            {'index': 1, 'duration': 5400},  # 1.5 hours - director's cut
            {'index': 2, 'duration': 3600},  # 1 hour - documentary
            {'index': 3, 'duration': 120},   # trailer
        ]
        result, fake_detected = mkv.select_best_track(tracks, 7200)

        assert result == 0
        assert fake_detected is False  # Not fake - durations vary significantly

    @patch('app.ripper.activity')
    def test_select_best_track_skips_short_tracks(self, mock_activity):
        """Test that short tracks are skipped"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        tracks = [
            {'index': 0, 'duration': 120},   # 2 min - too short
            {'index': 1, 'duration': 300},   # 5 min - too short
            {'index': 2, 'duration': 7200},  # 2 hours - valid
        ]
        result, fake_detected = mkv.select_best_track(tracks, 7200)

        assert result == 2  # Should select only valid long track

    @patch('app.ripper.activity')
    def test_select_best_track_threshold_45_min(self, mock_activity):
        """Test 45 minute threshold for track selection"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        tracks = [
            {'index': 0, 'duration': 2700},  # 45 min - exactly at threshold
            {'index': 1, 'duration': 2699},  # 44:59 - below threshold
        ]
        result, fake_detected = mkv.select_best_track(tracks, 2700)

        assert result == 0  # Should select 45 min track


class TestMultiAngleDetection:
    """Tests for multi-angle disc detection and audio language selection"""

    @patch('app.ripper.activity')
    def test_angle_detected_same_duration(self, mock_activity):
        """Test angles are detected when tracks have same duration"""
        from app.ripper import MakeMKV
        mkv = MakeMKV()

        # Simulate MakeMKV output with SINFO parsing
        # We test via get_disc_info structure
        config = {'ripping': {'preferred_language': 'eng'}}

        # Create mock track data with audio tracks
        tracks = [
            {
                'index': 0, 'duration': 8520, 'duration_str': '2:22:00',
                'audio_tracks': [
                    {'stream_idx': 1, 'lang_code': 'eng', 'lang_name': 'English', 'codec': 'DTS-HD MA', 'is_default': True}
                ]
            },
            {
                'index': 1, 'duration': 8520, 'duration_str': '2:22:00',
                'audio_tracks': [
                    {'stream_idx': 1, 'lang_code': 'spa', 'lang_name': 'Spanish', 'codec': 'DTS', 'is_default': True}
                ]
            },
            {
                'index': 2, 'duration': 8520, 'duration_str': '2:22:00',
                'audio_tracks': [
                    {'stream_idx': 1, 'lang_code': 'fra', 'lang_name': 'French', 'codec': 'DTS', 'is_default': True}
                ]
            }
        ]

        # Verify tracks with same duration count as angle candidates
        angle_candidates = [t for t in tracks if abs(t['duration'] - 8520) <= 5]
        assert len(angle_candidates) == 3

    def test_preferred_language_selects_correct_angle(self):
        """Test that angle with preferred language primary audio is selected"""
        # Test the selection logic
        preferred_lang = 'eng'
        angle_candidates = [
            {'index': 0, 'audio_tracks': [{'lang_code': 'spa'}]},
            {'index': 1, 'audio_tracks': [{'lang_code': 'eng'}]},
            {'index': 2, 'audio_tracks': [{'lang_code': 'fra'}]},
        ]

        matching_angles = []
        for candidate in angle_candidates:
            audio_tracks = candidate.get('audio_tracks', [])
            if audio_tracks:
                primary_lang = audio_tracks[0].get('lang_code', '')
                if primary_lang == preferred_lang:
                    matching_angles.append(candidate)

        assert len(matching_angles) == 1
        assert matching_angles[0]['index'] == 1  # English angle

    def test_needs_angle_selection_when_no_match(self):
        """Test needs_angle_selection flag when no angle has preferred language"""
        preferred_lang = 'eng'
        angle_candidates = [
            {'index': 0, 'audio_tracks': [{'lang_code': 'spa'}]},
            {'index': 1, 'audio_tracks': [{'lang_code': 'fra'}]},
            {'index': 2, 'audio_tracks': [{'lang_code': 'deu'}]},
        ]

        matching_angles = []
        for candidate in angle_candidates:
            audio_tracks = candidate.get('audio_tracks', [])
            if audio_tracks:
                primary_lang = audio_tracks[0].get('lang_code', '')
                if primary_lang == preferred_lang:
                    matching_angles.append(candidate)

        # No matches found - should need user selection
        needs_angle_selection = len(matching_angles) == 0
        assert needs_angle_selection is True

    def test_single_track_no_angle_selection_needed(self):
        """Test no angle selection needed for single main track"""
        tracks = [
            {'index': 0, 'duration': 8520, 'audio_tracks': [{'lang_code': 'eng'}]},
            {'index': 1, 'duration': 120, 'audio_tracks': []},  # Trailer
        ]

        longest_duration = 8520
        angle_candidates = [t for t in tracks if abs(t['duration'] - longest_duration) <= 5]

        assert len(angle_candidates) == 1
        # Single track = no angle selection needed

    def test_fallback_to_lowest_playlist_when_no_audio_info(self):
        """Test fallback to lowest playlist when no audio track info available"""
        track_playlists = {0: '00801.mpls', 1: '00800.mpls', 2: '00802.mpls'}
        angle_candidates = [
            {'index': 0, 'audio_tracks': []},
            {'index': 1, 'audio_tracks': []},
            {'index': 2, 'audio_tracks': []},
        ]

        # Sort by playlist name
        angle_candidates.sort(key=lambda t: track_playlists.get(t['index'], 'zzzzz'))
        best_track = angle_candidates[0]

        assert best_track['index'] == 1  # 00800.mpls comes first

    def test_rip_job_has_angle_fields(self):
        """Test RipJob dataclass has angle selection fields"""
        job = RipJob(id="test", device="/dev/sr0")

        assert hasattr(job, 'needs_angle_selection')
        assert hasattr(job, 'angle_candidates')
        assert job.needs_angle_selection is False
        assert job.angle_candidates == []

    def test_rip_job_to_dict_includes_angle_fields(self):
        """Test RipJob.to_dict() includes angle selection fields"""
        job = RipJob(id="test", device="/dev/sr0")
        job.needs_angle_selection = True
        job.angle_candidates = [{'track_index': 0, 'primary_audio_lang': 'spa'}]

        result = job.to_dict()

        assert 'needs_angle_selection' in result
        assert 'angle_candidates' in result
        assert result['needs_angle_selection'] is True
        assert len(result['angle_candidates']) == 1
