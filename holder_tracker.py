"""Holder tracker: scans neurons across all subnets, builds wallet database over time.

Instead of one-shot snapshots, this runs periodically and accumulates:
1. Which coldkeys hold stake on which subnets
2. How their stake changes over time (stake/unstake events)
3. Conviction lock status (locked vs liquid)
4. Cross-subnet wallet profiles (diversified vs concentrated)

Run periodically (cron). Each run appends to history. Over time we build:
- Wallet reputation (diamond hands vs dumpers)
- Stake flow tracking (who's entering/leaving which subnets)
- Concentration trends (holder base improving or deteriorating)
"""
import bittensor as bt
import json, os, time
from collections import defaultdict
from datetime import datetime, timezone

module = bt.storage.SubtensorModule

def scan_all_holders(sub):
    """Scan all subnets, return {coldkey: {netuid: stake}} mapping."""
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    
    wallet_db = {}  # coldkey -> {netuid: stake}
    subnet_holders = {}  # netuid -> {coldkey: stake}
    
    emission_enabled = {}
    for netuid_str in all_prices:
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        try:
            enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
            emission_enabled[netuid] = enabled
        except:
            emission_enabled[netuid] = False
    
    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        if not emission_enabled.get(netuid, False):
            continue
        
        name = names.get(netuid_str, f"SN{netuid}")
        
        try:
            neurons = sub.neurons.neurons(netuid=netuid)
            for n in neurons:
                stake_rao = n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0
                stake_tao = stake_rao / 1e9 if stake_rao else 0
                if stake_tao < 0.1:
                    continue
                
                ck = n.coldkey
                hk = n.hotkey
                
                if ck not in wallet_db:
                    wallet_db[ck] = {
                        'subnets': {},
                        'validator': False,
                        'total_stake': 0,
                    }
                wallet_db[ck]['subnets'][netuid] = stake_tao
                wallet_db[ck]['total_stake'] += stake_tao
                if n.validator_permit:
                    wallet_db[ck]['validator'] = True
                
                if netuid not in subnet_holders:
                    subnet_holders[netuid] = {}
                subnet_holders[netuid][ck] = stake_tao
        except:
            pass
    
    return wallet_db, subnet_holders, emission_enabled

def get_conviction_locks(sub):
    """Get all conviction locks, return {coldkey: {netuid: locked_alpha}}."""
    try:
        result = sub.query_map(module.Lock, params=[])
        locks = defaultdict(lambda: defaultdict(float))
        
        for item in result:
            if hasattr(item, 'key') and hasattr(item, 'value'):
                key, val = item.key, item.value
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                key, val = item
            else:
                continue
            
            if isinstance(key, (tuple, list)) and len(key) >= 2:
                coldkey, netuid = key[0], key[1]
            else:
                continue
            
            locked_mass = val.get('locked_mass', 0)
            if isinstance(locked_mass, (int, float)):
                locked_alpha = locked_mass / 1e9
            else:
                locked_alpha = 0
            
            locks[coldkey][netuid] += locked_alpha
        
        return locks
    except:
        return {}

def compute_subnet_locked_pct(sub, locks):
    """Compute locked alpha % of circulating supply per subnet."""
    all_prices = sub.prices.alpha_prices()
    results = {}
    
    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        
        try:
            alpha_out = int(sub.query(module.SubnetAlphaOut, params=[netuid])) / 1e9
            proto_alpha = int(sub.query(module.SubnetProtocolAlpha, params=[netuid])) / 1e9
        except:
            continue
        
        total_locked = sum(locks[ck][netuid] for ck in locks if netuid in locks[ck])
        
        locked_pct = (total_locked / alpha_out * 100) if alpha_out > 0 else 0
        proto_pct = (proto_alpha / (alpha_out + int(sub.query(module.SubnetAlphaIn, params=[netuid])) / 1e9) * 100) if alpha_out > 0 else 0
        
        results[netuid] = {
            'circulating': alpha_out,
            'locked': total_locked,
            'protocol': proto_alpha,
            'locked_pct': locked_pct,
            'proto_pct': proto_pct,
        }
    
    return results

