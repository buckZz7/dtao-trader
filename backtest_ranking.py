"""Backtest the composite ranking formula.

Tests whether the ranking predicts future price performance.

Approach:
1. Fetch prices N days ago for all subnets
2. Fetch chain buy data N days ago (actual on-chain SubnetExcessTao)
3. Compute ranking AS OF N days ago (using historical data)
4. Compare to actual price change over N days
5. Measure: did high-score subnets outperform low-score subnets?

Also tests individual components:
- Does equilibrium distance predict price change?
- Does code quality predict price change?
- Does conviction locking predict price change?
- Does holder base predict price change?
"""
import bittensor as bt
import json, os, math
from collections import defaultdict
from datetime import datetime, timezone

module = bt.storage.SubtensorModule
BLOCKS_PER_DAY = 7200

def fetch_historical_prices(sub, days_ago):
    """Fetch prices from N days ago."""
    current_block = sub.block()
    target_block = current_block - (days_ago * BLOCKS_PER_DAY)
    
    try:
        snapshot = sub.at(block=target_block)
        prices = snapshot.prices.alpha_prices()
        return {int(k): float(v) for k, v in prices.items()}, target_block
    except Exception as e:
        print(f"Error fetching block {target_block}: {e}")
        return {}, target_block

def fetch_historical_chain_buys(sub, days_ago):
    """Fetch chain buy data from N days ago."""
    current_block = sub.block()
    target_block = current_block - (days_ago * BLOCKS_PER_DAY)
    
    try:
        snapshot = sub.at(block=target_block)
        excess_tao = {}
        for netuid_str in snapshot.prices.alpha_prices():
            netuid = int(netuid_str)
            if netuid == 0:
                continue
            try:
                result = snapshot.query(module.SubnetExcessTao, params=[netuid])
                excess_tao[netuid] = int(result) / 1e9 if result else 0
            except:
                excess_tao[netuid] = 0
        return excess_tao
    except Exception as e:
        print(f"Error fetching chain buys: {e}")
        return {}

def fetch_historical_data(sub, days_ago):
    """Fetch all historical data at once."""
    current_block = sub.block()
    target_block = current_block - (days_ago * BLOCKS_PER_DAY)
    
    print(f"  Fetching data from block {target_block} ({days_ago}d ago)...")
    
    try:
        snapshot = sub.at(block=target_block)
        
        prices = {}
        chain_buys = {}
        tao_pools = {}
        root_props = {}
        emission_enabled = {}
        
        all_prices = snapshot.prices.alpha_prices()
        
        for netuid_str, price in all_prices.items():
            netuid = int(netuid_str)
            if netuid == 0:
                continue
            
            prices[netuid] = float(price)
            
            try:
                result = snapshot.query(module.SubnetExcessTao, params=[netuid])
                chain_buys[netuid] = int(result) / 1e9 if result else 0
            except:
                chain_buys[netuid] = 0
            
            try:
                result = snapshot.query(module.SubnetTAO, params=[netuid])
                tao_pools[netuid] = int(result) / 1e9 if result else 0
            except:
                tao_pools[netuid] = 0
            
            try:
                rp = snapshot.query(module.RootProp, params=[netuid])
                if isinstance(rp, dict):
                    root_props[netuid] = rp.get('bits', 0) / (2**32)
                else:
                    root_props[netuid] = 0
            except:
                root_props[netuid] = 0
            
            try:
                result = snapshot.query(module.SubnetEmissionEnabled, params=[netuid])
                emission_enabled[netuid] = bool(result)
            except:
                emission_enabled[netuid] = False
        
        return {
            'block': target_block,
            'prices': prices,
            'chain_buys': chain_buys,
            'tao_pools': tao_pools,
            'root_props': root_props,
            'emission_enabled': emission_enabled,
        }
    except Exception as e:
        print(f"Error: {e}")
        return None

