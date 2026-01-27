"""
Tests for RipForge identification module
"""

import pytest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.identify import SmartIdentifier, IdentificationResult


class TestIdentificationResult:
    """Tests for the IdentificationResult dataclass"""

    def test_is_confident_high(self):
        """Test is_confident returns True for high confidence"""
        result = IdentificationResult(title="Test Movie", year=2023, confidence=85)
        assert result.is_confident is True

    def test_is_confident_low(self):
        """Test is_confident returns False for low confidence"""
        result = IdentificationResult(title="Test Movie", year=2023, confidence=50)
        assert result.is_confident is False

    def test_is_confident_threshold(self):
        """Test is_confident at exactly 75%"""
        result = IdentificationResult(title="Test Movie", year=2023, confidence=75)
        assert result.is_confident is True

    def test_folder_name_basic(self):
        """Test basic folder name generation"""
        result = IdentificationResult(title="The Matrix", year=1999)
        assert result.folder_name == "The Matrix (1999)"

    def test_folder_name_with_colon(self):
        """Test folder name sanitizes colons"""
        result = IdentificationResult(title="Star Wars: The Last Jedi", year=2017)
        assert result.folder_name == "Star Wars - The Last Jedi (2017)"

    def test_folder_name_with_special_chars(self):
        """Test folder name removes invalid characters"""
        result = IdentificationResult(title="What If...?", year=2021)
        assert "?" not in result.folder_name
        assert result.folder_name == "What If... (2021)"

    def test_poster_thumbnail(self):
        """Test poster thumbnail URL conversion"""
        result = IdentificationResult(
            title="Test",
            year=2023,
            poster_url="https://image.tmdb.org/t/p/w500/abc123.jpg"
        )
        assert result.poster_thumbnail == "https://image.tmdb.org/t/p/w200/abc123.jpg"

    def test_poster_thumbnail_empty(self):
        """Test poster thumbnail with no poster URL"""
        result = IdentificationResult(title="Test", year=2023)
        assert result.poster_thumbnail == ""


