// ============================================
// Dashboard JavaScript
// Fast snapshot load + live quote polling
// ============================================

let dashboardState = {
    earningsData: [],
    todayEarnings: [],
    tomorrowEarnings: [],
    featuredUpcoming: [],
    remainingUpcoming: [],
    showAllToday: false,
    showAllTomorrow: false,
    showAllUpcoming: false,
    watchlist: [],
    preferredFocusSymbol: null,
    previousQuotes: {},
    earningsDebugEnabled: false,
    earningsDebug: null,
    earningsDebugFilter: 'all'
};

let marketDateNoteTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    initializeDashboard();
});

async function initializeDashboard() {
    dashboardState.earningsDebugEnabled = loadFromLocalStorage('earnings_debug_enabled', false) === true;
    dashboardState.earningsDebugFilter = loadFromLocalStorage('earnings_debug_filter', 'all') || 'all';
    dashboardState.preferredFocusSymbol = loadFromLocalStorage('preferred_focus_symbol', null);
    syncEarningsDebugToggle();
    syncEarningsDebugFilterButtons();
    setupDashboardInputs();
    await loadDashboardSnapshot();
    setTimeout(() => {
        refreshLiveQuotes();
    }, 100);
    setInterval(refreshLiveQuotes, 5000);
}

function setupDashboardInputs() {
    const addInput = document.getElementById('addWatchlistSymbol');
    if (addInput) {
        addInput.addEventListener('keypress', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                addWatchlistSymbol();
            }
        });
    }

    const heroInput = document.getElementById('dashboardHeroSearch');
    if (heroInput) {
        heroInput.addEventListener('keypress', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                searchHeroStock();
            }
        });
    }
}

async function loadDashboardSnapshot() {
    try {
        const debugQuery = dashboardState.earningsDebugEnabled ? '?earnings_debug=1' : '';
        const snapshot = await fetchAPI(`/api/dashboard_snapshot${debugQuery}`);
        dashboardState.todayEarnings = snapshot.today_earnings || [];
        dashboardState.tomorrowEarnings = snapshot.tomorrow_earnings || [];
        dashboardState.earningsData = [
            ...dashboardState.todayEarnings,
            ...dashboardState.tomorrowEarnings,
            ...(snapshot.featured_week_earnings || []),
            ...(snapshot.remaining_week_earnings || [])
        ];
        dashboardState.featuredUpcoming = snapshot.featured_week_earnings || [];
        dashboardState.remainingUpcoming = snapshot.remaining_week_earnings || [];
        dashboardState.watchlist = snapshot.watchlist || [];
        dashboardState.earningsDebug = snapshot.earnings_debug || null;

        document.getElementById('earningsCount').textContent = snapshot.upcoming_earnings_count || dashboardState.earningsData.length;
        document.getElementById('watchlistCount').textContent = dashboardState.watchlist.length;
        document.getElementById('openTradesCount').textContent = snapshot.open_trades_count || 0;
        startMarketDateNoteClock();

        renderTodayEarnings();
        renderTomorrowEarnings();
        renderUpcomingEarnings();
        renderEarningsDebug();
        renderWatchlist(snapshot.watchlist || []);
        renderFeaturedStock();
        renderDashboardAiInsight();
    } catch (error) {
        console.error('Error loading dashboard snapshot:', error);
        document.querySelectorAll('.earnings-list').forEach((el) => {
            el.innerHTML = '<div class="earnings-item"><span>Unable to load earnings right now</span></div>';
        });
        const container = document.getElementById('watchlistContainer');
        if (container) {
            container.innerHTML = '<div class="earnings-item"><span>Unable to load watchlist right now</span></div>';
        }
        const aiPanel = document.getElementById('dashboardAiInsight');
        if (aiPanel) {
            aiPanel.innerHTML = '<div class="trade-empty-state"><strong>AI market read unavailable.</strong><span>Refresh the dashboard to rebuild your personal overview.</span></div>';
        }
    }
}

function syncEarningsDebugToggle() {
    const button = document.getElementById('earningsDebugToggleBtn');
    if (!button) return;
    button.classList.toggle('active', dashboardState.earningsDebugEnabled);
    button.title = dashboardState.earningsDebugEnabled ? 'Disable earnings audit mode' : 'Enable earnings audit mode';
}

