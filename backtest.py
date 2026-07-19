"""Backtesting framework for dTAO trading strategies.

Tests multiple strategies on historical price data:
1. Grid trading (range buy low, sell high)
2. Mean reversion (buy below MA, sell above)
3. Momentum (ride trends)
4. Emission harvesting (stake to capture emissions)
5. Cross-subnet rotation (move TAO between subnets)

Usage:
  python backtest.py --strategy grid --netuid 15
  python backtest.py --strategy all --netuid 15
  python backtest.py --strategy all --compare
"""
import json, os, argparse
from datetime import datetime

# ── Load historical data ────────────────────────────────────────

def load_prices(filepath='data/historical_3day_hourly.json'):
    """Load historical prices from JSON file."""
    with open(filepath) as f:
        data = json.load(f)
    
    prices = {}
    for netuid_str, samples in data.items():
        netuid = int(netuid_str)
        prices[netuid] = [(s['hours_ago'], s['price']) for s in samples]
        # Sort by time (oldest first)
        prices[netuid].sort(key=lambda x: x[0], reverse=True)
    
    return prices

# ── Strategies ──────────────────────────────────────────────────

class GridStrategy:
    """Grid trading: buy low, sell high at regular intervals."""
    
    def __init__(self, n_grids=10, capital=100.0):
        self.n_grids = n_grids
        self.capital = capital
    
    def backtest(self, prices):
        """Backtest grid strategy on price series.
        
        Args:
            prices: list of (time, price) tuples
        
        Returns:
            dict with trade results
        """
        if len(prices) < 2:
            return {'error': 'not enough data'}
        
        price_values = [p[1] for p in prices]
        min_price = min(price_values)
        max_price = max(price_values)
        
        if min_price == max_price:
            return {'error': 'no price movement'}
        
        # Set grid levels
        grid_size = (max_price - min_price) / self.n_grids
        grid_levels = [min_price + i * grid_size for i in range(self.n_grids + 1)]
        
        # Simulate: start with TAO, buy alpha at each grid level
        tao_balance = self.capital
        alpha_balance = 0.0
        trades = []
        last_grid = -1
        
        for time, price in prices:
            # Find which grid level we're at
            current_grid = min(int((price - min_price) / grid_size), self.n_grids)
            
            if current_grid > last_grid and last_grid >= 0:
                # Price went up: sell alpha at higher grid
                if alpha_balance > 0:
                    sell_amount = alpha_balance * (current_grid - last_grid) / self.n_grids
                    tao_received = sell_amount * price
                    # 1% swap fee
                    tao_received *= 0.99
                    tao_balance += tao_received
                    alpha_balance -= sell_amount
                    trades.append({
                        'type': 'sell',
                        'price': price,
                        'alpha': sell_amount,
                        'tao': tao_received,
                        'time': time,
                    })
            elif current_grid < last_grid:
                # Price went down: buy alpha at lower grid
                if tao_balance > 0:
                    buy_amount_tao = tao_balance * (last_grid - current_grid) / self.n_grids
                    alpha_received = buy_amount_tao / price
                    # 1% swap fee
                    alpha_received *= 0.99
                    tao_balance -= buy_amount_tao
                    alpha_balance += alpha_received
                    trades.append({
                        'type': 'buy',
                        'price': price,
                        'alpha': alpha_received,
                        'tao': buy_amount_tao,
                        'time': time,
                    })
            
            last_grid = current_grid
        
        # Close position: sell remaining alpha at last price
        final_price = prices[-1][1]
        final_tao = tao_balance + (alpha_balance * final_price * 0.99)
        
        return {
            'strategy': 'grid',
            'n_grids': self.n_grids,
            'start_tao': self.capital,
            'final_tao': round(final_tao, 4),
            'profit_pct': round((final_tao / self.capital - 1) * 100, 2),
            'n_trades': len(trades),
            'min_price': min_price,
            'max_price': max_price,
            'range_pct': round((max_price / min_price - 1) * 100, 2),
        }

