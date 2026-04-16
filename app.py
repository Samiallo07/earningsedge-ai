import os
import json
import math
import re
import requests
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Any
from threading import Lock, Thread, Event
import time
from io import StringIO
from zoneinfo import ZoneInfo
from curl_cffi import requests as curl_requests
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import yfinance as yf
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path

# Load environment variables
load_dotenv()

# Some environments inject a dead local proxy that breaks Yahoo Finance requests.
for proxy_var in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    os.environ.pop(proxy_var, None)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here-please-change-this')
CORS(app)

@app.context_processor
def inject_market_context():
    return {
        'app_market_timezone': 'America/New_York',
        'app_market_date_override': market_today().strftime('%Y-%m-%d')
    }

@app.before_request
def start_trade_monitor_if_needed():
    ensure_trade_monitor_started()

# Initialize OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
MODEL = "gpt-4o-mini"

# Data storage paths
DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
TRADES_FILE = DATA_DIR / 'trades.json'
WATCHLIST_FILE = DATA_DIR / 'watchlist.json'
PATTERN_STATS_FILE = DATA_DIR / 'pattern_stats.json'
YFINANCE_CACHE_DIR = DATA_DIR / '.yfinance-cache'
YFINANCE_CACHE_DIR.mkdir(exist_ok=True)
yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))

# Live-data helpers
YF_SESSION = curl_requests.Session()
YF_SESSION.trust_env = False
YF_SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

DEFAULT_EARNINGS_UNIVERSE = [
    'AAPL', 'ABNB', 'ADBE', 'AMD', 'AMZN', 'AVGO', 'BAC', 'C', 'COST', 'CRM',
    'CSCO', 'DAL', 'DIS', 'GOOGL', 'GS', 'HD', 'IBM', 'INTC', 'JPM', 'KO',
    'LLY', 'LMT', 'MA', 'MCD', 'META', 'MS', 'MSFT', 'NFLX', 'NKE', 'NVDA',
    'ORCL', 'PEP', 'PFE', 'PYPL', 'QCOM', 'SHOP', 'SNOW', 'T', 'TSLA', 'TSM',
    'UBER', 'UNH', 'V', 'WFC', 'XOM'
]
FEATURED_SYMBOLS = {
    'AAPL', 'AMD', 'AMZN', 'BAC', 'C', 'GOOGL', 'GS', 'JPM', 'META',
    'MS', 'MSFT', 'NFLX', 'NVDA', 'TSLA', 'TSM', 'WFC'
}
ALLOWED_EARNINGS_EXCHANGES = {
    'NMS', 'NGM', 'NCM', 'NAS', 'NYQ', 'ASE', 'AMEX', 'PCX', 'BTS'
}
MIN_EARNINGS_MARKET_CAP = 2_000_000_000
MIN_EARNINGS_AVG_VOLUME = 500_000
MAX_CALENDAR_CANDIDATES_PER_DAY = 8

CACHE_LOCK = Lock()
CACHE: Dict[str, Dict[str, Any]] = {}
MARKET_TZ = ZoneInfo("America/New_York")
MARKET_DATE_OVERRIDE = os.getenv("APP_CURRENT_DATE_OVERRIDE", "").strip()
SYMBOL_ALIASES = {
    'GOOGLE': 'GOOGL',
    'ALPHABET': 'GOOGL',
    'FACEBOOK': 'META',
    'NETFLIX': 'NFLX'
}
SEC_CONTACT_EMAIL = os.getenv('SEC_CONTACT_EMAIL', 'support@earningsedge.ai')
SEC_HEADERS = {
    'User-Agent': f'EarningsEdgeAI/1.0 ({SEC_CONTACT_EMAIL})',
    'Accept-Encoding': 'gzip, deflate',
    'Host': 'www.sec.gov'
}
TRADE_MEMORY_FEATURE_KEYS = [
    'estimate_revision',
    'price_trend',
    'valuation_level',
    'pre_earnings_run',
    'expected_move',
    'historical_reaction',
    'setup_type'
]
TRADE_MEMORY_SCORE_WEIGHTS = {
    'estimate_revision': {'up': 2, 'down': -2, 'flat': 0},
    'price_trend': {'up': 1, 'down': -1, 'sideways': 0},
    'valuation_level': {'low': 1, 'medium': 0, 'high': -2, 'very_high': -3},
    'pre_earnings_run': {'small': 0, 'medium': -1, 'large': -2},
    'expected_move': {'low': 0, 'medium': 0, 'high': 1},
    'historical_reaction': {'positive': 1, 'mixed': 0, 'negative': -1},
}
TRADE_MEMORY_PATTERN_ADJUSTMENT = {
    'min_sample': 2,
    'positive_win_rate': 0.6,
    'negative_win_rate': 0.4,
    'positive_return': 1.0,
    'negative_return': -1.0,
    'match_cap': 0.75
}

# =========================
# Helper Functions
# =========================
def safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except:
        return default

def format_money(x):
    if x is None:
        return "N/A"
    return f"${x:,.2f}"

def format_pct(x):
    if x is None:
        return "N/A"
    return f"{x:.2f}%"

def normalize_symbol(symbol: str):
    normalized = str(symbol or '').upper().strip()
    return SYMBOL_ALIASES.get(normalized, normalized)

def market_now():
    now = datetime.now(MARKET_TZ)
    if MARKET_DATE_OVERRIDE:
        try:
            override_date = datetime.strptime(MARKET_DATE_OVERRIDE, "%Y-%m-%d").date()
            return now.replace(year=override_date.year, month=override_date.month, day=override_date.day)
        except ValueError:
            return now
    return now

def market_today():
    return market_now().date()

def parse_market_cap(market_cap: str):
    text = str(market_cap or '').strip().upper().replace(',', '')
    if not text or text in {'N/A', '--'}:
        return 0.0
    multipliers = {'T': 1_000_000_000_000, 'B': 1_000_000_000, 'M': 1_000_000}
    suffix = text[-1]
    try:
        if suffix in multipliers:
            return float(text[:-1]) * multipliers[suffix]
        return float(text)
    except ValueError:
        return 0.0

def get_ticker(symbol: str):
    return yf.Ticker(normalize_symbol(symbol), session=YF_SESSION)

def get_search(query: str, news_count: int = 8):
    return yf.Search(
        query,
        news_count=news_count,
        session=YF_SESSION,
        raise_errors=False
    )

def get_cache(key: str):
    with CACHE_LOCK:
        cached = CACHE.get(key)
        if not cached:
            return None
        if cached['expires_at'] < time.time():
            CACHE.pop(key, None)
            return None
        return cached['value']

def set_cache(key: str, value, ttl_seconds: int):
    with CACHE_LOCK:
        CACHE[key] = {
            'value': value,
            'expires_at': time.time() + ttl_seconds
        }
    return value

def clear_cache(prefix: str = ''):
    with CACHE_LOCK:
        if not prefix:
            CACHE.clear()
            return
        for key in list(CACHE.keys()):
            if key.startswith(prefix):
                CACHE.pop(key, None)

def remember(key: str, ttl_seconds: int, loader):
    cached = get_cache(key)
    if cached is not None:
        return cached
    return set_cache(key, loader(), ttl_seconds)

def sanitize_for_json(value):
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, (datetime, pd.Timestamp)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if hasattr(value, 'item'):
        try:
            return sanitize_for_json(value.item())
        except Exception:
            return str(value)
    if pd.isna(value):
        return None
    return str(value)

def safe_json_response(payload, status_code: int = 200):
    return jsonify(sanitize_for_json(payload)), status_code

def log_debug(event: str, **kwargs):
    safe_details = {key: sanitize_for_json(value) for key, value in kwargs.items()}
    print(f"[DEBUG] {event}: {json.dumps(safe_details, default=str)}")

def safe_component(label: str, loader, fallback):
    try:
        return loader()
    except Exception as e:
        print(f"Error loading {label}: {e}")
        traceback.print_exc()
        return fallback() if callable(fallback) else fallback

def normalize_earnings_date(value):
    """Return an earnings date in local YYYY-MM-DD format."""
    timestamp = pd.to_datetime(value)
    if pd.isna(timestamp):
        return None
    if getattr(timestamp, 'tzinfo', None) is not None:
        timestamp = timestamp.tz_convert(MARKET_TZ).tz_localize(None)
    return timestamp.date()

def get_earnings_scan_list(limit: int = 45):
    watchlist = load_watchlist()
    symbols = []
    seen = set()
    for symbol in watchlist + DEFAULT_EARNINGS_UNIVERSE:
        normalized = symbol.upper().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        symbols.append(normalized)
        if len(symbols) >= limit:
            break
    return symbols

def get_local_iso_date(offset_days: int = 0):
    return (market_now() + timedelta(days=offset_days)).strftime('%Y-%m-%d')

def load_trades():
    if TRADES_FILE.exists():
        try:
            with open(TRADES_FILE, 'r') as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        except Exception as e:
            print(f"Error loading trades file: {e}")
            return []
    return []

def save_trades(trades):
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f, indent=2)

def load_pattern_stats():
    if PATTERN_STATS_FILE.exists():
        try:
            with open(PATTERN_STATS_FILE, 'r') as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        except Exception as e:
            print(f"Error loading pattern stats file: {e}")
            return []
    return []

def save_pattern_stats(pattern_stats):
    with open(PATTERN_STATS_FILE, 'w') as f:
        json.dump(pattern_stats, f, indent=2)

def load_watchlist():
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, 'r') as f:
                items = json.load(f)
                cleaned = []
                seen = set()
                for item in items:
                    normalized = normalize_symbol(item)
                    if not normalized or normalized in seen:
                        continue
                    seen.add(normalized)
                    cleaned.append(normalized)
                return cleaned
        except Exception as e:
            print(f"Error loading watchlist file: {e}")
    return ['NVDA', 'AMD', 'META', 'TSLA', 'NFLX', 'AAPL', 'MSFT', 'GOOGL']

def save_watchlist(watchlist):
    with open(WATCHLIST_FILE, 'w') as f:
        cleaned = []
        seen = set()
        for symbol in watchlist:
            normalized = normalize_symbol(symbol)
            if normalized and normalized not in seen:
                seen.add(normalized)
                cleaned.append(normalized)
        json.dump(cleaned, f, indent=2)

# =========================
# Earnings Data Functions
# =========================
def normalize_calendar_time(raw_value):
    value = str(raw_value or '').strip().upper()
    if value in {'BMO', 'BEFORE MARKET OPEN'}:
        return {'label': 'Before Market Open', 'raw': value, 'is_confirmed': True}
    if value in {'AMC', 'AFTER MARKET CLOSE'}:
        return {'label': 'After Market Close', 'raw': value, 'is_confirmed': True}
    if value in {'TNS', 'DMT', 'DMH', 'TAS'}:
        return {'label': 'Time Not Confirmed', 'raw': value, 'is_confirmed': False}
    if ':' in value:
        try:
            hour = int(value.split(':', 1)[0])
            if hour < 12:
                return {'label': f'{value} ET', 'raw': value, 'is_confirmed': True}
            if hour >= 15:
                return {'label': f'{value} ET', 'raw': value, 'is_confirmed': True}
        except ValueError:
            pass
    return {'label': 'Time Not Confirmed', 'raw': value or 'Unknown', 'is_confirmed': False}

