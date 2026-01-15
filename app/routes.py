"""
RipForge Web Routes
"""

from flask import Blueprint, render_template, jsonify, request
from . import config
from . import ripper
from . import email as email_utils

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


@main.route('/notifications')
def notifications():
    """Notifications and newsletter management page"""
    cfg = config.load_config()
    return render_template('notifications.html', config=cfg)


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

    # Get current rip status
    engine = ripper.get_engine()
    rip_status = engine.get_status() if engine else None

    return jsonify({
        'integrations': integrations,
        'drives': drives,
        'ripping': rip_status
    })


@main.route('/api/rip/start', methods=['POST'])
def api_rip_start():
    """Start a new rip job"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'success': False, 'error': 'Rip engine not initialized'}), 500

    data = request.json or {}
    device = data.get('device', '/dev/sr0')
    custom_title = data.get('custom_title')  # User-specified title from scan

    success = engine.start_rip(device, custom_title=custom_title)
    if success:
        return jsonify({'success': True, 'message': 'Rip started'})
    else:
        return jsonify({'success': False, 'error': 'Already ripping or no disc'}), 400


@main.route('/api/rip/status')
def api_rip_status():
    """Get detailed rip status"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'status': 'idle'})

    status = engine.get_status()
    if status:
        return jsonify(status)
    return jsonify({'status': 'idle'})


@main.route('/api/rip/reset', methods=['POST'])
def api_rip_reset():
    """Reset/cancel the current rip job"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'success': False, 'error': 'Engine not initialized'}), 500

    engine.reset_job()
    return jsonify({'success': True, 'message': 'Job reset'})


@main.route('/api/disc/check')
def api_disc_check():
    """Check if a disc is present"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'present': False})

    device = request.args.get('device', '/dev/sr0')
    result = engine.check_disc(device)
    return jsonify(result)


