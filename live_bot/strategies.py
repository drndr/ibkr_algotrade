from typing import Optional


def _fmt_chicago_time(bar_time, check_prev_day: bool = False) -> str:
    """Return ' (HH:MM CT)' for a bar datetime, appending ' prev day' when applicable."""
    if bar_time is None:
        return ""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        chi = ZoneInfo("America/Chicago")
        t = bar_time.astimezone(chi) if getattr(bar_time, "tzinfo", None) else bar_time
        suffix = ""
        if check_prev_day and t.date() < datetime.now(chi).date():
            suffix = " prev day"
        return f" ({t.strftime('%H:%M CT')}{suffix})"
    except Exception:
        return ""


class HorizontalLineStrategy:
    """
    Live version of the "horizontal line" strategy.

    - reference line starts at previous day's close or todays open
      or first bar's close if previous_close is not available.
    - first cross:
        * price > line => go long 1
        * price < line => go short 1
    - later:
        * if long and price < line => SELL 2 (flip to net short 1)
        * if short and price > line => BUY 2 (flip to net long 1)
    - if use_dynamic_reference:
        line = last bar's close after each bar
      else:
        line stays fixed at previous day's close
    """

    def __init__(self, use_dynamic_reference: bool = False, ref_source: str = "prev_close") -> None:
        self.use_dynamic_reference = use_dynamic_reference
        # ref_source: "prev_close" | "day_open_rth" | "day_open_full"
        self.ref_source = ref_source
        self.reference_line: Optional[float] = None
        self.first_cross: bool = True
        self.direction: Optional[str] = None  # 'long' / 'short' / None
        self.contracts_bought: int = 0

    # Engine will call this on START TRADING
    def reset(self) -> None:
        self.reference_line = None
        self.first_cross = True
        self.direction = None
        self.contracts_bought = 0

    async def on_bar(self, engine, bar) -> None:
        """
        Called by TradingEngine every time a new bar is available.

        :param engine: TradingEngine instance
        :param bar: ib_insync BarData (has .close, .date, etc.)
        """
        close = float(getattr(bar, "close", 0.0))

        # 1) Initialize reference line if needed
        if self.reference_line is None:
            if self.ref_source == "day_open_rth":
                src = getattr(engine, "day_open_rth", None)
                label = "today open (RTH 9:30)"
                bar_time_attr = "day_open_rth_bar_time"
                check_prev = False
            elif self.ref_source == "day_open_full":
                src = getattr(engine, "day_open_full", None)
                label = "today open (Full day 23h)"
                bar_time_attr = "day_open_full_bar_time"
                check_prev = True   # 23h session opens ~17:00 CT previous calendar day
            else:  # "prev_close"
                src = getattr(engine, "previous_close", None)
                label = "previous close"
                bar_time_attr = "previous_close_bar_time"
                check_prev = False

            if src is not None:
                self.reference_line = float(src)
                bar_time = getattr(engine, bar_time_attr, None)
                engine._tk(
                    engine.gui.log_message,
                    f"[Strategy] Initial reference line from {label}: "
                    f"{self.reference_line:.2f}{_fmt_chicago_time(bar_time, check_prev_day=check_prev)}",
                )
            else:
                self.reference_line = close
                engine._tk(
                    engine.gui.log_message,
                    f"[Strategy] {label} unavailable, using first bar close: {self.reference_line:.2f}",
                )

        ref = float(self.reference_line)

        # 2) FIRST CROSS: from flat to initial direction
        if self.first_cross and self.direction is None:
            if close > ref:
                # Go LONG 1
                await engine.execute_market_order(+1)
                self.contracts_bought += 1
                self.direction = "long"
                engine.position = 1
                engine.update_portfolio(self.contracts_bought, "Long")
                engine._tk(
                    engine.gui.log_message,
                    f"[Strategy] First cross UP: long 1 @ {close:.2f} (line={ref:.2f})",
                )
                self.first_cross = False

            elif close < ref:
                # Go SHORT 1
                await engine.execute_market_order(-1)
                self.contracts_bought += 1
                self.direction = "short"
                engine.position = -1
                engine.update_portfolio(self.contracts_bought, "Short")
                engine._tk(
                    engine.gui.log_message,
                    f"[Strategy] First cross DOWN: short 1 @ {close:.2f} (line={ref:.2f})",
                )
                self.first_cross = False

        # 3) SUBSEQUENT CROSSES (flip logic)
        elif self.direction == "long" and close < ref:
            # Currently long 1; flip to short 1 => SELL 2
            await engine.execute_market_order(-2)
            self.contracts_bought += 2
            self.direction = "short"
            engine.position = -1
            engine.update_portfolio(self.contracts_bought, "Short")
            engine._tk(
                engine.gui.log_message,
                f"[Strategy] Flip to SHORT: sell 2 @ {close:.2f} (line={ref:.2f})",
            )

        elif self.direction == "short" and close > ref:
            # Currently short 1; flip to long 1 => BUY 2
            await engine.execute_market_order(+2)
            self.contracts_bought += 2
            self.direction = "long"
            engine.position = 1
            engine.update_portfolio(self.contracts_bought, "Long")
            engine._tk(
                engine.gui.log_message,
                f"[Strategy] Flip to LONG: buy 2 @ {close:.2f} (line={ref:.2f})",
            )

        # 4) Update reference line
        if self.use_dynamic_reference:
            self.reference_line = close
            engine._tk(
                engine.gui.log_message,
                f"[Strategy] Dynamic line updated to: {self.reference_line:.2f}",
            )
        else:
            # Fixed line; just log occasionally
            engine._tk(
                engine.gui.log_message,
                f"[Strategy] Fixed line remains at: {self.reference_line:.2f}",
            )
