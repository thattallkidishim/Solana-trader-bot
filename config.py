import os
import sys

# ── RPC ──────────────────────────────────────────────────────────────────────
HELIUS_RPC = os.getenv("HELIUS_RPC", "")

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── STARTUP VALIDATION ───────────────────────────────────────────────────────
if not HELIUS_RPC:
    sys.exit("MISSING ENV VAR: HELIUS_RPC")
if not TELEGRAM_TOKEN:
    sys.exit("MISSING ENV VAR: TELEGRAM_TOKEN")
if not TELEGRAM_CHAT_ID:
    sys.exit("MISSING ENV VAR: TELEGRAM_CHAT_ID")

# ── TRADING MODE ─────────────────────────────────────────────────────────────
PAPER_TRADE = os.getenv("PAPER_TRADE", "true").strip().lower() != "false"

# ── CAPITAL ──────────────────────────────────────────────────────────────────
STARTING_CAPITAL_USD   = float(os.getenv("STARTING_CAPITAL_USD", "10.0"))
MAX_POSITION_PCT       = 0.25
MAX_OPEN_POSITIONS     = 2
DAILY_LOSS_LIMIT_PCT   = 0.40
WEEKLY_PROFIT_LOCK_PCT = 0.20

# ── ENTRY FILTERS ────────────────────────────────────────────────────────────
MIN_LIQUIDITY_USD     = 15000.0
MIN_AGE_MINUTES       = 5.0
MAX_TOKEN_AGE_MINUTES = 30.0
MIN_VOLUME_5M         = 5000.0
MIN_PRICE_CHANGE_5M   = 15.0

# ── EXIT RULES ───────────────────────────────────────────────────────────────
TAKE_PROFIT_1_MULT = 2.0
TAKE_PROFIT_1_PCT  = 0.60
TAKE_PROFIT_2_MULT = 4.0
STOP_LOSS_PCT      = 0.25
MAX_HOLD_MINUTES   = 45.0

# ── RISK FILTER ──────────────────────────────────────────────────────────────
MAX_TOP_HOLDER_PCT = 20.0
RUGCHECK_API       = "https://api.rugcheck.xyz/v1"

# ── SCAN ─────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS      = 60
SEEN_TOKENS_FLUSH_INTERVAL = 500

# ── WALLET (live only) ───────────────────────────────────────────────────────
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")
