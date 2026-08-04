"""V440 Emission Gate backtest: which equilibrium formula predicts price best?

Tests 5 equilibrium variants at multiple historical snapshots (2-6d ago),
correlating distance-to-equilibrium with ACTUAL subsequent price change:

  naive          : eq = 0.5 * (price/sum) / root_prop          (pre-V440 model)
  gated_unorm    : eq = 0.5 * (share*gate(share)) / root_prop  (current impl July 28)
  gated_renorm   : eq = 0.5 * (gated/sum_gated) / root_prop    (chain-exact, spot prices)
  gated_chain    : EMA prices, burn-weighted, gate, renormalize (full chain replication)
  gated_burn_spot: spot prices, burn-weighted, gate, renormalize

Also reports gate-fit vs actual chain buys at each snapshot (liveness detector).

Usage: .venv/bin/python scripts/backtest_v440.py [--days 3]
"""
import bittensor as bt
import json, math, sys, time
from collections import defaultdict

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200
GATE_Q = 0.61
GATE_H = 3

def fetch_snapshot(sub, block):
    snap = sub.at(block=block)
    all_prices = {int(k): float(v) for k, v in snap.prices.alpha_prices().items()}
    out = {'block': block, 'prices': {}, 'ema': {}, 'root_props': {},
           'burns': {}, 'excess': {}, 'enabled': {}}
    for n, p in all_prices.items():
        if n == 0 or p <= 0:
            continue
        out['prices'][n] = p
        try:
            r = snap.query(module.SubnetMovingPrice, params=[n])
            out['ema'][n] = r.get('bits', 0) / (2**32) if isinstance(r, dict) else 0.0
        except Exception:
            out['ema'][n] = 0.0
        try:
            rp = snap.query(module.RootProp, params=[n])
            out['root_props'][n] = rp.get('bits', 0) / (2**32) if isinstance(rp, dict) else 0.0
        except Exception:
            out['root_props'][n] = 0.0
        try:
            b = snap.query(module.MinerBurned, params=[n])
            out['burns'][n] = b.get('bits', 0) / (2**32) if isinstance(b, dict) else 0.0
        except Exception:
            out['burns'][n] = 0.0
        try:
            out['excess'][n] = int(snap.query(module.SubnetExcessTao, params=[n])) / 1e9
        except Exception:
            out['excess'][n] = 0.0
        try:
            out['enabled'][n] = bool(snap.query(module.SubnetEmissionEnabled, params=[n]))
        except Exception:
            out['enabled'][n] = False
    return out

def gate_shares(shares, theta=None):
    """Apply Hill gate; return (gated_raw, gated_norm, theta, bar_rank)."""
    if theta is None:
        sorted_s = sorted(shares.values(), reverse=True)
        cum, theta = 0.0, 0.0
        bar_rank = None
        for i, s in enumerate(sorted_s, 1):
            cum += s
            theta = s
            if cum >= GATE_Q:
                bar_rank = i
                break
    else:
        bar_rank = None
    gated = {}
    for n, s in shares.items():
        if s <= 0:
            gated[n] = 0.0
            continue
        ratio = theta / s
        gate = 1.0 / (1.0 + ratio ** GATE_H)
        gated[n] = s * gate
    total = sum(gated.values())
    gated_norm = {n: g / total for n, g in gated.items()} if total > 0 else gated
    return gated, gated_norm, theta, bar_rank

def equilibrium_variants(snap):
    """Compute eq per subnet for all 5 variants. Returns dict variant -> {netuid: eq}."""
    prices = snap['prices']
    root_props = snap['root_props']
    burns = snap['burns']
    ema = snap['ema']
    enabled = snap['enabled']

    sum_prices = sum(prices.values())
    price_shares = {n: p / sum_prices for n, p in prices.items()}

    # theta from spot price shares (what our impl uses)
    _, _, theta_spot, _ = gate_shares(price_shares)

    # V1 naive
    v_naive = {n: (0.5 * price_shares[n] / root_props[n]) if root_props[n] > 0 else 0
               for n in prices}
    # V2 gated unrenormalized (current impl)
    g_raw, _, _, _ = gate_shares(price_shares, theta=theta_spot)
    v_unorm = {n: (0.5 * g_raw[n] / root_props[n]) if root_props[n] > 0 else 0
               for n in prices}
    # V3 gated renormalized (spot)
    _, g_norm_spot, _, _ = gate_shares(price_shares, theta=theta_spot)
    v_renorm = {n: (0.5 * g_norm_spot[n] / root_props[n]) if root_props[n] > 0 else 0
                for n in prices}
    # V4 chain-exact: EMA + burn-weight + gate + renormalize
    subs = [n for n in prices if ema.get(n, 0) > 0]
    tot_ema = sum(ema[n] for n in subs)
    ema_shares = {n: ema[n] / tot_ema for n in subs} if tot_ema > 0 else price_shares
    weighted = {n: ema_shares[n] * (1 - min(burns.get(n, 0), 1.0)) for n in subs}
    tw = sum(weighted.values())
    burn_shares = {n: weighted[n] / tw for n in subs} if tw > 0 else ema_shares
    _, g_norm_chain, _, _ = gate_shares(burn_shares)
    v_chain = {n: (0.5 * g_norm_chain[n] / root_props[n]) if root_props.get(n, 0) > 0 else 0
               for n in g_norm_chain}
    # V5 burn-weighted spot + gate + renormalize
    w_spot = {n: price_shares[n] * (1 - min(burns.get(n, 0), 1.0)) for n in prices}
    tws = sum(w_spot.values())
    b_spot = {n: w_spot[n] / tws for n in prices} if tws > 0 else price_shares
    _, g_norm_bs, _, _ = gate_shares(b_spot)
    v_burn_spot = {n: (0.5 * g_norm_bs[n] / root_props[n]) if root_props.get(n, 0) > 0 else 0
                   for n in g_norm_bs}

    return {
        'naive': v_naive,
        'gated_unorm': v_unorm,
        'gated_renorm': v_renorm,
        'gated_chain': v_chain,
        'gated_burn_spot': v_burn_spot,
    }, theta_spot

