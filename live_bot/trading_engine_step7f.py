
import threading
import asyncio
import traceback
import calendar
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

try:
    from ib_insync import IB, Future as IBFuture  # type: ignore
except Exception as e:
    IB = None
    IBFuture = None

class TradingEngine:
    is_trading: bool = False
    contracts_bought: int = 0
    direction: Optional[str] = None

    def __init__(self, gui):
        self.gui = gui
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
                self._fetch_account_info_sync()
            except Exception as e:
                self._tk(self.gui.log_message, f"Error refreshing balance: {e}")
        self.loop.call_soon_threadsafe(task)

    def refresh_previous_close(self):
        if not self.is_connected or not self.ib or not self.loop:
            self._tk(self.gui.log_message, "Not connected to IB - cannot refresh previous close")
            return

        def task():
            try:
                close_price = self._fetch_previous_close_sync()
                if close_price is not None:
                    self._tk(self.gui.on_previous_close_updated, close_price)
                    self._tk(self.gui.log_message, f"Previous close: {close_price:.2f}")
                else:
                    self._tk(self.gui.log_message, "No previous close data")
            except Exception as e:
                self._tk(self.gui.log_message, f"Error refreshing previous close: {e}")
        self.loop.call_soon_threadsafe(task)

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

            self._tk(self.gui.log_message, "Attempting to connect to Interactive Brokers...")
            self.loop.run_until_complete(self.ib.connectAsync('127.0.0.1', 7497, clientId=101))

            self.is_connected = True
            self._tk(self.gui.log_message, "Connected to Interactive Brokers")

            try:
                self.ib.reqMarketDataType(3)  # delayed mode
            except Exception:
                pass

            # Create ES contract
            contract_symbol, contract_expiration = self.get_current_futures_contract()
            if IBFuture:
                self.current_contract = IBFuture('ES', contract_expiration, 'CME')
                self._tk(self.gui.log_message, f"Created contract: {contract_symbol}")
                try:
                    qualified = self.ib.qualifyContracts(self.current_contract)
                    if not qualified:
                        self._tk(self.gui.log_message, "Warning: contract qualification failed")
                    else:
                        self._tk(self.gui.log_message, f"Qualified contract: {self.current_contract}")
                except Exception as e:
                    self._tk(self.gui.log_message, f"Contract qualification error: {e}")
            else:
                contract_symbol = "ES-Unknown"

            # Fetch account info synchronously
            self._fetch_account_info_sync()

            # Notify GUI
            info = {
                "account_id": self.account_id or "Unknown",
                "net_liquidation": self.current_balance,
                "available_funds": self.available_funds,
            }
            self._tk(self.gui.on_connection_success, contract_symbol, info)

            # Fetch previous close synchronously
            try:
                close_price = self._fetch_previous_close_sync()
                if close_price is not None:
                    self._tk(self.gui.on_previous_close_updated, close_price)
                    self._tk(self.gui.log_message, f"Previous close: {close_price:.2f}")
            except Exception as e:
                self._tk(self.gui.log_message, f"Previous close fetch failed: {e}")

            self.loop.create_task(self._heartbeat())
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

    def _fetch_account_info_sync(self):
        if not self.ib:
            return
        try:
            self.ib.reqAccountSummary()
            self.ib.sleep(1.0)
            account_values = self.ib.accountSummary()

            info = {
                'account_id': 'Unknown',
                'net_liquidation': 0.0,
                'available_funds': 0.0,
                'gross_position_value': 0.0,
                'unrealized_pnl': 0.0,
            }

            for item in account_values:
                if item.tag == 'AccountCode':
                    self.account_id = item.value
                    info['account_id'] = item.value
                elif item.tag == 'NetLiquidation':
                    try:
                        self.current_balance = float(item.value)
                        if self.initial_balance == 0:
                            self.initial_balance = self.current_balance
                        info['net_liquidation'] = self.current_balance
                    except Exception:
                        pass
                elif item.tag == 'AvailableFunds':
                    try:
                        self.available_funds = float(item.value)
                        info['available_funds'] = self.available_funds
                    except Exception:
                        pass
                elif item.tag == 'GrossPositionValue':
                    try:
                        info['gross_position_value'] = float(item.value)
                    except Exception:
                        pass
                elif item.tag == 'UnrealizedPnL':
                    try:
                        info['unrealized_pnl'] = float(item.value)
                    except Exception:
                        pass

            self._tk(self.gui.log_message,
                     (f"Account Summary - Net: {info['net_liquidation']:.2f}, "
                      f"Available: {info['available_funds']:.2f}, "
                      f"Positions: {info['gross_position_value']:.2f}, "
                      f"UnrlzdPnL: {info['unrealized_pnl']:.2f}"))
            self._tk(self.gui.on_balance_updated, self.current_balance, self.available_funds)
        except Exception as e:
            self._tk(self.gui.log_message, f"Error fetching account info: {e}")

    def _fetch_previous_close_sync(self) -> Optional[float]:
        if not self.ib or not self.current_contract:
            return None
        try:
            # Compute previous trading day (skip weekends)
            prev_day = datetime.now() - timedelta(days=1)
            while prev_day.weekday() >= 5:  # Saturday=5, Sunday=6
                prev_day -= timedelta(days=1)
            previous_day_str = prev_day.strftime('%Y%m%d 23:59:59')

            bars = self.ib.reqHistoricalData(
                self.current_contract,
                endDateTime=previous_day_str,
                durationStr='1 D',
                barSizeSetting='1 day',
                whatToShow='MIDPOINT',
                useRTH=False
            )
            if bars:
                return float(bars[-1].close)
            return None
        except Exception as e:
            self._tk(self.gui.log_message, f"_fetch_previous_close_sync error: {e}")
            return None

    def _run_coro(self, coro):
        if not self.loop:
            raise RuntimeError("IB loop not initialized")
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=60.0)
        except Exception as e:
            self._tk(self.gui.log_message, f"_run_coro error: {type(e).__name__}: {e}")
            raise

    async def _heartbeat(self):
        while not self._shutdown.is_set():
            await asyncio.sleep(0.2)

    # ------------------------- Utility: Contract selection -------------------------
    def get_current_futures_contract(self) -> Tuple[str, str]:
        month_codes = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}
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

    def _tk(self, func, *args, **kwargs):
        try:
            self.gui.root.after(0, lambda: func(*args, **kwargs))
        except Exception:
            try:
                func(*args, **kwargs)
            except Exception:
                pass
