"""
RipForge Email Notifications
Supports SendGrid API or system msmtp for sending emails
"""

import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from . import config


def get_sendgrid_suppressions(api_key: str) -> set:
    """Fetch all suppressed emails from SendGrid (unsubscribes, bounces, spam reports)"""
    suppressions = set()

    # Check all suppression types
    for endpoint in ['unsubscribes', 'bounces', 'spam_reports']:
        try:
            resp = requests.get(
                f'https://api.sendgrid.com/v3/suppression/{endpoint}',
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=10
            )
            if resp.status_code == 200:
                for item in resp.json():
                    email = item.get('email', '').lower()
                    if email:
                        suppressions.add(email)
        except Exception as e:
            print(f"Error fetching {endpoint}: {e}")

    return suppressions


def filter_suppressed_recipients(recipients: list, api_key: str) -> list:
    """Remove suppressed emails from recipient list"""
    suppressions = get_sendgrid_suppressions(api_key)
    if not suppressions:
        return recipients

    valid = []
    for r in recipients:
        email = r if isinstance(r, str) else r.get('email', '')
        if email.lower() not in suppressions:
            valid.append(r)
        else:
            print(f"Skipping suppressed email: {email}")

    return valid


def sync_suppressions_to_config() -> int:
    """Check SendGrid suppressions and disable matching recipients in config.
    Returns count of newly suppressed recipients."""
    cfg = config.load_config()
    email_cfg = cfg.get('notifications', {}).get('email', {})
    api_key = email_cfg.get('sendgrid_api_key')

    if not api_key:
        return 0

    suppressions = get_sendgrid_suppressions(api_key)
    if not suppressions:
        return 0

    recipients = email_cfg.get('recipients', [])
    count = 0

    for r in recipients:
        if isinstance(r, dict):
            email = r.get('email', '').lower()
            if email in suppressions and not r.get('suppressed'):
                r['enabled'] = False
                r['suppressed'] = True
                count += 1

    if count > 0:
        config.save_config(cfg)

    return count


