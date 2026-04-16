// ============================================
// Trading Workspace JavaScript
// ============================================

let workspaceState = {
    payload: null,
    refreshHandle: null,
    charts: {},
    currentEditId: null,
    importedDraft: null
};

document.addEventListener('DOMContentLoaded', () => {
    initializeTradeForm();
    loadTradeWorkspace();
    workspaceState.refreshHandle = setInterval(loadTradeWorkspace, 30000);
    maybeOpenImportedTradeDraft();
});

function initializeTradeForm() {
    const tradeForm = document.getElementById('tradeForm');
    if (tradeForm) {
        tradeForm.addEventListener('submit', saveTrade);
    }
}

async function loadTradeWorkspace() {
    try {
        const payload = await fetchAPI('/api/trade_workspace');
        console.debug('Trade workspace payload:', payload);
        workspaceState.payload = payload;
        populateTradeTypeFilter(payload.trade_types || []);
        renderTradeWorkspace();
    } catch (error) {
        console.error('Error loading trade workspace:', error);
        document.getElementById('tradeWorkspaceMetrics').innerHTML = `
            <div class="trade-empty-state">
                <strong>Trading workspace unavailable</strong>
                <span>There was a problem loading your trades. Try refreshing in a moment.</span>
            </div>
        `;
    }
}

function renderTradeWorkspace() {
    const payload = workspaceState.payload || {};
    renderWorkspaceMetrics(payload.summary || {});
    renderLiveMonitor(payload.live_monitor || []);
    renderOpenTradesTable(filterTrades(payload.open_trades || [], false));
    renderClosedTradesTable(filterTrades(payload.closed_trades || [], true));
    renderPatternPanels(payload.patterns || {});
    renderCharts(payload.charts || {});
}

function populateTradeTypeFilter(types) {
    const select = document.getElementById('tradeTypeFilter');
    if (!select || select.dataset.loaded === 'true') return;
    const options = ['<option value="all">All Types</option>']
        .concat(types.map((type) => `<option value="${type}">${type}</option>`));
    select.innerHTML = options.join('');
    select.dataset.loaded = 'true';
}

function filterTrades(trades, closedOnly) {
    const statusFilter = document.getElementById('tradeStatusFilter')?.value || 'all';
    const typeFilter = document.getElementById('tradeTypeFilter')?.value || 'all';

    return trades.filter((trade) => {
        if (typeFilter !== 'all' && trade.trade_type !== typeFilter) return false;
        if (!closedOnly && statusFilter === 'closed') return false;
        if (closedOnly && statusFilter === 'open') return false;
        if (closedOnly && statusFilter === 'wins' && trade.outcome !== 'win') return false;
        if (closedOnly && statusFilter === 'losses' && trade.outcome !== 'loss') return false;
        return true;
    });
}