def is_valid_earnings_symbol(symbol: str):
    return bool(re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', str(symbol or '').strip().upper()))

def is_allowed_earnings_exchange(exchange: str):
    value = str(exchange or '').strip().upper()
    return value in ALLOWED_EARNINGS_EXCHANGES

def get_earnings_source_ttl(day: str):
    target_date = datetime.strptime(day, '%Y-%m-%d').date()
    day_delta = (target_date - market_today()).days
    if day_delta <= 1:
        return 300
    if day_delta <= 4:
        return 900
    return 1800

def get_market_week_dates(reference_date=None):
    reference_date = reference_date or market_today()
    week_start = reference_date - timedelta(days=reference_date.weekday())
    week_days = []
    for offset in range(5):
        day = week_start + timedelta(days=offset)
        if day >= reference_date:
            week_days.append(day)
    return week_days

def build_earnings_candidate_priority(item, watchlist):
    market_cap_value = parse_market_cap(item.get('market_cap'))
    symbol = item.get('symbol')
    company = str(item.get('company', '')).strip()
    return (
        0 if symbol in watchlist else 1,
        0 if symbol in FEATURED_SYMBOLS else 1,
        0 if company else 1,
        -market_cap_value,
        symbol
    )

def get_symbol_profile(symbol: str):
    normalized = normalize_symbol(symbol)
    cache_key = f"symbol_profile:{normalized}"

    def loader():
        info = get_ticker(normalized).info or {}
        quote_type = str(info.get('quoteType', '')).lower()
        return {
            'symbol': normalized,
            'company_name': info.get('longName') or info.get('shortName') or normalized,
            'exchange': info.get('exchange', ''),
            'quote_type': quote_type,
            'market_cap_value': safe_int(info.get('marketCap')),
            'average_volume_value': safe_int(info.get('averageVolume')),
            'regular_market_price': safe_float(info.get('regularMarketPrice')),
            'is_equity': quote_type in {'equity', 'stock'} or not quote_type
        }

    return remember(cache_key, 3600, loader)

def get_verified_earnings_date_for_symbol(symbol: str, target_day=None):
    normalized = normalize_symbol(symbol)
    cache_day = target_day.strftime('%Y-%m-%d') if target_day else 'next'
    cache_key = f"verified_earnings_date:{normalized}:{cache_day}"

    def loader():
        stock = get_ticker(normalized)
        earnings_df = stock.get_earnings_dates(limit=8)
        if earnings_df is None or earnings_df.empty:
            return None

        for date, row in earnings_df.iterrows():
            normalized_date = normalize_earnings_date(date)
            if normalized_date is None:
                continue
            if target_day and normalized_date != target_day:
                continue
            raw_time = row.get('Earnings Time', pd.to_datetime(date).strftime('%H:%M'))
            timing = normalize_calendar_time(raw_time)
            return {
                'symbol': normalized,
                'date': normalized_date.strftime('%Y-%m-%d'),
                'timing': timing,
                'source': 'yfinance.get_earnings_dates',
                'is_confirmed': True
            }
        return None

    return remember(cache_key, 1800, loader)

def build_earnings_audit_record(symbol, company, reason, stage, row=None, profile=None, verified=None):
    record = {
        'symbol': symbol,
        'company': company,
        'reason': reason,
        'stage': stage
    }
    if row:
        record['calendar_date'] = row.get('date')
        record['calendar_market_cap'] = row.get('market_cap')
        record['calendar_time_raw'] = row.get('time_raw')
    if profile:
        record['exchange'] = profile.get('exchange')
        record['market_cap_value'] = profile.get('market_cap_value')
        record['average_volume_value'] = profile.get('average_volume_value')
    if verified:
        record['verified_date'] = verified.get('date')
        record['verified_source'] = verified.get('source')
        timing = verified.get('timing') or {}
        record['verified_time'] = timing.get('label')
    return sanitize_for_json(record)

def evaluate_earnings_candidate(row, target_day: str, watchlist):
    symbol = normalize_symbol(row.get('symbol', ''))
    company = str(row.get('company', '')).strip()
    if not symbol or not is_valid_earnings_symbol(symbol) or not company:
        return {
            'included': False,
            'reason': 'invalid symbol or missing company metadata',
            'audit': build_earnings_audit_record(symbol or str(row.get('symbol', '')).strip().upper(), company, 'invalid symbol or missing company metadata', 'calendar_parse', row=row)
        }

    profile = safe_component('symbol profile', lambda: get_symbol_profile(symbol), {})
    if not profile or not profile.get('is_equity'):
        return {
            'included': False,
            'reason': 'symbol is not a supported equity profile',
            'audit': build_earnings_audit_record(symbol, company, 'symbol is not a supported equity profile', 'profile_filter', row=row, profile=profile)
        }
    if not is_allowed_earnings_exchange(profile.get('exchange')):
        return {
            'included': False,
            'reason': 'exchange not in trusted earnings universe',
            'audit': build_earnings_audit_record(symbol, company, 'exchange not in trusted earnings universe', 'profile_filter', row=row, profile=profile)
        }

    market_cap_value = safe_int(profile.get('market_cap_value'))
    average_volume_value = safe_int(profile.get('average_volume_value'))
    if symbol not in watchlist and symbol not in FEATURED_SYMBOLS:
        if (market_cap_value or 0) < MIN_EARNINGS_MARKET_CAP and (average_volume_value or 0) < MIN_EARNINGS_AVG_VOLUME:
            return {
                'included': False,
                'reason': 'below trusted liquidity and size thresholds',
                'audit': build_earnings_audit_record(symbol, company, 'below trusted liquidity and size thresholds', 'quality_filter', row=row, profile=profile)
            }

    target_date = datetime.strptime(target_day, '%Y-%m-%d').date()
    verified = get_verified_earnings_date_for_symbol(symbol, target_day=target_date)
    if not verified:
        timing = normalize_calendar_time(row.get('time_raw', ''))
        item = sanitize_for_json({
            'symbol': symbol,
            'company': profile.get('company_name') or company,
            'date': target_day,
            'quarter': str(row.get('quarter', '')).strip() or 'N/A',
            'time': timing.get('label', 'Time Not Confirmed'),
            'time_raw': timing.get('raw', 'Unknown'),
            'time_confirmed': bool(timing.get('is_confirmed')),
            'eps_estimate': str(row.get('eps_estimate', '')).strip() or 'N/A',
            'market_cap': compact_number(market_cap_value) if market_cap_value else (str(row.get('market_cap', '')).strip() or 'N/A'),
            'market_cap_value': market_cap_value,
            'average_volume_value': average_volume_value,
            'exchange': profile.get('exchange', ''),
            'source': 'yahoo.calendar',
            'confidence': 'calendar',
            'is_confirmed': False,
            'is_watchlist': symbol in watchlist
        })
        return {
            'included': True,
            'reason': 'calendar fallback used because verification feed was unavailable',
            'item': item,
            'audit': build_earnings_audit_record(symbol, company, 'calendar fallback used because verification feed was unavailable', 'included', row=row, profile=profile)
        }

    timing = verified.get('timing') or normalize_calendar_time(row.get('time_raw', ''))
    if verified.get('date') != target_day:
        return {
            'included': False,
            'reason': 'verified earnings date does not match target day',
            'audit': build_earnings_audit_record(symbol, company, 'verified earnings date does not match target day', 'verification', row=row, profile=profile, verified=verified)
        }

    item = sanitize_for_json({
        'symbol': symbol,
        'company': profile.get('company_name') or company,
        'date': verified.get('date'),
        'quarter': str(row.get('quarter', '')).strip() or 'N/A',
        'time': timing.get('label', 'Time Not Confirmed'),
        'time_raw': timing.get('raw', 'Unknown'),
        'time_confirmed': bool(timing.get('is_confirmed')),
        'eps_estimate': str(row.get('eps_estimate', '')).strip() or 'N/A',
        'market_cap': compact_number(market_cap_value) if market_cap_value else (str(row.get('market_cap', '')).strip() or 'N/A'),
        'market_cap_value': market_cap_value,
        'average_volume_value': average_volume_value,
        'exchange': profile.get('exchange', ''),
        'source': verified.get('source'),
        'confidence': 'verified',
        'is_confirmed': True,
        'is_watchlist': symbol in watchlist
    })
    return {
        'included': True,
        'reason': 'verified and passed filters',
        'item': item,
        'audit': build_earnings_audit_record(symbol, company, 'verified and passed filters', 'included', row=row, profile=profile, verified=verified)
    }

def fetch_earnings_calendar_for_day(day: str, include_debug: bool = False):
    cache_key = f"earnings_day:{day}"

    def loader():
        try:
            url = f'https://finance.yahoo.com/calendar/earnings?day={day}'
            session = curl_requests.Session(impersonate='chrome110')
            session.trust_env = False
            response = session.get(url, timeout=12)
            response.raise_for_status()
            tables = pd.read_html(StringIO(response.text), flavor='lxml')
            if not tables:
                return {'items': [], 'audit': []} if include_debug else []

            table = tables[0].fillna('')
            raw_items = []
            audit_records = []
            for _, row in table.iterrows():
                symbol = str(row.get('Symbol', '')).strip().upper()
                company = str(row.get('Company', '')).strip()
                if not symbol or not company or not is_valid_earnings_symbol(symbol):
                    if include_debug:
                        audit_records.append(build_earnings_audit_record(symbol, company, 'dropped during scrape parse because symbol/company was invalid', 'calendar_parse'))
                    continue
                timing = normalize_calendar_time(row.get('Earnings Call Time', ''))
                raw_items.append({
                    'symbol': symbol,
                    'company': company,
                    'date': day,
                    'quarter': str(row.get('Event Name', '')).strip() or 'N/A',
                    'time': timing['label'],
                    'time_raw': timing['raw'],
                    'eps_estimate': str(row.get('EPS Estimate', '')).strip() or 'N/A',
                    'market_cap': str(row.get('Market Cap', '')).strip() or 'N/A'
                })

            watchlist = set(load_watchlist())
            ranked_candidates = sorted(
                raw_items,
                key=lambda item: build_earnings_candidate_priority(item, watchlist)
            )[:MAX_CALENDAR_CANDIDATES_PER_DAY]
            ranked_symbols = {item['symbol'] for item in ranked_candidates}
            if include_debug:
                for item in raw_items:
                    if item['symbol'] not in ranked_symbols:
                        audit_records.append(build_earnings_audit_record(item['symbol'], item['company'], 'not prioritized into top verification candidate set', 'candidate_ranking', row=item))

            items = []
            seen_symbols = set()
            with ThreadPoolExecutor(max_workers=min(4, len(ranked_candidates) or 1)) as executor:
                futures = [
                    executor.submit(evaluate_earnings_candidate, item, day, watchlist)
                    for item in ranked_candidates
                ]
                for future in as_completed(futures):
                    evaluation = future.result()
                    if include_debug and evaluation.get('audit'):
                        audit_records.append(evaluation['audit'])
                    if not evaluation.get('included'):
                        continue
                    verified_item = evaluation['item']
                    symbol = verified_item['symbol']
                    if symbol in seen_symbols:
                        if include_debug:
                            audit_records.append(build_earnings_audit_record(symbol, verified_item.get('company'), 'duplicate verified symbol removed for same day', 'dedupe', row=verified_item))
                        continue
                    seen_symbols.add(symbol)
                    items.append(verified_item)

            items.sort(key=lambda item: (
                0 if item.get('is_watchlist') else 1,
                0 if item.get('symbol') in FEATURED_SYMBOLS else 1,
                -(item.get('market_cap_value') or 0),
                item.get('symbol')
            ))
            if include_debug:
                return {
                    'items': items,
                    'audit': audit_records
                }
            return items
        except Exception as e:
            if 'No tables found' not in str(e):
                print(f"Error getting earnings calendar for {day}: {e}")
            if include_debug:
                return {
                    'items': [],
                    'audit': [build_earnings_audit_record('', '', f'calendar fetch failed: {e}', 'error')]
                }
            return []

    if include_debug:
        return loader()
    return remember(cache_key, get_earnings_source_ttl(day), loader)

def get_upcoming_earnings(days_ahead: int = 7, include_debug: bool = False):
    """Get verified earnings for the next N days from the current market week."""
    try:
        watchlist = set(load_watchlist())
        target_days = [
            day.strftime('%Y-%m-%d')
            for day in get_market_week_dates()
            if 0 <= (day - market_today()).days <= days_ahead
        ]
        earnings_data = []
        audit_by_day = {}

        with ThreadPoolExecutor(max_workers=min(4, len(target_days) or 1)) as executor:
            futures = {
                executor.submit(fetch_earnings_calendar_for_day, day, include_debug): day
                for day in target_days
            }
            for future in as_completed(futures):
                day = futures[future]
                result = future.result()
                rows = result.get('items', []) if include_debug else result
                if include_debug:
                    audit_by_day[day] = result.get('audit', [])
                for row in rows:
                    row = dict(row)
                    row['days_until'] = (datetime.strptime(day, '%Y-%m-%d').date() - market_today()).days
                    row['is_watchlist'] = row['symbol'] in watchlist
                    earnings_data.append(row)

        deduped = []
        seen = set()
        for item in sorted(earnings_data, key=lambda x: (x['date'], not x['is_watchlist'], -safe_int(x.get('market_cap_value'), 0), x['symbol'])):
            key = (item['symbol'], item['date'])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        deduped.sort(key=lambda x: (x['days_until'], not x['is_watchlist'], -safe_int(x.get('market_cap_value'), 0), x['symbol']))
        if include_debug:
            return {
                'items': deduped,
                'audit_by_day': audit_by_day
            }
        return deduped
    except Exception as e:
        print(f"Error getting earnings: {e}")
        if include_debug:
            return {'items': [], 'audit_by_day': {}}
        return []

def score_earnings_opportunity(item):
    score = 0.0
    market_cap_score = min(parse_market_cap(item.get('market_cap')) / 50_000_000_000, 8)
    score += market_cap_score
    if item.get('symbol') in FEATURED_SYMBOLS:
        score += 8
    if item.get('is_watchlist'):
        score += 5
    if item.get('time') in {'Before Market Open', 'After Market Close'}:
        score += 2
    if item.get('days_until') == 1:
        score += 2.5
    elif item.get('days_until') == 2:
        score += 1.5
    elif item.get('days_until') > 4:
        score -= 0.5
    return score

def enrich_earnings_with_quotes(items):
    if not items:
        return items
    symbols = sorted({item['symbol'] for item in items})
    quote_map = {quote['symbol']: quote for quote in get_live_quotes(symbols)}
    enriched = []
    for item in items:
        quote = quote_map.get(item['symbol'], {})
        enriched_item = dict(item)
        enriched_item['current_price'] = quote.get('current_price')
        enriched_item['price_formatted'] = quote.get('price_formatted', 'N/A')
        enriched_item['daily_change_pct'] = quote.get('daily_change_pct')
        enriched_item['daily_change_pct_formatted'] = quote.get('daily_change_pct_formatted', 'N/A')
        enriched_item['daily_change'] = quote.get('daily_change')
        enriched_item['daily_change_formatted'] = quote.get('daily_change_formatted', 'N/A')
        enriched_item['positive'] = quote.get('positive')
        enriched_item['updated_at'] = quote.get('updated_at')
        enriched.append(enriched_item)
    return enriched

def tag_interest_labels(items):
    tagged = []
    for index, item in enumerate(items):
        enriched = dict(item)
        if item.get('is_watchlist'):
            enriched['interest_label'] = 'On Your Watchlist'
        elif item.get('symbol') in FEATURED_SYMBOLS:
            enriched['interest_label'] = 'High Attention'
        elif index < 2:
            enriched['interest_label'] = 'Top Setup'
        tagged.append(enriched)
    return tagged

def get_focus_earnings_for_day(day: str, limit: int = 18):
    target_day = str(day)
    cache_key = f"focus_earnings:{target_day}:{limit}"

    def loader():
        watchlist = load_watchlist()
        watchlist_set = set(watchlist)
        focus_symbols = []
        for symbol in watchlist + sorted(FEATURED_SYMBOLS) + DEFAULT_EARNINGS_UNIVERSE:
            normalized = normalize_symbol(symbol)
            if normalized not in focus_symbols:
                focus_symbols.append(normalized)
            if len(focus_symbols) >= limit:
                break

        matching_symbols = []
        with ThreadPoolExecutor(max_workers=min(6, len(focus_symbols) or 1)) as executor:
            futures = {executor.submit(get_next_earnings_for_symbol, symbol): symbol for symbol in focus_symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    next_earnings = future.result()
                except Exception:
                    continue
                if next_earnings.get('next_earnings') == target_day:
                    matching_symbols.append((symbol, next_earnings))

        if not matching_symbols:
            return []

        quote_map = {
            quote.get('symbol'): quote
            for quote in get_live_quotes([symbol for symbol, _ in matching_symbols])
            if quote.get('symbol')
        }
        results = []
        for symbol, next_earnings in matching_symbols:
            profile = safe_component('symbol profile', lambda: get_symbol_profile(symbol), {})
            quote = quote_map.get(symbol, {})
            results.append(sanitize_for_json({
                'symbol': symbol,
                'company': profile.get('company_name') or symbol,
                'date': target_day,
                'quarter': 'Upcoming earnings',
                'time': next_earnings.get('earnings_time', 'Time Not Confirmed'),
                'time_raw': next_earnings.get('earnings_time', 'Unknown'),
                'time_confirmed': True,
                'eps_estimate': 'N/A',
                'market_cap': compact_number(profile.get('market_cap_value')) if profile.get('market_cap_value') else 'N/A',
                'market_cap_value': safe_int(profile.get('market_cap_value')),
                'average_volume_value': safe_int(profile.get('average_volume_value')),
                'exchange': profile.get('exchange', ''),
                'source': 'focus_universe',
                'confidence': 'verified',
                'is_confirmed': True,
                'is_watchlist': symbol in watchlist_set,
                'current_price': quote.get('current_price'),
                'price_formatted': quote.get('price_formatted', 'N/A'),
                'daily_change_pct': quote.get('daily_change_pct'),
                'daily_change_pct_formatted': quote.get('daily_change_pct_formatted', 'N/A')
            }))

        results.sort(key=lambda item: (not item.get('is_watchlist'), -(item.get('market_cap_value') or 0), item.get('symbol')))
        return results

    return remember(cache_key, get_earnings_source_ttl(target_day), loader)

def get_detailed_earnings(symbol: str):
    """Get detailed earnings history with price reactions"""
    normalized = normalize_symbol(symbol)
    cache_key = f"detailed_earnings:{normalized}"

    def loader():
        try:
            stock = get_ticker(normalized)
            earnings_df = stock.get_earnings_dates(limit=12)
            if earnings_df is None or earnings_df.empty:
                return []

            price_hist = stock.history(period="1y", interval="1d")
            price_lookup = {}
            if not price_hist.empty:
                price_hist_copy = price_hist.reset_index()
                price_hist_copy['DateOnly'] = pd.to_datetime(price_hist_copy['Date']).dt.date
                price_lookup = {row['DateOnly']: row for _, row in price_hist_copy.iterrows()}
                ordered_dates = sorted(price_lookup.keys())
            else:
                ordered_dates = []

            earnings_df = earnings_df.reset_index()
            date_col = earnings_df.columns[0]
            results = []

            for idx, row in earnings_df.iterrows():
                if idx >= 10:
                    break

                earnings_date = pd.to_datetime(row.get(date_col))
                quarter = row.get('Quarter', 'N/A')
                eps_est = row.get('EPS Estimate', 'N/A')
                eps_actual = row.get('Reported EPS', 'N/A')
                surprise_pct = row.get('Surprise(%)', 'N/A')
                earnings_day_move = 'N/A'
                next_day_move = 'N/A'
                reason = "Earnings report"

                try:
                    earnings_date_only = earnings_date.date()
                    earnings_day = price_lookup.get(earnings_date_only)
                    if earnings_day is not None:
                        open_price = earnings_day['Open']
                        close_price = earnings_day['Close']
                        earnings_day_move = ((close_price - open_price) / open_price) * 100
                        next_trade_date = next((date for date in ordered_dates if date > earnings_date_only), None)
                        if next_trade_date:
                            next_day_close = price_lookup[next_trade_date]['Close']
                            next_day_move = ((next_day_close - close_price) / close_price) * 100
                except Exception:
                    pass

                if pd.notna(eps_actual) and pd.notna(eps_est):
                    surprise_val = float(eps_actual) - float(eps_est)
                    if surprise_val > 0:
                        reason = "Strong earnings beat" if surprise_val > 0.5 else "Earnings beat expectations"
                    elif surprise_val < 0:
                        reason = "Major earnings miss" if surprise_val < -0.5 else "Earnings missed expectations"
                    else:
                        reason = "Met expectations"

                results.append({
                    'quarter': str(quarter)[:10],
                    'report_date': earnings_date.strftime('%Y-%m-%d') if pd.notna(earnings_date) else 'N/A',
                    'actual_eps': round(float(eps_actual), 2) if pd.notna(eps_actual) else 'N/A',
                    'estimate_eps': round(float(eps_est), 2) if pd.notna(eps_est) else 'N/A',
                    'surprise': round(float(eps_actual) - float(eps_est), 2) if pd.notna(eps_actual) and pd.notna(eps_est) else 'N/A',
                    'surprise_pct': round(float(surprise_pct), 2) if pd.notna(surprise_pct) else 'N/A',
                    'earnings_day_move': round(earnings_day_move, 2) if earnings_day_move != 'N/A' else 'N/A',
                    'next_day_move': round(next_day_move, 2) if next_day_move != 'N/A' else 'N/A',
                    'reason': reason
                })

            return results
        except Exception as e:
            print(f"Error getting earnings details: {e}")
            return []

    return remember(cache_key, 300, loader)

def parse_compact_number(value):
    text = str(value or '').strip().upper().replace(',', '')
    if not text or text in {'N/A', '--'}:
        return None
    suffix_map = {'T': 1_000_000_000_000, 'B': 1_000_000_000, 'M': 1_000_000}
    suffix = text[-1]
    try:
        if suffix in suffix_map:
            return float(text[:-1]) * suffix_map[suffix]
        return float(text)
    except ValueError:
        return None

def derive_estimate_revision(recommendation_summary, analyst_changes):
    score = 0
    for change in analyst_changes[:6]:
        action = str(change.get('action', '')).lower()
        to_grade = str(change.get('to_grade', '')).lower()
        from_grade = str(change.get('from_grade', '')).lower()
        if action == 'up':
            score += 1
        elif action == 'down':
            score -= 1
        if 'buy' in to_grade or 'overweight' in to_grade or 'outperform' in to_grade:
            score += 1
        if 'sell' in to_grade or 'underweight' in to_grade or 'underperform' in to_grade:
            score -= 1
        if ('hold' in to_grade or 'neutral' in to_grade) and ('buy' in from_grade or 'outperform' in from_grade):
            score -= 1
    consensus = recommendation_summary.get('consensus')
    if consensus == 'Bullish':
        score += 1
    elif consensus == 'Cautious':
        score -= 1
    if score >= 2:
        return 'up'
    if score <= -2:
        return 'down'
    return 'flat'

def derive_price_trend_from_series(price_series):
    values = [safe_float(item.get('value')) for item in price_series if safe_float(item.get('value')) is not None]
    if len(values) < 2:
        return 'sideways', None
    lookback = min(20, len(values) - 1)
    start_value = values[-lookback - 1]
    end_value = values[-1]
    if not start_value:
        return 'sideways', None
    change_pct = ((end_value - start_value) / start_value) * 100
    if change_pct >= 5:
        return 'up', round(change_pct, 2)
    if change_pct <= -5:
        return 'down', round(change_pct, 2)
    return 'sideways', round(change_pct, 2)

def derive_valuation_level(snapshot):
    forward_pe = safe_float(snapshot.get('forward_pe'))
    trailing_pe = safe_float(snapshot.get('trailing_pe'))
    pe = forward_pe if forward_pe is not None else trailing_pe
    market_cap = parse_compact_number(snapshot.get('market_cap'))
    if pe is None:
        if market_cap is not None and market_cap >= 500_000_000_000:
            return 'high'
        return 'medium'
    if pe >= 50:
        return 'very_high'
    if pe >= 30:
        return 'high'
    if pe <= 18:
        return 'low'
    return 'medium'

def derive_pre_earnings_run(recent_change_pct):
    change_pct = safe_float(recent_change_pct)
    if change_pct is None:
        return 'medium'
    absolute_change = abs(change_pct)
    if absolute_change >= 12:
        return 'large'
    if absolute_change >= 5:
        return 'medium'
    return 'small'

def derive_expected_move(earnings):
    moves = [abs(safe_float(item.get('earnings_day_move')) or 0) for item in earnings[:6] if safe_float(item.get('earnings_day_move')) is not None]
    if not moves:
        return 'medium', None
    average_move = sum(moves) / len(moves)
    if average_move >= 6:
        return 'high', round(average_move, 2)
    if average_move >= 3:
        return 'medium', round(average_move, 2)
    return 'low', round(average_move, 2)

def derive_historical_reaction(earnings):
    moves = [safe_float(item.get('earnings_day_move')) for item in earnings[:6] if safe_float(item.get('earnings_day_move')) is not None]
    if not moves:
        return 'mixed', 0.0
    positives = len([move for move in moves if move > 0])
    positive_ratio = positives / len(moves)
    average_move = sum(moves) / len(moves)
    if positive_ratio >= 0.6 and average_move >= 1:
        return 'positive', round(positive_ratio * 100, 1)
    if positive_ratio <= 0.4 and average_move <= -0.5:
        return 'negative', round(positive_ratio * 100, 1)
    return 'mixed', round(positive_ratio * 100, 1)

def derive_setup_type(price_trend, pre_earnings_run, historical_reaction):
    if price_trend == 'up' and pre_earnings_run == 'large':
        return 'overextended'
    if price_trend == 'down' and historical_reaction == 'positive':
        return 'rebound'
    if price_trend == 'up':
        return 'momentum'
    return 'neutral'

def build_trade_memory_features(symbol: str, research: Dict[str, Any] | None = None, trade_type: str = 'Other'):
    research = research or get_stock_research_data(symbol)
    snapshot = research.get('snapshot', {})
    earnings = research.get('earnings', [])
    price_history = research.get('price_history', {})
    recommendation_summary = research.get('recommendation_summary', {})
    analyst_changes = research.get('analyst_changes', [])
    price_targets = research.get('price_targets', {})
    next_earnings = get_next_earnings_for_symbol(symbol)

    price_trend, recent_change_pct = derive_price_trend_from_series(price_history.get('price_series', []))
    valuation_level = derive_valuation_level(snapshot)
    pre_earnings_run = derive_pre_earnings_run(recent_change_pct)
    expected_move, expected_move_pct = derive_expected_move(earnings)
    historical_reaction, historical_positive_rate = derive_historical_reaction(earnings)
    estimate_revision = derive_estimate_revision(recommendation_summary, analyst_changes)
    setup_type = derive_setup_type(price_trend, pre_earnings_run, historical_reaction)

    return sanitize_for_json({
        'trade_type': trade_type,
        'estimate_revision': estimate_revision,
        'price_trend': price_trend,
        'valuation_level': valuation_level,
        'pre_earnings_run': pre_earnings_run,
        'expected_move': expected_move,
        'historical_reaction': historical_reaction,
        'setup_type': setup_type,
        'expected_move_pct': expected_move_pct,
        'recent_price_change_pct': recent_change_pct,
        'historical_positive_reaction_rate': historical_positive_rate,
        'earnings_date': next_earnings.get('next_earnings'),
        'next_earnings_date': next_earnings.get('next_earnings'),
        'next_earnings_time': next_earnings.get('earnings_time'),
        'price_target_upside_pct': price_targets.get('upside_pct'),
        'analyst_consensus': recommendation_summary.get('consensus', 'N/A'),
        'current_price': snapshot.get('current_price'),
        'sector': snapshot.get('sector', 'N/A'),
        'industry': snapshot.get('industry', 'N/A'),
        'dcf_label': (research.get('dcf_valuation') or {}).get('valuation_label', 'N/A') if (research.get('dcf_valuation') or {}).get('available') else 'N/A'
    })

def feature_label(feature_name: str, feature_value: str):
    labels = {
        'estimate_revision': {
            'up': 'upward estimate revisions',
            'down': 'falling estimate revisions',
            'flat': 'flat estimate revisions'
        },
        'price_trend': {
            'up': 'upward price trend',
            'down': 'downward price trend',
            'sideways': 'sideways price action'
        },
        'valuation_level': {
            'low': 'low valuation',
            'medium': 'balanced valuation',
            'high': 'high valuation',
            'very_high': 'very high valuation'
        },
        'pre_earnings_run': {
            'small': 'small pre-earnings run',
            'medium': 'medium pre-earnings run',
            'large': 'large pre-earnings run'
        },
        'expected_move': {
            'low': 'low expected move',
            'medium': 'medium expected move',
            'high': 'high expected move'
        },
        'historical_reaction': {
            'positive': 'positive historical earnings reaction',
            'mixed': 'mixed historical earnings reaction',
            'negative': 'negative historical earnings reaction'
        },
        'setup_type': {
            'momentum': 'momentum setup',
            'overextended': 'overextended setup',
            'rebound': 'rebound setup',
            'neutral': 'neutral setup'
        }
    }
    return labels.get(feature_name, {}).get(feature_value, f"{feature_name.replace('_', ' ')}: {feature_value}")

def build_pattern_key(feature_name: str, feature_value: str):
    return f"{feature_name}:{feature_value}"

def get_trade_feature_snapshot(trade):
    setup = trade.get('setup_profile') or {}
    return {
        key: setup.get(key)
        for key in TRADE_MEMORY_FEATURE_KEYS
        if setup.get(key)
    }

def sort_feature_list(items):
    seen = set()
    cleaned = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned

def update_pattern_stats(trades):
    closed_trades = [enrich_trade_for_display(trade) for trade in trades if trade.get('status') == 'closed' and trade.get('profit_pct') is not None]
    stats_map = {}

    for trade in closed_trades:
        feature_snapshot = get_trade_feature_snapshot(trade)
        if not feature_snapshot:
            continue
        outcome = trade.get('outcome')
        return_pct = safe_float(trade.get('profit_pct')) or 0.0
        for feature_name, feature_value in feature_snapshot.items():
            key = build_pattern_key(feature_name, feature_value)
            stat = stats_map.setdefault(key, {
                'id': key,
                'feature_name': feature_name,
                'feature_value': feature_value,
                'win_count': 0,
                'loss_count': 0,
                'flat_count': 0,
                'avg_return': 0.0,
                'win_rate': 0.0,
                'sample_size': 0
            })
            stat['sample_size'] += 1
            stat['avg_return'] += return_pct
            if outcome == 'win':
                stat['win_count'] += 1
            elif outcome == 'loss':
                stat['loss_count'] += 1
            else:
                stat['flat_count'] += 1

    pattern_stats = []
    for stat in stats_map.values():
        sample_size = stat['sample_size'] or 1
        resolved = {
            **stat,
            'avg_return': round(stat['avg_return'] / sample_size, 2),
            'win_rate': round((stat['win_count'] / sample_size) * 100, 1)
        }
        pattern_stats.append(resolved)

    pattern_stats.sort(key=lambda item: (-item['sample_size'], item['feature_name'], item['feature_value']))
    save_pattern_stats(pattern_stats)
    return pattern_stats

def get_pattern_stats_lookup(pattern_stats):
    return {
        build_pattern_key(item.get('feature_name'), item.get('feature_value')): item
        for item in pattern_stats
    }

def find_similar_setups(current_features, trades=None, pattern_stats=None):
    trades = trades if trades is not None else refresh_open_trade_monitor(load_trades())
    closed_trades = [enrich_trade_for_display(trade) for trade in trades if trade.get('status') == 'closed' and trade.get('profit_pct') is not None]
    pattern_stats = pattern_stats if pattern_stats is not None else load_pattern_stats()
    lookup = get_pattern_stats_lookup(pattern_stats)

    matching_trades = []
    positive_patterns = []
    negative_patterns = []

    for trade in closed_trades:
        feature_snapshot = get_trade_feature_snapshot(trade)
        matches = [key for key in TRADE_MEMORY_FEATURE_KEYS if current_features.get(key) and current_features.get(key) == feature_snapshot.get(key)]
        if len(matches) < 2:
            continue
        matching_trades.append({
            'trade_id': trade.get('id'),
            'symbol': trade.get('symbol'),
            'outcome': trade.get('outcome'),
            'return_percent': safe_round(trade.get('profit_pct')),
            'match_count': len(matches),
            'matched_features': matches
        })

    for feature_name in TRADE_MEMORY_FEATURE_KEYS:
        feature_value = current_features.get(feature_name)
        if not feature_value:
            continue
        stat = lookup.get(build_pattern_key(feature_name, feature_value))
        if not stat or stat.get('sample_size', 0) < TRADE_MEMORY_PATTERN_ADJUSTMENT['min_sample']:
            continue
        feature_summary = {
            'feature_name': feature_name,
            'feature_value': feature_value,
            'label': feature_label(feature_name, feature_value),
            'win_rate': stat.get('win_rate'),
            'avg_return': stat.get('avg_return'),
            'sample_size': stat.get('sample_size')
        }
        if stat.get('win_rate', 0) / 100 >= TRADE_MEMORY_PATTERN_ADJUSTMENT['positive_win_rate'] and stat.get('avg_return', 0) > 0:
            positive_patterns.append(feature_summary)
        elif stat.get('win_rate', 0) / 100 <= TRADE_MEMORY_PATTERN_ADJUSTMENT['negative_win_rate'] and stat.get('avg_return', 0) < 0:
            negative_patterns.append(feature_summary)

    winners = [item for item in matching_trades if item.get('outcome') == 'win']
    losers = [item for item in matching_trades if item.get('outcome') == 'loss']
    strongest_negative = negative_patterns[0]['label'] if negative_patterns else None

    return {
        'matching_trades': sorted(matching_trades, key=lambda item: (-item['match_count'], item.get('trade_id') or 0))[:8],
        'similar_winning_trades': len(winners),
        'similar_losing_trades': len(losers),
        'strongest_positive_patterns': sorted(positive_patterns, key=lambda item: (-item['sample_size'], -item['avg_return']))[:4],
        'strongest_negative_patterns': sorted(negative_patterns, key=lambda item: (-item['sample_size'], item['avg_return']))[:4],
        'summary': (
            f"{len(winners)} similar winners and {len(losers)} similar losers in your history."
            if matching_trades else
            "No close historical setup matches yet. Log a few more closed trades to build memory."
        ),
        'headline_risk': strongest_negative
    }

def score_stock_setup(features, pattern_context=None):
    pattern_context = pattern_context or {}
    total_score = 0.0
    key_positives = []
    key_risks = []
    red_flags = []
    contributions = []

    for feature_name, weights in TRADE_MEMORY_SCORE_WEIGHTS.items():
        feature_value = features.get(feature_name)
        if not feature_value:
            continue
        contribution = weights.get(feature_value, 0)
        if contribution == 0:
            continue
        label = feature_label(feature_name, feature_value)
        contributions.append({
            'feature_name': feature_name,
            'feature_value': feature_value,
            'label': label,
            'score': contribution
        })
        total_score += contribution
        if contribution > 0:
            key_positives.append(label)
        else:
            key_risks.append(label)

    if features.get('price_trend') == 'up' and features.get('pre_earnings_run') == 'large':
        total_score -= 1
        key_risks.append('strong run-up leaves less room for earnings upside')
        red_flags.append('overextended before earnings')
    if features.get('valuation_level') == 'very_high':
        red_flags.append('high valuation')
    if features.get('pre_earnings_run') == 'large':
        red_flags.append('large pre-earnings run')
    if features.get('historical_reaction') == 'negative':
        red_flags.append('weak recent earnings reaction')
    if features.get('setup_type') == 'overextended':
        red_flags.append('overextended setup')

    pattern_adjustment = 0.0
    positive_patterns = pattern_context.get('strongest_positive_patterns', [])
    negative_patterns = pattern_context.get('strongest_negative_patterns', [])
    if positive_patterns:
        pattern_adjustment += min(len(positive_patterns) * 0.25, TRADE_MEMORY_PATTERN_ADJUSTMENT['match_cap'])
    if negative_patterns:
        pattern_adjustment -= min(len(negative_patterns) * 0.25, TRADE_MEMORY_PATTERN_ADJUSTMENT['match_cap'])
    total_score += pattern_adjustment

    if total_score >= 3:
        label = 'GOOD'
    elif total_score >= 1:
        label = 'RISKY'
    else:
        label = 'AVOID'

    confidence_score = clamp(52 + abs(total_score) * 9 + min(len(pattern_context.get('matching_trades', [])), 4) * 3, 50, 92)

    if pattern_adjustment > 0:
        key_positives.append('your historical trade memory slightly supports this setup')
    elif pattern_adjustment < 0:
        key_risks.append('your historical trade memory leans against this setup')

    return {
        'total_score': round(total_score, 2),
        'label': label,
        'confidence_score': round(confidence_score, 1),
        'key_positives': sort_feature_list(key_positives)[:5],
        'key_risks': sort_feature_list(key_risks)[:5],
        'red_flags': sort_feature_list(red_flags)[:5],
        'pattern_adjustment': round(pattern_adjustment, 2),
        'contributions': contributions,
        'setup_type': features.get('setup_type', 'neutral')
    }

def build_trade_insights(features, score_payload, similar_summary):
    positives = score_payload.get('key_positives', [])
    risks = score_payload.get('key_risks', [])
    red_flags = score_payload.get('red_flags', [])
    label = score_payload.get('label', 'RISKY')

    if label == 'GOOD':
        short_explanation = 'The setup has more support than friction based on the current earnings profile and your own history.'
    elif label == 'AVOID':
        short_explanation = 'The setup has too many warning signs to treat as a clean earnings trade right now.'
    else:
        short_explanation = 'The setup has enough positives to watch, but the risk side is still meaningful.'

    memory_summary = similar_summary.get('summary') if similar_summary else 'Trade memory is still building.'
    main_reason = positives[0] if positives else 'No single positive factor stood out enough.'
    secondary_reason = risks[0] if risks else (red_flags[0] if red_flags else 'No major secondary warning stood out.')

    return {
        'main_reason': main_reason,
        'secondary_reason': secondary_reason,
        'confidence_score': score_payload.get('confidence_score'),
        'red_flags': red_flags,
        'red_flags_json': red_flags,
        'short_explanation': short_explanation,
        'memory_summary': memory_summary
    }

def build_analyze_stock_payload(symbol: str, trade_type: str = 'Earnings'):
    normalized_symbol = normalize_symbol(symbol)
    cache_key = f"analyze_stock:{normalized_symbol}:{trade_type}"

    def loader():
        research = get_stock_research_data(normalized_symbol)
        features = build_trade_memory_features(normalized_symbol, research=research, trade_type=trade_type)
        trades = refresh_open_trade_monitor(load_trades())
        pattern_stats = load_pattern_stats() or update_pattern_stats(trades)
        similar_summary = find_similar_setups(features, trades=trades, pattern_stats=pattern_stats)
        score_payload = score_stock_setup(features, pattern_context=similar_summary)
        insights = build_trade_insights(features, score_payload, similar_summary)

        return sanitize_for_json({
            'symbol': normalized_symbol,
            'features': features,
            'score': score_payload['total_score'],
            'label': score_payload['label'],
            'confidence_score': score_payload['confidence_score'],
            'key_positives': score_payload['key_positives'],
            'key_risks': score_payload['key_risks'],
            'red_flags': score_payload['red_flags'],
            'setup_type': score_payload['setup_type'],
            'short_explanation': insights['short_explanation'],
            'memory_summary': insights['memory_summary'],
            'similar_trade_summary': similar_summary,
            'structured_explanation': {
                'contributions': score_payload['contributions'],
                'pattern_adjustment': score_payload['pattern_adjustment'],
                'main_reason': insights['main_reason'],
                'secondary_reason': insights['secondary_reason']
            },
            'insights': insights,
            'research_snapshot': {
                'company_name': research.get('snapshot', {}).get('long_name', normalized_symbol),
                'current_price': research.get('snapshot', {}).get('current_price'),
                'price_formatted': research.get('snapshot', {}).get('price_formatted', 'N/A'),
                'daily_change_pct': research.get('snapshot', {}).get('daily_change_pct'),
                'market_cap': research.get('snapshot', {}).get('market_cap', 'N/A')
            }
        })

    return remember(cache_key, 180, loader)

def attach_trade_memory(trade):
    provided_features = trade.get('trade_features') or trade.get('setup_profile')
    provided_insights = trade.get('trade_insights')
    provided_score = trade.get('score_payload')
    if provided_features and provided_insights and provided_score:
        trade['setup_profile'] = provided_features
        trade['trade_features'] = provided_features
        trade['trade_insights'] = provided_insights
        trade['score_payload'] = provided_score
        trade['earnings_date'] = trade.get('earnings_date') or (provided_features or {}).get('earnings_date')
        trade['stock_symbol'] = trade.get('symbol')
        return trade

    analysis = build_analyze_stock_payload(trade.get('symbol'), trade_type=trade.get('trade_type', 'Earnings'))
    trade['setup_profile'] = analysis.get('features', {})
    trade['trade_features'] = analysis.get('features', {})
    trade['trade_insights'] = {
        **(analysis.get('insights') or {}),
        'key_positives': analysis.get('key_positives', []),
        'key_risks': analysis.get('key_risks', []),
        'label': analysis.get('label'),
        'score': analysis.get('score'),
        'setup_type': analysis.get('setup_type')
    }
    trade['score_payload'] = {
        'score': analysis.get('score'),
        'label': analysis.get('label'),
        'confidence_score': analysis.get('confidence_score'),
        'key_positives': analysis.get('key_positives', []),
        'key_risks': analysis.get('key_risks', []),
        'red_flags': analysis.get('red_flags', [])
    }
    trade['earnings_date'] = trade.get('earnings_date') or (analysis.get('features') or {}).get('earnings_date')
    trade['stock_symbol'] = trade.get('symbol')
    return trade

# =========================
# Watchlist Functions
# =========================
def get_next_earnings_for_symbol(symbol: str):
    cache_key = f"next_earnings:{symbol}"

    def loader():
        try:
            normalized_symbol = normalize_symbol(symbol)
            if not normalized_symbol or not is_valid_earnings_symbol(normalized_symbol):
                return {'next_earnings': 'N/A', 'earnings_time': 'N/A'}
            stock = get_ticker(normalized_symbol)
            earnings = stock.get_earnings_dates(limit=6)
            if earnings is None or earnings.empty:
                return {'next_earnings': 'N/A', 'earnings_time': 'N/A'}

            today = market_today()
            for date, row in earnings.iterrows():
                earnings_date = normalize_earnings_date(date)
                if earnings_date and earnings_date >= today:
                    raw_time = row.get('Earnings Time', pd.to_datetime(date).tz_convert(MARKET_TZ).strftime('%H:%M'))
                    timing = normalize_calendar_time(raw_time)
                    return {
                        'next_earnings': earnings_date.strftime('%Y-%m-%d'),
                        'earnings_time': timing['label']
                    }
        except Exception as e:
            message = str(e)
            if 'Quote not found for symbol' not in message:
                print(f"Error getting next earnings for {symbol}: {e}")
        return {'next_earnings': 'N/A', 'earnings_time': 'N/A'}

    return remember(cache_key, 21600, loader)

def get_live_quotes(symbols: List[str]):
    normalized = [normalize_symbol(symbol) for symbol in symbols if symbol and str(symbol).strip()]
    if not normalized:
        return []

    cache_key = f"quotes:{','.join(normalized)}"

    def loader():
        quotes = {}
        try:
            joined = ' '.join(normalized)
            intraday = yf.download(
                joined,
                period='2d',
                interval='1m',
                group_by='ticker',
                auto_adjust=False,
                progress=False,
                threads=True,
                prepost=True,
                session=YF_SESSION
            )
            daily = yf.download(
                joined,
                period='5d',
                interval='1d',
                group_by='ticker',
                auto_adjust=False,
                progress=False,
                threads=True,
                session=YF_SESSION
            )

            def extract_symbol_frame(frame, symbol):
                if frame is None or frame.empty:
                    return pd.DataFrame()
                if isinstance(frame.columns, pd.MultiIndex):
                    try:
                        return frame[symbol]
                    except Exception:
                        return pd.DataFrame()
                return frame

            for symbol in normalized:
                current_price = None
                previous_close = None

                try:
                    symbol_intraday = extract_symbol_frame(intraday, symbol)
                    symbol_intraday = symbol_intraday.dropna(how='all')
                    if not symbol_intraday.empty:
                        current_price = safe_float(symbol_intraday['Close'].iloc[-1])
                except Exception:
                    pass

                try:
                    symbol_daily = extract_symbol_frame(daily, symbol)
                    symbol_daily = symbol_daily.dropna(how='all')
                    if not symbol_daily.empty:
                        previous_close = safe_float(symbol_daily['Close'].iloc[-2]) if len(symbol_daily) >= 2 else safe_float(symbol_daily['Close'].iloc[-1])
                        if current_price is None:
                            current_price = safe_float(symbol_daily['Close'].iloc[-1])
                except Exception:
                    pass

                daily_change = None
                daily_change_pct = None
                if current_price is not None and previous_close not in (None, 0):
                    daily_change = current_price - previous_close
                    daily_change_pct = (daily_change / previous_close) * 100

                quotes[symbol] = {
                    'symbol': symbol,
                    'current_price': current_price,
                    'previous_close': previous_close,
                    'daily_change': daily_change,
                    'daily_change_pct': daily_change_pct,
                    'price_formatted': format_money(current_price),
                    'daily_change_formatted': format_money(daily_change),
                    'daily_change_pct_formatted': format_pct(daily_change_pct),
                    'positive': daily_change_pct > 0 if daily_change_pct is not None else None,
                    'updated_at': market_now().isoformat()
                }
        except Exception as e:
            print(f"Error getting quotes: {e}")

        for symbol in normalized:
            quotes.setdefault(symbol, {
                'symbol': symbol,
                'current_price': None,
                'previous_close': None,
                'daily_change': None,
                'daily_change_pct': None,
                'price_formatted': 'N/A',
                'daily_change_formatted': 'N/A',
                'daily_change_pct_formatted': 'N/A',
                'positive': None,
                'updated_at': market_now().isoformat()
            })
        return [quotes[symbol] for symbol in normalized]

    return remember(cache_key, 5, loader)

def get_watchlist_data(watchlist):
    """Get current data for watchlist stocks."""
    normalized_watchlist = []
    seen = set()
    for symbol in watchlist:
        normalized = normalize_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_watchlist.append(normalized)

    quote_map = {item['symbol']: item for item in get_live_quotes(normalized_watchlist)}
    earnings_map = {}

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(normalized_watchlist)))) as executor:
        futures = {
            executor.submit(get_next_earnings_for_symbol, symbol): symbol
            for symbol in normalized_watchlist
        }
        for future in as_completed(futures):
            earnings_map[futures[future]] = future.result()

    watchlist_data = []

    for symbol in normalized_watchlist:
        quote = quote_map.get(symbol.upper(), {})
        earnings_info = earnings_map.get(symbol, {'next_earnings': 'N/A', 'earnings_time': 'N/A'})
        watchlist_data.append({
            'symbol': symbol,
            'current_price': quote.get('price_formatted', 'N/A'),
            'current_price_raw': quote.get('current_price'),
            'daily_change': quote.get('daily_change_formatted', 'N/A'),
            'daily_change_pct': quote.get('daily_change_pct_formatted', 'N/A'),
            'daily_change_raw': quote.get('daily_change'),
            'daily_change_pct_raw': quote.get('daily_change_pct'),
            'next_earnings': earnings_info.get('next_earnings', 'N/A'),
            'earnings_time': earnings_info.get('earnings_time', 'N/A'),
            'positive': quote.get('positive')
        })

    return watchlist_data

