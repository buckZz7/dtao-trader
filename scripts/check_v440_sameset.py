"""Strict same-set V440 check for the 3d window (where gated_chain looked best).

Identifies which subnets get dropped from EMA-based variants (ema == 0) and
re-runs naive vs gated_chain on the EXACT same subnet set.
"""
import bittensor as bt
import json, math

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200
GATE_Q, GATE_H = 0.61, 3

sub = bt.Subtensor(network='finney')
cur = sub.block()
blk = cur - 3 * BLOCKS_PER_DAY
snap = sub.at(block=blk)

prices = {int(k): float(v) for k, v in snap.prices.alpha_prices().items() if int(k) != 0 and float(v) > 0}
ema, rp, burns, excess = {}, {}, {}, {}
for n in prices:
    try:
        r = snap.query(module.SubnetMovingPrice, params=[n])
        ema[n] = r.get('bits', 0) / (2**32) if isinstance(r, dict) else 0.0
    except Exception:
        ema[n] = 0.0
    try:
        r = snap.query(module.RootProp, params=[n])
        rp[n] = r.get('bits', 0) / (2**32) if isinstance(r, dict) else 0.0
    except Exception:
        rp[n] = 0.0
    try:
        r = snap.query(module.MinerBurned, params=[n])
        burns[n] = r.get('bits', 0) / (2**32) if isinstance(r, dict) else 0.0
    except Exception:
        burns[n] = 0.0

now_prices = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}

zero_ema = sorted([n for n in prices if ema.get(n, 0) == 0])
print(f"subnets with ema=0 ({len(zero_ema)}): {zero_ema}")

# naive equilibrium for ALL subnets
sum_prices = sum(prices.values())
def naive_eq(n):
    s = prices[n] / sum_prices
    return 0.5 * s / rp[n] if rp[n] > 0 else 0

# gated chain-exact for EMA>0 subnets
subs = [n for n in prices if ema.get(n, 0) > 0]
tot_ema = sum(ema[n] for n in subs)
eshares = {n: ema[n] / tot_ema for n in subs}
weighted = {n: eshares[n] * (1 - min(burns.get(n, 0), 1.0)) for n in subs}
tw = sum(weighted.values())
bshares = {n: weighted[n] / tw for n in subs} if tw > 0 else eshares
sorted_s = sorted(bshares.values(), reverse=True)
cum, theta = 0.0, 0.0
for s in sorted_s:
    cum += s
    theta = s
    if cum >= GATE_Q:
        break
gated = {}
for n, s in bshares.items():
    if s <= 0:
        gated[n] = 0.0
        continue
    ratio = theta / s
    gate = 1.0 / (1.0 + ratio ** GATE_H)
    gated[n] = s * gate
gtot = sum(gated.values())
g_norm = {n: g / gtot for n, g in gated.items()}

def pearson(dists, changes):
    pts = [(x, y) for x, y in zip(dists, changes) if x == x and y == y]
    if len(pts) < 5:
        return 0.0, 0
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return (num/(dx*dy), len(pts)) if dx*dy else (0.0, 0)

def evaluate(netuids, eqfn, label):
    dists, changes, nids = [], [], []
    for n in netuids:
        eq = eqfn(n)
        pt, pn = prices.get(n, 0), now_prices.get(n, 0)
        if eq > 0 and pt > 0 and pn > 0:
            dists.append((pt / eq - 1) * 100)
            changes.append((pn / pt - 1) * 100)
            nids.append(n)
    r, cnt = pearson(dists, changes)
    order = sorted(range(cnt), key=lambda i: dists[i])
    k = max(2, cnt // 5)
    qd = (sum(changes[i] for i in order[-k:]) - sum(changes[i] for i in order[:k])) / k
    print(f"{label:22s}: r={r:+.3f} (n={cnt}) q_delta={qd:+.2f}%")
    return r, cnt, nids

# 1) naive on ALL subnets (like the main backtest)
evaluate(list(prices.keys()), naive_eq, "naive (all)")
# 2) naive on ONLY the EMA>0 set (same set gated_chain sees)
evaluate(subs, naive_eq, "naive (ema>0 only)")
# 3) gated chain-exact on EMA>0 set
r_g, cnt_g, gids = evaluate(subs, lambda n: (0.5 * g_norm[n] / rp[n]) if rp.get(n, 0) > 0 else 0, "gated_chain (ema>0)")
# 4) naive on the EXACT subnets gated_chain evaluated (n=98)
r_n, cnt_n, _ = evaluate(gids, naive_eq, "naive (gated set only)")
# 5) which subnets did gated drop?
dropped = sorted(set(subs) - set(gids))
print(f"gated dropped {len(dropped)}: {dropped}")
