"""Lite signal detector: fast check for emission toggles + price moves only.

Only queries prices + emission status — no pool depth, conviction, or chain buys.
Completes in ~10 seconds. Designed for 2-minute polling.

Signals:
1. Emission toggled (ON→OFF or OFF→ON) — triumvirate kill switch
2. Price move >5% between checks

Full check (every 15 min) covers pool shifts, chain buy changes, etc.
"""
import bittensor as bt
import json, os
from datetime import datetime, timezone

SIGNALS_FILE = 'data/signals_history.jsonl'
PREV_FILE = 'data/prev_lite_snapshot.json'

def collect_lite():
    """Fast snapshot: prices + emission status only."""
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
            emission_enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
            name = names.get(netuid_str, f"SN{netuid}")
            snapshot['subnets'][netuid] = {
                'name': name,
                'price': float(price),
                'emission_enabled': emission_enabled,
            }
        except:
            pass

    return snapshot

def detect_lite(current, previous):
    """Detect emission toggles and price moves only."""
    signals = []

    for netuid, curr in current['subnets'].items():
        prev = previous['subnets'].get(netuid)
        if not prev:
            continue

        name = curr['name']

        # Emission toggle
        if curr['emission_enabled'] != prev['emission_enabled']:
            if curr['emission_enabled']:
                signals.append({
                    'type': 'EMISSION_ON',
                    'message': f"🟢 EMISSION RE-ENABLED: SN{netuid} ({name})\n   Chain buys will resume. Price floor support returning.",
                })
            else:
                signals.append({
                    'type': 'EMISSION_OFF',
                    'message': f"🔴 EMISSION DISABLED: SN{netuid} ({name})\n   Chain buys stopped. No price floor. Triumvirate kill switch.",
                })

        # Price move >5%
        if prev['price'] > 0:
            change = ((curr['price'] / prev['price']) - 1) * 100
            if abs(change) > 5:
                emoji = "🟢" if change > 0 else "🔴"
                signals.append({
                    'type': 'PRICE_MOVE',
                    'message': f"{emoji} PRICE MOVE: SN{netuid} ({name})\n   {change:+.1f}% ({prev['price']:.6f} → {curr['price']:.6f})",
                })

    return signals

def run_lite():
    """Main: collect, compare, output signals."""
    os.makedirs('data', exist_ok=True)

    current = collect_lite()

    if os.path.exists(PREV_FILE):
        with open(PREV_FILE) as f:
            previous = json.load(f)
    else:
        with open(PREV_FILE, 'w') as f:
            json.dump(current, f, indent=2)
        return []

    with open(PREV_FILE, 'w') as f:
        json.dump(current, f, indent=2)

    signals = detect_lite(current, previous)

    if signals:
        with open(SIGNALS_FILE, 'a') as f:
            for s in signals:
                f.write(json.dumps({'timestamp': current['timestamp'], **s}) + '\n')

    return signals

if __name__ == '__main__':
    signals = run_lite()
    if signals:
        print(f"DETECTED {len(signals)} SIGNALS:")
        for s in signals:
            print(f"\n{s['message']}")
    else:
        print("No signals.")
