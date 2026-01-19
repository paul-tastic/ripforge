"""
Tests for RipForge configuration module
"""

import pytest
import yaml
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLoadConfig:
    """Tests for config loading"""

    def test_load_config_empty_when_no_files(self):
        """Test load_config returns empty dict when no config files exist"""
        from app import config

        with patch.object(config, 'CONFIG_FILE') as mock_config:
            with patch.object(config, 'DEFAULT_CONFIG') as mock_default:
                mock_config.exists.return_value = False
                mock_default.exists.return_value = False

                result = config.load_config()
                assert result == {}

    def test_load_config_from_settings_file(self):
        """Test load_config reads from settings.yaml"""
        from app import config

        test_config = {'ripping': {'min_length': 3000}}

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(test_config, f)
            f.flush()

            with patch.object(config, 'CONFIG_FILE', Path(f.name)):
                result = config.load_config()
                assert result == test_config


class TestSaveConfig:
    """Tests for config saving"""

    def test_save_config_creates_file(self):
        """Test save_config writes YAML file"""
        from app import config

        test_config = {'test': 'value', 'nested': {'key': 123}}

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'test_settings.yaml'

            with patch.object(config, 'CONFIG_FILE', config_path):
                with patch.object(config, 'CONFIG_DIR', Path(tmpdir)):
                    config.save_config(test_config)

                    assert config_path.exists()
                    with open(config_path) as f:
                        loaded = yaml.safe_load(f)
                    assert loaded == test_config


class TestCheckForUpdates:
    """Tests for update checking"""

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_current_version(self, mock_get):
        """Test returns current version when up to date"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'tag_name': 'v1.0.0',
            'html_url': 'https://github.com/example/repo/releases/v1.0.0'
        }
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['current_version'] == '1.0.0'
        assert result['latest_version'] == '1.0.0'
        assert result['update_available'] is False

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_new_version_available(self, mock_get):
        """Test detects when new version is available"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'tag_name': 'v2.0.0',
            'html_url': 'https://github.com/example/repo/releases/v2.0.0'
        }
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['latest_version'] == '2.0.0'
        assert result['update_available'] is True
        assert 'v2.0.0' in result['release_url']

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_minor_version(self, mock_get):
        """Test detects minor version updates"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'tag_name': 'v1.1.0'}
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['update_available'] is True

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_patch_version(self, mock_get):
        """Test detects patch version updates"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'tag_name': 'v1.0.1'}
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['update_available'] is True

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_older_remote(self, mock_get):
        """Test no update when remote is older"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'tag_name': 'v0.9.0'}
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['update_available'] is False

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_no_releases(self, mock_get):
        """Test handles 404 when no releases exist"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['update_available'] is False
        assert result['error'] is None

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_api_error(self, mock_get):
        """Test handles API errors gracefully"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        result = config.check_for_updates()

        assert result['error'] is not None
        assert '500' in result['error']

    @patch('app.config.requests.get')
    @patch('app.__version__', '1.0.0')
    def test_check_for_updates_network_error(self, mock_get):
        """Test handles network errors gracefully"""
        from app import config
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        result = config.check_for_updates()

        assert result['error'] is not None
        assert result['update_available'] is False


class TestTestConnection:
    """Tests for service connection testing"""

    @patch('app.config.requests.get')
    def test_radarr_connection_success(self, mock_get):
        """Test successful Radarr connection"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'version': '4.0.0'}
        mock_get.return_value = mock_response

        result = config.test_connection('radarr', 'http://localhost:7878', 'test_api_key')

        assert result['connected'] is True
        assert result['version'] == '4.0.0'
        assert result['error'] is None

    @patch('app.config.requests.get')
    def test_sonarr_connection_success(self, mock_get):
        """Test successful Sonarr connection"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'version': '3.0.0'}
        mock_get.return_value = mock_response

        result = config.test_connection('sonarr', 'http://localhost:8989', 'test_api_key')

        assert result['connected'] is True
        assert result['version'] == '3.0.0'

    @patch('app.config.requests.get')
    def test_connection_failure(self, mock_get):
        """Test connection failure"""
        from app import config
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = config.test_connection('radarr', 'http://localhost:7878', 'test_api_key')

        assert result['connected'] is False
        assert result['error'] is not None

    @patch('app.config.requests.get')
    def test_plex_connection_success(self, mock_get):
        """Test successful Plex connection"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = config.test_connection('plex', 'http://localhost:32400', token='plex_token')

        assert result['connected'] is True

    @patch('app.config.requests.get')
    def test_tautulli_connection_success(self, mock_get):
        """Test successful Tautulli connection"""
        from app import config

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = config.test_connection('tautulli', 'http://localhost:8181', api_key='tautulli_key')

        assert result['connected'] is True


class TestDetectNedAgent:
    """Tests for Ned agent detection"""

    def test_ned_not_installed(self):
        """Test when Ned agent is not installed"""
        from app import config

        with patch.object(Path, 'exists', return_value=False):
            result = config.detect_ned_agent()

            assert result['installed'] is False
            assert result['config_exists'] is False

    @patch('builtins.open')
    def test_ned_installed_with_config(self, mock_open):
        """Test when Ned agent is installed with config"""
        from app import config

        def path_exists(self):
            path_str = str(self)
            return '/usr/local/bin/ned-agent' in path_str or '/etc/ned/config' in path_str

        mock_file_content = 'api=https://mydashboard.com/api\ntoken=xxx'
        mock_open.return_value.__enter__.return_value = iter(mock_file_content.split('\n'))

        with patch.object(Path, 'exists', path_exists):
            result = config.detect_ned_agent()

            assert result['installed'] is True
            assert result['config_exists'] is True


