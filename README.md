# ibkr_algotrade

Algorithmic trading bot for ES (E-mini S&P 500) futures via Interactive Brokers. Includes a live trading GUI and a backtesting GUI.

## Requirements

- Python 3.9+
- ib_insync
- TWS (Trader Workstation) running locally on port 7497

## Structure

```
live_bot/
  trade_engine.py         — async IB engine (connection, bar polling, order execution, EOD close)
  strategies.py           — HorizontalLineStrategy
  trading_gui.py          — Tkinter GUI

backtest/
  ibkr_backtest.py  — backtesting GUI
```

## Running

**Live bot:**
```bash
cd live_bot
python trading_gui.py
```

**Backtest:**
```bash
cd backtest
python ibkr_backtest.py
```

## Strategy — HorizontalLineStrategy

Reference line = previous day's ES close at the 4:00 PM CT CME maintenance break.

- First bar that crosses the line → enter 1 contract in that direction
- Each subsequent cross → flip (trade 2 contracts to reverse position)

## GUI Settings

| Setting | Options | Description |
|---|---|---|
| Bar Size | 1 min / 5 mins / 15 mins / 30 mins / 1 hour | Candle interval used for bar data and strategy |
| Trading Hours | RTH only / Full day (23h) | RTH = CME regular hours (08:30–16:00 CT); Full day = entire 23h Globex session |
| Ref Line | Fixed / Dynamic | Fixed: reference line stays at its initial value all day; Dynamic: updates to the latest bar's close each interval |
| Reference | Previous day close / Today open (RTH) / Today open (Full day 23h) | Starting value for the reference line. "Previous day close" = EOD 16:00 CT; "Today open (RTH)" = 09:30 CT NYSE open; "Today open (Full day 23h)" = 17:00 CT overnight session open (previous calendar day) |

## Notes

- Trades are **real market orders**. Make sure TWS/Gateway is in paper trading mode until you are ready to go live.
- EOD auto-close: all positions are flattened automatically at 15:00 CT (RTH) or 16:00 CT (All Sessions).
- Client ID is hardcoded to `101` in `trade_engine.py` — change if running multiple bots on the same TWS instance.