function renderWorkspaceMetrics(summary) {
    const container = document.getElementById('tradeWorkspaceMetrics');
    if (!container) return;

    if (!summary.show_metrics) {
        container.innerHTML = `
            <div class="trade-empty-state">
                <strong>${summary.empty_message || 'Start logging trades to unlock your performance metrics.'}</strong>
                <span>Add your first trade and this page will start tracking live P&amp;L, patterns, and post-trade reviews.</span>
            </div>
        `;
        return;
    }

    const metrics = [
        { label: 'Total P&L', value: formatCurrency(summary.total_pnl), tone: getColorClass(summary.total_pnl) },
        { label: 'Win Rate', value: `${summary.win_rate}%`, tone: '' },
        { label: 'Live Open P&L', value: formatCurrency(summary.live_open_pnl), tone: getColorClass(summary.live_open_pnl) },
        { label: 'Profit Factor', value: Number(summary.profit_factor || 0).toFixed(2), tone: '' },
        { label: 'Best Trade Type', value: summary.best_trade_type || 'N/A', tone: '' },
        { label: 'Avg Closed Duration', value: summary.average_duration_hours ? `${summary.average_duration_hours}h` : 'N/A', tone: '' }
    ];

    container.innerHTML = `
        <div class="trade-metrics-grid">
            ${metrics.map((item) => `
                <div class="stat-card research-stat-card">
                    <div>
                        <span class="stat-value ${item.tone}">${item.value}</span>
                        <span class="stat-label">${item.label}</span>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderLiveMonitor(trades) {
    const container = document.getElementById('liveTradeMonitor');
    const meta = document.getElementById('tradeMonitorMeta');
    if (!container || !meta) return;

    if (!trades.length) {
        container.innerHTML = `
            <div class="trade-empty-state">
                <strong>No open trades right now.</strong>
                <span>Add a trade and the live monitor will start tracking the position here.</span>
            </div>
        `;
        meta.textContent = 'No active positions';
        return;
    }

    const updatedAt = trades[0]?.monitoring?.updated_at ? formatDateTime(trades[0].monitoring.updated_at) : 'Waiting for quote refresh';
    meta.textContent = `Last update: ${updatedAt}`;

    container.innerHTML = trades.map((trade) => `
        <div class="live-monitor-card">
            <div class="live-monitor-top">
                <div>
                    <div class="watchlist-symbol">${trade.symbol}</div>
                    <div class="trade-chip-row">
                        <span class="trade-side-chip trade-side-${(trade.position_side || 'Long').toLowerCase()}">${trade.position_side || 'Long'}</span>
                        <span class="trade-type-chip">${trade.trade_type}</span>
                        <span class="status-badge status-${trade.status}">${trade.status === 'open' ? 'Open' : 'Closed'}</span>
                    </div>
                </div>
                <div class="live-monitor-price-block">
                    <div class="live-monitor-price">${trade.current_price_formatted || 'N/A'}</div>
                    <div class="live-monitor-pnl ${getColorClass(trade.live_profit_loss)}">
                        ${formatPercentage(trade.live_profit_pct)}
                    </div>
                </div>
            </div>
            <div class="live-monitor-primary-row">
                ${renderMonitorMetric('Entry', formatCurrency(trade.entry_price), true)}
                ${renderMonitorMetric('Live', trade.current_price_formatted || 'N/A', true)}
                ${renderMonitorMetric('P&L', formatCurrency(trade.live_profit_loss), true, getColorClass(trade.live_profit_loss))}
                ${renderMonitorMetric('Shares', formatNumber(trade.shares), true)}
            </div>
            <div class="live-monitor-secondary-row">
                <span><strong>Status:</strong> ${trade.status === 'open' ? 'Open' : 'Closed'}</span>
                <span><strong>Time exit:</strong> ${trade.force_close_datetime ? formatDateTime(trade.force_close_datetime) : 'Off'}</span>
                <span><strong>Entered:</strong> ${trade.entry_datetime ? formatDateTime(trade.entry_datetime) : 'N/A'}</span>
                <span><strong>To stop:</strong> ${trade.distance_to_stop != null ? formatCurrency(trade.distance_to_stop) : 'N/A'}</span>
                <span><strong>To target:</strong> ${trade.distance_to_target != null ? formatCurrency(trade.distance_to_target) : 'N/A'}</span>
            </div>
            <div class="trade-action-row">
                <button class="btn-secondary" onclick="editTrade(${trade.id})" type="button">Edit Trade</button>
                <button class="btn-primary" onclick="openCloseTradeModal(${trade.id})" type="button">Close Trade</button>
            </div>
        </div>
    `).join('');
}

function renderMonitorMetric(label, value, primary = false, tone = '') {
    return `
        <div class="monitor-metric ${primary ? 'monitor-metric-primary' : ''}">
            <span>${label}</span>
            <strong class="${tone}">${value}</strong>
        </div>
    `;
}

function renderOpenTradesTable(trades) {
    const tbody = document.getElementById('openTradesTableBody');
    if (!tbody) return;

    if (!trades.length) {
        tbody.innerHTML = '<tr><td colspan="13" class="text-center">No open trades match this filter.</td></tr>';
        return;
    }

    tbody.innerHTML = trades.map((trade) => `
        <tr>
            <td class="watchlist-symbol">${trade.symbol}</td>
            <td><span class="trade-side-chip trade-side-${(trade.position_side || 'Long').toLowerCase()}">${trade.position_side || 'Long'}</span></td>
            <td><span class="trade-type-chip">${trade.trade_type}</span></td>
            <td>${formatCurrency(trade.entry_price)}</td>
            <td>${trade.current_price_formatted || 'N/A'}</td>
            <td>${formatNumber(trade.shares)}</td>
            <td class="${getColorClass(trade.live_profit_loss)}">${formatCurrency(trade.live_profit_loss)}</td>
            <td class="${getColorClass(trade.live_profit_pct)}">${formatPercentage(trade.live_profit_pct)}</td>
            <td>${formatCurrency(trade.stop_loss)}<div class="table-subcopy">${trade.distance_to_stop != null ? `${formatCurrency(trade.distance_to_stop)} away` : ''}</div></td>
            <td>${formatCurrency(trade.take_profit)}<div class="table-subcopy">${trade.distance_to_target != null ? `${formatCurrency(trade.distance_to_target)} away` : ''}</div></td>
            <td>${trade.duration_label || 'N/A'}</td>
            <td><span class="status-badge status-${trade.status}">Open</span></td>
            <td class="trade-action-row">
                <button class="btn-icon" onclick="editTrade(${trade.id})" title="Edit"><i class="fas fa-edit"></i></button>
                <button class="btn-icon" onclick="openCloseTradeModal(${trade.id})" title="Close"><i class="fas fa-check-circle"></i></button>
                <button class="btn-icon" onclick="deleteTrade(${trade.id})" title="Delete"><i class="fas fa-trash"></i></button>
            </td>
        </tr>
    `).join('');
}

function renderClosedTradesTable(trades) {
    const tbody = document.getElementById('closedTradesTableBody');
    if (!tbody) return;

    if (!trades.length) {
        tbody.innerHTML = '<tr><td colspan="12" class="text-center">No closed trades match this filter yet.</td></tr>';
        return;
    }

    tbody.innerHTML = trades.map((trade) => `
        <tr>
            <td class="watchlist-symbol">${trade.symbol}</td>
            <td><span class="trade-side-chip trade-side-${(trade.position_side || 'Long').toLowerCase()}">${trade.position_side || 'Long'}</span></td>
            <td><span class="trade-type-chip">${trade.trade_type}</span></td>
            <td>${formatCurrency(trade.entry_price)}</td>
            <td>${formatCurrency(trade.exit_price)}</td>
            <td><span class="result-pill result-${trade.outcome || 'flat'}">${trade.result_label || 'Closed'}</span></td>
            <td class="${getColorClass(trade.profit_loss)}">${formatCurrency(trade.profit_loss)}</td>
            <td class="${getColorClass(trade.profit_pct)}">${formatPercentage(trade.profit_pct)}</td>
            <td>${humanizeExitReason(trade.close_reason || trade.exit_reason)}</td>
            <td>${trade.duration_label || 'N/A'}</td>
            <td>
                <div class="trade-review-snippet">${trade.review?.summary || 'Review pending.'}</div>
            </td>
            <td class="trade-action-row">
                <button class="btn-icon" onclick="editTrade(${trade.id})" title="Edit"><i class="fas fa-edit"></i></button>
                <button class="btn-secondary btn-delete-history" onclick="deleteTrade(${trade.id})" type="button">Delete From History</button>
            </td>
        </tr>
    `).join('');
}

function renderPatternPanels(patterns) {
    renderInsightsPanel(patterns);
    renderFutureSuggestionPanel(patterns.future_suggestion_profile || {});
    renderSetupLeaderboardPanel(patterns);
}

function renderInsightsPanel(patterns) {
    const container = document.getElementById('patternInsightsPanel');
    if (!container) return;

    const insightItems = patterns.pattern_insights || [];
    const winning = patterns.winning_factors || [];
    const losing = patterns.losing_factors || [];

    container.innerHTML = `
        <div class="insight-stack">
            <div class="insight-row">
                <strong>Pattern Insights</strong>
                <span>${insightItems.length ? insightItems.join(' ') : 'Log more closed trades to unlock stronger pattern insights.'}</span>
            </div>
            <div class="insight-row">
                <strong>Common Winning Factors</strong>
                <span>${winning.length ? winning.map((item) => `${item.label} (${item.count})`).join(' | ') : 'No winning pattern is strong enough yet.'}</span>
            </div>
            <div class="insight-row">
                <strong>Common Losing Factors</strong>
                <span>${losing.length ? losing.map((item) => `${item.label} (${item.count})`).join(' | ') : 'No losing pattern is strong enough yet.'}</span>
            </div>
        </div>
    `;
}

function renderFutureSuggestionPanel(profile) {
    const container = document.getElementById('futureSuggestionPanel');
    if (!container) return;

    container.innerHTML = `
        <div class="insight-stack">
            <div class="insight-row">
                <strong>Give more weight to</strong>
                <span>${profile.boost_factors?.length ? profile.boost_factors.join(' | ') : 'As you log more winners, this section will show what deserves more weight.'}</span>
            </div>
            <div class="insight-row">
                <strong>Watch out for</strong>
                <span>${profile.warning_factors?.length ? profile.warning_factors.join(' | ') : 'As you log more losses, this section will show your main warning signs.'}</span>
            </div>
        </div>
    `;
}

function renderSetupLeaderboardPanel(patterns) {
    const container = document.getElementById('setupLeaderboardPanel');
    if (!container) return;

    const bestTypes = patterns.best_trade_types || [];
    const worstTypes = patterns.worst_trade_types || [];
    const strongSetups = patterns.strongest_setups || [];
    const weakSetups = patterns.weakest_setups || [];

    container.innerHTML = `
        <div class="leaderboard-section">
            <div class="leaderboard-title">Best Performing Trade Types</div>
            ${renderLeaderboardList(bestTypes, (item) => `${formatCurrency(item.pnl)} | ${item.win_rate}% win rate`)}
        </div>
        <div class="leaderboard-section">
            <div class="leaderboard-title">Weakest Trade Types</div>
            ${renderLeaderboardList(worstTypes, (item) => `${formatCurrency(item.pnl)} | ${item.win_rate}% win rate`)}
        </div>
        <div class="leaderboard-section">
            <div class="leaderboard-title">My Strongest Setups</div>
            ${renderLeaderboardList(strongSetups, (item) => `${formatCurrency(item.pnl)} across ${item.count} trades`)}
        </div>
        <div class="leaderboard-section">
            <div class="leaderboard-title">My Weakest Setups</div>
            ${renderLeaderboardList(weakSetups, (item) => `${formatCurrency(item.pnl)} across ${item.count} trades`)}
        </div>
    `;
}

function renderLeaderboardList(items, formatter) {
    if (!items.length) {
        return '<div class="leaderboard-empty">Not enough closed trades yet.</div>';
    }

    return items.map((item) => `
        <div class="leaderboard-row">
            <strong>${item.label}</strong>
            <span>${formatter(item)}</span>
        </div>
    `).join('');
}

function renderCharts(charts) {
    renderChartSummaries(charts);
    renderEquityCurve(charts.equity_curve || []);
    renderTradeTypeChart(charts.trade_type_performance || []);
}

function renderChartSummaries(charts) {
    const equitySummary = document.getElementById('equityCurveSummary');
    const typeSummary = document.getElementById('tradeTypeSummary');
    const equitySeries = charts.equity_curve || [];
    const typeSeries = charts.trade_type_performance || [];

    if (equitySummary) {
        const latest = equitySeries[equitySeries.length - 1];
        equitySummary.innerHTML = latest
            ? `
                <div class="chart-summary-item">
                    <span>Net P&amp;L</span>
                    <strong class="${getColorClass(latest.value)}">${formatCurrency(latest.value)}</strong>
                </div>
            `
            : '<div class="chart-summary-item"><span>Net P&amp;L</span><strong>N/A</strong></div>';
    }

    if (typeSummary) {
        const ranked = [...typeSeries].sort((a, b) => (b.value || 0) - (a.value || 0));
        const best = ranked[0];
        typeSummary.innerHTML = best
            ? `
                <div class="chart-summary-item">
                    <span>Best Trade Type</span>
                    <strong>${best.label}</strong>
                </div>
                <div class="chart-summary-item">
                    <span>P&amp;L</span>
                    <strong class="${getColorClass(best.value)}">${formatCurrency(best.value)}</strong>
                </div>
            `
            : '<div class="chart-summary-item"><span>Best Trade Type</span><strong>N/A</strong></div>';
    }
}

function renderEquityCurve(series) {
    if (!series.length) {
        clearTradeChart('performanceChart');
        return;
    }

    const canvas = document.getElementById('performanceChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    clearTradeChart('performanceChart');
    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue('--primary').trim() || '#4A90E2';
    const muted = styles.getPropertyValue('--gray').trim() || '#8D98A7';
    const border = styles.getPropertyValue('--surface-outline').trim() || '#232A34';

    workspaceState.charts.performanceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: series.map((item) => item.label),
            datasets: [{
                label: 'Cumulative P&L',
                data: series.map((item) => item.value),
                borderColor: accent,
                backgroundColor: 'rgba(74, 144, 226, 0.12)',
                fill: true,
                tension: 0.35,
                borderWidth: 2.2,
                pointRadius: 0,
                pointHoverRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: {
                        color: border
                    },
                    ticks: {
                        color: muted,
                        callback: (value) => formatCurrency(value)
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        color: muted
                    }
                }
            }
        }
    });
}

