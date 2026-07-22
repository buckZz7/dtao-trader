"""Backtest: does protocol chain-buy pressure predict forward returns
regardless of price vs equilibrium?

Context: chain code (run_coinbase.rs) shows excess TAO is swapped for alpha
EVERY block with no equilibrium gate — protocol buys at any price.
Our EqVel model historically treated prot_vel as bullish only below eq.

Test:
1. Snapshot chain state N days ago (multiple windows: 7, 14, 30).
2. For each subnet: cb_vs_pool, distance_pct, burn.
3. Forward return to today.
4. Correlations:
   - cb_vs_pool vs fwd return (all subnets)
   - split: below eq vs above eq
   - does prot_vel (cb*2*amp, burn-adj) add beyond raw cb?
5. Verdict: is chain buy pressure unconditionally bullish, conditionally
   bullish, or noise?
"""
import bittensor as bt
import json, math

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200

def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 1.0
    mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    vx = sum((x-mx)**2 for x in xs)
    vy = sum((y-my)**2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0, 1.0
    r = cov / math.sqrt(vx * vy)
    # t-stat p-value approx (two-tailed, normal approx for n>10)
    if abs(r) >= 1:
        return r, 0.0
    t = r * math.sqrt((n-2) / (1 - r*r))
    # rough p via normal approximation
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return r, p

def ranks(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    rk = [0.0] * len(vals)
    for pos, i in enumerate(order):
        rk[i] = pos
    return rk

def spearman(xs, ys):
    r, _ = pearson(ranks(xs), ranks(ys))
    return r

def snapshot_at(sub, block):
    snap = sub.at(block=block)
    data = {}
    prices = snap.prices.alpha_prices()
    sum_prices = sum(float(v) for v in prices.values())
    for ns, price in prices.items():
        n = int(ns)
        if n == 0:
            continue
        d = {'price': float(price)}
        try:
            r = snap.query(module.SubnetExcessTao, params=[n])
            d['excess'] = int(r) / 1e9 if r else 0
        except Exception:
            d['excess'] = 0
        try:
            r = snap.query(module.SubnetTAO, params=[n])
            d['pool'] = int(r) / 1e9 if r else 0
        except Exception:
            d['pool'] = 0
        try:
            rp = snap.query(module.RootProp, params=[n])
            d['root_prop'] = rp.get('bits', 0) / (2**32) if isinstance(rp, dict) else 0
        except Exception:
            d['root_prop'] = 0
        try:
            b = snap.query(module.MinerBurned, params=[n])
            d['burn'] = b.get('bits', 0) / (2**32) if isinstance(b, dict) else 0
        except Exception:
            d['burn'] = 0
        try:
            d['enabled'] = bool(snap.query(module.SubnetEmissionEnabled, params=[n]))
        except Exception:
            d['enabled'] = False
        # equilibrium (naive formula, matches ranking.py)
        if sum_prices > 0 and d['root_prop'] > 0:
            tao_emission = 0.5 * d['price'] / sum_prices
            d['equilibrium'] = tao_emission / d['root_prop']
            d['distance_pct'] = (d['price'] / d['equilibrium'] - 1) * 100 if d['equilibrium'] > 0 else 0
        else:
            d['equilibrium'] = 0
            d['distance_pct'] = 0
        d['cb_vs_pool'] = (d['excess'] * BLOCKS_PER_DAY / d['pool'] * 100) if d['pool'] > 0 else 0
        data[n] = d
    return data

def corr(x, y, label, min_n=8):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if len(pairs) < min_n:
        print(f"  {label:55s} n={len(pairs):3d}  (insufficient)")
        return None
    xs, ys = zip(*pairs)
    r, p = pearson(list(xs), list(ys))
    sr = spearman(list(xs), list(ys))
    sig = '***' if p < 0.01 else '**' if p < 0.05 else '*' if p < 0.1 else '  '
    print(f"  {label:55s} n={len(pairs):3d}  pearson r={r:+.3f} {sig}  spearman={sr:+.3f}")
    return r

def run_window(sub, days, current_prices):
    current_block = sub.block()
    target = current_block - days * BLOCKS_PER_DAY
    print(f"\n{'='*90}")
    print(f"WINDOW: {days} days (block {target} -> {current_block})")
    print(f"{'='*90}")
    hist = snapshot_at(sub, target)

    rows = []
    for n, d in hist.items():
        if not d['enabled'] or d['price'] <= 0:
            continue
        now = current_prices.get(n, 0)
        if now <= 0:
            continue
        fwd = (now / d['price'] - 1) * 100
        rows.append({**d, 'netuid': n, 'fwd': fwd})

    print(f"subnets: {len(rows)}")
    cb = [r['cb_vs_pool'] for r in rows]
    fwd = [r['fwd'] for r in rows]
    below = [r for r in rows if r['distance_pct'] < 0]
    above = [r for r in rows if r['distance_pct'] >= 0]

    print("\n-- ALL SUBNETS --")
    corr(cb, fwd, "cb_vs_pool vs fwd return (ALL)")
    # burn-adjusted prot vel (no amp — amp needs locked data, skip)
    prot = [r['cb_vs_pool'] * 2 * max(0.01, 1 - r['burn']) for r in rows]
    corr(prot, fwd, "prot_vel (cb*2, burn-adj) vs fwd (ALL)")
    dist = [r['distance_pct'] for r in rows]
    corr(dist, fwd, "distance_pct vs fwd (ALL)  [reference]")

    print(f"\n-- BELOW EQUILIBRIUM (n={len(below)}) --")
    corr([r['cb_vs_pool'] for r in below], [r['fwd'] for r in below], "cb_vs_pool vs fwd (BELOW eq)")

    print(f"\n-- ABOVE EQUILIBRIUM (n={len(above)}) --")
    corr([r['cb_vs_pool'] for r in above], [r['fwd'] for r in above], "cb_vs_pool vs fwd (ABOVE eq)")

    # Quadrant analysis: the money question — does high CB help ABOVE eq?
    if len(above) >= 8:
        above_sorted = sorted(above, key=lambda r: r['cb_vs_pool'], reverse=True)
        half = max(3, len(above_sorted) // 2)
        hi = above_sorted[:half]
        lo = above_sorted[-half:]
        hi_avg = sum(r['fwd'] for r in hi) / len(hi)
        lo_avg = sum(r['fwd'] for r in lo) / len(lo)
        print(f"\n  ABOVE eq, high-CB half avg fwd: {hi_avg:+.2f}%  vs low-CB half: {lo_avg:+.2f}%  delta={hi_avg-lo_avg:+.2f}%")
    if len(below) >= 8:
        below_sorted = sorted(below, key=lambda r: r['cb_vs_pool'], reverse=True)
        half = max(3, len(below_sorted) // 2)
        hi = below_sorted[:half]
        lo = below_sorted[-half:]
        hi_avg = sum(r['fwd'] for r in hi) / len(hi)
        lo_avg = sum(r['fwd'] for r in lo) / len(lo)
        print(f"  BELOW eq, high-CB half avg fwd: {hi_avg:+.2f}%  vs low-CB half: {lo_avg:+.2f}%  delta={hi_avg-lo_avg:+.2f}%")

    return rows

def main():
    sub = bt.Subtensor(network='finney')
    current_prices = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}
    for days in (7, 14, 30):
        try:
            run_window(sub, days, current_prices)
        except Exception as e:
            print(f"window {days}d FAILED: {e}")

if __name__ == '__main__':
    main()