# =========================
# Research Functions
# =========================
def safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default

def compact_number(value):
    if value is None:
        return 'N/A'
    value = float(value)
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return format_money(value)

def normalize_timestamp_label(value):
    try:
        timestamp = pd.to_datetime(value)
        if pd.isna(timestamp):
            return str(value)
        if getattr(timestamp, 'tzinfo', None) is not None:
            timestamp = timestamp.tz_convert(MARKET_TZ).tz_localize(None)
        return timestamp.strftime('%b %Y')
    except Exception:
        return str(value)

def get_statement_row(frame, candidates):
    if frame is None or getattr(frame, 'empty', True):
        return pd.Series(dtype='float64')
    for candidate in candidates:
        if candidate in frame.index:
            return frame.loc[candidate]
    return pd.Series(dtype='float64')

def build_series_payload(labels, values):
    payload = []
    for label, value in zip(labels, values):
        numeric = safe_float(value)
        if numeric is None:
            continue
        payload.append({'label': label, 'value': numeric})
    return payload

def clamp(value, lower, upper):
    return max(lower, min(upper, value))

def average(values, default=None):
    clean = [safe_float(value) for value in values if safe_float(value) is not None]
    if not clean:
        return default
    return sum(clean) / len(clean)

def extract_ordered_statement_values(frame, row_names):
    row = get_statement_row(frame, row_names)
    if row.empty:
        return []

    ordered_columns = sorted(frame.columns, key=lambda col: pd.to_datetime(col))
    values = []
    for column in ordered_columns:
        numeric = safe_float(row.get(column))
        if numeric is None:
            continue
        values.append({
            'date': column,
            'label': normalize_timestamp_label(column),
            'value': numeric
        })
    return values

