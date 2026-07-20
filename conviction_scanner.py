"""Conviction/takeover scanner: tracks subnet ownership changes via conviction locking.

Uses the subnet_convictions read API to get:
- Total locked alpha, conviction, and takeover threshold per subnet
- Top convicted hotkey (takeover candidate)
- Blocks to threshold (ETA for takeover)
- Whether subnet is old enough for ownership change

Saves to data/conviction_scan.json
"""
import bittensor as bt
import json, os
from datetime import datetime, timezone
from collections import defaultdict

def scan_convictions():
    sub = bt.Subtensor(network='finney')
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    current_block = sub.block()
    
    results = []
    
    for netuid_str in sorted(all_prices.keys(), key=lambda x: int(x)):
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        
        try:
            conv = sub.read('subnet_convictions', netuid=netuid)
            
            locked = conv.get('total_locked_alpha')
            if not locked or locked.rao == 0:
                continue
            
            # Convert Balance objects
            def bal(b):
                return int(b.rao) / 1e9 if b and hasattr(b, 'rao') else 0
            
            locked_tao = bal(conv.get('total_locked_alpha'))
            conviction_tao = bal(conv.get('total_conviction_alpha'))
            threshold_tao = bal(conv.get('threshold_alpha'))
            alpha_out = bal(conv.get('alpha_out'))
            
            pct_of_threshold = (conviction_tao / threshold_tao * 100) if threshold_tao > 0 else 0
            blocks_to_threshold = conv.get('total_blocks_to_threshold', 0) or 0
            days_to_threshold = blocks_to_threshold / 7200 if blocks_to_threshold else 0
            
            registered_at = conv.get('registered_at', 0) or 0
            changeable_at = conv.get('ownership_changeable_at_block', 0) or 0
            can_takeover = current_block >= changeable_at if changeable_at else False
            age_blocks = current_block - registered_at if registered_at else 0
            age_days = age_blocks / 7200
            
            owner_hotkey = conv.get('owner_hotkey', '')
            
            # Get top convicted hotkey(s)
            hotkeys = conv.get('hotkeys', [])
            top_hk = hotkeys[0] if hotkeys else {}
            
            # Aggregate all lockers per subnet
            lockers = []
            for hk in hotkeys:
                lockers.append({
                    'hotkey': hk.get('hotkey', ''),
                    'is_owner': hk.get('is_owner', False),
                    'locked_alpha': bal(hk.get('locked_alpha')),
                    'conviction_alpha': bal(hk.get('conviction_alpha')),
                    'pct_of_threshold': float(hk.get('pct_of_threshold', 0)) * 100,
                    'blocks_to_threshold': hk.get('blocks_to_threshold'),
                })
            
            # Sort lockers by conviction
            lockers.sort(key=lambda x: -x['conviction_alpha'])
            
            name = str(names.get(netuid_str, f'SN{netuid}'))
            
            results.append({
                'netuid': netuid,
                'name': name,
                'alpha_out': round(alpha_out, 0),
                'locked_alpha': round(locked_tao, 0),
                'conviction_alpha': round(conviction_tao, 0),
                'threshold_alpha': round(threshold_tao, 0),
                'pct_of_threshold': round(pct_of_threshold, 1),
                'blocks_to_threshold': blocks_to_threshold,
                'days_to_threshold': round(days_to_threshold, 1),
                'can_takeover': can_takeover,
                'registered_at': registered_at,
                'changeable_at': changeable_at,
                'age_days': round(age_days, 0),
                'owner_hotkey': owner_hotkey,
                'num_lockers': len(lockers),
                'top_lockers': lockers[:5],
            })
            
        except Exception as e:
            pass
    
    # Sort by % of threshold (descending)
    results.sort(key=lambda x: -x['pct_of_threshold'])
    
    # Print summary
    print(f'\n{"SN":>4} {"Name":>15} {"Locked":>10} {"Conv":>10} {"Thresh":>9} {"%Thr":>6} {"Blocks":>7} {"Days":>5} {"Takeover":>9} {"Lockers":>8}')
    print('-' * 95)
    for r in results[:30]:
        takeover = 'YES' if r['can_takeover'] else 'no'
        print(f'{r["netuid"]:>4} {r["name"][:15]:>15} {r["locked_alpha"]:>9.0f} {r["conviction_alpha"]:>9.0f} {r["threshold_alpha"]:>8.0f} {r["pct_of_threshold"]:>5.1f}% {r["blocks_to_threshold"]:>7} {r["days_to_threshold"]:>4.0f}d {takeover:>9} {r["num_lockers"]:>8}')
    
    # Save
    os.makedirs('data', exist_ok=True)
    with open('data/conviction_scan.json', 'w') as f:
        json.dump({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'block': current_block,
            'results': results,
        }, f, indent=2)
    
    print(f'\nSaved to data/conviction_scan.json')
    print(f'Total subnets with conviction locks: {len(results)}')
    return results

if __name__ == '__main__':
    scan_convictions()
