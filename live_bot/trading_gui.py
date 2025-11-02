import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime
from trading_engine_step7f import TradingEngine

class TradingGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Live Futures Trading - 5 Min RTH")
        self.root.geometry("900x700")
        
        # Initialize trading engine with callback
        self.trading_engine = TradingEngine(self)
        
        # UI state variables
        self.initial_balance = 0
        self.current_balance = 0
        self.available_funds = 0
        
        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_ui(self):
        """Create the user interface"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Status frame
        status_frame = ttk.LabelFrame(main_frame, text="Trading Status", padding="5")
        status_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.connection_label = ttk.Label(status_frame, text="Status: Disconnected", foreground="red")
        self.connection_label.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        
        self.contract_label = ttk.Label(status_frame, text="Contract: Not loaded")
        self.contract_label.grid(row=0, column=1, padx=20, pady=5, sticky=tk.W)
        
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

    def start_trading(self):
        """Start live trading"""
        self.start_trading_button.config(state="disabled")
        self.stop_trading_button.config(state="normal")
        self.trading_engine.start_trading()

    def stop_trading(self):
        """Stop live trading"""
        self.start_trading_button.config(state="normal")
        self.stop_trading_button.config(state="disabled")
        self.trading_engine.stop_trading()

    def refresh_balance(self):
        """Refresh account balance"""
        self.trading_engine.refresh_balance()

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
