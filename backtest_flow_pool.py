"""Backtest: pool-delta flow vs neuron-stake flow.

Current flow metric = change in sum(neurons.total_stake.rao) over 7d,
which measures consensus weight inflation (emissions accrual), NOT buying.
SN28/66 showed +396%/+612% "flow" while price fell -22%/-25%.

Candidate replacement: pool TAO delta = SubnetTAO(now) - SubnetTAO(7d ago),
as % of pool. This is actual TAO entering/leaving the AMM — what moves price.
Note: pool TAO also grows from emission injection + chain buys, so we test
both RAW pool delta and pool delta MINUS protocol injection (excess+tao_in
approximated via pool delta minus price-implied alpha side... keep simple:
test raw first, and a version subtracting daily_cb accumulation).

Compare 3 metrics vs forward returns:
  M1: neuron-stake flow (current, known contaminated)
  M2: pool TAO delta % (raw)
  M3: pool TAO delta % minus chain-buy contribution (excess*7200*7/pool)

Windows: 7d and 14d (flow needs a lookback; use 7d flow to predict next 7d,
and 14d flow to predict next 7d... simplest: measure flow over the window
[now-2w, now-1w], returns over [now-1w, now] — non-overlapping).
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

def q(snap, m, n, default=0):
    try:
        r = snap.query(m, params=[n])
        if isinstance(r, dict): return r.get('bits', 0) / (2**32)
        return int(r) / 1e9 if r else default
    except Exception:
        return default

def neuron_stake(snap, n):
    try:
        neurons = snap.neurons.neurons(netuid=n)
        return sum((x.total_stake.rao if hasattr(x.total_stake, 'rao') else 0) / 1e9 for x in neurons)
    except Exception:
        return None

def main():
    sub = bt.Subtensor(network='finney')
    blk_now = sub.block()
    blk_1w = blk_now - 7 * BLOCKS_PER_DAY
    blk_2w = blk_now - 14 * BLOCKS_PER_DAY

    prices_now = {int(k): float(v) for k, v in sub.prices.alpha_prices().items()}
    snap_1w = sub.at(block=blk_1w)
    snap_2w = sub.at(block=blk_2w)
    prices_1w = {int(k): float(v) for k, v in snap_1w.prices.alpha_prices().items()}
    prices_2w = {int(k): float(v) for k, v in snap_2w.prices.alpha_prices().items()}

    rows = []
    for n in prices_now:
        if n == 0: continue
        p_now, p_1w, p_2w = prices_now.get(n, 0), prices_1w.get(n, 0), prices_2w.get(n, 0)
        if p_now <= 0 or p_1w <= 0 or p_2w <= 0: continue

        # Flow window: [2w ago -> 1w ago]. Return window: [1w ago -> now].
        pool_2w = q(snap_2w, module.SubnetTAO, n)
        pool_1w = q(snap_1w, module.SubnetTAO, n)
        if pool_2w < 500 or pool_1w < 500: continue

        stake_2w = neuron_stake(snap_2w, n)
        stake_1w = neuron_stake(snap_1w, n)
        if stake_2w is None or stake_1w is None or stake_2w < 100: continue

        m1 = (stake_1w - stake_2w) / pool_1w * 100          # current metric
        m2 = (pool_1w - pool_2w) / pool_2w * 100            # raw pool delta
        cb_1w = q(snap_1w, module.SubnetExcessTao, n)        # per-block excess at 1w
        m3 = ((pool_1w - pool_2w) - cb_1w * 7 * BLOCKS_PER_DAY) / pool_2w * 100  # pool delta minus chain buys

        fwd = (p_now / p_1w - 1) * 100
        rows.append({'netuid': n, 'm1': m1, 'm2': m2, 'm3': m3, 'fwd': fwd})

    print(f"n = {len(rows)} subnets (flow: [14d->7d ago], fwd: [7d ago->now])")
    for key, label in (('m1', 'M1 neuron-stake flow (CURRENT)'),
                       ('m2', 'M2 pool TAO delta (raw)'),
                       ('m3', 'M3 pool delta minus chain buys')):
        xs = [r[key] for r in rows]; ys = [r['fwd'] for r in rows]
        r_ = pearson(xs, ys)
        srt = sorted(rows, key=lambda r: r[key], reverse=True)
        qn = max(3, len(srt) // 4)
        top = sum(r['fwd'] for r in srt[:qn]) / qn
        bot = sum(r['fwd'] for r in srt[-qn:]) / qn
        print(f"  {label:34s} r={r_:+.3f}   topQ={top:+.2f}%  botQ={bot:+.2f}%  delta={top-bot:+.2f}%")

    # Also: momentum guard idea — does filtering M2 by 30d momentum change things?
    # (can't do historical momentum easily here; note only)

if __name__ == '__main__':
    main()
