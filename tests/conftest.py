"""
Pytest fixtures for RipForge tests
"""

import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def sample_config():
    """Basic RipForge configuration for testing"""
    return {
        'identification': {
            'runtime_tolerance': 300,
            'confidence_threshold': 75
        },
        'ripping': {
            'tv_min_episode_length': 1200,  # 20 min
            'tv_max_episode_length': 3600,  # 60 min
            'tv_episode_tolerance': 60,
            'rip_mode': 'smart',
            'backup_fallback': True
        },
        'integrations': {
            'radarr': {
                'url': 'http://localhost:7878',
                'api_key': 'test_api_key'
            },
            'sonarr': {
                'url': 'http://localhost:8989',
                'api_key': 'test_api_key'
            }
        },
        'paths': {
            'raw': '/tmp/ripforge_test/raw',
            'completed': '/tmp/ripforge_test/completed',
            'movies': '/tmp/ripforge_test/movies',
            'tv': '/tmp/ripforge_test/tv'
        }
    }


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_tracks():
    """Sample track list as returned by MakeMKV"""
    return [
        {'index': 0, 'duration': 7200, 'size': 25000000000, 'name': 'title00.mkv'},  # 2 hours - main feature
        {'index': 1, 'duration': 120, 'size': 500000000, 'name': 'title01.mkv'},     # 2 min - trailer
        {'index': 2, 'duration': 180, 'size': 750000000, 'name': 'title02.mkv'},     # 3 min - featurette
    ]


@pytest.fixture
def sample_tv_tracks():
    """Sample TV episode tracks"""
    return [
        {'index': 0, 'duration': 2700, 'size': 3000000000, 'name': 'title00.mkv'},  # 45 min ep
        {'index': 1, 'duration': 2640, 'size': 2900000000, 'name': 'title01.mkv'},  # 44 min ep
        {'index': 2, 'duration': 2760, 'size': 3100000000, 'name': 'title02.mkv'},  # 46 min ep
        {'index': 3, 'duration': 2700, 'size': 3000000000, 'name': 'title03.mkv'},  # 45 min ep
    ]


@pytest.fixture
def mock_radarr_response():
    """Sample Radarr API response for movie lookup"""
    return [
        {
            'title': 'Guardians of the Galaxy Vol. 3',
            'year': 2023,
            'tmdbId': 447365,
            'imdbId': 'tt6791350',
            'runtime': 150,
            'popularity': 125.5,
            'images': [
                {'coverType': 'poster', 'remoteUrl': 'https://image.tmdb.org/t/p/w500/poster.jpg'}
            ]
        },
        {
            'title': 'Guardians of the Galaxy',
            'year': 2014,
            'tmdbId': 118340,
            'imdbId': 'tt2015381',
            'runtime': 121,
            'popularity': 95.2,
            'images': [
                {'coverType': 'poster', 'remoteUrl': 'https://image.tmdb.org/t/p/w500/poster2.jpg'}
            ]
        }
    ]


@pytest.fixture
def mock_sonarr_response():
    """Sample Sonarr API response for series lookup"""
    return [
        {
            'title': 'Breaking Bad',
            'year': 2008,
            'tvdbId': 81189,
            'runtime': 47,
            'ratings': {'votes': 50000},
            'seasons': [
                {'seasonNumber': 1, 'statistics': {'totalEpisodeCount': 7}},
                {'seasonNumber': 2, 'statistics': {'totalEpisodeCount': 13}}
            ],
            'images': [
                {'coverType': 'poster', 'remoteUrl': 'https://example.com/poster.jpg'}
            ]
        }
    ]
