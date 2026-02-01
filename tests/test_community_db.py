"""
Tests for RipForge Community Disc Database module
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import community_db


class TestIsEnabled:
    """Tests for is_enabled function"""

    def test_enabled_by_default(self):
        """Test community DB is enabled by default"""
        config = {}
        assert community_db.is_enabled(config) is True

    def test_explicitly_enabled(self):
        """Test explicitly enabled"""
        config = {'community_db': {'enabled': True}}
        assert community_db.is_enabled(config) is True

    def test_explicitly_disabled(self):
        """Test can be disabled"""
        config = {'community_db': {'enabled': False}}
        assert community_db.is_enabled(config) is False


class TestLookupDisc:
    """Tests for lookup_disc function"""

    def test_returns_none_when_disabled(self):
        """Test returns None when community DB disabled"""
        config = {'community_db': {'enabled': False}}
        result = community_db.lookup_disc('TEST_DISC', 7200, config)
        assert result is None

    @patch('app.community_db._check_cache')
    @patch('app.community_db.activity')
    def test_returns_cached_result(self, mock_activity, mock_cache):
        """Test returns cached result when found"""
        mock_cache.return_value = {
            'disc_label': 'TEST_DISC',
            'title': 'Test Movie',
            'year': 2024,
            'tmdb_id': 12345
        }

        config = {'community_db': {'enabled': True}}
        result = community_db.lookup_disc('TEST_DISC', 7200, config)

        assert result is not None
        assert result['title'] == 'Test Movie'
        mock_activity.log_info.assert_called()

    @patch('app.community_db._check_cache')
    @patch('app.community_db.requests.get')
    @patch('app.community_db.activity')
    def test_queries_api_on_cache_miss(self, mock_activity, mock_get, mock_cache):
        """Test queries API when cache miss"""
        mock_cache.return_value = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'found': True,
            'entry': {'title': 'API Movie', 'year': 2024, 'tmdb_id': 99999}
        }
        mock_get.return_value = mock_response

        config = {'community_db': {'enabled': True}}
        result = community_db.lookup_disc('NEW_DISC', 7200, config)

        assert result is not None
        assert result['title'] == 'API Movie'
        mock_get.assert_called_once()

    @patch('app.community_db._check_cache')
    @patch('app.community_db.requests.get')
    @patch('app.community_db.activity')
    def test_returns_none_on_api_not_found(self, mock_activity, mock_get, mock_cache):
        """Test returns None when API returns not found"""
        mock_cache.return_value = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'found': False}
        mock_get.return_value = mock_response

        config = {'community_db': {'enabled': True}}
        result = community_db.lookup_disc('UNKNOWN_DISC', 7200, config)

        assert result is None

    @patch('app.community_db._check_cache')
    @patch('app.community_db.requests.get')
    @patch('app.community_db.activity')
    def test_handles_network_error(self, mock_activity, mock_get, mock_cache):
        """Test handles network errors gracefully"""
        import requests
        mock_cache.return_value = None
        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        config = {'community_db': {'enabled': True}}
        result = community_db.lookup_disc('TEST_DISC', 7200, config)

        assert result is None
        mock_activity.log_warning.assert_called()


class TestContributeDisc:
    """Tests for contribute_disc function"""

    def test_returns_false_when_disabled(self):
        """Test returns False when community DB disabled"""
        config = {'community_db': {'enabled': False}}
        result = community_db.contribute_disc(
            disc_label='TEST',
            disc_type='dvd',
            duration_secs=7200,
            track_count=1,
            title='Test Movie',
            year=2024,
            tmdb_id=12345,
            config=config
        )
        assert result is False

    @patch('app.community_db.activity')
    def test_skips_tv_shows(self, mock_activity):
        """Test skips TV show contributions"""
        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='BREAKING_BAD_S1',
            disc_type='bluray',
            duration_secs=2700,
            track_count=7,
            title='Breaking Bad',
            year=2008,
            tmdb_id=1396,
            config=config,
            media_type='tv'
        )
        assert result is False
        mock_activity.log_info.assert_called()

    @patch('app.community_db.activity')
    def test_skips_generic_disc_labels(self, mock_activity):
        """Test skips generic disc labels like DVD_VIDEO"""
        config = {'community_db': {'enabled': True}}

        for label in ['DVD_VIDEO', 'DVDVIDEO', 'DISC1', 'BLURAY', 'BDROM']:
            result = community_db.contribute_disc(
                disc_label=label,
                disc_type='dvd',
                duration_secs=7200,
                track_count=1,
                title='Test Movie',
                year=2024,
                tmdb_id=12345,
                config=config
            )
            assert result is False

    @patch('app.community_db.activity')
    def test_skips_short_duration(self, mock_activity):
        """Test skips discs with duration under 60 minutes"""
        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='SHORT_VIDEO',
            disc_type='dvd',
            duration_secs=3000,  # 50 minutes
            track_count=1,
            title='Short Video',
            year=2024,
            tmdb_id=12345,
            config=config
        )
        assert result is False

    @patch('app.community_db.activity')
    def test_skips_long_duration(self, mock_activity):
        """Test skips discs with duration over 5 hours"""
        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='MARATHON_VIDEO',
            disc_type='bluray',
            duration_secs=20000,  # Over 5 hours
            track_count=1,
            title='Marathon Video',
            year=2024,
            tmdb_id=12345,
            config=config
        )
        assert result is False

    @patch('app.community_db.activity')
    def test_requires_valid_tmdb_id(self, mock_activity):
        """Test requires valid TMDB ID"""
        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='VALID_DISC',
            disc_type='dvd',
            duration_secs=7200,
            track_count=1,
            title='Test Movie',
            year=2024,
            tmdb_id=0,  # Invalid
            config=config
        )
        assert result is False

    @patch('app.community_db.activity')
    def test_requires_valid_year(self, mock_activity):
        """Test requires valid year"""
        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='VALID_DISC',
            disc_type='dvd',
            duration_secs=7200,
            track_count=1,
            title='Test Movie',
            year=None,  # Invalid
            tmdb_id=12345,
            config=config
        )
        assert result is False

    @patch('app.community_db.requests.post')
    @patch('app.community_db.activity')
    def test_successful_contribution(self, mock_activity, mock_post):
        """Test successful contribution"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True, 'duplicate': False}
        mock_post.return_value = mock_response

        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='NEW_MOVIE_2024',
            disc_type='bluray',
            duration_secs=7200,
            track_count=3,
            title='New Movie',
            year=2024,
            tmdb_id=12345,
            config=config
        )

        assert result is True
        mock_post.assert_called_once()
        mock_activity.log_success.assert_called()

    @patch('app.community_db.requests.post')
    @patch('app.community_db.activity')
    def test_handles_duplicate_gracefully(self, mock_activity, mock_post):
        """Test handles duplicate entries"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True, 'duplicate': True}
        mock_post.return_value = mock_response

        config = {'community_db': {'enabled': True}}
        result = community_db.contribute_disc(
            disc_label='EXISTING_MOVIE',
            disc_type='dvd',
            duration_secs=7200,
            track_count=1,
            title='Existing Movie',
            year=2020,
            tmdb_id=99999,
            config=config
        )

        assert result is True
        mock_activity.log_info.assert_called()


class TestRefreshCache:
    """Tests for refresh_cache function"""

    def test_returns_false_when_disabled(self):
        """Test returns False when disabled"""
        config = {'community_db': {'enabled': False}}
        result = community_db.refresh_cache(config)
        assert result is False

    @patch('app.community_db.requests.get')
    @patch('app.community_db.activity')
    def test_refreshes_cache_on_success(self, mock_activity, mock_get, tmp_path):
        """Test refreshes cache on successful API call"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'count': 100,
            'entries': [
                {'disc_label': 'MOVIE1', 'title': 'Movie 1'},
                {'disc_label': 'MOVIE2', 'title': 'Movie 2'},
            ]
        }
        mock_get.return_value = mock_response

        cache_file = tmp_path / 'cache.json'
        with patch.object(community_db, 'CACHE_FILE', cache_file):
            config = {'community_db': {'enabled': True}}
            result = community_db.refresh_cache(config)

            assert result is True
            assert cache_file.exists()
            mock_activity.log_info.assert_called()

    @patch('app.community_db.requests.get')
    @patch('app.community_db.activity')
    def test_handles_api_error(self, mock_activity, mock_get):
        """Test handles API error gracefully"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        config = {'community_db': {'enabled': True}}
        result = community_db.refresh_cache(config)

        assert result is False
        mock_activity.log_warning.assert_called()


class TestCheckCache:
    """Tests for _check_cache function"""

    def test_returns_none_when_no_cache(self, tmp_path):
        """Test returns None when cache doesn't exist"""
        cache_file = tmp_path / 'nonexistent.json'
        with patch.object(community_db, 'CACHE_FILE', cache_file):
            result = community_db._check_cache('TEST', 7200)
            assert result is None

    def test_returns_none_when_cache_too_old(self, tmp_path):
        """Test returns None when cache is expired"""
        cache_file = tmp_path / 'cache.json'
        old_time = (datetime.now() - timedelta(hours=2)).isoformat()
        cache_data = {
            'updated_at': old_time,
            'entries': [{'disc_label': 'TEST', 'title': 'Test'}]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(community_db, 'CACHE_FILE', cache_file):
            result = community_db._check_cache('TEST', 7200)
            assert result is None

    def test_returns_exact_match(self, tmp_path):
        """Test returns entry on exact disc label match"""
        cache_file = tmp_path / 'cache.json'
        cache_data = {
            'updated_at': datetime.now().isoformat(),
            'entries': [
                {'disc_label': 'MOVIE_2024', 'title': 'Movie', 'duration_secs': 7200}
            ]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(community_db, 'CACHE_FILE', cache_file):
            result = community_db._check_cache('MOVIE_2024', 7200)
            assert result is not None
            assert result['title'] == 'Movie'

    def test_returns_fuzzy_match_by_duration(self, tmp_path):
        """Test returns entry on fuzzy duration match"""
        cache_file = tmp_path / 'cache.json'
        cache_data = {
            'updated_at': datetime.now().isoformat(),
            'entries': [
                {'disc_label': 'DIFFERENT_LABEL', 'title': 'Fuzzy Movie', 'duration_secs': 7200}
            ]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(community_db, 'CACHE_FILE', cache_file):
            # 7100 is within 5% of 7200
            result = community_db._check_cache('UNKNOWN_DISC', 7100)
            assert result is not None
            assert result['title'] == 'Fuzzy Movie'


class TestGetCacheStats:
    """Tests for get_cache_stats function"""

    def test_returns_not_exists_when_no_cache(self, tmp_path):
        """Test returns exists=False when no cache file"""
        cache_file = tmp_path / 'nonexistent.json'
        with patch.object(community_db, 'CACHE_FILE', cache_file):
            stats = community_db.get_cache_stats()
            assert stats['exists'] is False
            assert stats['count'] == 0

    def test_returns_stats_when_cache_exists(self, tmp_path):
        """Test returns proper stats when cache exists"""
        cache_file = tmp_path / 'cache.json'
        cache_data = {
            'updated_at': '2024-01-15T10:30:00',
            'count': 150,
            'entries': []
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(community_db, 'CACHE_FILE', cache_file):
            stats = community_db.get_cache_stats()
            assert stats['exists'] is True
            assert stats['count'] == 150
            assert stats['updated_at'] == '2024-01-15T10:30:00'
