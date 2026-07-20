"""Wallet reputation system: tracks all holders, conviction locks, and dump risk.

For each subnet:
1. Gets ALL holders (not just top 10)
2. Checks conviction locks for each (perpetual vs decaying vs none)
3. Computes liquid stake vs locked stake
4. Identifies dump risk (liquid stake concentration)
5. Identifies diamond hands (perpetual conviction holders)
6. Tracks super-delegator wallets (appear across many subnets)

Wallet reputation database:
- Each coldkey gets a profile: subnets held, total stake, locked stake, validator status
- Over time: track unstake events (wallets that pulled out of crashing subnets)
"""
import bittensor as bt
import json, os, time, re
from collections import defaultdict
from datetime import datetime, timezone

module = bt.storage.SubtensorModule

def get_all_holders(sub, netuid):
    """Get ALL coldkeys with stake on a subnet."""
    neurons = sub.neurons.neurons(netuid=netuid)
    holders = {}
    for n in neurons:
        stake_rao = n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0
        stake_tao = stake_rao / 1e9 if stake_rao else 0
        if stake_tao > 0:
            ck = n.coldkey
            if ck not in holders:
                holders[ck] = {
                    'stake': 0,
                    'hotkeys': [],
                    'validator': False,
                    'uids': [],
                }
            holders[ck]['stake'] += stake_tao
            holders[ck]['hotkeys'].append(n.hotkey)
            holders[ck]['uids'].append(n.uid)
            if n.validator_permit:
                holders[ck]['validator'] = True
    return holders

def get_conviction_locks(sub, coldkey):
    """Get all conviction locks for a coldkey."""
    try:
        result = sub.locks.locks_for_coldkey(coldkey_ss58=coldkey)
        if not isinstance(result, list):
            return []
        
        locks = []
        for lock in result:
            if isinstance(lock, dict):
                locks.append({
                    'netuid': lock.get('netuid'),
                    'hotkey': lock.get('hotkey', ''),
                    'locked_alpha': float(lock.get('locked_alpha', 0)) if lock.get('locked_alpha') else 0,
                    'is_perpetual': lock.get('is_perpetual', False),
                })
        return locks
    except:
        return []

def parse_alpha_from_lock(lock_dict):
    """Parse the alpha amount from a lock dict (handles Balance objects)."""
    raw = lock_dict.get('locked_alpha', 0)
    if isinstance(raw, (int, float)):
        return float(raw)
    # Balance objects — try .rao or string parsing
    if hasattr(raw, 'rao'):
        return raw.rao / 1e9
    # Parse from string like "1,269,236.786271830י"
    s = str(raw).replace(',', '').replace('י', '').replace('ף', '').replace('ᚃ', '').strip()
    try:
        return float(s)
    except:
        return 0

def get_all_locks(sub, coldkey):
    """Get conviction locks with proper parsing."""
    try:
        result = sub.locks.locks_for_coldkey(coldkey_ss58=coldkey)
        if not isinstance(result, list):
            return []
        
        locks = []
        for lock in result:
            if isinstance(lock, dict):
                locked = parse_alpha_from_lock(lock)
                locks.append({
                    'netuid': lock.get('netuid'),
                    'hotkey': lock.get('hotkey', ''),
                    'locked_alpha': locked,
                    'is_perpetual': lock.get('is_perpetual', False),
                })
        return locks
    except:
        return []

