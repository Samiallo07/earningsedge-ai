// ============================================
// Stock Research Dashboard JavaScript
// ============================================

let researchState = {
    symbol: null,
    data: null,
    charts: {},
    priceView: 'price',
    activeRequestId: 0,
    preferredFocusSymbol: null
};

const RESEARCH_CHART_DEFAULTS = {
    interaction: {
        intersect: false,
        mode: 'index'
    },
    animation: {
        duration: 350,
        easing: 'easeOutQuart'
    },
    elements: {
        line: {
            borderWidth: 3
        },
        point: {
            radius: 0,
            hoverRadius: 4,
            hitRadius: 14
        }
    },
    plugins: {
        legend: {
            labels: {
                usePointStyle: true,
                boxWidth: 10,
                boxHeight: 10,
                padding: 18,
                color: '#B7C0CC',
                font: {
                    size: 12,
                    weight: '600'
                }
            }
        },
        tooltip: {
            backgroundColor: 'rgba(18, 24, 33, 0.98)',
            titleColor: '#FFFFFF',
            bodyColor: '#F5F7FA',
            borderColor: 'rgba(35, 42, 52, 1)',
            borderWidth: 1,
            padding: 12,
            displayColors: true
        }
    },
    scales: {
        x: {
            grid: {
                display: false
            },
            ticks: {
                color: '#8D98A7',
                maxRotation: 0,
                autoSkip: true,
                maxTicksLimit: 8
            }
        },
        y: {
            beginAtZero: false,
            grid: {
                color: 'rgba(183, 192, 204, 0.08)',
                drawBorder: false
            },
            ticks: {
                color: '#8D98A7',
                padding: 10
            }
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    researchState.preferredFocusSymbol = loadFromLocalStorage('preferred_focus_symbol', null);
    const urlParams = new URLSearchParams(window.location.search);
    const symbol = urlParams.get('symbol');

    if (symbol) {
        document.getElementById('analysisSearch').value = symbol;
        analyzeStock();
    }

    const searchInput = document.getElementById('analysisSearch');
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') analyzeStock();
        });
    }
});

async function analyzeStock() {
    const symbol = document.getElementById('analysisSearch').value.trim().toUpperCase();
    if (!symbol) {
        showToast('Please enter a stock symbol', 'error');
        return;
    }

    researchState.symbol = symbol;
    const requestId = ++researchState.activeRequestId;
    showResearchLoading();

    try {
        const data = await fetchAPI(`/api/stock_research/${symbol}`);
        if (requestId !== researchState.activeRequestId) return;
        console.debug('Research payload:', data);
        researchState.data = data;

        safelyRenderSection('research hero', () => renderResearchHero(data));
        safelyRenderSection('snapshot metrics', () => renderSnapshotMetrics(data.snapshot || {}));
        safelyRenderSection('dcf section', () => renderDCFSection(data.dcf_valuation || {}, data.snapshot || {}));
        safelyRenderSection('ai summary', () => renderAISummary(data.ai_summary || {}, data.ten_k_risks || {}));
        safelyRenderSection('price targets', () => renderPriceTargetRange(data.price_targets || {}));
        safelyRenderSection('recommendation summary', () => renderRecommendationSummary(data.recommendation_summary || {}));
        safelyRenderSection('analyst changes', () => renderAnalystChanges(data.analyst_changes || []));
        safelyRenderSection('recent earnings', () => renderRecentEarnings(data.earnings || []));
        safelyRenderSection('news list', () => renderNewsList(data.news_impact || data.news || []));
        safelyRenderSection('price chart', () => renderPriceChart());
        safelyRenderSection('comparison chart', () => renderComparisonChart());
        safelyRenderSection('financial chart', () => renderFinancialChart());

        document.getElementById('analysisResults').style.display = 'block';
        showToast(`Research dashboard ready for ${symbol}`, 'success');
    } catch (error) {
        if (requestId !== researchState.activeRequestId) return;
        console.error('Error building research dashboard:', error);
        document.getElementById('aiAnalysis').innerHTML = `
            <div class="error-message">
                <i class="fas fa-exclamation-triangle"></i>
                <p>Unable to build the research dashboard for ${symbol} right now.</p>
            </div>
        `;
        document.getElementById('analysisResults').style.display = 'block';
        showToast('Error loading research dashboard', 'error');
    }
}

