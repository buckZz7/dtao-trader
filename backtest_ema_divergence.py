"""Backtest: does spot/EMA divergence predict forward price returns?

Hypothesis (Buck's signal-#1 candidate):
  The chain allocates emissions using SubnetMovingPrice (EMA), not spot.
  - spot < EMA: chain is allocating chain-buy support as if price were higher
    (a lag subsidy) -> subnet gets more protocol buy pressure than its current
    price warrants -> expect positive forward returns.
  - spot > EMA: price is running ahead of protocol support -> stretched ->
    expect fading forward returns.

Signal: divergence_pct = (spot / ema - 1) * 100   (negative = below EMA)
We test correlation(divergence, forward_return) at 1d / 3d / 7d.
If the hypothesis holds, correlation should be NEGATIVE (below EMA -> up).

Methodology mirrors backtest_ranking.py: compute signal at a historical block
via sub.at(block=X), measure actual forward price change, Pearson r.
Multiple anchor blocks sampled to avoid single-window luck.
"""
import bittensor as bt
import math
from statistics import mean

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200
U96F32 = 2 ** 32

# Anchor blocks: sample signal at these offsets (days before "present" of each anchor's forward window)
# We need forward price at anchor+1d, +3d, +7d, so anchors must be >= 7 days back from current.
HORIZONS = [1, 3, 7]  # days forward
ANCHOR_DAYS_BACK = [7, 10, 14, 18, 21, 25, 28]  # each anchor is a separate sample

def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return float('nan')
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return float('nan')
    return cov / math.sqrt(vx * vy)

def main():
    sub = bt.Subtensor(network='finney')
    cur = sub.block()
    print(f"current block {cur}")

    # For each anchor: collect {netuid: divergence_pct} and forward prices
    # anchor block = cur - days_back*7200
    # forward block for horizon h = anchor + h*7200
    samples = {h: [] for h in HORIZONS}  # per horizon, list of (divergence, fwd_return)

    # Cache forward prices per (anchor_days, horizon) -> {netuid: price}
    for dback in ANCHOR_DAYS_BACK:
        anchor = cur - dback * BLOCKS_PER_DAY
        try:
            snap = sub.at(block=anchor)
            spots = snap.prices.alpha_prices()  # {netuid_int: float}
        except Exception as e:
            print(f"  anchor -{dback}d (block {anchor}): snapshot failed {e}")
            continue

        # EMA per subnet at anchor. Query all netuids with a positive spot.
        div = {}
        for netuid_str, spot in spots.items():
            netuid = int(netuid_str)
            if netuid == 0 or not spot or float(spot) <= 0:
                continue
            try:
                mp = snap.query(module.SubnetMovingPrice, params=[netuid])
                bits = mp.get('bits', 0) if isinstance(mp, dict) else int(mp)
                ema = bits / U96F32
                if ema <= 0:
                    continue
                div[netuid] = (float(spot) / ema - 1) * 100.0
            except Exception:
                continue

        # Forward prices
        for h in HORIZONS:
            fblock = anchor + h * BLOCKS_PER_DAY
            if fblock > cur:
                continue
            try:
                fsnap = sub.at(block=fblock)
                fspots = fsnap.prices.alpha_prices()
            except Exception:
                continue
            for netuid, d in div.items():
                fp = fspots.get(netuid) or fspots.get(str(netuid))
                sp = spots.get(netuid) or spots.get(str(netuid))
                if fp and sp and float(fp) > 0 and float(sp) > 0:
                    fwd = (float(fp) / float(sp) - 1) * 100.0
                    samples[h].append((d, fwd))
        print(f"  anchor -{dback}d (block {anchor}): {len(div)} subnets sampled")

    print("\n" + "=" * 70)
    print("SPOT/EMA DIVERGENCE BACKTEST")
    print("=" * 70)
    print("Negative r = hypothesis CONFIRMED (below EMA -> forward gains)")
    print("Positive r = hypothesis INVERTED (below EMA -> forward losses)")
    print("-" * 70)
    for h in HORIZONS:
        data = samples[h]
        if not data:
            print(f"  {h}d: no data")
            continue
        xs = [d for d, _ in data]
        ys = [f for _, f in data]
        r = pearson(xs, ys)
        print(f"  {h}d forward: n={len(data):4d}  r={r:+.4f}")

        # Quartile: most-below-EMA vs most-above-EMA
        srt = sorted(data, key=lambda t: t[0])
        q = max(1, len(srt) // 4)
        below = [f for _, f in srt[:q]]   # most negative divergence (below EMA)
        above = [f for _, f in srt[-q:]]  # most positive divergence (above EMA)
        print(f"      below-EMA quartile avg fwd: {mean(below):+.2f}%   "
              f"above-EMA quartile avg fwd: {mean(above):+.2f}%   "
              f"delta: {mean(below)-mean(above):+.2f}%")

if __name__ == '__main__':
    main()
