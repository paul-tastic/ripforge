"""
RipForge Email Notifications
Uses system msmtp for sending emails
"""

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from . import config


def send_email(to: list, subject: str, body: str, html: bool = False) -> bool:
    """Send email using msmtp"""
    try:
        # Build email content
        content_type = "text/html" if html else "text/plain"
        recipients = ", ".join(to) if isinstance(to, list) else to

        email = f"""From: RipForge <paul@dotvector.com>
To: {recipients}
Subject: {subject}
Content-Type: {content_type}; charset=utf-8
MIME-Version: 1.0

{body}
"""
        # Send via msmtp
        for recipient in (to if isinstance(to, list) else [to]):
            proc = subprocess.run(
                ["msmtp", recipient],
                input=email,
                capture_output=True,
                text=True,
                timeout=30
            )
            if proc.returncode != 0:
                print(f"Email error: {proc.stderr}")
                return False

        return True

    except Exception as e:
        print(f"Email exception: {e}")
        return False


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
            <td style="padding: 10px 0; color: #888;">Title</td>
            <td style="padding: 10px 0; color: #fff; font-weight: bold;">{title}</td>
        </tr>
        <tr>
            <td style="padding: 10px 0; color: #888;">Error</td>
            <td style="padding: 10px 0; color: #f87171;">{error}</td>
        </tr>
    </table>
    <p style="color: #888; margin-top: 30px; font-size: 12px;">Sent by RipForge</p>
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


def send_weekly_recap(recipients: list) -> bool:
    """Send weekly recap of ripping activity"""
    cfg = config.load_config()

    # Get rip history from the past week
    # For now, we'll read from a simple log file
    history_file = Path(cfg.get('paths', {}).get('logs', '/home/paul/ripforge/logs')) / 'rip_history.json'

    rips_this_week = []
    total_size = 0

    if history_file.exists():
        import json
        try:
            with open(history_file) as f:
                history = json.load(f)

            week_ago = (datetime.now() - timedelta(days=7)).isoformat()

            for rip in history:
                if rip.get('completed_at', '') >= week_ago:
                    rips_this_week.append(rip)
                    total_size += rip.get('size_mb', 0)
        except Exception as e:
            print(f"Error reading history: {e}")

    # Build recap email
    subject = f"RipForge: Weekly Recap - {len(rips_this_week)} discs ripped"

    if rips_this_week:
        rows = ""
        for rip in rips_this_week:
            status_color = "#4ade80" if rip.get('status') == 'complete' else "#f87171"
            rows += f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #333; color: #fff;">{rip.get('title', 'Unknown')}</td>
                <td style="padding: 10px; border-bottom: 1px solid #333; color: #888;">{rip.get('runtime', '-')}</td>
                <td style="padding: 10px; border-bottom: 1px solid #333; color: {status_color};">{rip.get('status', '-').title()}</td>
            </tr>
            """

        body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #e5a00d; margin: 0 0 10px;">Weekly Recap</h1>
    <p style="color: #888; margin-bottom: 20px;">Week of {(datetime.now() - timedelta(days=7)).strftime('%b %d')} - {datetime.now().strftime('%b %d, %Y')}</p>

    <div style="display: flex; gap: 20px; margin-bottom: 30px;">
        <div style="flex: 1; background: #1a1a1a; padding: 20px; border-radius: 8px; text-align: center;">
            <div style="font-size: 36px; font-weight: bold; color: #e5a00d;">{len(rips_this_week)}</div>
            <div style="color: #888; font-size: 12px; text-transform: uppercase;">Discs Ripped</div>
        </div>
        <div style="flex: 1; background: #1a1a1a; padding: 20px; border-radius: 8px; text-align: center;">
            <div style="font-size: 36px; font-weight: bold; color: #e5a00d;">{total_size / 1024:.1f}</div>
            <div style="color: #888; font-size: 12px; text-transform: uppercase;">GB Added</div>
        </div>
    </div>

    <table style="width: 100%; border-collapse: collapse;">
        <thead>
            <tr>
                <th style="text-align: left; padding: 10px; border-bottom: 2px solid #444; color: #888;">Title</th>
                <th style="text-align: left; padding: 10px; border-bottom: 2px solid #444; color: #888;">Runtime</th>
                <th style="text-align: left; padding: 10px; border-bottom: 2px solid #444; color: #888;">Status</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>

    <p style="color: #888; margin-top: 30px; font-size: 12px;">Sent by RipForge</p>
</div>
</body>
</html>
"""
    else:
        body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <h1 style="color: #e5a00d; margin: 0 0 10px;">Weekly Recap</h1>
    <p style="color: #888; margin-bottom: 20px;">Week of {(datetime.now() - timedelta(days=7)).strftime('%b %d')} - {datetime.now().strftime('%b %d, %Y')}</p>
    <p style="color: #fff; text-align: center; padding: 40px 0;">No discs ripped this week</p>
    <p style="color: #888; margin-top: 30px; font-size: 12px;">Sent by RipForge</p>
</div>
</body>
</html>
"""

    return send_email(recipients, subject, body, html=True)
