// ============================================
// EarningsEdge AI - Main JavaScript
// Core utilities and global functions
// ============================================

// API Base URL
const API_BASE = window.location.origin;
const MARKET_TIMEZONE = (window.marketConfig && window.marketConfig.timezone) || 'America/New_York';
const MARKET_DATE_OVERRIDE = (window.marketConfig && window.marketConfig.currentDateOverride) || null;

function getMarketNow() {
    if (!MARKET_DATE_OVERRIDE) return new Date();
    const now = new Date();
    const [year, month, day] = MARKET_DATE_OVERRIDE.split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day, now.getUTCHours(), now.getUTCMinutes(), now.getUTCSeconds()));
}

// Global state
window.appState = {
    currentStock: null,
    earningsData: [],
    trades: [],
    watchlist: [],
    charts: {}
};

function getUiThemeTokens() {
    const styles = getComputedStyle(document.documentElement);
    const read = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
    return {
        surface: read('--bg-card', '#ffffff'),
        elevated: read('--bg-elevated', '#f7f9fc'),
        border: read('--surface-outline', '#d8e1ea'),
        text: read('--ink-base', '#223247'),
        strong: read('--ink-strong', '#122033'),
        muted: read('--gray', '#6f8194'),
        mutedSoft: read('--gray-light', '#8a9aac'),
        accent: read('--primary', '#2f6fdd'),
        success: read('--success', '#169b62'),
        danger: read('--danger', '#d84b55')
    };
}

// Toast notification system
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <i class="fas ${type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle'}"></i>
        <span>${message}</span>
    `;
    
    // Add styles if not already added
    if (!document.querySelector('#toast-styles')) {
        const theme = getUiThemeTokens();
        const styles = document.createElement('style');
        styles.id = 'toast-styles';
        styles.textContent = `
            .toast {
                position: fixed;
                top: 92px;
                right: 24px;
                padding: 14px 18px;
                background: ${theme.surface};
                border-radius: 18px;
                border: 1px solid ${theme.border};
                box-shadow: 0 24px 60px -34px rgba(18, 32, 51, 0.18);
                backdrop-filter: blur(16px);
                display: flex;
                align-items: center;
                gap: 12px;
                z-index: 10000;
                animation: slideIn 0.28s cubic-bezier(.2,.9,.2,1);
                font-size: 13px;
                color: ${theme.text};
                min-width: 260px;
            }
            .toast-success { box-shadow: inset 3px 0 0 ${theme.success}, 0 24px 60px -34px rgba(18, 32, 51, 0.18); }
            .toast-error { box-shadow: inset 3px 0 0 ${theme.danger}, 0 24px 60px -34px rgba(18, 32, 51, 0.18); }
            .toast-info { box-shadow: inset 3px 0 0 ${theme.accent}, 0 24px 60px -34px rgba(18, 32, 51, 0.18); }
            .toast i { font-size: 16px; }
            @keyframes slideIn {
                from { transform: translateX(20px) translateY(-8px); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
        `;
        document.head.appendChild(styles);
    }
    
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

// Format currency
function formatCurrency(value) {
    if (value === null || value === undefined || value === 'N/A') return 'N/A';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(value);
}

// Format percentage
function formatPercentage(value) {
    if (value === null || value === undefined || value === 'N/A') return 'N/A';
    const num = parseFloat(value);
    if (Number.isNaN(num)) return 'N/A';
    return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
}

// Format date
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    const normalized = /^\d{4}-\d{2}-\d{2}$/.test(dateString)
        ? new Date(`${dateString}T12:00:00`)
        : new Date(dateString);
    return normalized.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        timeZone: MARKET_TIMEZONE
    });
}

function getMarketDateParts(date = new Date()) {
    const formatter = new Intl.DateTimeFormat('en-CA', {
        timeZone: MARKET_TIMEZONE,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
    const parts = Object.fromEntries(formatter.formatToParts(date).map(part => [part.type, part.value]));
    return parts;
}

function getLocalISODate(date = new Date()) {
    const parts = getMarketDateParts(MARKET_DATE_OVERRIDE ? getMarketNow() : date);
    return `${parts.year}-${parts.month}-${parts.day}`;
}

function formatDateTime(dateString) {
    if (!dateString) return 'N/A';
    const normalized = new Date(dateString);
    if (Number.isNaN(normalized.getTime())) return 'N/A';
    return normalized.toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        timeZone: MARKET_TIMEZONE
    });
}

function getMarketDateTimeParts(date = new Date()) {
    const formatter = new Intl.DateTimeFormat('en-US', {
        timeZone: MARKET_TIMEZONE,
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
        timeZoneName: 'short'
    });

    return Object.fromEntries(
        formatter.formatToParts(date).map((part) => [part.type, part.value])
    );
}

function formatMarketClock(date = new Date()) {
    const parts = getMarketDateTimeParts(date);
    const dateLabel = `${parts.month} ${parts.day}, ${parts.year}`;
    const timeLabel = `${parts.hour}:${parts.minute}:${parts.second} ${parts.dayPeriod} ${parts.timeZoneName}`;
    return {
        dateLabel,
        timeLabel,
        fullLabel: `Market date: ${dateLabel} | ${timeLabel}`
    };
}

// Format number with commas
function formatNumber(num) {
    if (num === null || num === undefined) return 'N/A';
    return new Intl.NumberFormat('en-US').format(num);
}

// Get color class based on value
function getColorClass(value, reverse = false) {
    if (value === null || value === undefined || value === 'N/A') return '';
    const num = parseFloat(value);
    if (isNaN(num)) return '';
    
    if (reverse) {
        return num > 0 ? 'negative' : num < 0 ? 'positive' : '';
    }
    return num > 0 ? 'positive' : num < 0 ? 'negative' : '';
}

// Safe JSON parse
function safeJSONParse(str, fallback = null) {
    try {
        return JSON.parse(str);
    } catch {
        return fallback;
    }
}

// Debounce function for search
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Fetch with error handling
async function fetchAPI(url, options = {}) {
    try {
        const response = await fetch(`${API_BASE}${url}`, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });

        const contentType = response.headers.get('content-type') || '';
        const rawText = await response.text();
        let parsed = null;

        if (rawText) {
            try {
                parsed = JSON.parse(rawText);
            } catch (parseError) {
                console.error('JSON parse error:', {
                    url,
                    status: response.status,
                    contentType,
                    snippet: rawText.slice(0, 400)
                });
                throw new Error(`Unexpected token in API response for ${url}`);
            }
        } else {
            parsed = {};
        }

        if (!response.ok) {
            const apiMessage = parsed?.message || parsed?.error || `HTTP ${response.status}: ${response.statusText}`;
            throw new Error(apiMessage);
        }

        return parsed;
    } catch (error) {
        console.error('API Error:', error);
        showToast(error.message, 'error');
        throw error;
    }
}

// Refresh all data
async function refreshAllData() {
    showToast('Refreshing all data...', 'info');

    const refreshers = [];
    if (typeof loadDashboardSnapshot === 'function') refreshers.push(loadDashboardSnapshot());
    if (typeof loadTradeWorkspace === 'function') refreshers.push(loadTradeWorkspace());
    if (typeof analyzeStock === 'function' && document.getElementById('analysisResults')?.style.display !== 'none' && document.getElementById('analysisSearch')?.value) {
        refreshers.push(analyzeStock());
    }
    if (typeof searchEarnings === 'function' && document.getElementById('stockInfo')?.style.display !== 'none' && document.getElementById('stockSearch')?.value) {
        refreshers.push(searchEarnings());
    }

    if (!refreshers.length) {
        showToast('Nothing to refresh on this page.', 'info');
        return;
    }

    await Promise.allSettled(refreshers);
    showToast('Data refreshed successfully!', 'success');
}

// Export to CSV
function downloadCSV(data, filename) {
    if (!data || data.length === 0) {
        showToast('No data to export', 'error');
        return;
    }
    
    const headers = Object.keys(data[0]);
    const csvRows = [
        headers.join(','),
        ...data.map(row => headers.map(header => {
            const value = row[header] || '';
            return `"${String(value).replace(/"/g, '""')}"`;
        }).join(','))
    ];
    
    const csvString = csvRows.join('\n');
    const blob = new Blob([csvString], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    showToast('Export complete!', 'success');
}

