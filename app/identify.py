"""
RipForge Smart Identification
Identifies ripped content using disc label parsing + runtime matching with Radarr/Sonarr
"""

import re
import os
import subprocess
import requests
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class IdentificationResult:
    """Result of content identification"""
    title: str = ""
    year: int = 0
    tmdb_id: int = 0
    runtime_minutes: int = 0
    confidence: int = 0
    media_type: str = "movie"  # movie or tv
    radarr_id: Optional[int] = None
    sonarr_id: Optional[int] = None

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 75

    @property
    def folder_name(self) -> str:
        """Generate filesystem-safe folder name"""
        name = f"{self.title} ({self.year})"
        # Remove invalid characters
        name = re.sub(r'[:]', '-', name)
        name = re.sub(r'[?<>"|*]', '', name)
        return name


class SmartIdentifier:
    """Smart content identification using label parsing + runtime matching"""

    # Studio prefixes to strip
    STUDIO_PREFIXES = [
        'MARVEL_STUDIOS_?', 'DISNEY_?', 'PIXAR_?', 'WARNER_?', 'WARNER_BROS_?', 'WB_?',
        'UNIVERSAL_?', 'SONY_?', 'COLUMBIA_?', 'PARAMOUNT_?', '20TH_CENTURY_?', 'FOX_?',
        'LIONSGATE_?', 'MGM_?', 'DREAMWORKS_?', 'NEW_LINE_?', 'HBO_?', 'A24_?',
        'BLU-?RAY_?', 'DVD_?', 'BD_?', 'UHD_?', '4K_?'
    ]

    # Franchise-specific patterns
    FRANCHISE_PATTERNS = [
        # Guardians of the Galaxy
        (r'^GUARDIANS\s*(\d+)$', r'Guardians of the Galaxy Vol \1'),
        (r'^GUARDIANS\s+OF\s+THE\s+GALAXY\s*(\d*)$', r'Guardians of the Galaxy Vol \1'),

        # John Wick
        (r'^JOHN\s*WICK\s*(\d+)$', r'John Wick Chapter \1'),

        # Spider-Man
        (r'^SPIDER\s*MAN', 'Spider-Man'),

        # Mission Impossible
        (r'^MISSION\s*IMPOSSIBLE', 'Mission Impossible'),

        # Jurassic World/Park
        (r'^JURASSIC\s*WORLD', 'Jurassic World'),
        (r'^JURASSIC\s*PARK', 'Jurassic Park'),

        # Fast and Furious
        (r'^(FAST|F)\s*(AND|&|N)?\s*(FURIOUS)?\s*X$', 'Fast X'),
        (r'^(FAST|F)\s*(AND|&|N)?\s*(FURIOUS)?\s*(\d+)$', r'Fast & Furious \4'),

        # Transformers
        (r'^TRANSFORMERS', 'Transformers'),

        # Avatar
        (r'^AVATAR\s*(2|WAY|THE)?.*WATER', 'Avatar The Way of Water'),

        # Indiana Jones
        (r'^INDIANA\s*JONES', 'Indiana Jones'),

        # Top Gun
        (r'^TOP\s*GUN\s*MAVERICK', 'Top Gun Maverick'),
        (r'^TOP\s*GUN\s*2', 'Top Gun Maverick'),

        # Ant-Man
        (r'^ANT\s*MAN', 'Ant-Man'),

        # Captain America
        (r'^CAPTAIN\s*AMERICA', 'Captain America'),

        # Iron Man
        (r'^IRON\s*MAN', 'Iron Man'),

        # Thor
        (r'^THOR', 'Thor'),

        # Avengers
        (r'^AVENGERS', 'Avengers'),
    ]

    def __init__(self, config: dict):
        self.config = config
        self.runtime_tolerance = config.get('identification', {}).get('runtime_tolerance', 300)
        self.confidence_threshold = config.get('identification', {}).get('confidence_threshold', 75)

        # Radarr config
        radarr_cfg = config.get('integrations', {}).get('radarr', {})
        self.radarr_url = radarr_cfg.get('url', 'http://localhost:7878')
        self.radarr_api = radarr_cfg.get('api_key', '')

        # Sonarr config
        sonarr_cfg = config.get('integrations', {}).get('sonarr', {})
        self.sonarr_url = sonarr_cfg.get('url', 'http://localhost:8989')
        self.sonarr_api = sonarr_cfg.get('api_key', '')

    def parse_disc_label(self, label: str) -> str:
        """Parse disc label into searchable title"""
        parsed = label.upper()

        # Remove studio prefixes
        for prefix in self.STUDIO_PREFIXES:
            parsed = re.sub(f'^{prefix}', '', parsed, flags=re.IGNORECASE)

        # Remove disc number suffixes
        parsed = re.sub(r'_?(DISC_?\d*|D\d+)$', '', parsed, flags=re.IGNORECASE)

        # Remove region codes and common suffixes
        parsed = re.sub(r'_?(PS|US|UK|EU|AU|CA|JP|KR|FR|DE|ES|IT|NL|BR|MX|R1|R2|R3|R4|REGION_?\d)$', '', parsed, flags=re.IGNORECASE)

        # Replace underscores with spaces
        parsed = parsed.replace('_', ' ')

        # Apply franchise-specific patterns
        for pattern, replacement in self.FRANCHISE_PATTERNS:
            match = re.match(pattern, parsed, re.IGNORECASE)
            if match:
                # Handle backreferences in replacement
                if '\\' in replacement:
                    parsed = re.sub(pattern, replacement, parsed, flags=re.IGNORECASE)
                else:
                    parsed = replacement
                break

        # Clean up spaces
        parsed = re.sub(r'\s+', ' ', parsed).strip()

        # Title case if all caps
        if parsed.isupper():
            parsed = parsed.title()

        # Clean up "Vol" without number
        parsed = re.sub(r'\s+Vol\s*$', '', parsed)

        return parsed

    def get_video_runtime(self, folder: str) -> Optional[int]:
        """Get runtime of video file in seconds using ffprobe"""
        # Find video file
        video_file = None
        for ext in ['mkv', 'mp4', 'avi', 'm4v']:
            files = list(Path(folder).glob(f'*.{ext}'))
            if files:
                video_file = files[0]
                break

        if not video_file:
            return None

        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', str(video_file)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return int(float(result.stdout.strip()))
        except Exception as e:
            print(f"Error getting runtime: {e}")

        return None

    def search_radarr(self, title: str, runtime_seconds: Optional[int] = None) -> Optional[IdentificationResult]:
        """Search Radarr for movie match"""
        if not self.radarr_api:
            return None

        try:
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie/lookup",
                params={'term': title},
                headers={'X-Api-Key': self.radarr_api},
                timeout=10
            )

            if response.status_code != 200:
                return None

            results = response.json()
            if not results:
                return None

            best_match = None
            best_score = 0

            for movie in results[:10]:
                score = 0
                movie_runtime = movie.get('runtime', 0) * 60  # Radarr returns minutes

                # Runtime match scoring
                if runtime_seconds and movie_runtime > 0:
                    diff = abs(runtime_seconds - movie_runtime)
                    if diff <= self.runtime_tolerance:
                        score += 100 - (diff / self.runtime_tolerance * 50)
                    elif diff <= self.runtime_tolerance * 2:
                        score += 25

                # Popularity bonus
                popularity = movie.get('popularity', 0)
                score += min(popularity / 10, 20)

                # Year recency bonus
                year = movie.get('year', 2000)
                if year >= 2020:
                    score += 10
                elif year >= 2015:
                    score += 5

                if score > best_score:
                    best_score = score
                    best_match = movie

            if best_match and best_score >= 50:
                return IdentificationResult(
                    title=best_match.get('title', ''),
                    year=best_match.get('year', 0),
                    tmdb_id=best_match.get('tmdbId', 0),
                    runtime_minutes=best_match.get('runtime', 0),
                    confidence=int(best_score),
                    media_type='movie'
                )

        except Exception as e:
            print(f"Error searching Radarr: {e}")

        return None

    def search_sonarr(self, title: str) -> Optional[IdentificationResult]:
        """Search Sonarr for TV show match"""
        if not self.sonarr_api:
            return None

        try:
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series/lookup",
                params={'term': title},
                headers={'X-Api-Key': self.sonarr_api},
                timeout=10
            )

            if response.status_code != 200:
                return None

            results = response.json()
            if not results:
                return None

            # For TV, just return the top match with reasonable confidence
            show = results[0]
            return IdentificationResult(
                title=show.get('title', ''),
                year=show.get('year', 0),
                tmdb_id=show.get('tvdbId', 0),
                confidence=70,  # TV matching is less precise
                media_type='tv'
            )

        except Exception as e:
            print(f"Error searching Sonarr: {e}")

        return None

    def check_overseerr_wanted(self, title: str) -> Optional[IdentificationResult]:
        """Check if title is on Overseerr wanted list for better matching"""
        # TODO: Implement Overseerr wanted list checking
        # This could give us high confidence if someone requested this exact title
        pass

    def identify(self, folder: str) -> Optional[IdentificationResult]:
        """
        Main identification method.
        Takes a folder path and returns identification result.
        """
        folder_path = Path(folder)
        folder_name = folder_path.name

        # Extract disc label from folder name
        disc_label = re.sub(r'\s*\(\d{4}\)(_\d+)?$', '', folder_name)
        disc_label = disc_label.replace('-', '_').upper()

        # Parse into search term
        search_term = self.parse_disc_label(disc_label)

        # Get video runtime
        runtime = self.get_video_runtime(folder)

        # Search Radarr (movies)
        result = self.search_radarr(search_term, runtime)

        if result and result.confidence >= 50:
            return result

        # Try Sonarr if movie search failed
        result = self.search_sonarr(search_term)

        return result

    def identify_and_rename(self, folder: str) -> Tuple[Optional[IdentificationResult], str]:
        """
        Identify content and rename folder/files if confident.
        Returns tuple of (result, new_path).
        """
        result = self.identify(folder)

        if not result:
            return None, folder

        folder_path = Path(folder)
        new_name = result.folder_name
        new_path = folder_path.parent / new_name

        # Rename video files inside folder
        for video_file in folder_path.glob('*'):
            if video_file.suffix.lower() in ['.mkv', '.mp4', '.avi', '.m4v']:
                new_file = folder_path / f"{new_name}{video_file.suffix}"
                if video_file != new_file:
                    video_file.rename(new_file)

        # Rename folder
        if str(folder_path) != str(new_path) and not new_path.exists():
            folder_path.rename(new_path)
            return result, str(new_path)

        return result, folder
