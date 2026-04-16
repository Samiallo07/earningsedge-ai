// ============================================
// Earnings Analysis JavaScript
// Handles earnings history and charts
// ============================================

let currentEarningsData = [];
let earningsChart = null;
let currentSetupAnalysis = null;
const TRADE_DRAFT_STORAGE_KEY = 'trade_draft_from_earnings';
let activeEarningsRequest = 0;

function getThemeChartColors() {
    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue('--primary').trim() || '#4A90E2';
    const accentSoft = styles.getPropertyValue('--primary-light').trim() || '#4A90E2';
    const success = styles.getPropertyValue('--success').trim() || '#16C784';
    const danger = styles.getPropertyValue('--danger').trim() || '#EA3943';
    const muted = styles.getPropertyValue('--gray').trim() || '#8D98A7';
    const border = styles.getPropertyValue('--surface-outline').trim() || '#232A34';
    return {
        accent,
        accentSoft,
        success,
        danger,
        muted,
        border,
        accentFill: 'rgba(74, 144, 226, 0.18)',
        successFill: 'rgba(22, 199, 132, 0.18)'
    };
}

// Get URL parameters
const urlParams = new URLSearchParams(window.location.search);
const initialSymbol = urlParams.get('symbol');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    if (initialSymbol) {
        document.getElementById('stockSearch').value = initialSymbol;
        searchEarnings();
    }
    
    // Setup search with debounce
    const searchInput = document.getElementById('stockSearch');
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') searchEarnings();
        });
    }
});

function prefillEarningsSymbol(symbol) {
    const input = document.getElementById('stockSearch');
    if (!input) return;
    input.value = symbol;
    searchEarnings();
}

// Search earnings
async function searchEarnings() {
    const symbol = document.getElementById('stockSearch').value.trim().toUpperCase();
    if (!symbol) {
        showToast('Please enter a stock symbol', 'error');
        return;
    }

    const requestId = ++activeEarningsRequest;
    showLoading();
    
    try {
        const [earnings, stockData, setupAnalysis] = await Promise.all([
            fetchAPI(`/api/earnings/${symbol}`),
            fetchAPI(`/api/stock_data/${symbol}`).catch(() => null),
            fetchAPI(`/api/analyze_stock/${symbol}?trade_type=Earnings`).catch(() => null)
        ]);

        if (requestId !== activeEarningsRequest) return;
        currentEarningsData = earnings;
        
        if (!earnings || earnings.length === 0) {
            showToast(`No earnings data found for ${symbol}`, 'error');
            hideLoading();
            return;
        }
        currentSetupAnalysis = setupAnalysis;
        
        // Update UI
        updateStockInfo(symbol, stockData, setupAnalysis);
        renderEarningsTable(earnings);
        renderEarningsStats(earnings);
        renderEarningsChart(earnings);
        renderSetupAnalysis(setupAnalysis);
        
        // Show all sections
        document.getElementById('stockInfo').style.display = 'block';
        document.getElementById('earningsTable').style.display = 'block';
        document.getElementById('performanceChart').style.display = 'block';
        document.getElementById('earningsStats').style.display = 'block';
        if (setupAnalysis) {
            document.getElementById('setupAnalysisPanel').style.display = 'block';
        }
        updateTradeLogButtonState(setupAnalysis);
        
        showToast(`Loaded earnings data for ${symbol}`, 'success');
    } catch (error) {
        console.error('Error loading earnings:', error);
        showToast('Error loading earnings data', 'error');
    } finally {
        if (requestId === activeEarningsRequest) {
            hideLoading();
        }
    }
}

function updateTradeLogButtonState(setupAnalysis) {
    const button = document.getElementById('logTradeFromEarningsBtn');
    if (!button) return;
    button.disabled = !setupAnalysis;
}

// Update stock info
function updateStockInfo(symbol, stockData, setupAnalysis) {
    document.getElementById('stockSymbol').textContent = symbol;
    document.getElementById('stockName').textContent = setupAnalysis?.research_snapshot?.company_name || symbol;
    
    const priceElement = document.getElementById('currentPrice');
    if (stockData && stockData.current_price) {
        priceElement.innerHTML = `
            <span class="stock-price-value">${formatCurrency(stockData.current_price)}</span>
            <span class="${getColorClass(stockData.change_pct)}">${formatPercentage(stockData.change_pct)}</span>
        `;
    } else {
        priceElement.innerHTML = '<span>Price data unavailable</span>';
    }
}