def derive_free_cash_flow_series(frame):
    operating_series = extract_ordered_statement_values(frame, [
        'Operating Cash Flow',
        'Cash Flow From Continuing Operating Activities',
        'Net Cash Provided By Operating Activities',
        'Net Cash Provided By Continuing Operating Activities'
    ])
    capex_series = extract_ordered_statement_values(frame, [
        'Capital Expenditure',
        'Capital Expenditures',
        'Purchase Of PPE',
        'Payments To Acquire Property Plant And Equipment'
    ])

    if not operating_series:
        return []

    operating_map = {str(item['date']): item for item in operating_series}
    capex_map = {str(item['date']): item for item in capex_series}
    derived = []

    for key, operating_item in operating_map.items():
        operating_value = operating_item['value']
        capex_value = capex_map.get(key, {}).get('value')
        if operating_value is None:
            continue

        if capex_value is None:
            free_cash_flow = operating_value
        elif capex_value < 0:
            free_cash_flow = operating_value + capex_value
        else:
            free_cash_flow = operating_value - capex_value

        derived.append({
            'date': operating_item['date'],
            'label': operating_item['label'],
            'value': free_cash_flow
        })

    return derived

def build_ttm_free_cash_flow_series(quarterly_frame):
    quarterly_series = extract_ordered_statement_values(quarterly_frame, ['Free Cash Flow'])
    source_label = 'quarterly free cash flow statement'

    if not quarterly_series:
        quarterly_series = derive_free_cash_flow_series(quarterly_frame)
        source_label = 'derived quarterly operating cash flow minus capex'

    if len(quarterly_series) < 4:
        return [], source_label

    trailing = []
    values = quarterly_series
    for index in range(3, len(values)):
        window = values[index - 3:index + 1]
        trailing.append({
            'date': values[index]['date'],
            'label': f"TTM {normalize_timestamp_label(values[index]['date'])}",
            'value': sum(item['value'] for item in window)
        })

    return trailing, source_label

def get_free_cash_flow_history(stock):
    annual_frame = stock.cashflow
    annual_series = extract_ordered_statement_values(annual_frame, ['Free Cash Flow'])
    if annual_series:
        return annual_series, 'annual free cash flow statement'

    annual_series = derive_free_cash_flow_series(annual_frame)
    if annual_series:
        return annual_series, 'derived annual operating cash flow minus capex'

    quarterly_frame = stock.quarterly_cashflow
    ttm_series, source_label = build_ttm_free_cash_flow_series(quarterly_frame)
    if ttm_series:
        return ttm_series, source_label

    return [], 'insufficient cash flow data'

def resolve_shares_outstanding(info, current_price):
    share_keys = ['sharesOutstanding', 'impliedSharesOutstanding']
    for key in share_keys:
        shares = safe_float(info.get(key))
        if shares and shares > 0:
            return shares, key

    market_cap = safe_float(info.get('marketCap'))
    if market_cap and current_price and current_price > 0:
        return market_cap / current_price, 'marketCap/currentPrice'

    return None, None

def resolve_balance_sheet_inputs(info):
    cash = safe_float(info.get('totalCash'), 0.0) or 0.0
    debt = safe_float(info.get('totalDebt'), 0.0) or 0.0
    return cash, debt

def calculate_recent_fcf_growth(fcf_history):
    values = [item['value'] for item in fcf_history if safe_float(item.get('value')) is not None]
    rates = []

    for previous, current in zip(values[:-1], values[1:]):
        if previous is None or previous == 0:
            continue
        rates.append((current - previous) / abs(previous))

    cagr = None
    positive_values = [value for value in values if value and value > 0]
    if len(positive_values) >= 2:
        first_positive = positive_values[0]
        last_positive = positive_values[-1]
        periods = max(len(positive_values) - 1, 1)
        if first_positive > 0 and last_positive > 0:
            cagr = (last_positive / first_positive) ** (1 / periods) - 1

    trend_components = rates[-3:]
    if cagr is not None:
        trend_components.append(cagr)

    growth = average(trend_components, default=0.05)
    return clamp(growth, -0.03, 0.12)

def resolve_starting_free_cash_flow(fcf_history):
    if not fcf_history:
        return None, None

    latest_value = safe_float(fcf_history[-1].get('value'))
    if latest_value is not None and latest_value > 0:
        return latest_value, 'latest'

    recent_positive = [
        safe_float(item.get('value'))
        for item in fcf_history[-3:]
        if safe_float(item.get('value')) is not None and safe_float(item.get('value')) > 0
    ]
    if recent_positive:
        return average(recent_positive), 'recent_positive_average'

    return None, None

def compute_projection_series(starting_fcf, growth_rate, years):
    projected = []
    current_fcf = starting_fcf

    for year in range(1, years + 1):
        current_fcf = current_fcf * (1 + growth_rate)
        projected.append({
            'year': year,
            'label': f"Year {year}",
            'fcf': current_fcf
        })

    return projected

def run_dcf_scenario(starting_fcf, discount_rate, terminal_growth_rate, growth_rate, years, cash, debt, shares_outstanding, current_price):
    projected = compute_projection_series(starting_fcf, growth_rate, years)
    discounted_cashflows = []

    for item in projected:
        year = item['year']
        present_value = item['fcf'] / ((1 + discount_rate) ** year)
        discounted_cashflows.append({
            **item,
            'present_value': present_value
        })

    last_fcf = projected[-1]['fcf']
    terminal_value = last_fcf * (1 + terminal_growth_rate) / max(discount_rate - terminal_growth_rate, 0.01)
    present_terminal_value = terminal_value / ((1 + discount_rate) ** years)
    enterprise_value = sum(item['present_value'] for item in discounted_cashflows) + present_terminal_value
    equity_value = enterprise_value + cash - debt
    fair_value_per_share = equity_value / shares_outstanding if shares_outstanding else None
    upside_pct = None
    if fair_value_per_share is not None and current_price and current_price > 0:
        upside_pct = ((fair_value_per_share / current_price) - 1) * 100

    return {
        'discount_rate': discount_rate,
        'terminal_growth_rate': terminal_growth_rate,
        'growth_rate': growth_rate,
        'projected_cashflows': discounted_cashflows,
        'terminal_value': terminal_value,
        'present_terminal_value': present_terminal_value,
        'enterprise_value': enterprise_value,
        'equity_value': equity_value,
        'fair_value_per_share': fair_value_per_share,
        'upside_pct': round(upside_pct, 2) if upside_pct is not None else None
    }

def classify_dcf_valuation(upside_pct):
    if upside_pct is None:
        return 'N/A'
    if upside_pct >= 15:
        return 'Undervalued'
    if upside_pct <= -15:
        return 'Overvalued'
    return 'Fairly valued'

def get_dcf_valuation(symbol: str, snapshot: Dict[str, Any] | None = None):
    cache_key = f"dcf_valuation:{symbol}"

    def loader():
        stock = get_ticker(symbol)
        info = stock.info
        quote = snapshot or get_performance_snapshot(symbol)
        current_price = safe_float((quote or {}).get('current_price')) or safe_float(info.get('currentPrice')) or safe_float(info.get('regularMarketPrice'))

        fcf_history, fcf_source = get_free_cash_flow_history(stock)
        starting_fcf, starting_source = resolve_starting_free_cash_flow(fcf_history)
        shares_outstanding, shares_source = resolve_shares_outstanding(info, current_price)
        cash, debt = resolve_balance_sheet_inputs(info)

        warnings = []
        if not fcf_history:
            warnings.append('Free cash flow history was not available from the current data feed.')
        if not shares_outstanding:
            warnings.append('Shares outstanding could not be confirmed, so a per-share fair value could not be calculated.')
        if current_price is None:
            warnings.append('Current market price was unavailable during this request.')
        if starting_fcf is None:
            warnings.append('Recent free cash flow is too limited or negative for this simplified DCF model.')

        projection_years = 5
        beta = safe_float(info.get('beta'), 1.0) or 1.0
        base_discount_rate = clamp(0.09 + (beta * 0.01), 0.085, 0.12)
        base_terminal_growth = 0.025
        base_growth = calculate_recent_fcf_growth(fcf_history) if fcf_history else 0.05

        if starting_fcf is None or shares_outstanding is None:
            return {
                'available': False,
                'status': 'unavailable',
                'message': 'There is not enough cash flow data to build a reliable DCF estimate for this stock right now.',
                'current_price': current_price,
                'current_price_formatted': format_money(current_price),
                'projection_years': projection_years,
                'warnings': warnings,
                'assumptions': {
                    'discount_rate': round(base_discount_rate * 100, 2),
                    'terminal_growth_rate': round(base_terminal_growth * 100, 2),
                    'starting_fcf': None,
                    'starting_fcf_formatted': 'N/A'
                },
                'data_sources': {
                    'free_cash_flow': fcf_source,
                    'shares_outstanding': shares_source or 'unavailable',
                    'cash_and_debt': 'company summary fields'
                },
                'explanation': [
                    'This model estimates what the business may be worth based on the cash it can generate in the future.',
                    'For this ticker, the available cash flow data is too thin to show a clean fair value estimate.',
                    'That does not mean the stock is good or bad. It just means the data behind this simple DCF is not strong enough yet.'
                ]
            }

        base_case = run_dcf_scenario(
            starting_fcf=starting_fcf,
            discount_rate=base_discount_rate,
            terminal_growth_rate=base_terminal_growth,
            growth_rate=base_growth,
            years=projection_years,
            cash=cash,
            debt=debt,
            shares_outstanding=shares_outstanding,
            current_price=current_price
        )
        bull_case = run_dcf_scenario(
            starting_fcf=starting_fcf,
            discount_rate=max(base_discount_rate - 0.01, 0.08),
            terminal_growth_rate=0.03,
            growth_rate=clamp(base_growth + 0.03, 0.0, 0.18),
            years=projection_years,
            cash=cash,
            debt=debt,
            shares_outstanding=shares_outstanding,
            current_price=current_price
        )
        bear_case = run_dcf_scenario(
            starting_fcf=starting_fcf,
            discount_rate=min(base_discount_rate + 0.01, 0.13),
            terminal_growth_rate=0.02,
            growth_rate=clamp(base_growth - 0.03, -0.08, 0.08),
            years=projection_years,
            cash=cash,
            debt=debt,
            shares_outstanding=shares_outstanding,
            current_price=current_price
        )

        fair_value = base_case.get('fair_value_per_share')
        upside_pct = base_case.get('upside_pct')

        confidence = 'High' if len(fcf_history) >= 4 and 'derived' not in fcf_source else 'Medium'
        if 'quarterly' in fcf_source or len(fcf_history) < 3:
            confidence = 'Low'

        return {
            'available': True,
            'status': 'available',
            'valuation_label': classify_dcf_valuation(upside_pct),
            'confidence': confidence,
            'current_price': current_price,
            'current_price_formatted': format_money(current_price),
            'fair_value': fair_value,
            'fair_value_formatted': format_money(fair_value),
            'upside_pct': upside_pct,
            'projection_years': projection_years,
            'starting_fcf': starting_fcf,
            'starting_fcf_formatted': compact_number(starting_fcf),
            'net_cash_adjustment': cash - debt,
            'net_cash_adjustment_formatted': compact_number(cash - debt),
            'assumptions': {
                'discount_rate': round(base_case['discount_rate'] * 100, 2),
                'terminal_growth_rate': round(base_case['terminal_growth_rate'] * 100, 2),
                'growth_rate': round(base_case['growth_rate'] * 100, 2),
                'starting_fcf': starting_fcf,
                'starting_fcf_formatted': compact_number(starting_fcf)
            },
            'data_sources': {
                'free_cash_flow': fcf_source,
                'starting_fcf_basis': starting_source or 'latest',
                'shares_outstanding': shares_source or 'unavailable',
                'cash_and_debt': 'company summary fields'
            },
            'warnings': warnings,
            'explanation': [
                'This model estimates what the company may be worth based on the cash it can generate in the future.',
                "If the fair value is above today's price, the stock may look undervalued.",
                "If the fair value is below today's price, the stock may already look expensive."
            ],
            'formula_summary': {
                'cash_flow_present_value': 'PV = FCF_t / (1 + r)^t',
                'terminal_value': 'TV = FCF_last * (1 + g) / (r - g)',
                'enterprise_value': 'DCF = sum of discounted FCF + discounted TV'
            },
            'historical_fcf': [
                {
                    'label': item['label'],
                    'value': item['value']
                }
                for item in fcf_history[-5:]
            ],
            'base_case': {
                'fair_value': base_case['fair_value_per_share'],
                'fair_value_formatted': format_money(base_case['fair_value_per_share']),
                'enterprise_value': base_case['enterprise_value'],
                'enterprise_value_formatted': compact_number(base_case['enterprise_value']),
                'equity_value': base_case['equity_value'],
                'equity_value_formatted': compact_number(base_case['equity_value']),
                'upside_pct': base_case['upside_pct'],
                'discount_rate': round(base_case['discount_rate'] * 100, 2),
                'terminal_growth_rate': round(base_case['terminal_growth_rate'] * 100, 2),
                'growth_rate': round(base_case['growth_rate'] * 100, 2),
                'projected_cashflows': [
                    {
                        'label': item['label'],
                        'fcf': item['fcf'],
                        'present_value': item['present_value']
                    }
                    for item in base_case['projected_cashflows']
                ],
                'terminal_value': base_case['terminal_value'],
                'terminal_value_formatted': compact_number(base_case['terminal_value']),
                'present_terminal_value': base_case['present_terminal_value'],
                'present_terminal_value_formatted': compact_number(base_case['present_terminal_value'])
            },
            'scenario_values': [
                {
                    'label': 'Bear',
                    'fair_value': bear_case['fair_value_per_share'],
                    'fair_value_formatted': format_money(bear_case['fair_value_per_share']),
                    'upside_pct': bear_case['upside_pct']
                },
                {
                    'label': 'Base',
                    'fair_value': base_case['fair_value_per_share'],
                    'fair_value_formatted': format_money(base_case['fair_value_per_share']),
                    'upside_pct': base_case['upside_pct']
                },
                {
                    'label': 'Bull',
                    'fair_value': bull_case['fair_value_per_share'],
                    'fair_value_formatted': format_money(bull_case['fair_value_per_share']),
                    'upside_pct': bull_case['upside_pct']
                }
            ]
        }

    return remember(cache_key, 1800, loader)

