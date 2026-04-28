import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from config import (
    DAILY_LOSS_LIMIT_PCT,
    HELIUS_RPC,
    PAPER_TRADE,
    SCAN_INTERVAL_SECONDS,
    STARTING_CAPITAL_USD,
)
from price_feed import PriceFeed
from reporter import TelegramReporter
from risk_filter import RiskFilter
from scanner import Scanner
from trade_manager import TradeManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


class SolanaTraderBot:
    def __init__(self) -> None:
        self.scan_count:        int  = 0
        self.hourly_candidates: int  = 0
        self.last_hour:         int  = -1
        self.last_day:          int  = -1
        self.last_week:         int  = -1
        self.paused:            bool = False

    async def _setup(self) -> None:
        self.session  = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "SolanaTraderBot/1.0"},
        )
        self.scanner  = Scanner(HELIUS_RPC)
        await self.scanner.start()
        self.risk     = RiskFilter(self.session, HELIUS_RPC)
        self.manager  = TradeManager(STARTING_CAPITAL_USD)
        self.feed     = PriceFeed(self.session)
        self.reporter = TelegramReporter(self.session)
        mode = "PAPER" if PAPER_TRADE else "LIVE"
        logger.info(f"Bot ready | {mode} | ${STARTING_CAPITAL_USD:.2f}")
        await self.reporter.report_startup(STARTING_CAPITAL_USD)

    async def _teardown(self) -> None:
        try:
            await self.scanner.stop()
        except Exception:
            pass
        try:
            if not self.session.closed:
                await self.session.close()
        except Exception:
            pass

    async def _cycle(self) -> None:
        self.scan_count += 1
        now = datetime.now(timezone.utc)
        mgr = self.manager

        # daily loss limit check
        if not self.paused and mgr.daily_start_capital > 0:
            if mgr.daily_pnl / mgr.daily_start_capital <= -DAILY_LOSS_LIMIT_PCT:
                self.paused = True
                await self.reporter.report_loss_limit(mgr.daily_pnl, mgr.capital_usd)

        # update prices + check exits
        open_addrs = list(mgr.open_positions.keys())
        if open_addrs:
            prices = await self.feed.get_prices(open_addrs)
            for pos in await mgr.check_exits(prices):
                await self.reporter.report_closed(pos, mgr.capital_usd)

        # scan + risk filter + open positions
        if not self.paused:
            candidates = await self.scanner.scan()
            self.hourly_candidates += len(candidates)
            for c in candidates:
                risk = await self.risk.analyze(c)
                if not risk.passed:
                    await self.reporter.report_rejected(c.symbol, risk.reasons)
                    continue
                await self.reporter.report_candidate(c, risk.score)
                pos = await mgr.open_position(c)
                if pos:
                    await self.reporter.report_opened(pos, mgr.capital_usd)

        # hourly heartbeat
        if now.hour != self.last_hour:
            self.last_hour = now.hour
            await self.reporter.report_heartbeat(
                self.scan_count, self.hourly_candidates, mgr.summary()
            )
            self.hourly_candidates = 0

        # daily reset
        if now.day != self.last_day:
            self.last_day = now.day
            if self.scan_count > 1:
                mgr.reset_daily_stats()
                self.paused = False
                await self.reporter.report_daily_summary(mgr.summary())

        # weekly profit lock (Sundays)
        week = now.isocalendar()[1]
        if week != self.last_week and now.weekday() == 6:
            self.last_week = week
            locked = mgr.lock_weekly_profits()
            if locked > 0:
                await self.reporter.send(
                    f"🔒 <b>Weekly Profit Lock</b>\n\n"
                    f"${locked:.2f} moved to cold wallet.\n"
                    f"Capital remaining: ${mgr.capital_usd:.2f}"
                )

    async def run(self) -> None:
        await self._setup()
        try:
            while True:
                try:
                    await self._cycle()
                except Exception as exc:
                    logger.error(f"Cycle error: {exc}", exc_info=True)
                    try:
                        await self.reporter.report_error(str(exc))
                    except Exception:
                        pass
                logger.info(f"Sleeping {SCAN_INTERVAL_SECONDS}s")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown")
        finally:
            await self._teardown()


if __name__ == "__main__":
    asyncio.run(SolanaTraderBot().run())
