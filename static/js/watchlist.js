// ============================================
// Watchlist JavaScript
// Handles watchlist management across pages
// ============================================

// Global watchlist data
let currentWatchlist = [];

// Initialize watchlist
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('watchlistContainer')) {
        loadWatchlistData();
        setInterval(loadWatchlistData, 60000); // Update every minute
    }
});

// Load watchlist data
async function loadWatchlistData() {
    try {
        const data = await fetchAPI('/api/watchlist');
        currentWatchlist = data;
        renderWatchlistTable(data);
    } catch (error) {
        console.error('Error loading watchlist:', error);
        const container = document.getElementById('watchlistContainer');
        if (container) {
            container.innerHTML = '<div class="error-message">Failed to load watchlist</div>';
        }
    }
}

// Render watchlist table
function renderWatchlistTable(watchlist) {
    const container = document.getElementById('watchlistContainer');
    if (!container) return;
    
    if (!watchlist || watchlist.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-star"></i>
                <p>No stocks in watchlist</p>
                <button class="btn-secondary" onclick="openEditWatchlist()">Add Stocks</button>
            </div>
        `;
        return;
    }
    
    const html = `
        <div class="table-responsive">
            <table class="watchlist-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Price</th>
                        <th>Change</th>
                        <th>Day Range</th>
                        <th>Next Earnings</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    ${watchlist.map(stock => `
                        <tr>
                            <td class="watchlist-symbol" onclick="searchStock('${stock.symbol}')">
                                <strong>${stock.symbol}</strong>
                            </td>
                            <td>${stock.current_price}</td>
                            <td class="${stock.positive ? 'watchlist-positive' : 'watchlist-negative'}">
                                ${stock.daily_change_pct}
                            </td>
                            <td>-</td>
                            <td>
                                ${stock.next_earnings !== 'N/A' ? 
                                    `<div>${formatDate(stock.next_earnings)}</div>
                                     <small>${stock.earnings_time}</small>` : 
                                    'N/A'}
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
        </div>
    `;
    
    container.innerHTML = html;
}

// Add to watchlist
async function addToWatchlist(symbol) {
    const currentSymbols = currentWatchlist.map(s => s.symbol);
    if (currentSymbols.includes(symbol)) {
        showToast(`${symbol} is already in your watchlist`, 'info');
        return;
    }
    
    const newWatchlist = [...currentSymbols, symbol];
    await saveWatchlist(newWatchlist);
    showToast(`${symbol} added to watchlist!`, 'success');
}

// Remove from watchlist
async function removeFromWatchlist(symbol) {
    const newWatchlist = currentWatchlist
        .map(s => s.symbol)
        .filter(s => s !== symbol);
    
    await saveWatchlist(newWatchlist);
    showToast(`${symbol} removed from watchlist`, 'success');
}

// Save watchlist
async function saveWatchlist(symbols) {
    try {
        await fetchAPI('/api/watchlist', {
            method: 'POST',
            body: JSON.stringify({ watchlist: symbols })
        });
        await loadWatchlistData();
    } catch (error) {
        showToast('Error saving watchlist', 'error');
        throw error;
    }
}

// Search stock (navigate to earnings page)
function searchStock(symbol) {
    window.location.href = `/earnings?symbol=${symbol}`;
}

// Add empty state styles
if (!document.querySelector('#watchlist-styles')) {
    const styles = document.createElement('style');
    styles.id = 'watchlist-styles';
    styles.textContent = `
        .empty-state {
            text-align: center;
            padding: 40px;
            color: var(--gray);
        }
        .empty-state i {
            font-size: 48px;
            margin-bottom: 16px;
            opacity: 0.5;
        }
        .error-message {
            text-align: center;
            padding: 20px;
            color: var(--danger);
        }
    `;
    document.head.appendChild(styles);
}