def get_price_history_payload(symbol: str, benchmark: str = '^GSPC'):
    cache_key = f"price_history:{symbol}:{benchmark}"

    def loader():
        stock_history = get_ticker(symbol).history(period='1y', interval='1d')
        benchmark_history = get_ticker(benchmark).history(period='1y', interval='1d')

        stock_history = stock_history.dropna(subset=['Close']) if not stock_history.empty else stock_history
        benchmark_history = benchmark_history.dropna(subset=['Close']) if not benchmark_history.empty else benchmark_history

        price_series = []
        performance_series = []
        comparison_series = []

        if not stock_history.empty:
            base_price = safe_float(stock_history['Close'].iloc[0])
            for idx, row in stock_history.iterrows():
                close_value = safe_float(row.get('Close'))
                if close_value is None:
                    continue
                label = normalize_timestamp_label(idx) if len(stock_history) <= 14 else pd.to_datetime(idx).strftime('%b %d')
                price_series.append({'label': label, 'value': close_value})
                if base_price:
                    performance_series.append({'label': label, 'value': round(((close_value / base_price) - 1) * 100, 2)})

        if not stock_history.empty and not benchmark_history.empty:
            stock_base = safe_float(stock_history['Close'].iloc[0])
            benchmark_base = safe_float(benchmark_history['Close'].iloc[0])
            joined = pd.DataFrame({
                'stock': stock_history['Close'],
                'benchmark': benchmark_history['Close']
            }).dropna()
            for idx, row in joined.iterrows():
                if not stock_base or not benchmark_base:
                    continue
                label = pd.to_datetime(idx).strftime('%b %d')
                comparison_series.append({
                    'label': label,
                    'stock': round(((safe_float(row['stock']) / stock_base) - 1) * 100, 2),
                    'benchmark': round(((safe_float(row['benchmark']) / benchmark_base) - 1) * 100, 2)
                })

        return {
            'price_series': price_series,
            'performance_series': performance_series,
            'comparison_series': comparison_series
        }

    return remember(cache_key, 300, loader)

def get_financial_trend_payload(symbol: str):
    cache_key = f"financial_trends:{symbol}"

    def loader():
        stock = get_ticker(symbol)
        quarterly = stock.quarterly_income_stmt
        revenue_row = get_statement_row(quarterly, ['Total Revenue', 'Operating Revenue', 'Revenue'])
        net_income_row = get_statement_row(quarterly, ['Net Income', 'Net Income Common Stockholders', 'Net Income Including Noncontrolling Interests'])

        if revenue_row.empty and net_income_row.empty:
            return {'revenue_series': [], 'net_income_series': []}

        labels = [normalize_timestamp_label(col) for col in quarterly.columns]
        revenue_values = [safe_float(revenue_row.get(col)) for col in quarterly.columns] if not revenue_row.empty else [None] * len(labels)
        income_values = [safe_float(net_income_row.get(col)) for col in quarterly.columns] if not net_income_row.empty else [None] * len(labels)

        return {
            'revenue_series': build_series_payload(labels, revenue_values),
            'net_income_series': build_series_payload(labels, income_values)
        }

    return remember(cache_key, 1800, loader)

def get_recommendation_summary(symbol: str):
    cache_key = f"recommendation_summary:{symbol}"

    def loader():
        stock = get_ticker(symbol)
        recommendations = stock.recommendations
        if recommendations is None or recommendations.empty:
            return {
                'breakdown': [],
                'headline': 'No analyst breakdown available',
                'consensus': 'N/A'
            }

        latest = recommendations.iloc[0]
        breakdown = [
            {'label': 'Strong Buy', 'value': safe_int(latest.get('strongBuy'), 0) or 0},
            {'label': 'Buy', 'value': safe_int(latest.get('buy'), 0) or 0},
            {'label': 'Hold', 'value': safe_int(latest.get('hold'), 0) or 0},
            {'label': 'Sell', 'value': safe_int(latest.get('sell'), 0) or 0},
            {'label': 'Strong Sell', 'value': safe_int(latest.get('strongSell'), 0) or 0},
        ]

        total = sum(item['value'] for item in breakdown)
        positive = breakdown[0]['value'] + breakdown[1]['value']
        hold = breakdown[2]['value']

        if total == 0:
            headline = 'No analyst votes available'
            consensus = 'N/A'
        elif positive / total >= 0.7:
            headline = 'Most analysts are positive'
            consensus = 'Bullish'
        elif hold / total >= 0.4:
            headline = 'Analysts are more mixed right now'
            consensus = 'Mixed'
        else:
            headline = 'Analyst view is cautious'
            consensus = 'Cautious'

        return {
            'breakdown': breakdown,
            'headline': headline,
            'consensus': consensus
        }

    return remember(cache_key, 1800, loader)

def get_recent_analyst_changes(symbol: str):
    cache_key = f"analyst_changes:{symbol}"

    def loader():
        stock = get_ticker(symbol)
        changes = []
        try:
            upgrades = stock.upgrades_downgrades
            if upgrades is not None and not upgrades.empty:
                latest = upgrades.head(6)
                for idx, row in latest.iterrows():
                    changes.append({
                        'date': pd.to_datetime(idx).tz_convert(MARKET_TZ).strftime('%b %d, %Y') if getattr(pd.to_datetime(idx), 'tzinfo', None) is not None else pd.to_datetime(idx).strftime('%b %d, %Y'),
                        'firm': row.get('Firm', 'N/A'),
                        'to_grade': row.get('ToGrade', row.get('To Grade', 'N/A')),
                        'from_grade': row.get('FromGrade', row.get('From Grade', 'N/A')),
                        'action': row.get('Action', 'update'),
                        'current_price_target': safe_float(row.get('currentPriceTarget'))
                    })
        except Exception as e:
            print(f"Error getting analyst changes for {symbol}: {e}")
        return changes

    return remember(cache_key, 1800, loader)

def get_price_targets(symbol: str):
    cache_key = f"price_targets:{symbol}"

    def loader():
        stock = get_ticker(symbol)
        targets = getattr(stock, 'analyst_price_targets', {}) or {}
        current = safe_float(targets.get('current'))
        low = safe_float(targets.get('low'))
        mean = safe_float(targets.get('mean'))
        high = safe_float(targets.get('high'))
        median = safe_float(targets.get('median'))

        upside_pct = None
        if current and mean:
            upside_pct = round(((mean / current) - 1) * 100, 2)

        return {
            'current': current,
            'low': low,
            'mean': mean,
            'median': median,
            'high': high,
            'upside_pct': upside_pct
        }

    return remember(cache_key, 1800, loader)

def get_performance_snapshot(symbol: str):
    cache_key = f"performance_snapshot:{symbol}"

    def loader():
        info = get_ticker(symbol).info
        quote = get_live_quotes([symbol])[0]
        return {
            'current_price': quote.get('current_price'),
            'price_formatted': quote.get('price_formatted'),
            'daily_change_pct': quote.get('daily_change_pct'),
            'daily_change_pct_formatted': quote.get('daily_change_pct_formatted'),
            'market_cap': compact_number(info.get('marketCap')),
            'forward_pe': round(float(info.get('forwardPE')), 2) if info.get('forwardPE') else 'N/A',
            'trailing_pe': round(float(info.get('trailingPE')), 2) if info.get('trailingPE') else 'N/A',
            'volume': f"{safe_int(info.get('volume'), 0):,}" if info.get('volume') else 'N/A',
            'avg_volume': f"{safe_int(info.get('averageVolume'), 0):,}" if info.get('averageVolume') else 'N/A',
            'fifty_two_week_range': f"{format_money(info.get('fiftyTwoWeekLow'))} - {format_money(info.get('fiftyTwoWeekHigh'))}" if info.get('fiftyTwoWeekLow') and info.get('fiftyTwoWeekHigh') else 'N/A',
            'sector': info.get('sectorDisp', info.get('sector', 'N/A')),
            'industry': info.get('industryDisp', info.get('industry', 'N/A')),
            'short_name': info.get('shortName', symbol),
            'long_name': info.get('longName', symbol),
            'website': info.get('website', ''),
            'summary': info.get('longBusinessSummary', '')
        }

    return remember(cache_key, 300, loader)

def get_sec_company_mapping():
    cache_key = 'sec_company_mapping'

    def loader():
        response = requests.get('https://www.sec.gov/files/company_tickers.json', headers=SEC_HEADERS, timeout=10)
        response.raise_for_status()
        payload = response.json()
        mapping = {}
        for item in payload.values():
            ticker = normalize_symbol(item.get('ticker', ''))
            if not ticker:
                continue
            mapping[ticker] = {
                'ticker': ticker,
                'title': item.get('title', ticker),
                'cik_str': str(item.get('cik_str', '')).zfill(10)
            }
        return mapping

    return remember(cache_key, 86400, loader)

