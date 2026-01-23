"""
RipForge Smart Identification
Identifies ripped content using disc label parsing + runtime matching with Radarr/Sonarr
"""

import re
import os
import subprocess
import time
import requests
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass

from . import activity
from . import community_db


@dataclass
class IdentificationResult:
    """Result of content identification"""
    title: str = ""
    year: int = 0
    tmdb_id: int = 0
    imdb_id: str = ""
    tvdb_id: int = 0
    runtime_minutes: int = 0
    confidence: int = 0
    media_type: str = "movie"  # movie or tv
    radarr_id: Optional[int] = None
    sonarr_id: Optional[int] = None
    poster_url: str = ""
    # TV-specific fields
    season_number: int = 0
    episode_mapping: Dict[int, dict] = None  # track_idx -> episode info

    def __post_init__(self):
        if self.episode_mapping is None:
            self.episode_mapping = {}

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 75

    @property
    def folder_name(self) -> str:
        """Generate filesystem-safe folder name"""
        name = f"{self.title} ({self.year})"
        # Sanitize: colon -> space-dash, remove other invalid characters
        name = name.replace(':', ' -')
        name = re.sub(r'[<>"|?*]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    @property
    def poster_thumbnail(self) -> str:
        """Get smaller poster for emails (w200)"""
        if self.poster_url:
            return self.poster_url.replace('/w500/', '/w200/')
        return ""


class SmartIdentifier:
    """Smart content identification using label parsing + runtime matching"""

    # Studio prefixes to strip
    STUDIO_PREFIXES = [
        'MARVEL_STUDIOS_?', 'DISNEY_?', 'PIXAR_?', 'WARNER_?', 'WARNER_BROS_?', 'WB_?',
        'UNIVERSAL_?', 'SONY_?', 'COLUMBIA_?', 'PARAMOUNT_?', '20TH_CENTURY_?', 'FOX_?',
        'LIONSGATE_?', 'MGM_?', 'DREAMWORKS_?', 'NEW_LINE_?', 'HBO_?', 'A24_?',
        'BLU-?RAY_?', 'DVD_?', 'BD_?', 'UHD_?', '4K_?'
    ]

    # Studio/format suffixes to strip (at end of label)
    STRIP_SUFFIXES = [
        # Studio codes
        'SCE', 'SPE', 'WB', 'FOX', 'UNI', 'PAR', 'DIS', 'LGF',
        # Region/market codes
        'DOM', 'DOMESTIC', 'INTL', 'INT', 'WW',
        # Format codes
        'WS', 'WIDESCREEN', 'FS', 'FULLSCREEN', '4X3', '16X9', 'NTSC', 'PAL',
        # Edition codes
        'SE', 'CE', 'DE', 'UE', 'DC', 'TC', 'EXT', 'EXTENDED', 'UNRATED', 'RATED', 'REMASTERED',
        # Audio codes
        'THX', 'DTS', 'DOLBY', 'ATMOS',
    ]

    # Common abbreviations to expand
    ABBREVIATIONS = {
        'SAT': 'SATURDAY',
        'SUN': 'SUNDAY',
        'MON': 'MONDAY',
        'TUE': 'TUESDAY',
        'TUES': 'TUESDAY',
        'WED': 'WEDNESDAY',
        'THU': 'THURSDAY',
        'THUR': 'THURSDAY',
        'THURS': 'THURSDAY',
        'FRI': 'FRIDAY',
        'NITE': 'NIGHT',
        'NIT': 'NIGHT',
        'ST': 'STREET',
        'MT': 'MOUNT',
        'VS': 'VERSUS',
        'MR': 'MISTER',
        'MRS': 'MISSES',
        'DR': 'DOCTOR',
        'JR': 'JUNIOR',
        'SR': 'SENIOR',
        'XMAS': 'CHRISTMAS',
        'BDAY': 'BIRTHDAY',
    }

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

    # TV show detection patterns for disc labels
    TV_PATTERNS = [
        (r'[_\s]S(\d{1,2})(?:[_\s]|$)', 'season'),           # S01, S1, S02
        (r'[_\s]SEASON[_\s]*(\d{1,2})', 'season'),           # SEASON_1, SEASON 2, SEASON1
        (r'COMPLETE[_\s]*SERIES', 'complete_series'),        # COMPLETE_SERIES
        (r'COMPLETE[_\s]*(?:S|SEASON)', 'complete_season'),  # COMPLETE_SEASON, COMPLETE_S1
        (r'[_\s](?:DISC|D)[_\s]*(\d+)[_\s]*OF[_\s]*(\d+)', 'multi_disc'),  # DISC_1_OF_4
    ]

    def __init__(self, config: dict):
        self.config = config
        self.runtime_tolerance = config.get('identification', {}).get('runtime_tolerance', 300)
        self.confidence_threshold = config.get('identification', {}).get('confidence_threshold', 75)

        # TV episode detection thresholds (from config or defaults)
        ripping_cfg = config.get('ripping', {})
        self.tv_min_episode_length = ripping_cfg.get('tv_min_episode_length', 1200)  # 20 min
        self.tv_max_episode_length = ripping_cfg.get('tv_max_episode_length', 3600)  # 60 min
        self.tv_episode_tolerance = ripping_cfg.get('tv_episode_tolerance', 60)  # seconds

        # Radarr config
        radarr_cfg = config.get('integrations', {}).get('radarr', {})
        self.radarr_url = radarr_cfg.get('url', 'http://localhost:7878')
        self.radarr_api = radarr_cfg.get('api_key', '')

        # Sonarr config
        sonarr_cfg = config.get('integrations', {}).get('sonarr', {})
        self.sonarr_url = sonarr_cfg.get('url', 'http://localhost:8989')
        self.sonarr_api = sonarr_cfg.get('api_key', '')

    def parse_disc_label(self, label: str, verbose: bool = True) -> str:
        """Parse disc label into searchable title"""
        original = label
        parsed = label.upper()
        transformations = []

        # Remove studio prefixes
        for prefix in self.STUDIO_PREFIXES:
            new_parsed = re.sub(f'^{prefix}', '', parsed, flags=re.IGNORECASE)
            if new_parsed != parsed:
                transformations.append(f"Stripped prefix: {prefix.replace('_?', '')}")
                parsed = new_parsed

        # Remove disc number suffixes
        new_parsed = re.sub(r'_?(DISC_?\d*|D\d+)$', '', parsed, flags=re.IGNORECASE)
        if new_parsed != parsed:
            transformations.append("Stripped disc number suffix")
            parsed = new_parsed

        # Remove region codes and common suffixes
        new_parsed = re.sub(r'_?(PS|US|UK|EU|AU|CA|JP|KR|FR|DE|ES|IT|NL|BR|MX|AC|R1|R2|R3|R4|REGION_?\d)$', '', parsed, flags=re.IGNORECASE)
        if new_parsed != parsed:
            transformations.append("Stripped region code")
            parsed = new_parsed

        # Remove studio/format suffixes (can appear multiple times)
        for _ in range(3):  # Multiple passes to catch stacked suffixes
            for suffix in self.STRIP_SUFFIXES:
                new_parsed = re.sub(rf'[_\s]+{suffix}$', '', parsed, flags=re.IGNORECASE)
                if new_parsed != parsed:
                    transformations.append(f"Stripped suffix: {suffix}")
                    parsed = new_parsed

        # Replace underscores with spaces
        parsed = parsed.replace('_', ' ')

        # Expand abbreviations (word boundaries)
        words = parsed.split()
        expanded_words = []
        for word in words:
            upper_word = word.upper()
            if upper_word in self.ABBREVIATIONS:
                expanded_words.append(self.ABBREVIATIONS[upper_word])
                transformations.append(f"Expanded: {word} -> {self.ABBREVIATIONS[upper_word]}")
            else:
                expanded_words.append(word)
        parsed = ' '.join(expanded_words)

        # Apply franchise-specific patterns
        franchise_matched = None
        for pattern, replacement in self.FRANCHISE_PATTERNS:
            match = re.match(pattern, parsed, re.IGNORECASE)
            if match:
                old_parsed = parsed
                # Handle backreferences in replacement
                if '\\' in replacement:
                    parsed = re.sub(pattern, replacement, parsed, flags=re.IGNORECASE)
                else:
                    parsed = replacement
                franchise_matched = f"{old_parsed} -> {parsed}"
                break

        # Clean up spaces
        parsed = re.sub(r'\s+', ' ', parsed).strip()

        # Title case if all caps
        if parsed.isupper():
            parsed = parsed.title()

        # Clean up "Vol" without number
        parsed = re.sub(r'\s+Vol\s*$', '', parsed)

        # Log the parsing details
        if verbose:
            activity.log_info(f"PARSE: '{original}' -> '{parsed}'")
            if transformations:
                activity.log_info(f"PARSE: Transformations: {', '.join(transformations)}")
            else:
                activity.log_info(f"PARSE: No patterns matched (used as-is)")
            if franchise_matched:
                activity.log_info(f"PARSE: Franchise pattern: {franchise_matched}")

        return parsed

    def detect_media_type(self, label: str, tracks: List[dict] = None) -> Tuple[str, int, str]:
        """
        Detect if disc is a TV show based on label patterns and track analysis.

        Args:
            label: Disc label string
            tracks: List of track dicts with 'duration' in seconds

        Returns:
            Tuple of (media_type, season_number, cleaned_title)
            - media_type: 'movie' or 'tv'
            - season_number: Extracted season number (0 if unknown)
            - cleaned_title: Title with season info removed for searching
        """
        upper_label = label.upper()
        season_number = 0
        is_tv = False
        cleaned_title = label

        # Check disc label for TV patterns
        for pattern, pattern_type in self.TV_PATTERNS:
            match = re.search(pattern, upper_label)
            if match:
                is_tv = True
                if pattern_type == 'season' and match.groups():
                    season_number = int(match.group(1))
                    # Remove the season indicator from title for cleaner search
                    cleaned_title = re.sub(pattern, '', upper_label, flags=re.IGNORECASE).strip('_').strip()
                elif pattern_type == 'complete_series':
                    season_number = 0  # All seasons
                    cleaned_title = re.sub(pattern, '', upper_label, flags=re.IGNORECASE).strip('_').strip()
                elif pattern_type == 'complete_season':
                    # Try to extract season number if present
                    season_match = re.search(r'(\d+)', match.group(0))
                    if season_match:
                        season_number = int(season_match.group(1))
                    cleaned_title = re.sub(pattern, '', upper_label, flags=re.IGNORECASE).strip('_').strip()
                break

        # Additional heuristic: multiple episode-length tracks suggest TV
        if not is_tv and tracks:
            episode_length_tracks = [
                t for t in tracks
                if self.tv_min_episode_length <= t.get('duration', 0) <= self.tv_max_episode_length
            ]
            # If 3+ tracks in episode range and no long (movie-length) track, likely TV
            if len(episode_length_tracks) >= 3:
                has_movie_length = any(t.get('duration', 0) > self.tv_max_episode_length * 1.5 for t in tracks)
                if not has_movie_length:
                    is_tv = True
                    activity.log_info(f"DETECT: Found {len(episode_length_tracks)} episode-length tracks, classifying as TV")

        media_type = 'tv' if is_tv else 'movie'

        if is_tv:
            activity.log_info(f"DETECT: '{label}' -> TV (season {season_number if season_number else 'unknown'})")
            activity.log_info(f"DETECT: Cleaned title for search: '{cleaned_title}'")
        else:
            activity.log_info(f"DETECT: '{label}' -> Movie")

        return media_type, season_number, cleaned_title

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

    def search_radarr(self, title: str, runtime_seconds: Optional[int] = None, verbose: bool = True) -> Optional[IdentificationResult]:
        """Search Radarr for movie match"""
        if not self.radarr_api:
            if verbose:
                activity.log_warning("RADARR: No API key configured")
            return None

        runtime_str = f"{runtime_seconds // 60}m {runtime_seconds % 60}s" if runtime_seconds else "unknown"
        if verbose:
            activity.log_info(f"RADARR: Searching for '{title}' (runtime: {runtime_str})")

        # Build list of search terms to try
        search_terms = [title]

        # For titles ending with a number (sequels like "Under Siege 2"), try with "movie" appended
        # This helps filter out TV shows/wrestling with similar names
        if re.search(r'\s+\d+$', title):
            search_terms.append(f"{title} movie")
            # Also try base title without the number to find franchise
            base_title = re.sub(r'\s+\d+$', '', title)
            search_terms.append(base_title)

        all_results = []
        seen_tmdb_ids = set()

        for search_term in search_terms:
            results = self._search_radarr_single(search_term, verbose=(verbose and search_term == title))
            if results:
                for movie in results:
                    tmdb_id = movie.get('tmdbId', 0)
                    if tmdb_id not in seen_tmdb_ids:
                        seen_tmdb_ids.add(tmdb_id)
                        all_results.append(movie)

        if not all_results:
            if verbose:
                activity.log_info(f"RADARR: No results found for any search term")
            return None

        if verbose:
            activity.log_info(f"RADARR: Found {len(all_results)} unique result(s) from {len(search_terms)} search(es)")

        return self._score_radarr_results(title, all_results, runtime_seconds, verbose)

    def _search_radarr_single(self, term: str, verbose: bool = False) -> Optional[List[dict]]:
        """Execute a single Radarr search"""
        max_retries = 3
        response = None
        last_error = None

        for attempt in range(max_retries):
            try:
                response = requests.get(
                    f"{self.radarr_url}/api/v3/movie/lookup",
                    params={'term': term},
                    headers={'X-Api-Key': self.radarr_api},
                    timeout=10
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue
            except Exception as e:
                return None

        if response is None:
            return None

        try:
            if response.status_code != 200:
                return None
            results = response.json()
            return results if results else None
        except Exception:
            return None

    def _score_radarr_results(self, title: str, results: List[dict], runtime_seconds: Optional[int], verbose: bool = True) -> Optional[IdentificationResult]:
        """Score and select best match from Radarr results"""
        if not results:
            return None

        best_match = None
        best_score = 0
        candidates = []

        # Normalize search title for comparison
        search_title_lower = title.lower().strip()
        # Also extract any number suffix for sequel matching (e.g., "2" from "Under Siege 2")
        sequel_num_match = re.search(r'\s+(\d+)$', title)
        sequel_num = sequel_num_match.group(1) if sequel_num_match else None

        for movie in results[:15]:  # Check more results for sequels
            score = 0
            score_breakdown = []
            movie_runtime = movie.get('runtime', 0) * 60
            movie_title = movie.get('title', 'Unknown')
            movie_year = movie.get('year', 0)
            movie_title_lower = movie_title.lower().strip()

            # Title match scoring
            if movie_title_lower == search_title_lower:
                score += 50
                score_breakdown.append("title exact +50")
            elif (movie_title_lower.startswith(search_title_lower + " ") or
                  movie_title_lower.startswith(search_title_lower + ":")):
                len_ratio = len(search_title_lower) / len(movie_title_lower)
                if len_ratio > 0.7:
                    score += 25
                    score_breakdown.append("title prefix +25")
                else:
                    score += 10
                    score_breakdown.append(f"title prefix +10 (ratio {len_ratio:.0%})")
            elif search_title_lower in movie_title_lower:
                score += 5
                score_breakdown.append("title contains +5")

            # Sequel number matching - boost if movie title contains the sequel number
            # e.g., "Under Siege 2" should match "Under Siege 2: Dark Territory"
            if sequel_num:
                base_title = re.sub(r'\s+\d+$', '', search_title_lower)
                if movie_title_lower.startswith(base_title) and sequel_num in movie_title_lower:
                    score += 40
                    score_breakdown.append(f"sequel #{sequel_num} match +40")

            # Runtime match scoring - use PERCENTAGE difference, not absolute
            # A 3min track claiming to be a 4min movie is 25% off - terrible match
            # A 115min track claiming to be a 120min movie is 4% off - good match
            if runtime_seconds and movie_runtime > 0:
                # CRITICAL: Tracks under 10 minutes cannot be main features
                if runtime_seconds < 600:  # < 10 minutes
                    score -= 100  # Heavy penalty - this is NOT a movie
                    score_breakdown.append(f"runtime -100 (track only {runtime_seconds // 60}m, too short for movie)")
                else:
                    # Use percentage-based scoring
                    diff = abs(runtime_seconds - movie_runtime)
                    pct_diff = diff / max(runtime_seconds, movie_runtime) * 100
                    
                    if pct_diff <= 5:  # Within 5% = excellent match
                        runtime_score = 100
                        score += runtime_score
                        score_breakdown.append(f"runtime +{runtime_score:.0f} (diff {pct_diff:.1f}%)")
                    elif pct_diff <= 10:  # Within 10% = good match
                        runtime_score = 75
                        score += runtime_score
                        score_breakdown.append(f"runtime +{runtime_score:.0f} (diff {pct_diff:.1f}%)")
                    elif pct_diff <= 20:  # Within 20% = okay match
                        runtime_score = 40
                        score += runtime_score
                        score_breakdown.append(f"runtime +{runtime_score:.0f} (diff {pct_diff:.1f}%, partial)")
                    else:  # Over 20% = bad match
                        score_breakdown.append(f"runtime +0 (diff {pct_diff:.1f}%, too far)")
            else:
                score_breakdown.append("runtime N/A")

            # Popularity bonus
            popularity = movie.get('popularity', 0)
            pop_score = min(popularity / 10, 20)
            score += pop_score
            if pop_score > 0:
                score_breakdown.append(f"popularity +{pop_score:.0f}")

            # Year recency bonus
            if movie_year >= 2020:
                score += 10
                score_breakdown.append("recent +10")
            elif movie_year >= 2015:
                score += 5
                score_breakdown.append("recent +5")

            candidates.append({
                'title': movie_title,
                'year': movie_year,
                'runtime': movie_runtime // 60,
                'score': score,
                'breakdown': score_breakdown,
                'movie': movie
            })

            if score > best_score:
                best_score = score
                best_match = movie

        # Log top 3 candidates
        if verbose and candidates:
            candidates.sort(key=lambda x: x['score'], reverse=True)
            activity.log_info(f"RADARR: Top candidates:")
            for i, c in enumerate(candidates[:3]):
                breakdown = ', '.join(c['breakdown'])
                activity.log_info(f"RADARR:   {i+1}. {c['title']} ({c['year']}) [{c['runtime']}m] = {c['score']:.0f} pts ({breakdown})")

        if best_match and best_score >= 50:
            # Get poster URL from images array or remotePoster
            poster_url = ""
            images = best_match.get('images', [])
            for img in images:
                if img.get('coverType') == 'poster':
                    poster_url = img.get('remoteUrl', '')
                    break
            if not poster_url:
                poster_url = best_match.get('remotePoster', '')
            # Fallback to TMDB direct URL
            if not poster_url and best_match.get('tmdbId'):
                tmdb_id = best_match.get('tmdbId')
                poster_url = f"https://image.tmdb.org/t/p/w500/{tmdb_id}"

            result = IdentificationResult(
                title=best_match.get('title', ''),
                year=best_match.get('year', 0),
                tmdb_id=best_match.get('tmdbId', 0),
                imdb_id=best_match.get('imdbId', ''),
                runtime_minutes=best_match.get('runtime', 0),
                confidence=int(best_score),
                media_type='movie',
                poster_url=poster_url
            )
            if verbose:
                activity.log_success(f"RADARR: Selected '{result.title}' ({result.year}) with {int(best_score)} pts")
            return result
        else:
            if verbose:
                if best_match:
                    activity.log_warning(f"RADARR: Best match score {best_score:.0f} < 50, rejected")
                else:
                    activity.log_warning(f"RADARR: No suitable match found")
            return None

    def search_radarr_multi(self, title: str, runtime_seconds: Optional[int] = None, limit: int = 5) -> List[dict]:
        """Search Radarr and return multiple results for user selection"""
        if not self.radarr_api:
            return []

        # Build list of search terms to try
        search_terms = [title]
        if re.search(r'\s+\d+$', title):
            search_terms.append(f"{title} movie")
            base_title = re.sub(r'\s+\d+$', '', title)
            search_terms.append(base_title)

        all_results = []
        seen_tmdb_ids = set()

        for search_term in search_terms:
            results = self._search_radarr_single(search_term, verbose=False)
            if results:
                for movie in results:
                    tmdb_id = movie.get('tmdbId', 0)
                    if tmdb_id not in seen_tmdb_ids:
                        seen_tmdb_ids.add(tmdb_id)
                        all_results.append(movie)

        if not all_results:
            return []

        # Score all results
        candidates = []
        search_title_lower = title.lower().strip()

        for movie in all_results[:20]:
            score = 0
            movie_runtime = movie.get('runtime', 0) * 60
            movie_title = movie.get('title', 'Unknown')
            movie_year = movie.get('year', 0)
            movie_title_lower = movie_title.lower().strip()

            # Title scoring
            if movie_title_lower == search_title_lower:
                score += 50
            elif search_title_lower in movie_title_lower:
                score += 25
            elif movie_title_lower in search_title_lower:
                score += 15

            # Runtime scoring
            if runtime_seconds and movie_runtime > 0 and runtime_seconds >= 600:
                diff = abs(runtime_seconds - movie_runtime)
                pct_diff = diff / max(runtime_seconds, movie_runtime) * 100
                if pct_diff <= 10:
                    score += 50
                elif pct_diff <= 20:
                    score += 25

            # Popularity bonus
            score += min(movie.get('popularity', 0) / 10, 20)

            # Get poster URL
            poster_url = ""
            images = movie.get('images', [])
            for img in images:
                if img.get('coverType') == 'poster':
                    poster_url = img.get('remoteUrl', '')
                    break
            if not poster_url:
                poster_url = movie.get('remotePoster', '')

            candidates.append({
                'title': movie_title,
                'year': movie_year,
                'tmdb_id': movie.get('tmdbId', 0),
                'imdb_id': movie.get('imdbId', ''),
                'runtime_minutes': movie.get('runtime', 0),
                'poster_url': poster_url,
                'score': score,
                'folder_name': f"{movie_title} ({movie_year})" if movie_year else movie_title,
                'media_type': 'movie'
            })

        # Sort by score and return top N
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates[:limit]

    def search_radarr_by_runtime(self, runtime_seconds: int, verbose: bool = True) -> Optional[IdentificationResult]:
        """Search Radarr library by runtime only - fallback for generic disc labels"""
        if not self.radarr_api or not runtime_seconds:
            return None

        try:
            runtime_str = f"{runtime_seconds // 60}m"
            if verbose:
                activity.log_info(f"RADARR: Runtime-only search ({runtime_str})")

            # Get all movies in library
            response = requests.get(
                f"{self.radarr_url}/api/v3/movie",
                headers={'X-Api-Key': self.radarr_api},
                timeout=15
            )

            if response.status_code != 200:
                return None

            movies = response.json()
            if not movies:
                return None

            # Find movies with matching runtime
            matches = []
            for movie in movies:
                movie_runtime = movie.get('runtime', 0) * 60  # Radarr returns minutes
                if movie_runtime <= 0:
                    continue

                diff = abs(runtime_seconds - movie_runtime)
                if diff <= self.runtime_tolerance:
                    score = 100 - (diff / self.runtime_tolerance * 50)
                    matches.append((movie, score, diff))

            if not matches:
                if verbose:
                    activity.log_info(f"RADARR: No runtime matches in library")
                return None

            # Sort by score (best match first)
            matches.sort(key=lambda x: x[1], reverse=True)

            if len(matches) == 1:
                # Single match - high confidence
                movie, score, diff = matches[0]
                if verbose:
                    activity.log_success(f"RADARR: Runtime match: '{movie['title']}' ({movie.get('year', 0)}) - diff {diff // 60}m")

                return IdentificationResult(
                    title=movie['title'],
                    year=movie.get('year', 0),
                    tmdb_id=movie.get('tmdbId', 0),
                    runtime_minutes=movie.get('runtime', 0),
                    confidence=min(85, int(score)),  # Cap at 85% for runtime-only match
                    folder_name=f"{movie['title']} ({movie.get('year', 0)})",
                    poster_url=next((img['remoteUrl'] for img in movie.get('images', []) if img.get('coverType') == 'poster'), ''),
                    media_type='movie'
                )
            else:
                # Multiple matches - lower confidence, log options
                if verbose:
                    activity.log_warning(f"RADARR: {len(matches)} runtime matches - review needed")
                    for movie, score, diff in matches[:3]:
                        activity.log_info(f"  - '{movie['title']}' ({movie.get('year', 0)}) diff {diff // 60}m")
                return None

        except Exception as e:
            if verbose:
                activity.log_error(f"RADARR: Runtime search error: {e}")

        return None

    def search_sonarr(self, title: str, episode_runtimes: List[int] = None,
                      season_number: int = 0, verbose: bool = True) -> Optional[IdentificationResult]:
        """Search Sonarr for TV show match with multi-factor scoring.

        Args:
            title: Show title to search for
            episode_runtimes: List of episode durations in seconds (for runtime matching)
            season_number: Season number if known (for episode lookup)
            verbose: Whether to log details

        Returns:
            IdentificationResult with show info and episode mapping if found
        """
        if not self.sonarr_api:
            if verbose:
                activity.log_warning("SONARR: No API key configured")
            return None

        if verbose:
            activity.log_info(f"SONARR: Searching for '{title}'")
            if episode_runtimes:
                avg_runtime = sum(episode_runtimes) / len(episode_runtimes) / 60
                activity.log_info(f"SONARR: {len(episode_runtimes)} episode tracks (avg {avg_runtime:.0f}m)")

        # Retry logic
        max_retries = 3
        response = None
        last_error = None

        for attempt in range(max_retries):
            try:
                response = requests.get(
                    f"{self.sonarr_url}/api/v3/series/lookup",
                    params={'term': title},
                    headers={'X-Api-Key': self.sonarr_api},
                    timeout=10
                )
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    if verbose:
                        activity.log_warning(f"SONARR: Attempt {attempt + 1}/{max_retries} failed, retrying...")
                    time.sleep(1)
                continue
            except Exception as e:
                if verbose:
                    activity.log_error(f"SONARR: Search error: {e}")
                return None

        if response is None:
            if verbose:
                activity.log_error(f"SONARR: All {max_retries} attempts failed: {last_error}")
            return None

        try:
            if response.status_code != 200:
                if verbose:
                    activity.log_warning(f"SONARR: API returned status {response.status_code}")
                return None

            results = response.json()
            if not results:
                if verbose:
                    activity.log_info(f"SONARR: No results found for '{title}'")
                return None

            if verbose:
                activity.log_info(f"SONARR: Found {len(results)} result(s)")

            best_match = None
            best_score = 0
            candidates = []

            for show in results[:10]:
                score = 0
                score_breakdown = []
                show_title = show.get('title', 'Unknown')
                show_year = show.get('year', 0)
                show_runtime = show.get('runtime', 0)  # Average episode runtime in minutes

                # Runtime match scoring (if we have episode runtimes)
                if episode_runtimes and show_runtime > 0:
                    avg_track_runtime = sum(episode_runtimes) / len(episode_runtimes) / 60  # minutes
                    diff = abs(avg_track_runtime - show_runtime)
                    if diff <= 5:  # Within 5 minutes
                        runtime_score = 50 - (diff * 5)
                        score += runtime_score
                        score_breakdown.append(f"runtime +{runtime_score:.0f} (diff {diff:.0f}m)")
                    elif diff <= 15:
                        score += 20
                        score_breakdown.append(f"runtime +20 (diff {diff:.0f}m, partial)")
                    else:
                        score_breakdown.append(f"runtime +0 (diff {diff:.0f}m, too far)")
                else:
                    score_breakdown.append("runtime N/A")

                # Popularity/ratings bonus
                ratings = show.get('ratings', {})
                if ratings.get('votes', 0) > 1000:
                    score += 20
                    score_breakdown.append("popular +20")
                elif ratings.get('votes', 0) > 100:
                    score += 10
                    score_breakdown.append("popular +10")

                # Year recency bonus
                if show_year >= 2020:
                    score += 15
                    score_breakdown.append("recent +15")
                elif show_year >= 2010:
                    score += 10
                    score_breakdown.append("recent +10")
                elif show_year >= 2000:
                    score += 5
                    score_breakdown.append("recent +5")

                # Title match bonus - exact match gets boost
                if show_title.upper() == title.upper():
                    score += 20
                    score_breakdown.append("exact title +20")

                candidates.append({
                    'title': show_title,
                    'year': show_year,
                    'runtime': show_runtime,
                    'score': score,
                    'breakdown': score_breakdown,
                    'tvdb_id': show.get('tvdbId', 0)
                })

                if score > best_score:
                    best_score = score
                    best_match = show

            # Log top candidates
            if verbose and candidates:
                candidates.sort(key=lambda x: x['score'], reverse=True)
                activity.log_info(f"SONARR: Top candidates:")
                for i, c in enumerate(candidates[:3]):
                    breakdown = ', '.join(c['breakdown'])
                    activity.log_info(f"SONARR:   {i+1}. {c['title']} ({c['year']}) [{c['runtime']}m/ep] = {c['score']:.0f} pts ({breakdown})")

            if best_match and best_score >= 30:
                # Get poster URL
                poster_url = ""
                images = best_match.get('images', [])
                for img in images:
                    if img.get('coverType') == 'poster':
                        poster_url = img.get('remoteUrl', '')
                        break

                # Get episode mapping if we have episode runtimes and season
                episode_mapping = {}
                if episode_runtimes and season_number > 0:
                    episode_mapping = self.match_episodes_to_tracks(
                        best_match.get('tvdbId', 0),
                        season_number,
                        episode_runtimes
                    )

                result = IdentificationResult(
                    title=best_match.get('title', ''),
                    year=best_match.get('year', 0),
                    tmdb_id=best_match.get('tvdbId', 0),  # Store TVDB ID here
                    runtime_minutes=best_match.get('runtime', 0),
                    confidence=int(best_score),
                    media_type='tv',
                    sonarr_id=best_match.get('id'),
                    poster_url=poster_url,
                    season_number=season_number,
                    episode_mapping=episode_mapping
                )

                if verbose:
                    activity.log_success(f"SONARR: Selected '{result.title}' ({result.year}) with {best_score:.0f} pts")
                return result
            else:
                if verbose:
                    if best_match:
                        activity.log_warning(f"SONARR: Best match score {best_score:.0f} < 30, rejected")
                    else:
                        activity.log_warning(f"SONARR: No suitable match found")
                return None

        except Exception as e:
            if verbose:
                activity.log_error(f"SONARR: Search error: {e}")
            return None

    def get_sonarr_episodes(self, series_id: int, season: int) -> List[dict]:
        """Fetch episode list for a series/season from Sonarr.

        Args:
            series_id: TVDB series ID
            season: Season number

        Returns:
            List of episode dicts with runtime and title info
        """
        if not self.sonarr_api:
            return []

        try:
            # First need to check if series is in Sonarr library
            response = requests.get(
                f"{self.sonarr_url}/api/v3/series/lookup",
                params={'term': f"tvdb:{series_id}"},
                headers={'X-Api-Key': self.sonarr_api},
                timeout=10
            )

            if response.status_code != 200:
                activity.log_warning(f"SONARR: Could not fetch series {series_id}")
                return []

            series_data = response.json()
            if not series_data:
                return []

            # Extract episodes from series data for the specified season
            series = series_data[0] if isinstance(series_data, list) else series_data
            seasons = series.get('seasons', [])

            for s in seasons:
                if s.get('seasonNumber') == season:
                    # Get episode count - actual episode info requires series to be in library
                    episode_count = s.get('statistics', {}).get('totalEpisodeCount', 0)
                    activity.log_info(f"SONARR: Season {season} has {episode_count} episodes")

                    # Build episode list with standard runtimes
                    runtime = series.get('runtime', 45)  # Default episode runtime
                    episodes = []
                    for ep_num in range(1, episode_count + 1):
                        episodes.append({
                            'episode_number': ep_num,
                            'season_number': season,
                            'runtime': runtime * 60,  # Convert to seconds
                            'title': f"Episode {ep_num}"  # Placeholder - real title requires series in library
                        })
                    return episodes

            return []

        except Exception as e:
            activity.log_error(f"SONARR: Error fetching episodes: {e}")
            return []

    def match_episodes_to_tracks(self, series_id: int, season: int,
                                  track_runtimes: List[int]) -> Dict[int, dict]:
        """Match disc tracks to episodes based on runtime.

        Args:
            series_id: TVDB series ID
            season: Season number
            track_runtimes: List of track durations in seconds (index = track index)

        Returns:
            Dict mapping track index to episode info
        """
        episodes = self.get_sonarr_episodes(series_id, season)
        if not episodes:
            # Fallback: create sequential episode mapping
            activity.log_info(f"SONARR: Using fallback sequential episode numbering")
            mapping = {}
            for idx, runtime in enumerate(track_runtimes):
                mapping[idx] = {
                    'episode_number': idx + 1,
                    'season_number': season,
                    'title': f"Episode {idx + 1}",
                    'runtime': runtime
                }
            return mapping

        # Try to match tracks to episodes by runtime
        mapping = {}
        used_episodes = set()

        for track_idx, track_runtime in enumerate(track_runtimes):
            best_match = None
            best_diff = float('inf')

            for ep in episodes:
                ep_num = ep['episode_number']
                if ep_num in used_episodes:
                    continue

                ep_runtime = ep.get('runtime', 0)
                diff = abs(track_runtime - ep_runtime)

                if diff < best_diff and diff <= self.tv_episode_tolerance:
                    best_diff = diff
                    best_match = ep

            if best_match:
                mapping[track_idx] = {
                    'episode_number': best_match['episode_number'],
                    'season_number': season,
                    'title': best_match.get('title', f"Episode {best_match['episode_number']}"),
                    'runtime': track_runtime
                }
                used_episodes.add(best_match['episode_number'])
            else:
                # No match - assign sequential episode number
                next_ep = len(mapping) + 1
                mapping[track_idx] = {
                    'episode_number': next_ep,
                    'season_number': season,
                    'title': f"Episode {next_ep}",
                    'runtime': track_runtime
                }

        activity.log_info(f"SONARR: Matched {len(mapping)} tracks to episodes")
        return mapping

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

        # Get video runtime (in seconds)
        runtime = self.get_video_runtime(folder)
        runtime_secs = runtime if runtime else 0

        # Try community database first (if enabled)
        community_match = community_db.lookup_disc(disc_label, runtime_secs, self.config)
        if community_match:
            activity.log_success(f"IDENTIFY: Community DB match for '{disc_label}' -> '{community_match['title']}'")
            return IdentificationResult(
                title=community_match.get('title', ''),
                year=community_match.get('year', 0) or 0,
                tmdb_id=community_match.get('tmdb_id', 0) or 0,
                runtime_minutes=community_match.get('duration_secs', 0) // 60,
                confidence=90,  # High confidence from community DB
                media_type='movie' if community_match.get('disc_type') in ['dvd', 'bluray'] else 'movie'
            )

        # Search Radarr (movies)
        result = self.search_radarr(search_term, runtime)

        if result and result.confidence >= 50:
            return result

        # Try Sonarr if movie search failed
        result = self.search_sonarr(search_term)

        if result and result.confidence >= 50:
            return result

        # Fallback: search Radarr library by runtime only
        # This helps when disc label is generic (e.g., "LOGICAL_VOLUME_ID")
        if runtime and not result:
            activity.log_info(f"IDENTIFY: Title search failed, trying runtime-only match...")
            result = self.search_radarr_by_runtime(runtime)

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