// Render earnings table
function renderEarningsTable(earnings) {
    const tbody = document.getElementById('earningsTableBody');
    
    const rows = earnings.map(earning => `
        <tr>
            <td>
                <div class="table-primary">${earning.quarter}</div>
                <div class="table-subcopy">${earning.report_date && earning.report_date !== 'N/A' ? formatDate(earning.report_date) : 'Date unavailable'}</div>
            </td>
            <td>${earning.actual_eps !== 'N/A' ? formatCurrency(earning.actual_eps) : 'N/A'}</td>
            <td>${earning.estimate_eps !== 'N/A' ? formatCurrency(earning.estimate_eps) : 'N/A'}</td>
            <td class="${getColorClass(earning.surprise)}">${earning.surprise !== 'N/A' ? formatCurrency(earning.surprise) : 'N/A'}</td>
            <td class="${getColorClass(earning.surprise_pct)}">${formatPercentage(earning.surprise_pct)}</td>
            <td class="${getColorClass(earning.earnings_day_move)}">${formatPercentage(earning.earnings_day_move)}</td>
            <td class="${getColorClass(earning.next_day_move)}">${formatPercentage(earning.next_day_move)}</td>
            <td><span class="reason-pill">${earning.reason}</span></td>
        </tr>
    `).join('');
    
    tbody.innerHTML = rows;
}

// Render earnings statistics
function renderEarningsStats(earnings) {
    // Calculate statistics
    const beats = earnings.filter(e => e.surprise_pct !== 'N/A' && parseFloat(e.surprise_pct) > 0);
    const positiveMoves = earnings.filter(e => e.earnings_day_move !== 'N/A' && parseFloat(e.earnings_day_move) > 0);
    const validMoves = earnings.filter(e => e.earnings_day_move !== 'N/A' && !Number.isNaN(parseFloat(e.earnings_day_move)));
    
    const avgBeat = beats.length > 0 
        ? beats.reduce((sum, e) => sum + parseFloat(e.surprise_pct), 0) / beats.length 
        : 0;
    
    const winRate = earnings.length > 0 
        ? (positiveMoves.length / earnings.length) * 100 
        : 0;
    
    const avgMove = validMoves.length > 0
        ? validMoves.reduce((sum, e) => sum + Math.abs(parseFloat(e.earnings_day_move)), 0) / validMoves.length
        : 0;
    
    document.getElementById('avgBeat').textContent = formatPercentage(avgBeat);
    document.getElementById('winRate').textContent = `${winRate.toFixed(1)}%`;
    document.getElementById('avgMove').textContent = formatPercentage(avgMove);
}

// Render earnings chart
function renderEarningsChart(earnings) {
    const theme = getThemeChartColors();
    const labels = earnings.map(e => e.quarter);
    const surpriseData = earnings.map(e => e.surprise_pct !== 'N/A' ? parseFloat(e.surprise_pct) : 0);
    
    if (earningsChart) earningsChart.destroy();
    
    const canvas = document.getElementById('earningsChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    earningsChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Surprise %',
                data: surpriseData,
                backgroundColor: theme.accentFill,
                borderColor: theme.accentSoft,
                borderWidth: 1.5,
                borderRadius: 10,
                yAxisID: 'y'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        color: theme.muted,
                        boxWidth: 12,
                        usePointStyle: true,
                        pointStyle: 'rectRounded'
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(18, 24, 33, 0.98)',
                    borderColor: theme.border,
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        label: function(context) {
                            return `${context.dataset.label}: ${context.parsed.y.toFixed(2)}%`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    grid: {
                        color: 'rgba(100, 116, 139, 0.12)'
                    },
                    title: {
                        display: true,
                        text: 'Percentage (%)'
                    },
                    ticks: {
                        color: theme.muted
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        color: theme.muted
                    }
                }
            }
        }
    });
}