function syncEarningsDebugFilterButtons() {
    document.querySelectorAll('[data-earnings-debug-filter]').forEach((button) => {
        button.classList.toggle('active', button.dataset.earningsDebugFilter === dashboardState.earningsDebugFilter);
    });
}

function updateMarketDateNote() {
    const dateNote = document.getElementById('marketDateNote');
    if (!dateNote || typeof formatMarketClock !== 'function') return;
    dateNote.textContent = formatMarketClock().fullLabel;
}

function startMarketDateNoteClock() {
    clearInterval(marketDateNoteTimer);
    updateMarketDateNote();
    marketDateNoteTimer = setInterval(updateMarketDateNote, 1000);
}

function renderEarningsList(earnings, containerId, label) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!earnings || earnings.length === 0) {
        container.innerHTML = `
            <div class="earnings-item">
                <span>No earnings names are available for ${label} right now.</span>
            </div>
        `;
        return;
    }

    container.innerHTML = earnings.map((earning) => `
        <div class="earnings-item" data-symbol="${earning.symbol}" onclick="searchStock('${earning.symbol}')">
            <div class="earnings-stack">
                <div class="earnings-symbol">${earning.symbol}</div>
                <div class="earnings-company">${earning.company || ''}</div>
                <div class="earnings-date">${formatDate(earning.date)}</div>
                ${earning.exchange ? `<div class="earnings-submeta">${earning.exchange}</div>` : ''}
                ${earning.interest_label ? `<div class="earnings-interest-tag">${earning.interest_label}</div>` : ''}
            </div>
            <div class="earnings-stack" style="text-align: right;">
                <div class="earnings-time">${earning.time || 'Unknown'}</div>
                <div class="earnings-submeta earnings-status-label">${earning.is_confirmed ? 'Confirmed' : 'Unconfirmed'}</div>
                <div class="earnings-price earnings-live-price">${earning.price_formatted || 'N/A'}</div>
                <div class="earnings-submeta earnings-live-change ${getColorClass(earning.daily_change_pct)}">${earning.daily_change_pct_formatted || 'N/A'}</div>
            </div>
        </div>
    `).join('');
}

function renderUpcomingEarnings() {
    const items = dashboardState.showAllUpcoming
        ? [...dashboardState.featuredUpcoming, ...dashboardState.remainingUpcoming]
        : dashboardState.featuredUpcoming;
    const tbody = document.getElementById('upcomingEarningsTableBody');
    if (tbody) {
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center">No weekly earnings names are available right now.</td></tr>';
        } else {
            tbody.innerHTML = items.map((earning) => `
                <tr data-symbol="${earning.symbol}" onclick="searchStock('${earning.symbol}')" class="dashboard-catalyst-row">
                    <td class="watchlist-symbol">${earning.symbol}</td>
                    <td>${earning.company || 'N/A'}</td>
                    <td>${formatDate(earning.date)}</td>
                    <td>${earning.time || 'Unknown'}</td>
                    <td>${earning.price_formatted || 'N/A'}</td>
                    <td class="${getColorClass(earning.daily_change_pct)}">${earning.daily_change_pct_formatted || 'N/A'}</td>
                </tr>
            `).join('');
        }
    }

    const toggleBtn = document.getElementById('toggleUpcomingBtn');
    if (!toggleBtn) return;

    if (dashboardState.remainingUpcoming.length === 0) {
        toggleBtn.style.display = 'none';
        return;
    }

    toggleBtn.style.display = 'inline-flex';
    toggleBtn.textContent = dashboardState.showAllUpcoming
        ? 'Show Less'
        : `Show More (${dashboardState.remainingUpcoming.length})`;
}

function getFeaturedStock() {
    const preferred = dashboardState.preferredFocusSymbol;
    const candidates = [
        ...(dashboardState.watchlist || []),
        ...(dashboardState.todayEarnings || []),
        ...(dashboardState.tomorrowEarnings || []),
        ...(dashboardState.featuredUpcoming || []),
        ...(dashboardState.remainingUpcoming || [])
    ];

    if (preferred) {
        const exact = candidates.find((item) => item.symbol === preferred);
        if (exact) return exact;
    }

    return candidates[0] || null;
}

