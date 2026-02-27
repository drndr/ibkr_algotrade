import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime

from trade_engine import TradingEngine
from strategies import HorizontalLineStrategy

# Maps the UI label to the strategy's ref_source string
_REF_SOURCE_MAP = {
    "Previous day close":      "prev_close",
    "Today open (RTH)":        "day_open_rth",
    "Today open (Full day 23h)": "day_open_full",
}

class TradingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Live Futures Trading - All Sessions")
        self.root.geometry("900x700")

        # Initialize trading engine with callback
        self.trading_engine = TradingEngine(self)

        # UI state variables
        self.initial_balance = 0
        self.current_balance = 0
        self.available_funds = 0

        self.bar_size_var = tk.StringVar(value="5 mins")
        self.trading_engine.set_bar_size(self.bar_size_var.get())

        # Trading Hours: "RTH only" | "Full day (23h)"
        self.trading_hours_var = tk.StringVar(value="Full day (23h)")
        self.trading_engine.set_use_rth(False)

        # Reference Line: "Fixed" | "Dynamic"
        self.ref_line_var = tk.StringVar(value="Dynamic")

        # Reference source: "Previous day close" | "Today open"
        self.ref_source_var = tk.StringVar(value="Previous day close")

        # Build UI first (creates self.log_text, etc.)
        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Attach horizontal line strategy AFTER UI exists
        self.strategy = HorizontalLineStrategy(
            use_dynamic_reference=(self.ref_line_var.get() == "Dynamic"),
            ref_source=_REF_SOURCE_MAP.get(self.ref_source_var.get(), "prev_close"),
        )
        self.trading_engine.set_strategy(self.strategy)

        self.log_message(
            f"HorizontalLineStrategy attached "
            f"(use_dynamic_reference={self.strategy.use_dynamic_reference})"
        )

    def create_ui(self):
        """Create the user interface"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Status frame
        status_frame = ttk.LabelFrame(main_frame, text="Trading Status", padding="5")
        status_frame.pack(fill=tk.X, padx=5, pady=5)

        # Row 0: connection status + contract
        self.connection_label = ttk.Label(status_frame, text="Status: Disconnected", foreground="red")
        self.connection_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        self.contract_label = ttk.Label(status_frame, text="Contract: Not loaded")
        self.contract_label.grid(row=0, column=1, padx=20, pady=5, sticky=tk.W)

        # Row 1: Bar Size + Trading Hours
        ttk.Label(status_frame, text="Bar Size:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.bar_size_combo = ttk.Combobox(
            status_frame,
            textvariable=self.bar_size_var,
            values=["1 min", "5 mins", "15 mins", "30 mins", "1 hour"],
            state="readonly",
            width=10,
        )
        self.bar_size_combo.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        self.bar_size_combo.bind("<<ComboboxSelected>>", self.on_bar_size_change)

        ttk.Label(status_frame, text="Trading Hours:").grid(row=1, column=2, padx=(20, 5), pady=5, sticky=tk.W)
        self.trading_hours_combo = ttk.Combobox(
            status_frame,
            textvariable=self.trading_hours_var,
            values=["RTH only", "Full day (23h)"],
            state="readonly",
            width=14,
        )
        self.trading_hours_combo.grid(row=1, column=3, padx=5, pady=5, sticky=tk.W)
        self.trading_hours_combo.bind("<<ComboboxSelected>>", self.on_trading_hours_change)

        # Row 2: Ref Line + Reference source
        ttk.Label(status_frame, text="Ref Line:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)
        self.ref_line_combo = ttk.Combobox(
            status_frame,
            textvariable=self.ref_line_var,
            values=["Fixed", "Dynamic"],
            state="readonly",
            width=10,
        )
        self.ref_line_combo.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W)
        self.ref_line_combo.bind("<<ComboboxSelected>>", self.on_ref_line_change)

        ttk.Label(status_frame, text="Reference:").grid(row=2, column=2, padx=(20, 5), pady=5, sticky=tk.W)
        self.ref_source_combo = ttk.Combobox(
            status_frame,
            textvariable=self.ref_source_var,
            values=["Previous day close", "Today open (RTH)", "Today open (Full day 23h)"],
            state="readonly",
            width=22,
        )
        self.ref_source_combo.grid(row=2, column=3, padx=5, pady=5, sticky=tk.W)
        self.ref_source_combo.bind("<<ComboboxSelected>>", self.on_ref_source_change)

        # Control buttons frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, padx=5, pady=5)

        self.connect_button = ttk.Button(button_frame, text="CONNECT TO IB",
                                        command=self.connect_to_ib, width=15)
        self.connect_button.pack(side=tk.LEFT, padx=5)

        self.start_trading_button = ttk.Button(button_frame, text="START TRADING",
                                              command=self.start_trading, width=15, state="disabled")
        self.start_trading_button.pack(side=tk.LEFT, padx=5)

        self.stop_trading_button = ttk.Button(button_frame, text="STOP TRADING",
                                             command=self.stop_trading, width=15, state="disabled")
        self.stop_trading_button.pack(side=tk.LEFT, padx=5)

        self.refresh_button = ttk.Button(button_frame, text="REFRESH BALANCE",
                                        command=self.refresh_balance, width=15)
        self.refresh_button.pack(side=tk.LEFT, padx=5)

        # Portfolio info frame
        portfolio_frame = ttk.LabelFrame(main_frame, text="Portfolio Information", padding="5")
        portfolio_frame.pack(fill=tk.X, padx=5, pady=5)

        self.balance_label = ttk.Label(portfolio_frame, text="Current Balance: Not loaded")
        self.balance_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        self.pnl_label = ttk.Label(portfolio_frame, text="P&L: $0.00")
        self.pnl_label.grid(row=0, column=1, padx=20, pady=5, sticky=tk.W)

        self.contracts_label = ttk.Label(portfolio_frame, text="Contracts Traded: 0")
        self.contracts_label.grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)

        self.direction_label = ttk.Label(portfolio_frame, text="Current Direction: None")
        self.direction_label.grid(row=1, column=1, padx=20, pady=5, sticky=tk.W)

        self.account_label = ttk.Label(portfolio_frame, text="Account: Not loaded")
        self.account_label.grid(row=2, column=0, padx=5, pady=5, sticky=tk.W)

        self.available_funds_label = ttk.Label(portfolio_frame, text="Available Funds: --")
        self.available_funds_label.grid(row=2, column=1, padx=20, pady=5, sticky=tk.W)

        # Market data frame
        market_frame = ttk.LabelFrame(main_frame, text="Market Data", padding="5")
        market_frame.pack(fill=tk.X, padx=5, pady=5)

        self.price_label = ttk.Label(market_frame, text="Current Price: --")
        self.price_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        self.prev_close_label = ttk.Label(market_frame, text="Previous Close: --")
        self.prev_close_label.grid(row=0, column=1, padx=20, pady=5, sticky=tk.W)

        self.last_update_label = ttk.Label(market_frame, text="Last Update: --")
        self.last_update_label.grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)

        # Trading log
        log_frame = ttk.LabelFrame(main_frame, text="Trading Log", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=25)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # Button handlers
    def connect_to_ib(self):
        """Connect to Interactive Brokers"""
        self.connect_button.config(state="disabled")
        self.log_message("Attempting to connect to Interactive Brokers...")
        self.trading_engine.connect_to_ib()

    def _set_session_controls_state(self, active: bool) -> None:
        """Enable or disable all session-config dropdowns during trading."""
        state = "readonly" if active else "disabled"
        self.bar_size_combo.config(state=state)
        self.trading_hours_combo.config(state=state)
        self.ref_line_combo.config(state=state)
        self.ref_source_combo.config(state=state)

    def start_trading(self):
        """Start live trading"""
        self.start_trading_button.config(state="disabled")
        self.stop_trading_button.config(state="normal")
        self._set_session_controls_state(False)
        self.trading_engine.start_trading()

    def stop_trading(self):
        """Stop live trading"""
        self.start_trading_button.config(state="normal")
        self.stop_trading_button.config(state="disabled")
        self._set_session_controls_state(True)
        self.trading_engine.stop_trading()

    def refresh_balance(self):
        """Refresh account balance"""
        self.trading_engine.refresh_balance()

    def on_trading_hours_change(self, _event=None):
        """Switch between RTH-only and full 23-hour session."""
        use_rth = (self.trading_hours_var.get() == "RTH only")
        self.trading_engine.set_use_rth(use_rth)
        mode = "RTH" if use_rth else "All Sessions"
        self.root.title(f"Live Futures Trading - {self.bar_size_var.get()} {mode}")
        self.log_message(f"Trading hours: {self.trading_hours_var.get()}")

    def on_ref_line_change(self, _event=None):
        """Switch between Fixed and Dynamic reference line."""
        use_dyn = (self.ref_line_var.get() == "Dynamic")
        if hasattr(self, "strategy") and self.strategy is not None:
            self.strategy.use_dynamic_reference = use_dyn
            # If switching to Fixed, pin to current previous_close (if available)
            if not use_dyn and getattr(self.trading_engine, "previous_close", None) is not None:
                self.strategy.reference_line = float(self.trading_engine.previous_close)
            self.log_message(f"Reference line mode: {'DYNAMIC' if use_dyn else 'FIXED'}")
        else:
            self.log_message("Reference line mode changed (strategy not attached yet).")

    def on_ref_source_change(self, _event=None):
        """Switch reference source."""
        ref_source = _REF_SOURCE_MAP.get(self.ref_source_var.get(), "prev_close")
        if hasattr(self, "strategy") and self.strategy is not None:
            self.strategy.ref_source = ref_source
            self.log_message(f"Reference source: {self.ref_source_var.get()}")

    # Callback methods for trading engine
    def log_message(self, message):
        """Add a message to the log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
        print(f"[{timestamp}] {message}")

    def on_connection_success(self, contract_symbol, account_info):
        """Handle successful connection"""
        self.connection_label.config(text="Status: Connected", foreground="green")
        self.contract_label.config(text=f"Contract: {contract_symbol}")
        self.start_trading_button.config(state="normal")
        self.connect_button.config(text="RECONNECT", state="normal")

        # Update account information
        self.account_label.config(text=f"Account: {account_info.get('account_id', 'Unknown')}")
        self.balance_label.config(text=f"Net Liquidation: ${account_info.get('net_liquidation', 0):,.2f}")
        self.available_funds_label.config(text=f"Available Funds: ${account_info.get('available_funds', 0):,.2f}")

        # Store initial values
        self.initial_balance = account_info.get('net_liquidation', 0)
        self.current_balance = account_info.get('net_liquidation', 0)
        self.available_funds = account_info.get('available_funds', 0)

        self.log_message(f"Successfully connected! Using contract: {contract_symbol}")
        self.log_message(f"Account: {account_info.get('account_id', 'Unknown')}")
        self.log_message(f"Net Liquidation Value: ${account_info.get('net_liquidation', 0):,.2f}")
        self.log_message(f"Available Funds: ${account_info.get('available_funds', 0):,.2f}")

        self.update_portfolio_display()

    def on_connection_error(self, error_msg):
        """Handle connection error"""
        self.log_message(f"Connection failed: {error_msg}")
        self.connect_button.config(state="normal")
        messagebox.showerror("Connection Error", f"Failed to connect to IB: {error_msg}")

    def on_trading_error(self, error_msg):
        """Handle trading error"""
        messagebox.showerror("Error", error_msg)

    def on_balance_updated(self, current_balance, available_funds):
        """Handle balance update from engine"""
        self.current_balance = current_balance
        self.available_funds = available_funds
        self.update_portfolio_display()
        self.available_funds_label.config(text=f"Available Funds: ${available_funds:,.2f}")

    def on_previous_close_updated(self, previous_close):
        """Handle previous close update"""
        self.prev_close_label.config(text=f"Previous Close: {previous_close:.2f}")

    def on_price_updated(self, current_price, bar_time):
        """Handle price update"""
        self.price_label.config(text=f"Current Price: {current_price:.2f}")
        self.last_update_label.config(text=f"Last Update: {bar_time.strftime('%H:%M:%S')}")

    def on_portfolio_updated(self, contracts_bought, direction):
        """Handle portfolio update"""
        self.trading_engine.contracts_bought = contracts_bought
        self.trading_engine.direction = direction
        self.update_portfolio_display()
        # Re-enable session controls if the engine stopped trading (e.g. EOD auto-close)
        if not self.trading_engine.is_trading:
            self._set_session_controls_state(True)
            self.start_trading_button.config(state="normal")
            self.stop_trading_button.config(state="disabled")

    def update_portfolio_display(self):
        """Update the portfolio information display"""
        if self.initial_balance > 0:
            pnl = self.current_balance - self.initial_balance
            self.pnl_label.config(text=f"P&L: ${pnl:,.2f}")
        else:
            self.pnl_label.config(text="P&L: Not available")

        contracts = getattr(self.trading_engine, 'contracts_bought', 0)
        direction = getattr(self.trading_engine, 'direction', None)

        self.contracts_label.config(text=f"Contracts Traded: {contracts}")
        direction_text = direction if direction else "None"
        self.direction_label.config(text=f"Current Direction: {direction_text}")

        if hasattr(self, 'current_balance') and self.current_balance > 0:
            self.balance_label.config(text=f"Current Balance: ${self.current_balance:,.2f}")

    def on_bar_size_change(self, _event=None):
        bar_size = self.bar_size_var.get()
        self.trading_engine.set_bar_size(bar_size)
        use_rth = (self.trading_hours_var.get() == "RTH only")
        suffix = " RTH" if use_rth else " All Sessions"
        self.root.title(f"Live Futures Trading - {bar_size}{suffix}")

    def on_closing(self):
        """Handle application closing"""
        if self.trading_engine.is_trading:
            self.stop_trading()

        self.trading_engine.disconnect()
        self.root.destroy()


def main():
    """Main entry point"""
    root = tk.Tk()
    app = TradingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
