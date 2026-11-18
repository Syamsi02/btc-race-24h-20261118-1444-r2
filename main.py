"""Dip-recovery cbBTC bot for make.fun's "Race a BTC trading bot live for 24 hours".

Strategy: on a falling day BTC grinds down, then snaps back in one sharp V.
The bot does not catch the knife — it buys only once the turn is already
visible: price at least 1.5% below its 240-bar (4-hour) high, the 5-bar
momentum already up, and price back above its 12-bar EMA. One position at a
time, full cash in, out on a +2% take-profit, a -1.5% hard stop, or a -1.5%
trailing stop off the best close since entry. After an exit it waits 30 bars
before looking again. In a window with no qualifying turn it holds cash and
pays no fees. Deterministic: integer comparisons on wire prices, one float
EMA recursion, no clock, no randomness, no network.

The entrypoint must insert its own directory into sys.path: the grader runs
``python3 -P -s main.py``, which leaves the script's directory off sys.path.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Entry: close >= DIP_BPS below the trailing LOOKBACK-bar high, above the
# close MOM_BARS bars ago, and above the EMA_SPAN fast average.
LOOKBACK = 240
DIP_BPS = 150
MOM_BARS = 5
EMA_SPAN = 12
# Exit: take-profit, hard stop, trailing stop off the best close since entry.
TP_BPS = 200
STOP_BPS = 150
TRAIL_BPS = 150
# Bars to wait after an exit before a new entry.
COOLDOWN = 30
# Cap the fill move vs the latest close (slippage + impact count toward it).
MAX_SLIP_BPS = 500
# Minimum notional the replay accepts (1 USDC).
MIN_NOTIONAL = 1_000_000


def ema_update(ema, close):
    """One step of the fast EMA recursion; None seeds it at the first close."""
    if ema is None:
        return float(close)
    alpha = 2.0 / (EMA_SPAN + 1)
    return alpha * close + (1.0 - alpha) * ema


def trailing_high(closes):
    """Highest close of the LOOKBACK bars before the latest one."""
    return max(closes[-LOOKBACK - 1:-1])


def entry_signal(closes, close, ema):
    """True when the dip has turned: deep below the trailing high, momentum
    up over MOM_BARS bars, and price reclaimed the fast EMA."""
    return (
        close * 10000 < trailing_high(closes) * (10000 - DIP_BPS)
        and close > closes[-MOM_BARS - 1]
        and close > ema
    )


def exit_reason(avg_px, close, peak):
    """Why to sell now: 'tp' on take-profit, 'stop' on the hard stop,
    'trail' on the trailing stop, else None. Checked in that order."""
    if close * 10000 >= avg_px * (10000 + TP_BPS):
        return "tp"
    if close * 10000 <= avg_px * (10000 - STOP_BPS):
        return "stop"
    if peak > 0 and close * 10000 <= peak * (10000 - TRAIL_BPS):
        return "trail"
    return None


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
        self.ema = ema_update(self.ema, close)

        if qty > 0:
            avg_px = cost * 10 ** 7 // qty
            if close > self.peak:
                self.peak = close
            if exit_reason(avg_px, close, self.peak) is not None:
                self.last_exit = k
                return [("sell", qty)]
            return []

        if k - self.last_exit < COOLDOWN:
            return []
        if len(self.closes) <= LOOKBACK:
            return []
        if entry_signal(self.closes, close, self.ema):
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