function renderFeaturedStock() {
    const panel = document.getElementById('featuredStockPanel');
    if (!panel) return;

    const stock = getFeaturedStock();
    if (!stock) {
        panel.innerHTML = `
            <div class="dashboard-feature-topline">Selected Focus</div>
            <div class="dashboard-feature-symbol">No active focus</div>
            <div class="dashboard-feature-company">Add a watchlist symbol or load earnings names to populate this panel.</div>
            <div class="dashboard-feature-price">--</div>
            <div class="dashboard-feature-move">--</div>
            <div class="focus-control-row">
                <button class="btn-secondary btn-compact" type="button" onclick="focusFromHeroSearch()">Change Focus</button>
            </div>
        `;
        return;
    }

    const nextDate = stock.next_earnings || stock.date || null;
    const moveValue = stock.daily_change_pct_number ?? stock.daily_change_pct_raw ?? stock.daily_change_pct;
    const moveClass = getColorClass(moveValue);
    const source = stock.is_watchlist ? 'Watchlist' : (stock.date || stock.next_earnings ? 'Earnings calendar' : 'Workspace');
    const isPreferredFocus = dashboardState.preferredFocusSymbol === stock.symbol;
    const isWatchlist = (dashboardState.watchlist || []).some((item) => item.symbol === stock.symbol);

    panel.innerHTML = `
        <div class="dashboard-feature-topline">Selected Focus</div>
        <div class="dashboard-feature-symbol">${stock.symbol}</div>
        <div class="dashboard-feature-company">${stock.company || 'Focused market name from your workspace'}</div>
        <div class="dashboard-feature-price">${stock.current_price || stock.price_formatted || 'N/A'}</div>
        <div class="dashboard-feature-move ${moveClass}">${stock.daily_change_pct_formatted || stock.daily_change_pct || '--'}</div>
        <div class="dashboard-feature-meta">
            <div>
                <span>Session</span>
                <strong>${stock.earnings_time || stock.time || 'Live'}</strong>
            </div>
            <div>
                <span>Next report</span>
                <strong>${nextDate ? formatDate(nextDate) : 'TBD'}</strong>
            </div>
            <div>
                <span>Context</span>
                <strong>${source}</strong>
            </div>
        </div>
        <div class="focus-control-row">
            ${isWatchlist
                ? `<button class="btn-secondary btn-compact" type="button" onclick="removeCurrentFocusFromWatchlist()">Remove Stock</button>`
                : `<button class="btn-secondary btn-compact" type="button" onclick="addCurrentFocusToWatchlist()">Add Stock</button>`}
            ${isPreferredFocus
                ? `<button class="btn-secondary btn-compact" type="button" onclick="clearDashboardFocus()">Remove Focus</button>`
                : `<button class="btn-primary btn-compact" type="button" onclick="setDashboardFocus('${stock.symbol}')">Set Focus</button>`}
            <button class="btn-secondary btn-compact" type="button" onclick="focusFromHeroSearch()">Change Focus</button>
            <button class="btn-secondary btn-compact" type="button" onclick="openFocusResearch('${stock.symbol}')">Open Research</button>
        </div>
    `;
}

function renderDashboardAiInsight() {
    const panel = document.getElementById('dashboardAiInsight');
    if (!panel) return;

    const featured = getFeaturedStock();
    const todayCount = dashboardState.todayEarnings?.length || 0;
    const tomorrowCount = dashboardState.tomorrowEarnings?.length || 0;
    const watchlist = dashboardState.watchlist || [];
    const watchlistSymbols = new Set(watchlist.map((item) => item.symbol));
    const overlaps = dashboardState.earningsData.filter((item) => watchlistSymbols.has(item.symbol)).slice(0, 4);
    const strongestMover = [...watchlist]
        .filter((item) => item.daily_change_pct_number != null || item.daily_change_pct)
        .sort((a, b) => Math.abs(parseFloat(b.daily_change_pct_number ?? b.daily_change_pct ?? 0)) - Math.abs(parseFloat(a.daily_change_pct_number ?? a.daily_change_pct ?? 0)))[0];

    const opening = featured
        ? `${featured.symbol} is the clearest lead name on the dashboard right now. It sits closest to the intersection of price action, watchlist relevance, and upcoming catalyst flow.`
        : 'The dashboard is ready, but it still needs a stronger active focus from either your watchlist or the earnings calendar.';
    const catalystSentence = `There are ${todayCount} names reporting today and ${tomorrowCount} more lined up for tomorrow, so the near-term earnings calendar is still active enough to matter.`;
    const overlapSentence = overlaps.length
        ? `${overlaps.map((item) => item.symbol).join(', ')} already overlap between your watchlist and the earnings calendar, which gives you a cleaner shortlist than scanning the full market.`
        : 'There is no strong overlap yet between your watchlist and the near earnings calendar, so the better setups may still come from fresh review rather than existing conviction names.';
    const tapeSentence = strongestMover
        ? `${strongestMover.symbol} is showing the strongest visible move on your watchlist, making it a useful pulse check for current risk appetite and momentum.`
        : 'Live pricing is still filling in, so the tape read will become more useful as fresh quotes update across the watchlist.';

    panel.innerHTML = `
        <div class="dashboard-ai-shell">
            <div class="dashboard-ai-kicker">AI market read</div>
            <div class="dashboard-ai-body">
                <p>${opening}</p>
                <p>${catalystSentence}</p>
                <p>${overlapSentence}</p>
                <p>${tapeSentence}</p>
            </div>
        </div>
    `;
}