def compute_historical_ranking(historical_data, current_ranking):
    """Compute ranking score using historical data + current code quality/github data.
    
    Code quality and GitHub activity don't change much in 7 days,
    so we use current data as approximation.
    """
    prices = historical_data['prices']
    chain_buys = historical_data['chain_buys']
    tao_pools = historical_data['tao_pools']
    root_props = historical_data['root_props']
    emission_enabled = historical_data['emission_enabled']
    
    sum_prices = sum(prices.values())
    
    # Load current code quality and github data (stable over short periods)
    code_quality = {}
    if os.path.exists('data/code_quality.json'):
        with open('data/code_quality.json') as f:
            for c in json.load(f):
                code_quality[c['netuid']] = c.get('quality_score', 0)
    
    github_activity = {}
    if os.path.exists('data/github_activity.json'):
        with open('data/github_activity.json') as f:
            for g in json.load(f):
                github_activity[g['netuid']] = g.get('commits_30d', 0) or 0
    
    locked_supply = {}
    if os.path.exists('data/locked_supply.json'):
        with open('data/locked_supply.json') as f:
            for l in json.load(f):
                locked_supply[l['netuid']] = l
    
    rankings = []
    for netuid, price in prices.items():
        if netuid == 0:
            continue
        
        enabled = emission_enabled.get(netuid, False)
        if not enabled:
            continue
        
        root_prop = root_props.get(netuid, 0)
        cb = chain_buys.get(netuid, 0)
        pool = tao_pools.get(netuid, 0)
        
        # Equilibrium (historical)
        emission_rate = price / sum_prices if sum_prices > 0 else 0
        tao_emission = 0.5 * emission_rate
        equilibrium = tao_emission / root_prop if root_prop > 0 else 0
        distance_pct = ((price / equilibrium) - 1) * 100 if equilibrium > 0 else 0
        
        # Valuation score (25 max)
        if distance_pct <= 0:
            val_score = 12.5 + min(12.5, -distance_pct / 35 * 12.5)
        else:
            val_score = 12.5 - min(12.5, distance_pct / 100 * 12.5)
        val_score = max(0, min(25, val_score))
        
        # Code quality (25 max) — using current data
        cq = code_quality.get(netuid, 0)
        code_score = min(25, cq / 100 * 25)
        
        # Conviction (20 max) — using current locked data as approximation
        locked_data = locked_supply.get(netuid, {})
        locked_pct = locked_data.get('locked_pct_circulating', 0)
        locked_score = min(15, locked_pct / 50 * 15) + min(5, locked_data.get('num_lockers', 0) / 10 * 5)
        
        # Activity (15 max) — using current data
        commits = github_activity.get(netuid, 0)
        act_score = min(10, commits / 100 * 10) + min(5, commits / 30 * 5)
        
        # Holder base (15 max) — using current data
        holder_score = 7.5  # Default middle if no data
        
        total = val_score + code_score + locked_score + act_score + holder_score
        
        rankings.append({
            'netuid': netuid,
            'price_then': price,
            'equilibrium': equilibrium,
            'distance_pct': distance_pct,
            'chain_buy': cb,
            'cb_vs_pool': (cb * BLOCKS_PER_DAY / pool * 100) if pool > 0 else 0,
            'total_score': total,
            'val_score': val_score,
            'code_score': code_score,
            'locked_score': locked_score,
            'act_score': act_score,
        })
    
    return rankings

def evaluate_prediction(historical_rankings, current_prices):
    """Evaluate how well the historical ranking predicted price changes."""
    results = []
    
    for r in historical_rankings:
        netuid = r['netuid']
        price_then = r['price_then']
        price_now = current_prices.get(netuid, 0)
        
        if price_then > 0 and price_now > 0:
            price_change = (price_now / price_then - 1) * 100
            r['price_change'] = price_change
            r['price_now'] = price_now
            results.append(r)
    
    return results

