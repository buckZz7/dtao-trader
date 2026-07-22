"""Composite weight review: which chain-state signals deserve weight?

Uses fully historical data (no current-snapshot leakage) to compare:
  A. distance_pct alone (valuation)
  B. current-style valuation score (piecewise, 35 max)
  C. valuation + CB-above-eq penalty (new finding)
  D. valuation + flow proxy (pool change as flow proxy where available)
  E. inverse-CB signal standalone

Reports per window: pearson r vs forward return, top/bottom quintile delta.
Windows: 7, 14, 30 days (matches prior backtests for comparability).
"""
import bittensor as bt
import math

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200

def pearson(xs, ys):
    n = len(xs)
    if n < 3: return 0.0
    mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    vx = sum((x-mx)**2 for x in xs); vy = sum((y-my)**2 for y in ys)
    if vx <= 0 or vy <= 0: return 0.0
    return cov / math.sqrt(vx*vy)

def snapshot_at(sub, block):
    snap = sub.at(block=block)
    prices = snap.prices.alpha_prices()
    sum_prices = sum(float(v) for v in prices.values())
    data = {}
    for ns, price in prices.items():
        n = int(ns)
        if n == 0: continue
        d = {'price': float(price)}
        def q(m, default=0):
            try:
                r = snap.query(m, params=[n])
                if isinstance(r, dict): return r.get('bits', 0) / (2**32)
                return int(r) / 1e9 if r else default
            except Exception: return default
        d['excess'] = q(module.SubnetExcessTao)
        d['pool'] = q(module.SubnetTAO)
        d['root_prop'] = q(module.RootProp)
        d['burn'] = q(module.MinerBurned)
        try: d['enabled'] = bool(snap.query(module.SubnetEmissionEnabled, params=[n]))
        except Exception: d['enabled'] = False
        if sum_prices > 0 and d['root_prop'] > 0:
            eq = (0.5 * d['price'] / sum_prices) / d['root_prop']
            d['distance_pct'] = (d['price'] / eq - 1) * 100 if eq > 0 else 0
        else:
            d['distance_pct'] = 0
        d['cb_vs_pool'] = (d['excess'] * BLOCKS_PER_DAY / d['pool'] * 100) if d['pool'] > 0 else 0
        data[n] = d
    return data

def norm(v, lo, hi):
    if hi == lo: return 0.5
    return max(0, min(1, (v - lo) / (hi - lo)))

def scores(d):
    """Compute candidate scores from a historical snapshot row."""
    dist = d['distance_pct']
    # A: raw distance (inverted: more negative = better)
    a = -dist
    # B: current valuation piecewise (0-35)
    if dist <= 0:
        b = 17.5 + norm(-dist, 0, 35) * 17.5
    else:
        b = 17.5 - norm(dist, 0, 100) * 17.5
    b = max(0, min(35, b))
    # C: B + CB-above-eq penalty (high CB above eq = bearish per backtest)
    cb_pen = 0
    if dist > 0 and d['cb_vs_pool'] > 0.5:
        cb_pen = min(15, d['cb_vs_pool'] * 3)  # cap penalty at 15
    c = max(0, b - cb_pen)
    # E: pure inverse CB
    e = -d['cb_vs_pool']
    return {'A_dist': a, 'B_val': b, 'C_val_cbpen': c, 'E_invcb': e}

def quintile_delta(rows, key):
    srt = sorted(rows, key=lambda r: r[key], reverse=True)
    n = max(3, len(srt) // 5)
    top = sum(r['fwd'] for r in srt[:n]) / n
    bot = sum(r['fwd'] for r in srt[-n:]) / n
    return top, bot, top - bot

def run(sub, days, current_prices):
    blk = sub.block() - days * BLOCKS_PER_DAY
    hist = snapshot_at(sub, blk)
    rows = []
    for n, d in hist.items():
        if not d['enabled'] or d['price'] <= 0: continue
        now = current_prices.get(n, 0)
        if now <= 0: continue
        row = dict(d)
        row.update(scores(d))
        row['fwd'] = (now / d['price'] - 1) * 100
        rows.append(row)
    print(f"\n== {days}d window, n={len(rows)} ==")
    print(f"  {'score':14s} {'r':>7s}  {'top20%':>8s} {'bot20%':>8s} {'delta':>8s}")
    for key in ('A_dist', 'B_val', 'C_val_cbpen', 'E_invcb'):
        xs = [r[key] for r in rows]; ys = [r['fwd'] for r in rows]
        r_ = pearson(xs, ys)
        t, b, dlt = quintile_delta(rows, key)
        print(f"  {key:14s} {r_:+7.3f}  {t:+7.2f}% {b:+7.2f}% {dlt:+7.2f}%")

def main():
    sub = bt.Subtensor(network='finney')
    cur = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}
    for days in (7, 14, 30):
        try: run(sub, days, cur)
        except Exception as e: print(f"{days}d FAILED: {e}")

if __name__ == '__main__':
    main()