def run_scan():
    """Main scan: collect holders, locks, compute metrics."""
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    timestamp = datetime.now(timezone.utc).isoformat()
    
    print(f"[{timestamp[:19]}] Block {block}: scanning holders...")
    
    # Scan all holders
    wallet_db, subnet_holders, emission_enabled = scan_all_holders(sub)
    print(f"  Found {len(wallet_db)} wallets across {len(subnet_holders)} subnets")
    
    # Get conviction locks
    locks = get_conviction_locks(sub)
    print(f"  Found {len(locks)} wallets with conviction locks")
    
    # Compute locked % per subnet
    locked_pct = compute_subnet_locked_pct(sub, locks)
    
    # Build wallet profiles
    wallet_profiles = []
    for coldkey, data in wallet_db.items():
        num_subnets = len(data['subnets'])
        total_stake = data['total_stake']
        
        # Locked amount across all subnets
        total_locked = sum(locks[coldkey].values()) if coldkey in locks else 0
        locked_pct_wallet = (total_locked / total_stake * 100) if total_stake > 0 else 0
        
        wallet_profiles.append({
            'coldkey': coldkey[:10] + '...' + coldkey[-6:],
            'num_subnets': num_subnets,
            'total_stake': round(total_stake, 2),
            'total_locked': round(total_locked, 2),
            'locked_pct': round(locked_pct_wallet, 1),
            'validator': data['validator'],
            'subnets': sorted(data['subnets'].keys()),
        })
    
    wallet_profiles.sort(key=lambda x: x['total_stake'], reverse=True)
    
    # Build subnet summary
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    
    subnet_summary = []
    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        
        holders = subnet_holders.get(netuid, {})
        lp = locked_pct.get(netuid, {})
        
        if not holders and not emission_enabled.get(netuid, False):
            continue
        
        total_stake = sum(holders.values())
        num_holders = len(holders)
        
        # Gini
        stakes = list(holders.values())
        gini = 0
        if len(stakes) > 1 and sum(stakes) > 0:
            sorted_s = sorted(stakes)
            n = len(sorted_s)
            cumsum = sum((i + 1) * v for i, v in enumerate(sorted_s))
            total = sum(sorted_s)
            gini = (2 * cumsum) / (n * total) - (n + 1) / n
        
        subnet_summary.append({
            'netuid': netuid,
            'name': names.get(netuid_str, f"SN{netuid}"),
            'price': float(price),
            'num_holders': num_holders,
            'total_stake': round(total_stake, 2),
            'gini': round(gini, 3),
            'locked_pct': round(lp.get('locked_pct', 0), 1),
            'proto_pct': round(lp.get('proto_pct', 0), 1),
            'emission_enabled': emission_enabled.get(netuid, False),
        })
    
    subnet_summary.sort(key=lambda x: x['total_stake'], reverse=True)
    
    # Save snapshot
    snapshot = {
        'timestamp': timestamp,
        'block': block,
        'wallets': wallet_profiles,
        'subnets': subnet_summary,
    }
    
    os.makedirs('data/snapshots', exist_ok=True)
    with open(f'data/snapshots/block_{block}.json', 'w') as f:
        json.dump(snapshot, f, indent=2)
    
    # Also save latest
    with open('data/latest_snapshot.json', 'w') as f:
        json.dump(snapshot, f, indent=2)
    
    # Compare to previous snapshot for stake flow detection
    prev_files = sorted([f for f in os.listdir('data/snapshots') if f.startswith('block_')])
    if len(prev_files) > 1:
        prev_file = prev_files[-2]
        with open(f'data/snapshots/{prev_file}') as f:
            prev = json.load(f)
        
        prev_wallets = {w['coldkey']: w for w in prev['wallets']}
        
        # Detect stake changes
        flows = []
        for w in wallet_profiles:
            prev_w = prev_wallets.get(w['coldkey'])
            if prev_w:
                # Check for new subnets entered or exited
                prev_subnets = set(prev_w.get('subnets', []))
                curr_subnets = set(w['subnets'])
                
                entered = curr_subnets - prev_subnets
                exited = prev_subnets - curr_subnets
                
                stake_change = w['total_stake'] - prev_w['total_stake']
                
                if entered or exited or abs(stake_change) > 100:
                    flows.append({
                        'coldkey': w['coldkey'],
                        'entered': list(entered),
                        'exited': list(exited),
                        'stake_change': round(stake_change, 2),
                        'total_stake': w['total_stake'],
                    })
        
        if flows:
            print(f"\n  Stake flows detected ({len(flows)} wallets changed):")
            for f in flows[:10]:
                if f['entered']:
                    print(f"    + {f['coldkey']} entered SN{f['entered']} ({f['stake_change']:+.0f} TAO)")
                if f['exited']:
                    print(f"    - {f['coldkey']} exited SN{f['exited']} ({f['stake_change']:+.0f} TAO)")
                elif abs(f['stake_change']) > 100:
                    print(f"    ~ {f['coldkey']} stake changed {f['stake_change']:+.0f} TAO")
    
    print(f"\n  Snapshot saved: data/snapshots/block_{block}.json")
    print(f"  Wallets: {len(wallet_profiles)}")
    print(f"  Subnets: {len(subnet_summary)}")
    
    return snapshot

if __name__ == '__main__':
    run_scan()