def pearson_log(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(pts) < 5:
        return 0.0, len(pts)
    xs = [math.log(p[0]) for p in pts]
    ys = [math.log(p[1]) for p in pts]
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return (num/(dx*dy), len(pts)) if dx*dy else (0.0, len(pts))

def pearson(a, b):
    pts = [(x, y) for x, y in zip(a, b) if x == x and y == y]  # drop nan
    if len(pts) < 5:
        return 0.0, len(pts)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return (num/(dx*dy), len(pts)) if dx*dy else (0.0, len(pts))

def main():
    days = 3
    if '--days' in sys.argv:
        days = int(sys.argv[sys.argv.index('--days') + 1])

    sub = bt.Subtensor(network='finney')
    cur = sub.block()
    print(f"current block: {cur}")

    windows = [2, 3, 4, 5, 6]
    results = {v: [] for v in ['naive', 'gated_unorm', 'gated_renorm', 'gated_chain', 'gated_burn_spot']}
    fits = []

    for d in windows:
        blk = cur - d * BLOCKS_PER_DAY
        print(f"\n--- {d}d ago (block {blk}) ---")
        snap = fetch_snapshot(sub, blk)
        if len(snap['prices']) < 20:
            print("  too few subnets, skipping")
            continue
        # current prices for price change
        now_prices = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}

        # gate-fit vs actual chain buys (liveness)
        subs = list(snap['prices'].keys())
        _, g_norm_spot, theta, bar_rank = gate_shares(
            {n: snap['prices'][n] / sum(snap['prices'].values()) for n in subs})
        r_n, n1 = pearson_log([snap['excess'].get(n, 0) for n in subs],
                              [snap['prices'][n] / sum(snap['prices'].values()) for n in subs])
        r_g, n2 = pearson_log([snap['excess'].get(n, 0) for n in subs],
                              [g_norm_spot.get(n, 0) for n in subs])
        fits.append((d, r_n, r_g, n2, bar_rank, theta))
        print(f"  gate-fit: naive r={r_n:+.3f} vs gated r={r_g:+.3f} (bar_rank={bar_rank}, theta={theta:.5f})")

        variants, theta_spot = equilibrium_variants(snap)

        # FAIR comparison: same subnet set across all variants
        all_variants = list(variants.keys())
        common = set(variants['naive'].keys())
        for v in all_variants:
            common &= set(variants[v].keys())

        per_var = {}
        for name, eq_map in variants.items():
            dists, changes, nids = [], [], []
            for n in common:
                eq = eq_map.get(n, 0)
                p_then = snap['prices'].get(n, 0)
                p_now = now_prices.get(n, 0)
                if eq > 0 and p_then > 0 and p_now > 0:
                    dists.append((p_then / eq - 1) * 100)
                    changes.append((p_now / p_then - 1) * 100)
                    nids.append(n)
            r, cnt = pearson(dists, changes)
            # quartile delta: top 20% dist (cheapest) vs bottom 20% (most expensive)
            q_delta = None
            if cnt >= 20:
                order = sorted(range(cnt), key=lambda i: dists[i])
                k = max(2, cnt // 5)
                top_idx = order[-k:]
                bot_idx = order[:k]
                top_avg = sum(changes[i] for i in top_idx) / k
                bot_avg = sum(changes[i] for i in bot_idx) / k
                q_delta = top_avg - bot_avg
            results[name].append((d, r, cnt, q_delta))
            print(f"  {name:16s}: r={r:+.3f} (n={cnt}) q_delta={q_delta:+.2f}%" if q_delta is not None
                  else f"  {name:16s}: r={r:+.3f} (n={cnt})")
        print(f"  common subnet set: {len(common)}")

    print(f"\n{'='*70}")
    print("SUMMARY: correlation of equilibrium-distance with price change (common set)")
    print(f"{'variant':16s} " + " ".join(f"{d}d r={r:+.2f} (q={qd:+.1f})" for d, r, n, qd in results['naive']))
    for name in results:
        row = " ".join(f"{r:+.2f}" for d, r, n, qd in results[name])
        avg = sum(r for d, r, n, qd in results[name]) / max(1, len(results[name]))
        print(f"{name:16s} {row}   AVG={avg:+.2f}")

if __name__ == '__main__':
    main()