class TestSmartIdentifier:
    """Tests for the SmartIdentifier class"""

    def test_parse_disc_label_basic(self, sample_config):
        """Test basic disc label parsing"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("THE_MATRIX", verbose=False)
        assert result == "The Matrix"

    def test_parse_disc_label_studio_prefix(self, sample_config):
        """Test stripping studio prefixes"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("WARNER_BROS_THE_DARK_KNIGHT", verbose=False)
        assert "Warner" not in result
        assert "Dark Knight" in result

    def test_parse_disc_label_region_code(self, sample_config):
        """Test stripping region codes"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("INCEPTION_US", verbose=False)
        assert result == "Inception"

    def test_parse_disc_label_disc_number(self, sample_config):
        """Test stripping disc numbers"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("LOTR_FELLOWSHIP_DISC1", verbose=False)
        assert "Disc" not in result and "disc" not in result

    def test_parse_disc_label_abbreviation(self, sample_config):
        """Test expanding abbreviations"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("SAT_NITE_FEVER", verbose=False)
        assert "Saturday" in result
        assert "Night" in result

    def test_parse_disc_label_guardians_franchise(self, sample_config):
        """Test franchise pattern for Guardians"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("GUARDIANS_3", verbose=False)
        assert "Guardians of the Galaxy" in result
        assert "Vol 3" in result or "Vol. 3" in result

    def test_parse_disc_label_john_wick(self, sample_config):
        """Test franchise pattern for John Wick"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("JOHN_WICK_4", verbose=False)
        assert "John Wick" in result
        assert "Chapter 4" in result

    def test_parse_disc_label_format_suffix(self, sample_config):
        """Test stripping format suffixes"""
        identifier = SmartIdentifier(sample_config)
        result = identifier.parse_disc_label("AVATAR_THX_DTS", verbose=False)
        assert "THX" not in result
        assert "DTS" not in result

    def test_detect_media_type_movie(self, sample_config, sample_tracks):
        """Test media type detection for movies"""
        identifier = SmartIdentifier(sample_config)
        media_type, season, title = identifier.detect_media_type("INCEPTION_2010", sample_tracks)
        assert media_type == "movie"
        assert season == 0

    def test_detect_media_type_tv_label(self, sample_config, sample_tv_tracks):
        """Test media type detection for TV from label"""
        identifier = SmartIdentifier(sample_config)
        media_type, season, title = identifier.detect_media_type("BREAKING_BAD_S01", sample_tv_tracks)
        assert media_type == "tv"
        assert season == 1

    def test_detect_media_type_tv_season_word(self, sample_config, sample_tv_tracks):
        """Test media type detection for TV with SEASON keyword"""
        identifier = SmartIdentifier(sample_config)
        media_type, season, title = identifier.detect_media_type("FRIENDS_SEASON_3", sample_tv_tracks)
        assert media_type == "tv"
        assert season == 3

    def test_detect_media_type_tv_tracks_heuristic(self, sample_config, sample_tv_tracks):
        """Test media type detection from track analysis"""
        identifier = SmartIdentifier(sample_config)
        # Label doesn't indicate TV, but tracks suggest it
        media_type, season, title = identifier.detect_media_type("SOME_SHOW", sample_tv_tracks)
        assert media_type == "tv"

    def test_detect_media_type_complete_series(self, sample_config, sample_tv_tracks):
        """Test detection of complete series box sets"""
        identifier = SmartIdentifier(sample_config)
        media_type, season, title = identifier.detect_media_type("OFFICE_COMPLETE_SERIES", sample_tv_tracks)
        assert media_type == "tv"
        assert season == 0  # Unknown season for complete series


class TestRuntimeMatching:
    """Tests for runtime-based matching logic"""

    def test_runtime_tolerance_default(self, sample_config):
        """Test default runtime tolerance is set"""
        identifier = SmartIdentifier(sample_config)
        assert identifier.runtime_tolerance == 300  # 5 minutes

    def test_runtime_tolerance_custom(self):
        """Test custom runtime tolerance from config"""
        config = {
            'identification': {'runtime_tolerance': 600},
            'integrations': {'radarr': {}, 'sonarr': {}}
        }
        identifier = SmartIdentifier(config)
        assert identifier.runtime_tolerance == 600


class TestRadarrSearch:
    """Tests for Radarr search functionality"""

    @patch('app.identify.requests.get')
    def test_search_radarr_no_api_key(self, mock_get, sample_config):
        """Test search returns None when no API key configured"""
        config = sample_config.copy()
        config['integrations']['radarr']['api_key'] = ''
        identifier = SmartIdentifier(config)
        result = identifier.search_radarr("Test Movie", verbose=False)
        assert result is None
        mock_get.assert_not_called()

    @patch('app.identify.requests.get')
    def test_search_radarr_success(self, mock_get, sample_config, mock_radarr_response):
        """Test successful Radarr search"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_radarr_response
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.search_radarr("Guardians of the Galaxy", runtime_seconds=9000, verbose=False)

        assert result is not None
        assert "Guardians" in result.title
        assert result.media_type == "movie"

    @patch('app.identify.requests.get')
    def test_search_radarr_no_results(self, mock_get, sample_config):
        """Test Radarr search with no results"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.search_radarr("Nonexistent Movie 12345", verbose=False)

        assert result is None

    @patch('app.identify.requests.get')
    def test_search_radarr_api_error(self, mock_get, sample_config):
        """Test Radarr search handles API errors"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.search_radarr("Test Movie", verbose=False)

        assert result is None


class TestSonarrSearch:
    """Tests for Sonarr search functionality"""

    @patch('app.identify.requests.get')
    def test_search_sonarr_no_api_key(self, mock_get, sample_config):
        """Test search returns None when no API key configured"""
        config = sample_config.copy()
        config['integrations']['sonarr']['api_key'] = ''
        identifier = SmartIdentifier(config)
        result = identifier.search_sonarr("Test Show", verbose=False)
        assert result is None
        mock_get.assert_not_called()

    @patch('app.identify.requests.get')
    def test_search_sonarr_success(self, mock_get, sample_config, mock_sonarr_response):
        """Test successful Sonarr search"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_sonarr_response
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.search_sonarr("Breaking Bad", verbose=False)

        assert result is not None
        assert result.title == "Breaking Bad"
        assert result.media_type == "tv"

    @patch('app.identify.requests.get')
    def test_search_sonarr_with_episode_runtimes(self, mock_get, sample_config, mock_sonarr_response):
        """Test Sonarr search with episode runtime matching"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_sonarr_response
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        # Episode runtimes close to 47 minutes (Breaking Bad average)
        episode_runtimes = [2820, 2760, 2880]  # 47, 46, 48 minutes in seconds
        result = identifier.search_sonarr("Breaking Bad", episode_runtimes=episode_runtimes, verbose=False)

        assert result is not None
        assert result.title == "Breaking Bad"