function showResearchLoading() {
    destroyResearchCharts();
    document.getElementById('analysisResults').style.display = 'block';
    document.getElementById('analysisSymbol').textContent = researchState.symbol || '--';
    document.getElementById('analysisCompany').textContent = 'Loading company data...';
    document.getElementById('heroPrice').textContent = '--';
    document.getElementById('heroChange').textContent = '--';
    document.getElementById('researchFocusToolbar').innerHTML = '';
    document.getElementById('researchHeroMeta').innerHTML = '';
    document.getElementById('companySummary').innerHTML = '<div class="loading-spinner">Loading stock research...</div>';
    document.getElementById('aiAnalysis').innerHTML = '<div class="loading-spinner">Building AI summary...</div>';
    document.getElementById('snapshotMetrics').innerHTML = '';
    document.getElementById('dcfStatusBadge').textContent = 'Base case';
    document.getElementById('dcfStatusBadge').className = 'dcf-header-badge';
    document.getElementById('dcfSection').innerHTML = '<div class="loading-spinner">Building discounted cash flow model...</div>';
    document.getElementById('priceTargetRange').innerHTML = '<div class="loading-spinner">Loading price targets...</div>';
    document.getElementById('recommendationHeadline').innerHTML = '<div class="loading-spinner">Loading analyst view...</div>';
    document.getElementById('analystChanges').innerHTML = '<div class="loading-spinner">Loading analyst changes...</div>';
    document.getElementById('recentEarnings').innerHTML = '<div class="loading-spinner">Loading earnings trends...</div>';
    document.getElementById('newsList').innerHTML = '<div class="loading-spinner">Loading recent news...</div>';
}

function renderResearchHero(data) {
    const snapshot = data.snapshot || {};
    document.getElementById('analysisSymbol').textContent = data.symbol;
    document.getElementById('analysisCompany').textContent = snapshot.long_name || snapshot.short_name || data.symbol;
    document.getElementById('heroPrice').textContent = snapshot.price_formatted || 'N/A';
    const heroChange = document.getElementById('heroChange');
    heroChange.textContent = snapshot.daily_change_pct_formatted || 'N/A';
    heroChange.className = `research-price-change ${getColorClass(snapshot.daily_change_pct)}`;
    document.getElementById('companySummary').textContent = truncateText(snapshot.summary || 'No company overview available yet.', 280);
    renderResearchFocusToolbar(snapshot);
    renderResearchHeroMeta(snapshot);
}

function renderResearchFocusToolbar(snapshot) {
    const container = document.getElementById('researchFocusToolbar');
    if (!container) return;

    const symbol = researchState.symbol;
    const isFocused = researchState.preferredFocusSymbol === symbol;
    const websiteLink = snapshot.website
        ? `<a class="btn-secondary btn-compact" href="${snapshot.website}" target="_blank" rel="noreferrer">Website</a>`
        : '';

    container.innerHTML = `
        <button class="btn-primary btn-compact" type="button" onclick="addResearchSymbolToWatchlist()">Add Stock</button>
        ${isFocused
            ? `<button class="btn-secondary btn-compact" type="button" onclick="clearResearchFocus()">Remove Focus</button>`
            : `<button class="btn-secondary btn-compact" type="button" onclick="setResearchFocus()">Set Focus</button>`}
        <button class="btn-secondary btn-compact" type="button" onclick="removeResearchSymbolFromWatchlist()">Remove Stock</button>
        ${websiteLink}
    `;
}

function renderResearchHeroMeta(snapshot) {
    const container = document.getElementById('researchHeroMeta');
    if (!container) return;

    const items = [
        ['Sector', snapshot.sector || 'N/A'],
        ['Industry', snapshot.industry || 'N/A'],
        ['Market Cap', snapshot.market_cap || 'N/A'],
        ['Forward P/E', snapshot.forward_pe ?? 'N/A']
    ];

    container.innerHTML = items.map(([label, value]) => `
        <div class="research-meta-chip">
            <span>${label}</span>
            <strong>${value}</strong>
        </div>
    `).join('');
}