def get_latest_10k_filing_info(symbol: str):
    normalized = normalize_symbol(symbol)
    cache_key = f"sec_latest_10k:{normalized}"

    def loader():
        company_map = get_sec_company_mapping()
        company = company_map.get(normalized)
        if not company:
            return None

        cik = company['cik_str']
        submissions_url = f'https://data.sec.gov/submissions/CIK{cik}.json'
        response = requests.get(submissions_url, headers=SEC_HEADERS, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        recent = payload.get('filings', {}).get('recent', {})
        forms = recent.get('form', [])
        accession_numbers = recent.get('accessionNumber', [])
        primary_documents = recent.get('primaryDocument', [])
        filing_dates = recent.get('filingDate', [])

        for form, accession, primary_document, filing_date in zip(forms, accession_numbers, primary_documents, filing_dates):
            if str(form).strip().upper() != '10-K':
                continue
            accession_no_dashes = str(accession).replace('-', '')
            filing_url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{primary_document}'
            return {
                'symbol': normalized,
                'company_name': company.get('title', normalized),
                'cik': cik,
                'form': '10-K',
                'filing_date': filing_date,
                'accession_number': accession,
                'filing_url': filing_url,
                'source': 'sec.edgar'
            }
        return None

    return remember(cache_key, 21600, loader)

def strip_html_to_text(html: str):
    text = re.sub(r'(?is)<script.*?>.*?</script>', ' ', html or '')
    text = re.sub(r'(?is)<style.*?>.*?</style>', ' ', text)
    text = re.sub(r'(?is)<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;|&#160;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&quot;|&#34;', '"', text)
    text = re.sub(r'&#39;|&apos;', "'", text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_10k_risk_section(text: str):
    clean = re.sub(r'\s+', ' ', text or '').strip()
    if not clean:
        return ''

    start_match = re.search(r'item\s+1a\.?\s+risk\s+factors', clean, flags=re.IGNORECASE)
    if not start_match:
        return ''

    search_start = start_match.end()
    end_match = re.search(r'item\s+1b\.?|item\s+2\.?', clean[search_start:], flags=re.IGNORECASE)
    end_index = search_start + end_match.start() if end_match else min(len(clean), search_start + 45000)
    section = clean[start_match.start():end_index].strip()
    return section[:45000]

def fallback_summarize_10k_risks(section_text: str):
    clean = re.sub(r'\s+', ' ', section_text or '').strip()
    if not clean:
        return []
    sentences = re.split(r'(?<=[.!?])\s+', clean)
    bullets = []
    for sentence in sentences:
        normalized = sentence.strip().strip('-').strip()
        if len(normalized) < 60:
            continue
        if not re.search(r'risk|could|may|adverse|competition|regulation|supply|demand|cyber|litigation|depend', normalized, flags=re.IGNORECASE):
            continue
        clipped = normalized[:180].rstrip(' ,;:')
        bullets.append(clipped)
        if len(bullets) >= 4:
            break
    return bullets

def summarize_10k_risk_section(symbol: str, company_name: str, section_text: str):
    cache_key = f"tenk_risk_summary:{normalize_symbol(symbol)}"

    def loader():
        prompt = f"""
        Summarize the main 10-K risks for {company_name} ({symbol}).
        Return valid JSON with:
        risks: array of 3 short bullet strings

        Rules:
        - Use simple readable language
        - Keep each bullet very short
        - Stay factual to the filing
        - Do not invent risks not grounded in the text
        - Do not include legal boilerplate
        - Focus only on the biggest business risks a trader or investor should understand quickly

        Filing risk text:
        {section_text[:18000]}
        """
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=260,
                response_format={"type": "json_object"}
            )
            parsed = json.loads(response.choices[0].message.content)
            risks = [str(item).strip() for item in parsed.get('risks', []) if str(item).strip()]
            return risks[:3]
        except Exception as e:
            print(f"Error summarizing 10-K risks for {symbol}: {e}")
            return fallback_summarize_10k_risks(section_text)[:3]

    return remember(cache_key, 21600, loader)

def get_ten_k_risk_summary(symbol: str):
    normalized = normalize_symbol(symbol)
    cache_key = f"tenk_payload:{normalized}"

    def loader():
        filing_info = get_latest_10k_filing_info(normalized)
        if not filing_info:
            return {
                'available': False,
                'symbol': normalized,
                'company_name': normalized,
                'risks': [],
                'message': '10-K risk summary not available for this stock.',
                'source': 'sec.edgar'
            }

        response = requests.get(filing_info['filing_url'], headers=SEC_HEADERS, timeout=12)
        response.raise_for_status()
        filing_text = strip_html_to_text(response.text)
        risk_section = extract_10k_risk_section(filing_text)
        if not risk_section:
            return {
                'available': False,
                'symbol': normalized,
                'company_name': filing_info.get('company_name', normalized),
                'risks': [],
                'message': '10-K risk summary not available for this stock.',
                'source': filing_info.get('source', 'sec.edgar'),
                'filing_date': filing_info.get('filing_date'),
                'filing_url': filing_info.get('filing_url')
            }

        risks = summarize_10k_risk_section(normalized, filing_info.get('company_name', normalized), risk_section)
        if not risks:
            return {
                'available': False,
                'symbol': normalized,
                'company_name': filing_info.get('company_name', normalized),
                'risks': [],
                'message': '10-K risk summary not available for this stock.',
                'source': filing_info.get('source', 'sec.edgar'),
                'filing_date': filing_info.get('filing_date'),
                'filing_url': filing_info.get('filing_url')
            }

        return {
            'available': True,
            'symbol': normalized,
            'company_name': filing_info.get('company_name', normalized),
            'risks': risks[:3],
            'message': '',
            'source': filing_info.get('source', 'sec.edgar'),
            'filing_date': filing_info.get('filing_date'),
            'filing_url': filing_info.get('filing_url'),
            'generated_at': market_now().isoformat()
        }

    return remember(cache_key, 21600, loader)

def generate_ai_summary(symbol: str, research_data: Dict[str, Any]):
    cache_key = f"ai_summary:{symbol}"

    def loader():
        if client is None:
            return {
                'primary_reason': 'AI summary is unavailable because the OpenAI API key is not configured.',
                'supporting_factors': ['The rest of the research dashboard can still load.', 'Live market data can still be used.', 'You can add the API key later without changing the app.'],
                'bullish_points': ['Core research tools remain available without AI.'],
                'bearish_risks': ['The written AI summary is currently disabled.'],
                'outlook': 'The dashboard can still be used for research, but the AI summary will stay in fallback mode until the API key is configured.',
                'confidence': 'Low'
            }

        metrics = research_data.get('snapshot', {})
        price_targets = research_data.get('price_targets', {})
        recommendation = research_data.get('recommendation_summary', {})
        earnings = research_data.get('earnings', [])
        news = research_data.get('news', [])
        ten_k_risks = research_data.get('ten_k_risks', {})

        prompt = f"""
        You are writing a simple stock research summary for a regular investor.
        Use short, plain-English language. Avoid jargon unless absolutely needed.
        Return valid JSON with these exact keys:
        primary_reason, supporting_factors, bullish_points, bearish_risks, outlook, confidence

        Rules:
        - primary_reason: one short sentence
        - supporting_factors: array with 2 to 3 short bullets
        - bullish_points: array with 2 short bullets
        - bearish_risks: array with 2 short bullets
        - outlook: one short paragraph, easy to understand
        - confidence: one of High, Medium, Low

        Stock: {symbol}
        Current price: {metrics.get('price_formatted')}
        1-day move: {metrics.get('daily_change_pct_formatted')}
        Sector: {metrics.get('sector')}
        Industry: {metrics.get('industry')}
        Analyst consensus: {recommendation.get('consensus')}
        Analyst headline: {recommendation.get('headline')}
        Price target range: {price_targets}
        Recent earnings: {earnings[:3]}
        Recent news headlines: {[item.get('title', '') for item in news[:4]]}
        10-K risk bullets: {ten_k_risks.get('risks', [])}

        Focus on what happened, why it matters, what could help the stock, what could hurt it, and the near-term outlook.
        """

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            parsed = json.loads(response.choices[0].message.content)
            return {
                'primary_reason': parsed.get('primary_reason', 'No clear AI summary available.'),
                'supporting_factors': parsed.get('supporting_factors', []),
                'bullish_points': parsed.get('bullish_points', []),
                'bearish_risks': parsed.get('bearish_risks', []),
                'outlook': parsed.get('outlook', ''),
                'confidence': parsed.get('confidence', 'Medium')
            }
        except Exception as e:
            print(f"Error generating AI summary for {symbol}: {e}")
            return {
                'primary_reason': 'The stock is moving on a mix of earnings, analyst opinion, and recent news.',
                'supporting_factors': ['Recent price action is active.', 'Analysts still care about the name.', 'Upcoming catalysts could move the stock again.'],
                'bullish_points': ['Analysts are still watching the stock closely.', 'The stock has clear catalysts ahead.'],
                'bearish_risks': ['The stock can swing hard after news or earnings.', 'If expectations are too high, even good results may not help.'],
                'outlook': 'This stock looks active, but the next move still depends on whether news and earnings keep supporting the story.',
                'confidence': 'Medium'
            }

    return remember(cache_key, 900, loader)

def build_empty_research_payload(symbol: str, error_message: str = '') -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    return {
        'symbol': normalized_symbol,
        'snapshot': {
            'current_price': None,
            'price_formatted': 'N/A',
            'daily_change_pct': None,
            'daily_change_pct_formatted': 'N/A',
            'market_cap': 'N/A',
            'forward_pe': 'N/A',
            'trailing_pe': 'N/A',
            'volume': 'N/A',
            'avg_volume': 'N/A',
            'fifty_two_week_range': 'N/A',
            'sector': 'N/A',
            'industry': 'N/A',
            'short_name': normalized_symbol,
            'long_name': normalized_symbol,
            'website': '',
            'summary': 'Stock data is temporarily unavailable.'
        },
        'price_history': {
            'price_series': [],
            'performance_series': [],
            'comparison_series': []
        },
        'financial_trends': {
            'revenue_series': [],
            'net_income_series': []
        },
        'recommendation_summary': {
            'breakdown': [],
            'headline': 'Recommendation data is unavailable right now.',
            'consensus': 'N/A'
        },
        'analyst_changes': [],
        'price_targets': {
            'current': None,
            'low': None,
            'mean': None,
            'median': None,
            'high': None,
            'upside_pct': None
        },
        'earnings': [],
        'news': [],
        'news_impact': [],
        'ten_k_risks': {
            'available': False,
            'symbol': normalized_symbol,
            'company_name': normalized_symbol,
            'risks': [],
            'message': '10-K risk summary not available for this stock.',
            'source': 'sec.edgar'
        },
        'dcf_valuation': {
            'available': False,
            'status': 'unavailable',
            'message': 'DCF is unavailable because the research data could not be loaded cleanly.',
            'current_price': None,
            'current_price_formatted': 'N/A',
            'projection_years': 5,
            'warnings': [error_message] if error_message else [],
            'assumptions': {
                'discount_rate': 10.0,
                'terminal_growth_rate': 2.5,
                'starting_fcf': None,
                'starting_fcf_formatted': 'N/A'
            },
            'data_sources': {
                'free_cash_flow': 'unavailable',
                'shares_outstanding': 'unavailable',
                'cash_and_debt': 'unavailable'
            },
            'explanation': [
                'This model estimates what the business may be worth based on future cash flow.',
                'This time the research feed did not return enough clean data to build the estimate.'
            ]
        },
        'ai_summary': {
            'primary_reason': 'The research dashboard is available, but some live data did not load.',
            'supporting_factors': ['Try the ticker again in a moment.', 'Some providers can return incomplete data.', 'The dashboard falls back instead of crashing.'],
            'bullish_points': ['If data refreshes cleanly, the full view will return.'],
            'bearish_risks': ['Live feeds can be incomplete for some requests.'],
            'outlook': 'Use the dashboard as a starting point, and refresh if live data is temporarily missing.',
            'confidence': 'Low'
        },
        'meta': {
            'status': 'degraded',
            'error': error_message or 'Research data unavailable'
        }
    }

def get_stock_research_data(symbol: str) -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    cache_key = f"stock_research:{normalized_symbol}"

    def loader():
        payload = build_empty_research_payload(normalized_symbol)

        def load_parallel(name, fn, fallback):
            return name, safe_component(name, fn, fallback)

        work_items = [
            ('snapshot', lambda: get_performance_snapshot(normalized_symbol), lambda: payload['snapshot']),
            ('price_history', lambda: get_price_history_payload(normalized_symbol), lambda: payload['price_history']),
            ('financial_trends', lambda: get_financial_trend_payload(normalized_symbol), lambda: payload['financial_trends']),
            ('recommendation_summary', lambda: get_recommendation_summary(normalized_symbol), lambda: payload['recommendation_summary']),
            ('analyst_changes', lambda: get_recent_analyst_changes(normalized_symbol), []),
            ('price_targets', lambda: get_price_targets(normalized_symbol), lambda: payload['price_targets']),
            ('earnings', lambda: get_detailed_earnings(normalized_symbol), []),
            ('news_data', lambda: get_stock_news_analysis(normalized_symbol), {'news': [], 'earnings': [], 'analyst_changes': []}),
            ('ten_k_risks', lambda: get_ten_k_risk_summary(normalized_symbol), lambda: payload['ten_k_risks'])
        ]
        results = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(load_parallel, name, fn, fallback): name
                for name, fn, fallback in work_items
            }
            for future in as_completed(futures):
                name, value = future.result()
                results[name] = value

        snapshot = results.get('snapshot', payload['snapshot'])
        price_history = results.get('price_history', payload['price_history'])
        financial_trends = results.get('financial_trends', payload['financial_trends'])
        recommendation_summary = results.get('recommendation_summary', payload['recommendation_summary'])
        analyst_changes = results.get('analyst_changes', [])
        price_targets = results.get('price_targets', payload['price_targets'])
        earnings = results.get('earnings', [])
        news_data = results.get('news_data', {'news': [], 'earnings': [], 'analyst_changes': []})
        ten_k_risks = results.get('ten_k_risks', payload['ten_k_risks'])
        dcf_valuation = safe_component('dcf valuation', lambda: get_dcf_valuation(normalized_symbol, snapshot=snapshot), lambda: payload['dcf_valuation'])

        research = {
            'symbol': normalized_symbol,
            'snapshot': snapshot,
            'price_history': price_history,
            'financial_trends': financial_trends,
            'recommendation_summary': recommendation_summary,
            'analyst_changes': analyst_changes,
            'price_targets': price_targets,
            'earnings': earnings,
            'news': news_data.get('news', []),
            'news_impact': news_data.get('news', [])[:5],
            'ten_k_risks': ten_k_risks,
            'dcf_valuation': dcf_valuation
        }
        research['ai_summary'] = safe_component('ai summary', lambda: generate_ai_summary(normalized_symbol, research), lambda: payload['ai_summary'])
        research['meta'] = {
            'status': 'ok',
            'has_dcf': bool((dcf_valuation or {}).get('available')),
            'has_news': bool(research['news']),
            'has_earnings': bool(research['earnings']),
            'has_ten_k_risks': bool((ten_k_risks or {}).get('available'))
        }
        return research

    return remember(cache_key, 300, loader)

# =========================
# AI Analysis Functions
# =========================
def analyze_stock_move(symbol: str) -> str:
    """Analyze why a stock moved using AI and real data"""
    try:
        stock = get_ticker(symbol)
        
        # Get recent price change
        hist = stock.history(period="5d")
        if hist.empty:
            return f"Unable to fetch price data for {symbol}"
            
        current_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2] if len(hist) >= 2 else None
        
        change_pct = ((current_price - prev_price) / prev_price * 100) if prev_price else 0
        
        # Get recent news
        news_items = []
        news_text = "No recent news found"
        try:
            search = get_search(symbol, news_count=8)
            news_items = getattr(search, "news", [])
            if news_items:
                news_text = "\n".join([f"- {item.get('title', '')}" for item in news_items[:5]])
        except:
            pass
        
        # Get recent earnings
        earnings = get_detailed_earnings(symbol)
        latest_earnings = earnings[0] if earnings else None
        
        # Get analyst data
        analyst_data = "Not available"
        try:
            recs = stock.recommendations
            if recs is not None and not recs.empty:
                latest_rec = recs.iloc[-1]
                analyst_data = f"Latest: {latest_rec.get('To Grade', 'N/A') if 'To Grade' in latest_rec else 'N/A'}"
        except:
            pass
        
        # Use AI to analyze
        prompt = f"""
        Analyze why {symbol} moved {change_pct:.2f}% recently.
        
        Recent News:
        {news_text}
        
        Latest Earnings Data:
        {latest_earnings if latest_earnings else 'No recent earnings in past 3 months'}
        
        Analyst Updates:
        {analyst_data}
        
        Provide a concise, practical analysis with:
        1. PRIMARY REASON: What caused the move (earnings, news, analyst, market sentiment)
        2. KEY SUPPORTING FACTORS: 2-3 additional reasons
        3. BRIEF OUTLOOK: What to watch for next
        
        Be specific and reference the data provided. Keep it under 200 words.
        """
        
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=400
        )
        
        return response.choices[0].message.content
    except Exception as e:
        return f"Error analyzing stock move: {str(e)}"

def get_stock_news_analysis(symbol: str) -> Dict:
    """Get comprehensive news analysis for a stock"""
    try:
        stock = get_ticker(symbol)
        
        # Get news
        news_items = []
        try:
            search = get_search(symbol, news_count=10)
            news_items = getattr(search, "news", [])
        except:
            pass
        
        # Get earnings
        earnings = get_detailed_earnings(symbol)
        
        # Get analyst changes
        analyst_changes = []
        try:
            recs = stock.recommendations
            if recs is not None and not recs.empty:
                latest = recs.iloc[-3:]
                for _, row in latest.iterrows():
                    analyst_changes.append({
                        'date': row.name.strftime('%Y-%m-%d') if hasattr(row.name, 'strftime') else str(row.name),
                        'firm': row.get('Firm', 'N/A'),
                        'to_grade': row.get('To Grade', 'N/A'),
                        'from_grade': row.get('From Grade', 'N/A')
                    })
        except:
            pass
        
        return {
            'symbol': symbol,
            'news': news_items[:8],
            'earnings': earnings[:3],
            'analyst_changes': analyst_changes
        }
    except Exception as e:
        return {'error': str(e)}

# =========================
# Trade Management
# =========================
TRADE_TYPES = ['Earnings', 'Swing', 'Long-term', 'Day Trade', 'News', 'Other']
TRADE_SIDES = ['Long', 'Short']
TRADE_MONITOR_INTERVAL_SECONDS = 60
TRADE_MONITOR_STARTED = False
TRADE_MONITOR_LOCK = Lock()
TRADE_MONITOR_STOP = Event()

def parse_trade_datetime(value):
    if not value:
        return None
    try:
        timestamp = pd.to_datetime(value)
        if pd.isna(timestamp):
            return None
        if getattr(timestamp, 'tzinfo', None) is not None:
            timestamp = timestamp.tz_convert(MARKET_TZ).tz_localize(None)
        return timestamp
    except Exception:
        return None

def to_trade_datetime_input(value):
    timestamp = parse_trade_datetime(value)
    return timestamp.strftime('%Y-%m-%dT%H:%M') if timestamp is not None else ''

def iso_from_trade_datetime(value, fallback=None):
    timestamp = parse_trade_datetime(value)
    if timestamp is not None:
        localized = timestamp.replace(tzinfo=MARKET_TZ)
        return localized.isoformat()
    return fallback

def get_trade_duration_label(start_value, end_value=None):
    start = parse_trade_datetime(start_value)
    end = parse_trade_datetime(end_value) or market_now().replace(tzinfo=None)
    if start is None:
        return 'N/A'

    delta = end - start
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    days, remainder = divmod(total_minutes, 1440)
    hours, minutes = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return ' '.join(parts)

def safe_round(value, digits=2):
    numeric = safe_float(value)
    if numeric is None:
        return None
    return round(numeric, digits)

def normalize_trade_side(value, default='Long'):
    side = str(value or default).strip().title()
    return side if side in TRADE_SIDES else default

def trade_side_multiplier(side):
    return -1 if normalize_trade_side(side) == 'Short' else 1

def normalize_trade_payload(data, existing=None):
    existing = existing or {}
    def coalesce(key, default=None):
        value = data.get(key) if isinstance(data, dict) else None
        return existing.get(key, default) if value is None else value

    symbol = normalize_symbol(data.get('symbol', existing.get('symbol', '')))
    created_at = existing.get('created_at') or market_now().isoformat()
    entry_timestamp = iso_from_trade_datetime(
        data.get('entry_datetime') or data.get('entry_date') or existing.get('entry_datetime') or existing.get('entry_date') or created_at,
        fallback=created_at
    )
    exit_timestamp = iso_from_trade_datetime(
        data.get('exit_datetime') or data.get('exit_date') or existing.get('exit_datetime') or existing.get('exit_date'),
        fallback=existing.get('exit_datetime') or existing.get('exit_date')
    )
    trade = {
        **existing,
        'symbol': symbol,
        'trade_type': data.get('trade_type', existing.get('trade_type', 'Other')) if data.get('trade_type', existing.get('trade_type', 'Other')) in TRADE_TYPES else 'Other',
        'position_side': normalize_trade_side(coalesce('position_side', existing.get('position_side', 'Long'))),
        'entry_price': safe_float(data.get('entry_price', existing.get('entry_price'))),
        'exit_price': safe_float(data.get('exit_price', existing.get('exit_price'))),
        'shares': safe_float(data.get('shares', existing.get('shares'))),
        'stop_loss': safe_float(data.get('stop_loss', existing.get('stop_loss'))),
        'take_profit': safe_float(data.get('take_profit', existing.get('take_profit'))),
        'notes': str(data.get('notes', existing.get('notes', '')) or ''),
        'setup_notes': str(data.get('setup_notes', existing.get('setup_notes', '')) or ''),
        'thesis': str(data.get('thesis', existing.get('thesis', '')) or ''),
        'status': data.get('status', existing.get('status', 'open')) or 'open',
        'earnings_date': coalesce('earnings_date'),
        'created_at': created_at,
        'entry_datetime': entry_timestamp,
        'entry_date': entry_timestamp.split('T')[0] if entry_timestamp else existing.get('entry_date'),
        'force_close_datetime': iso_from_trade_datetime(
            data.get('force_close_datetime') or data.get('force_close_date') or existing.get('force_close_datetime') or existing.get('force_close_date'),
            fallback=existing.get('force_close_datetime') or existing.get('force_close_date')
        ),
        'exit_datetime': exit_timestamp,
        'exit_date': exit_timestamp.split('T')[0] if exit_timestamp else existing.get('exit_date'),
        'close_reason': coalesce('close_reason'),
        'exit_reason': coalesce('exit_reason'),
        'auto_closed': bool(data.get('auto_closed', existing.get('auto_closed', False))),
        'review': existing.get('review'),
        'setup_profile': coalesce('setup_profile'),
        'trade_features': coalesce('trade_features'),
        'trade_insights': coalesce('trade_insights'),
        'score_payload': coalesce('score_payload'),
        'monitoring': existing.get('monitoring', {}),
        'result': existing.get('result'),
        'return_percent': existing.get('return_percent'),
        'stock_symbol': symbol,
        'id': existing.get('id')
    }
    if trade.get('exit_price') is None:
        trade['status'] = 'open'
        trade['exit_datetime'] = None
        trade['exit_date'] = None
        trade['close_reason'] = None
        trade['exit_reason'] = None
        trade['auto_closed'] = False
        trade['review'] = None
        trade['result'] = None
        trade['return_percent'] = None
    return trade

def calculate_trade_stats(trade, current_price=None):
    entry = safe_float(trade.get('entry_price'))
    stop = safe_float(trade.get('stop_loss'))
    target = safe_float(trade.get('take_profit'))
    shares = safe_float(trade.get('shares'))
    exit_price = safe_float(trade.get('exit_price'))
    reference_price = safe_float(current_price) if current_price is not None else exit_price
    side = normalize_trade_side(trade.get('position_side'))
    multiplier = trade_side_multiplier(side)

    stats = {
        'risk_amount': None,
        'reward_amount': None,
        'total_risk': None,
        'total_reward': None,
        'risk_reward_ratio': None,
        'profit_loss': None,
        'profit_pct': None,
        'live_profit_loss': None,
        'live_profit_pct': None,
        'distance_to_stop': None,
        'distance_to_stop_pct': None,
        'distance_to_target': None,
        'distance_to_target_pct': None,
        'outcome': trade.get('outcome')
    }

    if entry is None or shares is None:
        return stats

    if stop is not None:
        stats['risk_amount'] = safe_round((entry - stop) * multiplier)
        stats['total_risk'] = safe_round(((entry - stop) * multiplier) * shares)
    if target is not None:
        stats['reward_amount'] = safe_round((target - entry) * multiplier)
        stats['total_reward'] = safe_round(((target - entry) * multiplier) * shares)
    if stats['risk_amount'] not in (None, 0) and stats['reward_amount'] is not None:
        stats['risk_reward_ratio'] = safe_round(stats['reward_amount'] / stats['risk_amount'])

    if exit_price is not None:
        profit_loss = (exit_price - entry) * shares * multiplier
        stats['profit_loss'] = safe_round(profit_loss)
        stats['profit_pct'] = safe_round((((exit_price - entry) * multiplier) / entry) * 100) if entry else None
        if profit_loss > 0:
            stats['outcome'] = 'win'
        elif profit_loss < 0:
            stats['outcome'] = 'loss'
        else:
            stats['outcome'] = 'flat'

    if reference_price is not None:
        live_profit = (reference_price - entry) * shares * multiplier
        stats['live_profit_loss'] = safe_round(live_profit)
        stats['live_profit_pct'] = safe_round((((reference_price - entry) * multiplier) / entry) * 100) if entry else None
        if stop is not None:
            stats['distance_to_stop'] = safe_round((reference_price - stop) * multiplier)
            stats['distance_to_stop_pct'] = safe_round((((reference_price - stop) * multiplier) / stop) * 100) if stop else None
        if target is not None:
            stats['distance_to_target'] = safe_round((target - reference_price) * multiplier)
            stats['distance_to_target_pct'] = safe_round((((target - reference_price) * multiplier) / reference_price) * 100) if reference_price else None

    return stats