@main.route('/api/disc/info')
def api_disc_info():
    """Get detailed disc info (scans with MakeMKV)"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500

    device = request.args.get('device', '/dev/sr0')
    info = engine.makemkv.get_disc_info(device)
    return jsonify(info)


@main.route('/api/disc/scan-identify')
def api_disc_scan_identify():
    """Scan disc AND run smart identification to get suggested title"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'error': 'Engine not initialized'}), 500

    device = request.args.get('device', '/dev/sr0')

    # Get disc info from MakeMKV
    info = engine.makemkv.get_disc_info(device)

    if not info.get('disc_label'):
        return jsonify({'error': 'No disc found'})

    # Get main feature runtime in seconds
    main_feature = info.get('main_feature')
    runtime_seconds = None
    if main_feature is not None:
        track = next((t for t in info.get('tracks', []) if t['index'] == main_feature), None)
        if track:
            runtime_seconds = track.get('duration')

    # Run smart identification
    from .identify import SmartIdentifier
    cfg = config.load_config()
    identifier = SmartIdentifier(cfg)

    # Parse disc label into search term
    search_term = identifier.parse_disc_label(info['disc_label'])

    # Search Radarr with runtime matching
    result = identifier.search_radarr(search_term, runtime_seconds)

    # Build response
    response = {
        'disc_label': info['disc_label'],
        'disc_type': info.get('disc_type', 'unknown'),
        'tracks': info.get('tracks', []),
        'main_feature': main_feature,
        'runtime_seconds': runtime_seconds,
        'parsed_search': search_term,
        'identified': None,
        'suggested_title': search_term  # Fallback to parsed label
    }

    if result:
        response['identified'] = {
            'title': result.title,
            'year': result.year,
            'tmdb_id': result.tmdb_id,
            'runtime_minutes': result.runtime_minutes,
            'confidence': result.confidence,
            'folder_name': result.folder_name
        }
        response['suggested_title'] = result.folder_name

    return jsonify(response)


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
    """Get recent activity log entries (newest first)"""
    import glob
    from pathlib import Path

    logs_dir = Path(__file__).parent.parent / "logs"
    log_files = [
        logs_dir / "ripforge.log",
        logs_dir / "disc-detect.log",
        logs_dir / "rip-activity.log",
    ]

    all_lines = []
    for log_file in log_files:
        try:
            if log_file.exists():
                with open(log_file) as f:
                    lines = f.readlines()[-50:]  # Last 50 lines per file
                    all_lines.extend([line.strip() for line in lines if line.strip()])
        except Exception:
            continue

    # Sort by timestamp if possible (newest first)
    def get_timestamp(line):
        try:
            # Extract timestamp from start of line
            import re
            match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
            if match:
                return match.group(1)
        except Exception:
            pass
        return '0000-00-00 00:00:00'

    all_lines.sort(key=get_timestamp, reverse=True)

    # Add log level detection if not present
    formatted_lines = []
    for line in all_lines[:100]:  # Limit to 100 entries
        # Add INFO level if no level detected
        if not any(lvl in line.upper() for lvl in ['INFO', 'ERROR', 'WARN', 'SUCCESS', 'DEBUG']):
            # Insert INFO after timestamp
            import re
            line = re.sub(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*-?\s*', r'\1 | INFO | ', line)
        formatted_lines.append(line)

    return jsonify({'log': formatted_lines})


@main.route('/api/hardware')
def api_hardware():
    """Get system hardware info for the flex card"""
    hardware = config.detect_hardware()
    drives = config.detect_optical_drives()
    hardware['optical_drives'] = drives
    return jsonify(hardware)


@main.route('/api/newsletter/queue')
def api_newsletter_queue():
    """Get content queued for the next newsletter"""
    cfg = config.load_config()
    queue = cfg.get('newsletter', {}).get('queue', [])
    return jsonify({'queue': queue})


@main.route('/api/newsletter/queue', methods=['POST'])
def api_newsletter_queue_add():
    """Add item to newsletter queue"""
    data = request.json
    cfg = config.load_config()

    if 'newsletter' not in cfg:
        cfg['newsletter'] = {'queue': [], 'frequency': 'weekly', 'day': 'thursday', 'hour': 9}
    if 'queue' not in cfg['newsletter']:
        cfg['newsletter']['queue'] = []

    cfg['newsletter']['queue'].append({
        'title': data.get('title'),
        'type': data.get('type', 'movie'),
        'year': data.get('year'),
        'added': data.get('added')
    })

    config.save_config(cfg)
    return jsonify({'success': True})


@main.route('/api/newsletter/queue/<int:index>', methods=['DELETE'])
def api_newsletter_queue_remove(index):
    """Remove item from newsletter queue"""
    cfg = config.load_config()
    queue = cfg.get('newsletter', {}).get('queue', [])

    if 0 <= index < len(queue):
        queue.pop(index)
        config.save_config(cfg)
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Invalid index'}), 400


@main.route('/api/newsletter/settings', methods=['GET', 'POST'])
def api_newsletter_settings():
    """Get or update newsletter settings"""
    cfg = config.load_config()

    if request.method == 'GET':
        return jsonify(cfg.get('newsletter', {
            'frequency': 'weekly',
            'day': 'thursday',
            'hour': 9,
            'recipients': [],
            'queue': []
        }))

    data = request.json
    if 'newsletter' not in cfg:
        cfg['newsletter'] = {}

    cfg['newsletter'].update({
        'frequency': data.get('frequency', 'weekly'),
        'day': data.get('day', 'thursday'),
        'hour': data.get('hour', 9),
        'recipients': data.get('recipients', [])
    })

    config.save_config(cfg)
    return jsonify({'success': True})


@main.route('/api/newsletter/preview')
def api_newsletter_preview():
    """Generate a preview of the next newsletter"""
    cfg = config.load_config()
    queue = cfg.get('newsletter', {}).get('queue', [])

    # Group by type
    movies = [item for item in queue if item.get('type') == 'movie']
    shows = [item for item in queue if item.get('type') == 'tv']

    return jsonify({
        'movies': movies,
        'shows': shows,
        'total': len(queue)
    })


@main.route('/api/newsletter/send-test', methods=['POST'])
def api_newsletter_send_test():
    """Send a test newsletter (test mode only)"""
    # This would integrate with plex-newsletter.sh --test
    import subprocess
    try:
        result = subprocess.run(
            ['/mnt/media/docker/plex-newsletter.sh'],
            capture_output=True, text=True, timeout=60
        )
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr if result.returncode != 0 else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@main.route('/api/email/test', methods=['POST'])
def api_email_test():
    """Send a test email to verify configuration"""
    data = request.json or {}
    recipients = data.get('recipients', [])

    if not recipients:
        cfg = config.load_config()
        recipients = cfg.get('notifications', {}).get('email', {}).get('recipients', [])

    if not recipients:
        return jsonify({'success': False, 'error': 'No recipients configured'})

    success = email_utils.send_test_email(recipients)
    return jsonify({'success': success})


@main.route('/api/email/weekly-recap', methods=['POST'])
def api_email_weekly_recap():
    """Send weekly recap email"""
    data = request.json or {}
    recipients = data.get('recipients', [])

    if not recipients:
        cfg = config.load_config()
        recipients = cfg.get('notifications', {}).get('email', {}).get('recipients', [])

    if not recipients:
        return jsonify({'success': False, 'error': 'No recipients configured'})

    success = email_utils.send_weekly_recap(recipients)
    return jsonify({'success': success})


@main.route('/api/plex/users')
def api_plex_users():
    """Get Plex users with their emails"""
    users = config.get_plex_users()
    return jsonify({'users': users})
