"""Price logger: records all subnet alpha prices every 5 minutes.

Stores to data/prices.jsonl — one line per snapshot.
Used for backtesting trading strategies.

Usage:
  python price_logger.py              # Single snapshot
  python price_logger.py --loop       # Continuous (every 5 min)
"""
import bittensor as bt
import json, time, os, argparse
from datetime import datetime, timezone

def take_snapshot(sub):
    """Take a price snapshot of all subnets."""
    block = sub.block()
    prices = sub.prices.alpha_prices()
    
    snapshot = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': block,
        'prices': {str(k): float(v) for k, v in prices.items()},
    }
    return snapshot

def save_snapshot(snapshot, filepath='data/prices.jsonl'):
    """Append snapshot to JSONL file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'a') as f:
        f.write(json.dumps(snapshot) + '\n')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--loop', action='store_true', help='Continuous mode')
    parser.add_argument('--interval', type=int, default=300, help='Seconds between snapshots (default 300)')
    args = parser.parse_args()
    
    sub = bt.Subtensor(network='finney')
    print(f"Connected: {sub.network}")
    
    while True:
        try:
            snapshot = take_snapshot(sub)
            save_snapshot(snapshot)
            n = len(snapshot['prices'])
            print(f"[{snapshot['timestamp']}] Block {snapshot['block']}: {n} subnets logged")
        except Exception as e:
            print(f"Error: {e}")
        
        if not args.loop:
            break
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