function renderSnapshotMetrics(snapshot) {
    const metrics = [
        { label: 'Market Cap', value: snapshot.market_cap },
        { label: 'Forward P/E', value: snapshot.forward_pe },
        { label: 'Trailing P/E', value: snapshot.trailing_pe },
        { label: 'Volume', value: snapshot.volume },
        { label: 'Avg Volume', value: snapshot.avg_volume },
        { label: '52-Week Range', value: snapshot.fifty_two_week_range }
    ];

    document.getElementById('snapshotMetrics').innerHTML = metrics.map((item) => `
        <div class="stat-card research-stat-card">
            <div>
                <span class="stat-value">${item.value || 'N/A'}</span>
                <span class="stat-label">${item.label}</span>
            </div>
        </div>
    `).join('');
}

function renderAISummary(summary, tenKRisks = {}) {
    if (!summary) {
        document.getElementById('aiAnalysis').innerHTML = '<p>No AI summary available.</p>';
        return;
    }

    document.getElementById('aiAnalysis').innerHTML = `
        <div class="ai-summary-layout">
            <div class="ai-summary-hero">
                <div class="ai-summary-label">Primary reason</div>
                <div class="ai-summary-main">${summary.primary_reason || 'No clear summary available.'}</div>
                <div class="earnings-interest-tag">Confidence: ${summary.confidence || 'Medium'}</div>
            </div>
            <div class="ai-summary-grid">
                ${renderSummaryListCard('Supporting factors', summary.supporting_factors)}
                ${renderSummaryListCard('Bullish points', summary.bullish_points)}
                ${renderSummaryListCard('Bearish risks', summary.bearish_risks)}
                ${renderTenKRisksCard(tenKRisks)}
                <div class="ai-summary-card">
                    <div class="ai-summary-title">Brief outlook</div>
                    <p>${summary.outlook || 'No outlook available.'}</p>
                </div>
            </div>
        </div>
    `;
}

function renderTenKRisksCard(tenKRisks) {
    const available = Boolean(tenKRisks && tenKRisks.available && (tenKRisks.risks || []).length);
    const body = available
        ? `<ul>${(tenKRisks.risks || []).map((item) => `<li>${item}</li>`).join('')}</ul>`
        : `<p>${tenKRisks?.message || '10-K risk summary not available for this stock.'}</p>`;

    return `
        <div class="ai-summary-card ten-k-risk-card">
            <div class="ai-summary-title">10-K Risk Summary</div>
            ${body}
        </div>
    `;
}

