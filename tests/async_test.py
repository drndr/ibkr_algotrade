from ib_insync import IB, Future, util
from datetime import datetime, timedelta

ib = IB()
ib.connect('127.0.0.1', 7497, clientId=6)  # Sync connection

contract = Future('ES', '202412', 'CME')
yesterday = datetime.now() - timedelta(days=1)
while yesterday.weekday() >= 5:
    yesterday = yesterday - timedelta(days=1)
yesterday_str = yesterday.strftime("%Y%m%d %H:%M:%S")

bars = ib.reqHistoricalData(  # Sync version (no await)
    contract,
    endDateTime=yesterday_str,
    durationStr='1 D',
    barSizeSetting='1 day',
    whatToShow='MIDPOINT',
    useRTH=True
)

print(f"Sync connection got {len(bars) if bars else 0} bars")
if bars:
    print(f"Previous close: {bars[-1].close}")

ib.disconnect()