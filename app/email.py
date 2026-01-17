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


def send_via_sendgrid(to: list, subject: str, body: str, api_key: str) -> bool:
    """Send email via SendGrid API"""
    try:
        data = {
            "personalizations": [{"to": [{"email": r} for r in to]}],
            "from": {"email": "paul@dotvector.com", "name": "RipForge"},
            "subject": subject,
            "content": [{"type": "text/html", "value": body}]
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


def send_via_msmtp(to: list, subject: str, body: str, html: bool = False) -> bool:
    """Send email using system msmtp"""
    try:
        content_type = "text/html" if html else "text/plain"
        recipients = ", ".join(to) if isinstance(to, list) else to

        email = f"""From: RipForge <paul@dotvector.com>
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


def send_email(to: list, subject: str, body: str, html: bool = False) -> bool:
    """Send email - routes to SendGrid or msmtp based on config"""
    cfg = config.load_config()
    email_cfg = cfg.get('notifications', {}).get('email', {})

    provider = email_cfg.get('provider', 'msmtp')

    if provider == 'sendgrid':
        api_key = email_cfg.get('sendgrid_api_key')
        if api_key:
            return send_via_sendgrid(to, subject, body, api_key)
        else:
            print("SendGrid selected but no API key configured, falling back to msmtp")

    # Fallback to msmtp
    return send_via_msmtp(to, subject, body, html)


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


def send_weekly_recap(recipients: list) -> bool:
    """Send weekly recap of ripping activity with movie posters"""
    from . import activity

    # Get rips from the past week
    rips_this_week = activity.get_recent_rips(days=7)
    total_size = sum(rip.get('size_gb', 0) for rip in rips_this_week)

    # Build recap email
    subject = f"RipForge: Weekly Recap - {len(rips_this_week)} discs ripped"

    if rips_this_week:
        # Build movie cards with posters
        movie_cards = ""
        for rip in rips_this_week:
            poster_url = rip.get('poster_url', '')
            # Use a placeholder if no poster
            poster_html = f'<img src="{poster_url}" alt="" style="width: 80px; height: 120px; object-fit: cover; border-radius: 6px;">' if poster_url else '<div style="width: 80px; height: 120px; background: #333; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: #666; font-size: 24px;">🎬</div>'

            disc_badge = rip.get('disc_type', '').upper()
            badge_color = "#0095d9" if disc_badge == "BLURAY" else "#f97316"  # Blu-ray blue, DVD orange

            movie_cards += f"""
            <div style="display: flex; gap: 16px; padding: 16px; background: #1a1a1a; border-radius: 8px; margin-bottom: 12px;">
                {poster_html}
                <div style="flex: 1;">
                    <div style="font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 6px;">{rip.get('title', 'Unknown')}</div>
                    <div style="font-size: 12px; color: #888; margin-bottom: 8px;">
                        {rip.get('year', '')} • {rip.get('runtime', '')}
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <span style="font-size: 10px; background: {badge_color}; color: #fff; padding: 2px 8px; border-radius: 4px; font-weight: 600;">{disc_badge or 'DISC'}</span>
                        <span style="font-size: 11px; color: #888;">{rip.get('size_gb', 0):.1f} GB</span>
                        <span style="font-size: 11px; color: #4ade80;">✓ Complete</span>
                    </div>
                </div>
            </div>
            """

        body = f"""
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a1a; color: #eee; padding: 20px;">
<div style="max-width: 600px; margin: 0 auto; background: #252525; padding: 30px; border-radius: 12px;">
    <div style="text-align: center; margin-bottom: 24px;">
        <h1 style="color: #e5a00d; margin: 0 0 8px; font-size: 28px;">🔥 Weekly Recap</h1>
        <p style="color: #888; margin: 0; font-size: 14px;">{(datetime.now() - timedelta(days=7)).strftime('%b %d')} - {datetime.now().strftime('%b %d, %Y')}</p>
    </div>

    <div style="display: flex; gap: 16px; margin-bottom: 24px;">
        <div style="flex: 1; background: #1a1a1a; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 32px; font-weight: bold; color: #e5a00d;">{len(rips_this_week)}</div>
            <div style="color: #888; font-size: 11px; text-transform: uppercase;">Discs Ripped</div>
        </div>
        <div style="flex: 1; background: #1a1a1a; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 32px; font-weight: bold; color: #e5a00d;">{total_size:.1f}</div>
            <div style="color: #888; font-size: 11px; text-transform: uppercase;">GB Added</div>
        </div>
    </div>

    <h2 style="color: #fff; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; border-bottom: 1px solid #333; padding-bottom: 8px;">Recently Added</h2>

    {movie_cards}

    <p style="color: #555; margin-top: 24px; font-size: 11px; text-align: center;">Sent by RipForge • Powered by SendGrid</p>
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