function renderDCFSection(dcf, snapshot = {}) {
    const container = document.getElementById('dcfSection');
    const badge = document.getElementById('dcfStatusBadge');
    if (!container || !badge) return;

    if (!dcf || dcf.available === false) {
        badge.textContent = 'Limited data';
        badge.className = 'dcf-header-badge dcf-header-badge-muted';
        container.innerHTML = `
            <div class="dcf-empty-state">
                <div class="dcf-empty-copy">
                    <div class="dcf-empty-title">DCF estimate unavailable</div>
                    <p>${dcf?.message || 'There is not enough cash flow data to calculate a reliable fair value estimate right now.'}</p>
                    <div class="dcf-assumption-grid">
                        ${renderAssumptionTile('Current price', dcf?.current_price_formatted || snapshot.price_formatted || 'N/A')}
                        ${renderAssumptionTile('Discount rate', dcf?.assumptions?.discount_rate != null ? `${dcf.assumptions.discount_rate}%` : 'N/A')}
                        ${renderAssumptionTile('Terminal growth', dcf?.assumptions?.terminal_growth_rate != null ? `${dcf.assumptions.terminal_growth_rate}%` : 'N/A')}
                        ${renderAssumptionTile('Projection period', `${dcf?.projection_years || 5} years`)}
                    </div>
                    ${renderDCFWarnings(dcf?.warnings || [])}
                </div>
            </div>
        `;
        return;
    }

    const valuationLabel = dcf.valuation_label || 'Fairly valued';
    const upsideClass = getColorClass(dcf.upside_pct);
    const historicalItems = (dcf.historical_fcf || []).map((item) => `
        <div class="dcf-history-item">
            <span>${item.label}</span>
            <strong>${formatCompactCurrency(item.value)}</strong>
        </div>
    `).join('');

    badge.textContent = `${valuationLabel} | ${dcf.confidence || 'Medium'} confidence`;
    badge.className = `dcf-header-badge ${getDCFBadgeClass(valuationLabel)}`;
    container.innerHTML = `
        <div class="dcf-layout">
            <div class="dcf-top-grid">
                <div class="dcf-value-panel">
                    <div class="dcf-panel-kicker">Estimated fair value</div>
                    <div class="dcf-fair-value">${dcf.fair_value_formatted || 'N/A'}</div>
                    <div class="dcf-price-row">
                        <div>
                            <span>Current price</span>
                            <strong>${dcf.current_price_formatted || snapshot.price_formatted || 'N/A'}</strong>
                        </div>
                        <div>
                            <span>Base case move</span>
                            <strong class="${upsideClass}">${formatPercentage(dcf.upside_pct)}</strong>
                        </div>
                        <div>
                            <span>View</span>
                            <strong>${valuationLabel}</strong>
                        </div>
                    </div>
                    <p class="dcf-simple-copy">${(dcf.explanation || []).join(' ')}</p>
                </div>

                <div class="dcf-side-grid">
                    <div class="dcf-mini-card">
                        <div class="dcf-mini-label">Starting free cash flow</div>
                        <div class="dcf-mini-value">${dcf.starting_fcf_formatted || 'N/A'}</div>
                    </div>
                    <div class="dcf-mini-card">
                        <div class="dcf-mini-label">Discount rate</div>
                        <div class="dcf-mini-value">${dcf.assumptions?.discount_rate != null ? `${dcf.assumptions.discount_rate}%` : 'N/A'}</div>
                    </div>
                    <div class="dcf-mini-card">
                        <div class="dcf-mini-label">Terminal growth</div>
                        <div class="dcf-mini-value">${dcf.assumptions?.terminal_growth_rate != null ? `${dcf.assumptions.terminal_growth_rate}%` : 'N/A'}</div>
                    </div>
                    <div class="dcf-mini-card">
                        <div class="dcf-mini-label">Projection period</div>
                        <div class="dcf-mini-value">${dcf.projection_years || 5} years</div>
                    </div>
                    <div class="dcf-mini-card">
                        <div class="dcf-mini-label">Net cash / debt adjustment</div>
                        <div class="dcf-mini-value">${dcf.net_cash_adjustment_formatted || 'N/A'}</div>
                    </div>
                    <div class="dcf-mini-card">
                        <div class="dcf-mini-label">FCF source</div>
                        <div class="dcf-mini-value dcf-mini-value-sm">${humanizeLabel(dcf.data_sources?.free_cash_flow || 'N/A')}</div>
                    </div>
                </div>
            </div>

            <div class="dcf-chart-grid">
                <div class="dcf-chart-card">
                    <div class="dcf-chart-title">Projected free cash flow</div>
                    <div class="research-chart-frame research-chart-frame-md">
                        <canvas id="dcfProjectionChart"></canvas>
                    </div>
                </div>
                <div class="dcf-chart-card">
                    <div class="dcf-chart-title">Bull / Base / Bear fair value</div>
                    <div class="research-chart-frame research-chart-frame-md">
                        <canvas id="dcfScenarioChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="dcf-detail-grid">
                <div class="dcf-detail-card">
                    <div class="dcf-detail-title">What this means</div>
                    <ul class="dcf-bullet-list">
                        ${(dcf.explanation || []).map((item) => `<li>${item}</li>`).join('')}
                    </ul>
                </div>
                <div class="dcf-detail-card">
                    <div class="dcf-detail-title">Model inputs</div>
                    <div class="dcf-assumption-grid">
                        ${renderAssumptionTile('FCF growth', dcf.assumptions?.growth_rate != null ? `${dcf.assumptions.growth_rate}%` : 'N/A')}
                        ${renderAssumptionTile('Terminal value', dcf.base_case?.terminal_value_formatted || 'N/A')}
                        ${renderAssumptionTile('Enterprise value', dcf.base_case?.enterprise_value_formatted || 'N/A')}
                        ${renderAssumptionTile('Equity value', dcf.base_case?.equity_value_formatted || 'N/A')}
                    </div>
                </div>
                <div class="dcf-detail-card">
                    <div class="dcf-detail-title">Recent free cash flow history</div>
                    <div class="dcf-history-list">
                        ${historicalItems || '<div class="dcf-history-item"><span>History</span><strong>N/A</strong></div>'}
                    </div>
                </div>
            </div>

            ${renderDCFWarnings(dcf.warnings || [])}
        </div>
    `;

    renderDCFProjectionChart(dcf.base_case);
    renderDCFScenarioChart(dcf.scenario_values, dcf.current_price);
}

