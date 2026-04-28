import logging
from typing import List, Tuple

import aiohttp

from config import MAX_TOP_HOLDER_PCT, RUGCHECK_API
from scanner import TokenCandidate

logger = logging.getLogger(__name__)


class RiskResult:
    def __init__(self, passed: bool, score: int, reasons: List[str]) -> None:
        self.passed  = passed
        self.score   = score
        self.reasons = reasons

    def __repr__(self) -> str:
        s = "SAFE" if self.passed else "RISKY"
        return f"RiskResult({s} score={self.score}/100 flags={len(self.reasons)})"


class RiskFilter:
    def __init__(self, session: aiohttp.ClientSession, helius_rpc: str) -> None:
        self.session    = session
        self.helius_rpc = helius_rpc

    async def _rugcheck(self, address: str) -> dict:
        url = f"{RUGCHECK_API}/tokens/{address}/report/summary"
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
                logger.warning(f"RugCheck {r.status} for {address[:8]}")
                return {}
        except Exception as exc:
            logger.warning(f"RugCheck unavailable: {exc}")
            return {}

    async def _get_account_info(self, address: str) -> dict:
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "jsonParsed"}],
            }
            async with self.session.post(
                self.helius_rpc,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return {}
                body  = await r.json(content_type=None)
                value = (body.get("result") or {}).get("value") or {}
                data  = value.get("data") or {}
                if not isinstance(data, dict):
                    return {}
                return data.get("parsed", {}).get("info", {})
        except Exception as exc:
            logger.warning(f"getAccountInfo error: {exc}")
            return {}

    async def _mint_revoked(self, address: str) -> bool:
        info = await self._get_account_info(address)
        return info.get("mintAuthority") is None

    async def _freeze_revoked(self, address: str) -> bool:
        info = await self._get_account_info(address)
        return info.get("freezeAuthority") is None

    async def _top_holder_pct(self, address: str) -> Tuple[bool, float]:
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [address],
            }
            async with self.session.post(
                self.helius_rpc,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return False, 100.0
                body     = await r.json(content_type=None)
                accounts = (body.get("result") or {}).get("value") or []
                if not accounts:
                    return False, 100.0

                amounts: List[float] = []
                for a in accounts:
                    try:
                        amounts.append(float(a.get("uiAmount") or 0))
                    except (TypeError, ValueError):
                        pass

                total = sum(amounts)
                if total <= 0:
                    return False, 100.0

                top_pct = (amounts[0] / total) * 100.0
                return top_pct <= MAX_TOP_HOLDER_PCT, top_pct
        except Exception as exc:
            logger.warning(f"Top-holder check error: {exc}")
            return False, 100.0

    async def analyze(self, candidate: TokenCandidate) -> RiskResult:
        flags: List[str] = []
        score = 100

        # 1. RugCheck
        rc = await self._rugcheck(candidate.address)
        if rc:
            for risk in (rc.get("risks") or []):
                if not isinstance(risk, dict):
                    continue
                name  = risk.get("name", "unknown")
                level = risk.get("level", "warn")
                flags.append(f"RugCheck:{name}")
                score -= 30 if level == "danger" else 10
            try:
                if float(rc.get("score") or 0) > 5000:
                    flags.append("RugCheck:high-score")
                    score -= 20
            except (TypeError, ValueError):
                pass
        else:
            score -= 5

        # 2. Mint authority
        if not await self._mint_revoked(candidate.address):
            flags.append("mint-authority-NOT-revoked")
            score -= 20

        # 3. Freeze authority
        if not await self._freeze_revoked(candidate.address):
            flags.append("freeze-authority-NOT-revoked")
            score -= 15

        # 4. Top holder
        holders_ok, top_pct = await self._top_holder_pct(candidate.address)
        if not holders_ok:
            flags.append(f"top-holder:{top_pct:.1f}%")
            score -= 25
        elif top_pct > 10:
            flags.append(f"top-holder-moderate:{top_pct:.1f}%")
            score -= 10

        # 5. Thin liquidity
        if candidate.liquidity_usd < 20000:
            flags.append(f"thin-liq:${candidate.liquidity_usd:,.0f}")
            score -= 10

        score = max(0, min(100, score))

        hard_reject = (
            "mint-authority-NOT-revoked" in flags
            or any(f.startswith("top-holder:") for f in flags)
        )
        passed = score >= 50 and not hard_reject

        result = RiskResult(passed=passed, score=score, reasons=flags)
        logger.info(f"Risk [{candidate.symbol}]: {result}")
        return result
