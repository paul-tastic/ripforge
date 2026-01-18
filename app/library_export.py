"""
Library Export - Generate PDF of movies/TV shows
"""

import os
import io
import requests
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.enums import TA_LEFT, TA_CENTER

from . import config


EXPORT_DIR = Path(__file__).parent.parent / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_movies_from_radarr() -> List[Dict]:
    """Fetch all movies from Radarr"""
    cfg = config.load_config()
    radarr = cfg.get('integrations', {}).get('radarr', {})

    if not radarr.get('enabled') or not radarr.get('api_key'):
        return []

    url = radarr.get('url', 'http://localhost:7878')
    api_key = radarr.get('api_key')

    try:
        r = requests.get(
            f"{url}/api/v3/movie",
            headers={"X-Api-Key": api_key},
            timeout=30
        )
        if r.status_code == 200:
            movies = r.json()
            # Sort by title
            return sorted(movies, key=lambda m: m.get('title', '').lower())
    except Exception as e:
        print(f"Error fetching Radarr movies: {e}")

    return []


def fetch_shows_from_sonarr() -> List[Dict]:
    """Fetch all TV shows from Sonarr"""
    cfg = config.load_config()
    sonarr = cfg.get('integrations', {}).get('sonarr', {})

    if not sonarr.get('enabled') or not sonarr.get('api_key'):
        return []

    url = sonarr.get('url', 'http://localhost:8989')
    api_key = sonarr.get('api_key')

    try:
        r = requests.get(
            f"{url}/api/v3/series",
            headers={"X-Api-Key": api_key},
            timeout=30
        )
        if r.status_code == 200:
            shows = r.json()
            # Sort by title
            return sorted(shows, key=lambda s: s.get('title', '').lower())
    except Exception as e:
        print(f"Error fetching Sonarr shows: {e}")

    return []


def download_poster(url: str, max_width: float = 0.75*inch, max_height: float = 1*inch) -> Optional[Image]:
    """Download a poster image and return as reportlab Image"""
    if not url:
        return None

    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            img_data = io.BytesIO(r.content)
            img = Image(img_data, width=max_width, height=max_height)
            return img
    except Exception as e:
        print(f"Error downloading poster: {e}")

    return None


def generate_library_pdf(
    include_movies: bool = True,
    include_shows: bool = True,
    include_images: bool = False,
    filename: str = None
) -> str:
    """
    Generate a PDF of the library.

    Args:
        include_movies: Include movies from Radarr
        include_shows: Include TV shows from Sonarr
        include_images: Include poster images (slower, larger file)
        filename: Custom filename (without extension)

    Returns:
        Path to the generated PDF file
    """

    # Generate filename
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"library_export_{timestamp}"

    pdf_path = EXPORT_DIR / f"{filename}.pdf"

    # Create PDF document
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch
    )

    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=12,
        alignment=TA_CENTER
    )
    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=14,
        spaceBefore=20,
        spaceAfter=10,
        textColor=colors.HexColor('#60a5fa')
    )
    normal_style = styles['Normal']

    # Build content
    story = []

    # Title
    story.append(Paragraph("Media Library", title_style))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        ParagraphStyle('Date', parent=normal_style, alignment=TA_CENTER, textColor=colors.grey)
    ))
    story.append(Spacer(1, 20))

    # Movies section
    if include_movies:
        movies = fetch_movies_from_radarr()
        if movies:
            story.append(Paragraph(f"Movies ({len(movies)})", section_style))

            if include_images:
                # Table with images
                movie_data = []
                for movie in movies:
                    year = movie.get('year', '')
                    title = f"{movie.get('title', 'Unknown')} ({year})" if year else movie.get('title', 'Unknown')

                    # Get poster URL
                    poster = None
                    for image in movie.get('images', []):
                        if image.get('coverType') == 'poster':
                            poster_url = image.get('remoteUrl') or image.get('url')
                            if poster_url:
                                poster = download_poster(poster_url)
                            break

                    movie_data.append([poster or '', title])

                # Create table
                if movie_data:
                    table = Table(movie_data, colWidths=[1*inch, 6*inch])
                    table.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 4),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
                    ]))
                    story.append(table)
            else:
                # Simple text list
                for movie in movies:
                    year = movie.get('year', '')
                    title = f"{movie.get('title', 'Unknown')} ({year})" if year else movie.get('title', 'Unknown')
                    story.append(Paragraph(f"• {title}", normal_style))

            story.append(Spacer(1, 20))

    # TV Shows section
    if include_shows:
        shows = fetch_shows_from_sonarr()
        if shows:
            story.append(Paragraph(f"TV Shows ({len(shows)})", section_style))

            if include_images:
                # Table with images
                show_data = []
                for show in shows:
                    year = show.get('year', '')
                    seasons = show.get('seasonCount', 0)
                    title = f"{show.get('title', 'Unknown')} ({year})" if year else show.get('title', 'Unknown')
                    title += f" - {seasons} season{'s' if seasons != 1 else ''}"

                    # Get poster URL
                    poster = None
                    for image in show.get('images', []):
                        if image.get('coverType') == 'poster':
                            poster_url = image.get('remoteUrl') or image.get('url')
                            if poster_url:
                                poster = download_poster(poster_url)
                            break

                    show_data.append([poster or '', title])

                # Create table
                if show_data:
                    table = Table(show_data, colWidths=[1*inch, 6*inch])
                    table.setStyle(TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 4),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
                    ]))
                    story.append(table)
            else:
                # Simple text list
                for show in shows:
                    year = show.get('year', '')
                    seasons = show.get('seasonCount', 0)
                    title = f"{show.get('title', 'Unknown')} ({year})" if year else show.get('title', 'Unknown')
                    title += f" - {seasons} season{'s' if seasons != 1 else ''}"
                    story.append(Paragraph(f"• {title}", normal_style))

    # Build PDF
    doc.build(story)

    return str(pdf_path)


def email_library_pdf(
    recipients: List[str],
    pdf_path: str,
    include_movies: bool = True,
    include_shows: bool = True
) -> bool:
    """Email the library PDF to recipients"""
    from . import email as email_utils

    cfg = config.load_config()
    email_cfg = cfg.get('notifications', {}).get('email', {})

    # Determine what was included
    content_types = []
    if include_movies:
        content_types.append("movies")
    if include_shows:
        content_types.append("TV shows")
    content_str = " and ".join(content_types)

    subject = f"Media Library Export - {content_str.title()}"

    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333;">
        <h2>Media Library Export</h2>
        <p>Attached is your media library export containing {content_str}.</p>
        <p>Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
        <hr>
        <p style="color: #666; font-size: 12px;">Sent by RipForge</p>
    </body>
    </html>
    """

    # Use SendGrid or msmtp based on config
    provider = email_cfg.get('provider', 'msmtp')

    if provider == 'sendgrid':
        api_key = email_cfg.get('sendgrid_api_key')
        if not api_key:
            print("SendGrid API key not configured")
            return False
        return email_utils.send_via_sendgrid_with_attachment(
            recipients, subject, body, api_key, pdf_path
        )
    else:
        # msmtp with attachment
        return email_utils.send_via_msmtp_with_attachment(
            recipients, subject, body, pdf_path
        )