function renderSummaryListCard(title, items) {
    const list = (items || []).map((item) => `<li>${item}</li>`).join('');
    return `
        <div class="ai-summary-card">
            <div class="ai-summary-title">${title}</div>
            <ul>${list || '<li>No details available.</li>'}</ul>
        </div>
    `;
}

function renderPriceTargetRange(targets) {
    if (!targets || typeof targets !== 'object') {
        document.getElementById('priceTargetRange').innerHTML = '<div class="text-center">Price target data is unavailable.</div>';
        return;
    }
    const current = targets.current ?? 0;
    const low = targets.low ?? current;
    const mean = targets.mean ?? current;
    const high = targets.high ?? current;

    const min = Math.min(low, current, mean, high);
    const max = Math.max(low, current, mean, high);
    const scale = max - min || 1;

    const offset = (value) => `${((value - min) / scale) * 100}%`;

    document.getElementById('priceTargetRange').innerHTML = `
        <div class="target-range-values">
            <div><span>Low</span><strong>${formatCurrency(low)}</strong></div>
            <div><span>Average</span><strong>${formatCurrency(mean)}</strong></div>
            <div><span>High</span><strong>${formatCurrency(high)}</strong></div>
        </div>
        <div class="target-range-track">
            <div class="target-range-band"></div>
            <div class="target-marker target-low" style="left:${offset(low)}">Low</div>
            <div class="target-marker target-current" style="left:${offset(current)}">Now</div>
            <div class="target-marker target-mean" style="left:${offset(mean)}">Avg</div>
            <div class="target-marker target-high" style="left:${offset(high)}">High</div>
        </div>
        <div class="target-range-foot">
            <span>Potential move to average target</span>
            <strong class="${getColorClass(targets.upside_pct)}">${formatPercentage(targets.upside_pct)}</strong>
        </div>
    `;
}

function renderRecommendationSummary(summary) {
    const safeSummary = summary || {};
    document.getElementById('recommendationHeadline').innerHTML = `
        <div class="recommendation-headline">${safeSummary.headline || 'No recommendation summary available.'}</div>
        <div class="recommendation-consensus">Consensus: ${safeSummary.consensus || 'N/A'}</div>
    `;

    const canvasId = 'recommendationChart';
    const breakdown = (safeSummary.breakdown || []).filter((item) => Number(item.value) > 0);

    if (breakdown.length === 0) {
        if (researchState.charts[canvasId]) {
            researchState.charts[canvasId].destroy();
            delete researchState.charts[canvasId];
        }
        document.getElementById('recommendationHeadline').innerHTML += `
            <div class="recommendation-empty">There is not enough analyst recommendation data yet.</div>
        `;
        return;
    }

    createOrUpdateChart(canvasId, 'doughnut', {
        labels: breakdown.map((item) => item.label),
        datasets: [{
            data: breakdown.map((item) => item.value),
            backgroundColor: ['#16C784', '#2EBD85', '#8D98A7', '#4A90E2', '#EA3943'],
            borderColor: '#151C24',
            borderWidth: 4,
            hoverOffset: 6
        }]
    }, {
        plugins: {
            legend: {
                position: 'bottom'
            },
            tooltip: {
                callbacks: {
                    label: (context) => `${context.label}: ${context.parsed} analyst${context.parsed === 1 ? '' : 's'}`
                }
            }
        },
        cutout: '64%'
    });
}

function renderAnalystChanges(changes) {
    const container = document.getElementById('analystChanges');
    if (!changes || changes.length === 0) {
        container.innerHTML = '<div class="text-center">No recent analyst changes</div>';
        return;
    }

    container.innerHTML = changes.map((change) => `
        <div class="news-item analyst-change-item">
            <div class="news-title">${change.firm}</div>
            <div class="analyst-change-rating">${change.from_grade} -> ${change.to_grade}</div>
            <div class="news-date">${change.date}</div>
            ${change.current_price_target != null ? `<div class="analyst-target-line">Price target: ${formatCurrency(change.current_price_target)}</div>` : ''}
        </div>
    `).join('');
}

