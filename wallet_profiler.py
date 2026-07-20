"""Wallet profiler: identifies whales, chart wreckers, and diamond hands.

For each coldkey, builds a profile:
1. Which subnets they hold stake in
2. How concentrated/diversified their portfolio is
3. Whether they're a validator (long-term) or speculator
4. Total stake value in TAO

Then for each subnet, builds a holder base score:
1. Top holder concentration (Gini coefficient)
2. How many holders are validators (long-term aligned)
3. How many holders are cross-subnet diversified (lower dump risk)
4. Known dumper detection (wallets that have unstaked from multiple subnets)
"""
import bittensor as bt
import json, os, time
from collections import defaultdict

module = bt.storage.SubtensorModule

def get_subnet_holders(sub, netuid):
    """Get all coldkeys with stake on a subnet."""
    neurons = sub.neurons.neurons(netuid=netuid)
    holders = {}
    for n in neurons:
        stake_rao = n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0
        stake_tao = stake_rao / 1e9 if stake_rao else 0
        if stake_tao > 0:
            ck = n.coldkey
            if ck not in holders:
                holders[ck] = {'stake': 0, 'hotkeys': [], 'validator': False}
            holders[ck]['stake'] += stake_tao
            holders[ck]['hotkeys'].append(n.hotkey)
            if n.validator_permit:
                holders[ck]['validator'] = True
    return holders

def get_coldkey_portfolio(sub, coldkey):
    """Get all stake positions for a coldkey across all subnets."""
    try:
        result = sub.staking.stake_for_coldkey(coldkey_ss58=coldkey)
        if not isinstance(result, list):
            return []
        
        positions = []
        for p in result:
            # Parse StakePosition
            p_str = str(p)
            # Extract netuid and stake
            import re
            netuid_match = re.search(r'netuid=(\d+)', p_str)
            stake_match = re.search(r'stake=([\d.]+)', p_str)
            
            if netuid_match and stake_match:
                netuid = int(netuid_match.group(1))
                stake = float(stake_match.group(1))
                positions.append({'netuid': netuid, 'stake': stake})
        
        return positions
    except:
        return []

def compute_gini(values):
    """Compute Gini coefficient (0=equal, 1=monopoly)."""
    if not values or sum(values) == 0:
        return 0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumsum = sum((i + 1) * v for i, v in enumerate(sorted_vals))
    total = sum(sorted_vals)
    return (2 * cumsum) / (n * total) - (n + 1) / n

