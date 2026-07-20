"""Scientific subnet valuation ranking.

Combines all data layers into a composite undervaluation score (0-100):

1. Price vs equilibrium (25 pts) — chain's own valuation
2. Code quality (25 pts) — real LOC, tests, docs, architecture
3. Conviction locks (20 pts) — % of supply locked by diamond hands
4. Holder base (15 pts) — distribution, diversification, dump risk
5. Development activity (15 pts) — GitHub commits, recency

Higher score = more undervalued (better buy opportunity).
Lower score = overvalued or risky.
"""
import bittensor as bt
import json, os, math
from collections import defaultdict
from datetime import datetime, timezone

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200

def normalize(value, min_val, max_val):
    """Normalize to 0-1 range."""
    if max_val == min_val:
        return 0.5
    return max(0, min(1, (value - min_val) / (max_val - min_val)))

def score_valuation(distance_pct, emission_enabled, miner_burn_pct=0):
    """Score based on distance from equilibrium price.
    STRONGEST predictor (r=+0.661 7d). Weight: 35 pts.
    
    Uses naive equilibrium (no burn weighting) — backtest proved this is
    more predictive than the burn-aware formula. Miner burn is tracked
    separately as a standalone signal, not baked into valuation.
    Emission-off subnets still get scored — re-enablement is a catalyst.
    """
    if not emission_enabled:
        if distance_pct <= 0:
            score = 8.75 + normalize(-distance_pct, 0, 35) * 8.75
        else:
            score = 8.75 - normalize(distance_pct, 0, 100) * 8.75
        return max(0, min(17.5, score))
    
    if distance_pct <= 0:
        score = 17.5 + normalize(-distance_pct, 0, 35) * 17.5
    else:
        score = 17.5 - normalize(distance_pct, 0, 100) * 17.5
    return max(0, min(35, score))

def score_code_quality(quality_data):
    """Score from code quality assessment.
    Moderate predictor (r=0.205). Weight kept at 20.
    """
    if not quality_data or 'error' in quality_data:
        return 3
    q = quality_data.get('quality_score', 0)
    return normalize(q, 0, 100) * 20

def score_conviction(locked_pct_circulating, num_lockers):
    """Score from conviction locks.
    Weak short-term predictor (r=0.103) but matters for risk/safety.
    Weight reduced from 20 to 15.
    """
    locked_score = normalize(min(locked_pct_circulating, 50), 0, 50) * 10
    locker_score = normalize(min(num_lockers, 10), 0, 10) * 5
    return locked_score + locker_score

def score_holder_base(holder_data):
    """Score from holder base quality.
    Not directly tested but proxy for safety. Weight kept at 15.
    """
    if not holder_data or 'error' in holder_data:
        return 5
    
    num_holders = holder_data.get('num_holders', 0)
    gini = holder_data.get('gini', 1.0)
    top1_pct = holder_data.get('top1_pct', 100)
    
    gini_score = (1 - normalize(gini, 0.5, 0.98)) * 7
    holder_score = normalize(min(num_holders, 100), 5, 100) * 4
    conc_score = (1 - normalize(top1_pct, 20, 100)) * 4
    
    return gini_score + holder_score + conc_score

def score_activity(commits_30d, commits_7d):
    """Score from GitHub activity.
    INVERSE predictor (r=-0.231). Subnets with lots of commits performed WORSE.
    Weight reduced from 15 to 5. Activity is not a buy signal.
    """
    # Very low weight — activity is inverse predictive
    commit_score = normalize(min(commits_30d, 50), 0, 50) * 3
    recent_score = normalize(min(commits_7d, 15), 0, 15) * 2
    return commit_score + recent_score

def score_concept(concept_data):
    """Score from concept assessment (necessity, TAM, moat, execution).
    Returns 0 if not assessed. Dashboard shows '—' for unscored.
    """
    if not concept_data or not concept_data.get('concept_score'):
        return 0
    score = concept_data.get('concept_score', 0)
    return normalize(score, 0, 100) * 10

def score_flow(flow_data):
    """Score from 7-day net stake flow vs pool size.
    Predictive (r=+0.191). Subnets with net inflow outperform.
    Positive flow = bullish. Negative flow = bearish.
    
    Filters out deregistration contamination: if stake dropped to near-zero
    or pool is tiny (< 500 TAO), the flow data is unreliable.
    """
    if not flow_data:
        return 5  # Default neutral if no data
    
    stake_now = flow_data.get('stake_now', 0)
    stake_7d = flow_data.get('stake_7d_ago', 0)
    pool = flow_data.get('pool_size', 0)
    
    # Skip deregistered/contaminated subnets
    if stake_now < 100 or pool < 500:
        return 5  # Neutral — don't penalize or reward
    
    flow_pct = flow_data.get('flow_vs_pool', 0)
    
    # Cap extreme values (dereg artifacts, tiny pools)
    flow_pct = max(-100, min(100, flow_pct))
    
    # Map flow to score:
    # +20% flow vs pool = max score (10)
    # 0% = neutral (5)
    # -20% = min score (0)
    score = 5 + normalize(flow_pct, -20, 20) * 5
    return max(0, min(10, score))

