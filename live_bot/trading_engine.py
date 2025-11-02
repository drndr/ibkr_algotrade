import asyncio
import threading
import calendar
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from ib_insync import IB, Future as IBFuture, util, MarketOrder


class TradingEngine:
    """
    Clean, thread-safe wrapper around ib_insync for a simple ES-futures strategy.

    Key idea: **All IB API calls run on the single IB event-loop thread.**
    - We start a dedicated thread (self.ib_thread) that owns the asyncio loop managed by ib_insync.
    - From any other thread (GUI/main), we schedule work onto that loop via `_run_in_ib_loop`.
    - No ThreadPoolExecutor is used for IB calls.

    Callback handler contract (expected to be thread-safe on its side):
      - log_message(msg: str)
      - on_connection_success(contract_symbol: str, account_info: dict)
      - on_connection_error(error: str)
      - on_balance_updated(current_balance: float, available_funds: float)
      - on_previous_close_updated(previous_close: float)
      - on_price_updated(price: float, bar_time)
      - on_portfolio_updated(contracts_bought: int, direction: Optional[str])
      - on_trading_error(error: str)
    """

    def __init__(self, callback_handler):
        self.callback = callback_handler
        self.ib: Optional[IB] = None
        self.is_trading: bool = False
        self.current_contract: Optional[IBFuture] = None
        self.previous_close: Optional[float] = None
        self.direction: Optional[str] = None
        self.first_cross: bool = True
        self.contracts_bought: int = 0
        self.long_positions = []  # list of entry contract values
        self.short_positions = []  # list of entry contract values
        self.initial_balance: float = 0.0
        self.current_balance: float = 0.0
        self.account_id: Optional[str] = None
        self.last_bar_time = None
        self.available_funds: float = 0.0

        # Real-time bars subscription holder
        self.rt_bars = None

        # Threading
        self.ib_thread: Optional[threading.Thread] = None
        self.is_connected: bool = False
        self.shutdown_event = threading.Event()

    # ------------------------- Utility: Contract selection -------------------------
    def get_current_futures_contract(self) -> Tuple[str, str]:
        """Return (symbol_for_logging, expiration_YYYYMM) for the current ES quarterly contract."""
        month_codes = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}
        current_date = datetime.now()
        year = current_date.year
        month = current_date.month

        quarterly_months = [3, 6, 9, 12]

        # Next quarterly month >= current month
        if month >= quarterly_months[-1]:
            exp_month = quarterly_months[0]
            year += 1
        else:
            exp_month = next((m for m in quarterly_months if m >= month), quarterly_months[0])

        # If it's already past the 3rd Friday of that month, roll to next quarter
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

    # ------------------------- Core threading/loop bridge -------------------------
    def _run_in_ib_loop(self, func, *args, **kwargs):
        """
        Run a function or coroutine safely inside IB's loop.
        """
        if not self.is_connected or not self.ib:
            return {"error": "Not connected to IB"}

        try:
            result = func(*args, **kwargs)
            return self.ib.run(result) if asyncio.iscoroutine(result) else result
        except Exception as e:
            return {"error": str(e)}


    # ------------------------- IB thread worker -------------------------
    def _ib_worker(self):
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except Exception:
            pass

        self.ib = IB()

        try:
            # Establish connection (adjust host/port/clientId to your TWS/Gateway)
            self.ib.connect('127.0.0.1', 7497, clientId=2)
            self.is_connected = True
            self.callback.log_message("Connected to Interactive Brokers")

            # Prime account info
            account_info = self._get_account_info_sync()

            # Prepare contract
            contract_symbol, contract_expiration = self.get_current_futures_contract()
            self.current_contract = IBFuture('ES', contract_expiration, 'CME')
            self.callback.log_message(f"Created contract: {contract_symbol}")

            # Notify UI
            self.callback.on_connection_success(contract_symbol, account_info)

            # Keep loop alive, handle events
            while not self.shutdown_event.is_set():
                self.ib.sleep(0.1)

        except Exception as e:
            self.callback.log_message(f"IB worker error: {e}")
            self.callback.on_connection_error(str(e))
            self.is_connected = False
        finally:
            try:
                if self.rt_bars is not None:
                    try:
                        self.rt_bars.updateEvent -= self._on_bar_update
                    except Exception:
                        pass
                    self.rt_bars = None
                if self.ib and self.ib.isConnected():
                    self.ib.disconnect()
            finally:
                self.is_connected = False

    # ------------------------- Account info / balance -------------------------
    def _get_account_info_sync(self) -> Dict[str, Any]:
        try:
            # Trigger fetch and give TWS a moment to populate cache
            self.ib.reqAccountSummary()
            self.ib.sleep(1.5)
            account_values = self.ib.accountSummary()

            info = {
                'account_id': 'Unknown',
                'net_liquidation': 0.0,
                'available_funds': 0.0,
                'buying_power': 0.0,
                'cash_balance': 0.0,
                'unrealized_pnl': 0.0,
                'realized_pnl': 0.0,
                'gross_position_value': 0.0,
                'excess_liquidity': 0.0,
                'init_margin_req': 0.0,
                'maint_margin_req': 0.0,
            }

            tag_map = {
                'AccountCode': 'account_id',
                'NetLiquidation': 'net_liquidation',
                'AvailableFunds': 'available_funds',
                'BuyingPower': 'buying_power',
                'CashBalance': 'cash_balance',
                'UnrealizedPnL': 'unrealized_pnl',
                'RealizedPnL': 'realized_pnl',
                'GrossPositionValue': 'gross_position_value',
                'ExcessLiquidity': 'excess_liquidity',
                'InitMarginReq': 'init_margin_req',
                'MaintMarginReq': 'maint_margin_req',
            }

            for item in account_values:
                if item.tag not in tag_map:
                    continue
                key = tag_map[item.tag]
                if item.tag == 'AccountCode':
                    info[key] = item.value
                    self.account_id = item.value
                else:
                    try:
                        val = float(item.value)
                        info[key] = val
                        if item.tag == 'NetLiquidation':
                            self.current_balance = val
                            if self.initial_balance == 0:
                                self.initial_balance = val
                        elif item.tag == 'AvailableFunds':
                            self.available_funds = val
                    except Exception:
                        self.callback.log_message(f"Non-numeric account value for {item.tag}: {item.value}")

            # Optional summary log
            self.callback.log_message(
                f"Account Summary - Net: {info['net_liquidation']:.2f}, "
                f"Available: {info['available_funds']:.2f}, "
                f"Positions: {info['gross_position_value']:.2f}, "
                f"UnrlzdPnL: {info['unrealized_pnl']:.2f}"
            )
            return info
        except Exception as e:
            self.callback.log_message(f"Error getting account info: {e}")
            return {"account_id": "Error", "error": str(e)}

    def refresh_balance(self):
        if not self.is_connected:
            self.callback.log_message("Not connected to IB - cannot refresh balance")
            return

        try:
            # run the sync getter inside IB's event loop
            account_info = self.ib.run(self._get_account_info_sync())
        except Exception as e:
            self.callback.log_message(f"Error refreshing balance: {e}")
            return

        self.current_balance = account_info.get('net_liquidation', 0)
        self.available_funds = account_info.get('available_funds', 0)
        self.callback.on_balance_updated(self.current_balance, self.available_funds)
        self.callback.log_message(f"Balance refreshed: ${self.current_balance:,.2f}")

    # ------------------------- Market data & strategy -------------------------
    def _get_previous_close_sync(self) -> float:
        # Choose previous business day
        d = datetime.now() - timedelta(days=1)
        while d.weekday() >= 5:  # 5=Sat, 6=Sun
            d -= timedelta(days=1)
        end_ts = d.strftime("%Y%m%d %H:%M:%S")

        bars = self.ib.reqHistoricalData(
            self.current_contract,
            endDateTime=end_ts,
            durationStr='1 D',
            barSizeSetting='1 day',
            whatToShow='MIDPOINT',
            useRTH=True,
        )
        if not bars:
            raise RuntimeError("Could not retrieve previous day's close")
        df = util.df(bars)
        self.previous_close = float(df['close'].iloc[-1])
        self.callback.log_message(f"Previous close: {self.previous_close:.2f}")
        return self.previous_close

    def _subscribe_realtime_bars_sync(self):
        # One-day rolling, 5-minute bars, keepUpToDate
        self.rt_bars = self.ib.reqHistoricalData(
            self.current_contract,
            endDateTime='',
            durationStr='1 D',
            barSizeSetting='5 mins',
            whatToShow='MIDPOINT',
            useRTH=True,
            keepUpToDate=True,
        )
        self.rt_bars.updateEvent += self._on_bar_update
        self.callback.log_message("Subscribed to real-time 5-minute bars (RTH)")

    def _on_bar_update(self, bars, hasNewBar):
        # Executed on IB thread
        if not self.is_trading or not hasNewBar:
            return
        try:
            latest_bar = bars[-1]
            current_price = float(latest_bar.close)
            bar_time = latest_bar.time

            # Push price to GUI without blocking IB loop
            threading.Thread(
                target=self.callback.on_price_updated,
                args=(current_price, bar_time),
                daemon=True,
            ).start()

            if self.last_bar_time is None or bar_time > self.last_bar_time:
                self.last_bar_time = bar_time
                self._process_trading_signal(current_price)
        except Exception as e:
            self.callback.log_message(f"Error processing bar update: {e}")

    def _process_trading_signal(self, current_price: float):
        if self.previous_close is None:
            return
        self.callback.log_message(
            f"Signal - Price: {current_price:.2f}, PrevClose: {self.previous_close:.2f}"
        )

        if self.first_cross:
            if current_price > self.previous_close:
                self._execute_trade('long', 1, current_price)
                self.direction = 'long'
                self.first_cross = False
                self.callback.log_message(f"First cross UP - Long 1 @ {current_price:.2f}")
            elif current_price < self.previous_close:
                self._execute_trade('short', 1, current_price)
                self.direction = 'short'
                self.first_cross = False
                self.callback.log_message(f"First cross DOWN - Short 1 @ {current_price:.2f}")
        elif self.direction == 'long' and current_price < self.previous_close:
            self._execute_trade('short', 2, current_price)
            self.direction = 'short'
            self.callback.log_message(f"Cross DOWN - Short 2 @ {current_price:.2f}")
        elif self.direction == 'short' and current_price > self.previous_close:
            self._execute_trade('long', 2, current_price)
            self.direction = 'long'
            self.callback.log_message(f"Cross UP - Long 2 @ {current_price:.2f}")

        threading.Thread(
            target=self.callback.on_portfolio_updated,
            args=(self.contracts_bought, self.direction),
            daemon=True,
        ).start()

    def _execute_trade(self, trade_type: str, quantity: int, price: float):
        # NOTE: This is a SIMULATED execution. To place real orders, uncomment lines below.
        try:
            contract_value = price * 50  # ES multiplier
            if trade_type == 'long':
                self.long_positions.extend([contract_value] * quantity)
            elif trade_type == 'short':
                self.short_positions.extend([contract_value] * quantity)
            else:
                raise ValueError(f"Unknown trade_type: {trade_type}")

            self.contracts_bought += quantity

            # --- REAL ORDER EXAMPLE ---
            # side = 'BUY' if trade_type == 'long' else 'SELL'
            # order = MarketOrder(side, quantity)
            # trade = self.ib.placeOrder(self.current_contract, order)
            # self.callback.log_message(f"Order submitted: {side} {quantity}")

            self.callback.log_message(
                f"SIMULATED TRADE: {trade_type.upper()} {quantity} @ {price:.2f}"
            )
        except Exception as e:
            self.callback.log_message(f"Error executing trade: {e}")

    # ------------------------- Public API -------------------------
    def connect_to_ib(self):
        if self.ib_thread and self.ib_thread.is_alive():
            self.callback.log_message("Already connected or connecting...")
            return
        self.shutdown_event.clear()
        self.ib_thread = threading.Thread(target=self._ib_worker, daemon=True)
        self.ib_thread.start()

    def start_trading(self):
        if not self.is_connected:
            self.callback.on_trading_error("Not connected to Interactive Brokers")
            return
        self.is_trading = True
        self.direction = None
        self.first_cross = True
        self.contracts_bought = 0
        self.long_positions.clear()
        self.short_positions.clear()

        # Refresh balance and start subscriptions on IB loop
        self.refresh_balance()

        def _go():
            prev = self._get_previous_close_sync()
            self._subscribe_realtime_bars_sync()
            self.callback.on_previous_close_updated(prev)
            return {"success": True}

        result = self._run_in_ib_loop(_go)
        if isinstance(result, dict) and "error" in result:
            self.callback.on_trading_error(result["error"])
        else:
            self.callback.log_message("Trading started (5-min bars, RTH)")

    def stop_trading(self):
        self.is_trading = False
        self.callback.log_message("Trading stopped.")

        def _cleanup_and_close_open_positions():
            # Unsubscribe
            if self.rt_bars is not None:
                try:
                    self.rt_bars.updateEvent -= self._on_bar_update
                except Exception:
                    pass
                self.rt_bars = None

            # Close simulated positions at a reasonable current price snapshot
            return self._close_positions_sync()

        if self.long_positions or self.short_positions:
            self.callback.log_message("Closing all open positions (simulated)...")
            result = self._run_in_ib_loop(_cleanup_and_close_open_positions)
            if isinstance(result, dict) and "error" in result:
                self.callback.log_message(f"Error closing positions: {result['error']}")

    def _close_positions_sync(self) -> Dict[str, Any]:
        # Get a quick snapshot price
        ticker = self.ib.reqMktData(self.current_contract, '', False, False)
        self.ib.sleep(1.5)
        price = None
        try:
            if ticker.last and not math.isnan(ticker.last):
                price = float(ticker.last)
            elif ticker.bid and not math.isnan(ticker.bid):
                price = float(ticker.bid)
            elif ticker.ask and not math.isnan(ticker.ask):
                price = float(ticker.ask)
        except Exception:
            price = None

        if price is None:
            raise RuntimeError("Could not get current price for closing positions")

        self._close_positions_at_price(price)
        return {"success": True}

    def _close_positions_at_price(self, close_price: float):
        contract_value = close_price * 50

        if self.long_positions:
            long_count = len(self.long_positions)
            total_cost = sum(self.long_positions)
            total_value = long_count * contract_value
            pnl = total_value - total_cost
            self.callback.log_message(
                f"SIM CLOSE: {long_count} long @ {close_price:.2f} - P&L: {pnl:.2f}"
            )
            self.long_positions = []

        if self.short_positions:
            short_count = len(self.short_positions)
            total_cost = sum(self.short_positions)
            total_value = short_count * contract_value
            pnl = total_cost - total_value
            self.callback.log_message(
                f"SIM CLOSE: {short_count} short @ {close_price:.2f} - P&L: {pnl:.2f}"
            )
            self.short_positions = []

        threading.Thread(
            target=self.callback.on_portfolio_updated,
            args=(self.contracts_bought, self.direction),
            daemon=True,
        ).start()

    def disconnect(self):
        self.is_trading = False
        self.is_connected = False
        self.shutdown_event.set()

        # Release RT bars on loop thread if needed
        def _cleanup():
            if self.rt_bars is not None:
                try:
                    self.rt_bars.updateEvent -= self._on_bar_update
                except Exception:
                    pass
                self.rt_bars = None

        if self.ib and self.ib.loop:
            try:
                self._run_in_ib_loop(_cleanup)
            except Exception:
                pass

        if self.ib_thread and self.ib_thread.is_alive():
            self.ib_thread.join(timeout=5)
        self.callback.log_message("Disconnected from Interactive Brokers")

    def is_connected_to_ib(self) -> bool:
        return bool(self.is_connected and self.ib_thread and self.ib_thread.is_alive())
