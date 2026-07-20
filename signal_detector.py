"""Signal detector: compares latest snapshot to previous, detects changes.

Signals:
1. Emission toggled (ON→OFF or OFF→ON) — triumvirate kill switch
2. Chain buy spike (>2x previous)
3. Chain buy stopped (was >0, now 0)
4. Price move (>5% between snapshots)
5. Pool depth shift (>10% liquidity change)

Outputs signals as formatted messages for Telegram.
"""
import bittensor as bt
import json, os
from datetime import datetime, timezone

SIGNALS_FILE = 'data/signals_history.jsonl'
PREV_SNAPSHOT_FILE = 'data/prev_snapshot.json'

CHANNEL_ID = -1004304020541

def collect_snapshot():
    """Collect current state of all subnets."""
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    module = bt.storage.SubtensorModule
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()

    snapshot = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': block,
        'subnets': {},
    }

    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        try:
            name = names.get(netuid_str, f"SN{netuid}")
            emission_enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
            excess_tao = int(sub.query(module.SubnetExcessTao, params=[netuid])) / 1e9
            tao_pool = int(sub.query(module.SubnetTAO, params=[netuid])) / 1e9

            snapshot['subnets'][netuid] = {
                'name': name,
                'price': float(price),
                'emission_enabled': emission_enabled,
                'excess_tao': excess_tao,
                'tao_pool': tao_pool,
            }
        except:
            pass

    return snapshot

def detect_signals(current, previous):
    """Compare two snapshots, return list of signals."""
    signals = []

    for netuid_str, curr in current['subnets'].items():
        netuid = int(netuid_str)
        prev = previous['subnets'].get(netuid_str)

        if not prev:
            continue

        name = curr['name']

        # 1. Emission toggled
        if curr['emission_enabled'] != prev['emission_enabled']:
            if curr['emission_enabled']:
                signals.append({
                    'type': 'EMISSION_ON',
                    'netuid': netuid,
                    'name': name,
                    'severity': 'HIGH',
                    'message': f"🟢 EMISSION RE-ENABLED: SN{netuid} ({name})\n   Chain buys will resume. Price floor support returning.",
                })
            else:
                signals.append({
                    'type': 'EMISSION_OFF',
                    'netuid': netuid,
                    'name': name,
                    'severity': 'CRITICAL',
                    'message': f"🔴 EMISSION DISABLED: SN{netuid} ({name})\n   Chain buys stopped. No price floor. Triumvirate kill switch.",
                })

        # 2. Chain buy spike (>2x previous)
        if curr['excess_tao'] > 0 and prev['excess_tao'] > 0:
            if curr['excess_tao'] > prev['excess_tao'] * 2:
                signals.append({
                    'type': 'CB_SPIKE',
                    'netuid': netuid,
                    'name': name,
                    'severity': 'MEDIUM',
                    'message': f"📈 CHAIN BUY SPIKE: SN{netuid} ({name})\n   {prev['excess_tao']:.6f} → {curr['excess_tao']:.6f} TAO/block ({((curr['excess_tao']/prev['excess_tao'])-1)*100:.0f}% increase)",
                })

        # 3. Chain buy stopped (was >0, now 0)
        if curr['excess_tao'] == 0 and prev['excess_tao'] > 0:
            signals.append({
                'type': 'CB_STOPPED',
                'netuid': netuid,
                'name': name,
                'severity': 'MEDIUM',
                'message': f"⚠️ CHAIN BUY STOPPED: SN{netuid} ({name})\n   Was {prev['excess_tao']:.6f} TAO/block, now zero. Price reached equilibrium.",
            })

        # 4. Chain buy started (was 0, now >0)
        if curr['excess_tao'] > 0 and prev['excess_tao'] == 0:
            signals.append({
                'type': 'CB_STARTED',
                'netuid': netuid,
                'name': name,
                'severity': 'MEDIUM',
                'message': f"🟡 CHAIN BUY STARTED: SN{netuid} ({name})\n   Now {curr['excess_tao']:.6f} TAO/block. Price floor forming.",
            })

        # 5. Price move >5%
        if prev['price'] > 0:
            price_change = ((curr['price'] / prev['price']) - 1) * 100
            if abs(price_change) > 5:
                direction = "up" if price_change > 0 else "down"
                emoji = "🟢" if price_change > 0 else "🔴"
                signals.append({
                    'type': 'PRICE_MOVE',
                    'netuid': netuid,
                    'name': name,
                    'severity': 'MEDIUM',
                    'message': f"{emoji} PRICE MOVE: SN{netuid} ({name})\n   {price_change:+.1f}% ({prev['price']:.6f} → {curr['price']:.6f})",
                })

        # 6. Pool depth shift >10%
        if prev['tao_pool'] > 0:
            pool_change = ((curr['tao_pool'] / prev['tao_pool']) - 1) * 100
            if abs(pool_change) > 10:
                direction = "increase" if pool_change > 0 else "decrease"
                emoji = "💧" if pool_change > 0 else "🚨"
                signals.append({
                    'type': 'POOL_SHIFT',
                    'netuid': netuid,
                    'name': name,
                    'severity': 'MEDIUM',
                    'message': f"{emoji} POOL LIQUIDITY {direction.upper()}: SN{netuid} ({name})\n   {pool_change:+.1f}% ({prev['tao_pool']:.0f} → {curr['tao_pool']:.0f} TAO)",
                })

    return signals

def run_detector():
    """Main: collect snapshot, compare to previous, output signals."""
    os.makedirs('data', exist_ok=True)

    # Collect current
    current = collect_snapshot()

    # Load previous
    if os.path.exists(PREV_SNAPSHOT_FILE):
        with open(PREV_SNAPSHOT_FILE) as f:
            previous = json.load(f)
    else:
        previous = None

    # Save current as previous for next run
    with open(PREV_SNAPSHOT_FILE, 'w') as f:
        json.dump(current, f, indent=2)

    if not previous:
        print(f"[{current['timestamp'][:19]}] First run — no previous snapshot. Saved baseline.")
        return []

    # Detect signals
    signals = detect_signals(current, previous)

    # Save signals to history
    if signals:
        with open(SIGNALS_FILE, 'a') as f:
            for s in signals:
                f.write(json.dumps(s) + '\n')

    return signals

if __name__ == '__main__':
    signals = run_detector()
    if signals:
        print(f"\n{'='*50}")
        print(f"DETECTED {len(signals)} SIGNALS")
        print(f"{'='*50}")
        for s in signals:
            print(f"\n{s['message']}")
    else:
        print("No signals detected.")