def assess_subnet_holders(sub, netuid, name, price):
    """Full holder assessment with conviction locks."""
    holders = get_all_holders(sub, netuid)
    
    if not holders:
        return {'netuid': netuid, 'name': name, 'error': 'no holders'}
    
    total_stake = sum(h['stake'] for h in holders.values())
    num_holders = len(holders)
    
    # Sort by stake
    sorted_holders = sorted(holders.items(), key=lambda x: x[1]['stake'], reverse=True)
    
    # Check conviction locks for ALL holders
    all_holders_data = []
    total_locked = 0
    total_perpetual = 0
    total_liquid = 0
    locked_holders = 0
    perpetual_holders = 0
    
    for coldkey, h in sorted_holders:
        # Get portfolio (how many subnets this wallet is in)
        portfolio = []
        try:
            positions = sub.staking.stake_for_coldkey(coldkey_ss58=coldkey)
            if isinstance(positions, list):
                portfolio = [p for p in positions]
                num_subnets = len(set(str(p).split('netuid=')[1].split(',')[0] for p in portfolio if 'netuid=' in str(p)))
            else:
                num_subnets = '?'
        except:
            num_subnets = '?'
        
        # Get conviction locks
        locks = get_all_locks(sub, coldkey)
        
        # Locked amount on THIS subnet
        locked_here = sum(l['locked_alpha'] for l in locks if l.get('netuid') == netuid)
        perpetual_here = sum(l['locked_alpha'] for l in locks if l.get('netuid') == netuid and l.get('is_perpetual'))
        liquid_stake = max(0, h['stake'] - locked_here)
        
        total_locked += locked_here
        total_perpetual += perpetual_here
        total_liquid += liquid_stake
        if locked_here > 0:
            locked_holders += 1
        if perpetual_here > 0:
            perpetual_holders += 1
        
        all_holders_data.append({
            'coldkey': coldkey[:10] + '...' + coldkey[-6:],
            'full_coldkey': coldkey,
            'stake': round(h['stake'], 2),
            'pct': round(h['stake'] / total_stake * 100, 2),
            'locked': round(locked_here, 2),
            'perpetual': round(perpetual_here, 2),
            'liquid': round(liquid_stake, 2),
            'liquid_pct': round(liquid_stake / total_stake * 100, 2),
            'validator': h['validator'],
            'num_subnets': num_subnets,
            'has_locks': len(locks) > 0,
        })
        
        time.sleep(0.02)  # Rate limit
    
    # Compute holder base quality
    liquid_concentration = total_liquid / total_stake * 100 if total_stake > 0 else 100
    locked_pct = total_locked / total_stake * 100 if total_stake > 0 else 0
    perpetual_pct = total_perpetual / total_stake * 100 if total_stake > 0 else 0
    
    # Top liquid holder (dump risk)
    top_liquid = max(h['liquid'] for h in all_holders_data) if all_holders_data else 0
    top_liquid_pct = top_liquid / total_stake * 100 if total_stake > 0 else 0
    
    # Gini on liquid stake (what actually matters for dump risk)
    liquid_stakes = [h['liquid'] for h in all_holders_data if h['liquid'] > 0]
    liquid_gini = compute_gini(liquid_stakes) if liquid_stakes else 1.0
    
    # Holder base score (0-100)
    # Locked stake is safe (can't dump)
    # Liquid stake is at risk
    # Score rewards: high locked %, low liquid concentration, many holders, diversified
    
    safety_score = min(40, perpetual_pct / 2)  # 40 pts for 80%+ perpetual locked
    distribution_score = (1 - liquid_gini) * 25  # 25 pts for distributed liquid
    holder_count_score = min(15, (num_holders / 50) * 15)  # 15 pts for 50+ holders
    concentration_penalty = -15 if top_liquid_pct > 50 else 0  # -15 if one wallet holds >50% liquid
    
    base_score = safety_score + distribution_score + holder_count_score + concentration_penalty
    
    return {
        'netuid': netuid,
        'name': name,
        'price': price,
        'num_holders': num_holders,
        'total_stake': round(total_stake, 2),
        'total_locked': round(total_locked, 2),
        'total_perpetual': round(total_perpetual, 2),
        'total_liquid': round(total_liquid, 2),
        'locked_pct': round(locked_pct, 1),
        'perpetual_pct': round(perpetual_pct, 1),
        'liquid_pct': round(liquid_concentration, 1),
        'liquid_gini': round(liquid_gini, 3),
        'top_liquid_pct': round(top_liquid_pct, 1),
        'locked_holders': locked_holders,
        'perpetual_holders': perpetual_holders,
        'holder_base_score': round(base_score, 1),
        'holders': all_holders_data,
    }

def compute_gini(values):
    if not values or sum(values) == 0:
        return 0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumsum = sum((i + 1) * v for i, v in enumerate(sorted_vals))
    total = sum(sorted_vals)
    return (2 * cumsum) / (n * total) - (n + 1) / n