// Create chart
function createChart(canvasId, type, labels, data, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    
    const ctx = canvas.getContext('2d');
    
    // Destroy existing chart if it exists
    if (window.appState.charts[canvasId]) {
        window.appState.charts[canvasId].destroy();
    }
    
    const chart = new Chart(ctx, {
        type: type,
        data: {
            labels: labels,
            datasets: [{
                label: options.label || 'Value',
                data: data,
                backgroundColor: options.backgroundColor || 'rgba(37, 99, 235, 0.2)',
                borderColor: options.borderColor || '#2563eb',
                borderWidth: 2,
                tension: 0.4,
                fill: options.fill !== false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'top',
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) label += ': ';
                            if (context.parsed.y !== null) {
                                label += formatCurrency(context.parsed.y);
                            }
                            return label;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: options.beginAtZero !== false,
                    ticks: {
                        callback: function(value) {
                            return formatCurrency(value);
                        }
                    }
                }
            },
            ...options
        }
    });
    
    window.appState.charts[canvasId] = chart;
    return chart;
}

// Calculate moving average
function calculateMA(data, period) {
    const result = [];
    for (let i = 0; i < data.length; i++) {
        if (i < period - 1) {
            result.push(null);
        } else {
            let sum = 0;
            for (let j = 0; j < period; j++) {
                sum += data[i - j];
            }
            result.push(sum / period);
        }
    }
    return result;
}

