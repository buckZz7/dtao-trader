"""Fetch historical alpha prices by querying past blocks.

The chain stores all state. We can reconstruct price history by
querying alpha_prices at regular block intervals.

Block time: 12 seconds on mainnet.
1 hour = 300 blocks
1 day = 7,200 blocks
1 week = 50,400 blocks

Usage:
  python historical_fetcher.py --days 7     # Fetch last 7 days
  python historical_fetcher.py --blocks 7200 # Fetch last 7200 blocks
  python historical_fetcher.py --netuid 1 --days 30  # Single subnet, 30 days
"""
import bittensor as bt
import json, time, os, argparse
from datetime import datetime, timezone

BLOCK_TIME = 12  # seconds per block on mainnet

def fetch_historical_prices(sub, start_block, end_block, step_blocks=300, netuids=None):
    """Fetch alpha prices at regular block intervals.
    
    Args:
        sub: Subtensor client
        start_block: First block to query
        end_block: Last block to query
        step_blocks: Blocks between samples (300 = 1 hour)
        netuids: Optional list of netuids to track (None = all)
    
    Returns:
        List of {block, timestamp, prices} snapshots
    """
    snapshots = []
    current_block = start_block
    
    while current_block <= end_block:
        try:
            # Get snapshot at this block
            snapshot = sub.at(block=current_block)
            prices = snapshot.prices.alpha_prices()
            
            # Filter to specific netuids if specified
            if netuids:
                prices = {k: v for k, v in prices.items() if int(k) in netuids}
            
            data = {
                'block': current_block,
                'prices': {str(k): float(v) for k, v in prices.items()},
            }
            snapshots.append(data)
            
            n = len(data['prices'])
            print(f"  Block {current_block} ({n} subnets)")
            
        except Exception as e:
            print(f"  Block {current_block} Error: {str(e)[:60]}")
        
        current_block += step_blocks
        time.sleep(0.1)  # Rate limit
    
    return snapshots

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=float, help='Days of history to fetch')
    parser.add_argument('--blocks', type=int, help='Number of blocks to fetch')
    parser.add_argument('--step', type=int, default=300, help='Blocks between samples (default 300 = 1 hour)')
    parser.add_argument('--netuid', type=int, nargs='*', help='Specific netuids to track')
    parser.add_argument('--output', default='data/historical_prices.jsonl', help='Output file')
    args = parser.parse_args()
    
    sub = bt.Subtensor(network='finney')
    current_block = sub.block()
    print(f"Connected: {sub.network}, current block: {current_block}")
    
    # Calculate start block
    if args.blocks:
        start_block = current_block - args.blocks
    elif args.days:
        blocks_back = int(args.days * 24 * 3600 / BLOCK_TIME)
        start_block = current_block - blocks_back
    else:
        start_block = current_block - 7200  # Default: 1 day
    
    step = args.step
    n_samples = (current_block - start_block) // step
    
    print(f"Fetching {n_samples} samples from block {start_block} to {current_block}")
    print(f"Step: {step} blocks ({step * BLOCK_TIME / 60:.0f} min apart)")
    print(f"Netuids: {args.netuid or 'all'}")
    print()
    
    snapshots = fetch_historical_prices(
        sub, start_block, current_block, step_blocks=step, netuids=args.netuid
    )
    
    # Save
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w') as f:
        for snap in snapshots:
            f.write(json.dumps(snap) + '\n')
    
    print(f"\nSaved {len(snapshots)} snapshots to {args.output}")
    
    # Quick summary
    if snapshots and args.netuid:
        netuid = str(args.netuid[0])
        prices = [s['prices'].get(netuid, 0) for s in snapshots if netuid in s['prices']]
        if prices:
            print(f"\nSN{args.netuid[0]} price summary:")
            print(f"  Start: {prices[0]:.6f}")
            print(f"  End:   {prices[-1]:.6f}")
            print(f"  Min:   {min(prices):.6f}")
            print(f"  Max:   {max(prices):.6f}")
            print(f"  Change: {(prices[-1] / prices[0] - 1) * 100:+.1f}%" if prices[0] > 0 else "  Change: N/A")

if __name__ == '__main__':
    main()