class MeanReversionStrategy:
    """Mean reversion: buy below moving average, sell above."""
    
    def __init__(self, window=5, threshold=0.02, capital=100.0):
        self.window = window
        self.threshold = threshold  # 2% below/above MA
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < self.window + 1:
            return {'error': 'not enough data'}
        
        tao_balance = self.capital
        alpha_balance = 0.0
        trades = []
        
        price_values = [p[1] for p in prices]
        
        for i in range(self.window, len(prices)):
            # Compute moving average
            ma = sum(price_values[i-self.window:i]) / self.window
            price = price_values[i]
            time = prices[i][0]
            
            dev = (price - ma) / ma if ma > 0 else 0
            
            if dev < -self.threshold and tao_balance > 0:
                # Price below MA: buy
                alpha = (tao_balance / price) * 0.99
                alpha_balance += alpha
                tao_balance = 0
                trades.append({'type': 'buy', 'price': price, 'time': time})
            elif dev > self.threshold and alpha_balance > 0:
                # Price above MA: sell
                tao = alpha_balance * price * 0.99
                tao_balance += tao
                alpha_balance = 0
                trades.append({'type': 'sell', 'price': price, 'time': time})
        
        final_price = prices[-1][1]
        final_tao = tao_balance + (alpha_balance * final_price * 0.99)
        
        return {
            'strategy': 'mean_reversion',
            'window': self.window,
            'threshold': self.threshold,
            'start_tao': self.capital,
            'final_tao': round(final_tao, 4),
            'profit_pct': round((final_tao / self.capital - 1) * 100, 2),
            'n_trades': len(trades),
        }

class MomentumStrategy:
    """Momentum: buy when price is rising, sell when falling."""
    
    def __init__(self, window=3, capital=100.0):
        self.window = window
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < self.window + 2:
            return {'error': 'not enough data'}
        
        tao_balance = self.capital
        alpha_balance = 0.0
        trades = []
        price_values = [p[1] for p in prices]
        
        for i in range(self.window, len(prices)):
            # Check if trending up
            recent = price_values[i-self.window:i]
            current = price_values[i]
            avg_recent = sum(recent) / len(recent)
            
            trending_up = current > avg_recent * 1.005  # 0.5% above recent avg
            trending_down = current < avg_recent * 0.995
            
            time = prices[i][0]
            
            if trending_up and tao_balance > 0:
                alpha = (tao_balance / current) * 0.99
                alpha_balance += alpha
                tao_balance = 0
                trades.append({'type': 'buy', 'price': current, 'time': time})
            elif trending_down and alpha_balance > 0:
                tao = alpha_balance * current * 0.99
                tao_balance += tao
                alpha_balance = 0
                trades.append({'type': 'sell', 'price': current, 'time': time})
        
        final_price = prices[-1][1]
        final_tao = tao_balance + (alpha_balance * final_price * 0.99)
        
        return {
            'strategy': 'momentum',
            'window': self.window,
            'start_tao': self.capital,
            'final_tao': round(final_tao, 4),
            'profit_pct': round((final_tao / self.capital - 1) * 100, 2),
            'n_trades': len(trades),
        }

class BuyHoldStrategy:
    """Buy and hold: buy at start, sell at end."""
    
    def __init__(self, capital=100.0):
        self.capital = capital
    
    def backtest(self, prices):
        if len(prices) < 2:
            return {'error': 'not enough data'}
        
        buy_price = prices[0][1]
        sell_price = prices[-1][1]
        
        alpha = (self.capital / buy_price) * 0.99
        final_tao = alpha * sell_price * 0.99
        
        return {
            'strategy': 'buy_hold',
            'start_tao': self.capital,
            'final_tao': round(final_tao, 4),
            'profit_pct': round((final_tao / self.capital - 1) * 100, 2),
            'n_trades': 1,
            'buy_price': buy_price,
            'sell_price': sell_price,
        }

# ── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', default='all', choices=['grid', 'mean_reversion', 'momentum', 'buy_hold', 'all'])
    parser.add_argument('--netuid', type=int, default=None)
    parser.add_argument('--compare', action='store_true', help='Compare all subnets')
    parser.add_argument('--data', default='data/historical_3day_hourly.json')
    args = parser.parse_args()
    
    prices_data = load_prices(args.data)
    
    names = {116:'TaoLend',107:'Minos',95:'Actual',9:'iota',15:'ORO',44:'Score',51:'lium.io',4:'Targon',64:'Chutes',120:'Affine'}
    
    if args.compare:
        # Compare all subnets
        print(f"\n{'='*80}")
        print(f"STRATEGY COMPARISON ACROSS ALL SUBNETS")
        print(f"{'='*80}")
        
        strategies = {
            'grid': GridStrategy(),
            'mean_rev': MeanReversionStrategy(),
            'momentum': MomentumStrategy(),
            'buy_hold': BuyHoldStrategy(),
        }
        
        print(f"\n{'SN':>4} {'Name':>12} {'Grid%':>8} {'MeanR%':>8} {'Mom%':>8} {'HODL%':>8} {'Range%':>8}")
        print("-" * 65)
        
        for netuid in sorted(prices_data.keys()):
            prices = prices_data[netuid]
            name = names.get(netuid, f'SN{netuid}')
            
            results = {}
            for sname, strat in strategies.items():
                r = strat.backtest(prices)
                results[sname] = r.get('profit_pct', 0)
            
            price_values = [p[1] for p in prices]
            range_pct = ((max(price_values) - min(price_values)) / min(price_values)) * 100 if price_values else 0
            
            print(f"  SN{netuid:3d} {name:>12} {results['grid']:>+7.1f}% {results['mean_rev']:>+7.1f}% {results['momentum']:>+7.1f}% {results['buy_hold']:>+7.1f}% {range_pct:>7.1f}%")
    
    elif args.netuid:
        # Detailed backtest for one subnet
        netuid = args.netuid
        if netuid not in prices_data:
            print(f"SN{netuid} not in data")
            return
        
        prices = prices_data[netuid]
        name = names.get(netuid, f'SN{netuid}')
        
        print(f"\n{'='*60}")
        print(f"BACKTEST: SN{netuid} ({name})")
        print(f"{'='*60}")
        print(f"Samples: {len(prices)}")
        print(f"Period: {prices[0][0]:.0f}h ago to {prices[-1][0]:.0f}h ago")
        
        price_values = [p[1] for p in prices]
        print(f"Price range: {min(price_values):.6f} - {max(price_values):.6f} TAO")
        
        strategies_all = {
            'Grid (10 levels)': GridStrategy(n_grids=10),
            'Grid (5 levels)': GridStrategy(n_grids=5),
            'Grid (20 levels)': GridStrategy(n_grids=20),
            'Mean Reversion (5h, 2%)': MeanReversionStrategy(window=5, threshold=0.02),
            'Mean Reversion (3h, 1%)': MeanReversionStrategy(window=3, threshold=0.01),
            'Momentum (3h)': MomentumStrategy(window=3),
            'Momentum (5h)': MomentumStrategy(window=5),
            'Buy & Hold': BuyHoldStrategy(),
        }
        
        print(f"\n{'Strategy':>30} {'Profit':>10} {'Trades':>8} {'Final TAO':>12}")
        print("-" * 65)
        
        for name, strat in strategies_all.items():
            r = strat.backtest(prices)
            if 'error' in r:
                print(f"  {name:>30} ERROR: {r['error']}")
            else:
                print(f"  {name:>30} {r['profit_pct']:>+9.1f}% {r['n_trades']:>8} {r['final_tao']:>12.4f}")
    
    else:
        print("Specify --netuid or --compare")

if __name__ == '__main__':
    main()