def compute_ranking():
    """Compute composite ranking for all emission-enabled subnets."""
    sub = bt.Subtensor(network='finney')
    block = sub.block()
    
    all_prices = sub.prices.alpha_prices()
    names = sub.subnets.subnet_names()
    
    # Load data files
    concept_scores = {}
    if os.path.exists('data/concept_scores.json'):
        with open('data/concept_scores.json') as f:
            for c in json.load(f):
                concept_scores[c['netuid']] = c
    
    # Load flow cache (updated daily by flow_scanner.py)
    flow_cache = {}
    if os.path.exists('data/flow_cache.json'):
        with open('data/flow_cache.json') as f:
            flow_data = json.load(f)
            for r in flow_data.get('results', []):
                flow_cache[r['netuid']] = r
    
    code_quality = {}
    if os.path.exists('data/code_quality.json'):
        with open('data/code_quality.json') as f:
            for c in json.load(f):
                code_quality[c['netuid']] = c
    
    github_activity = {}
    if os.path.exists('data/github_activity.json'):
        with open('data/github_activity.json') as f:
            for g in json.load(f):
                github_activity[g['netuid']] = g
    
    holder_base = {}
    if os.path.exists('data/holder_base_full.json'):
        with open('data/holder_base_full.json') as f:
            for h in json.load(f):
                holder_base[h['netuid']] = h
    elif os.path.exists('data/holder_base.json'):
        with open('data/holder_base.json') as f:
            for h in json.load(f):
                holder_base[h['netuid']] = h
    
    locked_supply = {}
    if os.path.exists('data/locked_supply.json'):
        with open('data/locked_supply.json') as f:
            for l in json.load(f):
                locked_supply[l['netuid']] = l
    
    # Load subnet health data (from health_scanner.py)
    health_data = {}
    if os.path.exists('data/subnet_health.json'):
        with open('data/subnet_health.json') as f:
            health_raw = json.load(f)
            for h in health_raw.get('results', []):
                health_data[h['netuid']] = h

    # Get all conviction locks for locker count
    locks_by_subnet = defaultdict(int)
    try:
        result = sub.query_map(module.Lock, params=[])
        for item in result:
            if hasattr(item, 'key'):
                key = item.key
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                key = item[0]
            else:
                continue
            if isinstance(key, (tuple, list)) and len(key) >= 2:
                netuid = key[1]
                locks_by_subnet[netuid] += 1
    except:
        pass
    
    # Compute sum of prices for equilibrium calculation
    # Note: The chain uses burn-weighted sum (spec 431), but backtest showed
    # the naive sum (no burn) has stronger price prediction (r=+0.661 vs +0.047).
    # The market trades on naive equilibrium, not burn-adjusted.
    # Miner burn is tracked separately as a standalone signal.
    sum_prices = sum(float(v) for v in all_prices.values() if v > 0)
    
    rankings = []
    
    for netuid_str, price in all_prices.items():
        netuid = int(netuid_str)
        if netuid == 0:
            continue
        
        try:
            name = names.get(netuid_str, f"SN{netuid}")
            spot_price = float(price)
            
            # Emission status
            emission_enabled = bool(sub.query(module.SubnetEmissionEnabled, params=[netuid]))
            
            # Root prop (U96F32)
            rp_raw = sub.query(module.RootProp, params=[netuid])
            rp_bits = rp_raw.get('bits', 0) if isinstance(rp_raw, dict) else int(rp_raw)
            root_prop = rp_bits / (2**32)
            
            # Equilibrium price (naive — no burn weighting)
            # Backtest proved naive formula is more predictive (r=+0.661 vs +0.047)
            emission_rate = spot_price / sum_prices if sum_prices > 0 else 0
            tao_emission = 0.5 * emission_rate
            equilibrium = tao_emission / root_prop if root_prop > 0 else 0
            distance_pct = ((spot_price / equilibrium) - 1) * 100 if equilibrium > 0 else 0
            
            # Chain buy data
            excess_tao = int(sub.query(module.SubnetExcessTao, params=[netuid])) / 1e9
            daily_cb = excess_tao * BLOCKS_PER_DAY
            tao_pool = int(sub.query(module.SubnetTAO, params=[netuid])) / 1e9
            cb_vs_pool = (daily_cb / tao_pool * 100) if tao_pool > 0 else 0
            
            # Supply data
            alpha_out = int(sub.query(module.SubnetAlphaOut, params=[netuid])) / 1e9
            proto_alpha = int(sub.query(module.SubnetProtocolAlpha, params=[netuid])) / 1e9
            
            # Locked alpha from conviction
            locked_data = locked_supply.get(netuid, {})
            locked_alpha = locked_data.get('locked_alpha', 0)
            locked_pct = locked_data.get('locked_pct_circulating', 0)
            num_lockers = locks_by_subnet.get(netuid, 0)
            
            # Equilibrium velocity: how fast price moves toward equilibrium
            # Protocol velocity = chain buys / pool * 2 (AMM) * float amplification
            # Flow velocity = 7d net stake flow as % of pool (from flow_cache)
            flow_vs_pool_raw = flow_cache.get(netuid, {}).get('flow_vs_pool', 0)
            flow_vs_pool_capped = max(-50, min(50, flow_vs_pool_raw))
            
            float_ratio = max(0.1, 1 - locked_pct / 100)
            amp = min(5, 1 / float_ratio)
            prot_vel = cb_vs_pool * 2 * amp  # %/day upward (only when below eq)
            flow_vel = flow_vs_pool_capped  # %/day (7d avg, capped)
            
            # Categorize
            if abs(distance_pct) < 2:
                eq_cat = 'at_eq'
                eq_label = 'At Eq'
            elif distance_pct < 0:
                # Below equilibrium
                if prot_vel > 0 and flow_vel >= 0:
                    eq_cat = 'fast'
                    eq_label = '↑Fast'
                elif prot_vel > 0 and flow_vel < 0:
                    if prot_vel > abs(flow_vel):
                        eq_cat = 'slow'
                        eq_label = '↑Slow'
                    else:
                        eq_cat = 'div'
                        eq_label = '↓Div'
                elif prot_vel == 0 and flow_vel > 0:
                    eq_cat = 'flow_up'
                    eq_label = '↑Flow'
                else:
                    eq_cat = 'stuck'
                    eq_label = 'Stuck'
            else:
                # Above equilibrium, no chain buys
                if flow_vel < -0.1:
                    eq_cat = 'correcting'
                    eq_label = '↓Corr'
                elif flow_vel > 0.1:
                    eq_cat = 'bubble'
                    eq_label = 'Bubble'
                else:
                    eq_cat = 'floating'
                    eq_label = 'Float'
            
            # Compute scores
            miner_burn_pct = health_data.get(netuid, {}).get('miner_burn_pct', 0)
            s_valuation = score_valuation(distance_pct, emission_enabled, miner_burn_pct)
            s_code = score_code_quality(code_quality.get(netuid, {}))
            s_conviction = score_conviction(locked_pct, num_lockers)
            s_holders = score_holder_base(holder_base.get(netuid, {}))
            
            gh = github_activity.get(netuid, {})
            s_activity = score_activity(gh.get('commits_30d', 0) or 0, gh.get('commits_7d', 0) or 0)
            s_concept = score_concept(concept_scores.get(netuid, {}))
            s_flow = score_flow(flow_cache.get(netuid, {}))
            
            total_score = (s_valuation + s_code + s_conviction + s_holders + s_activity + s_concept + s_flow) / 110 * 100
            
            # Verdict
            if not emission_enabled:
                if total_score >= 50:
                    verdict = "WATCH (emit off)"
                elif total_score >= 35:
                    verdict = "PENDING"
                else:
                    verdict = "INACTIVE"
            elif total_score >= 65:
                verdict = "STRONG BUY"
            elif total_score >= 50:
                verdict = "BUY"
            elif total_score >= 35:
                verdict = "FAIR"
            elif total_score >= 20:
                verdict = "AVOID"
            else:
                verdict = "RISKY"
            
            rankings.append({
                'netuid': netuid,
                'name': name,
                'price': spot_price,
                'emission_enabled': emission_enabled,
                'equilibrium': equilibrium,
                'distance_pct': round(distance_pct, 1),
                'cb_vs_pool': round(cb_vs_pool, 2),
                'daily_cb': round(daily_cb, 1),
                'tao_pool': round(tao_pool, 0),
                'locked_pct': round(locked_pct, 1),
                'num_lockers': num_lockers,
                'proto_pct': round((proto_alpha / (alpha_out + int(sub.query(module.SubnetAlphaIn, params=[netuid])) / 1e9) * 100) if alpha_out > 0 else 0, 1),
                'commits_30d': gh.get('commits_30d', 0) or 0,
                'quality_score': code_quality.get(netuid, {}).get('quality_score', 0),
                'num_holders': holder_base.get(netuid, {}).get('num_holders', 0),
                'gini': holder_base.get(netuid, {}).get('gini', 0),
                'concept_score': concept_scores.get(netuid, {}).get('concept_score', 0),
                'concept_verdict': concept_scores.get(netuid, {}).get('verdict', ''),
                'concept_summary': concept_scores.get(netuid, {}).get('summary', ''),
                'concept_necessity': concept_scores.get(netuid, {}).get('necessity_score', 0),
                'concept_necessity_reason': concept_scores.get(netuid, {}).get('necessity_reasoning', ''),
                'concept_tam': concept_scores.get(netuid, {}).get('tam_score', 0),
                'concept_tam_reason': concept_scores.get(netuid, {}).get('tam_reasoning', ''),
                'concept_moat': concept_scores.get(netuid, {}).get('moat_score', 0),
                'concept_moat_reason': concept_scores.get(netuid, {}).get('moat_reasoning', ''),
                'concept_execution': concept_scores.get(netuid, {}).get('execution_score', 0),
                'concept_execution_reason': concept_scores.get(netuid, {}).get('execution_reasoning', ''),
                'flow_vs_pool': flow_cache.get(netuid, {}).get('flow_vs_pool', 0),
                'net_flow': flow_cache.get(netuid, {}).get('net_flow', 0),
                'health_score': round(health_data.get(netuid, {}).get('health_score', 0), 1),
                'health_active': health_data.get(netuid, {}).get('active_neurons', 0),
                'health_total_neurons': health_data.get(netuid, {}).get('total_neurons', 0),
                'health_validators': health_data.get(netuid, {}).get('validators', 0),
                'health_staked': health_data.get(netuid, {}).get('staked_neurons', 0),
                'health_freshness': health_data.get(netuid, {}).get('freshness_rate', 0),
                'health_activity_rate': health_data.get(netuid, {}).get('activity_rate', 0),
                'health_stale': health_data.get(netuid, {}).get('stale_neurons', 0),
                'health_burn': health_data.get(netuid, {}).get('miner_burn_pct', 0),  # now correctly U96F32 percentage
                'miner_burn_pct': round(health_data.get(netuid, {}).get('miner_burn_pct', 0), 1),  # emission burn %
                'health_activity_pts': health_data.get(netuid, {}).get('activity_pts', 0),
                'health_freshness_pts': health_data.get(netuid, {}).get('freshness_pts', 0),
                'health_validator_pts': health_data.get(netuid, {}).get('validator_pts', 0),
                'health_burn_pts': health_data.get(netuid, {}).get('burn_pts', 0),
                'eq_vel_cat': eq_cat,
                'eq_vel_label': eq_label,
                'eq_prot_vel': round(prot_vel, 2),
                'eq_flow_vel': round(flow_vel, 1),
                'eq_amp': round(amp, 2),
                'scores': {
                    'valuation': round(s_valuation, 1),
                    'code': round(s_code, 1),
                    'conviction': round(s_conviction, 1),
                    'holders': round(s_holders, 1),
                    'activity': round(s_activity, 1),
                    'concept': round(s_concept, 1),
                    'flow': round(s_flow, 1),
                },
                'total_score': round(total_score, 1),
                'verdict': verdict,
            })
        except Exception as e:
            pass
    
    # Sort by total score (most undervalued first)
    rankings.sort(key=lambda x: x['total_score'], reverse=True)
    
    # Add rank
    for i, r in enumerate(rankings):
        r['rank'] = i + 1
    
    return rankings