function renderTradeTypeChart(series) {
    if (!series.length) {
        clearTradeChart('tradeTypeChart');
        return;
    }

    const canvas = document.getElementById('tradeTypeChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    clearTradeChart('tradeTypeChart');
    const styles = getComputedStyle(document.documentElement);
    const profit = styles.getPropertyValue('--success').trim() || '#16C784';
    const loss = styles.getPropertyValue('--danger').trim() || '#EA3943';
    const muted = styles.getPropertyValue('--gray').trim() || '#8D98A7';
    const border = styles.getPropertyValue('--surface-outline').trim() || '#232A34';

    workspaceState.charts.tradeTypeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: series.map((item) => item.label),
            datasets: [{
                label: 'P&L by Trade Type',
                data: series.map((item) => item.value),
                backgroundColor: series.map((item) => item.value >= 0 ? 'rgba(22, 199, 132, 0.78)' : 'rgba(234, 57, 67, 0.78)'),
                borderColor: series.map((item) => item.value >= 0 ? profit : loss),
                borderWidth: 1,
                borderRadius: 12,
                maxBarThickness: 52
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: {
                        color: border
                    },
                    ticks: {
                        color: muted,
                        callback: (value) => formatCurrency(value)
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        color: muted
                    }
                }
            }
        }
    });
}

