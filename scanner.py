import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

from config import (
    MAX_TOKEN_AGE_MINUTES,
    MIN_AGE_MINUTES,
    MIN_LIQUIDITY_USD,
    MIN_PRICE_CHANGE_5M,
    MIN_VOLUME_5M,
    SEEN_TOKENS_FLUSH_INTERVAL,
)

logger = logging.getLogger(__name__)


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class TokenCandidate:
    def __init__(self, data: Dict) -> None:
        self.address:         str   = data.get("address", "")
        self.symbol:          str   = data.get("symbol", "UNKNOWN")
        self.name:            str   = data.get("name", "Unknown")
        self.price_usd:       float = _f(data.get("price_usd"))
        self.liquidity_usd:   float = _f(data.get("liquidity_usd"))
        self.volume_5m:       float = _f(data.get("volume_5m"))
        self.price_change_5m: float = _f(data.get("price_change_5m"))
        self.age_minutes:     float = _f(data.get("age_minutes"), 9999.0)
        self.pair_address:    str   = data.get("pair_address", "")
        self.dex:             str   = data.get("dex", "unknown")
        self.market_cap:      float = _f(data.get("market_cap"))
        self.found_at:        datetime = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"Token({self.symbol} liq=${self.liquidity_usd:,.0f} "
            f"vol5m=${self.volume_5m:,.0f} chg={self.price_change_5m:.1f}% "
            f"age={self.age_minutes:.0f}min)"
        )


class Scanner:
    def __init__(self, helius_rpc: str) -> None:
        self.helius_rpc  = helius_rpc
        self.session: Optional[aiohttp.ClientSession] = None
        self._seen:       set = set()
        self._scan_count: int = 0

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "SolanaTraderBot/1.0"},
        )
        logger.info("Scanner ready")

    async def stop(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info("Scanner closed")

    async def _fetch_pairs(self) -> List[Dict]:
        url = "https://api.dexscreener.com/latest/dex/search?q=SOL"
        try:
            async with self.session.get(url) as r:
                if r.status != 200:
                    logger.warning(f"DexScreener {r.status}")
                    return []
                body  = await r.json(content_type=None)
                pairs = body.get("pairs") or []
                return [p for p in pairs if p.get("chainId") == "solana"]
        except asyncio.TimeoutError:
            logger.warning("DexScreener timeout")
            return []
        except Exception as exc:
            logger.error(f"DexScreener error: {exc}")
            return []

    def _parse(self, pair: Dict) -> Optional[Dict]:
        try:
            base    = pair.get("baseToken") or {}
            address = base.get("address", "")
            symbol  = base.get("symbol", "UNKNOWN")
            name    = base.get("name", "Unknown")

            if not address:
                return None

            skip = {"USD", "USDC", "USDT", "WRAPPED", "WETH", "WBTC"}
            if any(k in symbol.upper() for k in skip):
                return None

            price_usd = _f(pair.get("priceUsd"))
            if price_usd <= 0:
                return None

            liq = pair.get("liquidity") or {}
            vol = pair.get("volume")    or {}
            chg = pair.get("priceChange") or {}

            created = pair.get("pairCreatedAt")
            age     = 9999.0
            if created:
                try:
                    dt  = datetime.fromtimestamp(int(created) / 1000, tz=timezone.utc)
                    age = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
                except Exception:
                    age = 9999.0

            return {
                "address":         address,
                "symbol":          symbol,
                "name":            name,
                "price_usd":       price_usd,
                "liquidity_usd":   _f(liq.get("usd")),
                "volume_5m":       _f(vol.get("m5")),
                "price_change_5m": _f(chg.get("m5")),
                "age_minutes":     age,
                "pair_address":    pair.get("pairAddress", ""),
                "dex":             pair.get("dexId", "unknown"),
                "market_cap":      _f(pair.get("marketCap")),
            }
        except Exception as exc:
            logger.error(f"Parse error: {exc}")
            return None

    def _filter(self, t: Dict) -> Tuple[bool, str]:
        age = _f(t.get("age_minutes"), 9999.0)
        if age < MIN_AGE_MINUTES:
            return False, f"too new ({age:.1f}min)"
        if age > MAX_TOKEN_AGE_MINUTES:
            return False, f"too old ({age:.1f}min)"

        liq = _f(t.get("liquidity_usd"))
        if liq < MIN_LIQUIDITY_USD:
            return False, f"low liq (${liq:,.0f})"

        vol = _f(t.get("volume_5m"))
        if vol < MIN_VOLUME_5M:
            return False, f"low vol (${vol:,.0f})"

        chg = _f(t.get("price_change_5m"))
        if chg < MIN_PRICE_CHANGE_5M:
            return False, f"weak momentum ({chg:.1f}%)"

        return True, "ok"

    async def scan(self) -> List[TokenCandidate]:
        self._scan_count += 1

        if self._scan_count % SEEN_TOKENS_FLUSH_INTERVAL == 0:
            self._seen.clear()
            logger.info("seen-token cache flushed")

        pairs = await self._fetch_pairs()
        logger.info(f"Scan #{self._scan_count}: {len(pairs)} SOL pairs")

        candidates: List[TokenCandidate] = []
        for pair in pairs:
            data = self._parse(pair)
            if not data:
                continue
            addr = data.get("address", "")
            if not addr or addr in self._seen:
                continue
            ok, reason = self._filter(data)
            if ok:
                c = TokenCandidate(data)
                candidates.append(c)
                self._seen.add(addr)
                logger.info(f"CANDIDATE: {c}")
            else:
                logger.debug(f"skip {data.get('symbol','?')}: {reason}")

        logger.info(f"Scan #{self._scan_count} done — {len(candidates)} candidates")
        return candidates