// Switch chart type
function switchChart(type) {
    if (!currentEarningsData || currentEarningsData.length === 0) return;
    const theme = getThemeChartColors();
    
    const labels = currentEarningsData.map(e => e.quarter);
    let data;
    let label;
    
    if (type === 'surprise') {
        data = currentEarningsData.map(e => e.surprise_pct !== 'N/A' ? parseFloat(e.surprise_pct) : 0);
        label = 'Surprise %';
    } else {
        data = currentEarningsData.map(e => e.earnings_day_move !== 'N/A' ? parseFloat(e.earnings_day_move) : 0);
        label = 'Price Move %';
    }
    
    if (earningsChart) {
        earningsChart.data.datasets[0].data = data;
        earningsChart.data.datasets[0].label = label;
        earningsChart.data.datasets[0].backgroundColor = type === 'surprise' ? theme.accentFill : theme.successFill;
        earningsChart.data.datasets[0].borderColor = type === 'surprise' ? theme.accentSoft : theme.success;
        earningsChart.update();
    }

    document.querySelectorAll('[data-earnings-chart]').forEach((button) => {
        button.classList.toggle('active', button.dataset.earningsChart === type);
    });
}

function renderSetupAnalysis(analysis) {
    const panel = document.getElementById('setupAnalysisPanel');
    if (!panel) return;

    if (!analysis) {
        panel.style.display = 'none';
        return;
    }

    const metrics = document.getElementById('setupScoreMetrics');
    const explanation = document.getElementById('setupExplanationList');
    const memory = document.getElementById('setupMemoryList');
    const features = document.getElementById('setupFeatureList');

    const scoreTone = analysis.label === 'GOOD' ? 'positive' : analysis.label === 'AVOID' ? 'negative' : '';
    metrics.innerHTML = [
        { label: 'Score', value: analysis.score, tone: scoreTone },
        { label: 'Label', value: analysis.label, tone: scoreTone },
        { label: 'Confidence', value: `${Number(analysis.confidence_score || 0).toFixed(1)}%`, tone: '' },
        { label: 'Setup Type', value: titleizeValue(analysis.setup_type), tone: '' }
    ].map((item) => `
        <div class="stat-card">
            <div>
                <span class="stat-value ${item.tone}">${item.value}</span>
                <span class="stat-label">${item.label}</span>
            </div>
        </div>
    `).join('');

    explanation.innerHTML = [
        renderInsightRow('Summary', analysis.short_explanation || 'No structured summary yet.'),
        renderInsightRow('Key Positives', listOrFallback(analysis.key_positives, 'No major positive factor stood out.')),
        renderInsightRow('Top Risks', listOrFallback(analysis.key_risks, 'No major risk factor stood out.')),
        renderInsightRow('Red Flags', listOrFallback(analysis.red_flags, 'No major red flags were triggered.'))
    ].join('');

    const similar = analysis.similar_trade_summary || {};
    memory.innerHTML = [
        renderInsightRow('Memory Summary', analysis.memory_summary || similar.summary || 'Trade memory is still building.'),
        renderInsightRow('Similar Winners', String(similar.similar_winning_trades ?? 0)),
        renderInsightRow('Similar Losers', String(similar.similar_losing_trades ?? 0)),
        renderInsightRow(
            'Pattern Tilt',
            listOrFallback(
                (similar.strongest_negative_patterns || []).map((item) => `${item.label} (${item.avg_return}% avg return)`),
                listOrFallback(
                    (similar.strongest_positive_patterns || []).map((item) => `${item.label} (${item.win_rate}% win rate)`),
                    'No strong learned pattern yet.'
                )
            )
        )
    ].join('');

    const featureMap = analysis.features || {};
    const featureRows = [
        ['Estimate Revision', titleizeValue(featureMap.estimate_revision)],
        ['Price Trend', titleizeValue(featureMap.price_trend)],
        ['Valuation Level', titleizeValue(featureMap.valuation_level)],
        ['Pre-Earnings Run', titleizeValue(featureMap.pre_earnings_run)],
        ['Expected Move', featureMap.expected_move_pct != null ? `${titleizeValue(featureMap.expected_move)} (${featureMap.expected_move_pct}%)` : titleizeValue(featureMap.expected_move)],
        ['Historical Reaction', featureMap.historical_positive_reaction_rate != null ? `${titleizeValue(featureMap.historical_reaction)} (${featureMap.historical_positive_reaction_rate}% positive)` : titleizeValue(featureMap.historical_reaction)],
        ['Next Earnings', featureMap.earnings_date || 'N/A']
    ];
    features.innerHTML = featureRows.map(([label, value]) => renderInsightRow(label, value || 'N/A')).join('');
    panel.style.display = 'block';
}

function renderInsightRow(label, value) {
    return `
        <div class="insight-row">
            <strong>${label}</strong>
            <span>${value}</span>
        </div>
    `;
}