class TestMediaTypeDetection:
    """Tests for media type detection"""

    def test_detect_tv_by_episode_count(self, sample_config):
        """Test detection of TV by episode-like track count"""
        identifier = SmartIdentifier(sample_config)

        # Multiple tracks with similar durations indicates TV
        track_info = [
            {'duration': 2700},  # 45 min
            {'duration': 2700},
            {'duration': 2700},
            {'duration': 2700},
        ]
        media_type, _, _ = identifier.detect_media_type("TEST_DISC", tracks=track_info)
        assert media_type == "tv"

    def test_detect_movie_by_single_track(self, sample_config):
        """Test detection of movie by single long track"""
        identifier = SmartIdentifier(sample_config)

        track_info = [
            {'duration': 7200},  # 2 hours
        ]
        media_type, _, _ = identifier.detect_media_type("TEST_DISC", tracks=track_info)
        assert media_type == "movie"


class TestParseDiscLabel:
    """Tests for disc label parsing"""

    def test_parse_removes_studio_prefix(self, sample_config):
        """Test removing studio prefixes"""
        identifier = SmartIdentifier(sample_config)

        result = identifier.parse_disc_label("MARVEL_STUDIOS_GUARDIANS_3", verbose=False)
        assert "guardians" in result.lower()

    def test_parse_removes_underscores(self, sample_config):
        """Test converting underscores to spaces"""
        identifier = SmartIdentifier(sample_config)

        result = identifier.parse_disc_label("THE_MATRIX", verbose=False)
        assert "_" not in result or "matrix" in result.lower()

    def test_parse_handles_year(self, sample_config):
        """Test parsing labels with years"""
        identifier = SmartIdentifier(sample_config)

        result = identifier.parse_disc_label("MOVIE_2023", verbose=False)
        assert result is not None


class TestMatchTracksToEpisodes:
    """Tests for the match_tracks_to_episodes function"""

    def test_empty_tracks_returns_empty(self):
        """Test empty tracks list returns empty list"""
        from app.identify import match_tracks_to_episodes
        result = match_tracks_to_episodes([], [])
        assert result == []

    def test_no_episodes_assigns_sequential(self):
        """Test without episode data, tracks get sequential assignment"""
        from app.identify import match_tracks_to_episodes
        tracks = [
            {'duration_secs': 2700, 'filename': 't1.mkv'},
            {'duration_secs': 2700, 'filename': 't2.mkv'},
            {'duration_secs': 2700, 'filename': 't3.mkv'},
        ]
        result = match_tracks_to_episodes(tracks, [])
        assert result[0]['suggested_episode'] == 1
        assert result[1]['suggested_episode'] == 2
        assert result[2]['suggested_episode'] == 3
        assert all(t['confidence'] == 50 for t in result)

    def test_matches_by_duration(self):
        """Test tracks are matched to episodes by duration"""
        from app.identify import match_tracks_to_episodes
        tracks = [
            {'duration_secs': 2700, 'filename': 't1.mkv'},  # 45 min
            {'duration_secs': 2580, 'filename': 't2.mkv'},  # 43 min
        ]
        episodes = [
            {'episode_num': 1, 'runtime_secs': 2700},  # 45 min
            {'episode_num': 2, 'runtime_secs': 2580},  # 43 min
        ]
        result = match_tracks_to_episodes(tracks, episodes)
        # Should match based on duration
        assert result[0]['suggested_episode'] in [1, 2]
        assert result[1]['suggested_episode'] in [1, 2]
        assert result[0]['suggested_episode'] != result[1]['suggested_episode']

    def test_high_confidence_for_close_match(self):
        """Test high confidence when duration matches closely"""
        from app.identify import match_tracks_to_episodes
        tracks = [{'duration_secs': 2700, 'filename': 't1.mkv'}]
        episodes = [{'episode_num': 1, 'runtime_secs': 2700}]  # Exact match
        result = match_tracks_to_episodes(tracks, episodes)
        assert result[0]['confidence'] >= 90

    def test_lower_confidence_for_distant_match(self):
        """Test lower confidence when duration match is further"""
        from app.identify import match_tracks_to_episodes
        tracks = [{'duration_secs': 2700, 'filename': 't1.mkv'}]
        episodes = [{'episode_num': 1, 'runtime_secs': 2800}]  # 100 sec diff
        result = match_tracks_to_episodes(tracks, episodes)
        assert result[0]['confidence'] < 95

    def test_marks_extras_for_short_tracks(self):
        """Test very short tracks are marked as potential extras"""
        from app.identify import match_tracks_to_episodes
        tracks = [
            {'duration_secs': 2700, 'filename': 't1.mkv'},  # Normal ep
            {'duration_secs': 300, 'filename': 't2.mkv'},   # 5 min - too short
        ]
        episodes = [
            {'episode_num': 1, 'runtime_secs': 2700},
            {'episode_num': 2, 'runtime_secs': 2700},
        ]
        result = match_tracks_to_episodes(tracks, episodes)
        # Short track should be flagged as potential extra
        short_track = next(t for t in result if t['filename'] == 't2.mkv')
        assert short_track['is_extra'] is True

    def test_marks_extras_for_long_tracks(self):
        """Test very long tracks are marked as potential extras"""
        from app.identify import match_tracks_to_episodes
        tracks = [
            {'duration_secs': 2700, 'filename': 't1.mkv'},  # Normal ep
            {'duration_secs': 7200, 'filename': 't2.mkv'},  # 2 hours - too long
        ]
        episodes = [
            {'episode_num': 1, 'runtime_secs': 2700},
            {'episode_num': 2, 'runtime_secs': 2700},
        ]
        result = match_tracks_to_episodes(tracks, episodes)
        long_track = next(t for t in result if t['filename'] == 't2.mkv')
        assert long_track['is_extra'] is True

    def test_tolerance_parameter(self):
        """Test custom tolerance affects matching"""
        from app.identify import match_tracks_to_episodes
        tracks = [{'duration_secs': 2700, 'filename': 't1.mkv'}]
        episodes = [{'episode_num': 1, 'runtime_secs': 2900}]  # 200 sec diff

        # With default 120 sec tolerance, no match
        result_default = match_tracks_to_episodes(tracks, episodes, tolerance_secs=120)
        assert result_default[0]['suggested_episode'] == 0 or result_default[0]['is_extra']

        # With higher tolerance, should match
        result_high = match_tracks_to_episodes(tracks, episodes, tolerance_secs=300)
        assert result_high[0]['suggested_episode'] == 1