function renderRecentEarnings(earnings) {
    const container = document.getElementById('recentEarnings');
    if (!earnings || earnings.length === 0) {
        container.innerHTML = '<div class="text-center">No recent earnings data</div>';
        return;
    }

    container.innerHTML = earnings.map((item) => `
        <div class="news-item">
            <div class="news-title">${item.quarter}</div>
            <div class="news-date">${item.report_date && item.report_date !== 'N/A' ? formatDate(item.report_date) : 'Report date unavailable'}</div>
            <div>Actual EPS: ${item.actual_eps !== 'N/A' ? item.actual_eps : 'N/A'} | Estimate: ${item.estimate_eps !== 'N/A' ? item.estimate_eps : 'N/A'}</div>
            <div class="${getColorClass(item.surprise_pct)}">Surprise: ${formatPercentage(item.surprise_pct)}</div>
            <div class="${getColorClass(item.earnings_day_move)}">Price reaction: ${formatPercentage(item.earnings_day_move)}</div>
            <div class="${getColorClass(item.next_day_move)}">Next day move: ${formatPercentage(item.next_day_move)}</div>
            <div class="briefing-copy">${item.reason}</div>
        </div>
    `).join('');
}

function renderNewsList(news) {
    const container = document.getElementById('newsList');
    if (!news || news.length === 0) {
        container.innerHTML = '<div class="text-center">No recent news found</div>';
        return;
    }

    container.innerHTML = news.map((item) => `
        <div class="news-item">
            <div class="news-title">${item.title || 'No title'}</div>
            <div class="news-date">${item.providerPublishTime ? formatDateTime(new Date(item.providerPublishTime * 1000)) : 'Recent'}</div>
            <div class="briefing-copy">${item.publisher || item.provider?.displayName || 'Unknown source'}</div>
        </div>
    `).join('');
}

function renderPriceChart() {
    const priceHistory = researchState.data?.price_history || {};
    const series = researchState.priceView === 'performance'
        ? (priceHistory.performance_series || [])
        : (priceHistory.price_series || []);

    if (!series.length) {
        clearChartIfExists('pricePerformanceChart');
        return;
    }

    createOrUpdateChart('pricePerformanceChart', 'line', {
        labels: series.map((item) => item.label),
        datasets: [{
            label: researchState.priceView === 'performance' ? 'Return %' : 'Price',
            data: series.map((item) => item.value),
            borderColor: '#4A90E2',
            backgroundColor: 'rgba(74, 144, 226, 0.14)',
            fill: true,
            tension: 0.35,
            pointHoverBackgroundColor: '#4A90E2'
        }]
    }, {
        plugins: { legend: { display: false } },
        scales: {
            y: {
                ticks: {
                    callback: (value) => researchState.priceView === 'performance' ? `${value}%` : formatCurrency(value)
                }
            }
        }
    });
}

function renderComparisonChart() {
    const series = researchState.data?.price_history?.comparison_series || [];
    if (!series.length) {
        clearChartIfExists('comparisonChart');
        return;
    }
    createOrUpdateChart('comparisonChart', 'line', {
        labels: series.map((item) => item.label),
        datasets: [
            {
                label: researchState.data.symbol,
                data: series.map((item) => item.stock),
                borderColor: '#4A90E2',
                backgroundColor: 'rgba(74, 144, 226, 0.08)',
                fill: false,
                tension: 0.32
            },
            {
                label: 'S&P 500',
                data: series.map((item) => item.benchmark),
                borderColor: '#16C784',
                backgroundColor: 'rgba(22, 199, 132, 0.08)',
                fill: false,
                tension: 0.32,
                borderDash: [6, 4]
            }
        ]
    }, {
        scales: {
            y: {
                ticks: {
                    callback: (value) => `${value}%`
                }
            }
        }
    });
}

function renderFinancialChart() {
    const trends = researchState.data?.financial_trends || {};
    const labels = (trends.revenue_series || []).map((item) => item.label);
    if (!labels.length) {
        clearChartIfExists('financialTrendChart');
        return;
    }
    createOrUpdateChart('financialTrendChart', 'bar', {
        labels,
        datasets: [
            {
                label: 'Revenue',
                data: (trends.revenue_series || []).map((item) => item.value / 1_000_000_000),
                backgroundColor: 'rgba(74, 144, 226, 0.78)',
                borderRadius: 8,
                maxBarThickness: 34
            },
            {
                label: 'Net Income',
                data: (trends.net_income_series || []).map((item) => item.value / 1_000_000_000),
                backgroundColor: 'rgba(22, 199, 132, 0.74)',
                borderRadius: 8,
                maxBarThickness: 34
            }
        ]
    }, {
        scales: {
            y: {
                ticks: {
                    callback: (value) => `$${value}B`
                }
            }
        }
    });
}