function searchHeroStock() {
    const input = document.getElementById('dashboardHeroSearch');
    const symbol = input?.value?.trim()?.toUpperCase();
    if (!symbol) {
        showToast('Enter a stock symbol first', 'error');
        return;
    }
    searchStock(symbol);
}

function focusFromHeroSearch() {
    const input = document.getElementById('dashboardHeroSearch');
    const symbol = input?.value?.trim()?.toUpperCase();
    if (!symbol) {
        showToast('Enter a ticker in the search box to change focus.', 'error');
        return;
    }
    setDashboardFocus(symbol);
    searchStock(symbol);
}

function setDashboardFocus(symbol) {
    const normalized = String(symbol || '').trim().toUpperCase();
    if (!normalized) return;
    dashboardState.preferredFocusSymbol = normalized;
    saveToLocalStorage('preferred_focus_symbol', normalized);
    renderFeaturedStock();
    renderDashboardAiInsight();
    showToast(`${normalized} is now your active focus.`, 'success');
}

function clearDashboardFocus() {
    dashboardState.preferredFocusSymbol = null;
    saveToLocalStorage('preferred_focus_symbol', null);
    renderFeaturedStock();
    renderDashboardAiInsight();
    showToast('Active focus cleared.', 'info');
}

async function addCurrentFocusToWatchlist() {
    const stock = getFeaturedStock();
    const symbol = stock?.symbol;
    if (!symbol) {
        showToast('No focus stock available.', 'error');
        return;
    }

    try {
        await fetchAPI('/api/watchlist/add', {
            method: 'POST',
            body: JSON.stringify({ symbol })
        });
        await loadDashboardSnapshot();
        showToast(`${symbol} added to watchlist.`, 'success');
    } catch (error) {
        console.error('Error adding focused stock to watchlist:', error);
        showToast(`Could not add ${symbol}.`, 'error');
    }
}

async function removeCurrentFocusFromWatchlist() {
    const stock = getFeaturedStock();
    const symbol = stock?.symbol;
    if (!symbol) {
        showToast('No focus stock available.', 'error');
        return;
    }
    await removeFromWatchlist(symbol);
}

function openFocusResearch(symbol) {
    window.location.href = `/news?symbol=${encodeURIComponent(symbol)}`;
}

