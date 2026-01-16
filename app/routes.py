"""
RipForge Web Routes
"""

from flask import Blueprint, render_template, jsonify, request
from . import config
from . import ripper
from . import email as email_utils
from . import activity

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
    # Info from scan to determine if we should send uncertain email
    original_suggested = data.get('original_suggested')  # What auto-ID suggested
    was_uncertain = data.get('was_uncertain', False)  # True if needs_review was set
    disc_label = data.get('disc_label', '')
    runtime_str = data.get('runtime_str', '')

    success = engine.start_rip(device, custom_title=custom_title)
    if success:
        title = custom_title or "Unknown disc"
        activity.rip_started(title, "main feature only")

        # Send uncertain email ONLY if user didn't correct the title
        if was_uncertain and custom_title == original_suggested:
            cfg = config.load_config()
            notify_uncertain = cfg.get('ripping', {}).get('notify_uncertain', True)
            if notify_uncertain:
                email_cfg = cfg.get('notifications', {}).get('email', {})
                recipients = email_cfg.get('recipients', [])
                if recipients:
                    confidence = data.get('confidence', 0)
                    email_utils.send_uncertain_identification(
                        disc_label=disc_label,
                        best_guess=original_suggested,
                        confidence=confidence,
                        runtime_str=runtime_str,
                        recipients=recipients
                    )
                    activity.log_info(f"Uncertain ID email sent for: {disc_label}")

        return jsonify({'success': True, 'message': 'Rip started'})
    else:
        activity.log_warning("Rip start failed - already ripping or no disc")
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
    activity.scan_started(device)

    # Get disc info from MakeMKV
    info = engine.makemkv.get_disc_info(device)

    if not info.get('disc_label'):
        activity.scan_failed("No disc found")
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

    # Get runtime string for logging
    runtime_str = None
    if runtime_seconds:
        hours, remainder = divmod(runtime_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        runtime_str = f"{int(hours)}h {int(minutes)}m" if hours else f"{int(minutes)}m"

    # Build identification methods debug info
    identification_methods = []

    # Method 1: Parsed disc label
    identification_methods.append({
        'method': 'Disc Label Parsing',
        'result': search_term,
        'confidence': 50 if search_term != info['disc_label'] else 30,
        'details': f"Raw: {info['disc_label']} → Parsed: {search_term}"
    })

    # Method 2: Radarr/TMDB search with runtime matching
    if result:
        runtime_diff = None
        if runtime_seconds and result.runtime_minutes:
            diff_mins = abs(runtime_seconds/60 - result.runtime_minutes)
            runtime_diff = f"{diff_mins:.0f}min diff"

        identification_methods.append({
            'method': 'Radarr + Runtime Match',
            'result': result.folder_name,
            'confidence': result.confidence,
            'details': f"TMDB: {result.title} ({result.year}) - {result.runtime_minutes}min" +
                      (f" ({runtime_diff})" if runtime_diff else "")
        })

    # Log each identification method to activity log
    for method in identification_methods:
        activity.id_method_result(
            method['method'],
            method['result'],
            method['confidence'],
            method.get('details')
        )

    # Get expected size for main feature (round up slightly to avoid underestimate)
    import math
    track_sizes = info.get('track_sizes', {})
    expected_size_bytes = track_sizes.get(main_feature, 0) if main_feature is not None else 0
    expected_size_str = None
    if expected_size_bytes > 0:
        # Round up to nearest 0.1 GB
        size_gb = expected_size_bytes / (1024**3)
        size_gb_rounded = math.ceil(size_gb * 10) / 10
        expected_size_str = f"{size_gb_rounded:.1f} GB"

    # Build response
    response = {
        'disc_label': info['disc_label'],
        'disc_type': info.get('disc_type', 'unknown'),
        'tracks': info.get('tracks', []),
        'main_feature': main_feature,
        'runtime_seconds': runtime_seconds,
        'runtime_str': runtime_str,
        'expected_size_bytes': expected_size_bytes,
        'expected_size_str': expected_size_str,
        'parsed_search': search_term,
        'identified': None,
        'suggested_title': search_term,  # Fallback to parsed label
        'identification_methods': identification_methods
    }

    # Log scan completion
    activity.scan_completed(info['disc_label'], info.get('disc_type', 'disc').upper(), runtime_str)

    # Get auto-rip settings
    rip_settings = cfg.get('ripping', {})
    confidence_threshold = rip_settings.get('confidence_threshold', 75)
    notify_uncertain = rip_settings.get('notify_uncertain', True)

    if result:
        response['identified'] = {
            'title': result.title,
            'year': result.year,
            'tmdb_id': result.tmdb_id,
            'runtime_minutes': result.runtime_minutes,
            'confidence': result.confidence,
            'folder_name': result.folder_name,
            'poster_url': result.poster_url
        }
        # Only use identified title if confidence is high enough
        if result.confidence >= confidence_threshold:
            response['suggested_title'] = result.folder_name
            response['needs_review'] = False
        else:
            response['needs_review'] = True
        # Log identification
        activity.rip_identified(info['disc_label'], result.folder_name, result.confidence)
    else:
        response['needs_review'] = True

    # Don't send uncertain email here - user may correct the title before ripping
    # Email will be sent from /api/rip/start if title wasn't corrected

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
    from pathlib import Path

    logs_dir = Path(__file__).parent.parent / "logs"
    activity_log = logs_dir / "activity.log"

    lines = []
    try:
        if activity_log.exists():
            with open(activity_log) as f:
                lines = f.readlines()[-100:]  # Last 100 lines
                lines = [line.strip() for line in lines if line.strip()]
                lines.reverse()  # Newest first
    except Exception:
        pass

    return jsonify({'log': lines})


@main.route('/api/hardware')
def api_hardware():
    """Get system hardware info for the flex card"""
    hardware = config.detect_hardware()
    drives = config.detect_optical_drives()
    hardware['optical_drives'] = drives
    return jsonify(hardware)


@main.route('/api/rip-stats')
def api_rip_stats():
    """Get rip statistics from activity log"""
    from pathlib import Path
    from datetime import datetime, timedelta
    import re

    logs_dir = Path(__file__).parent.parent / "logs"
    activity_log = logs_dir / "activity.log"

    stats = {
        'today': 0,
        'week': 0,
        'total': 0,
        'errors': 0,
        'avg_bluray_mins': None,
        'avg_dvd_mins': None
    }

    bluray_times = []
    dvd_times = []
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    # Pattern to match completed rips with duration
    # Example: "2026-01-15 22:46:58 | SUCCESS | Rip completed: Expendables 3 (0:34:23)"
    completed_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| SUCCESS \| Rip completed: .* \((\d+):(\d{2}):(\d{2})\)')
    error_pattern = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ERROR \| Rip failed:')
    # Pattern to get disc type from scan line
    scan_pattern = re.compile(r'Scan completed: .* \((BLURAY|DVD)\)')

    try:
        if activity_log.exists():
            with open(activity_log) as f:
                lines = f.readlines()

            # Track disc types from scan lines
            current_disc_type = None

            for line in lines:
                line = line.strip()

                # Check for scan completed to get disc type
                scan_match = scan_pattern.search(line)
                if scan_match:
                    current_disc_type = scan_match.group(1)

                # Check for completed rips
                match = completed_pattern.match(line)
                if match:
                    stats['total'] += 1

                    # Parse timestamp
                    timestamp = datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
                    if timestamp >= today_start:
                        stats['today'] += 1
                    if timestamp >= week_start:
                        stats['week'] += 1

                    # Parse duration (H:MM:SS)
                    hours = int(match.group(2))
                    mins = int(match.group(3))
                    secs = int(match.group(4))
                    total_mins = hours * 60 + mins + secs / 60

                    # Add to appropriate list based on disc type
                    if current_disc_type == 'BLURAY':
                        bluray_times.append(total_mins)
                    elif current_disc_type == 'DVD':
                        dvd_times.append(total_mins)

                # Check for errors
                if error_pattern.match(line):
                    stats['errors'] += 1

    except Exception:
        pass

    # Calculate averages
    if bluray_times:
        stats['avg_bluray_mins'] = round(sum(bluray_times) / len(bluray_times))
    if dvd_times:
        stats['avg_dvd_mins'] = round(sum(dvd_times) / len(dvd_times))

    return jsonify(stats)


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

    activity.test_email_requested(recipients)
    success = email_utils.send_test_email(recipients)
    if success:
        activity.email_sent("Test", recipients)
    else:
        activity.email_failed("Test", "Send failed")
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
    if success:
        activity.weekly_recap_sent(recipients)
    else:
        activity.email_failed("Weekly recap", "Send failed")
    return jsonify({'success': success})


@main.route('/api/plex/users')
def api_plex_users():
    """Get Plex users with their emails"""
    users = config.get_plex_users()
    return jsonify({'users': users})