function clearTradeChart(id) {
    if (workspaceState.charts[id]) {
        workspaceState.charts[id].destroy();
        delete workspaceState.charts[id];
    }
}

function openAddTradeModal(keepImportedDraft = false) {
    workspaceState.currentEditId = null;
    if (!keepImportedDraft) {
        workspaceState.importedDraft = null;
    }
    document.getElementById('modalTitle').textContent = 'Add New Trade';
    document.getElementById('tradeForm').reset();
    document.getElementById('entryDateTime').value = currentDateTimeLocal();
    document.getElementById('positionSide').value = 'Long';
    renderTradeImportNotice(keepImportedDraft ? workspaceState.importedDraft : null);
    syncTradeModalActions(null);
    document.getElementById('tradeModal').style.display = 'flex';
}

function editTrade(tradeId) {
    const trade = getTradeById(tradeId);
    if (!trade) return;

    workspaceState.currentEditId = tradeId;
    workspaceState.importedDraft = null;
    document.getElementById('modalTitle').textContent = 'Edit Trade';
    document.getElementById('tradeSymbol').value = trade.symbol || '';
    document.getElementById('positionSide').value = trade.position_side || 'Long';
    document.getElementById('tradeType').value = trade.trade_type || 'Other';
    document.getElementById('entryPrice').value = trade.entry_price ?? '';
    document.getElementById('shares').value = trade.shares ?? '';
    document.getElementById('stopLoss').value = trade.stop_loss ?? '';
    document.getElementById('takeProfit').value = trade.take_profit ?? '';
    document.getElementById('entryDateTime').value = trade.entry_datetime_display || '';
    document.getElementById('forceCloseDateTime').value = trade.force_close_datetime_display || '';
    document.getElementById('exitDateTime').value = trade.exit_datetime_display || '';
    document.getElementById('exitPrice').value = trade.exit_price ?? '';
    document.getElementById('closeReason').value = trade.close_reason || 'manual';
    renderTradeImportNotice(null);
    syncTradeModalActions(trade);
    document.getElementById('tradeModal').style.display = 'flex';
}