// Local storage helpers
function saveToLocalStorage(key, value) {
    try {
        if (value === null || value === undefined) {
            localStorage.removeItem(`earningsedge_${key}`);
            return true;
        }
        localStorage.setItem(`earningsedge_${key}`, JSON.stringify(value));
        return true;
    } catch (error) {
        console.error('Error saving to localStorage:', error);
        return false;
    }
}

function loadFromLocalStorage(key, defaultValue = null) {
    try {
        const value = localStorage.getItem(`earningsedge_${key}`);
        return value ? JSON.parse(value) : defaultValue;
    } catch (error) {
        console.error('Error loading from localStorage:', error);
        return defaultValue;
    }
}

// Auto-resize textarea
function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

// Add auto-resize to all textareas
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('textarea').forEach(textarea => {
        textarea.addEventListener('input', () => autoResizeTextarea(textarea));
        autoResizeTextarea(textarea);
    });

    initializeResponsiveShell();
    initializeRevealSystem();
    applyGlobalChartDefaults();
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    // Ctrl + K to focus search
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const searchInput = document.querySelector('.search-input');
        if (searchInput) searchInput.focus();
    }
    
    // Escape to close modals
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal').forEach(modal => {
            modal.style.display = 'none';
        });
    }
    
    // Ctrl + R to refresh
    if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
        e.preventDefault();
        refreshAllData();
    }
});

function initializeRevealSystem() {
    const targets = document.querySelectorAll('.page-header, .welcome-section, .card, .stats-grid .stat-card');
    if (!targets.length || typeof IntersectionObserver === 'undefined') return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
        });
    }, { threshold: 0.12 });

    targets.forEach((target, index) => {
        target.classList.add('reveal-on-scroll');
        target.style.transitionDelay = `${Math.min(index * 35, 180)}ms`;
        observer.observe(target);
    });
}

function initializeResponsiveShell() {
    const navToggle = document.getElementById('navToggleBtn');
    const primaryNav = document.getElementById('primaryNav');
    if (!navToggle || !primaryNav) return;

    const closeNav = () => {
        document.body.classList.remove('nav-open');
        navToggle.setAttribute('aria-expanded', 'false');
    };

    navToggle.addEventListener('click', () => {
        const willOpen = !document.body.classList.contains('nav-open');
        document.body.classList.toggle('nav-open', willOpen);
        navToggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });

    primaryNav.querySelectorAll('a').forEach((link) => {
        link.addEventListener('click', closeNav);
    });

    window.addEventListener('resize', debounce(() => {
        if (window.innerWidth > 1024) {
            closeNav();
        }
        refreshVisibleCharts();
    }, 120));

    document.addEventListener('click', (event) => {
        if (window.innerWidth > 1024) return;
        if (navToggle.contains(event.target) || primaryNav.contains(event.target)) return;
        closeNav();
    });
}

function refreshVisibleCharts() {
    if (typeof Chart === 'undefined') return;

    const globalCharts = window.appState?.charts ? Object.values(window.appState.charts) : [];
    const researchCharts = window.researchState?.charts ? Object.values(window.researchState.charts) : [];
    const workspaceCharts = window.workspaceState?.charts ? Object.values(window.workspaceState.charts) : [];
    const earningsCharts = typeof earningsChart !== 'undefined' && earningsChart ? [earningsChart] : [];

    [...globalCharts, ...researchCharts, ...workspaceCharts, ...earningsCharts].forEach((chart) => {
        if (chart && typeof chart.resize === 'function') {
            chart.resize();
        }
    });
}

function applyGlobalChartDefaults() {
    if (typeof Chart === 'undefined') return;
    const theme = getUiThemeTokens();

    Chart.defaults.font.family = 'Manrope, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    Chart.defaults.font.weight = '600';
    Chart.defaults.color = theme.text;
    Chart.defaults.borderColor = theme.border;
    Chart.defaults.responsive = true;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.boxHeight = 10;
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.legend.labels.color = theme.text;
    Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(18, 32, 51, 0.96)';
    Chart.defaults.plugins.tooltip.titleColor = '#ffffff';
    Chart.defaults.plugins.tooltip.bodyColor = '#f8fbff';
    Chart.defaults.plugins.tooltip.borderColor = theme.border;
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.cornerRadius = 14;
    Chart.defaults.plugins.tooltip.padding = 12;
    Chart.defaults.scale.grid.color = theme.border;
    Chart.defaults.scale.ticks.color = theme.muted;
    Chart.defaults.elements.line.borderWidth = 2.5;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 4;
    Chart.defaults.animation.duration = 480;
    Chart.defaults.animation.easing = 'easeOutQuart';
}

// Console greeting
console.log('%c🚀 EarningsEdge AI Trading Dashboard', 'color: #2563eb; font-size: 16px; font-weight: bold;');
console.log('%cProfessional Trading Intelligence System', 'color: #64748b; font-size: 12px;');
console.log('%cReady to help you trade smarter!', 'color: #10b981; font-size: 12px;');
