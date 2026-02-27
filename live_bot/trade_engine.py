import threading
import asyncio
import traceback
import calendar
from datetime import datetime, timedelta, time, date, timezone
from typing import Any, Optional, Tuple, List

from ib_insync import IB, Future as IBFuture  # so its not just "Future"
from zoneinfo import ZoneInfo

BAR_SIZE_TO_SECONDS = {
    "1 min": 60,
    "5 mins": 5 * 60,
    "15 mins": 15 * 60,
    "30 mins": 30 * 60,
    "1 hour": 60 * 60,
}

class TradingEngine:
    """Core engine that talks to IB and hosts the event loop.

    Strategy is external and attached via set_strategy(strategy).
    The strategy must implement:

        async def on_bar(self, engine: "TradingEngine", bar: Any) -> None:
            ...

    STOP TRADING:
        - stops the trading loop
        - flattens all positions in the current ES contract
        - resets portfolio & strategy state
    """

    # High-level portfolio state (mirrored into GUI)
    is_trading: bool = False
    contracts_bought: int = 0
    direction: Optional[str] = None

    def __init__(self, gui):
        self.gui = gui

        # IB / asyncio / threading
        self.ib: Optional[IB] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()

        self.is_connected: bool = False
        self.account_id: Optional[str] = None
        self.current_balance: float = 0.0
        self.available_funds: float = 0.0
        self.initial_balance: float = 0.0

        self.current_contract: Optional[IBFuture] = None

        # Previous close for strategy (set when _fetch_previous_close runs)
        self.previous_close: Optional[float] = None
        self.previous_close_bar_time: Optional[datetime] = None
        # RTH open: first bar at/after 09:30 CT (set on first trading loop iteration)
        self.day_open_rth: Optional[float] = None
        self.day_open_rth_bar_time: Optional[datetime] = None
        # Full-session open: first bar of the 23h session (~17:00 CT prev day)
        self.day_open_full: Optional[float] = None
        self.day_open_full_bar_time: Optional[datetime] = None

        # --- Strategy-related state (filled from external module) ---
        self.strategy: Optional[Any] = None  # object with async .on_bar(engine, bar)
        self.strategy_task: Optional[asyncio.Task] = None
        self.trading_interval_sec: int = 5 * 60  # 5-minute bars by default
        
        self.bar_size_setting = "5 mins"
        self.trading_interval_sec = BAR_SIZE_TO_SECONDS[self.bar_size_setting]
        
        # Whether to request Regular Trading Hours (RTH) only bars from IB.
        # True = RTH only, False = include extended hours.
        self.use_rth: bool = True

        # Recent bars cache for convenience (strategies may inspect this)
        self.bars: List[Any] = []

        # Simple logical position tracking (strategy can use/override)
        self.position: int = 0  # +ve = long, -ve = short, 0 = flat
        
        # --- End-of-day auto close settings (CME / America/Chicago for ES) ---
        # If use_rth is True -> close at RTH end (15:00 CT).
        # If use_rth is False -> close at global session end (16:00 CT).
        self.exchange_tz: str = "America/Chicago"
        self.rth_end_time: time = time(15, 0)   # 15:00 CT
        self.eth_end_time: time = time(16, 0)   # 16:00 CT
        self._last_eod_close_date: Optional[date] = None
        
        
    def _on_ib_error(self, reqId, errorCode, errorString, contract):
        # Ignore noisy/non-fatal codes:
        #   161 = cancel attempted on order that was already filled/cancelled
        #   202 = order cancelled confirmation
        #   10311 = direct-routing informational warning (non-fatal)
        if errorCode in (161, 202, 10311):
            return

        self._tk(self.gui.log_message, f"IB Error {errorCode}, reqId {reqId}: {errorString}")

    # ------------------------- Strategy wiring -------------------------
    def set_strategy(self, strategy: Any) -> None:
        """Attach a trading strategy object (with async on_bar(engine, bar))."""
        self.strategy = strategy
        self._tk(self.gui.log_message, f"Strategy attached: {type(strategy).__name__}")
        
    def set_use_rth(self, use_rth: bool) -> None:
        """Set whether historical bars should be requested for RTH only."""
        self.use_rth = bool(use_rth)
        mode = "RTH only" if self.use_rth else "All sessions (incl. ETH)"
        self._tk(self.gui.log_message, f"Bar mode set to: {mode}")
        # Refresh previous close when switching modes (if connected)
        self.refresh_previous_close()
        
    def set_bar_size(self, bar_size_setting: str) -> None:
        """Set IB historical bar size and align the trading loop interval to it."""
        if bar_size_setting not in BAR_SIZE_TO_SECONDS:
            self._tk(self.gui.log_message, f"Invalid bar size: {bar_size_setting}")
            return

        self.bar_size_setting = bar_size_setting
        self.trading_interval_sec = BAR_SIZE_TO_SECONDS[bar_size_setting]
        self._tk(self.gui.log_message, f"Bar size set to: {bar_size_setting}")
        
    def _reset_internal_state_for_start(self) -> None:
        """Reset engine/strategy state when user presses START TRADING."""
        self.position = 0
        self.contracts_bought = 0
        self.direction = None
        self.day_open_rth = None
        self.day_open_rth_bar_time = None
        self.day_open_full = None
        self.day_open_full_bar_time = None
        if self.strategy and hasattr(self.strategy, "reset"):
            try:
                self.strategy.reset()
            except Exception:
                pass

    # ------------------------- Public API -------------------------
    def connect_to_ib(self):
        if IB is None:
            self._tk(self.gui.on_connection_error, "ib_insync not installed")
            return
        if self._thread and self._thread.is_alive():
            self._tk(self.gui.log_message, "Already connecting or connected.")
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._ib_thread_main, name="IBThread", daemon=True)
        self._thread.start()

    def refresh_balance(self):
        if not self.is_connected or not self.ib or not self.loop:
            self._tk(self.gui.log_message, "Not connected to IB - cannot refresh balance")
            return

        def task():
            try:
                assert self.loop is not None
                self.loop.create_task(self._fetch_account_info())
            except Exception as e:
                self._tk(self.gui.log_message, f"Error refreshing balance: {e}")

        assert self.loop is not None
        self.loop.call_soon_threadsafe(task)

    def refresh_previous_close(self):
        if not self.is_connected or not self.ib or not self.loop:
            self._tk(self.gui.log_message, "Not connected to IB - cannot refresh previous close")
            return

        async def _task():
            try:
                close_price = await self._fetch_previous_close()
                if close_price is not None:
                    self._tk(self.gui.on_previous_close_updated, close_price)
                else:
                    self._tk(self.gui.log_message, "No previous close data")
            except Exception as e:
                self._tk(self.gui.log_message, f"Error refreshing previous close: {e}")

        def schedule():
            assert self.loop is not None
            self.loop.create_task(_task())

        assert self.loop is not None
        self.loop.call_soon_threadsafe(schedule)

    def start_trading(self) -> None:
        """Start the async trading loop and strategy."""
        if not self.is_connected or not self.ib or not self.loop:
            self._tk(self.gui.log_message, "Cannot start trading – not connected to IB")
            try:
                self._tk(self.gui.on_trading_error, "Not connected to IB")
            except Exception:
                pass
            return

        if self.strategy is None:
            self._tk(self.gui.log_message, "Cannot start trading – no strategy attached")
            try:
                self._tk(self.gui.on_trading_error, "No strategy configured on TradingEngine")
            except Exception:
                pass
            return

        if not self.current_contract:
            self._tk(self.gui.log_message, "Cannot start trading – no contract is set")
            return

        if self.is_trading:
            self._tk(self.gui.log_message, "Trading is already running")
            return

        self._reset_internal_state_for_start()
        self.is_trading = True
        self._tk(self.gui.log_message, "Starting trading loop...")

        def _start():
            if self.strategy_task and not self.strategy_task.done():
                self.strategy_task.cancel()
            assert self.loop is not None
            self.strategy_task = self.loop.create_task(self._trading_loop())

        assert self.loop is not None
        self.loop.call_soon_threadsafe(_start)

    def stop_trading(self) -> None:
        """
        Stop the async trading loop AND flatten all positions
        in the current contract.
        """
        if not self.loop:
            return

        self.is_trading = False
        self._tk(self.gui.log_message, "Stopping trading loop and flattening positions...")

        async def _stop_and_flatten():
            # 1) Cancel trading loop task
            if self.strategy_task and not self.strategy_task.done():
                self.strategy_task.cancel()
                try:
                    await self.strategy_task
                except asyncio.CancelledError:
                    self._tk(self.gui.log_message, "Trading loop cancelled (STOP TRADING).")

            # 2) Close positions at IB level
            await self.close_all_positions()
            
            # 3) Close open orders if any
            await self.cancel_all_open_orders()

            # 4) Reset internal portfolio state & GUI
            self.position = 0
            self.contracts_bought = 0
            self.direction = "Flat"
            try:
                self._tk(self.gui.on_portfolio_updated, 0, "Flat")
            except Exception:
                self._tk(self.gui.update_portfolio_display)

            # 5) Reset strategy state, if supported
            if self.strategy and hasattr(self.strategy, "reset"):
                try:
                    self.strategy.reset()
                except Exception:
                    pass

            self._tk(self.gui.log_message, "STOP TRADING completed – account flattened for current contract.")

        def _schedule():
            assert self.loop is not None
            self.loop.create_task(_stop_and_flatten())

        self.loop.call_soon_threadsafe(_schedule)

    def disconnect(self):
        if not self.loop:
            return
        self._shutdown.set()
        try:
            if self.ib and self.ib.isConnected():
                self.loop.call_soon_threadsafe(self.ib.disconnect)
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.is_connected = False
        self._tk(self.gui.log_message, "Disconnected.")

    # ------------------------- Thread main -------------------------
    def _ib_thread_main(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.ib = IB()
            
            self.ib.errorEvent += self._on_ib_error

            self._tk(self.gui.log_message, "Attempting to connect to Interactive Brokers...")
            self.loop.run_until_complete(self.ib.connectAsync("127.0.0.1", 7497, clientId=101))

            self.is_connected = True
            self._tk(self.gui.log_message, "Connected to Interactive Brokers")

            try:
                # 3 = delayed market data; change if you have real-time permissions
                self.ib.reqMarketDataType(3)
            except Exception:
                pass

            # Create & qualify ES contract
            contract_symbol, contract_expiration = self.get_current_futures_contract()

            async def _create_and_qualify():
                self.current_contract = IBFuture("ES", contract_expiration, "CME")
                self._tk(self.gui.log_message, f"Created contract: {self.current_contract}")
                try:
                    qualified_list = await self.ib.qualifyContractsAsync(self.current_contract)
                except Exception as e:
                    self._tk(self.gui.log_message, f"Contract qualification error: {e}")
                    return

                if not qualified_list:
                    self._tk(
                        self.gui.log_message,
                        "Warning: contract qualification returned empty list (no matching ES contract?)"
                    )
                    return

                self.current_contract = qualified_list[0]
                self._tk(self.gui.log_message, f"Qualified contract: {self.current_contract}")

            if IBFuture is None:
                contract_symbol = "ES-Unknown"
                self._tk(self.gui.log_message, "IBFuture is None – ib_insync not imported correctly?")
            else:
                self.loop.run_until_complete(_create_and_qualify())

            # Fetch account info using async version before run_forever()
            self.loop.run_until_complete(self._fetch_account_info())

            # Notify GUI
            info = {
                "account_id": self.account_id or "Unknown",
                "net_liquidation": self.current_balance,
                "available_funds": self.available_funds,
            }
            self._tk(self.gui.on_connection_success, contract_symbol, info)

            # Fetch previous close asynchronously before starting the loop
            try:
                close_price = self.loop.run_until_complete(self._fetch_previous_close())
                if close_price is not None:
                    self.previous_close = close_price
                    self._tk(self.gui.on_previous_close_updated, close_price)
            except Exception as e:
                self._tk(self.gui.log_message, f"Previous close fetch failed: {e}")

            # Start background tasks
            self.loop.create_task(self._heartbeat())

            # Hand over control to asyncio loop
            self.loop.run_forever()
        except Exception as e:
            tb = traceback.format_exc()
            self._tk(self.gui.log_message, f"IB worker error: {repr(e)}\n{tb}")
            self._tk(self.gui.on_connection_error, str(e))
            self.is_connected = False
        finally:
            try:
                if self.ib and self.ib.isConnected():
                    self.ib.disconnect()
            except Exception:
                pass
            try:
                if self.loop and self.loop.is_running():
                    self.loop.stop()
            except Exception:
                pass

    # ------------------------- IB helpers -------------------------
    async def _fetch_account_info(self):
        """Pure async version: safe to call while the event loop is running."""
        if not self.ib:
            return
        try:
            account_values = await self.ib.accountSummaryAsync()

            info = {
                "account_id": "Unknown",
                "net_liquidation": 0.0,
                "available_funds": 0.0,
                "gross_position_value": 0.0,
                "unrealized_pnl": 0.0,
            }

            for item in account_values:
                if item.tag == "AccountCode":
                    self.account_id = item.value
                    info["account_id"] = item.value
                elif item.tag == "NetLiquidation":
                    try:
                        self.current_balance = float(item.value)
                        if self.initial_balance == 0:
                            self.initial_balance = self.current_balance
                        info["net_liquidation"] = self.current_balance
                    except Exception:
                        pass
                elif item.tag == "AvailableFunds":
                    try:
                        self.available_funds = float(item.value)
                        info["available_funds"] = self.available_funds
                    except Exception:
                        pass
                elif item.tag == "GrossPositionValue":
                    try:
                        info["gross_position_value"] = float(item.value)
                    except Exception:
                        pass
                elif item.tag == "UnrealizedPnL":
                    try:
                        info["unrealized_pnl"] = float(item.value)
                    except Exception:
                        pass

            self._tk(
                self.gui.log_message,
                (
                    f"Account Summary - Net: {info['net_liquidation']:.2f}, "
                    f"Available: {info['available_funds']:.2f}, "
                    f"Positions: {info['gross_position_value']:.2f}, "
                    f"UnrlzdPnL: {info['unrealized_pnl']:.2f}"
                ),
            )
            self._tk(self.gui.on_balance_updated, self.current_balance, self.available_funds)
        except Exception as e:
            self._tk(self.gui.log_message, f"Error fetching account info: {e}")

    async def _fetch_previous_close(self) -> Optional[float]:
        """Fetch previous trading day's close asynchronously."""
        if not self.ib or not self.current_contract:
            return None
        try:
            prev_day = datetime.now() - timedelta(days=1)
            while prev_day.weekday() >= 5:  # Saturday=5, Sunday=6
                prev_day -= timedelta(days=1)
            # Always anchor to the true ES end-of-day: the 4 PM CT CME maintenance break.
            # useRTH only controls which bars the trading loop uses, not the reference close.
            previous_day_str = prev_day.strftime("%Y%m%d 16:00:00") + " US/Central"

            bars = await self.ib.reqHistoricalDataAsync(
                self.current_contract,
                endDateTime=previous_day_str,
                durationStr="1 D",
                barSizeSetting="5 mins",
                whatToShow="MIDPOINT",
                useRTH=False,
            )
            if bars:
                last = bars[-1]
                _bd = getattr(last, "date", None)
                # IB start-stamps bars: the last 5-min bar covers 15:55→16:00 CT
                # and is timestamped at 15:55.  Add one bar interval so the log
                # shows the actual close time (16:00 CT).
                self.previous_close_bar_time = (
                    _bd + timedelta(minutes=5) if _bd is not None else None
                )
                return float(last.close)
            return None
        except Exception as e:
            self._tk(self.gui.log_message, f"_fetch_previous_close error: {e}")
            return None

    async def _fetch_rth_open(self) -> Tuple[Optional[float], Optional[datetime]]:
        """
        Fetch today's 09:30 CT open price (NYSE/CME equity index open).

        Always uses 5-min bars anchored to today's 09:35 CT.  The last bar
        returned will be the 09:30–09:35 bar; its .open is the 09:30 price.
        Returns (None, None) if called before 09:30 CT (bars list is empty).
        """
        if not self.ib or not self.current_contract:
            return None, None
        try:
            chi = ZoneInfo(self.exchange_tz)
            # Build "today 09:35 CT" as a tz-aware datetime, then convert to UTC
            # so IB receives a " GMT" suffix.  This avoids IB treating "US/Central"
            # as a fixed UTC-5 offset (CDT) in winter when the real offset is UTC-6 (CST).
            today_ct = datetime.now(chi).date()
            end_ct = datetime(today_ct.year, today_ct.month, today_ct.day,
                              9, 35, 0, tzinfo=chi)
            end_utc = end_ct.astimezone(timezone.utc)
            # IB UTC format: "YYYYMMDD-HH:MM:SS" (dash separator, no timezone suffix)
            end_str = end_utc.strftime("%Y%m%d-%H:%M:%S")
            bars = await self.ib.reqHistoricalDataAsync(
                self.current_contract,
                endDateTime=end_str,
                durationStr="1800 S",  # 30 min back from 09:35 → 09:05 start
                barSizeSetting="5 mins",
                whatToShow="MIDPOINT",
                useRTH=False,          # full-session data avoids error 162 on short RTH windows
                keepUpToDate=False,
            )
            if not bars:
                return None, None
            last = bars[-1]
            return float(last.open), getattr(last, "date", None)
        except Exception as e:
            self._tk(self.gui.log_message, f"_fetch_rth_open error: {e}")
            return None, None

    async def _handle_end_of_day_if_needed(self) -> bool:
        """
        Close all positions and stop trading when the session ends.

        Cutoff depends on self.use_rth:
          - True  -> self.rth_end_time (RTH end)
          - False -> self.eth_end_time (full session end)
        Returns True if it closed/stopped trading.
        """
        tz = ZoneInfo(self.exchange_tz) if ZoneInfo else None
        now = datetime.now(tz) if tz else datetime.now()
        today = now.date()

        # Only do this once per day
        if self._last_eod_close_date == today:
            return False

        cutoff = self.rth_end_time if self.use_rth else self.eth_end_time
        if now.time() < cutoff:
            return False

        self._last_eod_close_date = today
        mode = "RTH" if self.use_rth else "All sessions"
        self._tk(self.gui.log_message, f"EOD cutoff reached ({mode} @ {cutoff}). Canceling orders + closing positions...")

        await self.cancel_all_open_orders()
        await self.close_all_positions()  # you said you already have this now

        self.is_trading = False
        return True

    async def _trading_loop(self):
        """Main 5-minute trading loop.

        This loop pulls recent bars, updates GUI price, then calls strategy.on_bar().
        """
        self._tk(self.gui.log_message, "Trading loop started")

        try:
            while (
                self.is_trading
                and not self._shutdown.is_set()
                and self.ib
                and self.current_contract
            ):
                # End-of-day safety: cancel orders + close positions + stop
                if await self._handle_end_of_day_if_needed():
                    break
                try:
                    # Get recent 5-minute bars for context (last 30 minutes)
                    bars = await self.ib.reqHistoricalDataAsync(
                        self.current_contract,
                        endDateTime="",              # now
                        durationStr="1 D",           # last RTH session worth of bars
                        barSizeSetting=self.bar_size_setting,
                        whatToShow="MIDPOINT",       # <-- same as backtest
                        useRTH=self.use_rth,                 # RTH only, like your backtest default
                        keepUpToDate=False,
                    )

                    if not bars:
                        self._tk(self.gui.log_message,
                                 "No bar data returned (1 D, 5-min, MIDPOINT, RTH).")
                    else:
                        # Populate day_open_rth / day_open_full lazily and only when
                        # the active strategy actually needs that reference source.
                        # This prevents spurious IB HMDS requests (and error 162 pacing
                        # issues) when the user has chosen "Previous day close".
                        ref_src = (
                            getattr(self.strategy, "ref_source", "prev_close")
                            if self.strategy else "prev_close"
                        )

                        if self.day_open_rth is None and ref_src == "day_open_rth":
                            if self.bar_size_setting == "1 hour":
                                # 1h bars have no bar aligned to 09:30; use a
                                # separate 5-min fetch anchored to that time.
                                self.day_open_rth, self.day_open_rth_bar_time = \
                                    await self._fetch_rth_open()
                            else:
                                # Scan the already-fetched bars for the first
                                # bar whose timestamp is exactly 09:30 CT today.
                                # bar.date from ib_insync for US futures is a
                                # naive datetime in the exchange (CT) timezone.
                                _chi = ZoneInfo(self.exchange_tz)
                                _today = datetime.now(_chi).date()
                                for _b in bars:
                                    _bd = getattr(_b, "date", None)
                                    if _bd is None:
                                        continue
                                    _bd_ct = (
                                        _bd if getattr(_bd, "tzinfo", None) is None
                                        else _bd.astimezone(_chi)
                                    )
                                    if (
                                        _bd_ct.date() == _today
                                        and _bd_ct.hour == 9
                                        and _bd_ct.minute == 30
                                    ):
                                        self.day_open_rth = float(_b.open)
                                        self.day_open_rth_bar_time = _bd
                                        break

                        if self.day_open_full is None and ref_src == "day_open_full":
                            if not self.use_rth:
                                # Main bars are already full-session; first bar is the 23h open
                                self.day_open_full = float(bars[0].open)
                                self.day_open_full_bar_time = getattr(bars[0], "date", None)
                            else:
                                # RTH mode: fetch full-session bars separately
                                full_bars = await self.ib.reqHistoricalDataAsync(
                                    self.current_contract,
                                    endDateTime="",
                                    durationStr="1 D",
                                    barSizeSetting=self.bar_size_setting,
                                    whatToShow="MIDPOINT",
                                    useRTH=False,
                                    keepUpToDate=False,
                                )
                                if full_bars:
                                    self.day_open_full = float(full_bars[0].open)
                                    self.day_open_full_bar_time = getattr(full_bars[0], "date", None)
                        latest_bar = bars[-1]
                        self.bars.extend(bars)
                        self.bars = self.bars[-200:]

                        price = float(latest_bar.close)
                        bar_time = getattr(latest_bar, "date", datetime.now())
                        if not isinstance(bar_time, datetime):
                            bar_time = datetime.now()

                        self._tk(self.gui.on_price_updated, price, bar_time)

                        if self.strategy is not None and hasattr(self.strategy, "on_bar"):
                            await self.strategy.on_bar(self, latest_bar)

                except Exception as e:
                    self._tk(self.gui.log_message, f"Error in trading loop: {e}")
                    try:
                        self._tk(self.gui.on_trading_error, str(e))
                    except Exception:
                        pass

                await asyncio.sleep(self.trading_interval_sec)

        except asyncio.CancelledError:
            self._tk(self.gui.log_message, "Trading loop cancelled")
        finally:
            self._tk(self.gui.log_message, "Trading loop exited")

    async def execute_market_order(self, quantity: int) -> None:
        """Place a market order to adjust position by `quantity` contracts.

        quantity > 0 -> BUY
        quantity < 0 -> SELL
        """
        if quantity == 0 or not self.ib or not self.current_contract:
            return

        try:
            from ib_insync import MarketOrder  # type: ignore
        except Exception as e:
            self._tk(self.gui.log_message, f"Cannot place order – ib_insync missing: {e}")
            return

        action = "BUY" if quantity > 0 else "SELL"
        size = abs(quantity)

        self._tk(self.gui.log_message, f"Placing market order: {action} {size}")

        try:
            trade = self.ib.placeOrder(self.current_contract, MarketOrder(action, size, tif="DAY"))

            # Optionally wait for fill
            while not trade.isDone():
                await asyncio.sleep(0.2)

            self._tk(self.gui.log_message, f"Order completed: {action} {size}")
        except Exception as e:
            self._tk(self.gui.log_message, f"Order error: {e}")
            try:
                self._tk(self.gui.on_trading_error, str(e))
            except Exception:
                pass

    def _matches_current_contract(self, pos_contract) -> bool:
        """Return True if a position's contract matches self.current_contract."""
        if not self.current_contract:
            return False
        # Primary: match by conId (set after qualifyContractsAsync)
        cur_id = getattr(self.current_contract, "conId", 0)
        pos_id = getattr(pos_contract, "conId", 0)
        if cur_id and pos_id:
            return cur_id == pos_id
        # Fallback: symbol + secType
        return (
            getattr(pos_contract, "symbol", "") == getattr(self.current_contract, "symbol", "ES")
            and getattr(pos_contract, "secType", "") == "FUT"
        )

    async def close_all_positions(self) -> None:
        """
        Close (liquidate) open positions for the current ES contract only.
        Does NOT touch other instruments in the account.
        """
        if not self.ib:
            self._tk(self.gui.log_message, "Close all: no IB connection.")
            return

        try:
            from ib_insync import MarketOrder  # type: ignore
        except Exception as e:
            self._tk(self.gui.log_message, f"Cannot close positions – ib_insync missing: {e}")
            return

        try:
            positions = list(self.ib.positions())
        except Exception as e:
            self._tk(self.gui.log_message, f"Close all: failed to fetch positions: {e}")
            return

        open_positions = [
            p for p in positions
            if getattr(p, "position", 0) and self._matches_current_contract(p.contract)
        ]
        if not open_positions:
            self._tk(self.gui.log_message, "Close all: no open ES positions to close.")
            return

        self._tk(self.gui.log_message, f"Close all: found {len(open_positions)} open ES position(s). Closing...")

        for pos in open_positions:
            try:
                qty = int(pos.position)
            except Exception:
                continue
            if qty == 0:
                continue

            contract = pos.contract
            action = "SELL" if qty > 0 else "BUY"
            size = abs(qty)

            # Qualify contract if needed (safe no-op if already qualified)
            try:
                await self.ib.qualifyContractsAsync(contract)
            except Exception:
                try:
                    self.ib.qualifyContracts(contract)
                except Exception:
                    pass

            try:
                trade = self.ib.placeOrder(contract, MarketOrder(action, size, tif="DAY"))
                while not trade.isDone():
                    await asyncio.sleep(0.2)
                self._tk(self.gui.log_message, f"Closed: {action} {size} {getattr(contract, 'localSymbol', '')}")
            except Exception as e:
                self._tk(self.gui.log_message, f"Close all: order failed for {getattr(contract, 'localSymbol', '')}: {e}")
                
        # Verify
        still_open = [p for p in self.ib.positions() if int(getattr(p, "position", 0)) != 0]
        if still_open:
            desc = ", ".join(f"{getattr(p.contract,'localSymbol','')}: {int(p.position)}" for p in still_open)
            self._tk(self.gui.log_message, f"WARNING: positions still open after liquidation: {desc}")
        else:
            self._tk(self.gui.log_message, "All positions confirmed closed.")

        
    async def cancel_all_open_orders(self) -> None:
        """Cancel all open orders (so nothing can re-open positions after closing)."""
        if not self.ib:
            self._tk(self.gui.log_message, "Cancel orders: no IB connection.")
            return

        # Global cancel (best-effort)
        try:
            self.ib.reqGlobalCancel()
        except Exception as e:
            self._tk(self.gui.log_message, f"Cancel orders: reqGlobalCancel failed: {e}")

        # Also cancel any locally-visible open trades
        try:
            for trade in list(self.ib.openTrades()):
                try:
                    self.ib.cancelOrder(trade.order)
                except Exception:
                    pass
        except Exception:
            pass

        self._tk(self.gui.log_message, "Cancel orders: cancel requested.")

    def update_portfolio(self, contracts_bought: int, direction: str) -> None:
        """Update internal portfolio state and notify the GUI."""
        self.contracts_bought = contracts_bought
        self.direction = direction
        try:
            self._tk(self.gui.on_portfolio_updated, contracts_bought, direction)
        except Exception:
            self._tk(self.gui.update_portfolio_display)

    async def _heartbeat(self):
        while not self._shutdown.is_set():
            await asyncio.sleep(0.2)

    # ------------------------- Utility: Contract selection -------------------------
    def get_current_futures_contract(self) -> Tuple[str, str]:
        month_codes = {3: "H", 6: "M", 9: "U", 12: "Z"}
        current_date = datetime.now()
        year = current_date.year
        month = current_date.month

        quarterly_months = [3, 6, 9, 12]

        if month >= quarterly_months[-1]:
            exp_month = quarterly_months[0]
            year += 1
        else:
            exp_month = next((m for m in quarterly_months if m >= month), quarterly_months[0])

        exp_day = self.get_third_friday(year, exp_month)
        exp_date = datetime(year, exp_month, exp_day)
        if current_date >= exp_date:
            idx = quarterly_months.index(exp_month)
            if idx == len(quarterly_months) - 1:
                exp_month = quarterly_months[0]
                year += 1
            else:
                exp_month = quarterly_months[idx + 1]

        contract_symbol = f"ES{month_codes[exp_month]}{str(year)[-2:]}"
        contract_expiration = f"{year}{exp_month:02d}"
        return contract_symbol, contract_expiration

    def get_third_friday(self, year: int, month: int) -> int:
        cal = calendar.monthcalendar(year, month)
        return cal[2][calendar.FRIDAY] if cal[0][calendar.FRIDAY] > 0 else cal[3][calendar.FRIDAY]

    # ------------------------- Tkinter helper -------------------------
    def _tk(self, func, *args, **kwargs):
        """Execute *func* on the Tkinter thread if possible."""
        try:
            self.gui.root.after(0, lambda: func(*args, **kwargs))
        except Exception:
            try:
                func(*args, **kwargs)
            except Exception:
                pass