def extract_trade_setup_profile(symbol: str, trade_type: str):
    research = safe_component('trade setup profile', lambda: get_stock_research_data(symbol), lambda: build_empty_research_payload(symbol))
    snapshot = research.get('snapshot', {})
    earnings = research.get('earnings', [])
    latest_earnings = earnings[0] if earnings else {}
    surprise_pct = safe_float(latest_earnings.get('surprise_pct'))
    earnings_signal = 'Neutral'
    if surprise_pct is not None:
        if surprise_pct > 5:
            earnings_signal = 'Recent earnings beat'
        elif surprise_pct < -5:
            earnings_signal = 'Recent earnings miss'

    profile = build_trade_memory_features(symbol, research=research, trade_type=trade_type)
    profile['news_count'] = len(research.get('news_impact', []))
    profile['earnings_signal'] = earnings_signal
    profile['momentum_label'] = {
        'up': 'Strong positive momentum',
        'down': 'Weak momentum',
        'sideways': 'Mixed'
    }.get(profile.get('price_trend'), 'Mixed')
    profile['recent_performance_pct'] = profile.get('recent_price_change_pct')
    profile['earnings_date'] = profile.get('next_earnings_date')
    profile['stock_symbol'] = normalize_symbol(symbol)
    return sanitize_for_json(profile)

def build_trade_review(trade):
    setup = trade.get('setup_profile') or {}
    insight_data = trade.get('trade_insights') or {}
    score_payload = trade.get('score_payload') or {}
    pnl = safe_float(trade.get('profit_loss'))
    outcome = trade.get('outcome') or ('win' if pnl and pnl > 0 else 'loss' if pnl and pnl < 0 else 'flat')
    positives = insight_data.get('key_positives') or score_payload.get('key_positives') or []
    risks = insight_data.get('key_risks') or score_payload.get('key_risks') or []
    context_notes = []
    if setup.get('setup_type'):
        context_notes.append(f"Setup type: {setup.get('setup_type')}.")
    if setup.get('expected_move_pct') is not None:
        context_notes.append(f"Expected move was around {setup.get('expected_move_pct')}%.")
    if trade.get('earnings_date') not in (None, 'N/A'):
        context_notes.append(f"Earnings date tracked: {trade.get('earnings_date')}.")
    if insight_data.get('memory_summary'):
        context_notes.append(insight_data.get('memory_summary'))

    if outcome == 'win':
        summary = 'This trade worked because the pre-earnings setup had enough support and avoided the biggest warning signs.'
    elif outcome == 'loss':
        summary = 'This trade failed because the warning signs outweighed the setup support after entry.'
    else:
        summary = 'This trade finished flat, so the setup did not create a meaningful edge.'

    return sanitize_for_json({
        'summary': summary,
        'positive_factors': positives[:4] or ['No clear positive factor stood out.'],
        'negative_factors': risks[:4] or ['No clear negative factor stood out.'],
        'context_notes': context_notes[:4],
        'setup_profile': setup,
        'outcome': outcome,
        'main_reason': insight_data.get('main_reason'),
        'secondary_reason': insight_data.get('secondary_reason'),
        'confidence_score': insight_data.get('confidence_score')
    })

def close_trade_record(trade, exit_price, exit_datetime=None, close_reason='manual', auto_closed=False):
    trade['exit_price'] = safe_float(exit_price)
    trade['exit_datetime'] = iso_from_trade_datetime(exit_datetime, fallback=market_now().isoformat())
    trade['exit_date'] = trade['exit_datetime'].split('T')[0] if trade.get('exit_datetime') else trade.get('exit_date')
    trade['closed_at'] = trade['exit_datetime']
    trade['status'] = 'closed'
    trade['close_reason'] = close_reason
    trade['exit_reason'] = close_reason
    trade['auto_closed'] = auto_closed
    trade.update(calculate_trade_stats(trade, current_price=trade.get('exit_price')))
    trade['result'] = trade.get('outcome')
    trade['return_percent'] = trade.get('profit_pct')
    trade['review'] = build_trade_review(trade)
    return trade

def ensure_trade_defaults(trade, fallback_id):
    normalized = normalize_trade_payload(trade, existing=trade)
    normalized['id'] = trade.get('id', fallback_id)
    existing_setup = trade.get('setup_profile') or trade.get('trade_features') or {}
    if not all(existing_setup.get(key) for key in TRADE_MEMORY_FEATURE_KEYS):
        if normalized.get('status') == 'open' and normalized.get('symbol'):
            existing_setup = extract_trade_setup_profile(normalized['symbol'], normalized.get('trade_type', 'Other'))
        else:
            existing_setup = {
                **existing_setup,
                'trade_type': normalized.get('trade_type', 'Other'),
                'sector': existing_setup.get('sector', 'N/A'),
                'industry': existing_setup.get('industry', 'N/A'),
                'setup_type': existing_setup.get('setup_type', 'neutral')
            }
    normalized['setup_profile'] = existing_setup
    normalized['trade_features'] = normalized['setup_profile']
    normalized.update(calculate_trade_stats(normalized, current_price=normalized.get('exit_price')))
    normalized['earnings_date'] = normalized.get('earnings_date') or (normalized.get('setup_profile') or {}).get('earnings_date')
    normalized['result'] = normalized.get('outcome')
    normalized['return_percent'] = normalized.get('profit_pct')
    if normalized.get('status') == 'closed' and not normalized.get('review'):
        normalized['review'] = build_trade_review(normalized)
    return sanitize_for_json(normalized)

def prepare_trades(trades):
    prepared = []
    for index, trade in enumerate(trades, start=1):
        prepared.append(ensure_trade_defaults(trade, index))
    return prepared

def refresh_open_trade_monitor(trades):
    prepared = prepare_trades(trades)
    open_symbols = sorted({trade['symbol'] for trade in prepared if trade.get('status') == 'open' and trade.get('symbol')})
    quote_map = {item['symbol']: item for item in get_live_quotes(open_symbols)} if open_symbols else {}

    for trade in prepared:
        if trade.get('status') != 'open':
            continue
        quote = quote_map.get(trade.get('symbol'), {})
        current_price = safe_float(quote.get('current_price'))
        trade['monitoring'] = {
            'current_price': current_price,
            'current_price_formatted': format_money(current_price),
            'updated_at': quote.get('updated_at')
        }
        trade.update(calculate_trade_stats(trade, current_price=current_price))

        stop = safe_float(trade.get('stop_loss'))
        target = safe_float(trade.get('take_profit'))
        side = normalize_trade_side(trade.get('position_side'))
        force_close_at = parse_trade_datetime(trade.get('force_close_datetime'))
        alerts = []
        if force_close_at and market_now().replace(tzinfo=None) >= force_close_at:
            alerts.append('time-exit due')

        if current_price is not None:
            if side == 'Short':
                if target is not None and current_price <= target:
                    alerts.append('target reached')
                if stop is not None and current_price >= stop:
                    alerts.append('stop reached')
            else:
                if target is not None and current_price >= target:
                    alerts.append('target reached')
                if stop is not None and current_price <= stop:
                    alerts.append('stop reached')

        trade['monitoring']['alerts'] = alerts

    save_trades(prepared)
    return prepared

def enrich_trade_for_display(trade):
    live_price = safe_float((trade.get('monitoring') or {}).get('current_price'))
    stats = calculate_trade_stats(trade, current_price=live_price if trade.get('status') == 'open' else trade.get('exit_price'))
    trade = {**trade, **stats}
    reference_price = live_price if trade.get('status') == 'open' else safe_float(trade.get('exit_price'))
    trade['entry_datetime_display'] = to_trade_datetime_input(trade.get('entry_datetime'))
    trade['force_close_datetime_display'] = to_trade_datetime_input(trade.get('force_close_datetime'))
    trade['exit_datetime_display'] = to_trade_datetime_input(trade.get('exit_datetime'))
    trade['duration_label'] = get_trade_duration_label(trade.get('entry_datetime'), trade.get('exit_datetime'))
    trade['current_price'] = reference_price
    trade['current_price_formatted'] = format_money(reference_price)
    trade['result_label'] = {'win': 'Win', 'loss': 'Loss', 'flat': 'Flat'}.get(trade.get('outcome'), 'Open')
    trade['result'] = trade.get('outcome')
    trade['return_percent'] = trade.get('profit_pct')
    trade['stock_symbol'] = trade.get('symbol')
    trade['trade_features'] = trade.get('trade_features') or trade.get('setup_profile')
    trade['position_side'] = normalize_trade_side(trade.get('position_side'))
    return sanitize_for_json(trade)

def run_trade_monitor_loop():
    while not TRADE_MONITOR_STOP.wait(TRADE_MONITOR_INTERVAL_SECONDS):
        try:
            refresh_open_trade_monitor(load_trades())
        except Exception as error:
            print(f"Error in background trade monitor: {error}")

def ensure_trade_monitor_started():
    global TRADE_MONITOR_STARTED
    if TRADE_MONITOR_STARTED:
        return
    with TRADE_MONITOR_LOCK:
        if TRADE_MONITOR_STARTED:
            return
        worker = Thread(target=run_trade_monitor_loop, daemon=True, name='trade-monitor')
        worker.start()
        TRADE_MONITOR_STARTED = True

def get_trade_summary(trades):
    closed_trades = [enrich_trade_for_display(trade) for trade in trades if trade.get('status') == 'closed' and trade.get('profit_loss') is not None]
    open_trades = [enrich_trade_for_display(trade) for trade in trades if trade.get('status') == 'open']

    if not closed_trades:
        return {
            'show_metrics': False,
            'empty_message': 'Start logging trades to unlock your performance metrics.',
            'total_trades': len(trades),
            'open_trades': len(open_trades),
            'closed_trades': 0
        }

    wins = [trade for trade in closed_trades if trade.get('profit_loss', 0) > 0]
    losses = [trade for trade in closed_trades if trade.get('profit_loss', 0) < 0]
    total_pnl = sum(trade.get('profit_loss', 0) for trade in closed_trades)
    total_wins = sum(trade.get('profit_loss', 0) for trade in wins)
    total_losses = abs(sum(trade.get('profit_loss', 0) for trade in losses))
    avg_duration = average([
        (parse_trade_datetime(trade.get('exit_datetime')) - parse_trade_datetime(trade.get('entry_datetime'))).total_seconds() / 3600
        for trade in closed_trades
        if parse_trade_datetime(trade.get('entry_datetime')) and parse_trade_datetime(trade.get('exit_datetime'))
    ], default=0)

    return {
        'show_metrics': True,
        'total_trades': len(trades),
        'open_trades': len(open_trades),
        'closed_trades': len(closed_trades),
        'win_rate': round(len(wins) / len(closed_trades) * 100, 1) if closed_trades else 0,
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(total_wins / len(wins), 2) if wins else 0,
        'avg_loss': round(total_losses / len(losses), 2) if losses else 0,
        'largest_win': round(max([trade.get('profit_loss', 0) for trade in wins]), 2) if wins else 0,
        'largest_loss': round(min([trade.get('profit_loss', 0) for trade in losses]), 2) if losses else 0,
        'profit_factor': round(total_wins / total_losses, 2) if total_losses > 0 else 0,
        'live_open_pnl': round(sum(trade.get('live_profit_loss', 0) or 0 for trade in open_trades), 2),
        'average_duration_hours': round(avg_duration, 1) if avg_duration else 0,
        'best_trade_type': max(
            ({trade.get('trade_type', 'Other'): 0 for trade in closed_trades} | {
                trade_type: sum((trade.get('profit_loss') or 0) for trade in closed_trades if trade.get('trade_type') == trade_type)
                for trade_type in {trade.get('trade_type', 'Other') for trade in closed_trades}
            }).items(),
            key=lambda item: item[1]
        )[0] if closed_trades else 'N/A'
    }

def summarize_factor_frequency(closed_trades, factor_bucket):
    counts = {}
    for trade in closed_trades:
        review = trade.get('review') or {}
        for item in review.get(factor_bucket, []):
            counts[item] = counts.get(item, 0) + 1
    return sorted(
        [{'label': label, 'count': count} for label, count in counts.items()],
        key=lambda item: (-item['count'], item['label'])
    )

def build_pattern_insights(trades):
    closed_trades = [enrich_trade_for_display(trade) for trade in trades if trade.get('status') == 'closed' and trade.get('profit_loss') is not None]
    pattern_stats = update_pattern_stats(trades) if closed_trades else load_pattern_stats()
    if not closed_trades:
        return {
            'best_trade_types': [],
            'worst_trade_types': [],
            'winning_factors': [],
            'losing_factors': [],
            'strongest_setups': [],
            'weakest_setups': [],
            'learned_patterns': [],
            'pattern_insights': ['Log a few closed trades and this page will start showing what is working best for you.'],
            'future_suggestion_profile': {
                'boost_factors': [],
                'warning_factors': []
            }
        }

    by_type = []
    trade_types = sorted({trade.get('trade_type', 'Other') for trade in closed_trades})
    for trade_type in trade_types:
        subset = [trade for trade in closed_trades if trade.get('trade_type') == trade_type]
        win_rate = round(len([trade for trade in subset if trade.get('outcome') == 'win']) / len(subset) * 100, 1) if subset else 0
        pnl = round(sum(trade.get('profit_loss', 0) or 0 for trade in subset), 2)
        by_type.append({'label': trade_type, 'count': len(subset), 'win_rate': win_rate, 'pnl': pnl})

    by_sector = []
    sectors = sorted({(trade.get('setup_profile') or {}).get('sector', 'N/A') for trade in closed_trades})
    for sector in sectors:
        subset = [trade for trade in closed_trades if (trade.get('setup_profile') or {}).get('sector', 'N/A') == sector]
        if not subset:
            continue
        by_sector.append({
            'label': sector,
            'count': len(subset),
            'pnl': round(sum(trade.get('profit_loss', 0) or 0 for trade in subset), 2)
        })

    winning_factors = summarize_factor_frequency([trade for trade in closed_trades if trade.get('outcome') == 'win'], 'positive_factors')
    losing_factors = summarize_factor_frequency([trade for trade in closed_trades if trade.get('outcome') == 'loss'], 'negative_factors')
    strongest_positive_features = [
        item for item in pattern_stats
        if item.get('sample_size', 0) >= 2 and item.get('win_rate', 0) >= 60 and item.get('avg_return', 0) > 0
    ]
    strongest_negative_features = [
        item for item in pattern_stats
        if item.get('sample_size', 0) >= 2 and item.get('win_rate', 0) <= 40 and item.get('avg_return', 0) < 0
    ]

    pattern_insights = []
    if by_type:
        best_type = max(by_type, key=lambda item: (item['pnl'], item['win_rate']))
        worst_type = min(by_type, key=lambda item: (item['pnl'], item['win_rate']))
        pattern_insights.append(f"Your strongest trade type so far is {best_type['label']} with {best_type['win_rate']}% win rate.")
        if worst_type['label'] != best_type['label']:
            pattern_insights.append(f"{worst_type['label']} has been your weakest trade type so far.")
    if winning_factors:
        pattern_insights.append(f"Winning trades often shared this factor: {winning_factors[0]['label']}")
    if losing_factors:
        pattern_insights.append(f"Losing trades often shared this warning sign: {losing_factors[0]['label']}")
    if strongest_positive_features:
        best_feature = strongest_positive_features[0]
        pattern_insights.append(
            f"Memory edge: {feature_label(best_feature['feature_name'], best_feature['feature_value'])} has a {best_feature['win_rate']}% win rate across {best_feature['sample_size']} trades."
        )
    if strongest_negative_features:
        weak_feature = strongest_negative_features[0]
        pattern_insights.append(
            f"Memory warning: {feature_label(weak_feature['feature_name'], weak_feature['feature_value'])} has averaged {weak_feature['avg_return']}% in your history."
        )

    future_profile = {
        'boost_factors': (
            [feature_label(item['feature_name'], item['feature_value']) for item in strongest_positive_features[:3]]
            or [item['label'] for item in winning_factors[:3]]
        ),
        'warning_factors': (
            [feature_label(item['feature_name'], item['feature_value']) for item in strongest_negative_features[:3]]
            or [item['label'] for item in losing_factors[:3]]
        )
    }

    return {
        'best_trade_types': sorted(by_type, key=lambda item: (-item['pnl'], -item['win_rate']))[:4],
        'worst_trade_types': sorted(by_type, key=lambda item: (item['pnl'], item['win_rate']))[:4],
        'winning_factors': winning_factors[:5],
        'losing_factors': losing_factors[:5],
        'strongest_setups': sorted(by_sector, key=lambda item: -item['pnl'])[:4],
        'weakest_setups': sorted(by_sector, key=lambda item: item['pnl'])[:4],
        'learned_patterns': sorted(pattern_stats, key=lambda item: (-item.get('sample_size', 0), item.get('feature_name', '')))[:12],
        'strongest_positive_features': [
            {
                **item,
                'label': feature_label(item['feature_name'], item['feature_value'])
            }
            for item in sorted(strongest_positive_features, key=lambda item: (-item['sample_size'], -item['avg_return']))[:5]
        ],
        'strongest_negative_features': [
            {
                **item,
                'label': feature_label(item['feature_name'], item['feature_value'])
            }
            for item in sorted(strongest_negative_features, key=lambda item: (-item['sample_size'], item['avg_return']))[:5]
        ],
        'pattern_insights': pattern_insights,
        'future_suggestion_profile': future_profile
    }

