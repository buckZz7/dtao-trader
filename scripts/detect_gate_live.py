"""Detect whether the V440 emission gate is live on-chain.

Method: the chain's per-subnet chain-buy (SubnetExcessTao) is proportional to
its emission share. Compute predicted shares two ways:
  naive  = price_i / sum(price)
  gated  = gate(share) * share, then RENORMALIZED (chain-exact)
and correlate each with actual on-chain excess_tao. If gated correlates
better, the gate is live and our model direction is right.

Also prints the emission-gate bar (theta) and rank, and how many subnets are
below the bar.
"""
import bittensor as bt
import math
from collections import defaultdict

module = bt.storage.SubtensorModule
GATE_Q = 0.61
GATE_H = 3

sub = bt.Subtensor(network='finney')
prices = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}
ema = {}
for n in prices:
    if n == 0:
        continue
    try:
        r = sub.query(module.SubnetMovingPrice, params=[n])
        if isinstance(r, dict):
            ema[n] = r.get('bits', 0) / (2**32)
        else:
            ema[n] = float(r) / 1e9 if r else 0.0
    except Exception:
        ema[n] = 0.0

# Miner burn (chain gates the burn-weighted shares)
burns = {}
for n in prices:
    if n == 0:
        continue
    try:
        b = sub.query(module.MinerBurned, params=[n])
        burns[n] = b.get('bits', 0) / (2**32) if isinstance(b, dict) else 0.0
    except Exception:
        burns[n] = 0.0

# Actual chain buys
excess = {}
for n in prices:
    if n == 0:
        continue
    try:
        excess[n] = int(sub.query(module.SubnetExcessTao, params=[n])) / 1e9
    except Exception:
        excess[n] = 0.0

# --- Replicate chain get_shares (EMA-based, burn-weighted, normalized) ---
subnets = [n for n in prices if n != 0 and ema.get(n, 0) > 0]
tot_ema = sum(ema[n] for n in subnets)
price_shares = {n: ema[n] / tot_ema for n in subnets}

weighted = {n: price_shares[n] * (1 - min(burns.get(n, 0), 1.0)) for n in subnets}
tw = sum(weighted.values())
if tw > 0:
    shares = {n: weighted[n] / tw for n in subnets}
else:
    shares = price_shares

# theta = q-mass bar
sorted_shares = sorted(shares.values(), reverse=True)
cum, theta = 0.0, 0.0
bar_rank = None
for i, s in enumerate(sorted_shares, 1):
    cum += s
    theta = s
    if cum >= GATE_Q:
        bar_rank = i
        break

# gate and renormalize
gated = {}
for n, s in shares.items():
    if s <= 0:
        gated[n] = 0.0
        continue
    ratio = theta / s
    gate = 1.0 / (1.0 + ratio ** GATE_H)
    gated[n] = s * gate
gtot = sum(gated.values())
gated_norm = {n: g / gtot for n, g in gated.items()} if gtot > 0 else gated

# naive normalized (for comparison, over same subnet set)
naive_norm = {n: price_shares[n] for n in subnets}

# --- Correlate with actual chain buys ---
def pearson(a, b):
    xs, ys = [], []
    for n in subnets:
        if a.get(n, 0) > 0 and b.get(n, 0) > 0:
            xs.append(math.log(a[n])); ys.append(math.log(b[n]))
    if len(xs) < 5:
        return 0, 0
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx*dy else 0, len(xs)

r_naive, n1 = pearson(excess, naive_norm)
r_gated, n2 = pearson(excess, gated_norm)

print(f"subnets: {len(subnets)}, theta={theta:.5f}, bar_rank={bar_rank}")
below = sum(1 for s in shares.values() if s < theta)
print(f"subnets below bar: {below}")
print(f"\nlog-log correlation of predicted share vs ACTUAL chain buy:")
print(f"  naive (price share):   r={r_naive:+.3f} (n={n1})")
print(f"  gated (renormalized):  r={r_gated:+.3f} (n={n2})")
print(f"\n{'GATED is live' if r_gated > r_naive + 0.02 else 'NAIVE matches better'}")
