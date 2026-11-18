# Dip-recovery cbBTC bot for make.fun's "Race a BTC trading bot live for 24 hours".
#
# Idea: BTC's minute bars spend most of a falling day grinding down, then snap
# back in one sharp V. This bot waits for a real turn instead of catching the
# knife: it only buys after price has fallen well below its 4-hour high AND
# short momentum has already turned up AND price has reclaimed its fast EMA.
# One position at a time, full size, out on a fixed take-profit, a hard stop,
# or a trailing stop off the best close since entry. No trade, no fee bleed:
# in a window with no qualifying turn it simply holds cash.
#
# Everything is integer math on the wire prices except the EMA, which is a
# plain float recursion (deterministic: same inputs, same outputs, every run).
# No clock, no randomness, no network, no files outside the repo.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Entry: close at least DIP_BPS below the trailing LOOKBACK-bar high, above the
# close MOM_BARS bars ago, and above the EMA_SPAN fast average.
LOOKBACK = 240
DIP_BPS = 150
MOM_BARS = 5
EMA_SPAN = 12
# Exit: take profit, hard stop, trailing stop off the best close since entry.
TP_BPS = 200
STOP_BPS = 150
TRAIL_BPS = 150
# Bars to wait after an exit before a new entry.
COOLDOWN = 30
# Cap the fill move vs the latest close (slippage + impact count toward it).
MAX_SLIP_BPS = 500
# Minimum notional the replay accepts (1 USDC).
MIN_NOTIONAL = 1_000_000


class Trader:
    """Stateful dip-recovery logic. decide() mirrors the replay harness used
    to grade it locally: same inputs, same integer comparisons."""

    def __init__(self):
        self.closes = []
        self.ema = None
        self.peak = 0
        self.last_exit = -10 ** 9

    def decide(self, k, close, cash, qty, cost):
        """One bar's orders. k is the batch seq, close the bar's close price
        (raw units), cash/qty/cost the account's current values."""
        self.closes.append(close)
        if self.ema is None:
            self.ema = float(close)
        else:
            alpha = 2.0 / (EMA_SPAN + 1)
            self.ema = alpha * close + (1.0 - alpha) * self.ema

        if qty > 0:
            avg_px = cost * 10 ** 7 // qty
            if close > self.peak:
                self.peak = close
            if close * 10000 >= avg_px * (10000 + TP_BPS):
                self.last_exit = k
                return [("sell", qty)]
            if close * 10000 <= avg_px * (10000 - STOP_BPS):
                self.last_exit = k
                return [("sell", qty)]
            if self.peak > 0 and close * 10000 <= self.peak * (10000 - TRAIL_BPS):
                self.last_exit = k
                return [("sell", qty)]
            return []

        if k - self.last_exit < COOLDOWN:
            return []
        if len(self.closes) <= LOOKBACK:
            return []
        high = max(self.closes[-LOOKBACK - 1:-1])
        if (
            close * 10000 < high * (10000 - DIP_BPS)
            and close > self.closes[-MOM_BARS - 1]
            and close > self.ema
        ):
            if cash >= MIN_NOTIONAL:
                self.peak = close
                return [("buy", cash)]
        return []


TRADER = Trader()


def on_batch(state, batch):
    if state.warming_up:
        return []
    holding = state.positions.get(SYMBOL)
    qty = holding.qty if holding else 0
    cost = holding.cost if holding else 0
    bar = state.latest.get(SYMBOL)
    if bar is None:
        return []
    close = int(bar["c"])
    orders = TRADER.decide(state.seq, close, state.cash, qty, cost)
    out = []
    for side, amount in orders:
        if side == "buy":
            out.append(
                {
                    "side": "buy",
                    "symbol": SYMBOL,
                    "notional_quote_units": amount,
                    "max_slippage_bps": MAX_SLIP_BPS,
                }
            )
        else:
            out.append(
                {
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": amount,
                    "max_slippage_bps": MAX_SLIP_BPS,
                }
            )
    return out


if __name__ == "__main__":
    makefun_bars.run(on_batch)
