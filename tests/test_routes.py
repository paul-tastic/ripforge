"""
Tests for RipForge Flask routes
"""

import pytest
from unittest.mock import patch, MagicMock, mock_open
import json

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def app():
    """Create Flask test client"""
    # Import here to avoid circular imports
    from run import create_app
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Create Flask test client"""
    return app.test_client()


class TestIndexRoute:
    """Tests for the index/dashboard route"""

    def test_index_returns_200(self, client):
        """Test index page loads successfully"""
        response = client.get('/')
        assert response.status_code == 200

    def test_index_renders_template(self, client):
        """Test index page contains expected content"""
        response = client.get('/')
        assert b'RipForge' in response.data or b'ripforge' in response.data.lower()


class TestSettingsRoute:
    """Tests for the settings route"""

    def test_settings_returns_200(self, client):
        """Test settings page loads successfully"""
        response = client.get('/settings')
        assert response.status_code == 200


class TestHistoryRoute:
    """Tests for the history route"""

    def test_history_returns_200(self, client):
        """Test history page loads successfully"""
        response = client.get('/history')
        assert response.status_code == 200


class TestFailuresRoute:
    """Tests for the failures route"""

    def test_failures_returns_200(self, client):
        """Test failures page loads successfully"""
        response = client.get('/failures')
        assert response.status_code == 200

    def test_api_failures_returns_json(self, client):
        """Test failures API returns JSON"""
        response = client.get('/api/failures')
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_api_failures_has_failures_key(self, client):
        """Test failures API returns failures array"""
        response = client.get('/api/failures')
        data = json.loads(response.data)
        assert 'failures' in data
        assert isinstance(data['failures'], list)

    def test_api_failures_delete(self, client):
        """Test clearing failures via DELETE"""
        with patch('app.config.clear_failure_log') as mock_clear:
            response = client.delete('/api/failures')
            assert response.status_code == 200
            mock_clear.assert_called_once()

    def test_api_failures_delete_single(self, client):
        """Test deleting single failure by index"""
        with patch('app.config.delete_failure') as mock_delete:
            response = client.delete('/api/failures/0')
            assert response.status_code == 200
            mock_delete.assert_called_once_with(0)


class TestAPIStatus:
    """Tests for the /api/status endpoint"""

    def test_api_status_returns_json(self, client):
        """Test status API returns JSON"""
        response = client.get('/api/status')
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_api_status_has_required_keys(self, client):
        """Test status API returns required fields"""
        response = client.get('/api/status')
        data = json.loads(response.data)

        assert 'integrations' in data
        assert 'drives' in data
        assert 'ripping' in data


class TestAPISettings:
    """Tests for the /api/settings endpoint"""

    def test_get_settings(self, client):
        """Test GET settings returns config"""
        response = client.get('/api/settings')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)

    @patch('app.routes.config.save_config')
    @patch('app.routes.config.load_config')
    def test_post_settings(self, mock_load, mock_save, client):
        """Test POST settings updates config"""
        mock_load.return_value = {'test': 'config'}

        response = client.post(
            '/api/settings',
            data=json.dumps({'test': 'updated'}),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True


class TestAPIVersion:
    """Tests for the /api/version endpoint"""

    @patch('app.routes.config.check_for_updates')
    def test_api_version(self, mock_check, client):
        """Test version API returns version info"""
        mock_check.return_value = {
            'current': '0.2.2',
            'latest': '0.2.2',
            'update_available': False
        }

        response = client.get('/api/version')
        assert response.status_code == 200
        data = json.loads(response.data)

        assert 'current' in data
        assert 'latest' in data


class TestAPIRipStatus:
    """Tests for the /api/rip/status endpoint"""

    def test_rip_status_idle(self, client):
        """Test rip status when idle"""
        response = client.get('/api/rip/status')
        assert response.status_code == 200
        data = json.loads(response.data)
        # Should return status (either idle or current job status)
        assert 'status' in data or 'id' in data


class TestAPIRipReset:
    """Tests for the /api/rip/reset endpoint"""

    @patch('app.routes.ripper.get_engine')
    def test_rip_reset_no_engine(self, mock_engine, client):
        """Test reset when engine not initialized"""
        mock_engine.return_value = None

        response = client.post('/api/rip/reset')
        assert response.status_code == 500
        data = json.loads(response.data)
        assert data['success'] is False

    @patch('app.routes.ripper.get_engine')
    def test_rip_reset_success(self, mock_engine, client):
        """Test successful reset"""
        mock_engine_instance = MagicMock()
        mock_engine.return_value = mock_engine_instance

        response = client.post('/api/rip/reset')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        mock_engine_instance.reset_job.assert_called_once()


class TestAPIActivityLog:
    """Tests for the /api/activity-log endpoint"""

    def test_activity_log_returns_json(self, client):
        """Test activity log returns JSON"""
        response = client.get('/api/activity-log')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'log' in data
        assert isinstance(data['log'], list)


class TestAPIRipHistory:
    """Tests for the /api/rip-history endpoint"""

    def test_rip_history_returns_json(self, client):
        """Test rip history returns JSON with rips list"""
        response = client.get('/api/rip-history')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'rips' in data
        assert isinstance(data['rips'], list)

    @patch('builtins.open', mock_open(read_data='[{"title": "Test Movie", "year": 2024}]'))
    @patch('pathlib.Path.exists', return_value=True)
    def test_rip_history_with_data(self, mock_exists, client):
        """Test rip history returns data from file"""
        response = client.get('/api/rip-history')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'rips' in data


class TestAPIHardware:
    """Tests for the /api/hardware endpoint"""

    def test_hardware_returns_json(self, client):
        """Test hardware info returns JSON"""
        response = client.get('/api/hardware')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, dict)


class TestAPIRipStats:
    """Tests for the /api/rip-stats endpoint"""

    def test_rip_stats_returns_json(self, client):
        """Test rip stats returns expected fields"""
        response = client.get('/api/rip-stats')
        assert response.status_code == 200
        data = json.loads(response.data)

        assert 'today' in data
        assert 'week' in data
        assert 'total' in data
        assert 'errors' in data


class TestAPINewsletterQueue:
    """Tests for newsletter queue endpoints"""

    def test_get_newsletter_queue(self, client):
        """Test getting newsletter queue"""
        response = client.get('/api/newsletter/queue')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'queue' in data


class TestAPIEmailTest:
    """Tests for email test endpoint"""

    @patch('app.routes.email_utils.send_test_email')
    @patch('app.routes.config.load_config')
    def test_email_test_no_recipients(self, mock_config, mock_send, client):
        """Test email test with no recipients configured"""
        mock_config.return_value = {'notifications': {'email': {'recipients': []}}}

        response = client.post(
            '/api/email/test',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is False
        assert 'No recipients' in data.get('error', '')


class TestAPIUpdate:
    """Tests for the /api/update endpoint"""

    @patch('app.routes.subprocess.run')
    def test_update_git_pull_failure(self, mock_run, client):
        """Test update handles git pull failure"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error: cannot pull"
        mock_run.return_value = mock_result

        response = client.post('/api/update')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is False
        assert 'Git pull failed' in data.get('error', '')


