// RipForge - Main JavaScript

// Utility functions
function formatTime(seconds) {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hrs > 0) {
        return `${hrs}h ${mins}m`;
    }
    return `${mins}m ${secs}s`;
}

function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// API wrapper
const api = {
    get: async (endpoint) => {
        const response = await fetch(endpoint);
        return response.json();
    },

    post: async (endpoint, data) => {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        return response.json();
    }
};

// Toast notifications
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// WebSocket connection for real-time updates (future feature)
class RipForgeSocket {
    constructor() {
        this.ws = null;
        this.handlers = {};
    }

    connect() {
        // WebSocket implementation for real-time rip progress
        // To be implemented
    }

    on(event, handler) {
        this.handlers[event] = handler;
    }

    emit(event, data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ event, data }));
        }
    }
}

// Load rip statistics for sidebar
function loadRipStats() {
    fetch('/api/library-stats')
        .then(r => r.json())
        .then(data => {
            const movies = document.getElementById('stat-movies');
            const tv = document.getElementById('stat-tv');
            const size = document.getElementById('stat-size');
            const errors = document.getElementById('stat-errors');

            if (movies) movies.textContent = data.movies || 0;
            if (tv) tv.textContent = data.tv || 0;
            if (size) size.textContent = data.total_size || '--';
            if (errors) errors.textContent = data.errors || 0;
        })
        .catch(err => console.error('Error loading library stats:', err));
}

// Load integration status for sidebar
function loadIntegrationStatus() {
    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            if (data.integrations) {
                for (const [service, status] of Object.entries(data.integrations)) {
                    const el = document.getElementById('int-' + service);
                    if (el) {
                        if (!status.enabled) {
                            el.className = 'int-dot disabled';
                        } else if (status.connected) {
                            el.className = 'int-dot connected';
                        } else {
                            el.className = 'int-dot error';
                        }
                    }
                }
            }
        })
        .catch(err => console.error('Error loading status:', err));
}

// Load version for sidebar
function loadSidebarVersion() {
    fetch('/api/version')
        .then(r => r.json())
        .then(data => {
            const el = document.getElementById('sidebar-version');
            if (el) {
                el.textContent = 'v' + data.current_version;
            }
        })
        .catch(err => console.error('Error loading version:', err));
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('RipForge loaded');

    // Load sidebar stats and version on all pages
    loadRipStats();
    loadIntegrationStatus();
    loadSidebarVersion();

    // Refresh stats every 30 seconds
    setInterval(loadRipStats, 30000);
    // Refresh integration status every 10 seconds
    setInterval(loadIntegrationStatus, 10000);
});