class TestDetectDockerServices:
    """Tests for Docker service detection"""

    @patch('app.config.subprocess.run')
    def test_detect_radarr_container(self, mock_run):
        """Test detecting Radarr container"""
        from app import config

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'radarr|linuxserver/radarr:latest|0.0.0.0:7878->7878/tcp'
        mock_run.return_value = mock_result

        result = config.detect_docker_services()

        assert 'radarr' in result
        assert result['radarr']['detected'] is True
        assert result['radarr']['url'] == 'http://localhost:7878'

    @patch('app.config.subprocess.run')
    def test_detect_multiple_services(self, mock_run):
        """Test detecting multiple services"""
        from app import config

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '''radarr|linuxserver/radarr|7878
sonarr|linuxserver/sonarr|8989
plex|linuxserver/plex|32400'''
        mock_run.return_value = mock_result

        result = config.detect_docker_services()

        assert 'radarr' in result
        assert 'sonarr' in result
        assert 'plex' in result

    @patch('app.config.subprocess.run')
    def test_detect_no_containers(self, mock_run):
        """Test when no containers running"""
        from app import config

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        result = config.detect_docker_services()

        assert result == {}

    @patch('app.config.subprocess.run')
    def test_detect_docker_not_available(self, mock_run):
        """Test when Docker is not available"""
        from app import config

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mock_run.return_value = mock_result

        result = config.detect_docker_services()

        assert result == {}


class TestFailureLog:
    """Tests for failure log management"""

    def test_get_failure_log_empty(self):
        """Test get_failure_log returns empty list when no log exists"""
        from app import config

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, 'FAILURE_LOG_FILE', Path(tmpdir) / 'failures.json'):
                result = config.get_failure_log()
                assert result == []

    def test_log_failure_creates_entry(self):
        """Test log_failure creates a failure entry"""
        from app import config
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / 'failures.json'
            with patch.object(config, 'FAILURE_LOG_FILE', log_path):
                with patch.object(config, 'CONFIG_DIR', Path(tmpdir)):
                    with patch('app.config.subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stdout='')

                        config.log_failure({
                            'disc_label': 'TEST_DISC',
                            'disc_type': 'DVD',
                            'reason': 'Test failure'
                        })

                        assert log_path.exists()
                        with open(log_path) as f:
                            failures = json.load(f)
                        assert len(failures) == 1
                        assert failures[0]['disc_label'] == 'TEST_DISC'
                        assert failures[0]['attempt_count'] == 1

    def test_log_failure_increments_attempt_count(self):
        """Test log_failure increments attempt count for same disc"""
        from app import config
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / 'failures.json'
            with patch.object(config, 'FAILURE_LOG_FILE', log_path):
                with patch.object(config, 'CONFIG_DIR', Path(tmpdir)):
                    with patch('app.config.subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stdout='')

                        # First failure
                        config.log_failure({'disc_label': 'TEST_DISC', 'reason': 'Fail 1'})
                        # Second failure - same disc
                        config.log_failure({'disc_label': 'TEST_DISC', 'reason': 'Fail 2'})

                        with open(log_path) as f:
                            failures = json.load(f)
                        assert len(failures) == 1  # Only one entry
                        assert failures[0]['attempt_count'] == 2

    def test_clear_failure_log(self):
        """Test clear_failure_log removes the log file"""
        from app import config
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / 'failures.json'
            # Create a log file
            with open(log_path, 'w') as f:
                json.dump([{'disc_label': 'TEST'}], f)

            with patch.object(config, 'FAILURE_LOG_FILE', log_path):
                config.clear_failure_log()
                assert not log_path.exists()

    def test_delete_failure_by_index(self):
        """Test delete_failure removes entry at index"""
        from app import config
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / 'failures.json'
            # Create a log file with multiple entries
            with open(log_path, 'w') as f:
                json.dump([
                    {'disc_label': 'DISC_A'},
                    {'disc_label': 'DISC_B'},
                    {'disc_label': 'DISC_C'}
                ], f)

            with patch.object(config, 'FAILURE_LOG_FILE', log_path):
                config.delete_failure(1)  # Delete DISC_B

                with open(log_path) as f:
                    failures = json.load(f)
                assert len(failures) == 2
                assert failures[0]['disc_label'] == 'DISC_A'
                assert failures[1]['disc_label'] == 'DISC_C'

    def test_failure_log_limits_to_50_entries(self):
        """Test failure log only keeps last 50 entries"""
        from app import config
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / 'failures.json'
            # Create a log file with 50 entries
            existing = [{'disc_label': f'DISC_{i}'} for i in range(50)]
            with open(log_path, 'w') as f:
                json.dump(existing, f)

            with patch.object(config, 'FAILURE_LOG_FILE', log_path):
                with patch.object(config, 'CONFIG_DIR', Path(tmpdir)):
                    with patch('app.config.subprocess.run') as mock_run:
                        mock_run.return_value = MagicMock(returncode=1, stdout='')

                        # Add one more - should push out oldest
                        config.log_failure({'disc_label': 'NEW_DISC', 'reason': 'New failure'})

                        with open(log_path) as f:
                            failures = json.load(f)
                        assert len(failures) == 50
                        assert failures[0]['disc_label'] == 'NEW_DISC'