def build_trade_chart_payload(trades):
    closed_trades = [enrich_trade_for_display(trade) for trade in trades if trade.get('status') == 'closed' and trade.get('profit_loss') is not None]
    closed_trades.sort(key=lambda trade: parse_trade_datetime(trade.get('exit_datetime')) or datetime.min)

    cumulative = []
    running = 0.0
    for trade in closed_trades:
        running += trade.get('profit_loss', 0) or 0
        cumulative.append({
            'label': trade.get('exit_date') or trade.get('symbol'),
            'value': round(running, 2)
        })

    type_performance = []
    for trade_type in sorted({trade.get('trade_type', 'Other') for trade in closed_trades}):
        subset = [trade for trade in closed_trades if trade.get('trade_type') == trade_type]
        type_performance.append({
            'label': trade_type,
            'value': round(sum(trade.get('profit_loss', 0) or 0 for trade in subset), 2)
        })

    return {
        'equity_curve': cumulative,
        'trade_type_performance': type_performance
    }

def get_trade_workspace_payload():
    trades = refresh_open_trade_monitor(load_trades())
    enriched_trades = [enrich_trade_for_display(trade) for trade in trades]
    open_trades = [trade for trade in enriched_trades if trade.get('status') == 'open']
    closed_trades = [trade for trade in enriched_trades if trade.get('status') == 'closed']

    live_monitor = sorted(open_trades, key=lambda trade: abs(trade.get('live_profit_pct') or 0), reverse=True)
    closed_trades.sort(key=lambda trade: parse_trade_datetime(trade.get('exit_datetime')) or datetime.min, reverse=True)
    open_trades.sort(key=lambda trade: parse_trade_datetime(trade.get('entry_datetime')) or datetime.min, reverse=True)

    summary = get_trade_summary(trades)
    patterns = build_pattern_insights(trades)

    return sanitize_for_json({
        'summary': summary,
        'open_trades': open_trades,
        'closed_trades': closed_trades,
        'live_monitor': live_monitor,
        'history': closed_trades,
        'patterns': patterns,
        'charts': build_trade_chart_payload(trades),
        'trade_types': TRADE_TYPES
    })

# =========================
# Routes
# =========================
@app.route('/')
def index():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/dashboard')
def dashboard():
    """Dashboard page"""
    return render_template('dashboard.html')

@app.route('/earnings')
def earnings():
    """Earnings analysis page"""
    return render_template('earnings.html')

@app.route('/trades')
def trades():
    """Trade planner page"""
    return render_template('trades.html')

@app.route('/news')
def news():
    """News analysis page"""
    return render_template('news.html')

# API Routes
@app.route('/api/upcoming_earnings')
def api_upcoming_earnings():
    """Get upcoming earnings"""
    earnings = get_upcoming_earnings(days_ahead=7)
    return jsonify(earnings)

@app.route('/api/earnings_calendar')
def api_earnings_calendar():
    """Get earnings calendar rows for a specific day."""
    day = request.args.get('day', get_local_iso_date())
    earnings = fetch_earnings_calendar_for_day(day)
    requested_day = datetime.strptime(day, '%Y-%m-%d').date()
    earnings = [
        {
            **item,
            'days_until': (requested_day - market_today()).days
        }
        for item in earnings
    ]
    return jsonify(earnings)

@app.route('/api/earnings/<symbol>')
def api_earnings(symbol):
    """Get detailed earnings for a symbol"""
    earnings = get_detailed_earnings(symbol)
    return jsonify(earnings)

@app.route('/api/analyze_stock')
@app.route('/api/analyze_stock/<symbol>')
def api_analyze_stock(symbol=None):
    """Analyze an earnings setup using structured scoring plus trade memory."""
    requested_symbol = symbol or request.args.get('symbol', '')
    trade_type = request.args.get('trade_type', 'Earnings')
    if not requested_symbol:
        return safe_json_response({'error': 'Symbol required'}, 400)
    return safe_json_response(build_analyze_stock_payload(requested_symbol, trade_type=trade_type))

@app.route('/api/similar_setups')
def api_similar_setups():
    """Return similar setup memory for the requested stock."""
    symbol = request.args.get('symbol', '')
    trade_type = request.args.get('trade_type', 'Earnings')
    if not symbol:
        return safe_json_response({'error': 'Symbol required'}, 400)
    analysis = build_analyze_stock_payload(symbol, trade_type=trade_type)
    return safe_json_response(analysis.get('similar_trade_summary', {}))

@app.route('/api/patterns')
def api_patterns():
    """Return learned pattern statistics from closed trades."""
    trades = refresh_open_trade_monitor(load_trades())
    payload = build_pattern_insights(trades)
    return safe_json_response(payload)

@app.route('/api/watchlist')
def api_watchlist():
    """Get watchlist data"""
    watchlist = load_watchlist()
    data = get_watchlist_data(watchlist)
    return jsonify(data)

@app.route('/api/watchlist', methods=['POST'])
def api_update_watchlist():
    """Update watchlist"""
    data = request.get_json(silent=True) or {}
    watchlist = [normalize_symbol(symbol) for symbol in data.get('watchlist', []) if symbol and str(symbol).strip()]
    save_watchlist(watchlist)
    clear_cache('dashboard_snapshot')
    return jsonify({'status': 'success'})

@app.route('/api/watchlist/add', methods=['POST'])
def api_add_to_watchlist():
    """Add a stock to the watchlist."""
    data = request.json or {}
    symbol = normalize_symbol(data.get('symbol', ''))
    if not symbol:
        return jsonify({'error': 'Symbol required'}), 400

    watchlist = load_watchlist()
    if symbol not in watchlist:
        watchlist.append(symbol)
        save_watchlist(watchlist)
        clear_cache('dashboard_snapshot')

    return jsonify({'status': 'success', 'watchlist': watchlist})

@app.route('/api/quotes')
def api_quotes():
    """Get live quotes for one or more symbols."""
    symbols = request.args.get('symbols', '')
    symbol_list = [symbol.strip().upper() for symbol in symbols.split(',') if symbol.strip()]
    return jsonify(get_live_quotes(symbol_list))

@app.route('/api/dashboard_snapshot')
def api_dashboard_snapshot():
    """Combined dashboard payload for faster initial loads."""
    include_debug = request.args.get('earnings_debug', '').lower() in {'1', 'true', 'yes'}

    def loader():
        watchlist = load_watchlist()
        today = get_local_iso_date(0)
        tomorrow = get_local_iso_date(1)
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_week = executor.submit(get_upcoming_earnings, 7, include_debug)
            future_today = executor.submit(fetch_earnings_calendar_for_day, today, include_debug)
            future_tomorrow = executor.submit(fetch_earnings_calendar_for_day, tomorrow, include_debug)
            earnings_result = future_week.result()
            today_result = future_today.result()
            tomorrow_result = future_tomorrow.result()

        earnings = earnings_result.get('items', []) if include_debug else earnings_result
        today_earnings = today_result.get('items', []) if include_debug else today_result
        tomorrow_earnings = tomorrow_result.get('items', []) if include_debug else tomorrow_result
        used_focus_fallback = {'today': False, 'tomorrow': False}

        if not today_earnings:
            today_earnings = get_focus_earnings_for_day(today)
            used_focus_fallback['today'] = bool(today_earnings)
        if not tomorrow_earnings:
            tomorrow_earnings = get_focus_earnings_for_day(tomorrow)
            used_focus_fallback['tomorrow'] = bool(tomorrow_earnings)

        week_earnings = [item for item in earnings if item['days_until'] >= 0]
        seen_week_keys = {(item.get('symbol'), item.get('date')) for item in week_earnings}
        for fallback_item in today_earnings + tomorrow_earnings:
            key = (fallback_item.get('symbol'), fallback_item.get('date'))
            if key in seen_week_keys:
                continue
            week_item = dict(fallback_item)
            week_item['days_until'] = (datetime.strptime(week_item['date'], '%Y-%m-%d').date() - market_today()).days
            week_earnings.append(week_item)
            seen_week_keys.add(key)

        tagged_week = tag_interest_labels(sorted(week_earnings, key=lambda item: (item['days_until'], not item['is_watchlist'], -safe_int(item.get('market_cap_value'), 0), item['symbol'])))
        featured_week = tagged_week[:4]
        remaining_week = tagged_week[4:]
        payload = {
            'today_earnings': today_earnings,
            'tomorrow_earnings': tomorrow_earnings,
            'featured_week_earnings': featured_week,
            'remaining_week_earnings': remaining_week,
            'upcoming_earnings_count': len(week_earnings),
            'watchlist': get_watchlist_data(watchlist),
            'open_trades_count': len([trade for trade in load_trades() if trade.get('status') == 'open']),
            'generated_at': market_now().isoformat(),
            'current_market_date': today,
            'current_market_time': market_now().strftime('%Y-%m-%d %I:%M %p ET'),
            'timezone': 'America/New_York'
        }
        if include_debug:
            payload['earnings_debug'] = {
                'enabled': True,
                'candidate_limit_per_day': MAX_CALENDAR_CANDIDATES_PER_DAY,
                'min_market_cap': MIN_EARNINGS_MARKET_CAP,
                'min_average_volume': MIN_EARNINGS_AVG_VOLUME,
                'used_focus_fallback': used_focus_fallback,
                'audit_by_day': {
                    **earnings_result.get('audit_by_day', {}),
                    today: today_result.get('audit', []),
                    tomorrow: tomorrow_result.get('audit', [])
                }
            }
        return payload

    if include_debug:
        return jsonify(loader())
    return jsonify(remember('dashboard_snapshot', 15, loader))

@app.route('/api/trades')
def api_trades():
    """Get all trades"""
    trades = refresh_open_trade_monitor(load_trades())
    return safe_json_response([enrich_trade_for_display(trade) for trade in trades])

@app.route('/api/trade_workspace')
def api_trade_workspace():
    """Get the full trading workspace payload."""
    payload = get_trade_workspace_payload()
    log_debug(
        'trade_workspace',
        open_trades=len(payload.get('open_trades', [])),
        closed_trades=len(payload.get('closed_trades', [])),
        show_metrics=payload.get('summary', {}).get('show_metrics')
    )
    return safe_json_response(payload)

@app.route('/api/trades', methods=['POST'])
@app.route('/api/log_trade', methods=['POST'])
def api_add_trade():
    """Add a new trade"""
    trade = request.json or {}
    trades = load_trades()

    normalized = normalize_trade_payload(trade)
    normalized['created_at'] = market_now().isoformat()
    normalized['id'] = max([item.get('id', 0) for item in trades], default=0) + 1
    normalized['status'] = 'closed' if normalized.get('exit_price') is not None else 'open'
    normalized = attach_trade_memory(normalized)
    normalized.update(calculate_trade_stats(normalized, current_price=normalized.get('exit_price')))
    if normalized['status'] == 'closed':
        close_trade_record(
            normalized,
            normalized.get('exit_price'),
            exit_datetime=normalized.get('exit_datetime') or market_now().isoformat(),
            close_reason=normalized.get('close_reason') or 'manual'
        )

    trades.append(normalized)
    save_trades(trades)
    update_pattern_stats(trades)
    clear_cache('quotes:')
    return safe_json_response({'status': 'success', 'id': normalized['id']})

@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
def api_update_trade(trade_id):
    """Update a trade (close it)"""
    data = request.json or {}
    trades = load_trades()

    updated = False
    for index, trade in enumerate(trades):
        if trade.get('id') != trade_id:
            continue
        normalized = normalize_trade_payload(data, existing=trade)
        normalized['id'] = trade_id
        normalized = attach_trade_memory(normalized)

        should_close = normalized.get('status') == 'closed' or normalized.get('exit_price') is not None
        if should_close and normalized.get('exit_price') is not None:
            close_trade_record(
                normalized,
                normalized.get('exit_price'),
                exit_datetime=normalized.get('exit_datetime') or market_now().isoformat(),
                close_reason=data.get('close_reason') or normalized.get('close_reason') or 'manual'
            )
        else:
            normalized['status'] = 'open'
            normalized['exit_price'] = None
            normalized['exit_datetime'] = None
            normalized['exit_date'] = None
            normalized['closed_at'] = None
            normalized['review'] = None
            normalized.update(calculate_trade_stats(normalized))
            normalized['result'] = None
            normalized['return_percent'] = None

        trades[index] = sanitize_for_json(normalized)
        updated = True
        break

    save_trades(trades)
    update_pattern_stats(trades)
    if not updated:
        return safe_json_response({'error': 'Trade not found'}, 404)
    return safe_json_response({'status': 'success'})

@app.route('/api/trades/<int:trade_id>', methods=['DELETE'])
def api_delete_trade(trade_id):
    """Delete a trade"""
    trades = load_trades()
    trades = [t for t in trades if t.get('id') != trade_id]
    save_trades(trades)
    update_pattern_stats(trades)
    return safe_json_response({'status': 'success'})

@app.route('/api/trades/<int:trade_id>/close', methods=['POST'])
@app.route('/api/close_trade/<int:trade_id>', methods=['POST'])
def api_close_trade(trade_id):
    """Close a trade explicitly."""
    data = request.json or {}
    trades = load_trades()
    for index, trade in enumerate(trades):
        if trade.get('id') != trade_id:
            continue
        exit_price = safe_float(data.get('exit_price'))
        if exit_price is None:
            return safe_json_response({'error': 'Exit price required'}, 400)
        close_reason = data.get('close_reason', 'manual')
        trade = normalize_trade_payload(trade, existing=trade)
        trade = attach_trade_memory(trade)
        close_trade_record(
            trade,
            exit_price,
            exit_datetime=data.get('exit_datetime') or market_now().isoformat(),
            close_reason=close_reason,
            auto_closed=False
        )
        trades[index] = sanitize_for_json(trade)
        save_trades(trades)
        update_pattern_stats(trades)
        return safe_json_response({'status': 'success'})
    return safe_json_response({'error': 'Trade not found'}, 404)

@app.route('/api/trade_summary')
def api_trade_summary():
    """Get trade summary statistics"""
    trades = refresh_open_trade_monitor(load_trades())
    summary = get_trade_summary(trades)
    return safe_json_response(summary)

@app.route('/api/analyze_move', methods=['POST'])
def api_analyze_move():
    """Analyze stock move"""
    data = request.get_json(silent=True) or {}
    symbol = data.get('symbol', '')
    if not symbol:
        return jsonify({'error': 'Symbol required'}), 400
    
    analysis = analyze_stock_move(symbol)
    return jsonify({'analysis': analysis})

@app.route('/api/news_analysis/<symbol>')
def api_news_analysis(symbol):
    """Get news analysis for a stock"""
    analysis = get_stock_news_analysis(symbol)
    return safe_json_response(analysis)

@app.route('/api/stock_research/<symbol>')
def api_stock_research(symbol):
    """Get premium stock research data for a symbol."""
    normalized_symbol = normalize_symbol(symbol)
    try:
        research = get_stock_research_data(normalized_symbol)
        sanitized = sanitize_for_json(research)
        log_debug(
            'stock_research_response',
            symbol=normalized_symbol,
            meta=sanitized.get('meta', {}),
            has_dcf=sanitized.get('dcf_valuation', {}).get('available'),
            price=sanitized.get('snapshot', {}).get('current_price')
        )
        return safe_json_response(sanitized)
    except Exception as e:
        print(f"Error in /api/stock_research/{normalized_symbol}: {e}")
        traceback.print_exc()
        fallback = build_empty_research_payload(normalized_symbol, str(e))
        fallback['meta'] = {
            'status': 'error',
            'error': str(e)
        }
        log_debug('stock_research_fallback', symbol=normalized_symbol, error=str(e))
        return safe_json_response(fallback, 200)

@app.route('/api/stock_data/<symbol>')
def api_stock_data(symbol):
    """Get current stock data"""
    try:
        quote = get_live_quotes([symbol])[0]
        current = quote.get('current_price')
        change_pct = quote.get('daily_change_pct') if quote.get('daily_change_pct') is not None else 0
        
        return jsonify({
            'symbol': symbol,
            'current_price': current,
            'price_formatted': format_money(current),
            'change_pct': round(change_pct, 2),
            'change_formatted': format_pct(change_pct)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 404

@app.route('/api/search/<query>')
def api_search(query):
    """Search for stocks"""
    try:
        normalized = normalize_symbol(query)
        ticker = get_ticker(normalized)
        info = ticker.info
        
        return jsonify({
            'symbol': normalized,
            'name': info.get('longName', info.get('shortName', normalized)),
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A')
        })
    except:
        return jsonify({'error': 'Not found'}), 404

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'message': 'EarningsEdge AI Trading Dashboard is running',
        'version': '2.0',
        'tools_available': 9,
        'model': MODEL,
        'timestamp': market_now().isoformat()
    })

@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if request.path.startswith('/api/'):
        print(f"Unhandled API error on {request.path}: {error}")
        traceback.print_exc()
        payload = {
            'error': 'Internal server error',
            'message': str(error),
            'path': request.path,
            'status': 'error'
        }
        if request.path.startswith('/api/stock_research/'):
            symbol = request.path.rsplit('/', 1)[-1]
            fallback = build_empty_research_payload(symbol, str(error))
            fallback['meta'] = {
                'status': 'error',
                'error': str(error)
            }
            return safe_json_response(fallback, 200)
        return safe_json_response(payload, 500)
    raise error

if __name__ == '__main__':
    print("\n" + "="*80)
    print("🚀 EarningsEdge AI Trading Dashboard Starting...")
    print("="*80)
    print(f"📊 Dashboard URL: http://127.0.0.1:5000")
    print(f"💚 Health Check: http://127.0.0.1:5000/health")
    print(f"🔧 Features:")
    print(f"   • Real-time Earnings Calendar")
    print(f"   • Detailed Earnings Analysis with Price Reactions")
    print(f"   • Personal Trade Tracker with P&L")
    print(f"   • Customizable Watchlist")
    print(f"   • AI-Powered Stock Move Analysis")
    print(f"   • News & Analyst Updates")
    print("="*80)
    print(f"📁 Data Directory: {DATA_DIR}")
    print(f"📝 Trades File: {TRADES_FILE}")
    print(f"⭐ Watchlist File: {WATCHLIST_FILE}")
    print("="*80 + "\n")
    app.run(debug=True, host='127.0.0.1', port=5000)
