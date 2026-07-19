# dTAO Trading Research Notes

## Chain Buys

### How they work (from source code: coinbase/run_coinbase.rs)

Every block, 0.5 TAO is emitted to subnets based on emission rate.
Emission share = EMA_price * (1 - miner_burn) / sum(all EMA_prices * (1 - miner_burn))
(Spec 431, July 14: root_prop REMOVED from cross-subnet emission allocation)

Then per subnet:
1. alpha_in = tao_emission / price (how much alpha needed to keep price stable)
2. alpha_injection_cap = root_proportion * alpha_emission (max alpha that can be injected)
3. If alpha_in > cap: cap it, tao_in = cap * price
4. excess_tao (chain buy) = tao_emission - tao_in

Chain-bought alpha is stored as SubnetProtocolAlpha (protocol-owned, recycled at dissolution).

### Key: DON'T calculate. READ from storage.
`SubnetExcessTao` is the actual chain buy amount per block, stored on-chain.
No need to reverse-engineer the formula. Query it directly.

### RootProp is U96F32 (not U64F64)
Value = raw_bits / 2^32
All values are 0-1 (between 0% and ~63%)
- Older subnets (Targon, Chutes, iota): ~14-15%
- Newer subnets (TaoLend, Minos, ORO): ~35-63%

### Current chain buy data (July 19, 2026, block 8658624)
Top subnets by CB vs Pool (strongest price floor):
- SN107 Minos: 0.80%/day (110 TAO/day into 14K pool)
- SN114 SOMA: 0.68%/day
- SN38 ChronoLLM: 0.65%/day
- SN15 ORO: 0.55%/day
- SN97 Albedo: 0.52%/day

15 subnets have ZERO chain buys (price above emission equilibrium):
- SN9 iota, SN1 Apex, SN2 DSperse, SN19 blockmachine, etc.

### What changed recently (specs 421-432, June-July 2026)
- Spec 421 (Jun 23): Emission allocation switched to price-based with miner-burn scaling
- Spec 422 (Jun 23): New subnets start with emissions OFF
- Spec 423 (Jun 25): Limit orders, Balancer AMM replaces Uniswap V3, user LP disabled
- Spec 431 (Jul 14): Root prop REMOVED from emission allocation. Conviction ownership live.
- Spec 432 (Jul 16): Intent privilege system, nested dispatch failure reporting

### How to query
```python
module = bt.storage.SubtensorModule
excess_tao = int(sub.query(module.SubnetExcessTao, params=[netuid])) / 1e9
daily_chain_buy = excess_tao * 7200  # blocks per day
tao_pool = int(sub.query(module.SubnetTAO, params=[netuid])) / 1e9
cb_vs_pool = daily_chain_buy / tao_pool * 100
```

## Emission Toggle (Triumvirate)

### How it works
The triumvirate (chain leadership) can toggle TAO emission on/off per subnet.
- When OFF: no TAO injection, no chain buys, no alpha injection
- Price floor disappears entirely
- This is a kill switch — semi-centralized control

### Current state (July 19, 2026)
- 72 subnets: emission ENABLED
- 57 subnets: emission DISABLED (nearly half the network)
- Allways (SN7) is DISABLED
- Query: `SubnetEmissionEnabled` storage or `hyperparameters.subnet_emission_enabled(netuid)`

### What it means for trading
- Disabled = dead capital, avoid
- Re-enabling = massive catalyst (chain buys resume, price floor returns)
- Triumvirate decisions are the biggest market-moving events
- Need to monitor for status changes (compare snapshots)

## Conviction Locking

### How it works
- Lock alpha tokens to earn conviction (governance weight)
- Lock to subnet owner's hotkey = instant conviction
- Lock to other hotkey = matures over time (60-day half-life)
- Decaying by default (opt-in to perpetual)
- Perpetual locks don't decay, conviction grows toward 100%

### Parameters (post PR #2687)
- UnlockRate: 648,000 blocks (~60 days half-life)
- ConvictionMaturityRate: 648,000 blocks (~60 days half-life)
- Both equal now (simplified coupling)

### Conviction growth (perpetual, non-owner)
| Days | Conviction % |
|---|---|
| 0 | 0% |
| 7 | 7.8% |
| 30 | 29.3% |
| 60 | 50% |
| 90 | 64.6% |
| 365 | 98.5% |

### Locked mass decay (decaying default)
| Days | Locked Mass % |
|---|---|
| 0 | 100% |
| 30 | 70.7% |
| 60 | 50% |
| 180 | 12.5% |
| 365 | 1.5% |

### What it means for trading
- High conviction = committed holders, less sell pressure
- Large locks to owner = governance alignment, bullish
- Unlocking (switching to decaying) = potential sell pressure
- Can detect via `subnet_convictions(netuid)` read

## Pool Mechanics

### AMM formula
Constant product: k = tao_reserve * alpha_reserve (like Uniswap V2)
Price = tao_reserve / alpha_reserve

