import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from config import (
    DAILY_LOSS_LIMIT_PCT,
    MAX_HOLD_MINUTES,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_PCT,
    PAPER_TRADE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_1_MULT,
    TAKE_PROFIT_1_PCT,
    TAKE_PROFIT_2_MULT,
    WEEKLY_PROFIT_LOCK_PCT,
)
from scanner import TokenCandidate

logger = logging.getLogger(__name__)


@dataclass
class Position:
    token_address:     str
    symbol:            str
    name:              str
    entry_price:       float
    position_size_usd: float
    tokens_held:       float
    entry_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    tp1_hit:          bool  = False
    tp1_proceeds_usd: float = 0.0
    closed:           bool  = False
    exit_price:       float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason:      str   = ""
    pnl_usd:          float = 0.0
    pnl_pct:          float = 0.0

    def age_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 60.0

    def current_pnl_pct(self, price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return ((price - self.entry_price) / self.entry_price) * 100.0

    def __repr__(self) -> str:
        return (
            f"Position({self.symbol} entry=${self.entry_price:.8f} "
            f"size=${self.position_size_usd:.2f} age={self.age_minutes():.0f}min)"
        )


class TradeManager:
    def __init__(self, starting_capital_usd: float = 10.0) -> None:
        self.capital_usd:      float = max(starting_capital_usd, 0.0)
        self.starting_capital: float = self.capital_usd
        self.open_positions:   Dict[str, Position] = {}
        self.closed_positions: List[Position] = []

        self.total_trades:   int   = 0
        self.winning_trades: int   = 0
        self.losing_trades:  int   = 0

        self.daily_start_capital: float = self.capital_usd
        self.daily_pnl:           float = 0.0
        self.total_pnl:           float = 0.0
        self.locked_profits:      float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100.0

    @property
    def available_capital(self) -> float:
        used = sum(p.position_size_usd for p in self.open_positions.values())
        return max(0.0, self.capital_usd - used)

    def can_open_position(self) -> Tuple[bool, str]:
        if len(self.open_positions) >= MAX_OPEN_POSITIONS:
            return False, f"max positions ({MAX_OPEN_POSITIONS}) reached"
        if self.daily_start_capital > 0:
            loss_pct = self.daily_pnl / self.daily_start_capital
            if loss_pct <= -DAILY_LOSS_LIMIT_PCT:
                return False, f"daily loss limit ({loss_pct*100:.1f}%)"
        if self.capital_usd * MAX_POSITION_PCT < 0.10:
            return False, "capital too low"
        return True, "ok"

    def _position_size(self) -> float:
        size = self.capital_usd * MAX_POSITION_PCT
        return min(size, self.available_capital)

    async def open_position(
        self,
        candidate: TokenCandidate,
        executor=None,
    ) -> Optional[Position]:
        ok, reason = self.can_open_position()
        if not ok:
            logger.warning(f"Cannot open: {reason}")
            return None
        if candidate.address in self.open_positions:
            logger.warning(f"Already holding {candidate.symbol}")
            return None
        if candidate.price_usd <= 0:
            logger.warning(f"Bad price for {candidate.symbol}")
            return None

        size_usd = self._position_size()
        if size_usd <= 0:
            logger.warning("Position size zero — skip")
            return None

        tokens = size_usd / candidate.price_usd

        if not PAPER_TRADE:
            if executor is None:
                logger.error("Live mode: no executor")
                return None
            success = await executor.buy(
                token_address=candidate.address,
                amount_usd=size_usd,
            )
            if not success:
                logger.error(f"Executor buy failed: {candidate.symbol}")
                return None

        self.capital_usd = max(0.0, self.capital_usd - size_usd)

        pos = Position(
            token_address=candidate.address,
            symbol=candidate.symbol,
            name=candidate.name,
            entry_price=candidate.price_usd,
            position_size_usd=size_usd,
            tokens_held=tokens,
        )
        self.open_positions[candidate.address] = pos
        self.total_trades += 1

        mode = "PAPER" if PAPER_TRADE else "LIVE"
        logger.info(f"[{mode}] BUY {candidate.symbol} ${size_usd:.2f} @ ${candidate.price_usd:.8f}")
        return pos

    async def check_exits(
        self,
        price_feed: Dict[str, float],
        executor=None,
    ) -> List[Position]:
        closed_now: List[Position] = []

        for address, pos in list(self.open_positions.items()):
            price = price_feed.get(address)
            if price is None or price <= 0:
                continue

            pnl_pct = pos.current_pnl_pct(price)
            age     = pos.age_minutes()

            if pnl_pct <= -(STOP_LOSS_PCT * 100):
                reason = f"StopLoss {pnl_pct:.1f}%"

            elif age >= MAX_HOLD_MINUTES:
                reason = f"TimeStop {age:.0f}min"

            elif pos.tp1_hit and price >= pos.entry_price * TAKE_PROFIT_2_MULT:
                reason = f"TP2 {TAKE_PROFIT_2_MULT}x"

            elif not pos.tp1_hit and price >= pos.entry_price * TAKE_PROFIT_1_MULT:
                tp1_tokens  = pos.tokens_held * TAKE_PROFIT_1_PCT
                tp1_cash    = tp1_tokens * price
                partial_pnl = tp1_cash - pos.position_size_usd * TAKE_PROFIT_1_PCT

                pos.tp1_hit          = True
                pos.tp1_proceeds_usd = tp1_cash
                self.capital_usd    += tp1_cash
                self.daily_pnl      += partial_pnl
                self.total_pnl      += partial_pnl

                logger.info(f"TP1 {pos.symbol} sold 60% @ ${price:.8f} +${tp1_cash:.2f}")
                if not PAPER_TRADE and executor:
                    await executor.sell(token_address=address, amount_tokens=tp1_tokens)
                continue

            else:
                continue

            closed = await self._close(pos, price, reason, executor)
            closed_now.append(closed)
            del self.open_positions[address]

        return closed_now

    async def _close(
        self,
        pos: Position,
        price: float,
        reason: str,
        executor=None,
    ) -> Position:
        remaining_pct    = (1.0 - TAKE_PROFIT_1_PCT) if pos.tp1_hit else 1.0
        remaining_tokens = pos.tokens_held * remaining_pct
        exit_cash        = remaining_tokens * price
        total_proceeds   = exit_cash + pos.tp1_proceeds_usd

        pnl_usd = total_proceeds - pos.position_size_usd
        pnl_pct = (
            (pnl_usd / pos.position_size_usd * 100.0)
            if pos.position_size_usd > 0 else 0.0
        )

        pos.closed      = True
        pos.exit_price  = price
        pos.exit_time   = datetime.now(timezone.utc)
        pos.exit_reason = reason
        pos.pnl_usd     = pnl_usd
        pos.pnl_pct     = pnl_pct

        self.capital_usd = max(0.0, self.capital_usd + exit_cash)
        self.daily_pnl  += pnl_usd
        self.total_pnl  += pnl_usd

        if pnl_usd >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        self.closed_positions.append(pos)

        sign = "+" if pnl_usd >= 0 else ""
        mode = "PAPER" if PAPER_TRADE else "LIVE"
        logger.info(
            f"[{mode}] CLOSE {pos.symbol} | {reason} | "
            f"P&L {sign}${pnl_usd:.2f} ({sign}{pnl_pct:.1f}%) | "
            f"Capital ${self.capital_usd:.2f}"
        )

        if not PAPER_TRADE and executor and remaining_tokens > 0:
            await executor.sell(
                token_address=pos.token_address,
                amount_tokens=remaining_tokens,
            )
        return pos

    def reset_daily_stats(self) -> None:
        self.daily_start_capital = self.capital_usd
        self.daily_pnl           = 0.0
        logger.info(f"Daily reset. Capital: ${self.capital_usd:.2f}")

    def lock_weekly_profits(self) -> float:
        if self.total_pnl <= 0:
            return 0.0
        lock = min(self.total_pnl * WEEKLY_PROFIT_LOCK_PCT, self.capital_usd * 0.5)
        self.locked_profits += lock
        self.capital_usd     = max(0.0, self.capital_usd - lock)
        logger.info(f"Weekly lock: ${lock:.2f}")
        return lock

    def summary(self) -> Dict:
        return {
            "capital_usd":       round(self.capital_usd, 2),
            "starting_capital":  round(self.starting_capital, 2),
            "total_pnl":         round(self.total_pnl, 2),
            "daily_pnl":         round(self.daily_pnl, 2),
            "locked_profits":    round(self.locked_profits, 2),
            "total_trades":      self.total_trades,
            "winning_trades":    self.winning_trades,
            "losing_trades":     self.losing_trades,
            "win_rate":          round(self.win_rate, 1),
            "open_positions":    len(self.open_positions),
            "available_capital": round(self.available_capital, 2),
        }