function renderEarningsDebug() {
    const card = document.getElementById('earningsDebugCard');
    const content = document.getElementById('earningsDebugContent');
    if (!card || !content) return;

    if (!dashboardState.earningsDebugEnabled) {
        card.style.display = 'none';
        content.innerHTML = '';
        return;
    }

    card.style.display = 'block';
    const debug = dashboardState.earningsDebug;
    if (!debug || !debug.audit_by_day) {
        content.innerHTML = '<div class="trade-empty-state"><strong>No earnings audit data returned.</strong><span>Refresh again to inspect the verification pipeline.</span></div>';
        return;
    }

    const daySections = Object.entries(debug.audit_by_day)
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([day, entries]) => {
            const filteredEntries = entries.filter(matchesEarningsDebugFilter);
            const sortedEntries = [...filteredEntries].sort((a, b) => {
                if (a.stage === 'included' && b.stage !== 'included') return -1;
                if (a.stage !== 'included' && b.stage === 'included') return 1;
                return (a.symbol || '').localeCompare(b.symbol || '');
            });
            return `
                <div class="leaderboard-section">
                    <div class="leaderboard-title">${formatDate(day)} | ${sortedEntries.length} matching audit entries</div>
                    ${sortedEntries.length ? sortedEntries.map((entry) => `
                        <div class="leaderboard-row">
                            <strong>${entry.symbol || 'N/A'}${entry.company ? ` - ${entry.company}` : ''}</strong>
                            <span>${buildEarningsAuditSummary(entry)}</span>
                        </div>
                    `).join('') : '<div class="leaderboard-empty">No candidates were audited for this day.</div>'}
                </div>
            `;
        }).filter(Boolean).join('');

    content.innerHTML = `
        <div class="insight-stack">
            <div class="insight-row">
                <strong>Audit Rules</strong>
                <span>Top ${debug.candidate_limit_per_day} candidates per day are verified. Minimum market cap ${formatCurrency(debug.min_market_cap)} or average volume ${formatNumber(debug.min_average_volume)} unless the name is featured or on your watchlist.</span>
            </div>
        </div>
        ${daySections || '<div class="leaderboard-empty">No debug entries returned.</div>'}
    `;
}

function matchesEarningsDebugFilter(entry) {
    const filter = dashboardState.earningsDebugFilter || 'all';
    if (filter === 'all') return true;
    if (filter === 'included') return entry.stage === 'included';
    if (filter === 'rejected') return entry.stage !== 'included';
    if (filter === 'mismatch') {
        const calendarDate = entry.calendar_date || '';
        const verifiedDate = entry.verified_date || '';
        return Boolean(calendarDate || verifiedDate) && calendarDate !== verifiedDate;
    }
    return true;
}

function buildEarningsAuditSummary(entry) {
    const parts = [entry.reason];
    if (entry.exchange) {
        parts.push(entry.exchange);
    }

    if (entry.calendar_date || entry.verified_date) {
        const calendarLabel = entry.calendar_date ? `calendar ${entry.calendar_date}` : 'calendar n/a';
        const verifiedLabel = entry.verified_date ? `verified ${entry.verified_date}` : 'verified none';
        parts.push(`${calendarLabel} vs ${verifiedLabel}`);
    }

    if (entry.calendar_time_raw || entry.verified_time) {
        const calendarTime = entry.calendar_time_raw || 'unknown';
        const verifiedTime = entry.verified_time || 'unknown';
        parts.push(`time ${calendarTime} vs ${verifiedTime}`);
    }

    return parts.join(' | ');
}

function getPreviewCount(items) {
    return Math.min(3, items.length);
}

function renderTodayEarnings() {
    const allItems = dashboardState.todayEarnings || [];
    const previewCount = getPreviewCount(allItems);
    const items = dashboardState.showAllToday ? allItems : allItems.slice(0, previewCount);
    renderEarningsList(items, 'todayEarningsList', 'today');
    toggleDashboardSessionGroup('todayEarningsList', allItems.length > 0);
    updateExpandButton('toggleTodayBtn', allItems.length - previewCount, dashboardState.showAllToday);
}

function renderTomorrowEarnings() {
    const allItems = dashboardState.tomorrowEarnings || [];
    const previewCount = getPreviewCount(allItems);
    const items = dashboardState.showAllTomorrow ? allItems : allItems.slice(0, previewCount);
    renderEarningsList(items, 'tomorrowEarningsList', 'tomorrow');
    toggleDashboardSessionGroup('tomorrowEarningsList', allItems.length > 0);
    updateExpandButton('toggleTomorrowBtn', allItems.length - previewCount, dashboardState.showAllTomorrow);
}

function toggleDashboardSessionGroup(containerId, hasContent) {
    const container = document.getElementById(containerId);
    const group = container?.closest('.dashboard-session-group');
    if (!group) return;

    group.style.display = hasContent ? '' : 'none';

    const panel = document.querySelector('.dashboard-market-panel');
    if (!panel) return;
    const visibleGroups = Array.from(panel.querySelectorAll('.dashboard-session-group')).filter((item) => item.style.display !== 'none');
    panel.style.display = visibleGroups.length ? '' : 'none';
}