function openCloseTradeModal(tradeId) {
    const trade = getTradeById(tradeId);
    if (!trade) {
        showToast('Trade not found.', 'error');
        return;
    }

    editTrade(tradeId);
    document.getElementById('modalTitle').textContent = `Close ${trade.symbol}`;
    document.getElementById('exitDateTime').value = currentDateTimeLocal();

    if (!document.getElementById('exitPrice').value && trade.current_price != null) {
        document.getElementById('exitPrice').value = Number(trade.current_price).toFixed(2);
    }

    showToast('Add the exit price, then click Close Trade to finish the position.', 'info');
}

async function submitTradeClose() {
    const tradeId = workspaceState.currentEditId;
    const trade = getTradeById(tradeId);
    if (!trade || trade.status !== 'open') {
        showToast('Only open trades can be closed here.', 'error');
        return;
    }

    const exitPriceValue = document.getElementById('exitPrice').value;
    const exitPrice = exitPriceValue ? parseFloat(exitPriceValue) : null;
    if (exitPrice == null || Number.isNaN(exitPrice)) {
        showToast('Enter an exit price before closing the trade.', 'error');
        return;
    }

    const exitDateTime = document.getElementById('exitDateTime').value || currentDateTimeLocal();
    const closeReason = document.getElementById('closeReason').value || 'manual';

    try {
        await fetchAPI(`/api/trades/${tradeId}/close`, {
            method: 'POST',
            body: JSON.stringify({
                exit_price: exitPrice,
                exit_datetime: exitDateTime,
                close_reason: closeReason
            })
        });
        showToast('Trade closed successfully.', 'success');
        closeTradeModal();
        await loadTradeWorkspace();
    } catch (error) {
        console.error('Error closing trade:', error);
        showToast('Could not close trade.', 'error');
    }
}

