"""dTAO Quant System — monitors all Bittensor subnets for trading opportunities.

Data layers:
1. On-chain: prices, pool depth, emissions, conviction locks, stake flows
2. GitHub: subtensor PRs, subnet repo activity
3. Social: X announcements (plugs in from PostProphet infrastructure)

The agent watches all layers, correlates signals, and surfaces opportunities.
"""
import bittensor as bt
import json, os, time, math
from datetime import datetime, timezone
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────

SUBNET_REGISTRY_FILE = 'data/subnet_registry.json'
PRICE_HISTORY_FILE = 'data/price_history.jsonl'
ALERTS_FILE = 'data/alerts.jsonl'

# ── Subnet Registry ─────────────────────────────────────────────

def build_subnet_registry(sub):
    """Build a registry of all subnets with on-chain identity data."""
    print("Building subnet registry...")
    
    all_subnets = sub.subnets.subnets()
    names = sub.subnets.subnet_names()
    prices = sub.prices.alpha_prices()
    
    registry = {}
    for sn_info in all_subnets:
        if not isinstance(sn_info, dict):
            continue
        netuid = sn_info.get('netuid')
        if netuid is None:
            continue
        
        netuid_str = str(netuid)
        name = names.get(netuid_str, f"SN{netuid}")
        price = float(prices.get(netuid, 0))
        
        # Get identity
        try:
            identity = sub.subnets.subnet_identity(netuid=netuid)
        except:
            identity = None
        
        # Get subnet details (tempo, burn, neuron count)
        try:
            details = sub.subnets.subnet(netuid=netuid)
        except:
            details = None
        
        registry[netuid] = {
            'netuid': netuid,
            'name': name,
            'price': price,
            'identity': identity if isinstance(identity, dict) else {},
            'github_repo': identity.get('github_repo', '') if isinstance(identity, dict) else '',
            'description': identity.get('description', '') if isinstance(identity, dict) else '',
            'subnet_url': identity.get('subnet_url', '') if isinstance(identity, dict) else '',
            'discord': identity.get('discord', '') if isinstance(identity, dict) else '',
        }
    
    # Save
    os.makedirs('data', exist_ok=True)
    with open(SUBNET_REGISTRY_FILE, 'w') as f:
        json.dump(registry, f, indent=2)
    
    print(f"  Registered {len(registry)} subnets")
    return registry

# ── Price Snapshot ──────────────────────────────────────────────

def take_snapshot(sub, registry):
    """Take a snapshot of all subnet prices + pool state."""
    block = sub.block()
    prices = sub.prices.alpha_prices()
    
    snapshot = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'block': block,
        'subnets': {},
    }
    
    for netuid, info in registry.items():
        price = float(prices.get(netuid, 0))
        
        # Get quote for 1 TAO (pool depth indicator)
        try:
            quote = sub.prices.quote_stake(netuid=netuid, amount_tao=1.0)
            if isinstance(quote, dict):
                alpha_out = float(quote.get('alpha_out', 0))
                slippage = float(quote.get('slippage_pct', 0))
            else:
                alpha_out = 0
                slippage = 0
        except:
            alpha_out = 0
            slippage = 0
        
        snapshot['subnets'][netuid] = {
            'price': price,
            'alpha_for_1_tao': alpha_out,
            'slippage_1_tao': slippage,
        }
    
    return snapshot

def save_snapshot(snapshot):
    """Append snapshot to history file."""
    with open(PRICE_HISTORY_FILE, 'a') as f:
        f.write(json.dumps(snapshot) + '\n')

def load_price_history(n_snapshots=100):
    """Load recent price history."""
    if not os.path.exists(PRICE_HISTORY_FILE):
        return []
    
    with open(PRICE_HISTORY_FILE) as f:
        lines = f.readlines()
    
    snapshots = []
    for line in lines[-n_snapshots:]:
        if line.strip():
            snapshots.append(json.loads(line))
    
    return snapshots

# ── Analysis ────────────────────────────────────────────────────