function updateExpandButton(buttonId, hiddenCount, expanded) {
    const button = document.getElementById(buttonId);
    if (!button) return;

    if (hiddenCount <= 0) {
        button.style.display = 'none';
        return;
    }

    button.style.display = 'inline-flex';
    button.textContent = expanded ? 'Show Less' : `Show More (${hiddenCount})`;
}

function toggleTodayEarnings() {
    dashboardState.showAllToday = !dashboardState.showAllToday;
    renderTodayEarnings();
}

function toggleTomorrowEarnings() {
    dashboardState.showAllTomorrow = !dashboardState.showAllTomorrow;
    renderTomorrowEarnings();
}

function toggleUpcomingEarnings() {
    dashboardState.showAllUpcoming = !dashboardState.showAllUpcoming;
    renderUpcomingEarnings();
}

function renderWatchlist(watchlist) {
    const container = document.getElementById('watchlistContainer');
    if (!container) return;

    dashboardState.watchlist = watchlist;

    if (!watchlist || watchlist.length === 0) {
        container.innerHTML = '<div class="earnings-item"><span>No stocks in your watchlist yet</span></div>';
        return;
    }

    const movementClass = (value) => value === true ? 'watchlist-positive' : value === false ? 'watchlist-negative' : '';

    const html = `
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Price</th>
                    <th>Move</th>
                    <th>Next Earnings</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                ${watchlist.map((stock) => `
                    <tr data-symbol="${stock.symbol}">
                        <td class="watchlist-symbol" onclick="searchStock('${stock.symbol}')" style="cursor:pointer;">${stock.symbol}</td>
                        <td class="watchlist-price-cell">${stock.current_price}</td>
                        <td class="${movementClass(stock.positive)} watchlist-daily-change-cell">
                            ${stock.daily_change} / ${stock.daily_change_pct}
                        </td>
                        <td>
                            ${stock.next_earnings !== 'N/A'
                                ? `<div>${formatDate(stock.next_earnings)}</div><small>${stock.earnings_time}</small>`
                                : 'N/A'}
                        </td>
                        <td>
                            <button class="btn-icon" onclick="removeFromWatchlist('${stock.symbol}')" title="Remove">
                                <i class="fas fa-times"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;

    container.innerHTML = html;
}

async function refreshEarnings() {
    await loadDashboardSnapshot();
    showToast('Dashboard refreshed', 'success');
}

async function toggleEarningsDebugMode() {
    dashboardState.earningsDebugEnabled = !dashboardState.earningsDebugEnabled;
    saveToLocalStorage('earnings_debug_enabled', dashboardState.earningsDebugEnabled);
    syncEarningsDebugToggle();
    await loadDashboardSnapshot();
    showToast(
        dashboardState.earningsDebugEnabled ? 'Earnings audit mode enabled.' : 'Earnings audit mode disabled.',
        'info'
    );
}

function setEarningsDebugFilter(filter) {
    dashboardState.earningsDebugFilter = filter;
    saveToLocalStorage('earnings_debug_filter', filter);
    syncEarningsDebugFilterButtons();
    renderEarningsDebug();
}

async function refreshLiveQuotes() {
    if (document.hidden) return;
    const visibleEarningsSymbols = Array.from(document.querySelectorAll('.earnings-item[data-symbol]'))
        .map((element) => element.dataset.symbol)
        .filter(Boolean);

    const symbols = [
        ...dashboardState.watchlist.map((item) => item.symbol),
        ...visibleEarningsSymbols
    ].filter(Boolean);
    if (symbols.length === 0) return;

    try {
        const uniqueSymbols = [...new Set(symbols)];
        const quotes = await fetchAPI(`/api/quotes?symbols=${encodeURIComponent(uniqueSymbols.join(','))}`);
        applyLiveQuotes(quotes);
    } catch (error) {
        console.error('Error refreshing live quotes:', error);
    }
}

function applyLiveQuotes(quotes) {
    quotes.forEach((quote) => {
        const row = document.querySelector(`tr[data-symbol="${quote.symbol}"]`);
        if (!row) return;

        const priceCell = row.querySelector('.watchlist-price-cell');
        const moveCell = row.querySelector('.watchlist-daily-change-cell');

        if (priceCell) {
            priceCell.textContent = quote.price_formatted;
        }

        if (moveCell) {
            moveCell.textContent = `${quote.daily_change_formatted} / ${quote.daily_change_pct_formatted}`;
            const movementClass = quote.positive === true ? 'watchlist-positive' : quote.positive === false ? 'watchlist-negative' : '';
            moveCell.className = `${movementClass} watchlist-daily-change-cell`;
        }

        dashboardState.previousQuotes[quote.symbol] = quote;
    });

    updateEarningsPrices(quotes);
    updateFeaturedStockFromQuotes(quotes);
}

