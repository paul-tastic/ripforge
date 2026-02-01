"""
Tests for RipForge Library Export module
"""

import pytest
import io
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Skip tests if reportlab is not installed (dev dependency)
pytest.importorskip('reportlab', reason='reportlab not installed')

from app import library_export


class TestFetchMoviesFromRadarr:
    """Tests for fetch_movies_from_radarr function"""

    @patch('app.library_export.config.load_config')
    def test_returns_empty_when_radarr_disabled(self, mock_config):
        """Test returns empty list when Radarr disabled"""
        mock_config.return_value = {
            'integrations': {'radarr': {'enabled': False}}
        }

        result = library_export.fetch_movies_from_radarr()

        assert result == []

    @patch('app.library_export.config.load_config')
    def test_returns_empty_when_no_api_key(self, mock_config):
        """Test returns empty list when no API key"""
        mock_config.return_value = {
            'integrations': {'radarr': {'enabled': True, 'api_key': ''}}
        }

        result = library_export.fetch_movies_from_radarr()

        assert result == []

    @patch('app.library_export.requests.get')
    @patch('app.library_export.config.load_config')
    def test_fetches_and_sorts_movies(self, mock_config, mock_get):
        """Test fetches movies and sorts alphabetically"""
        mock_config.return_value = {
            'integrations': {
                'radarr': {
                    'enabled': True,
                    'url': 'http://localhost:7878',
                    'api_key': 'test_key'
                }
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {'title': 'Zebra Movie', 'year': 2024},
            {'title': 'Apple Movie', 'year': 2023},
            {'title': 'Moon Movie', 'year': 2022},
        ]
        mock_get.return_value = mock_resp

        result = library_export.fetch_movies_from_radarr()

        assert len(result) == 3
        assert result[0]['title'] == 'Apple Movie'
        assert result[1]['title'] == 'Moon Movie'
        assert result[2]['title'] == 'Zebra Movie'

    @patch('app.library_export.requests.get')
    @patch('app.library_export.config.load_config')
    def test_handles_api_error(self, mock_config, mock_get):
        """Test handles API error gracefully"""
        mock_config.return_value = {
            'integrations': {
                'radarr': {
                    'enabled': True,
                    'url': 'http://localhost:7878',
                    'api_key': 'test_key'
                }
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = library_export.fetch_movies_from_radarr()

        assert result == []

    @patch('app.library_export.requests.get')
    @patch('app.library_export.config.load_config')
    def test_handles_network_exception(self, mock_config, mock_get):
        """Test handles network exceptions gracefully"""
        mock_config.return_value = {
            'integrations': {
                'radarr': {
                    'enabled': True,
                    'url': 'http://localhost:7878',
                    'api_key': 'test_key'
                }
            }
        }
        mock_get.side_effect = Exception("Connection refused")

        result = library_export.fetch_movies_from_radarr()

        assert result == []


class TestFetchShowsFromSonarr:
    """Tests for fetch_shows_from_sonarr function"""

    @patch('app.library_export.config.load_config')
    def test_returns_empty_when_sonarr_disabled(self, mock_config):
        """Test returns empty list when Sonarr disabled"""
        mock_config.return_value = {
            'integrations': {'sonarr': {'enabled': False}}
        }

        result = library_export.fetch_shows_from_sonarr()

        assert result == []

    @patch('app.library_export.config.load_config')
    def test_returns_empty_when_no_api_key(self, mock_config):
        """Test returns empty list when no API key"""
        mock_config.return_value = {
            'integrations': {'sonarr': {'enabled': True, 'api_key': ''}}
        }

        result = library_export.fetch_shows_from_sonarr()

        assert result == []

    @patch('app.library_export.requests.get')
    @patch('app.library_export.config.load_config')
    def test_fetches_and_sorts_shows(self, mock_config, mock_get):
        """Test fetches shows and sorts alphabetically"""
        mock_config.return_value = {
            'integrations': {
                'sonarr': {
                    'enabled': True,
                    'url': 'http://localhost:8989',
                    'api_key': 'test_key'
                }
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {'title': 'Zeta Show', 'year': 2020},
            {'title': 'Alpha Show', 'year': 2019},
        ]
        mock_get.return_value = mock_resp

        result = library_export.fetch_shows_from_sonarr()

        assert len(result) == 2
        assert result[0]['title'] == 'Alpha Show'
        assert result[1]['title'] == 'Zeta Show'


class TestDownloadPoster:
    """Tests for download_poster function"""

    def test_returns_none_for_empty_url(self):
        """Test returns None for empty URL"""
        result = library_export.download_poster('')
        assert result is None

    def test_returns_none_for_none_url(self):
        """Test returns None for None URL"""
        result = library_export.download_poster(None)
        assert result is None

    @patch('app.library_export.requests.get')
    def test_downloads_and_creates_image(self, mock_get):
        """Test downloads poster and creates Image object"""
        # Create minimal valid JPEG data
        jpeg_header = b'\xff\xd8\xff\xe0\x00\x10JFIF'

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = jpeg_header + b'\x00' * 100
        mock_get.return_value = mock_resp

        # Can't easily test Image creation without reportlab
        # Just test the request is made
        with patch('app.library_export.Image') as mock_image:
            mock_image.return_value = MagicMock()
            result = library_export.download_poster('https://example.com/poster.jpg')
            mock_get.assert_called_once()

    @patch('app.library_export.requests.get')
    def test_handles_download_error(self, mock_get):
        """Test handles download errors gracefully"""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        result = library_export.download_poster('https://example.com/missing.jpg')

        assert result is None

    @patch('app.library_export.requests.get')
    def test_handles_exception(self, mock_get):
        """Test handles exceptions gracefully"""
        mock_get.side_effect = Exception("Network error")

        result = library_export.download_poster('https://example.com/poster.jpg')

        assert result is None


class TestGenerateLibraryPdf:
    """Tests for generate_library_pdf function"""

    @patch('app.library_export.fetch_shows_from_sonarr')
    @patch('app.library_export.fetch_movies_from_radarr')
    def test_generates_pdf_file(self, mock_movies, mock_shows, tmp_path):
        """Test generates PDF file"""
        mock_movies.return_value = [
            {'title': 'Test Movie', 'year': 2024, 'images': []}
        ]
        mock_shows.return_value = []

        with patch.object(library_export, 'EXPORT_DIR', tmp_path):
            result = library_export.generate_library_pdf(
                include_movies=True,
                include_shows=False,
                include_images=False,
                filename='test_export'
            )

            assert 'test_export.pdf' in result
            assert Path(result).exists()

    @patch('app.library_export.fetch_shows_from_sonarr')
    @patch('app.library_export.fetch_movies_from_radarr')
    def test_uses_timestamp_filename_by_default(self, mock_movies, mock_shows, tmp_path):
        """Test uses timestamp-based filename when not specified"""
        mock_movies.return_value = []
        mock_shows.return_value = []

        with patch.object(library_export, 'EXPORT_DIR', tmp_path):
            result = library_export.generate_library_pdf(
                include_movies=True,
                include_shows=True
            )

            assert 'library_export_' in result

    @patch('app.library_export.fetch_shows_from_sonarr')
    @patch('app.library_export.fetch_movies_from_radarr')
    def test_includes_movies_section(self, mock_movies, mock_shows, tmp_path):
        """Test includes movies when include_movies=True"""
        mock_movies.return_value = [
            {'title': 'Movie A', 'year': 2024, 'images': []},
            {'title': 'Movie B', 'year': 2023, 'images': []},
        ]
        mock_shows.return_value = []

        with patch.object(library_export, 'EXPORT_DIR', tmp_path):
            result = library_export.generate_library_pdf(
                include_movies=True,
                include_shows=False
            )

            assert Path(result).exists()
            mock_movies.assert_called_once()

    @patch('app.library_export.fetch_shows_from_sonarr')
    @patch('app.library_export.fetch_movies_from_radarr')
    def test_includes_shows_section(self, mock_movies, mock_shows, tmp_path):
        """Test includes TV shows when include_shows=True"""
        mock_movies.return_value = []
        mock_shows.return_value = [
            {'title': 'Show A', 'year': 2020, 'images': []},
        ]

        with patch.object(library_export, 'EXPORT_DIR', tmp_path):
            result = library_export.generate_library_pdf(
                include_movies=False,
                include_shows=True
            )

            assert Path(result).exists()
            mock_shows.assert_called_once()

    @patch('app.library_export.fetch_shows_from_sonarr')
    @patch('app.library_export.fetch_movies_from_radarr')
    def test_skips_movies_when_disabled(self, mock_movies, mock_shows, tmp_path):
        """Test skips movies when include_movies=False"""
        mock_movies.return_value = []
        mock_shows.return_value = []

        with patch.object(library_export, 'EXPORT_DIR', tmp_path):
            library_export.generate_library_pdf(
                include_movies=False,
                include_shows=True
            )

            mock_movies.assert_not_called()


class TestExportDir:
    """Tests for export directory handling"""

    def test_export_dir_exists(self):
        """Test EXPORT_DIR is defined"""
        assert library_export.EXPORT_DIR is not None
        assert isinstance(library_export.EXPORT_DIR, Path)
