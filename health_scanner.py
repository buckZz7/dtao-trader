"""Subnet health scanner: checks if subnets are actually alive.

Measures on-chain activity, not GitHub activity:
1. Active miners — registered neurons that are actually serving
2. Active validators — validators with stake that are responding
3. Miner burn % — miners paying to participate (commitment signal)
4. Last update freshness — are neurons updating regularly?
5. Emission distribution — is the chain actually paying out?
"""
import bittensor as bt
import json, os, time, math
from datetime import datetime, timezone
from collections import defaultdict

module = bt.storage.SubtensorModule

def scan_health():
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    
    print(f"Block: {block}")
    
    results = []
    
    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        
        try:
            name = names.get(netuid_str, f"SN{netuid}")
            
            # Get neurons
            neurons = sub.neurons.neurons(netuid=netuid)
            total_neurons = len(neurons)
            
            # Count active neurons (active flag)
            active_neurons = sum(1 for n in neurons if n.active)
            
            # Count validators
            validators = sum(1 for n in neurons if n.validator_permit)
            
            # Count neurons with stake
            staked_neurons = sum(1 for n in neurons if (n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0) > 0)
            
            # Check last update freshness
            current_block = block
            stale_threshold = 1000  # ~2 hours without update = stale
            recent_updates = 0
            stale_neurons = 0
            
            for n in neurons:
                # n.last_update is the block of last update
                last_update = n.last_update if hasattr(n, 'last_update') else 0
                if isinstance(last_update, (int, float)):
                    blocks_since = current_block - last_update
                    if blocks_since < stale_threshold:
                        recent_updates += 1
                    else:
                        stale_neurons += 1
            
            # Miner burn %
            try:
                burned = sub.query(module.MinerBurned, params=[netuid])
                if isinstance(burned, dict):
                    miner_burn_pct = float(burned.get('bits', 0)) / 1e9
                else:
                    miner_burn_pct = float(burned) / 1e9 if burned else 0
            except:
                miner_burn_pct = 0
            
            # Emission enabled
            emission_enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
            
            # Total stake
            total_stake = sum((n.total_stake.rao if hasattr(n.total_stake, 'rao') else 0) / 1e9 for n in neurons)
            
            # Activity rate: active neurons / total registered
            activity_rate = (active_neurons / total_neurons * 100) if total_neurons > 0 else 0
            
            # Freshness rate: recently updated / total
            freshness_rate = (recent_updates / total_neurons * 100) if total_neurons > 0 else 0
            
            # Health score (0-100)
            # Active miners matter most (40 pts)
            # Freshness second (30 pts)  
            # Validators with stake (20 pts)
            # Registration demand — log scale (10 pts)

            activity_score = min(40, (activity_rate / 100) * 40)
            freshness_score = min(30, (freshness_rate / 100) * 30)
            validator_score = min(20, (validators / 10) * 20)
            # Registration burn price = miner demand proxy
            # 0 = no registrations (no demand), 5+ TAO = active demand, 50+ = frenzy
            if miner_burn_pct > 0:
                burn_score = min(10, math.log10(miner_burn_pct * 100) / math.log10(1000) * 10)
            else:
                burn_score = 0
            burn_score = max(0, burn_score)
            
            health_score = activity_score + freshness_score + validator_score + burn_score
            
            results.append({
                'netuid': netuid,
                'name': name,
                'price': float(price),
                'emission_enabled': emission_enabled,
                'total_neurons': total_neurons,
                'active_neurons': active_neurons,
                'validators': validators,
                'staked_neurons': staked_neurons,
                'recent_updates': recent_updates,
                'stale_neurons': stale_neurons,
                'activity_rate': round(activity_rate, 1),
                'freshness_rate': round(freshness_rate, 1),
                'miner_burn_pct': round(miner_burn_pct, 3),
                'reg_burn_tao': round(miner_burn_pct, 3),  # renamed: registration burn price in TAO (demand proxy)
                'total_stake': round(total_stake, 0),
                'health_score': round(health_score, 1),
            })
        except Exception as e:
            pass
    
    # Sort by health score
    results.sort(key=lambda x: x['health_score'], reverse=True)
    
    print(f"\n{'SN':>4} {'Name':>15} {'Health':>7} {'Active':>7} {'Total':>6} {'Valid':>6} {'Fresh':>6} {'RegBurn':>8} {'Emit':>5}")
    print("-" * 80)
    for r in results[:30]:
        print(f"  SN{r['netuid']:3d} {r['name']:>15} {r['health_score']:>6.1f} {r['active_neurons']:>3}/{r['total_neurons']:<3} {r['validators']:>5} {r['freshness_rate']:>5.0f}% {r['reg_burn_tao']:>7.3f} {'ON' if r['emission_enabled'] else 'OFF':>4}")
    
    # Save
    with open('data/subnet_health.json', 'w') as f:
        json.dump({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'block': block,
            'results': results,
        }, f, indent=2)
    
    print(f"\nSaved to data/subnet_health.json")
    return results

if __name__ == '__main__':
    scan_health()