function renderDCFProjectionChart(baseCase) {
    const projected = baseCase?.projected_cashflows || [];
    if (!projected.length) {
        clearChartIfExists('dcfProjectionChart');
        return;
    }
    createOrUpdateChart('dcfProjectionChart', 'bar', {
        labels: projected.map((item) => item.label),
        datasets: [
            {
                type: 'bar',
                label: 'Projected FCF',
                data: projected.map((item) => item.fcf / 1_000_000_000),
                backgroundColor: 'rgba(74, 144, 226, 0.8)',
                borderRadius: 10,
                maxBarThickness: 42
            },
            {
                type: 'line',
                label: 'Present value',
                data: projected.map((item) => item.present_value / 1_000_000_000),
                borderColor: '#16C784',
                backgroundColor: 'rgba(22, 199, 132, 0.14)',
                tension: 0.35,
                fill: false,
                pointRadius: 3,
                pointHoverRadius: 5
            }
        ]
    }, {
        scales: {
            y: {
                ticks: {
                    callback: (value) => `$${value}B`
                }
            }
        }
    });
}

function renderDCFScenarioChart(scenarios = [], currentPrice = null) {
    const labels = scenarios.map((item) => item.label);
    if (!labels.length) {
        clearChartIfExists('dcfScenarioChart');
        return;
    }
    createOrUpdateChart('dcfScenarioChart', 'bar', {
        labels,
        datasets: [
            {
                label: 'Fair value',
                data: scenarios.map((item) => item.fair_value),
                backgroundColor: ['rgba(234, 57, 67, 0.72)', 'rgba(74, 144, 226, 0.82)', 'rgba(22, 199, 132, 0.8)'],
                borderRadius: 12,
                maxBarThickness: 50
            },
            {
                type: 'line',
                label: 'Current price',
                data: labels.map(() => currentPrice),
                borderColor: '#B7C0CC',
                borderDash: [7, 5],
                borderWidth: 2,
                pointRadius: 0,
                fill: false
            }
        ]
    }, {
        scales: {
            y: {
                ticks: {
                    callback: (value) => formatCurrency(value)
                }
            }
        }
    });
}

function switchPriceView(view) {
    researchState.priceView = view;
    const priceButtons = Array.from(document.querySelectorAll('.chart-controls .btn-sm'))
        .filter((button) => (button.getAttribute('onclick') || '').includes('switchPriceView'));

    priceButtons.forEach((button) => button.classList.remove('active'));
    const targetButton = priceButtons
        .find((button) => button.textContent.toLowerCase().includes(view === 'price' ? 'price' : 'return'));
    if (targetButton) targetButton.classList.add('active');
    renderPriceChart();
}

function switchFinancialView() {
    renderFinancialChart();
}

function createOrUpdateChart(canvasId, type, data, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    if (researchState.charts[canvasId]) {
        researchState.charts[canvasId].destroy();
    }

    researchState.charts[canvasId] = new Chart(ctx, {
        type,
        data,
        options: mergeChartOptions(getResearchChartDefaults(), {
            responsive: true,
            maintainAspectRatio: false
        }, options)
    });
}

function clearChartIfExists(canvasId) {
    if (researchState.charts[canvasId]) {
        researchState.charts[canvasId].destroy();
        delete researchState.charts[canvasId];
    }
}

function destroyResearchCharts() {
    Object.values(researchState.charts).forEach((chart) => chart.destroy());
    researchState.charts = {};
}

function mergeChartOptions(...sources) {
    const result = {};

    sources.forEach((source) => {
        if (!source || typeof source !== 'object') return;
        Object.entries(source).forEach(([key, value]) => {
            if (Array.isArray(value)) {
                result[key] = value.slice();
                return;
            }

            if (value && typeof value === 'object') {
                result[key] = mergeChartOptions(result[key] || {}, value);
                return;
            }

            result[key] = value;
        });
    });

    return result;
}