function titleizeValue(value) {
    if (!value) return 'N/A';
    return String(value).replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

function listOrFallback(items, fallback) {
    if (!items || !items.length) return fallback;
    return items.join(' | ');
}

function logTradeFromEarnings() {
    if (!currentSetupAnalysis) {
        showToast('Analyze a symbol first so we can carry the setup into your trade journal.', 'error');
        return;
    }

    const analysis = currentSetupAnalysis;
    const draft = {
        source: 'earnings_analysis',
        symbol: analysis.symbol,
        trade_type: 'Earnings',
        earnings_date: analysis.features?.earnings_date || null,
        entry_price: analysis.research_snapshot?.current_price || null,
        stop_loss: null,
        take_profit: null,
        thesis: analysis.short_explanation || '',
        setup_notes: buildTradeDraftNotes(analysis),
        notes: buildTradeDraftJournalNote(analysis),
        setup_profile: analysis.features || {},
        trade_features: analysis.features || {},
        trade_insights: {
            ...(analysis.insights || {}),
            key_positives: analysis.key_positives || [],
            key_risks: analysis.key_risks || [],
            label: analysis.label,
            score: analysis.score,
            setup_type: analysis.setup_type,
            memory_summary: analysis.memory_summary
        },
        score_payload: {
            score: analysis.score,
            label: analysis.label,
            confidence_score: analysis.confidence_score,
            key_positives: analysis.key_positives || [],
            key_risks: analysis.key_risks || [],
            red_flags: analysis.red_flags || []
        },
        analysis_snapshot: {
            score: analysis.score,
            label: analysis.label,
            confidence_score: analysis.confidence_score,
            setup_type: analysis.setup_type,
            memory_summary: analysis.memory_summary
        }
    };

    saveToLocalStorage(TRADE_DRAFT_STORAGE_KEY, draft);
    window.location.href = '/trades?openTradeDraft=1';
}

function buildTradeDraftNotes(analysis) {
    const lines = [
        `Setup score: ${analysis.score} (${analysis.label})`,
        `Confidence: ${Number(analysis.confidence_score || 0).toFixed(1)}%`,
        `Setup type: ${titleizeValue(analysis.setup_type)}`
    ];
    if (analysis.key_positives?.length) {
        lines.push(`Positives: ${analysis.key_positives.join(' | ')}`);
    }
    if (analysis.key_risks?.length) {
        lines.push(`Risks: ${analysis.key_risks.join(' | ')}`);
    }
    if (analysis.red_flags?.length) {
        lines.push(`Red flags: ${analysis.red_flags.join(' | ')}`);
    }
    return lines.join('\n');
}

function buildTradeDraftJournalNote(analysis) {
    return analysis.memory_summary || 'Captured directly from the earnings analysis workflow.';
}

// Export to CSV
function exportToCSV() {
    if (!currentEarningsData || currentEarningsData.length === 0) {
        showToast('No data to export', 'error');
        return;
    }
    
    const exportData = currentEarningsData.map(e => ({
        'Quarter': e.quarter,
        'Actual EPS': e.actual_eps,
        'Estimate EPS': e.estimate_eps,
        'Surprise': e.surprise,
        'Surprise %': e.surprise_pct,
        'Earnings Day Move %': e.earnings_day_move,
        'Next Day Move %': e.next_day_move,
        'Reason': e.reason
    }));
    
    downloadCSV(exportData, `${document.getElementById('stockSymbol').textContent || 'stock'}_earnings.csv`);
}

// Show/hide loading
function showLoading() {
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'earningsLoading';
    loadingDiv.className = 'loading-overlay';
    loadingDiv.innerHTML = `
        <div class="loading-overlay-card">
            <div class="loading-spinner">Loading earnings setup...</div>
        </div>
    `;
    document.body.appendChild(loadingDiv);
}

function hideLoading() {
    const loading = document.getElementById('earningsLoading');
    if (loading) loading.remove();
}

// Add loading styles if not present
if (!document.querySelector('#loading-styles')) {
    const styles = document.createElement('style');
    styles.id = 'loading-styles';
    styles.textContent = `
        .loading-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(15, 23, 42, 0.22);
            backdrop-filter: blur(8px);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 9999;
        }
        .loading-overlay-card {
            min-width: 240px;
            padding: 18px 22px;
            border-radius: 20px;
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid rgba(226, 232, 240, 0.95);
            box-shadow: 0 24px 60px -34px rgba(15, 23, 42, 0.48);
        }
        .loading-overlay .loading-spinner {
            padding: 0;
        }
    `;
    document.head.appendChild(styles);
}
