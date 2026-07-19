"""Advanced backtesting: fee-aware strategies with multiple approaches.

Strategies:
1. Grid (fee-aware: only trade when profit > fee)
2. Mean Reversion (multiple windows)
3. Momentum (trend following)
4. Emission Harvesting (stake to capture emissions, sell alpha)
5. Cross-Subnet Rotation (move TAO to best-performing subnet)
6. Grid + Emission (grid trade AND capture emissions)
7. Bollinger Band (buy at lower band, sell at upper)
8. RSI (oversold/overbought)
9. Pairs Trading (cointegrated subnets)
10. Buy & Hold (baseline)

All strategies account for 1% swap fee (2% round trip).
"""
import json, os, math
from collections import deque

SWAP_FEE = 0.01  # 1% per swap

# ── Load data ───────────────────────────────────────────────────

def load_prices(filepath='data/historical_3day_hourly.json'):
    with open(filepath) as f:
        data = json.load(f)
    
    prices = {}
    for netuid_str, samples in data.items():
        netuid = int(netuid_str)
        # Sort oldest first (highest hours_ago = oldest)
        sorted_samples = sorted(samples, key=lambda x: x['hours_ago'], reverse=True)
        prices[netuid] = [(s['hours_ago'], s['price']) for s in sorted_samples]
    
    return prices

# ── Helpers ─────────────────────────────────────────────────────

def moving_average(values, window):
    if len(values) < window:
        return sum(values) / len(values) if values else 0
    return sum(values[-window:]) / window

def std_dev(values, window):
    if len(values) < window:
        return 0
    ma = sum(values[-window:]) / window
    variance = sum((v - ma) ** 2 for v in values[-window:]) / window
    return math.sqrt(variance)

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ── Strategy: Grid (fee-aware) ──────────────────────────────────

class GridStrategy:
    def __init__(self, n_grids=10, capital=100.0, min_profit_pct=0.03):
        self.n_grids = n_grids
        self.capital = capital
        self.min_profit_pct = min_profit_pct  # Only trade if grid profit > this
    
    def backtest(self, prices):
        if len(prices) < 2:
            return {'error': 'not enough data'}
        
        pv = [p[1] for p in prices]
        lo, hi = min(pv), max(pv)
        if lo == hi:
            return {'error': 'no movement'}
        
        grid_size = (hi - lo) / self.n_grids
        # Only trade if grid profit > 2x fee (need to cover round trip)
        grid_profit_pct = grid_size / lo
        if grid_profit_pct < SWAP_FEE * 2 * 1.5:  # 3% min for 2% fee
            return {
                'strategy': 'grid_fee_aware',
                'profit_pct': 0,
                'n_trades': 0,
                'note': f'grid too tight ({grid_profit_pct*100:.1f}% < {SWAP_FEE*2*1.5*100:.1f}%)'
            }
        
        tao = self.capital
        alpha = 0.0
        trades = 0
        last_grid = -1
        
        for time, price in prices:
            grid = min(int((price - lo) / grid_size), self.n_grids)
            
            if grid > last_grid and last_grid >= 0 and alpha > 0:
                # Sell
                sell = alpha * (grid - last_grid) / self.n_grids
                tao += sell * price * (1 - SWAP_FEE)
                alpha -= sell
                trades += 1
            elif grid < last_grid and tao > 0:
                # Buy
                buy_tao = tao * (last_grid - grid) / self.n_grids
                alpha += (buy_tao / price) * (1 - SWAP_FEE)
                tao -= buy_tao
                trades += 1
            
            last_grid = grid
        
        final = tao + alpha * prices[-1][1] * (1 - SWAP_FEE)
        return {
            'strategy': 'grid_fee_aware',
            'profit_pct': round((final / self.capital - 1) * 100, 2),
            'n_trades': trades,
            'final_tao': round(final, 4),
            'grid_size_pct': round(grid_profit_pct * 100, 2),
        }

# ── Strategy: Emission Harvesting ───────────────────────────────

