import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
from ib_insync import *
import pandas as pd
import asyncio
import io
import sys
from datetime import datetime, timedelta
import calendar

class SimpleTradingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Futures Trading Simulator")
        self.root.geometry("800x600")
        
        # Create main frame
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create date selection frame
        date_frame = ttk.LabelFrame(main_frame, text="Backtest Date Selection", padding="5")
        date_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Date entry components
        ttk.Label(date_frame, text="Test Date (YYYY-MM-DD):").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.date_entry = ttk.Entry(date_frame, width=15)
        self.date_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        
        # Default to today's date
        today = datetime.now().strftime("%Y-%m-%d")
        self.date_entry.insert(0, today)
        
        # Create timeframe selection frame
        timeframe_frame = ttk.LabelFrame(main_frame, text="Timeframe Selection", padding="5")
        timeframe_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Timeframe dropdown
        ttk.Label(timeframe_frame, text="Bar Timeframe:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        
        # Define timeframe options (display_name: ib_bar_size)
        self.timeframe_options = {
            "1 Minute": "1 min",
            "5 Minutes": "5 mins", 
            "15 Minutes": "15 mins",
            "30 Minutes": "30 mins",
            "1 Hour": "1 hour"
        }
        
        self.timeframe_var = tk.StringVar(value="5 Minutes")  # Default to 5 minutes
        self.timeframe_dropdown = ttk.Combobox(
            timeframe_frame, 
            textvariable=self.timeframe_var,
            values=list(self.timeframe_options.keys()),
            state="readonly",
            width=15
        )
        self.timeframe_dropdown.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        
        # Create radio button frame for RTH selection
        rth_frame = ttk.LabelFrame(main_frame, text="Trading Hours", padding="5")
        rth_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Create variable for radio buttons
        self.use_rth = tk.BooleanVar(value=True)
        
        # Create radio buttons
        self.rth_radio = ttk.Radiobutton(rth_frame, text="Regular Trading Hours (RTH)", 
                                         variable=self.use_rth, value=True,
                                         command=self.toggle_warning_label)
        self.rth_radio.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        
        self.full_day_radio = ttk.Radiobutton(rth_frame, text="Full Trading Day (23h)", 
                                             variable=self.use_rth, value=False,
                                             command=self.toggle_warning_label)
        self.full_day_radio.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        
        # Warning label for full trading day option
        self.warning_label = ttk.Label(
            rth_frame, 
            text="⚠️ Warning: Backtesting with data from the past 7 days may be incomplete due to IBKR data consolidation issues for 23-hours (full day) data.",
            foreground="red",
            wraplength=780
        )
        self.warning_label.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W)
        self.warning_label.grid_remove()  # Initially hidden
        
        # NEW: Create reference line strategy frame
        strategy_frame = ttk.LabelFrame(main_frame, text="Horizontal Line Strategy", padding="5")
        strategy_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Create variable for strategy selection
        self.use_dynamic_reference = tk.BooleanVar(value=False)
        
        # Create radio buttons for strategy selection
        self.fixed_reference_radio = ttk.Radiobutton(
            strategy_frame, 
            text="Fixed (Previous Day Close)", 
            variable=self.use_dynamic_reference, 
            value=False,
            command=self.toggle_strategy_info
        )
        self.fixed_reference_radio.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        
        self.dynamic_reference_radio = ttk.Radiobutton(
            strategy_frame, 
            text="Dynamic (Latest Bar Close)", 
            variable=self.use_dynamic_reference, 
            value=True,
            command=self.toggle_strategy_info
        )
        self.dynamic_reference_radio.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        
        # Strategy info labels
        self.strategy_info_label = ttk.Label(
            strategy_frame, 
            text="Fixed: Horizontal line stays at previous day's close throughout the day",
            foreground="blue",
            wraplength=780
        )
        self.strategy_info_label.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W)
        
        # Add START BACKTEST button
        self.start_button = ttk.Button(main_frame, text="START BACKTEST", 
                                      command=self.run_backtest, width=20)
        self.start_button.pack(pady=10)
        
        # Results text area
        self.results_text = scrolledtext.ScrolledText(main_frame, height=30)
        self.results_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
    def toggle_warning_label(self):
        """Show or hide the warning label based on the selected radio button"""
        if not self.use_rth.get():  # If Full Trading Day is selected
            self.warning_label.grid()  # Show warning
        else:
            self.warning_label.grid_remove()  # Hide warning
    
    def toggle_strategy_info(self):
        """Update the strategy info label based on selected strategy"""
        if self.use_dynamic_reference.get():
            self.strategy_info_label.config(
                text="Dynamic: Horizontal line updates to the close of each completed bar",
                foreground="green"
            )
        else:
            self.strategy_info_label.config(
                text="Fixed: Horizontal line stays at previous day's close throughout the day",
                foreground="blue"
            )
        
    def get_current_futures_contract(self, test_date=None):
        """
        Get the relevant ES futures contract for the given test date
        E-mini S&P 500 futures typically expire quarterly in March (H), June (M), 
        September (U), and December (Z).
        
        Args:
            test_date: Optional datetime object to use instead of current date
        """
        # Month codes for futures contracts
        month_codes = {
            3: 'H',  # March
            6: 'M',  # June
            9: 'U',  # September
            12: 'Z'  # December
        }
        
        # Use provided test date or current date
        if test_date:
            current_date = test_date
        else:
            current_date = datetime.now()
            
        year = current_date.year
        month = current_date.month
        
        # Find which quarterly cycle we're closest to
        quarterly_months = list(month_codes.keys())  # [3, 6, 9, 12]
        
        # Find the next expiration month
        if month >= quarterly_months[-1]:  # If we're in December or later
            exp_month = quarterly_months[0]  # Use March of next year
            year += 1
        else:
            # Find the closest upcoming quarterly month
            exp_month = next((m for m in quarterly_months if m >= month), quarterly_months[0])
        
        # Calculate the expiration date (third Friday of expiration month)
        exp_day = self.get_third_friday(year, exp_month)
        exp_date = datetime(year, exp_month, exp_day)
        
        # If we're past the expiration for the current quarter, move to the next quarter
        if current_date >= exp_date:
            idx = quarterly_months.index(exp_month)
            if idx == len(quarterly_months) - 1:  # If December, go to March next year
                exp_month = quarterly_months[0]
                year += 1
            else:
                exp_month = quarterly_months[idx + 1]
        
        # Format the contract symbol: SYMBOL + MONTH_CODE + YEAR_DIGIT
        contract_symbol = f"ES{month_codes[exp_month]}{str(year)[-2:]}"
        
        # Full contract identifier for IB
        contract_expiration = f"{year}{exp_month:02d}"
        
        return contract_symbol, contract_expiration
        
    def get_third_friday(self, year, month):
        """Calculate the third Friday of a given month and year"""
        # Get the calendar for the month
        cal = calendar.monthcalendar(year, month)
        
        # The third Friday is the third Friday ([4]) in the calendar
        # If the first Friday is in the first week, it's in the first row
        # Otherwise, it's in the second row
        if cal[0][calendar.FRIDAY] > 0:
            third_friday = cal[2][calendar.FRIDAY]
        else:
            third_friday = cal[3][calendar.FRIDAY]
            
        return third_friday
        
    def validate_date(self, date_str):
        try:
            # Check if the date is in the correct format
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Check if the date is not in the future
            if date_obj > datetime.now():
                return False, "Cannot backtest future dates"
                
            if date_obj.weekday() >= 5:
                return False, "Cannot test weekend"
            # Get contract for this specific test date
            contract_symbol, contract_expiration = self.get_current_futures_contract(date_obj)
            contract_year = int(contract_expiration[:4])
            contract_month = int(contract_expiration[4:6])
            
            # Calculate contract start date (roughly 9 months before expiration)
            months_before = 9
            start_month = contract_month - months_before
            start_year = contract_year
            if start_month <= 0:
                start_month += 12
                start_year -= 1
            
            # Create the approximate contract start date
            contract_start_date = datetime(start_year, start_month, 1)
            
            # Check if the backtest date is too old for the contract
            if date_obj < contract_start_date:
                return False, f"Test date is too old for contract {contract_symbol}. Must be after {contract_start_date.strftime('%Y-%m-%d')}"
                
            return True, date_obj
        except ValueError:
            return False, "Invalid date format. Please use YYYY-MM-DD"
        
    def run_backtest(self):
        # Validate the date before proceeding
        date_str = self.date_entry.get()
        valid_date, date_result = self.validate_date(date_str)
        
        if not valid_date:
            messagebox.showerror("Invalid Date", date_result)
            return
        
        # Get the contract for the specific test date
        date_obj = date_result  # This is now a datetime object from validate_date
        contract_symbol, contract_expiration = self.get_current_futures_contract(date_obj)
        
        # Get selected timeframe
        selected_timeframe = self.timeframe_var.get()
        ib_bar_size = self.timeframe_options[selected_timeframe]
            
        # Disable the button while running
        self.start_button.config(state="disabled")
        self.results_text.delete(1.0, tk.END)
        
        self.results_text.insert(tk.END, f"Starting backtest for {date_str}...\n")
        self.results_text.insert(tk.END, f"Using futures contract: {contract_symbol} (expiration: {contract_expiration})\n")
        self.results_text.insert(tk.END, f"Bar timeframe: {selected_timeframe}\n")
        
        # Show RTH status
        rth_status = "Regular Trading Hours" if self.use_rth.get() else "Full Trading Day (23h)"
        self.results_text.insert(tk.END, f"Trading hours mode: {rth_status}\n")
        
        # Show strategy status
        strategy_status = "Dynamic (Latest Bar Close)" if self.use_dynamic_reference.get() else "Fixed (Previous Day Close)"
        self.results_text.insert(tk.END, f"Horizontal line strategy: {strategy_status}\n\n")
        
        # Run the backtest in a separate thread
        threading.Thread(target=self.execute_backtest, args=(date_str, contract_expiration, self.use_rth.get(), ib_bar_size, selected_timeframe, self.use_dynamic_reference.get()), daemon=True).start()
    
    def execute_backtest(self, backtest_date, contract_expiration, use_rth, ib_bar_size, timeframe_display, use_dynamic_reference):
        try:
            # Create a custom stdout to capture print statements
            stdout_capture = io.StringIO()
            sys.stdout = stdout_capture
            
            # Run the async function using asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            final_value, contracts_bought, total_profit_loss = loop.run_until_complete(
                self.run_async_backtest(backtest_date, contract_expiration, use_rth, ib_bar_size, timeframe_display, use_dynamic_reference)
            )
            loop.close()
            
            # Restore stdout
            captured_output = stdout_capture.getvalue()
            sys.stdout = sys.__stdout__
            
            # Display results in the GUI
            self.root.after(0, lambda: self.update_results(final_value, contracts_bought, total_profit_loss, captured_output))
            
        except Exception as e:
            # Restore stdout in case of exception
            sys.stdout = sys.__stdout__
            
            # Handle any exceptions
            error_msg = f"Error: {str(e)}"
            self.root.after(0, lambda: self.results_text.insert(tk.END, error_msg + "\n"))
            self.root.after(0, lambda: self.start_button.config(state="normal"))
    
    async def run_async_backtest(self, backtest_date, contract_expiration, use_rth, ib_bar_size, timeframe_display, use_dynamic_reference):
        # Convert the date string to a datetime object
        target_date = datetime.strptime(backtest_date, "%Y-%m-%d") + timedelta(days=1)
        
        # Format the date for IB's historical data request
        target_date_str = target_date.strftime("%Y%m%d %H:%M:%S")
        
        # Get the previous trading day
        previous_day = target_date - timedelta(days=1)
        # If it's a weekend, go back to Friday
        while previous_day.weekday() >= 5:  # 5=Saturday, 6=Sunday
            previous_day = previous_day - timedelta(days=1)
        previous_day_str = previous_day.strftime("%Y%m%d %H:%M:%S")
        
        # Connect to IBKR Paper Trading
        ib = IB()
        await ib.connectAsync('127.0.0.1', 7497, clientId=1)  # Using async connect
        
        try:
            # Define the ES futures contract with the appropriate expiration
            contract = Future('ES', contract_expiration, 'CME')
            
            print(f"Backtesting for date: {backtest_date}")
            print(f"Using contract: ES with expiration {contract_expiration}")
            print(f"Bar timeframe: {timeframe_display}")
            print(f"Trading hours mode: {'Regular Trading Hours' if use_rth else 'Full Trading Day (23h)'}")
            print(f"Horizontal line strategy: {'Dynamic (Latest Bar Close)' if use_dynamic_reference else 'Fixed (Previous Day Close)'}")
            
            # Request historical data for the previous day's close
            print("Requesting daily bars...")
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime=previous_day_str,
                durationStr='1 D',
                barSizeSetting='1 day',
                whatToShow='MIDPOINT',
                useRTH=use_rth
            )
            
            # Check if we received any data
            if not bars or len(bars) == 0:
                raise Exception(f"No historical data found for previous day with contract expiration {contract_expiration}")
            
            # Convert data to DataFrame
            df = util.df(bars)
            df.set_index('date', inplace=True)
            
            print(f"Previous day data: {df.index[0]}")
            # Get the previous day's close
            previous_close = df['close'].iloc[-1]
            print(f"Previous Day's Close: {previous_close}")
            
            # Request bars for the target day with selected timeframe
            print(f"Requesting {timeframe_display} bars for test date: {backtest_date}...")
            bars_timeframe = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime=target_date_str,
                durationStr='1 D',
                barSizeSetting=ib_bar_size,
                whatToShow='MIDPOINT',
                useRTH=use_rth
            )
            
            # Check if we received any timeframe data
            if not bars_timeframe or len(bars_timeframe) == 0:
                raise Exception(f"No {timeframe_display} bar data available for {backtest_date} with contract {contract_expiration}")
            
            # Convert timeframe data to a DataFrame
            df_timeframe = util.df(bars_timeframe)
            df_timeframe.set_index('date', inplace=True)
            
            # Initialize variables for the portfolio and logic
            initial_balance = 10000000
            balance = initial_balance
            contracts_bought = 0
            direction = None
            first_cross = True
            
            # Initialize reference line
            reference_line = previous_close
            print(f"Initial horizontal line: {reference_line}")
            
            # Simulate the trading logic for the day
            short_contract_prices = []
            long_contract_prices = []
            
            for i, (index, row) in enumerate(df_timeframe.iterrows()):
                print(f"\nAt {index}, the price is {row['close']}")
                print(f"Current horizontal line: {reference_line}")
                
                price = row['close'] * 50
                
                # Logic for first cross
                if first_cross:
                    if row['close'] > reference_line:  # Price crosses upward
                        long_contract_prices.append(price)
                        balance -= price
                        direction = 'long'
                        contracts_bought = 1
                        print(f"Bought 1 long position at {row['close']}")
                        first_cross = False
                    elif row['close'] < reference_line:  # Price crosses downward
                        short_contract_prices.append(price)
                        balance -= price
                        direction = 'short'
                        contracts_bought = 1
                        print(f"Bought 1 short position at {row['close']}")
                        first_cross = False
                
                # Logic for subsequent crosses
                elif direction == 'long' and row['close'] < reference_line:
                    short_contract_prices.append(price)
                    short_contract_prices.append(price)
                    balance -= 2 * price
                    contracts_bought += 2
                    direction = 'short'
                    print(f"Bought 2 more short positions at {row['close']}")
                
                elif direction == 'short' and row['close'] > reference_line:
                    long_contract_prices.append(price)
                    long_contract_prices.append(price)
                    balance -= 2 * price
                    contracts_bought += 2
                    direction = 'long'
                    print(f"Bought 2 more long positions at {row['close']}")
                
                # UPDATE REFERENCE LINE LOGIC
                if use_dynamic_reference:
                    # Update reference line to current bar's close
                    reference_line = row['close']
                    print(f"Horizontal line updated to: {reference_line}")
                else:
                    # Keep reference line at previous day's close
                    print(f"Horizontal line remains at previous day close: {reference_line}")
            
            print(f"Total number of contracts bought this day {contracts_bought}")
            
            # For long positions
            if len(long_contract_prices) > 0:
                total_cost = sum(long_contract_prices)
                current_price = df_timeframe['close'].iloc[-1]
                total_value = len(long_contract_prices) * (current_price * 50)
                profit_loss = total_value - total_cost
                
                print(f"Number of long contracts: {len(long_contract_prices)}")
                print(f"Total cost of long contracts: {total_cost}")
                print(f"Current value at {current_price}: {total_value}")
                print(f"Profit/Loss: {profit_loss}")
                
                # Add back the original cost (to reverse the subtraction when opened)
                # Then add the profit/loss
                balance += total_cost + profit_loss
                print(f"Closed all long positions at {current_price} (Remaining balance: {balance})")

            # For short positions
            if len(short_contract_prices) > 0:
                total_cost = sum(short_contract_prices)
                current_price = df_timeframe['close'].iloc[-1]
                total_value = len(short_contract_prices) * (current_price * 50)
                profit_loss = total_cost - total_value
                
                print(f"Number of short contracts: {len(short_contract_prices)}")
                print(f"Total cost of short contracts: {total_cost}")
                print(f"Current value at {current_price}: {total_value}")
                print(f"Profit/Loss: {profit_loss}")
                
                # Add back the original cost (to reverse the subtraction when opened)
                # Then add the profit/loss
                balance += total_cost + profit_loss
                print(f"Closed all short positions at {current_price} (Remaining balance: {balance})")
            
            # Final Portfolio Value
            final_value = balance
            total_profit_loss = final_value - initial_balance
            
            # Disconnect from IB
            ib.disconnect()
            
            return final_value, contracts_bought, total_profit_loss
            
        except Exception as e:
            # Ensure we disconnect IB in case of errors
            ib.disconnect()
            raise e
    
    def update_results(self, final_value, contracts_bought, total_profit_loss, log_output):
        # Display captured print output
        self.results_text.insert(tk.END, log_output)
        
        # Add a separator
        self.results_text.insert(tk.END, "\n" + "-"*50 + "\n\n")
        
        # Display summary at the end
        self.results_text.insert(tk.END, "SUMMARY:\n")
        self.results_text.insert(tk.END, f"Profit/Loss Value: ${total_profit_loss:.2f}\n")
        self.results_text.insert(tk.END, f"Final Portfolio Value: ${final_value:.2f}\n")
        self.results_text.insert(tk.END, f"Total contracts traded: {contracts_bought}\n")
        
        # Re-enable the button
        self.start_button.config(state="normal")
        
        # Scroll to the end to show the final results
        self.results_text.see(tk.END)

# Create and run the application
if __name__ == "__main__":
    root = tk.Tk()
    app = SimpleTradingApp(root)
    root.mainloop()