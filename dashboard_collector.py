"""Dashboard data collector — runs every 5 minutes, updates dashboard-data.json.

Queries all subnets, collects prices, chain buys, pool state, emission status,
and writes to docs/dashboard-data.json for the dashboard to consume.
"""
import bittensor as bt
import json, os, time
from datetime import datetime, timezone

# ── V440 Emission Gate (July 2026) ─────────────────────────────────────────
# Import gate logic from ranking.py to keep a single source of truth.
# Falls back to inline implementation if import fails (e.g. path issues).
GATE_Q = 0.61
GATE_H = 3

def compute_emission_gate(prices_excl_root):
    """Compute V440 emission gate parameters from live prices.

    Args:
        prices_excl_root: dict {netuid: price} excluding SN0 root.
    Returns:
        dict with: theta, bar_rank, and per-netuid gate data.
    """
    sorted_items = sorted(
        ((netuid, price) for netuid, price in prices_excl_root.items() if price > 0),
        key=lambda x: x[1],
        reverse=True,
    )
    if not sorted_items:
        return {'theta': 0, 'bar_rank': 0, 'subnets': {}}

    total = sum(p for _, p in sorted_items)
    shares = [(netuid, price / total) for netuid, price in sorted_items]

    cumulative = 0.0
    bar_rank = len(shares)
    theta = 0.0
    for rank, (netuid, share) in enumerate(shares, start=1):
        cumulative += share
        if cumulative >= GATE_Q:
            bar_rank = rank
            theta = share
            break

    if theta == 0 and shares:
        theta = shares[-1][1]

    theta_h = theta ** GATE_H

    subnets = {}
    for rank, (netuid, share) in enumerate(shares, start=1):
        s_h = share ** GATE_H
        gate_value = s_h / (s_h + theta_h) if theta_h > 0 else 1.0
        gated_share = share * gate_value
        subnets[netuid] = {
            'gate_value': gate_value,
            'above_bar': rank <= bar_rank,
            'emission_rank': rank,
            'share': share,
            'gated_share': gated_share,
            'old_share_pct': share * 100,
            'new_share_pct': gated_share * 100,
        }

    return {'theta': theta, 'bar_rank': bar_rank, 'subnets': subnets}

def collect():
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    module = bt.storage.SubtensorModule

    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()

    # ── V440 Emission Gate ───────────────────────────────────────────────
    # Compute gate parameters from live prices (excluding SN0 root).
    prices_excl_root = {int(k): float(v) for k, v in all_prices.items() if int(k) != 0 and float(v) > 0}
    gate_data = compute_emission_gate(prices_excl_root)
    gate_subnets = gate_data['subnets']
    gate_theta = gate_data['theta']
    gate_bar_rank = gate_data['bar_rank']

    data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': block,
        'subnets': [],
        # V440 gate globals (for dashboard display)
        'gate_theta': round(gate_theta, 6),
        'gate_bar_rank': gate_bar_rank,
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

            # Equilibrium price: tao_emission / root_prop
            # (where chain buy stops)
            # V440: emission share now passes through a Hill gate function.
            sum_prices_approx = sum(float(v) for v in all_prices.values() if v > 0)
            gs = gate_subnets.get(netuid, {})
            gate_value = gs.get('gate_value', 1.0)
            gated_share = gs.get('gated_share', spot_price / sum_prices_approx if sum_prices_approx > 0 else 0)
            tao_emission = 0.5 * gated_share
            equilibrium = tao_emission / root_prop if root_prop > 0 else 0
            distance_pct = ((spot_price / equilibrium) - 1) * 100 if equilibrium > 0 else 0

            # Load GitHub activity if available
            commits_30d = 0
            commits_7d = 0
            try:
                import os as _os
                if _os.path.exists('data/github_activity.json'):
                    with open('data/github_activity.json') as f:
                        gh = json.load(f)
                    for g in gh:
                        if g.get('netuid') == netuid:
                            commits_30d = g.get('commits_30d', 0) or 0
                            commits_7d = g.get('commits_7d', 0) or 0
                            break
            except:
                pass

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
                'equilibrium': equilibrium,
                'distance_pct': distance_pct,
                # V440 Emission Gate fields
                'gate_value': round(gs.get('gate_value', 1.0), 4),
                'above_bar': gs.get('above_bar', True),
                'emission_rank': gs.get('emission_rank', 0),
                'gated_share_pct': round(gs.get('new_share_pct', 0), 2),
                'old_share_pct': round(gs.get('old_share_pct', 0), 2),
                'gate_theta': round(gate_theta, 6),
                'gate_bar_rank': gate_bar_rank,
                'commits_30d': commits_30d,
                'commits_7d': commits_7d,
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
