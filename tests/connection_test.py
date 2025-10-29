#!/usr/bin/env python3

from ib_insync import *
import time

def get_account_funds():
    # Connect to TWS paper trading (default port 7497)
    ib = IB()
    
    try:
        print("Connecting to TWS...")
        ib.connect('127.0.0.1', 7497, clientId=1)
        
        # Wait a moment for connection to stabilize
        time.sleep(2)
        
        if not ib.isConnected():
            print("Failed to connect to TWS")
            return
            
        print("Connected successfully!")
        
        # Get account summary for funds
        account_summary = ib.accountSummary()
        
        # Print relevant fund information
        print("\n=== Account Funds ===")
        for item in account_summary:
            if item.tag in ['TotalCashValue', 'NetLiquidation', 'BuyingPower', 'AvailableFunds']:
                print(f"{item.tag}: {item.value} {item.currency}")
        
    except Exception as e:
        print(f"Error: {e}")
        print("\nTroubleshooting tips:")
        print("1. Make sure TWS is running")
        print("2. Enable API in TWS (File → Global Configuration → API → Settings)")
        print("3. Check if port 7497 is correct for paper trading")
        print("4. Verify 'localhost' is in trusted IP addresses")
    finally:
        if ib.isConnected():
            ib.disconnect()

if __name__ == "__main__":
    get_account_funds()