"""Daily flow scanner: user-driven TAO flow vs pool size for all subnets.

Metric (M3, July 22 2026 backtest): pool TAO delta over 7d MINUS protocol
chain-buy contribution, as % of pool. This isolates TAO entering/leaving
the AMM from actual user buys/sells — protocol injection (chain buys,
emission liquidity) is excluded.

Backtest (backtest_flow_pool.py, n=128, non-overlapping windows):
  M1 neuron-stake flow (old):   r=+0.010, quintile delta -2.3%  -> BROKEN
      (measured consensus weight inflation, not buying; emission accrual
      masqueraded as inflow — SN28/66 showed +400-600% "flow" in a dump)
  M2 raw pool delta:            r=-0.180  -> contaminated by protocol injection
  M3 pool delta minus chain buys: r=+0.090, quintile delta +9.3% -> SHIPPED
      Value is at the bottom: user-driven outflow = -8.4% avg fwd return.
      It's an avoidance signal, not an entry signal.

Runs once daily (overnight). ~3 storage queries per subnet at 2 block
heights — much faster than the old 256-neuron scan.
Caches results for the ranking and dashboard.
"""
import bittensor as bt
import json
from datetime import datetime, timezone

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200

def query_pool_excess(snap, netuid):
    pool = excess = 0
    try:
        r = snap.query(module.SubnetTAO, params=[netuid])
        pool = int(r) / 1e9 if r else 0
    except Exception:
        pass
    try:
        r = snap.query(module.SubnetExcessTao, params=[netuid])
        excess = int(r) / 1e9 if r else 0
    except Exception:
        pass
    return pool, excess

def scan_flow():
    sub = bt.Subtensor(network='finney')
    current_block = sub.block()
    block_7d = current_block - (7 * BLOCKS_PER_DAY)

    print(f"Block: {current_block}, 7d ago: {block_7d}")

    snap_7d = sub.at(block=block_7d)
    prices_now = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}
    target = [int(k) for k in prices_now if int(k) != 0]

    results = []
    for i, netuid in enumerate(target):
        if i % 20 == 0:
            print(f"  Processing {i+1}/{len(target)}...")
        pool_now, excess_now = query_pool_excess(sub, netuid)
        pool_7d, _ = query_pool_excess(snap_7d, netuid)
        if pool_now <= 0:
            continue

        pool_delta = pool_now - pool_7d
        # Protocol contribution over the week (current per-block excess * blocks)
        protocol_buy = excess_now * 7 * BLOCKS_PER_DAY
        user_flow = pool_delta - protocol_buy

        flow_vs_pool = (user_flow / pool_now * 100) if pool_now > 0 else 0
        # Keep legacy fields for compatibility; stake fields now carry pool data
        results.append({
            'netuid': netuid,
            'stake_7d_ago': round(pool_7d, 2),      # now: pool TAO 7d ago
            'stake_now': round(pool_now, 2),        # now: pool TAO now
            'net_flow': round(user_flow, 2),        # user-driven TAO flow
            'pool_size': round(pool_now, 0),
            'pool_delta': round(pool_delta, 2),     # raw pool change (incl protocol)
            'protocol_buy': round(protocol_buy, 2), # est. chain-buy contribution
            'flow_pct': round(flow_vs_pool, 1),     # user flow as % of pool
            'flow_vs_pool': round(flow_vs_pool, 1),
        })

    data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': current_block,
        'metric': 'pool_delta_minus_chain_buys (M3, user-driven flow)',
        'results': results,
    }

    with open('data/flow_cache.json', 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nDone. {len(results)} subnets scanned.")
    print(f"Saved to data/flow_cache.json")

    sorted_results = sorted(results, key=lambda x: x['flow_vs_pool'], reverse=True)
    print(f"\nTop 10 (user inflow):")
    for r in sorted_results[:10]:
        print(f"  SN{r['netuid']:3d}: {r['flow_vs_pool']:>+6.1f}% (user flow: {r['net_flow']:>+10.0f} TAO)")
    print(f"\nBottom 10 (user outflow):")
    for r in sorted_results[-10:]:
        print(f"  SN{r['netuid']:3d}: {r['flow_vs_pool']:>+6.1f}% (user flow: {r['net_flow']:>+10.0f} TAO)")

if __name__ == '__main__':
    scan_flow()
