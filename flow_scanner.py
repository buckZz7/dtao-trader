"""Daily flow scanner: computes 7-day net stake flow vs pool size for all subnets.

Runs once daily (overnight). Queries neurons at 7d ago and now, computes
net flow as % of pool. Caches results for the ranking and dashboard.

Flow vs Pool % = (stake_now - stake_7d_ago) / pool_size * 100
"""
import bittensor as bt
import json, os
from datetime import datetime, timezone

module = bt.storage.SubtensorModule

def scan_flow():
    sub = bt.Subtensor(network='finney')
    current_block = sub.block()
    
    block_7d = current_block - (7 * 7200)
    
    print(f"Block: {current_block}")
    print(f"Fetching neurons at block {block_7d} (7d ago)...")
    
    snap_7d = sub.at(block=block_7d)
    prices_now = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}
    
    target = [int(k) for k in prices_now if int(k) != 0]
    
    results = []
    for i, netuid in enumerate(target):
        if i % 20 == 0:
            print(f"  Processing {i+1}/{len(target)}...")
        
        try:
            # Stake 7d ago
            neurons_7d = snap_7d.neurons.neurons(netuid=netuid)
            stake_7d = sum((n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0) / 1e9 for n in neurons_7d)
            
            # Stake now
            neurons_now = sub.neurons.neurons(netuid=netuid)
            stake_now = sum((n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0) / 1e9 for n in neurons_now)
            
            # Pool size now
            pool = int(sub.query(module.SubnetTAO, params=[netuid])) / 1e9
            
            # Flow
            net_flow = stake_now - stake_7d
            flow_pct = (net_flow / stake_7d * 100) if stake_7d > 0 else 0
            flow_vs_pool = (net_flow / pool * 100) if pool > 0 else 0
            
            results.append({
                'netuid': netuid,
                'stake_7d_ago': round(stake_7d, 2),
                'stake_now': round(stake_now, 2),
                'net_flow': round(net_flow, 2),
                'pool_size': round(pool, 0),
                'flow_pct': round(flow_pct, 1),
                'flow_vs_pool': round(flow_vs_pool, 1),
            })
        except:
            pass
    
    # Save cache
    data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': current_block,
        'results': results,
    }
    
    with open('data/flow_cache.json', 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"\nDone. {len(results)} subnets scanned.")
    print(f"Saved to data/flow_cache.json")
    
    # Print top/bottom 10
    sorted_results = sorted(results, key=lambda x: x['flow_vs_pool'], reverse=True)
    print(f"\nTop 10 (net inflow):")
    for r in sorted_results[:10]:
        print(f"  SN{r['netuid']:3d}: {r['flow_vs_pool']:>+6.1f}% (flow: {r['net_flow']:>+10.0f} TAO)")
    
    print(f"\nBottom 10 (net outflow):")
    for r in sorted_results[-10:]:
        print(f"  SN{r['netuid']:3d}: {r['flow_vs_pool']:>+6.1f}% (flow: {r['net_flow']:>+10.0f} TAO)")
    
    return results

if __name__ == '__main__':
    scan_flow()