### Pool depth
- Query via `SubnetTAO` (TAO in pool) and `SubnetAlphaIn` (alpha in pool)
- Estimate from slippage: pool_size = trade_size / (2 * slippage_ratio)
- Or use `quote_stake(netuid, amount_tao)` for exact slippage

### Current pool depths (July 19, 2026)
| Subnet | TAO Pool | Alpha Pool | Est Depth |
|---|---|---|---|
| SN64 Chutes | 209K | 2.6M | ~9K |
| SN4 Targon | 133K | 2.4M | ~8.6K |
| SN51 lium.io | 127K | 2.1M | ~8.6K |
| SN120 Affine | 80K | 1.4M | ~8K |
| SN44 Score | 64K | 1.7M | ~7.6K |
| SN9 iota | 60K | 1.8M | ~7.5K |
| SN107 Minos | 14K | 294K | ~4K |
| SN15 ORO | 9K | 423K | ~3.2K |
| SN7 Allways | 7K | 1.8M | ~2 |
| SN116 TaoLend | 3K | 104K | ~1.4K |

## Emission Rates

### How emissions are determined (3 factors, spec 421)
1. **Price**: EMA price on chain
2. **Root prop**: subnet's Root proportion
3. **Miner Burn**: how much of miners' incentive is burned

Normalized across all subnets to 100%.
0.5 TAO per block total (post first halving December 2025).

### Emission split
- 18% → Subnet owner
- 41% → Miners (based on incentive scores)
- 41% → Validators (split between Alpha holders and Root)

### Alpha injection
- Alpha Injection = TAO Emission Rate / Alpha Price
- Range: 0 to 1 alpha per block
- Cap changed to `alpha_emission * root_prop` (June 2026, spec 421)
- Newer subnets (higher root prop) → more injection
- Older subnets (lower root prop) → less injection, more chain buys

## On-Chain Data Sources (Bittensor SDK v11)

### Working queries
```python
import bittensor as bt
sub = bt.Subtensor(network='finney')

# All alpha prices (free, real-time)
prices = sub.prices.alpha_prices()

# Single subnet price
price = sub.prices.alpha_price(netuid=44)

# Simulate swap (get slippage, fee)
quote = sub.prices.quote_stake(netuid=44, amount_tao=1.0)

# Subnet names
names = sub.subnets.subnet_names()

# Subnet identity (github, discord, url)
identity = sub.subnets.subnet_identity(netuid=44)

# Epoch timing
epoch = sub.epochs.epoch_status(netuid=44)

# Emission enabled/disabled (triumvirate kill switch)
enabled = sub.hyperparameters.subnet_emission_enabled(netuid=44)

# Historical prices (query any past block)
snapshot = sub.at(block=8629333)
prices = snapshot.prices.alpha_prices()
```

### Storage queries (need bt.storage.SubtensorModule)
```python
module = bt.storage.SubtensorModule

# Pool reserves
alpha_in = sub.query(module.SubnetAlphaIn, params=[44]) / 1e9
tao_in_pool = sub.query(module.SubnetTAO, params=[44]) / 1e9

# Chain buy amount (excess TAO per block)
excess_tao = sub.query(module.SubnetExcessTao, params=[44]) / 1e9

# Root proportion
rp = sub.query(module.RootProp, params=[44])
root_prop = rp['bits'] / 1e9 if isinstance(rp, dict) else 0

# Emission per UID (array)
emission = sub.query(module.Emission, params=[44])

# Conviction locks
convictions = sub.reads.subnet_convictions(netuid=44)
```

### Block time
12 seconds per block on mainnet
1 hour = 300 blocks
1 day = 7,200 blocks
1 week = 50,400 blocks

## GitHub PR Monitoring

### Subtensor repo (affects token economics)
- repo: opentensor/subtensor (redirected to RaoFoundation/subtensor)
- Key PRs to watch: emission changes, swap mechanics, conviction, fee changes
- 15 open PRs as of July 19, 2026

### Subnet repos (per-subnet activity)
- Available on-chain via `subnet_identity` → `github_repo` field
- Track commits, issues, PRs for each subnet
- Activity = team still building = bullish signal

## Backtesting Results

### Data
- 3 days, 2-hour intervals, 10 subnets
- Historical prices fetched via `sub.at(block=X).prices.alpha_prices()`

### Strategy comparison
| Strategy | Best case | Notes |
|---|---|---|
| Emission Harvest | +13.6% (lium.io) | Best on 7/10 subnets. 1% daily emission yield. |
| Bollinger (10,2) | +7.1% (ORO) | Caught the swing. Works with volatility. |
| 24h Rotation | +8.0% | Move TAO to best subnet daily. Low fees. |
| Grid (fee-aware) | +0.0% | Refused to trade (grid too tight for 2% fee). |
| Momentum | -12.3% | Bought high, sold low. Worst. |

### Key insight
The 1% swap fee (2% round trip) kills high-frequency strategies.
Strategies that trade less win more.
Emission yield (1%/day) is the real edge.