def analyze_price_moves(history, registry):
    """Detect significant price movements."""
    if len(history) < 2:
        return []
    
    alerts = []
    latest = history[-1]
    previous = history[-2]
    
    for netuid, info in registry.items():
        curr = latest['subnets'].get(str(netuid), {}).get('price', 0)
        prev = previous['subnets'].get(str(netuid), {}).get('price', 0)
        
        if prev == 0 or curr == 0:
            continue
        
        change_pct = ((curr / prev) - 1) * 100
        name = info.get('name', f'SN{netuid}')
        
        if abs(change_pct) > 2.0:  # 2% threshold
            alerts.append({
                'type': 'price_move',
                'netuid': netuid,
                'name': name,
                'change_pct': round(change_pct, 2),
                'prev_price': prev,
                'curr_price': curr,
                'timestamp': latest['timestamp'],
            })
    
    return alerts

def analyze_trends(history, registry, lookback=6):
    """Detect trending subnets over multiple snapshots."""
    if len(history) < lookback:
        return []
    
    alerts = []
    recent = history[-lookback:]
    
    for netuid, info in registry.items():
        prices = []
        for snap in recent:
            p = snap['subnets'].get(str(netuid), {}).get('price', 0)
            if p > 0:
                prices.append(p)
        
        if len(prices) < 3:
            continue
        
        # Simple trend: compare first half avg to second half avg
        mid = len(prices) // 2
        first_half = sum(prices[:mid]) / mid if mid > 0 else 0
        second_half = sum(prices[mid:]) / (len(prices) - mid) if len(prays) > mid else 0
        
        if first_half > 0:
            trend_pct = ((second_half / first_half) - 1) * 100
        else:
            continue
        
        name = info.get('name', f'SN{netuid}')
        
        if abs(trend_pct) > 5.0:  # 5% trend over lookback period
            alerts.append({
                'type': 'trend',
                'netuid': netuid,
                'name': name,
                'trend_pct': round(trend_pct, 2),
                'direction': 'up' if trend_pct > 0 else 'down',
                'start_price': prices[0],
                'end_price': prices[-1],
                'timestamp': recent[-1]['timestamp'],
            })
    
    return alerts

def analyze_pool_depth(history, registry):
    """Detect pool depth changes (liquidity shifts)."""
    if len(history) < 2:
        return []
    
    alerts = []
    latest = history[-1]
    previous = history[-2]
    
    for netuid, info in registry.items():
        curr_slip = latest['subnets'].get(str(netuid), {}).get('slippage_1_tao', 0)
        prev_slip = previous['subnets'].get(str(netuid), {}).get('slippage_1_tao', 0)
        
        if prev_slip == 0 or curr_slip == 0:
            continue
        
        # Increasing slippage = decreasing liquidity (someone pulled out)
        # Decreasing slippage = increasing liquidity (someone added)
        slip_change = curr_slip - prev_slip
        name = info.get('name', f'SN{netuid}')
        
        if abs(slip_change) > 1.0:  # 1% slippage change
            alerts.append({
                'type': 'liquidity_shift',
                'netuid': netuid,
                'name': name,
                'slip_change': round(slip_change, 2),
                'direction': 'liquidity_decreasing' if slip_change > 0 else 'liquidity_increasing',
                'timestamp': latest['timestamp'],
            })
    
    return alerts

