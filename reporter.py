import logging
from datetime import datetime, timezone
from typing import Dict, List

import aiohttp

from config import PAPER_TRADE, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

logger = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


class TelegramReporter:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.chat_id = TELEGRAM_CHAT_ID

    async def send(self, text: str) -> bool:
        try:
            payload = {
                "chat_id":                  self.chat_id,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            }
            async with self.session.post(
                _API,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    return True
                body = await r.text()
                logger.warning(f"Telegram {r.status}: {body[:200]}")
                return False
        except Exception as exc:
            logger.error(f"Telegram error: {exc}")
            return False

    async def report_startup(self, capital: float) -> None:
        mode = "PAPER TRADING" if PAPER_TRADE else "LIVE TRADING"
        await self.send(
            f"🤖 <b>Solana Trader Bot — {mode}</b>\n\n"
            f"Capital: <b>${capital:.2f}</b>\n"
            f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"Scanning every 60s\n"
            f"Min liq $15k | Age 5-30min | Momentum +15%/5min\n"
            f"Max 2 positions | Stop -25% | TP1 2x | TP2 4x"
        )

    async def report_candidate(self, c, score: int) -> None:
        await self.send(
            f"🔍 <b>Candidate: {c.symbol}</b>\n\n"
            f"Price: ${c.price_usd:.8f}\n"
            f"Liquidity: ${c.liquidity_usd:,.0f}\n"
            f"5min Volume: ${c.volume_5m:,.0f}\n"
            f"5min Change: <b>+{c.price_change_5m:.1f}%</b>\n"
            f"Age: {c.age_minutes:.0f}min\n"
            f"Risk score: {score}/100\n"
            f"<code>{c.address}</code>"
        )

    async def report_rejected(self, symbol: str, flags: List[str]) -> None:
        flag_text = "\n".join(f"  • {f}" for f in flags[:5])
        await self.send(f"🚨 <b>Rejected: {symbol}</b>\n\n{flag_text}")

    async def report_opened(self, pos, capital: float) -> None:
        mode = "PAPER" if PAPER_TRADE else "LIVE"
        icon = "📄" if PAPER_TRADE else "🟢"
        await self.send(
            f"{icon} <b>[{mode}] BUY {pos.symbol}</b>\n\n"
            f"Entry: ${pos.entry_price:.8f}\n"
            f"Size: ${pos.position_size_usd:.2f}\n"
            f"TP1: ${pos.entry_price * 2:.8f} (2x — sell 60%)\n"
            f"TP2: ${pos.entry_price * 4:.8f} (4x — sell rest)\n"
            f"Stop: ${pos.entry_price * 0.75:.8f} (-25%)\n"
            f"Time stop: 45min\n\n"
            f"Capital left: ${capital:.2f}"
        )

    async def report_closed(self, pos, capital: float) -> None:
        mode  = "PAPER" if PAPER_TRADE else "LIVE"
        emoji = "✅" if pos.pnl_usd >= 0 else "❌"
        sign  = "+" if pos.pnl_usd >= 0 else ""
        await self.send(
            f"{emoji} <b>[{mode}] CLOSED {pos.symbol}</b>\n\n"
            f"Reason: {pos.exit_reason}\n"
            f"Entry: ${pos.entry_price:.8f}\n"
            f"Exit: ${pos.exit_price:.8f}\n"
            f"P&L: <b>{sign}${pos.pnl_usd:.2f} ({sign}{pos.pnl_pct:.1f}%)</b>\n"
            f"Held: {pos.age_minutes():.0f}min\n\n"
            f"Capital: <b>${capital:.2f}</b>"
        )

    async def report_heartbeat(
        self, scan_no: int, candidates: int, summary: Dict
    ) -> None:
        pnl  = summary["total_pnl"]
        sign = "+" if pnl >= 0 else ""
        await self.send(
            f"💓 <b>Alive — Scan #{scan_no}</b>\n\n"
            f"Candidates this hour: {candidates}\n"
            f"Capital: ${summary['capital_usd']:.2f}\n"
            f"Open positions: {summary['open_positions']}\n"
            f"Win rate: {summary['win_rate']:.0f}% "
            f"({summary['winning_trades']}W / {summary['losing_trades']}L)\n"
            f"Total P&L: {sign}${pnl:.2f}"
        )

    async def report_daily_summary(self, summary: Dict) -> None:
        d     = summary["daily_pnl"]
        sign  = "+" if d >= 0 else ""
        emoji = "📈" if d >= 0 else "📉"
        await self.send(
            f"{emoji} <b>Daily Summary</b>\n\n"
            f"Capital: <b>${summary['capital_usd']:.2f}</b>\n"
            f"Day P&L: <b>{sign}${d:.2f}</b>\n"
            f"Total P&L: {'+' if summary['total_pnl'] >= 0 else ''}${summary['total_pnl']:.2f}\n\n"
            f"Trades: {summary['total_trades']} | Win rate: {summary['win_rate']:.0f}%\n"
            f"Locked profits: ${summary['locked_profits']:.2f}"
        )

    async def report_loss_limit(self, daily_pnl: float, capital: float) -> None:
        await self.send(
            f"⛔ <b>Daily Loss Limit Hit</b>\n\n"
            f"Down ${abs(daily_pnl):.2f} today.\n"
            f"Trading paused until tomorrow.\n"
            f"Capital preserved: ${capital:.2f}"
        )

    async def report_error(self, error: str) -> None:
        await self.send(f"⚠️ <b>Bot Error</b>\n\n<code>{error[:400]}</code>")
