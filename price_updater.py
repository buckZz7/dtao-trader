"""Lightweight price updater: fetches prices + equilibrium data and pushes to GitHub.

Runs every 5 minutes via cron. Only fetches:
- All alpha prices (1 API call)
- Root prop per subnet (128 queries, ~2s)
- Sum of prices for equilibrium calculation

Does NOT recompute scores, health, flow, or conviction.
The full ranking (hourly) reads this file for fresh prices.

Output: docs/prices-live.json
"""
import bittensor as bt
import json, os, subprocess
from datetime import datetime, timezone

module = bt.storage.SubtensorModule

def update_prices():
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    sum_prices = sum(float(v) for v in all_prices.values() if v > 0)
    
    data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': block,
        'sum_prices': sum_prices,
        'subnets': {}
    }
    
    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        
        spot = float(price)
        name = str(names.get(netuid_str, f'SN{netuid}'))
        
        # Root prop (needed for equilibrium)
        try:
            rp_raw = sub.query(module.RootProp, params=[netuid])
            rp_bits = rp_raw.get('bits', 0) if isinstance(rp_raw, dict) else int(rp_raw)
            root_prop = rp_bits / (2**32)
        except:
            root_prop = 0
        
        # Equilibrium
        emission_rate = spot / sum_prices if sum_prices > 0 else 0
        tao_emission = 0.5 * emission_rate
        equilibrium = tao_emission / root_prop if root_prop > 0 else 0
        distance_pct = ((spot / equilibrium) - 1) * 100 if equilibrium > 0 else 0
        
        # Emission status
        try:
            emission_enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
        except:
            emission_enabled = False
        
        # Chain buy data
        try:
            excess_tao = int(sub.query(module.SubnetExcessTao, params=[netuid])) / 1e9
            tao_pool = int(sub.query(module.SubnetTAO, params=[netuid])) / 1e9
            daily_cb = excess_tao * 7200
            cb_vs_pool = (daily_cb / tao_pool * 100) if tao_pool > 0 else 0
        except:
            daily_cb = 0
            cb_vs_pool = 0
            tao_pool = 0
        
        data['subnets'][str(netuid)] = {
            'name': name,
            'price': spot,
            'equilibrium': equilibrium,
            'distance_pct': round(distance_pct, 1),
            'emission_enabled': emission_enabled,
            'cb_vs_pool': round(cb_vs_pool, 2),
            'tao_pool': round(tao_pool, 0),
        }
    
    # Save
    with open('docs/prices-live.json', 'w') as f:
        json.dump(data, f, separators=(',', ':'))  # compact, no indent (smaller file)
    
    print(f"Block {block} | {len(data['subnets'])} subnets | {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
    
    # Git push
    try:
        subprocess.run(['git', 'add', 'docs/prices-live.json'], check=True, cwd=os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(['git', 'diff', '--staged', '--quiet'], cwd=os.path.dirname(os.path.abspath(__file__)))
        if result.returncode != 0:
            subprocess.run(['git', 'commit', '-m', 'Update live prices [skip ci]'], check=True, cwd=os.path.dirname(os.path.abspath(__file__)))
            subprocess.run(['git', 'push', 'origin', 'main'], check=True, cwd=os.path.dirname(os.path.abspath(__file__)))
            print("Pushed to GitHub")
        else:
            print("No changes")
    except Exception as e:
        print(f"Git error: {e}")

if __name__ == '__main__':
    update_prices()