def rank_opportunities(registry, history):
    """Rank subnets by trading opportunity score."""
    if len(history) < 2:
        return []
    
    latest = history[-1]
    scores = []
    
    for netuid, info in registry.items():
        # Get recent prices
        prices = []
        for snap in history[-12:]:  # Last 12 snapshots (~1 hour if 5min interval)
            p = snap['subnets'].get(str(netuid), {}).get('price', 0)
            if p > 0:
                prices.append(p)
        
        if len(prices) < 3:
            continue
        
        # Volatility (range / min)
        price_range = (max(prices) - min(prices)) / min(prices) if min(prices) > 0 else 0
        
        # Trend
        if len(prices) >= 4:
            mid = len(prices) // 2
            trend = (sum(prices[mid:]) / len(prices[mid:])) / (sum(prices[:mid]) / len(prices[:mid])) - 1 if sum(prices[:mid]) > 0 else 0
        else:
            trend = 0
        
        # Liquidity (inverse of slippage)
        slippage = latest['subnets'].get(str(netuid), {}).get('slippage_1_tao', 100)
        liquidity_score = max(0, 10 - slippage) / 10  # 0-1, higher = more liquid
        
        # Opportunity score: high volatility + good liquidity + clear trend
        opportunity_score = price_range * liquidity_score * (1 + abs(trend))
        
        name = info.get('name', f'SN{netuid}')
        price = prices[-1]
        
        scores.append({
            'netuid': netuid,
            'name': name,
            'price': price,
            'volatility_pct': round(price_range * 100, 2),
            'trend_pct': round(trend * 100, 2),
            'liquidity_score': round(liquidity_score, 2),
            'opportunity_score': round(opportunity_score, 4),
            'github': info.get('github_repo', ''),
        })
    
    scores.sort(key=lambda x: x['opportunity_score'], reverse=True)
    return scores

# ── Main ────────────────────────────────────────────────────────

def run_monitor():
    """Main monitor loop."""
    sub = bt.Subtensor(network='finney')
    print(f"Connected: {sub.network}, block: {sub.block()}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    
    # Build or load registry
    if os.path.exists(SUBNET_REGISTRY_FILE):
        with open(SUBNET_REGISTRY_FILE) as f:
            registry = json.load(f)
        print(f"Loaded registry: {len(registry)} subnets")
    else:
        registry = build_subnet_registry(sub)
    
    # Take snapshot
    print("\nTaking snapshot...")
    snapshot = take_snapshot(sub, registry)
    save_snapshot(snapshot)
    print(f"Saved snapshot: {len(snapshot['subnets'])} subnets")
    
    # Load history
    history = load_price_history(100)
    print(f"Price history: {len(history)} snapshots")
    
    # Analyze
    alerts = []
    alerts.extend(analyze_price_moves(history, registry))
    alerts.extend(analyze_trends(history, registry))
    alerts.extend(analyze_pool_depth(history, registry))
    
    # Rank opportunities
    opportunities = rank_opportunities(registry, history)
    
    # Display
    print(f"\n{'='*70}")
    print(f"ALERTS ({len(alerts)})")
    print(f"{'='*70}")
    for a in alerts:
        if a['type'] == 'price_move':
            print(f"  {'🟢' if a['change_pct'] > 0 else '🔴'} {a['name']:>12} (SN{a['netuid']}): {a['change_pct']:+.1f}% (${a['prev_price']:.6f} -> ${a['curr_price']:.6f})")
        elif a['type'] == 'trend':
            print(f"  {'📈' if a['direction'] == 'up' else '📉'} {a['name']:>12} (SN{a['netuid']}): {a['direction']} trend {a['trend_pct']:+.1f}%")
        elif a['type'] == 'liquidity_shift':
            print(f"  {'💧' if 'increasing' in a['direction'] else '🚨'} {a['name']:>12} (SN{a['netuid']}): {a['direction']} (slippage {a['slip_change']:+.1f}%)")
    
    print(f"\n{'='*70}")
    print(f"TOP OPPORTUNITIES")
    print(f"{'='*70}")
    print(f"{'SN':>4} {'Name':>12} {'Price':>12} {'Vol%':>8} {'Trend%':>8} {'Liq':>5} {'Score':>8}")
    print("-" * 65)
    for o in opportunities[:15]:
        print(f"  SN{o['netuid']:3d} {o['name']:>12} {o['price']:>12.6f} {o['volatility_pct']:>7.1f}% {o['trend_pct']:>+7.1f}% {o['liquidity_score']:>4.1f} {o['opportunity_score']:>8.4f}")
    
    # Save alerts
    if alerts:
        with open(ALERTS_FILE, 'a') as f:
            for a in alerts:
                f.write(json.dumps(a) + '\n')
    
    return alerts, opportunities

if __name__ == '__main__':
    run_monitor()