def main():
    print("Computing composite subnet ranking...")
    rankings = compute_ranking()
    
    print(f"\n{'='*120}")
    print(f"SCIENTIFIC SUBNET RANKING — Most Undervalued to Most Overvalued")
    print(f"{'='*120}")
    print(f"\n{'Rk':>3} {'SN':>4} {'Name':>15} {'Price':>9} {'Dist%':>7} {'Val':>5} {'Code':>5} {'Conv':>5} {'Hold':>5} {'Act':>5} {'TOTAL':>6} {'Verdict':>12}")
    print("-" * 100)
    
    for r in rankings:
        s = r['scores']
        print(f"  {r['rank']:>2} SN{r['netuid']:3d} {r['name']:>15} {r['price']:>9.5f} {r['distance_pct']:>+6.1f}% {s['valuation']:>5.1f} {s['code']:>5.1f} {s['conviction']:>5.1f} {s['holders']:>5.1f} {s['activity']:>5.1f} {r['total_score']:>6.1f} {r['verdict']:>12}")
    
    # Save
    os.makedirs('data', exist_ok=True)
    with open('data/rankings.json', 'w') as f:
        json.dump(rankings, f, indent=2)
    print(f"\nSaved to data/rankings.json")
    
    # Also save for dashboard
    dashboard_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'rankings': rankings,
    }
    with open('docs/rankings-data.json', 'w') as f:
        json.dump(dashboard_data, f, indent=2)
    print(f"Saved dashboard data to docs/rankings-data.json")

if __name__ == '__main__':
    main()