def assess_holder_base(sub, netuid, name, all_prices):
    """Assess the quality of a subnet's holder base."""
    holders = get_subnet_holders(sub, netuid)
    
    if not holders:
        return {'netuid': netuid, 'name': name, 'error': 'no holders'}
    
    total_stake = sum(h['stake'] for h in holders.values())
    num_holders = len(holders)
    
    # Gini coefficient
    stakes = [h['stake'] for h in holders.values()]
    gini = compute_gini(stakes)
    
    # Top holder concentration
    sorted_holders = sorted(holders.items(), key=lambda x: x[1]['stake'], reverse=True)
    top1_pct = sorted_holders[0][1]['stake'] / total_stake * 100 if total_stake > 0 else 0
    top3_pct = sum(h['stake'] for _, h in sorted_holders[:3]) / total_stake * 100 if total_stake > 0 else 0
    top5_pct = sum(h['stake'] for _, h in sorted_holders[:5]) / total_stake * 100 if total_stake > 0 else 0
    
    # Validator holders (long-term aligned)
    validator_holders = sum(1 for h in holders.values() if h['validator'])
    validator_stake = sum(h['stake'] for h in holders.values() if h['validator'])
    validator_pct = validator_stake / total_stake * 100 if total_stake > 0 else 0
    
    # Portfolio diversity (how many subnets each holder is in)
    # Sample top 10 holders for portfolio check
    diversified = 0
    single_subnet = 0
    portfolios = {}
    
    for ck, h in sorted_holders[:10]:  # Check top 10 holders
        portfolio = get_coldkey_portfolio(sub, ck)
        num_subnets = len(set(p['netuid'] for p in portfolio))
        portfolios[ck] = num_subnets
        if num_subnets > 3:
            diversified += 1
        elif num_subnets == 1:
            single_subnet += 1
        time.sleep(0.05)  # Rate limit
    
    # Holder base score (0-100)
    # Lower Gini = better (more distributed)
    # More validators = better (long-term aligned)
    # More diversified holders = better (lower dump risk)
    
    gini_score = (1 - gini) * 40  # 40 points max for perfect distribution
    validator_score = min(20, (validator_pct / 50) * 20)  # 20 points for 50%+ validator stake
    diversity_score = min(20, (diversified / 10) * 20)  # 20 points if all top 10 are diversified
    holder_count_score = min(10, (num_holders / 30) * 10)  # 10 points for 30+ holders
    concentration_penalty = 0
    if top1_pct > 50:
        concentration_penalty = -10  # One wallet holds >50% = risky
    
    base_score = gini_score + validator_score + diversity_score + holder_count_score + concentration_penalty
    
    return {
        'netuid': netuid,
        'name': name,
        'num_holders': num_holders,
        'total_stake': total_stake,
        'gini': round(gini, 3),
        'top1_pct': round(top1_pct, 1),
        'top3_pct': round(top3_pct, 1),
        'top5_pct': round(top5_pct, 1),
        'validator_holders': validator_holders,
        'validator_stake_pct': round(validator_pct, 1),
        'diversified_holders': diversified,
        'single_subnet_holders': single_subnet,
        'holder_base_score': round(base_score, 1),
        'top_holders': [
            {
                'coldkey': ck[:8] + '...' + ck[-6:],
                'stake': h['stake'],
                'pct': round(h['stake'] / total_stake * 100, 1),
                'validator': h['validator'],
                'subnets': portfolios.get(ck, '?'),
            }
            for ck, h in sorted_holders[:10]
        ],
    }

def main():
    sub = bt.Subtensor(network='finney')
    print(f"Block: {sub.block()}")
    
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    
    # Assess top emission-enabled subnets by chain buy
    with open('data/emission_enabled_subnets.json') as f:
        subnets = json.load(f)
    
    # Focus on the most interesting subnets
    # Top 15 by CB vs Pool + top 10 by price
    sorted_by_cb = sorted(subnets, key=lambda x: x['daily_cb'] / x['tao_pool'] if x['tao_pool'] > 0 else 0, reverse=True)
    sorted_by_price = sorted(subnets, key=lambda x: x['price'], reverse=True)
    
    to_assess = list(set(
        [s['netuid'] for s in sorted_by_cb[:15]] + 
        [s['netuid'] for s in sorted_by_price[:10]]
    ))
    to_assess.sort()
    
    print(f"\nAssessing holder base for {len(to_assess)} subnets...")
    
    results = []
    for i, netuid in enumerate(to_assess):
        name = names.get(str(netuid), f"SN{netuid}")
        print(f"  [{i+1}/{len(to_assess)}] SN{netuid} ({name})...", end=' ', flush=True)
        
        result = assess_holder_base(sub, netuid, name, all_prices)
        if 'error' not in result:
            print(f"score: {result['holder_base_score']}, holders: {result['num_holders']}, gini: {result['gini']}")
            results.append(result)
        else:
            print(f"error: {result['error']}")
    
    # Sort by holder base score
    results.sort(key=lambda x: x['holder_base_score'], reverse=True)
    
    print(f"\n{'='*90}")
    print(f"HOLDER BASE ASSESSMENT")
    print(f"{'='*90}")
    print(f"\n{'SN':>4} {'Name':>15} {'Score':>6} {'Holders':>8} {'Gini':>6} {'Top1%':>7} {'ValStk%':>8} {'Divers':>7}")
    print("-" * 65)
    for r in results:
        print(f"  SN{r['netuid']:3d} {r['name']:>15} {r['holder_base_score']:>6.1f} {r['num_holders']:>8} {r['gini']:>6.3f} {r['top1_pct']:>6.1f}% {r['validator_stake_pct']:>7.1f}% {r['diversified_holders']:>7}")
    
    # Save
    with open('data/holder_base.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to data/holder_base.json")

if __name__ == '__main__':
    main()