class EmissionHarvestStrategy:
    """Stake TAO, capture emissions, sell alpha at optimal time.
    
    Every block, the subnet emits alpha + TAO to the pool.
    Stakers earn alpha proportional to their stake.
    The question: when to sell the earned alpha?
    
    Assumption: emission rate is ~2 alpha/block (from dTAO docs).
    With stake weight proportional to pool share.
    """
    def __init__(self, capital=100.0, sell_threshold=0.05, hold_days=3):
        self.capital = capital
        self.sell_threshold = sell_threshold  # Sell when alpha price rises 5%
        self.hold_days = hold_days
    
    def backtest(self, prices):
        if len(prices) < 2:
            return {'error': 'not enough data'}
        
        # Simplified: assume we stake at start, earn emissions, sell at end
        # Real emission rate depends on pool share, which we don't have
        # Use price appreciation as proxy for emission value
        
        tao = self.capital
        buy_price = prices[0][1]
        
        # Buy alpha
        alpha = (tao / buy_price) * (1 - SWAP_FEE)
        tao = 0
        
        # Simulate emission accrual (simplified)
        # Assume 1% daily emission on stake (rough estimate)
        hours = prices[-1][0] - prices[0][0]
        days = abs(hours / 24)
        emission_rate = 0.01 * days  # 1% per day
        alpha += alpha * emission_rate
        
        # Sell at end
        sell_price = prices[-1][1]
        final = alpha * sell_price * (1 - SWAP_FEE)
        
        return {
            'strategy': 'emission_harvest',
            'profit_pct': round((final / self.capital - 1) * 100, 2),
            'n_trades': 2,
            'final_tao': round(final, 4),
            'emission_gain_pct': round(emission_rate * 100, 2),
            'days_held': round(days, 1),
        }

# ── Strategy: Cross-Subnet Rotation ─────────────────────────────

class RotationStrategy:
    """Move TAO to the best-performing subnet every N hours.
    
    Each period, check which subnet had the best recent performance.
    Move all TAO there. Repeat.
    """
    def __init__(self, capital=100.0, rebalance_hours=12, lookback=4):
        self.capital = capital
        self.rebalance_hours = rebalance_hours
        self.lookback = lookback  # Hours to look back for performance
    
    def backtest_multi(self, all_prices):
        """Backtest across multiple subnets.
        
        all_prices: {netuid: [(time, price), ...]}
        """
        # Align time points across subnets
        timepoints = sorted(set(t for prices in all_prices.values() for t, _ in prices))
        
        tao = self.capital
        current_netuid = None
        alpha = 0
        trades = 0
        last_rebalance = -999
        
        for t in timepoints:
            # Get prices at this timepoint for all subnets
            current_prices = {}
            for netuid, prices in all_prices.items():
                for pt, price in prices:
                    if pt == t:
                        current_prices[netuid] = price
                        break
            
            if not current_prices:
                continue
            
            # Check if time to rebalance
            if t - last_rebalance >= self.rebalance_hours or current_netuid is None:
                # Sell current position
                if current_netuid and alpha > 0 and current_netuid in current_prices:
                    tao = alpha * current_prices[current_netuid] * (1 - SWAP_FEE)
                    alpha = 0
                    trades += 1
                
                # Find best performing subnet (lookback)
                best_netuid = None
                best_return = -999
                
                for netuid in current_prices:
                    # Get price lookback hours ago
                    lookback_t = t - self.lookback
                    past_price = None
                    for pt, price in all_prices.get(netuid, []):
                        if abs(pt - lookback_t) < 2:
                            past_price = price
                            break
                    
                    if past_price and past_price > 0:
                        ret = (current_prices[netuid] / past_price - 1)
                        if ret > best_return:
                            best_return = ret
                            best_netuid = netuid
                
                # Buy into best subnet
                if best_netuid and tao > 0:
                    alpha = (tao / current_prices[best_netuid]) * (1 - SWAP_FEE)
                    tao = 0
                    current_netuid = best_netuid
                    trades += 1
                    last_rebalance = t
        
        # Close position
        if current_netuid and alpha > 0:
            final_prices = {}
            for netuid, prices in all_prices.items():
                if prices:
                    final_prices[netuid] = prices[-1][1]
            
            if current_netuid in final_prices:
                tao = alpha * final_prices[current_netuid] * (1 - SWAP_FEE)
        
        return {
            'strategy': 'cross_subnet_rotation',
            'profit_pct': round((tao / self.capital - 1) * 100, 2),
            'n_trades': trades,
            'final_tao': round(tao, 4),
            'rebalance_hours': self.rebalance_hours,
        }

# ── Strategy: Bollinger Bands ───────────────────────────────────

