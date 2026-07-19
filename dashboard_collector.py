"""Dashboard data collector — runs every 5 minutes, updates dashboard-data.json.

Queries all subnets, collects prices, chain buys, pool state, emission status,
and writes to docs/dashboard-data.json for the dashboard to consume.
"""
import bittensor as bt
import json, os, time
from datetime import datetime, timezone

def collect():
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    module = bt.storage.SubtensorModule

    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()

    data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': block,
        'subnets': [],
    }

    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        try:
            name = names.get(netuid_str, f"SN{netuid}")
            spot_price = float(price)
            emission_enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
            excess_tao = int(sub.query(module.SubnetExcessTao, params=[netuid])) / 1e9
            daily_cb = excess_tao * 7200
            tao_pool = int(sub.query(module.SubnetTAO, params=[netuid])) / 1e9
            alpha_pool = int(sub.query(module.SubnetAlphaIn, params=[netuid])) / 1e9
            rp_raw = sub.query(module.RootProp, params=[netuid])
            rp_bits = rp_raw.get('bits', 0) if isinstance(rp_raw, dict) else int(rp_raw)
            root_prop = rp_bits / (2**32)

            try:
                identity = sub.subnets.subnet_identity(netuid=netuid)
                github = identity.get('github_repo', '') if isinstance(identity, dict) else ''
                description = identity.get('description', '') if isinstance(identity, dict) else ''
            except:
                github = ''
                description = ''

            cb_vs_pool = (daily_cb / tao_pool * 100) if tao_pool > 0 else 0

            data['subnets'].append({
                'netuid': netuid,
                'name': name,
                'price': spot_price,
                'emission_enabled': emission_enabled,
                'excess_tao': excess_tao,
                'daily_cb': daily_cb,
                'tao_pool': tao_pool,
                'alpha_pool': alpha_pool,
                'root_prop': root_prop,
                'cb_vs_pool': cb_vs_pool,
                'github': github,
                'description': description,
            })
        except:
            pass

    data['subnets'].sort(key=lambda x: x['price'], reverse=True)

    os.makedirs('docs', exist_ok=True)
    with open('docs/dashboard-data.json', 'w') as f:
        json.dump(data, f, indent=2)

    enabled = sum(1 for s in data['subnets'] if s['emission_enabled'])
    with_cb = sum(1 for s in data['subnets'] if s['excess_tao'] > 0)
    print(f"[{data['timestamp'][:19]}] Block {block}: {len(data['subnets'])} subnets, {enabled} on, {with_cb} with chain buys")

if __name__ == '__main__':
    import sys
    if '--loop' in sys.argv:
        interval = 300  # 5 min
        while True:
            try:
                collect()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(interval)
    else:
        collect()