def send_via_sendgrid(to: list, subject: str, body: str, api_key: str, from_name: str = "RipForge", include_unsubscribe: bool = True) -> bool:
    """Send email via SendGrid API"""
    try:
        data = {
            "personalizations": [{"to": [{"email": r} for r in to]}],
            "from": {"email": "paul@dotvector.com", "name": from_name},
            "subject": subject,
            "content": [{"type": "text/html", "value": body}]
        }

        # Add unsubscribe tracking if enabled
        if include_unsubscribe:
            data["tracking_settings"] = {
                "subscription_tracking": {
                    "enable": True,
                    "text": "Unsubscribe from these emails",
                    "html": '<p style="text-align: center; margin-top: 20px;"><a href="{{{unsubscribe}}}" style="color: #888; font-size: 11px;">Unsubscribe</a></p>'
                }
            }

        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=data,
            timeout=30
        )

        if response.status_code == 202:
            return True
        else:
            print(f"SendGrid error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"SendGrid exception: {e}")
        return False


def send_via_msmtp(to: list, subject: str, body: str, html: bool = False, from_name: str = "RipForge") -> bool:
    """Send email using system msmtp"""
    try:
        content_type = "text/html" if html else "text/plain"
        recipients = ", ".join(to) if isinstance(to, list) else to

        email = f"""From: {from_name} <paul@dotvector.com>
To: {recipients}
Subject: {subject}
Content-Type: {content_type}; charset=utf-8
MIME-Version: 1.0

{body}
"""
        for recipient in (to if isinstance(to, list) else [to]):
            proc = subprocess.run(
                ["msmtp", recipient],
                input=email,
                capture_output=True,
                text=True,
                timeout=30
            )
            if proc.returncode != 0:
                print(f"msmtp error: {proc.stderr}")
                return False

        return True

    except Exception as e:
        print(f"msmtp exception: {e}")
        return False


def send_email(to: list, subject: str, body: str, html: bool = False, from_name: str = None) -> bool:
    """Send email - routes to SendGrid or msmtp based on config"""
    cfg = config.load_config()
    email_cfg = cfg.get('notifications', {}).get('email', {})

    provider = email_cfg.get('provider', 'msmtp')
    api_key = email_cfg.get('sendgrid_api_key', '')

    # Filter suppressed recipients if enabled (SendGrid only)
    if email_cfg.get('check_suppressions', True) and api_key and provider == 'sendgrid':
        to = filter_suppressed_recipients(to, api_key)
        if not to:
            print("All recipients are suppressed, skipping email")
            return False

    # Use provided from_name or fall back to config or default
    if not from_name:
        from_name = email_cfg.get('from_name', 'RipForge')

    if provider == 'sendgrid':
        if api_key:
            include_unsubscribe = email_cfg.get('sendgrid_unsubscribe_footer', True)
            return send_via_sendgrid(to, subject, body, api_key, from_name, include_unsubscribe)
        else:
            print("SendGrid selected but no API key configured, falling back to msmtp")

    # Fallback to msmtp
    return send_via_msmtp(to, subject, body, html, from_name)


def send_rip_complete(title: str, runtime: str, path: str, recipients: list) -> bool:
    """Send notification that a rip completed successfully"""
    subject = f"RipForge: {title} ripped successfully"

    body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #e5a00d; margin: 0 0 20px;">Rip Complete</h1>
    <table style="width: 100%; border-collapse: collapse;">
        <tr>
            <td style="padding: 10px 0; color: #888;">Title</td>
            <td style="padding: 10px 0; color: #fff; font-weight: bold;">{title}</td>
        </tr>
        <tr>
            <td style="padding: 10px 0; color: #888;">Runtime</td>
            <td style="padding: 10px 0; color: #fff;">{runtime}</td>
        </tr>
        <tr>
            <td style="padding: 10px 0; color: #888;">Location</td>
            <td style="padding: 10px 0; color: #fff; font-family: monospace; font-size: 12px;">{path}</td>
        </tr>
    </table>
    <p style="color: #888; margin-top: 30px; font-size: 12px;">Sent by RipForge</p>
</div>
</body>
</html>
"""
    return send_email(recipients, subject, body, html=True)


def send_rip_error(title: str, error: str, recipients: list) -> bool:
    """Send notification that a rip failed"""
    subject = f"RipForge: Error ripping {title}"

    body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #f87171; margin: 0 0 20px;">Rip Failed</h1>
    <table style="width: 100%; border-collapse: collapse;">
        <tr>
            <td style="padding: 10px 20px 10px 0; color: #888; vertical-align: top; width: 80px;">Title</td>
            <td style="padding: 10px 0; color: #fff; font-weight: bold;">{title}</td>
        </tr>
        <tr>
            <td style="padding: 10px 20px 10px 0; color: #888; vertical-align: top; width: 80px;">Error</td>
            <td style="padding: 10px 0; color: #f87171;">{error}</td>
        </tr>
    </table>
    <p style="color: #888; margin-top: 30px; font-size: 12px;">Sent by RipForge</p>
</div>
</body>
</html>
"""
    return send_email(recipients, subject, body, html=True)


def send_uncertain_identification(
    disc_label: str,
    best_guess: str,
    confidence: int,
    runtime_str: str,
    recipients: list,
    review_url: str = "http://192.168.0.104:8081"
) -> bool:
    """Send notification that a disc couldn't be confidently identified"""
    subject = f"RipForge: Help identify disc - {disc_label}"

    # Confidence color
    if confidence >= 50:
        conf_color = "#f59e0b"  # Amber
        conf_label = "MEDIUM"
    else:
        conf_color = "#ef4444"  # Red
        conf_label = "LOW"

    body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #f59e0b; margin: 0 0 20px;">🔍 Identification Needed</h1>
    <p style="color: #fff; margin-bottom: 24px;">RipForge couldn't confidently identify this disc. Please review before ripping.</p>

    <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
        <tr>
            <td style="padding: 12px 0; color: #888; border-bottom: 1px solid #333;">Disc Label</td>
            <td style="padding: 12px 0; color: #fff; font-weight: bold; font-family: monospace; border-bottom: 1px solid #333;">{disc_label}</td>
        </tr>
        <tr>
            <td style="padding: 12px 0; color: #888; border-bottom: 1px solid #333;">Best Guess</td>
            <td style="padding: 12px 0; color: #fff; border-bottom: 1px solid #333;">{best_guess or 'Unknown'}</td>
        </tr>
        <tr>
            <td style="padding: 12px 0; color: #888; border-bottom: 1px solid #333;">Confidence</td>
            <td style="padding: 12px 0; border-bottom: 1px solid #333;">
                <span style="background: {conf_color}; color: #000; padding: 4px 12px; border-radius: 4px; font-weight: bold; font-size: 12px;">{confidence}% {conf_label}</span>
            </td>
        </tr>
        <tr>
            <td style="padding: 12px 0; color: #888;">Runtime</td>
            <td style="padding: 12px 0; color: #fff;">{runtime_str or 'Unknown'}</td>
        </tr>
    </table>

    <div style="text-align: center; margin-top: 24px;">
        <a href="{review_url}" style="display: inline-block; background: #e5a00d; color: #000; padding: 12px 32px; border-radius: 8px; text-decoration: none; font-weight: bold;">Review & Rip</a>
    </div>

    <p style="color: #666; margin-top: 24px; font-size: 12px; text-align: center;">
        The disc is waiting for your review. Click above to confirm the title and start ripping.
    </p>

    <p style="color: #555; margin-top: 24px; font-size: 11px; text-align: center;">Sent by RipForge</p>
</div>
</body>
</html>
"""
    return send_email(recipients, subject, body, html=True)


def send_test_email(recipients: list) -> bool:
    """Send a test email to verify configuration"""
    subject = "RipForge: Test Email"

    body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #4ade80; margin: 0 0 20px;">Email Test Successful</h1>
    <p style="color: #fff;">Your RipForge email notifications are configured correctly.</p>
    <p style="color: #888; margin-top: 20px;">Sent at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p style="color: #888; margin-top: 30px; font-size: 12px;">Sent by RipForge</p>
</div>
</body>
</html>
"""
    return send_email(recipients, subject, body, html=True)


def _build_content_card(item: dict, is_tv: bool = False) -> str:
    """Build HTML card for a movie or TV show"""
    poster_url = item.get('poster_url', '')
    if poster_url:
        poster_html = f'<img src="{poster_url}" alt="" style="width: 100px; height: 150px; object-fit: cover; border-radius: 6px;">'
    else:
        icon = "📺" if is_tv else "🎬"
        poster_html = f'<div style="width: 100px; height: 150px; background: #333; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: #666; font-size: 32px;">{icon}</div>'

    disc_badge = item.get('disc_type', '').upper()
    badge_color = "#0095d9" if disc_badge == "BLURAY" else "#f97316" if disc_badge == "DVD" else "#666"

    # Truncate overview to ~120 chars
    overview = item.get('overview', '')
    if len(overview) > 120:
        overview = overview[:117] + '...'

    # Build ratings display (movies only - TV shows don't have RT scores in the same way)
    rt_rating = item.get('rt_rating', 0)
    imdb_rating = item.get('imdb_rating', 0)
    ratings_html = ""
    if rt_rating:
        tomato = "🍅" if rt_rating >= 60 else "🥫"
        ratings_html += f'<span style="font-size: 12px; color: #fff; margin-right: 12px;">{tomato} {rt_rating}%</span>'
    if imdb_rating:
        ratings_html += f'<span style="font-size: 12px; color: #f5c518;">⭐ {imdb_rating:.1f}</span>'

    # Runtime display
    runtime = item.get('runtime_str', '')

    # For TV shows, add season info
    subtitle_parts = []
    if item.get('year'):
        subtitle_parts.append(str(item['year']))
    if runtime:
        subtitle_parts.append(runtime)
    if is_tv and item.get('seasons_modified'):
        seasons = item['seasons_modified']
        if len(seasons) == 1:
            subtitle_parts.append(f"Season {seasons[0]}")
        else:
            subtitle_parts.append(f"Seasons {', '.join(map(str, seasons))}")
    subtitle = ' • '.join(subtitle_parts)

    # Badge section
    badge_html = ""
    if disc_badge:
        badge_html = f'<span style="font-size: 10px; background: {badge_color}; color: #fff; padding: 2px 8px; border-radius: 4px; font-weight: 600;">{disc_badge}</span>'

    return f"""
    <div style="display: flex; padding: 16px; background: #1a1a1a; border-radius: 8px; margin-bottom: 12px;">
        {poster_html}
        <div style="flex: 1; padding-left: 16px;">
            <div style="font-size: 17px; font-weight: 600; color: #fff; margin-bottom: 4px;">{item.get('title', 'Unknown')}</div>
            <div style="font-size: 12px; color: #888; margin-bottom: 6px;">{subtitle}</div>
            <div style="margin-bottom: 8px;">{ratings_html}</div>
            <div style="font-size: 12px; color: #aaa; line-height: 1.4; margin-bottom: 10px;">{overview}</div>
            <div style="display: flex; align-items: center;">
                {badge_html}
                <span style="font-size: 11px; color: #888; margin-left: 8px;">{item.get('size_gb', 0):.1f} GB</span>
            </div>
        </div>
    </div>
    """


def send_weekly_recap(recipients: list, test_mode: bool = False) -> bool:
    """Send weekly recap of library additions with movie posters, ratings, and blurbs.

    Uses filesystem scan to detect recently added content - more reliable than
    tracking rip history which can have stale/duplicate data.

    Args:
        recipients: List of email addresses to send to
        test_mode: If True, only send to paul@dotvector.com regardless of recipients list
    """
    from . import activity

    # Get config for email settings
    cfg = config.load_config()
    email_cfg = cfg.get('notifications', {}).get('email', {})
    from_name = email_cfg.get('from_name', 'Plex Media Server')
    weekly_subject = email_cfg.get('weekly_subject', 'Weekly Digest - New Additions')

    # Override recipients in test mode
    if test_mode:
        recipients = ['paul@dotvector.com']

    # Scan filesystem for recently added content
    content = activity.scan_library_for_recent(days=7)
    movies = content.get('movies', [])
    tv_shows = content.get('tv', [])

    total_count = len(movies) + len(tv_shows)
    total_size = sum(m.get('size_gb', 0) for m in movies) + sum(t.get('size_gb', 0) for t in tv_shows)

    # Build subject with count
    subject = f"{weekly_subject} ({total_count} titles)" if total_count else weekly_subject

    if total_count:
        # Build movie cards section
        movies_section = ""
        if movies:
            movie_cards = "".join(_build_content_card(m, is_tv=False) for m in movies)
            movies_section = f"""
            <h2 style="color: #fff; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; margin-top: 24px; border-bottom: 1px solid #333; padding-bottom: 8px;">🎬 Movies ({len(movies)})</h2>
            {movie_cards}
            """

        # Build TV shows section
        tv_section = ""
        if tv_shows:
            tv_cards = "".join(_build_content_card(t, is_tv=True) for t in tv_shows)
            tv_section = f"""
            <h2 style="color: #fff; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; margin-top: 24px; border-bottom: 1px solid #333; padding-bottom: 8px;">📺 TV Shows ({len(tv_shows)})</h2>
            {tv_cards}
            """

        body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <div style="text-align: center; margin-bottom: 24px;">
        <h1 style="color: #e5a00d; margin: 0 0 8px; font-size: 26px;">{from_name}</h1>
        <p style="color: #888; margin: 0; font-size: 14px;">{(datetime.now() - timedelta(days=7)).strftime('%b %d')} - {datetime.now().strftime('%b %d, %Y')}</p>
    </div>

    <div style="text-align: center; margin-bottom: 8px;">
        <span style="font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 1px;">This Week</span>
    </div>
    <div style="display: flex; gap: 16px; margin-bottom: 24px;">
        <div style="flex: 1; background: #1a1a1a; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 32px; font-weight: bold; color: #e5a00d;">{total_count}</div>
            <div style="color: #888; font-size: 11px; text-transform: uppercase;">New Titles</div>
        </div>
        <div style="flex: 1; background: #1a1a1a; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 32px; font-weight: bold; color: #e5a00d;">{total_size:.1f}</div>
            <div style="color: #888; font-size: 11px; text-transform: uppercase;">GB Added</div>
        </div>
    </div>

    {movies_section}
    {tv_section}

    <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #333; text-align: center;">
        <p style="color: #666; font-size: 11px; margin: 0;">
            Media ripped by <a href="https://github.com/paul-tastic/ripforge" style="color: #e5a00d; text-decoration: none;">RipForge</a>
        </p>
        <p style="color: #555; font-size: 10px; margin: 8px 0 0; font-style: italic;">
            To unsubscribe, just text Paul since you know him
        </p>
    </div>
</div>
</body>
</html>
"""
    else:
        body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #e5a00d; margin: 0 0 10px;">{from_name}</h1>
    <p style="color: #888; margin-bottom: 20px;">Week of {(datetime.now() - timedelta(days=7)).strftime('%b %d')} - {datetime.now().strftime('%b %d, %Y')}</p>
    <p style="color: #fff; text-align: center; padding: 40px 0;">No new titles this week</p>
    <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #333; text-align: center;">
        <p style="color: #666; font-size: 11px; margin: 0;">
            Media ripped by <a href="https://github.com/paul-tastic/ripforge" style="color: #e5a00d; text-decoration: none;">RipForge</a>
        </p>
        <p style="color: #555; font-size: 10px; margin: 8px 0 0; font-style: italic;">
            To unsubscribe, just text Paul since you know him
        </p>
    </div>
</div>
</body>
</html>
"""

    return send_email(recipients, subject, body, html=True, from_name=from_name)


def send_via_sendgrid_with_attachment(to: list, subject: str, body: str, api_key: str, attachment_path: str, from_name: str = "RipForge") -> bool:
    """Send email via SendGrid API with file attachment"""
    import base64
    from pathlib import Path

    attachment_file = Path(attachment_path)
    if not attachment_file.exists():
        print(f"Attachment not found: {attachment_path}")
        return False

    try:
        # Read and encode attachment
        with open(attachment_file, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')

        data = {
            "personalizations": [{"to": [{"email": r} for r in to]}],
            "from": {"email": "paul@dotvector.com", "name": from_name},
            "subject": subject,
            "content": [{"type": "text/html", "value": body}],
            "attachments": [{
                "content": content,
                "filename": attachment_file.name,
                "type": "application/pdf",
                "disposition": "attachment"
            }]
        }

        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=data,
            timeout=60
        )

        if response.status_code == 202:
            return True
        else:
            print(f"SendGrid error: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"Error sending email with attachment via SendGrid: {e}")
        return False


def send_via_msmtp_with_attachment(to: list, subject: str, body: str, attachment_path: str) -> bool:
    """Send email via msmtp with file attachment"""
    import base64
    from pathlib import Path
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    attachment_file = Path(attachment_path)
    if not attachment_file.exists():
        print(f"Attachment not found: {attachment_path}")
        return False

    try:
        # Create message
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['To'] = ', '.join(to)

        # Add body
        msg.attach(MIMEText(body, 'html'))

        # Add attachment
        with open(attachment_file, 'rb') as f:
            part = MIMEBase('application', 'pdf')
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{attachment_file.name}"'
            )
            msg.attach(part)

        # Send via msmtp
        for recipient in to:
            result = subprocess.run(
                ["msmtp", recipient],
                input=msg.as_string(),
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                print(f"msmtp error for {recipient}: {result.stderr}")
                return False

        return True

    except Exception as e:
        print(f"Error sending email with attachment via msmtp: {e}")
        return False
