import asyncio
import logging
from typing import Dict, List

import aiohttp

logger = logging.getLogger(__name__)

_BATCH_SIZE = 30


class PriceFeed:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def get_prices(self, addresses: List[str]) -> Dict[str, float]:
        if not addresses:
            return {}
        prices: Dict[str, float] = {}
        for i in range(0, len(addresses), _BATCH_SIZE):
            batch = addresses[i : i + _BATCH_SIZE]
            prices.update(await self._fetch_batch(batch))
        return prices

    async def _fetch_batch(self, addresses: List[str]) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(addresses)}"
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                if r.status != 200:
                    logger.warning(f"PriceFeed {r.status}")
                    return prices
                body  = await r.json(content_type=None)
                pairs = body.get("pairs") or []
                seen: set = set()
                for pair in pairs:
                    if pair.get("chainId") != "solana":
                        continue
                    addr = (pair.get("baseToken") or {}).get("address", "")
                    if not addr or addr in seen or addr not in addresses:
                        continue
                    try:
                        price = float(pair.get("priceUsd") or 0)
                        if price > 0:
                            prices[addr] = price
                            seen.add(addr)
                    except (TypeError, ValueError):
                        pass
        except asyncio.TimeoutError:
            logger.warning("PriceFeed timeout")
        except Exception as exc:
            logger.error(f"PriceFeed error: {exc}")
        return prices