def analyze_results(results, label=""):
    """Analyze how well scores predicted price changes."""
    if not results:
        print("No results to analyze")
        return
    
    print(f"\n{'='*80}")
    print(f"BACKTEST RESULTS {label}")
    print(f"{'='*80}")
    
    # Sort by total score
    sorted_by_score = sorted(results, key=lambda x: x['total_score'], reverse=True)
    
    n = len(sorted_by_score)
    top_n = max(5, n // 5)  # Top 20%
    bottom_n = top_n
    
    top = sorted_by_score[:top_n]
    bottom = sorted_by_score[-bottom_n:]
    
    top_avg = sum(r['price_change'] for r in top) / len(top)
    bottom_avg = sum(r['price_change'] for r in bottom) / len(bottom)
    
    print(f"\nTop {top_n} by composite score:")
    print(f"  Avg price change: {top_avg:+.2f}%")
    for r in top[:10]:
        print(f"    SN{r['netuid']:3d} score:{r['total_score']:.1f} → {r['price_change']:+.2f}%")
    
    print(f"\nBottom {bottom_n} by composite score:")
    print(f"  Avg price change: {bottom_avg:+.2f}%")
    for r in bottom[:10]:
        print(f"    SN{r['netuid']:3d} score:{r['total_score']:.1f} → {r['price_change']:+.2f}%")
    
    print(f"\nDelta (top - bottom): {top_avg - bottom_avg:+.2f}%")
    print(f"  Positive delta = ranking has predictive value")
    print(f"  Negative delta = ranking is backwards (avoid high scores)")
    
    # Test individual components
    print(f"\n{'='*80}")
    print(f"COMPONENT ANALYSIS")
    print(f"{'='*80}")
    
    for component, label in [
        ('distance_pct', 'Equilibrium Distance'),
        ('val_score', 'Valuation Score'),
        ('code_score', 'Code Quality'),
        ('locked_score', 'Conviction'),
        ('act_score', 'Activity'),
        ('chain_buy', 'Chain Buy Amount'),
        ('cb_vs_pool', 'CB vs Pool %'),
    ]:
        sorted_by_comp = sorted(results, key=lambda x: x.get(component, 0), reverse=True)
        comp_top = sorted_by_comp[:top_n]
        comp_bottom = sorted_by_comp[-bottom_n:]
        
        comp_top_avg = sum(r['price_change'] for r in comp_top) / len(comp_top) if comp_top else 0
        comp_bottom_avg = sum(r['price_change'] for r in comp_bottom) / len(comp_bottom) if comp_bottom else 0
        
        delta = comp_top_avg - comp_bottom_avg
        signal = "✅ predictive" if delta > 1 else "❌ not predictive" if abs(delta) < 1 else "⚠️ inverse"
        
        print(f"  {label:>20}: top {comp_top_avg:+.2f}%, bottom {comp_bottom_avg:+.2f}%, delta {delta:+.2f}% {signal}")
    
    # Correlation
    print(f"\n{'='*80}")
    print(f"CORRELATION WITH PRICE CHANGE")
    print(f"{'='*80}")
    
    for component in ['total_score', 'val_score', 'code_score', 'locked_score', 'act_score', 'distance_pct', 'chain_buy', 'cb_vs_pool']:
        vals = [(r.get(component, 0), r['price_change']) for r in results]
        corr = pearson_correlation(vals)
        print(f"  {component:>15}: r = {corr:+.3f} ({'strong' if abs(corr) > 0.3 else 'moderate' if abs(corr) > 0.15 else 'weak'})")
    
    return {
        'top_avg': top_avg,
        'bottom_avg': bottom_avg,
        'delta': top_avg - bottom_avg,
    }

def pearson_correlation(pairs):
    """Compute Pearson correlation coefficient."""
    n = len(pairs)
    if n < 3:
        return 0
    
    sum_x = sum(x for x, _ in pairs)
    sum_y = sum(y for _, y in pairs)
    sum_xy = sum(x * y for x, y in pairs)
    sum_x2 = sum(x * x for x, _ in pairs)
    sum_y2 = sum(y * y for _, y in pairs)
    
    numerator = n * sum_xy - sum_x * sum_y
    denominator = math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))
    
    if denominator == 0:
        return 0
    
    return numerator / denominator

def main():
    sub = bt.Subtensor(network='finney')
    print(f"Block: {sub.block()}")
    
    # Current prices
    current_prices_raw = sub.prices.alpha_prices()
    current_prices = {int(k): float(v) for k, v in current_prices_raw.items()}
    
    # Test multiple time horizons
    horizons = [7, 3, 1]  # Days
    
    summary = {}
    
    for days in horizons:
        print(f"\n{'='*80}")
        print(f"TESTING {days}-DAY HORIZON")
        print(f"{'='*80}")
        
        historical = fetch_historical_data(sub, days)
        if not historical:
            continue
        
        rankings = compute_historical_ranking(historical, None)
        results = evaluate_prediction(rankings, current_prices)
        
        if len(results) < 10:
            print(f"Only {len(results)} subnets with data, skipping")
            continue
        
        result = analyze_results(results, f"({days}d)")
        summary[f'{days}d'] = result
    
    # Final summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"\n{'Horizon':>10} {'Top avg':>10} {'Bottom avg':>10} {'Delta':>10} {'Predictive?':>15}")
    print("-" * 60)
    for horizon, r in summary.items():
        pred = "✅ YES" if r['delta'] > 1 else "❌ NO" if r['delta'] < -1 else "⚠️ WEAK"
        print(f"  {horizon:>8} {r['top_avg']:>+9.2f}% {r['bottom_avg']:>+9.2f}% {r['delta']:>+9.2f}% {pred:>15}")

if __name__ == '__main__':
    main()