class TestReviewQueue:
    """Tests for review queue endpoints"""

    def test_get_review_queue(self, client):
        """Test getting review queue"""
        response = client.get('/api/review/queue')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'items' in data
        assert 'total' in data

    def test_review_search_missing_query(self, client):
        """Test review search requires query"""
        response = client.post(
            '/api/review/search',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_review_apply_missing_params(self, client):
        """Test review apply requires parameters"""
        response = client.post(
            '/api/review/apply',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400

    def test_review_delete_missing_folder(self, client):
        """Test review delete requires folder_name"""
        response = client.post(
            '/api/review/delete',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400


class TestNotificationsRoute:
    """Tests for the /notifications page"""

    def test_notifications_returns_200(self, client):
        """Test notifications page loads"""
        response = client.get('/notifications')
        assert response.status_code == 200


class TestAPIDiscCheck:
    """Tests for the /api/disc/check endpoint"""

    @patch('app.ripper.get_engine')
    def test_disc_check_no_engine(self, mock_engine, client):
        """Test disc check when no engine"""
        mock_engine.return_value = None
        response = client.get('/api/disc/check')
        assert response.status_code == 200

    @patch('app.ripper.get_engine')
    def test_disc_check_with_engine(self, mock_engine, client):
        """Test disc check with engine"""
        mock_engine.return_value.check_disc.return_value = {'present': True, 'label': 'TEST_DISC'}
        response = client.get('/api/disc/check')
        assert response.status_code == 200


class TestAPIDriveEject:
    """Tests for the /api/drive/eject endpoint"""

    @patch('app.ripper.get_engine')
    def test_drive_eject_no_engine(self, mock_engine, client):
        """Test drive eject when no engine"""
        mock_engine.return_value = None
        response = client.post('/api/drive/eject')
        assert response.status_code in [200, 500]


class TestAPIDriveReset:
    """Tests for the /api/drive/reset endpoint"""

    @patch('app.ripper.get_engine')
    def test_drive_reset_no_engine(self, mock_engine, client):
        """Test drive reset when no engine"""
        mock_engine.return_value = None
        response = client.post('/api/drive/reset')
        assert response.status_code in [200, 500]


class TestAPITestConnection:
    """Tests for the /api/test-connection endpoint"""

    def test_test_connection_missing_params(self, client):
        """Test connection test requires parameters"""
        response = client.post(
            '/api/test-connection',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code in [200, 400]

    def test_test_connection_invalid_service(self, client):
        """Test connection with invalid service"""
        response = client.post(
            '/api/test-connection',
            data=json.dumps({'service': 'invalid', 'url': 'http://localhost', 'api_key': 'test'}),
            content_type='application/json'
        )
        assert response.status_code == 200


class TestAPIImportKeys:
    """Tests for the /api/import-keys endpoint"""

    @patch('app.routes.config.load_config')
    @patch('app.routes.config.save_config')
    def test_import_keys(self, mock_save, mock_load, client):
        """Test import keys endpoint"""
        mock_load.return_value = {'integrations': {}}
        response = client.post('/api/import-keys')
        assert response.status_code == 200


class TestAPIRipHistory:
    """Tests for the /api/rip-history endpoint"""

    def test_rip_history_returns_json(self, client):
        """Test rip history returns JSON"""
        response = client.get('/api/rip-history')
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_rip_history_has_rips_key(self, client):
        """Test rip history has rips array"""
        response = client.get('/api/rip-history')
        data = json.loads(response.data)
        assert 'rips' in data
        assert isinstance(data['rips'], list)


class TestAPIActivityLog:
    """Tests for the /api/activity-log endpoint"""

    def test_activity_log_returns_json(self, client):
        """Test activity log returns JSON"""
        response = client.get('/api/activity-log')
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_activity_log_has_log_key(self, client):
        """Test activity log has log array"""
        response = client.get('/api/activity-log')
        data = json.loads(response.data)
        assert 'log' in data


class TestAPIRipStats:
    """Tests for the /api/rip-stats endpoint"""

    def test_rip_stats_returns_json(self, client):
        """Test rip stats returns JSON"""
        response = client.get('/api/rip-stats')
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_rip_stats_has_expected_keys(self, client):
        """Test rip stats has expected structure"""
        response = client.get('/api/rip-stats')
        data = json.loads(response.data)
        assert 'today' in data
        assert 'week' in data


class TestAPIDriveStatus:
    """Tests for the /api/drive/status endpoint"""

    def test_drive_status_returns_json(self, client):
        """Test drive status returns JSON"""
        response = client.get('/api/drive/status')
        assert response.status_code == 200
        assert response.content_type == 'application/json'


class TestLibraryPage:
    """Tests for the /library page"""

    def test_library_returns_200(self, client):
        """Test library page loads successfully"""
        response = client.get('/library')
        assert response.status_code == 200

    def test_library_renders_template(self, client):
        """Test library page contains expected content"""
        response = client.get('/library')
        assert b'Library' in response.data


class TestAPILibraryList:
    """Tests for the /api/library/list endpoint"""

    def test_library_list_returns_json(self, client):
        """Test library list returns JSON"""
        response = client.get('/api/library/list')
        assert response.status_code == 200
        assert response.content_type == 'application/json'

    def test_library_list_has_expected_keys(self, client):
        """Test library list has movies and tv keys"""
        response = client.get('/api/library/list')
        data = json.loads(response.data)
        assert 'movies' in data
        assert 'tv' in data
        assert isinstance(data['movies'], list)
        assert isinstance(data['tv'], list)


class TestAPILibraryRename:
    """Tests for the /api/library/rename endpoint"""

    def test_library_rename_missing_params(self, client):
        """Test rename requires parameters"""
        response = client.post(
            '/api/library/rename',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False

    def test_library_rename_missing_title(self, client):
        """Test rename requires new_title"""
        response = client.post(
            '/api/library/rename',
            data=json.dumps({'old_folder': 'Test Movie (2024)'}),
            content_type='application/json'
        )
        assert response.status_code == 400

    @patch('app.routes.config.load_config')
    def test_library_rename_folder_not_found(self, mock_config, client):
        """Test rename with non-existent folder"""
        mock_config.return_value = {'paths': {'movies': '/tmp/nonexistent'}}
        response = client.post(
            '/api/library/rename',
            data=json.dumps({
                'old_folder': 'Nonexistent Movie (2024)',
                'new_title': 'New Title',
                'new_year': '2024',
                'media_type': 'movies'
            }),
            content_type='application/json'
        )
        assert response.status_code == 404


class TestAPILibraryDelete:
    """Tests for the /api/library/delete endpoint"""

    def test_library_delete_missing_folder(self, client):
        """Test delete requires folder_name"""
        response = client.post(
            '/api/library/delete',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False

    @patch('app.routes.config.load_config')
    def test_library_delete_folder_not_found(self, mock_config, client):
        """Test delete with non-existent folder"""
        mock_config.return_value = {'paths': {'movies': '/tmp/nonexistent'}}
        response = client.post(
            '/api/library/delete',
            data=json.dumps({
                'folder_name': 'Nonexistent Movie (2024)',
                'media_type': 'movies'
            }),
            content_type='application/json'
        )
        assert response.status_code == 404


class TestAPILibraryRescanPlex:
    """Tests for the /api/library/rescan-plex endpoint"""

    @patch('app.routes.config.trigger_plex_scan')
    def test_rescan_plex_calls_trigger(self, mock_trigger, client):
        """Test rescan calls trigger_plex_scan"""
        mock_trigger.return_value = {'success': True, 'scanned': ['Movies']}
        response = client.post(
            '/api/library/rescan-plex',
            data=json.dumps({'library_type': 'movies'}),
            content_type='application/json'
        )
        assert response.status_code == 200
        mock_trigger.assert_called_once_with('movies')

    @patch('app.routes.config.trigger_plex_scan')
    def test_rescan_plex_default_all(self, mock_trigger, client):
        """Test rescan defaults to all libraries"""
        mock_trigger.return_value = {'success': True, 'scanned': ['Movies', 'TV Shows']}
        response = client.post(
            '/api/library/rescan-plex',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 200
        mock_trigger.assert_called_once_with('all')


class TestAPILibraryIdentify:
    """Tests for the /api/library/identify endpoint"""

    def test_identify_missing_query(self, client):
        """Test identify requires query"""
        response = client.post(
            '/api/library/identify',
            data=json.dumps({}),
            content_type='application/json'
        )
        assert response.status_code == 400

    @patch('app.routes.config.load_config')
    def test_identify_returns_results(self, mock_config, client):
        """Test identify returns results array"""
        mock_config.return_value = {
            'integrations': {
                'radarr': {'enabled': False},
                'sonarr': {'enabled': False}
            }
        }
        response = client.post(
            '/api/library/identify',
            data=json.dumps({'query': 'Test Movie', 'media_type': 'movies'}),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'results' in data
        assert 'query' in data


class TestAPIReviewThumbnail:
    """Tests for the /api/review/thumbnail endpoint"""

    @patch('app.routes.config.load_config')
    def test_thumbnail_folder_not_found(self, mock_config, client):
        """Test returns 404 when folder doesn't exist"""
        mock_config.return_value = {'paths': {'review': '/nonexistent/path'}}
        response = client.get('/api/review/thumbnail/fake_folder/thumb.jpg')
        assert response.status_code == 404

    @patch('app.routes.config.load_config')
    def test_thumbnail_invalid_file_type(self, mock_config, client, tmp_path):
        """Test returns 400 for non-jpg files"""
        # Create temp review folder
        review_path = tmp_path / "review"
        review_path.mkdir()
        folder = review_path / "test_folder"
        folder.mkdir()

        mock_config.return_value = {'paths': {'review': str(review_path)}}
        response = client.get('/api/review/thumbnail/test_folder/file.txt')
        assert response.status_code == 400

    @patch('app.routes.config.load_config')
    @patch('app.routes.send_from_directory')
    def test_thumbnail_serves_file(self, mock_send, mock_config, client, tmp_path):
        """Test serves thumbnail file when it exists"""
        # Create temp review folder with jpg
        review_path = tmp_path / "review"
        review_path.mkdir()
        folder = review_path / "test_folder"
        folder.mkdir()
        thumb = folder / "thumb.jpg"
        thumb.write_bytes(b'\xff\xd8\xff\xe0')  # Minimal JPEG header

        mock_config.return_value = {'paths': {'review': str(review_path)}}
        mock_send.return_value = "served"

        response = client.get('/api/review/thumbnail/test_folder/thumb.jpg')
        # Will either succeed or send_from_directory was called
        mock_send.assert_called_once()


class TestAPIReviewEpisodes:
    """Tests for the /api/review/episodes endpoint"""

    @patch('app.routes.config.load_config')
    @patch('app.identify.SmartIdentifier.get_season_episodes_for_review')
    def test_episodes_returns_json(self, mock_get_eps, mock_config, client):
        """Test episodes endpoint returns proper JSON structure"""
        mock_config.return_value = {
            'integrations': {
                'sonarr': {'url': 'http://localhost:8989', 'api_key': 'test'}
            }
        }
        mock_get_eps.return_value = [
            {'episode_num': 1, 'title': 'Episode 1', 'runtime_secs': 2700},
            {'episode_num': 2, 'title': 'Episode 2', 'runtime_secs': 2700},
        ]

        response = client.get('/api/review/episodes/12345/1')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['tvdb_id'] == 12345
        assert data['season'] == 1
        assert 'episodes' in data
        assert data['count'] == 2

    @patch('app.routes.config.load_config')
    @patch('app.identify.SmartIdentifier.get_season_episodes_for_review')
    def test_episodes_empty_when_not_found(self, mock_get_eps, mock_config, client):
        """Test returns empty list when no episodes found"""
        mock_config.return_value = {
            'integrations': {
                'sonarr': {'url': 'http://localhost:8989', 'api_key': 'test'}
            }
        }
        mock_get_eps.return_value = []

        response = client.get('/api/review/episodes/99999/99')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['episodes'] == []
        assert data['count'] == 0


class TestAPIReviewApplyWithEpisodeAssignments:
    """Tests for episode assignment handling in /api/review/apply"""

    def test_apply_missing_required_fields(self, client):
        """Test apply returns error when required fields missing"""
        response = client.post(
            '/api/review/apply',
            data=json.dumps({'folder_name': 'test'}),  # missing identified_title
            content_type='application/json'
        )
        assert response.status_code == 400

        response = client.post(
            '/api/review/apply',
            data=json.dumps({'identified_title': 'test'}),  # missing folder_name
            content_type='application/json'
        )
        assert response.status_code == 400

    @patch('app.routes.config.load_config')
    def test_apply_missing_folder(self, mock_config, client, tmp_path):
        """Test apply returns error when folder missing"""
        mock_config.return_value = {
            'paths': {
                'review': str(tmp_path / 'review'),
                'movies': str(tmp_path / 'movies'),
                'tv': str(tmp_path / 'tv')
            }
        }

        response = client.post(
            '/api/review/apply',
            data=json.dumps({
                'folder_name': 'nonexistent',
                'identified_title': 'Test',
                'media_type': 'movie'
            }),
            content_type='application/json'
        )

        assert response.status_code == 404

    def test_apply_accepts_episode_assignments_param(self, client):
        """Test apply endpoint accepts episode_assignments parameter without error"""
        # Just verify the parameter is parsed correctly (will fail on folder not found)
        response = client.post(
            '/api/review/apply',
            data=json.dumps({
                'folder_name': 'test_folder',
                'identified_title': 'Test Show',
                'media_type': 'tv',
                'season_number': 1,
                'episode_assignments': [
                    {'filename': 't1.mkv', 'episode_num': 1, 'is_extra': False},
                ]
            }),
            content_type='application/json'
        )
        # Should fail with 404 (folder not found) not 400 (bad request)
        assert response.status_code == 404