function updateEarningsPrices(quotes) {
    quotes.forEach((quote) => {
        document.querySelectorAll(`.earnings-item[data-symbol="${quote.symbol}"]`).forEach((card) => {
            const priceElement = card.querySelector('.earnings-live-price');
            const moveElement = card.querySelector('.earnings-live-change');
            if (priceElement) {
                priceElement.textContent = quote.price_formatted;
            }
            if (moveElement) {
                moveElement.textContent = quote.daily_change_pct_formatted;
                moveElement.className = `earnings-submeta ${getColorClass(quote.daily_change_pct)}`;
            }
        });
    });
}

function updateFeaturedStockFromQuotes(quotes) {
    const featured = getFeaturedStock();
    if (!featured) return;
    const liveQuote = quotes.find((quote) => quote.symbol === featured.symbol);
    if (!liveQuote) return;

    featured.current_price = liveQuote.price_formatted;
    featured.price_formatted = liveQuote.price_formatted;
    featured.daily_change_pct_raw = liveQuote.daily_change_pct;
    featured.daily_change_pct = liveQuote.daily_change_pct_formatted;
    featured.daily_change_pct_formatted = liveQuote.daily_change_pct_formatted;
    renderFeaturedStock();
}

async function addWatchlistSymbol() {
    const input = document.getElementById('addWatchlistSymbol');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) {
        showToast('Enter a stock symbol first', 'error');
        return;
    }

    try {
        await fetchAPI(`/api/search/${symbol}`);
        await fetchAPI('/api/watchlist/add', {
            method: 'POST',
            body: JSON.stringify({ symbol })
        });
        input.value = '';
        await loadDashboardSnapshot();
        showToast(`${symbol} added to watchlist`, 'success');
    } catch (error) {
        showToast(`Could not add ${symbol}`, 'error');
    }
}

async function removeFromWatchlist(symbol) {
    const symbols = dashboardState.watchlist
        .map((item) => item.symbol)
        .filter((item) => item !== symbol);

    try {
        await fetchAPI('/api/watchlist', {
            method: 'POST',
            body: JSON.stringify({ watchlist: symbols })
        });
        delete dashboardState.previousQuotes[symbol];
        await loadDashboardSnapshot();
        showToast(`${symbol} removed from watchlist`, 'success');
    } catch (error) {
        showToast('Error updating watchlist', 'error');
    }
}

function searchStock(symbol) {
    window.location.href = `/earnings?symbol=${symbol}`;
}

function openEditWatchlist() {
    const modal = document.getElementById('editWatchlistModal');
    const textarea = document.getElementById('watchlistInput');
    textarea.value = dashboardState.watchlist.map((item) => item.symbol).join(', ');
    modal.style.display = 'flex';
}

function closeEditWatchlist() {
    document.getElementById('editWatchlistModal').style.display = 'none';
}

async function saveDashboardWatchlist() {
    const textarea = document.getElementById('watchlistInput');
    const symbols = textarea.value
        .split(',')
        .map((value) => value.trim().toUpperCase())
        .filter(Boolean);

    try {
        await fetchAPI('/api/watchlist', {
            method: 'POST',
            body: JSON.stringify({ watchlist: symbols })
        });
        closeEditWatchlist();
        dashboardState.previousQuotes = {};
        await loadDashboardSnapshot();
        showToast('Watchlist updated', 'success');
    } catch (error) {
        showToast('Error updating watchlist', 'error');
    }
}

async function analyzeStockMove() {
    const typedSymbol = document.getElementById('addWatchlistSymbol')?.value?.trim().toUpperCase();
    const fallbackSymbol = dashboardState.watchlist?.[0]?.symbol || dashboardState.todayEarnings?.[0]?.symbol || 'NVDA';
    const symbol = typedSymbol || fallbackSymbol;
    window.location.href = `/news?symbol=${symbol}`;
}

window.onclick = function(event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = 'none';
    }
};
