"""
Tests for RipForge Email Notifications module
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import email


class TestGetSendgridSuppressions:
    """Tests for get_sendgrid_suppressions function"""

    @patch('app.email.requests.get')
    def test_fetches_all_suppression_types(self, mock_get):
        """Test fetches from unsubscribes, bounces, spam_reports"""
        # Mock different responses for each endpoint
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if 'unsubscribes' in url:
                mock_resp.json.return_value = [{'email': 'unsub@test.com'}]
            elif 'bounces' in url:
                mock_resp.json.return_value = [{'email': 'bounce@test.com'}]
            elif 'spam_reports' in url:
                mock_resp.json.return_value = [{'email': 'spam@test.com'}]
            return mock_resp

        mock_get.side_effect = side_effect

        result = email.get_sendgrid_suppressions('fake_api_key')

        assert 'unsub@test.com' in result
        assert 'bounce@test.com' in result
        assert 'spam@test.com' in result
        assert mock_get.call_count == 3

    @patch('app.email.requests.get')
    def test_normalizes_email_to_lowercase(self, mock_get):
        """Test email addresses are normalized to lowercase"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{'email': 'UPPER@TEST.COM'}]
        mock_get.return_value = mock_resp

        result = email.get_sendgrid_suppressions('fake_api_key')

        assert 'upper@test.com' in result

    @patch('app.email.requests.get')
    def test_handles_api_error(self, mock_get):
        """Test handles API errors gracefully"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        result = email.get_sendgrid_suppressions('bad_api_key')

        assert len(result) == 0

    @patch('app.email.requests.get')
    def test_handles_network_error(self, mock_get):
        """Test handles network errors gracefully"""
        mock_get.side_effect = Exception("Connection error")

        result = email.get_sendgrid_suppressions('fake_api_key')

        assert len(result) == 0


class TestFilterSuppressedRecipients:
    """Tests for filter_suppressed_recipients function"""

    @patch('app.email.get_sendgrid_suppressions')
    def test_removes_suppressed_emails(self, mock_get_suppressions):
        """Test removes suppressed emails from list"""
        mock_get_suppressions.return_value = {'suppressed@test.com', 'blocked@test.com'}

        recipients = ['good@test.com', 'suppressed@test.com', 'another@test.com']
        result = email.filter_suppressed_recipients(recipients, 'api_key')

        assert 'good@test.com' in result
        assert 'another@test.com' in result
        assert 'suppressed@test.com' not in result

    @patch('app.email.get_sendgrid_suppressions')
    def test_handles_dict_recipients(self, mock_get_suppressions):
        """Test handles recipient dicts with email key"""
        mock_get_suppressions.return_value = {'suppressed@test.com'}

        recipients = [
            {'email': 'good@test.com', 'name': 'Good User'},
            {'email': 'suppressed@test.com', 'name': 'Suppressed'},
        ]
        result = email.filter_suppressed_recipients(recipients, 'api_key')

        assert len(result) == 1
        assert result[0]['email'] == 'good@test.com'

    @patch('app.email.get_sendgrid_suppressions')
    def test_returns_all_when_no_suppressions(self, mock_get_suppressions):
        """Test returns all recipients when no suppressions"""
        mock_get_suppressions.return_value = set()

        recipients = ['a@test.com', 'b@test.com']
        result = email.filter_suppressed_recipients(recipients, 'api_key')

        assert result == recipients


class TestSendViaSendgrid:
    """Tests for send_via_sendgrid function"""

    @patch('app.email.requests.post')
    def test_successful_send(self, mock_post):
        """Test successful email send"""
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_post.return_value = mock_resp

        result = email.send_via_sendgrid(
            to=['test@example.com'],
            subject='Test Subject',
            body='<p>Test body</p>',
            api_key='test_api_key'
        )

        assert result is True
        mock_post.assert_called_once()

    @patch('app.email.requests.post')
    def test_includes_unsubscribe_tracking(self, mock_post):
        """Test includes unsubscribe tracking by default"""
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_post.return_value = mock_resp

        email.send_via_sendgrid(
            to=['test@example.com'],
            subject='Test',
            body='Body',
            api_key='api_key',
            include_unsubscribe=True
        )

        call_data = mock_post.call_args.kwargs['json']
        assert 'tracking_settings' in call_data
        assert call_data['tracking_settings']['subscription_tracking']['enable'] is True

    @patch('app.email.requests.post')
    def test_handles_api_error(self, mock_post):
        """Test handles API error"""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = 'Bad request'
        mock_post.return_value = mock_resp

        result = email.send_via_sendgrid(
            to=['test@example.com'],
            subject='Test',
            body='Body',
            api_key='api_key'
        )

        assert result is False

    @patch('app.email.requests.post')
    def test_handles_exception(self, mock_post):
        """Test handles exceptions"""
        mock_post.side_effect = Exception("Network error")

        result = email.send_via_sendgrid(
            to=['test@example.com'],
            subject='Test',
            body='Body',
            api_key='api_key'
        )

        assert result is False


class TestSendViaMsmtp:
    """Tests for send_via_msmtp function"""

    @patch('app.email.subprocess.run')
    def test_successful_send(self, mock_run):
        """Test successful msmtp send"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = email.send_via_msmtp(
            to=['test@example.com'],
            subject='Test Subject',
            body='Test body'
        )

        assert result is True
        mock_run.assert_called_once()

    @patch('app.email.subprocess.run')
    def test_sends_to_multiple_recipients(self, mock_run):
        """Test sends to multiple recipients"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = email.send_via_msmtp(
            to=['a@test.com', 'b@test.com'],
            subject='Test',
            body='Body'
        )

        assert result is True
        assert mock_run.call_count == 2

    @patch('app.email.subprocess.run')
    def test_handles_failure(self, mock_run):
        """Test handles msmtp failure"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = 'Connection refused'
        mock_run.return_value = mock_result

        result = email.send_via_msmtp(
            to=['test@example.com'],
            subject='Test',
            body='Body'
        )

        assert result is False

    @patch('app.email.subprocess.run')
    def test_sets_correct_content_type(self, mock_run):
        """Test sets HTML content type when html=True"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        email.send_via_msmtp(
            to=['test@example.com'],
            subject='Test',
            body='<p>HTML</p>',
            html=True
        )

        call_input = mock_run.call_args.kwargs['input']
        assert 'text/html' in call_input


class TestSendEmail:
    """Tests for send_email routing function"""

    @patch('app.email.config.load_config')
    @patch('app.email.send_via_sendgrid')
    def test_routes_to_sendgrid_when_configured(self, mock_sendgrid, mock_config):
        """Test routes to SendGrid when configured"""
        mock_config.return_value = {
            'notifications': {
                'email': {
                    'provider': 'sendgrid',
                    'sendgrid_api_key': 'test_key',
                    'check_suppressions': False
                }
            }
        }
        mock_sendgrid.return_value = True

        result = email.send_email(['test@example.com'], 'Subject', 'Body')

        assert result is True
        mock_sendgrid.assert_called_once()

    @patch('app.email.config.load_config')
    @patch('app.email.send_via_msmtp')
    def test_routes_to_msmtp_by_default(self, mock_msmtp, mock_config):
        """Test routes to msmtp by default"""
        mock_config.return_value = {
            'notifications': {
                'email': {
                    'provider': 'msmtp'
                }
            }
        }
        mock_msmtp.return_value = True

        result = email.send_email(['test@example.com'], 'Subject', 'Body')

        assert result is True
        mock_msmtp.assert_called_once()

    @patch('app.email.config.load_config')
    @patch('app.email.filter_suppressed_recipients')
    @patch('app.email.send_via_sendgrid')
    def test_filters_suppressions_when_enabled(self, mock_sendgrid, mock_filter, mock_config):
        """Test filters suppressed recipients when check_suppressions enabled"""
        mock_config.return_value = {
            'notifications': {
                'email': {
                    'provider': 'sendgrid',
                    'sendgrid_api_key': 'test_key',
                    'check_suppressions': True
                }
            }
        }
        mock_filter.return_value = ['valid@test.com']
        mock_sendgrid.return_value = True

        email.send_email(['valid@test.com', 'suppressed@test.com'], 'Subject', 'Body')

        mock_filter.assert_called_once()

    @patch('app.email.config.load_config')
    @patch('app.email.filter_suppressed_recipients')
    def test_skips_when_all_suppressed(self, mock_filter, mock_config):
        """Test returns False when all recipients are suppressed"""
        mock_config.return_value = {
            'notifications': {
                'email': {
                    'provider': 'sendgrid',
                    'sendgrid_api_key': 'test_key',
                    'check_suppressions': True
                }
            }
        }
        mock_filter.return_value = []  # All suppressed

        result = email.send_email(['suppressed@test.com'], 'Subject', 'Body')

        assert result is False

    @patch('app.email.config.load_config')
    @patch('app.email.send_via_msmtp')
    def test_falls_back_to_msmtp_without_api_key(self, mock_msmtp, mock_config):
        """Test falls back to msmtp when SendGrid has no API key"""
        mock_config.return_value = {
            'notifications': {
                'email': {
                    'provider': 'sendgrid',
                    'sendgrid_api_key': ''  # No key
                }
            }
        }
        mock_msmtp.return_value = True

        result = email.send_email(['test@example.com'], 'Subject', 'Body')

        mock_msmtp.assert_called_once()


class TestSyncSuppressionsToConfig:
    """Tests for sync_suppressions_to_config function"""

    @patch('app.email.config.load_config')
    def test_returns_zero_without_api_key(self, mock_config):
        """Test returns 0 when no API key configured"""
        mock_config.return_value = {
            'notifications': {'email': {'sendgrid_api_key': ''}}
        }

        result = email.sync_suppressions_to_config()

        assert result == 0

    @patch('app.email.config.save_config')
    @patch('app.email.config.load_config')
    @patch('app.email.get_sendgrid_suppressions')
    def test_marks_suppressed_recipients(self, mock_get_sup, mock_load, mock_save):
        """Test marks suppressed recipients in config"""
        mock_load.return_value = {
            'notifications': {
                'email': {
                    'sendgrid_api_key': 'test_key',
                    'recipients': [
                        {'email': 'good@test.com', 'enabled': True},
                        {'email': 'suppressed@test.com', 'enabled': True},
                    ]
                }
            }
        }
        mock_get_sup.return_value = {'suppressed@test.com'}

        result = email.sync_suppressions_to_config()

        assert result == 1
        mock_save.assert_called_once()
