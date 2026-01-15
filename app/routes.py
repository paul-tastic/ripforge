"""
RipForge Web Routes
"""

from flask import Blueprint, render_template, jsonify, request
from . import config

main = Blueprint('main', __name__)


@main.route('/')
def index():
    """Main dashboard"""
    cfg = config.load_config()
    return render_template('index.html', config=cfg)


@main.route('/settings')
def settings():
    """Settings page"""
    cfg = config.load_config()
    return render_template('settings.html', config=cfg)


@main.route('/history')
def history():
    """Rip history page"""
    cfg = config.load_config()
    return render_template('history.html', config=cfg)


@main.route('/api/status')
def api_status():
    """Get current system status"""
    cfg = config.load_config()

    # Check integrations status
    integrations = {}
    for service in ['radarr', 'sonarr', 'overseerr', 'plex', 'tautulli']:
        svc_cfg = cfg.get('integrations', {}).get(service, {})
        if svc_cfg.get('enabled'):
            api_key = svc_cfg.get('api_key', '')
            token = svc_cfg.get('token', '')
            url = svc_cfg.get('url', '')
            status = config.test_connection(service, url, api_key, token)
            integrations[service] = {
                'enabled': True,
                'url': url,
                **status
            }
        else:
            integrations[service] = {'enabled': False}

    # Check optical drive
    drives = config.detect_optical_drives()

    return jsonify({
        'integrations': integrations,
        'drives': drives,
        'ripping': None  # TODO: current rip status
    })


@main.route('/api/auto-detect', methods=['POST'])
def api_auto_detect():
    """Run auto-detection of services"""
    result = config.run_auto_setup()
    return jsonify(result)


@main.route('/api/test-connection', methods=['POST'])
def api_test_connection():
    """Test connection to a specific service"""
    data = request.json
    service = data.get('service')
    url = data.get('url')
    api_key = data.get('api_key', '')
    token = data.get('token', '')

    result = config.test_connection(service, url, api_key, token)
    return jsonify(result)


@main.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """Get or update settings"""
    if request.method == 'GET':
        cfg = config.load_config()
        return jsonify(cfg)

    elif request.method == 'POST':
        data = request.json
        cfg = config.load_config()

        # Update config with provided data
        def deep_update(base, updates):
            for key, value in updates.items():
                if isinstance(value, dict) and key in base:
                    deep_update(base[key], value)
                else:
                    base[key] = value

        deep_update(cfg, data)
        config.save_config(cfg)

        return jsonify({'success': True})


@main.route('/api/import-keys', methods=['POST'])
def api_import_keys():
    """Import API keys from existing scripts"""
    keys = config.import_existing_api_keys()
    return jsonify(keys)


@main.route('/api/activity-log')
def api_activity_log():
    """Get recent activity log entries"""
    log_file = "/mnt/media/docker/arm/rip-activity.log"
    try:
        with open(log_file) as f:
            lines = f.readlines()[-100:]  # Last 100 lines
            return jsonify({'log': [line.strip() for line in lines]})
    except FileNotFoundError:
        return jsonify({'log': []})