function getResearchChartDefaults() {
    const styles = getComputedStyle(document.documentElement);
    const read = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
    return mergeChartOptions(RESEARCH_CHART_DEFAULTS, {
        plugins: {
            legend: {
                labels: {
                    color: read('--ink-base', '#223247')
                }
            },
            tooltip: {
                borderColor: read('--surface-outline', '#d8e1ea')
            }
        },
        scales: {
            x: {
                ticks: {
                    color: read('--gray', '#6f8194')
                }
            },
            y: {
                grid: {
                    color: read('--surface-outline', '#d8e1ea')
                },
                ticks: {
                    color: read('--gray', '#6f8194')
                }
            }
        }
    });
}

function truncateText(text, maxLength) {
    if (!text || text.length <= maxLength) return text;
    return `${text.slice(0, maxLength).trim()}...`;
}

function safelyRenderSection(label, renderFn) {
    try {
        renderFn();
    } catch (error) {
        console.error(`Error rendering ${label}:`, error);
    }
}

function renderAssumptionTile(label, value) {
    return `
        <div class="dcf-assumption-tile">
            <span>${label}</span>
            <strong>${value}</strong>
        </div>
    `;
}

function renderDCFWarnings(warnings = []) {
    if (!warnings.length) return '';
    return `
        <div class="dcf-warning-box">
            <div class="dcf-warning-title">Data notes</div>
            <ul class="dcf-bullet-list">
                ${warnings.map((item) => `<li>${item}</li>`).join('')}
            </ul>
        </div>
    `;
}

function getDCFBadgeClass(label) {
    const normalized = (label || '').toLowerCase();
    if (normalized.includes('under')) return 'dcf-header-badge-positive';
    if (normalized.includes('over')) return 'dcf-header-badge-negative';
    return 'dcf-header-badge-neutral';
}

function formatCompactCurrency(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    const number = Number(value);
    const absolute = Math.abs(number);
    if (absolute >= 1_000_000_000_000) return `${formatCompactSigned(number / 1_000_000_000_000)}T`;
    if (absolute >= 1_000_000_000) return `${formatCompactSigned(number / 1_000_000_000)}B`;
    if (absolute >= 1_000_000) return `${formatCompactSigned(number / 1_000_000)}M`;
    return formatCurrency(number);
}

function formatCompactSigned(value) {
    return `${value < 0 ? '-' : ''}$${Math.abs(value).toFixed(2)}`;
}

function humanizeLabel(text) {
    if (!text) return 'N/A';
    const normalized = String(text).replace(/_/g, ' ');
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

async function addResearchSymbolToWatchlist() {
    const symbol = researchState.symbol;
    if (!symbol) return;

    try {
        await fetchAPI('/api/watchlist/add', {
            method: 'POST',
            body: JSON.stringify({ symbol })
        });
        showToast(`${symbol} added to watchlist.`, 'success');
    } catch (error) {
        console.error('Error adding research symbol to watchlist:', error);
        showToast(`Could not add ${symbol}.`, 'error');
    }
}

async function removeResearchSymbolFromWatchlist() {
    const symbol = researchState.symbol;
    if (!symbol) return;

    try {
        const watchlist = await fetchAPI('/api/watchlist');
        const symbols = (watchlist || [])
            .map((item) => item.symbol)
            .filter((item) => item !== symbol);
        await fetchAPI('/api/watchlist', {
            method: 'POST',
            body: JSON.stringify({ watchlist: symbols })
        });
        showToast(`${symbol} removed from watchlist.`, 'success');
    } catch (error) {
        console.error('Error removing research symbol from watchlist:', error);
        showToast(`Could not remove ${symbol}.`, 'error');
    }
}

function setResearchFocus() {
    if (!researchState.symbol) return;
    researchState.preferredFocusSymbol = researchState.symbol;
    saveToLocalStorage('preferred_focus_symbol', researchState.symbol);
    renderResearchFocusToolbar(researchState.data?.snapshot || {});
    showToast(`${researchState.symbol} is now your active focus.`, 'success');
}

function clearResearchFocus() {
    researchState.preferredFocusSymbol = null;
    saveToLocalStorage('preferred_focus_symbol', null);
    renderResearchFocusToolbar(researchState.data?.snapshot || {});
    showToast('Active focus cleared.', 'info');
}
