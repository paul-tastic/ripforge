"""
RipForge Web Routes
"""

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, render_template, jsonify, request
from . import config
from . import ripper
from . import email as email_utils
from . import activity
from . import community_db

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


@main.route('/failures')
def failures():
    """Failed rips page"""
    cfg = config.load_config()
    return render_template('failures.html', config=cfg)


@main.route('/api/failures', methods=['GET', 'DELETE'])
def api_failures():
    """Get or clear failure log"""
    if request.method == 'DELETE':
        config.clear_failure_log()
        return jsonify({'success': True})
    return jsonify({'failures': config.get_failure_log()})


@main.route('/api/failures/<int:index>', methods=['DELETE'])
def api_delete_failure(index):
    """Delete a specific failure entry"""
    config.delete_failure(index)
    return jsonify({'success': True})


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

    # Quick RAM stats for live updates
    ram_stats = None
    try:
        import subprocess
        result = subprocess.run(['free', '-b'], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 3:
                    total = int(parts[1])
                    used = int(parts[2])
                    ram_stats = {
                        'total_gb': round(total / (1024**3), 1),
                        'used_gb': round(used / (1024**3), 1),
                        'percent': round(used / total * 100) if total > 0 else 0
                    }
    except Exception:
        pass

    return jsonify({
        'integrations': integrations,
        'drives': drives,
        'ripping': rip_status,
        'ram': ram_stats
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

    # TV-specific parameters
    media_type = data.get('media_type', 'movie')
    season_number = data.get('season_number', 0)
    selected_tracks = data.get('selected_tracks', [])  # Track indices for TV episodes
    episode_mapping = data.get('episode_mapping', {})  # track_idx -> episode info
    series_title = data.get('series_title', '')

    # Smart track selection - TMDB runtime for handling fake playlists
    tmdb_runtime_seconds = data.get('tmdb_runtime_seconds', 0)

    # Convert episode_mapping keys from strings to ints (JSON serialization)
    if episode_mapping:
        episode_mapping = {int(k): v for k, v in episode_mapping.items()}

    success = engine.start_rip(
        device,
        custom_title=custom_title,
        media_type=media_type,
        season_number=season_number,
        selected_tracks=selected_tracks,
        episode_mapping=episode_mapping,
        series_title=series_title,
        tmdb_runtime_seconds=tmdb_runtime_seconds
    )
    if success:
        title = custom_title or series_title or "Unknown disc"
        if media_type == 'tv':
            activity.rip_started(f"{title} S{season_number:02d}", f"{len(selected_tracks)} episodes")
        else:
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


@main.route('/api/drive/stop', methods=['POST'])
def api_drive_stop():
    """Stop drive - kill MakeMKV, reset job, and eject disc"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'success': False, 'error': 'Engine not initialized'}), 500

    # Stop the drive (kills MakeMKV, resets job, ejects)
    result = engine.stop_drive()
    return jsonify(result)


    return jsonify(result)



@main.route('/api/drive/reset', methods=['POST'])
def api_drive_reset():
    """Reset drive state - kills lingering processes, clears stale state"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'success': False, 'error': 'Engine not initialized'}), 500

    result = engine.reset_drive_state()
    return jsonify(result)


@main.route('/api/drive/eject', methods=['POST'])
def api_drive_eject():
    """Force eject disc"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'success': False, 'error': 'Engine not initialized'}), 500

    result = engine.force_eject_disc()
    return jsonify(result)


@main.route('/api/service/restart', methods=['POST'])
def api_service_restart():
    """Restart RipForge service"""
    engine = ripper.get_engine()
    if not engine:
        return jsonify({'success': False, 'error': 'Engine not initialized'}), 500

    result = engine.restart_service()
    return jsonify(result)

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

    # Get disc info from MakeMKV (pass config for TV detection thresholds)
    cfg = config.load_config()
    info = engine.makemkv.get_disc_info(device, cfg)

    if not info.get('disc_label'):
        activity.scan_failed("No disc found")
        return jsonify({'error': 'No disc found'})

    # Run smart identification
    from .identify import SmartIdentifier
    identifier = SmartIdentifier(cfg)

    # Detect media type from disc label and tracks
    media_type, season_number, cleaned_title = identifier.detect_media_type(
        info['disc_label'],
        info.get('tracks', [])
    )

    # Also consider the disc info's TV detection
    if info.get('is_tv_disc') and media_type == 'movie':
        media_type = 'tv'
        activity.log_info("SCAN: Disc has multiple episode tracks, switching to TV mode")

    # Get episode tracks for TV
    episode_tracks = info.get('episode_tracks', [])

    # Get main feature runtime in seconds (for movies)
    main_feature = info.get('main_feature')
    runtime_seconds = None
    if main_feature is not None:
        track = next((t for t in info.get('tracks', []) if t['index'] == main_feature), None)
        if track:
            runtime_seconds = track.get('duration')

    # Parse disc label into search term
    search_term = identifier.parse_disc_label(cleaned_title if media_type == 'tv' else info['disc_label'])

    # Search based on media type
    result = None
    if media_type == 'tv':
        # Get episode runtimes for Sonarr matching
        episode_runtimes = [t['duration'] for t in episode_tracks]
        result = identifier.search_sonarr(search_term, episode_runtimes, season_number)
    else:
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

    # Smart track selection: check for fake playlists if we have TMDB runtime
    suggested_track = main_feature
    fake_playlist_detected = False
    tmdb_runtime_seconds = 0

    if result and result.runtime_minutes and media_type == 'movie':
        tmdb_runtime_seconds = result.runtime_minutes * 60
        # Use smart track selection to detect fake playlists and suggest best track
        smart_track, fake_playlist_detected = engine.makemkv.select_best_track(
            info.get('tracks', []),
            tmdb_runtime_seconds
        )
        if smart_track is not None:
            suggested_track = smart_track
            if fake_playlist_detected:
                activity.log_warning(f"SCAN: Fake playlists detected - suggesting track {suggested_track}")

    # Build response
    response = {
        'disc_label': info['disc_label'],
        'disc_type': info.get('disc_type', 'unknown'),
        'tracks': info.get('tracks', []),
        'main_feature': main_feature,
        'suggested_track': suggested_track,  # May differ from main_feature for fake playlists
        'fake_playlist_detected': fake_playlist_detected,
        'tmdb_runtime_seconds': tmdb_runtime_seconds,  # For smart track selection during rip
        'runtime_seconds': runtime_seconds,
        'runtime_str': runtime_str,
        'expected_size_bytes': expected_size_bytes,
        'expected_size_str': expected_size_str,
        'parsed_search': search_term,
        'identified': None,
        'suggested_title': search_term,  # Fallback to parsed label
        'identification_methods': identification_methods,
        # TV-specific fields
        'media_type': media_type,
        'season_number': season_number,
        'episode_tracks': episode_tracks,
        'is_tv_disc': info.get('is_tv_disc', False),
        'episode_mapping': {}  # Will be populated from result if TV
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
            'poster_url': result.poster_url,
            'media_type': result.media_type
        }

        # For TV, include episode mapping and season info
        if result.media_type == 'tv':
            response['episode_mapping'] = result.episode_mapping
            response['season_number'] = result.season_number or season_number
            response['suggested_title'] = result.title  # Series name without year for TV

        # Only use identified title if confidence is high enough
        if result.confidence >= confidence_threshold:
            if result.media_type == 'movie':
                response['suggested_title'] = result.folder_name
            response['needs_review'] = False
        else:
            response['needs_review'] = True

        # Log identification
        if result.media_type == 'tv':
            activity.rip_identified(info['disc_label'], f"{result.title} S{response['season_number']:02d}", result.confidence)
        else:
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

        # Check if community_db is being enabled (was off, now on)
        was_enabled = cfg.get('community_db', {}).get('enabled', True)
        will_enable = data.get('community_db', {}).get('enabled', was_enabled)

        # Update config with provided data
        def deep_update(base, updates):
            for key, value in updates.items():
                if isinstance(value, dict) and key in base:
                    deep_update(base[key], value)
                else:
                    base[key] = value

        deep_update(cfg, data)
        config.save_config(cfg)

        # Upload pending captures if community_db was just enabled
        if will_enable and not was_enabled:
            community_db.upload_pending_captures()

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


@main.route('/api/rip-history')
def api_rip_history():
    """Get rip history for the history page"""
    from pathlib import Path
    import json
    
    logs_dir = Path(__file__).parent.parent / "logs"
    history_file = logs_dir / "rip_history.json"
    
    rips = []
    try:
        if history_file.exists():
            with open(history_file) as f:
                rips = json.load(f)
    except Exception:
        pass
    
    # Sort by completed_at descending (newest first)
    rips.sort(key=lambda x: x.get('completed_at', ''), reverse=True)
    return jsonify({'rips': rips})


@main.route('/api/hardware')
def api_hardware():
    """Get system hardware info for the flex card"""
    hardware = config.detect_hardware()
    drives = config.detect_optical_drives()
    hardware['optical_drives'] = drives
    hardware['ned'] = config.detect_ned_agent()
    return jsonify(hardware)


@main.route('/api/drive/status')
def api_drive_status():
    """Get detailed optical drive status including LibreDrive info"""
    return jsonify(config.get_optical_drive_status())


@main.route('/api/version')
def api_version():
    """Get version info and check for updates"""
    return jsonify(config.check_for_updates())


@main.route('/api/update', methods=['POST'])
def api_update():
    """Pull latest code from GitHub and restart service"""
    repo_dir = Path(__file__).parent.parent
    venv_pip = repo_dir / 'venv' / 'bin' / 'pip'

    result = {
        'success': False,
        'git_output': '',
        'pip_output': '',
        'error': None
    }

    try:
        # Git pull
        git_result = subprocess.run(
            ['git', 'pull', 'origin', 'main'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=60
        )
        result['git_output'] = git_result.stdout + git_result.stderr

        if git_result.returncode != 0:
            result['error'] = f'Git pull failed: {git_result.stderr}'
            return jsonify(result)

        # Check if requirements.txt changed or just always update deps
        pip_result = subprocess.run(
            [str(venv_pip), 'install', '-r', 'requirements.txt', '--quiet'],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120
        )
        result['pip_output'] = pip_result.stdout + pip_result.stderr

        if pip_result.returncode != 0:
            result['error'] = f'Pip install failed: {pip_result.stderr}'
            return jsonify(result)

        result['success'] = True

        # Schedule restart after response is sent
        def delayed_exit():
            import time
            time.sleep(2)
            os._exit(0)  # Hard exit, systemd will restart us

        threading.Thread(target=delayed_exit, daemon=True).start()

    except subprocess.TimeoutExpired:
        result['error'] = 'Command timed out'
    except Exception as e:
        result['error'] = str(e)

    return jsonify(result)


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
    # Patterns to get disc type from scan or detect lines
    scan_pattern = re.compile(r'Scan completed: .* \((BLURAY|DVD)\)')
    detect_pattern = re.compile(r'Disc detected: .* \((BLURAY|DVD)\)')

    try:
        if activity_log.exists():
            with open(activity_log) as f:
                lines = f.readlines()

            # Track disc types from scan lines
            current_disc_type = None

            for line in lines:
                line = line.strip()

                # Check for scan completed or disc detected to get disc type
                scan_match = scan_pattern.search(line) or detect_pattern.search(line)
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


@main.route('/api/stats/clear', methods=['POST'])
def api_stats_clear():
    """Clear the activity log (resets all stats)"""
    activity.clear_activity_log()
    return jsonify({'success': True, 'message': 'Activity log cleared'})


@main.route('/api/stats/errors')
def api_stats_errors():
    """Get detailed list of rip errors"""
    errors = activity.get_rip_errors()
    return jsonify({'errors': errors, 'count': len(errors)})


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
        newsletter = cfg.get('newsletter', {})
        email_cfg = cfg.get('notifications', {}).get('email', {})
        raw_recipients = email_cfg.get('recipients', [])

        # Convert legacy string format to object format
        recipients = []
        for r in raw_recipients:
            if isinstance(r, str):
                recipients.append({'email': r, 'enabled': True})
            else:
                recipients.append(r)

        return jsonify({
            'frequency': newsletter.get('frequency', 'weekly'),
            'day': newsletter.get('day', 'thursday'),
            'hour': newsletter.get('hour', 9),
            'recipients': recipients,
            'queue': newsletter.get('queue', [])
        })

    data = request.json

    # Update newsletter scheduling
    if 'newsletter' not in cfg:
        cfg['newsletter'] = {}
    cfg['newsletter'].update({
        'frequency': data.get('frequency', 'weekly'),
        'day': data.get('day', 'thursday'),
        'hour': data.get('hour', 9)
    })

    # Update recipients in notifications.email (single source of truth)
    # Normalize to plain strings - handle both string and object formats
    raw_recipients = data.get('recipients', [])
    normalized_recipients = []
    for r in raw_recipients:
        if isinstance(r, str):
            normalized_recipients.append(r)
        elif isinstance(r, dict) and r.get('email'):
            normalized_recipients.append(r['email'])

    if 'notifications' not in cfg:
        cfg['notifications'] = {}
    if 'email' not in cfg['notifications']:
        cfg['notifications']['email'] = {}
    cfg['notifications']['email']['recipients'] = normalized_recipients

    config.save_config(cfg)
    return jsonify({'success': True})


@main.route('/api/newsletter/preview')
def api_newsletter_preview():
    """Get preview of content that will go in the next weekly digest"""
    from . import activity
    from datetime import datetime, timedelta

    # Calculate date range based on newsletter schedule
    cfg = config.load_config()
    newsletter_cfg = cfg.get('notifications', {}).get('newsletter', {})
    frequency = newsletter_cfg.get('frequency', 'weekly')
    send_day = newsletter_cfg.get('day', 'thursday').lower()

    # Map day names to weekday numbers (Monday=0, Sunday=6)
    day_map = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    target_weekday = day_map.get(send_day, 3)  # Default Thursday

    # Calculate the end date (next newsletter send day)
    now = datetime.now()
    days_until_send = (target_weekday - now.weekday()) % 7
    if days_until_send == 0:
        days_until_send = 7  # If today is send day, show next week
    end_date = now + timedelta(days=days_until_send)

    # Calculate start date based on frequency
    if frequency == 'monthly':
        start_date = end_date - timedelta(days=30)
    elif frequency == 'biweekly':
        start_date = end_date - timedelta(days=14)
    else:
        start_date = end_date - timedelta(days=7)

    # Get rips within this date range
    days_back = (now - start_date).days + 1
    rips = activity.get_recent_rips(days=days_back)

    return jsonify({
        'rips': rips,
        'total': len(rips),
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'frequency': frequency,
        'send_day': send_day
    })


@main.route('/api/newsletter/send-test', methods=['POST'])
def api_newsletter_send_test():
    """Send a test weekly recap email (to first enabled recipient only)"""
    from . import email as email_module

    cfg = config.load_config()
    raw_recipients = cfg.get('notifications', {}).get('email', {}).get('recipients', [])

    # Get enabled recipients only
    enabled_recipients = []
    for r in raw_recipients:
        if isinstance(r, str):
            enabled_recipients.append(r)
        elif isinstance(r, dict) and r.get('enabled', True):
            enabled_recipients.append(r.get('email'))

    if not enabled_recipients:
        return jsonify({'success': False, 'error': 'No enabled recipients. Enable at least one email in Newsletter Settings.'})

    # Only send to first enabled recipient for testing
    test_recipient = [enabled_recipients[0]]

    try:
        success = email_module.send_weekly_recap(test_recipient)
        return jsonify({
            'success': success,
            'recipients': test_recipient,
            'error': None if success else 'Failed to send email'
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


@main.route('/api/email/reset-digest', methods=['POST'])
def api_reset_digest():
    """Reset the digest list - clears recently added content for next digest"""
    activity.reset_digest_list()
    return jsonify({'success': True, 'message': 'Digest list cleared'})


@main.route('/api/email/sync-suppressions', methods=['POST'])
def api_sync_suppressions():
    """Sync SendGrid suppressions to local recipients list"""
    count = email_utils.sync_suppressions_to_config()
    return jsonify({'success': True, 'updated': count})


@main.route('/api/plex/users')
def api_plex_users():
    """Get Plex users with their emails"""
    users = config.get_plex_users()
    return jsonify({'users': users})


# ============================================================================
# Library Export API
# ============================================================================

@main.route('/api/library/export', methods=['POST'])
def api_library_export():
    """Generate library PDF export"""
    from . import library_export

    data = request.json or {}
    include_movies = data.get('include_movies', True)
    include_shows = data.get('include_shows', True)
    include_images = data.get('include_images', False)
    send_email = data.get('send_email', False)
    recipients = data.get('recipients', [])

    try:
        # Generate PDF
        pdf_path = library_export.generate_library_pdf(
            include_movies=include_movies,
            include_shows=include_shows,
            include_images=include_images
        )

        result = {
            'success': True,
            'pdf_path': pdf_path,
            'filename': Path(pdf_path).name
        }

        # Optionally send email
        if send_email and recipients:
            email_sent = library_export.email_library_pdf(
                recipients=recipients,
                pdf_path=pdf_path,
                include_movies=include_movies,
                include_shows=include_shows
            )
            result['email_sent'] = email_sent
            result['email_recipients'] = recipients

        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main.route('/api/library/exports')
def api_library_exports():
    """List available export files"""
    from . import library_export

    exports = []
    for f in library_export.EXPORT_DIR.glob('*.pdf'):
        exports.append({
            'filename': f.name,
            'path': str(f),
            'size': f.stat().st_size,
            'created': datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        })

    # Sort by creation time, newest first
    exports.sort(key=lambda x: x['created'], reverse=True)

    return jsonify({'exports': exports})


@main.route('/exports/<filename>')
def serve_export(filename):
    """Serve an export file for download"""
    from flask import send_file
    from . import library_export

    filepath = library_export.EXPORT_DIR / filename
    if filepath.exists() and filepath.suffix == '.pdf':
        return send_file(filepath, as_attachment=True)

    return jsonify({'error': 'File not found'}), 404


# ============================================================================
# Review Queue API
# ============================================================================

@main.route('/api/review/queue')
def api_review_queue():
    """Get all items in the review queue"""
    import os
    import json
    from pathlib import Path

    cfg = config.load_config()
    review_path = cfg.get('paths', {}).get('review', '/mnt/media/rips/review')

    items = []
    if os.path.isdir(review_path):
        for folder_name in os.listdir(review_path):
            folder_path = os.path.join(review_path, folder_name)
            if os.path.isdir(folder_path):
                metadata_file = os.path.join(folder_path, 'review_metadata.json')
                if os.path.exists(metadata_file):
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)
                        metadata['folder_name'] = folder_name
                        metadata['folder_path'] = folder_path
                        items.append(metadata)
                    except Exception as e:
                        activity.log_warning(f"Failed to read review metadata: {folder_name} - {e}")

    # Sort by created_at, newest first
    items.sort(key=lambda x: x.get('created_at', ''), reverse=True)

    return jsonify({
        'items': items,
        'total': len(items),
        'review_path': review_path
    })


@main.route('/api/review/search', methods=['POST'])
def api_review_search():
    """Search for a movie/show title to identify a review item"""
    data = request.json or {}
    search_term = data.get('query', '')
    runtime_seconds = data.get('runtime_seconds', 0)
    media_type = data.get('media_type', 'movie')

    if not search_term:
        return jsonify({'error': 'Search query required'}), 400

    cfg = config.load_config()
    from .identify import SmartIdentifier
    identifier = SmartIdentifier(cfg)

    results = []

    if media_type == 'movie':
        # Search Radarr/TMDB
        result = identifier.search_radarr(search_term, runtime_seconds if runtime_seconds else None)
        if result:
            results.append({
                'title': result.title,
                'year': result.year,
                'tmdb_id': result.tmdb_id,
                'imdb_id': result.imdb_id,
                'runtime_minutes': result.runtime_minutes,
                'confidence': result.confidence,
                'folder_name': result.folder_name,
                'poster_url': result.poster_url,
                'media_type': 'movie'
            })
    else:
        # Search Sonarr for TV
        result = identifier.search_sonarr(search_term, [], 0)
        if result:
            results.append({
                'title': result.title,
                'year': result.year,
                'tmdb_id': result.tmdb_id,
                'imdb_id': result.imdb_id,
                'tvdb_id': result.tvdb_id,
                'runtime_minutes': result.runtime_minutes,
                'confidence': result.confidence,
                'folder_name': result.folder_name,
                'poster_url': result.poster_url,
                'media_type': 'tv'
            })

    return jsonify({
        'results': results,
        'query': search_term
    })


@main.route('/api/review/apply', methods=['POST'])
def api_review_apply():
    """Apply identification and move review item to library"""
    import os
    import shutil
    import json
    import glob
    from pathlib import Path

    data = request.json or {}
    folder_name = data.get('folder_name')
    identified_title = data.get('identified_title')  # e.g. "Under Siege (1992)"
    media_type = data.get('media_type', 'movie')
    year = data.get('year', 0)
    tmdb_id = data.get('tmdb_id', 0)
    poster_url = data.get('poster_url', '')

    if not folder_name or not identified_title:
        return jsonify({'error': 'folder_name and identified_title required'}), 400

    cfg = config.load_config()
    review_path = cfg.get('paths', {}).get('review', '/mnt/media/rips/review')
    movies_path = cfg.get('paths', {}).get('movies', '/mnt/media/movies')
    tv_path = cfg.get('paths', {}).get('tv', '/mnt/media/tv')

    source_path = os.path.join(review_path, folder_name)

    if not os.path.isdir(source_path):
        return jsonify({'error': f'Review folder not found: {folder_name}'}), 404

    try:
        # Read metadata for rip history
        metadata_file = os.path.join(source_path, 'review_metadata.json')
        metadata = {}
        if os.path.exists(metadata_file):
            with open(metadata_file) as f:
                metadata = json.load(f)

        # Find MKV files
        mkv_files = glob.glob(os.path.join(source_path, '*.mkv'))
        if not mkv_files:
            return jsonify({'error': 'No MKV files found in review folder'}), 400

        # Determine destination
        if media_type == 'movie':
            dest_path = os.path.join(movies_path, identified_title)
        else:
            # For TV, identified_title should be series name
            dest_path = os.path.join(tv_path, identified_title)

        activity.log_info(f"REVIEW: Moving '{folder_name}' to '{dest_path}'")

        # Create destination and move files
        Path(dest_path).mkdir(parents=True, exist_ok=True)

        for mkv_file in mkv_files:
            if media_type == 'movie':
                new_filename = f"{identified_title}.mkv"
                if len(mkv_files) > 1:
                    idx = mkv_files.index(mkv_file) + 1
                    new_filename = f"{identified_title} - Part {idx}.mkv"
            else:
                # Keep original filename for TV (should be properly named)
                new_filename = os.path.basename(mkv_file)

            dest_file = os.path.join(dest_path, new_filename)
            activity.log_info(f"REVIEW: Moving: {os.path.basename(mkv_file)} -> {new_filename}")
            shutil.move(mkv_file, dest_file)

        # Calculate file size
        total_size = sum(os.path.getsize(f) for f in glob.glob(os.path.join(dest_path, '*.mkv')))
        size_gb = total_size / (1024**3)

        # Remove review folder (including metadata.json)
        shutil.rmtree(source_path)

        activity.file_moved(identified_title, dest_path)
        activity.log_success(f"REVIEW: Identified and moved: {identified_title}")

        # Save to rip history
        activity.enrich_and_save_rip(
            title=identified_title,
            disc_type=metadata.get('disc_type', 'unknown'),
            duration_str='',
            size_gb=size_gb,
            year=year,
            tmdb_id=tmdb_id,
            poster_url=poster_url,
            runtime_str=metadata.get('runtime_str', ''),
            content_type=media_type,
            rip_method="review"
        )

        # Contribute to community disc database (if enabled)
        # Manual identifications are valuable contributions
        community_db.contribute_disc(
            disc_label=folder_name,  # Original disc label
            disc_type=metadata.get('disc_type', 'dvd'),
            duration_secs=metadata.get('duration_secs', 0),
            track_count=metadata.get('track_count', 0),
            title=identified_title.split(' (')[0],  # Strip year from title
            year=year,
            tmdb_id=tmdb_id,
            config=cfg
        )

        # Trigger Plex scan
        activity.plex_scan_triggered("Movies" if media_type == 'movie' else "TV Shows")

        return jsonify({
            'success': True,
            'message': f'Moved to {dest_path}',
            'destination': dest_path
        })

    except Exception as e:
        activity.log_error(f"REVIEW: Apply failed - {e}")
        return jsonify({'error': str(e)}), 500


@main.route('/api/review/delete', methods=['POST'])
def api_review_delete():
    """Delete a review item (remove from queue and disk)"""
    import os
    import shutil

    data = request.json or {}
    folder_name = data.get('folder_name')

    if not folder_name:
        return jsonify({'error': 'folder_name required'}), 400

    cfg = config.load_config()
    review_path = cfg.get('paths', {}).get('review', '/mnt/media/rips/review')
    source_path = os.path.join(review_path, folder_name)

    if not os.path.isdir(source_path):
        return jsonify({'error': f'Review folder not found: {folder_name}'}), 404

    try:
        shutil.rmtree(source_path)
        activity.log_warning(f"REVIEW: Deleted review item: {folder_name}")
        return jsonify({'success': True, 'message': f'Deleted {folder_name}'})
    except Exception as e:
        activity.log_error(f"REVIEW: Delete failed - {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Library Management Page
# ============================================================================

@main.route('/library')
def library():
    """Library management page"""
    cfg = config.load_config()
    return render_template('library.html', config=cfg)


@main.route('/api/library/list')
def api_library_list():
    """List all movies and TV shows in the library folders"""
    import os
    import re
    import glob
    import json

    cfg = config.load_config()
    movies_path = cfg.get('paths', {}).get('movies', '/mnt/media/movies')
    tv_path = cfg.get('paths', {}).get('tv', '/mnt/media/tv')

    # Load rip history to get poster URLs
    logs_dir = Path(__file__).parent.parent / "logs"
    history_file = logs_dir / "rip_history.json"
    rip_history = []
    try:
        if history_file.exists():
            with open(history_file) as f:
                rip_history = json.load(f)
    except Exception:
        pass

    # Build lookup by title and title+year for matching
    poster_lookup = {}
    for rip in rip_history:
        title = rip.get('title', '')
        year = rip.get('year')
        poster_url = rip.get('poster_url', '')
        if title and poster_url:
            # Key by "Title (Year)" format to match folder names
            if year:
                poster_lookup[f"{title} ({year})"] = poster_url
            poster_lookup[title.lower()] = poster_url

    def parse_folder_name(folder_name):
        """Parse 'Title (Year)' format from folder name"""
        match = re.match(r'^(.+?)\s*\((\d{4})\)$', folder_name)
        if match:
            return match.group(1).strip(), match.group(2)
        return folder_name, None

    def get_folder_info(folder_path, folder_name):
        """Get info about a library folder"""
        title, year = parse_folder_name(folder_name)

        # Calculate total size of MKV files
        size_bytes = 0
        has_mkv = False
        for mkv in glob.glob(os.path.join(folder_path, '*.mkv')):
            size_bytes += os.path.getsize(mkv)
            has_mkv = True

        # Also check subdirectories for TV shows
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                if f.endswith('.mkv'):
                    size_bytes += os.path.getsize(os.path.join(root, f))
                    has_mkv = True

        # Look up poster URL from rip history
        poster_url = poster_lookup.get(folder_name) or poster_lookup.get(title.lower())

        return {
            'folder_name': folder_name,
            'title': title,
            'year': year,
            'size_gb': round(size_bytes / (1024**3), 2) if size_bytes else 0,
            'has_mkv': has_mkv,
            'poster_url': poster_url
        }

    movies = []
    tv = []

    # Scan movies folder
    if os.path.isdir(movies_path):
        for folder_name in os.listdir(movies_path):
            folder_path = os.path.join(movies_path, folder_name)
            if os.path.isdir(folder_path):
                info = get_folder_info(folder_path, folder_name)
                if info['has_mkv']:
                    movies.append(info)

    # Scan TV folder
    if os.path.isdir(tv_path):
        for folder_name in os.listdir(tv_path):
            folder_path = os.path.join(tv_path, folder_name)
            if os.path.isdir(folder_path):
                info = get_folder_info(folder_path, folder_name)
                # TV shows may not have MKV directly in root
                info['has_mkv'] = True  # Assume valid if folder exists
                tv.append(info)

    # Sort alphabetically by title
    movies.sort(key=lambda x: x['title'].lower())
    tv.sort(key=lambda x: x['title'].lower())

    return jsonify({
        'movies': movies,
        'tv': tv,
        'movies_path': movies_path,
        'tv_path': tv_path
    })


@main.route('/api/library/rename', methods=['POST'])
def api_library_rename():
    """Rename a library folder and its MKV file"""
    import os
    import shutil
    import glob

    data = request.json or {}
    old_folder = data.get('old_folder')
    new_title = data.get('new_title')
    new_year = data.get('new_year')
    media_type = data.get('media_type', 'movies')

    if not old_folder or not new_title:
        return jsonify({'success': False, 'error': 'old_folder and new_title required'}), 400

    cfg = config.load_config()
    base_path = cfg.get('paths', {}).get('movies' if media_type == 'movies' else 'tv')

    if not base_path:
        return jsonify({'success': False, 'error': 'Library path not configured'}), 400

    old_path = os.path.join(base_path, old_folder)
    if not os.path.isdir(old_path):
        return jsonify({'success': False, 'error': f'Folder not found: {old_folder}'}), 404

    # Build new folder name
    new_folder = f"{new_title} ({new_year})" if new_year else new_title
    new_path = os.path.join(base_path, new_folder)

    # Check if target already exists (and isn't the same folder)
    if os.path.exists(new_path) and old_path.lower() != new_path.lower():
        return jsonify({'success': False, 'error': f'Folder already exists: {new_folder}'}), 400

    try:
        # For movies, also rename the MKV file inside
        if media_type == 'movies':
            mkv_files = glob.glob(os.path.join(old_path, '*.mkv'))
            for i, mkv_file in enumerate(mkv_files):
                old_mkv_name = os.path.basename(mkv_file)
                if len(mkv_files) == 1:
                    new_mkv_name = f"{new_folder}.mkv"
                else:
                    # Multiple files - keep part number
                    new_mkv_name = f"{new_folder} - Part {i+1}.mkv"
                new_mkv_path = os.path.join(old_path, new_mkv_name)
                if mkv_file != new_mkv_path:
                    os.rename(mkv_file, new_mkv_path)
                    activity.log_info(f"LIBRARY: Renamed file: {old_mkv_name} -> {new_mkv_name}")

        # Rename the folder
        if old_path != new_path:
            shutil.move(old_path, new_path)
            activity.log_info(f"LIBRARY: Renamed folder: {old_folder} -> {new_folder}")

        return jsonify({'success': True, 'new_folder': new_folder})

    except Exception as e:
        activity.log_error(f"LIBRARY: Rename failed - {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main.route('/api/library/delete', methods=['POST'])
def api_library_delete():
    """Delete a library folder and all its contents"""
    import os
    import shutil

    data = request.json or {}
    folder_name = data.get('folder_name')
    media_type = data.get('media_type', 'movies')

    if not folder_name:
        return jsonify({'success': False, 'error': 'folder_name required'}), 400

    cfg = config.load_config()
    base_path = cfg.get('paths', {}).get('movies' if media_type == 'movies' else 'tv')

    if not base_path:
        return jsonify({'success': False, 'error': 'Library path not configured'}), 400

    folder_path = os.path.join(base_path, folder_name)

    if not os.path.isdir(folder_path):
        return jsonify({'success': False, 'error': f'Folder not found: {folder_name}'}), 404

    try:
        shutil.rmtree(folder_path)
        activity.log_warning(f"LIBRARY: Deleted: {folder_name}")
        return jsonify({'success': True, 'message': f'Deleted {folder_name}'})
    except Exception as e:
        activity.log_error(f"LIBRARY: Delete failed - {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main.route('/api/library/rescan-plex', methods=['POST'])
def api_library_rescan_plex():
    """Trigger a Plex library scan"""
    data = request.json or {}
    library_type = data.get('library_type', 'all')  # 'movies', 'tv', or 'all'

    result = config.trigger_plex_scan(library_type)
    return jsonify(result)


@main.route('/api/library/identify', methods=['POST'])
def api_library_identify():
    """Search for movie/show identification"""
    data = request.json or {}
    query = data.get('query', '')
    media_type = data.get('media_type', 'movies')

    if not query:
        return jsonify({'results': [], 'error': 'Query required'}), 400

    cfg = config.load_config()
    from .identify import SmartIdentifier
    identifier = SmartIdentifier(cfg)

    results = []

    if media_type == 'movies':
        # Search Radarr/TMDB
        result = identifier.search_radarr(query, None)
        if result:
            results.append({
                'title': result.title,
                'year': result.year,
                'tmdb_id': result.tmdb_id,
                'runtime_minutes': result.runtime_minutes,
                'confidence': result.confidence,
                'folder_name': result.folder_name,
                'poster_url': result.poster_url,
                'media_type': 'movie'
            })
    else:
        # Search Sonarr for TV
        result = identifier.search_sonarr(query, [], 0)
        if result:
            results.append({
                'title': result.title,
                'year': result.year,
                'tmdb_id': result.tmdb_id,
                'tvdb_id': getattr(result, 'tvdb_id', None),
                'runtime_minutes': result.runtime_minutes,
                'confidence': result.confidence,
                'folder_name': result.folder_name,
                'poster_url': result.poster_url,
                'media_type': 'tv'
            })

    return jsonify({'results': results, 'query': query})