def main():
    sub = bt.Subtensor(network='finney')
    print(f"Block: {sub.block()}")
    
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    
    # Load emission-enabled subnets
    with open('data/emission_enabled_subnets.json') as f:
        subnets = json.load(f)
    
    # Sort by chain buy vs pool (most interesting first)
    sorted_subnets = sorted(subnets, key=lambda x: x['daily_cb'] / x['tao_pool'] if x['tao_pool'] > 0 else 0, reverse=True)
    
    # Assess top 20 subnets + some undervalued ones
    to_assess = [s['netuid'] for s in sorted_subnets[:20]]
    # Also add some undervalued subnets from valuation analysis
    try:
        with open('data/valuation_analysis.json') as f:
            valuation = json.load(f)
        undervalued = [v['netuid'] for v in valuation if v['distance_pct'] < -28][:10]
        to_assess = list(set(to_assess + undervalued))
    except:
        pass
    to_assess.sort()
    
    print(f"\nAssessing {len(to_assess)} subnets with full conviction lock analysis...")
    
    results = []
    wallet_db = {}  # coldkey -> wallet profile
    
    for i, netuid in enumerate(to_assess):
        name = names.get(str(netuid), f"SN{netuid}")
        price = float(all_prices.get(netuid, 0))
        print(f"  [{i+1}/{len(to_assess)}] SN{netuid} ({name})...", end=' ', flush=True)
        
        result = assess_subnet_holders(sub, netuid, name, price)
        if 'error' not in result:
            print(f"score: {result['holder_base_score']}, holders: {result['num_holders']}, locked: {result['locked_pct']}%, liquid: {result['liquid_pct']}%")
            results.append(result)
            
            # Build wallet database
            for h in result['holders']:
                ck = h['full_coldkey']
                if ck not in wallet_db:
                    wallet_db[ck] = {
                        'coldkey': ck,
                        'subnets': [],
                        'total_stake': 0,
                        'total_locked': 0,
                        'validator': False,
                    }
                wallet_db[ck]['subnets'].append(netuid)
                wallet_db[ck]['total_stake'] += h['stake']
                wallet_db[ck]['total_locked'] += h['locked']
                if h['validator']:
                    wallet_db[ck]['validator'] = True
        else:
            print(f"error: {result['error']}")
    
    # Sort results by holder base score
    results.sort(key=lambda x: x['holder_base_score'], reverse=True)
    
    print(f"\n{'='*100}")
    print(f"FULL HOLDER BASE ASSESSMENT (with conviction locks)")
    print(f"{'='*100}")
    print(f"\n{'SN':>4} {'Name':>15} {'Score':>6} {'Holds':>6} {'TotalStk':>10} {'Locked%':>8} {'Perp%':>7} {'Liquid%':>8} {'LiqGini':>8} {'TopLiq%':>8}")
    print("-" * 90)
    for r in results:
        print(f"  SN{r['netuid']:3d} {r['name']:>15} {r['holder_base_score']:>6.1f} {r['num_holders']:>6} {r['total_stake']:>10.0f} {r['locked_pct']:>7.1f}% {r['perpetual_pct']:>6.1f}% {r['liquid_pct']:>7.1f}% {r['liquid_gini']:>8.3f} {r['top_liquid_pct']:>7.1f}%")
    
    # Wallet database summary
    print(f"\n{'='*80}")
    print(f"WALLET DATABASE: {len(wallet_db)} unique wallets tracked")
    print(f"{'='*80}")
    
    # Super-delegators (10+ subnets)
    super_del = [(ck, w) for ck, w in wallet_db.items() if len(w['subnets']) >= 10]
    super_del.sort(key=lambda x: x[1]['total_stake'], reverse=True)
    print(f"\nSuper-delegators (10+ subnets): {len(super_del)}")
    for ck, w in super_del[:10]:
        locked_pct = w['total_locked'] / w['total_stake'] * 100 if w['total_stake'] > 0 else 0
        print(f"  {ck[:10]}...{ck[-6:]}: {len(w['subnets'])} subnets, {w['total_stake']:.0f} TAO, {locked_pct:.0f}% locked, val:{w['validator']}")
    
    # Single-subnet whales (dump risk)
    single_whales = [(ck, w) for ck, w in wallet_db.items() if len(w['subnets']) == 1 and w['total_stake'] > 50000]
    single_whales.sort(key=lambda x: x[1]['total_stake'], reverse=True)
    print(f"\nSingle-subnet whales (>50K TAO, 1 subnet): {len(single_whales)}")
    for ck, w in single_whales[:10]:
        locked_pct = w['total_locked'] / w['total_stake'] * 100 if w['total_stake'] > 0 else 0
        liquid = w['total_stake'] - w['total_locked']
        print(f"  {ck[:10]}...{ck[-6:]}: SN{w['subnets'][0]}, {w['total_stake']:.0f} TAO, {locked_pct:.0f}% locked, {liquid:.0f} liquid")
    
    # Save
    os.makedirs('data', exist_ok=True)
    with open('data/holder_base_full.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    # Save wallet DB (without full coldkeys for privacy)
    wallet_summary = []
    for ck, w in wallet_db.items():
        wallet_summary.append({
            'coldkey': ck[:10] + '...' + ck[-6:],
            'num_subnets': len(w['subnets']),
            'subnets': w['subnets'],
            'total_stake': round(w['total_stake'], 2),
            'total_locked': round(w['total_locked'], 2),
            'locked_pct': round(w['total_locked'] / w['total_stake'] * 100, 1) if w['total_stake'] > 0 else 0,
            'validator': w['validator'],
        })
    wallet_summary.sort(key=lambda x: x['total_stake'], reverse=True)
    
    with open('data/wallet_database.json', 'w') as f:
        json.dump(wallet_summary, f, indent=2)
    
    print(f"\nSaved to data/holder_base_full.json and data/wallet_database.json")

if __name__ == '__main__':
    main()