async function deleteTrade(tradeId) {
    if (!confirm('Delete this trade from your journal?')) return;

    try {
        await fetchAPI(`/api/trades/${tradeId}`, { method: 'DELETE' });
        showToast('Trade deleted.', 'success');
        await loadTradeWorkspace();
    } catch (error) {
        console.error('Error deleting trade:', error);
        showToast('Could not delete trade.', 'error');
    }
}

async function saveTrade(event) {
    event.preventDefault();
    const existingTrade = workspaceState.currentEditId ? getTradeById(workspaceState.currentEditId) : null;

    const payload = {
        symbol: document.getElementById('tradeSymbol').value.trim().toUpperCase(),
        position_side: document.getElementById('positionSide').value,
        trade_type: document.getElementById('tradeType').value,
        entry_price: parseFloat(document.getElementById('entryPrice').value),
        shares: parseFloat(document.getElementById('shares').value),
        stop_loss: parseFloat(document.getElementById('stopLoss').value),
        take_profit: parseFloat(document.getElementById('takeProfit').value),
        entry_datetime: document.getElementById('entryDateTime').value,
        force_close_datetime: document.getElementById('forceCloseDateTime').value || null,
        exit_datetime: document.getElementById('exitDateTime').value || null,
        exit_price: document.getElementById('exitPrice').value ? parseFloat(document.getElementById('exitPrice').value) : null,
        close_reason: document.getElementById('closeReason').value,
        thesis: workspaceState.importedDraft?.thesis ?? existingTrade?.thesis ?? '',
        setup_notes: workspaceState.importedDraft?.setup_notes ?? existingTrade?.setup_notes ?? '',
        notes: workspaceState.importedDraft?.notes ?? existingTrade?.notes ?? '',
        earnings_date: workspaceState.importedDraft?.earnings_date ?? existingTrade?.earnings_date ?? null,
        setup_profile: workspaceState.importedDraft?.setup_profile ?? existingTrade?.setup_profile ?? null,
        trade_features: workspaceState.importedDraft?.trade_features ?? existingTrade?.trade_features ?? null,
        trade_insights: workspaceState.importedDraft?.trade_insights ?? existingTrade?.trade_insights ?? null,
        score_payload: workspaceState.importedDraft?.score_payload ?? existingTrade?.score_payload ?? null
    };

    if (!payload.symbol || !payload.entry_price || !payload.shares || !payload.stop_loss || !payload.take_profit || !payload.entry_datetime) {
        showToast('Fill in the required trade fields first.', 'error');
        return;
    }

    try {
        if (workspaceState.currentEditId) {
            payload.status = payload.exit_price != null ? 'closed' : 'open';
            await fetchAPI(`/api/trades/${workspaceState.currentEditId}`, {
                method: 'PUT',
                body: JSON.stringify(payload)
            });
            showToast('Trade updated.', 'success');
        } else {
            await fetchAPI('/api/trades', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            showToast('Trade saved.', 'success');
        }
        closeTradeModal();
        clearImportedTradeDraft();
        await loadTradeWorkspace();
    } catch (error) {
        console.error('Error saving trade:', error);
        showToast('Could not save trade.', 'error');
    }
}

function closeTradeModal() {
    document.getElementById('tradeModal').style.display = 'none';
    workspaceState.currentEditId = null;
    renderTradeImportNotice(null);
    syncTradeModalActions(null);
}

function currentDateTimeLocal() {
    const now = new Date();
    const offset = now.getTimezoneOffset();
    const local = new Date(now.getTime() - offset * 60000);
    return local.toISOString().slice(0, 16);
}

function humanizeExitReason(value) {
    if (!value) return 'Manual';
    return value.replace(/_/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase());
}

function maybeOpenImportedTradeDraft() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('openTradeDraft') !== '1') return;

    const draft = loadFromLocalStorage('trade_draft_from_earnings');
    if (!draft || !draft.symbol) return;

    workspaceState.importedDraft = draft;
    openAddTradeModal(true);
    prefillTradeModalFromDraft(draft);

    params.delete('openTradeDraft');
    const nextQuery = params.toString();
    const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}`;
    window.history.replaceState({}, '', nextUrl);
}

function prefillTradeModalFromDraft(draft) {
    document.getElementById('tradeSymbol').value = draft.symbol || '';
    document.getElementById('positionSide').value = draft.position_side || 'Long';
    document.getElementById('tradeType').value = draft.trade_type || 'Earnings';
    document.getElementById('entryPrice').value = draft.entry_price ?? '';
    document.getElementById('entryDateTime').value = currentDateTimeLocal();
    renderTradeImportNotice(draft);
}

function getTradeById(tradeId) {
    const trades = (workspaceState.payload?.open_trades || []).concat(workspaceState.payload?.closed_trades || []);
    return trades.find((item) => item.id === tradeId) || null;
}

function syncTradeModalActions(trade) {
    const closeButton = document.getElementById('closeTradeActionBtn');
    if (!closeButton) return;

    const showCloseButton = Boolean(trade && trade.status === 'open');
    closeButton.style.display = showCloseButton ? 'inline-flex' : 'none';

    document.querySelectorAll('.trade-close-only').forEach((group) => {
        group.style.display = showCloseButton ? 'grid' : 'none';
    });
}

function renderTradeImportNotice(draft) {
    const notice = document.getElementById('tradeImportNotice');
    if (!notice) return;

    if (!draft) {
        notice.style.display = 'none';
        notice.innerHTML = '';
        return;
    }

    const snapshot = draft.analysis_snapshot || {};
    notice.innerHTML = `
        <strong>Imported from earnings analysis</strong>
        <span>${draft.symbol} came in with a score of ${snapshot.score ?? 'N/A'} (${snapshot.label || 'N/A'}) and ${Number(snapshot.confidence_score || 0).toFixed(1)}% confidence.</span>
        <span>${snapshot.memory_summary || 'This draft includes the setup snapshot and memory notes from the earnings page.'}</span>
    `;
    notice.style.display = 'block';
}

function clearImportedTradeDraft() {
    workspaceState.importedDraft = null;
    saveToLocalStorage('trade_draft_from_earnings', null);
}