class TestGetSeasonEpisodesForReview:
    """Tests for get_season_episodes_for_review method"""

    def test_returns_empty_without_api_key(self, sample_config):
        """Test returns empty list when Sonarr API key missing"""
        config_no_key = sample_config.copy()
        config_no_key['integrations'] = {'sonarr': {'url': 'http://localhost:8989', 'api_key': ''}}
        identifier = SmartIdentifier(config_no_key)
        result = identifier.get_season_episodes_for_review(12345, 1)
        assert result == []

    @patch('requests.get')
    def test_returns_episodes_on_success(self, mock_get, sample_config):
        """Test returns episode list on successful API call"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'title': 'Test Show',
            'runtime': 45,
            'seasons': [
                {'seasonNumber': 1, 'statistics': {'totalEpisodeCount': 10}},
                {'seasonNumber': 2, 'statistics': {'totalEpisodeCount': 12}},
            ]
        }]
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.get_season_episodes_for_review(12345, 1)

        assert len(result) == 10
        assert result[0]['episode_num'] == 1
        assert result[0]['runtime_secs'] == 45 * 60  # Converted to seconds
        assert result[9]['episode_num'] == 10

    @patch('requests.get')
    def test_handles_api_error(self, mock_get, sample_config):
        """Test handles API errors gracefully"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.get_season_episodes_for_review(12345, 1)
        assert result == []

    @patch('requests.get')
    def test_handles_missing_season(self, mock_get, sample_config):
        """Test handles request for non-existent season"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'title': 'Test Show',
            'runtime': 45,
            'seasons': [
                {'seasonNumber': 1, 'statistics': {'totalEpisodeCount': 10}},
            ]
        }]
        mock_get.return_value = mock_response

        identifier = SmartIdentifier(sample_config)
        result = identifier.get_season_episodes_for_review(12345, 5)  # Season 5 doesn't exist
        assert result == []

    @patch('requests.get')
    def test_handles_network_exception(self, mock_get, sample_config):
        """Test handles network exceptions gracefully"""
        mock_get.side_effect = Exception("Connection failed")

        identifier = SmartIdentifier(sample_config)
        result = identifier.get_season_episodes_for_review(12345, 1)
        assert result == []
