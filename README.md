# dTAO Trader

Quant analysis system for trading dTAO (dynamic TAO) subnet tokens on Bittensor.

Not an auto-trading bot. Monitors all 129 subnets, surfaces opportunities, and provides data for human execution.

## What it does

- **Chain buy analysis** — reads `SubnetExcessTao` directly from on-chain storage to identify subnets with the strongest protocol buy pressure (price floor)
- **Emission monitoring** — tracks which subnets have emissions enabled/disabled (triumvirate kill switch)
- **Pool depth** — queries TAO and alpha reserves for every subnet AMM pool
- **Price tracking** — logs all subnet alpha prices every 5 minutes for historical analysis
- **Backtesting** — tests trading strategies against historical data (emission harvesting, grid, Bollinger, rotation)
- **Dashboard** — live web dashboard with all subnet data, searchable and filterable

## Architecture

```
On-chain data (Bittensor SDK v11, free, real-time)
    ↓
Data collector (every 5 min)
    ↓
dashboard-data.json (committed to repo by GitHub Actions)
    ↓
GitHub Pages dashboard (accessible from anywhere)
```

## Key findings

- **Chain buys** are permanent protocol-level buy pressure. SN107 Minos has 0.80%/day (strongest floor)
- **57 of 129 subnets** have emissions disabled (no chain buy support, dead capital)
- **Root prop** (`U96F32`) determines alpha injection cap. Older subnets ~15%, newer ~40-63%
- **Emission harvesting** is the best strategy (1%/day yield + price appreciation)
- The 1% swap fee kills high-frequency strategies — trade less, hold more

## Files

- `dashboard_collector.py` — collects all subnet data, writes dashboard JSON
- `price_logger.py` — continuous price logger (every 5 min)
- `backtest.py` / `backtest_advanced.py` — backtesting framework
- `docs/dashboard.html` — live web dashboard
- `docs/dashboard-data.json` — latest subnet data (auto-updated)
- `docs/research.md` — full research notes (chain buys, emissions, conviction)

## Dashboard

Live at: https://prophettensor.github.io/dtao-trader/

Data updates hourly via GitHub Actions.

## On-chain queries

```python
import bittensor as bt
sub = bt.Subtensor(network='finney')
module = bt.storage.SubtensorModule

# All alpha prices
prices = sub.prices.alpha_prices()

# Chain buy per block (actual on-chain value)
excess_tao = int(sub.query(module.SubnetExcessTao, params=[44])) / 1e9

# Emission status (triumvirate kill switch)
enabled = sub.query(module.SubnetEmissionEnabled, params=[44])

# Pool reserves
tao_pool = int(sub.query(module.SubnetTAO, params=[44])) / 1e9
alpha_pool = int(sub.query(module.SubnetAlphaIn, params=[44])) / 1e9

# Root proportion (U96F32: bits / 2^32)
rp = sub.query(module.RootProp, params=[44])
root_prop = rp['bits'] / (2**32)
```