class BollingerStrategy:
    def __init__(self, window=10, num_std=2, capital=100.0):
        self.window = window
        self.num_std = num_std
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < self.window + 1:
            return {'error': 'not enough data'}
        
        tao = self.capital
        alpha = 0
        trades = 0
        pv = [p[1] for p in prices]
        
        for i in range(self.window, len(prices)):
            window_prices = pv[i-self.window:i]
            ma = sum(window_prices) / self.window
            sd = std_dev(window_prices, self.window)
            upper = ma + self.num_std * sd
            lower = ma - self.num_std * sd
            price = pv[i]
            time = prices[i][0]
            
            if price < lower and tao > 0:
                alpha = (tao / price) * (1 - SWAP_FEE)
                tao = 0
                trades += 1
            elif price > upper and alpha > 0:
                tao = alpha * price * (1 - SWAP_FEE)
                alpha = 0
                trades += 1
        
        final = tao + alpha * prices[-1][1] * (1 - SWAP_FEE)
        return {
            'strategy': 'bollinger',
            'profit_pct': round((final / self.capital - 1) * 100, 2),
            'n_trades': trades,
            'final_tao': round(final, 4),
        }

# ── Strategy: RSI ───────────────────────────────────────────────

class RSIStrategy:
    def __init__(self, period=14, oversold=30, overbought=70, capital=100.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < self.period + 2:
            return {'error': 'not enough data'}
        
        tao = self.capital
        alpha = 0
        trades = 0
        pv = [p[1] for p in prices]
        
        for i in range(self.period + 1, len(prices)):
            rsi_val = rsi(pv[:i+1], self.period)
            price = pv[i]
            
            if rsi_val < self.oversold and tao > 0:
                alpha = (tao / price) * (1 - SWAP_FEE)
                tao = 0
                trades += 1
            elif rsi_val > self.overbought and alpha > 0:
                tao = alpha * price * (1 - SWAP_FEE)
                alpha = 0
                trades += 1
        
        final = tao + alpha * prices[-1][1] * (1 - SWAP_FEE)
        return {
            'strategy': 'rsi',
            'profit_pct': round((final / self.capital - 1) * 100, 2),
            'n_trades': trades,
            'final_tao': round(final, 4),
        }

# ── Strategy: Grid + Emissions ──────────────────────────────────

class GridEmissionStrategy:
    """Grid trade AND capture emissions while holding alpha."""
    
    def __init__(self, n_grids=10, capital=100.0):
        self.n_grids = n_grids
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < 2:
            return {'error': 'not enough data'}
        
        pv = [p[1] for p in prices]
        lo, hi = min(pv), max(pv)
        if lo == hi:
            return {'error': 'no movement'}
        
        grid_size = (hi - lo) / self.n_grids
        tao = self.capital
        alpha = 0.0
        trades = 0
        last_grid = -1
        
        # Stake at first price (buy alpha)
        alpha = (tao / prices[0][1]) * (1 - SWAP_FEE)
        tao = 0
        trades += 1
        
        # Emission accrual (1% per day simplified)
        hours = abs(prices[-1][0] - prices[0][0])
        days = hours / 24
        emission_gain = 0.01 * days
        
        for time, price in prices:
            grid = min(int((price - lo) / grid_size), self.n_grids)
            
            if grid > last_grid and last_grid >= 0 and alpha > 0:
                sell = alpha * (grid - last_grid) / self.n_grids
                tao += sell * price * (1 - SWAP_FEE)
                alpha -= sell
                trades += 1
            elif grid < last_grid and tao > 0:
                buy_tao = tao * (last_grid - grid) / self.n_grids
                alpha += (buy_tao / price) * (1 - SWAP_FEE)
                tao -= buy_tao
                trades += 1
            
            last_grid = grid
        
        # Add emission gains to remaining alpha
        alpha *= (1 + emission_gain)
        
        final = tao + alpha * prices[-1][1] * (1 - SWAP_FEE)
        return {
            'strategy': 'grid_emission',
            'profit_pct': round((final / self.capital - 1) * 100, 2),
            'n_trades': trades,
            'final_tao': round(final, 4),
            'emission_gain_pct': round(emission_gain * 100, 2),
        }

# ── Strategy: Buy & Hold ────────────────────────────────────────

class BuyHoldStrategy:
    def __init__(self, capital=100.0):
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < 2:
            return {'error': 'not enough data'}
        
        alpha = (self.capital / prices[0][1]) * (1 - SWAP_FEE)
        final = alpha * prices[-1][1] * (1 - SWAP_FEE)
        return {
            'strategy': 'buy_hold',
            'profit_pct': round((final / self.capital - 1) * 100, 2),
            'n_trades': 1,
            'final_tao': round(final, 4),
        }

# ── Main ────────────────────────────────────────────────────────

def main():
    prices_data = load_prices()
    names = {116:'TaoLend',107:'Minos',95:'Actual',9:'iota',15:'ORO',44:'Score',51:'lium.io',4:'Targon',64:'Chutes',120:'Affine'}
    
    print(f"\n{'='*90}")
    print(f"COMPREHENSIVE STRATEGY BACKTEST")
    print(f"{'='*90}")
    print(f"Data: 3 days, 2-hour intervals, 10 subnets")
    print(f"Swap fee: {SWAP_FEE*100}% per swap ({SWAP_FEE*2*100}% round trip)")
    
    # Per-subnet strategies
    single_strategies = {
        'Grid (fee-aware)': GridStrategy(n_grids=10),
        'Grid (5 levels)': GridStrategy(n_grids=5),
        'Grid (20 levels)': GridStrategy(n_grids=20),
        'Bollinger (10,2)': BollingerStrategy(window=10, num_std=2),
        'Bollinger (5,1.5)': BollingerStrategy(window=5, num_std=1.5),
        'RSI (14,30/70)': RSIStrategy(period=14),
        'RSI (7,25/75)': RSIStrategy(period=7, oversold=25, overbought=75),
        'Emission Harvest': EmissionHarvestStrategy(),
        'Grid+Emission': GridEmissionStrategy(n_grids=10),
        'Buy & Hold': BuyHoldStrategy(),
    }
    
    print(f"\n{'SN':>4} {'Name':>12}", end='')
    for sname in single_strategies:
        print(f" {sname:>16}", end='')
    print(f" {'Range%':>8}")
    print("-" * (18 + len(single_strategies) * 17 + 9))
    
    for netuid in sorted(prices_data.keys()):
        prices = prices_data[netuid]
        name = names.get(netuid, f'SN{netuid}')
        
        pv = [p[1] for p in prices]
        range_pct = ((max(pv) - min(pv)) / min(pv)) * 100
        
        print(f"  SN{netuid:3d} {name:>12}", end='')
        
        for sname, strat in single_strategies.items():
            r = strat.backtest(prices)
            pct = r.get('profit_pct', 0)
            print(f" {pct:>+15.1f}%", end='')
        
        print(f" {range_pct:>7.1f}%")
    
    # Cross-subnet rotation (uses all subnets together)
    print(f"\n{'='*90}")
    print(f"CROSS-SUBNET STRATEGIES")
    print(f"{'='*90}")
    
    rotation_strategies = [
        ('Rotation (12h, 4h lookback)', RotationStrategy(rebalance_hours=12, lookback=4)),
        ('Rotation (6h, 2h lookback)', RotationStrategy(rebalance_hours=6, lookback=2)),
        ('Rotation (24h, 12h lookback)', RotationStrategy(rebalance_hours=24, lookback=12)),
    ]
    
    print(f"\n{'Strategy':>35} {'Profit':>10} {'Trades':>8} {'Final TAO':>12}")
    print("-" * 70)
    
    for name, strat in rotation_strategies:
        r = strat.backtest_multi(prices_data)
        print(f"  {name:>35} {r['profit_pct']:>+9.1f}% {r['n_trades']:>8} {r['final_tao']:>12.4f}")
    
    # Find best strategy per subnet
    print(f"\n{'='*90}")
    print(f"BEST STRATEGY PER SUBNET")
    print(f"{'='*90}")
    
    for netuid in sorted(prices_data.keys()):
        prices = prices_data[netuid]
        name = names.get(netuid, f'SN{netuid}')
        
        best_name = None
        best_pct = -999
        
        for sname, strat in single_strategies.items():
            r = strat.backtest(prices)
            pct = r.get('profit_pct', -999)
            if pct > best_pct:
                best_pct = pct
                best_name = sname
        
        print(f"  SN{netuid:3d} {name:>12}: {best_name:>20} ({best_pct:+.1f}%)")

if __name__ == '__main__':
    main()
