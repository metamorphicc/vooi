# VOOI Funding Regime Rotation Bot

This project implements a delta-neutral funding-regime rotation strategy for VOOI-supported perps venues.

The strategy is built for automation, but live order placement is disabled by default. Start with:

```powershell
python -m vooi_bot scan --config config.example.toml
python -m vooi_bot propose --config config.example.toml
python -m vooi_bot paper --config config.example.toml --once
python -m vooi_bot status --config config.example.toml
```

To use the real VOOI MCP endpoint from a normal Python process, set:

```powershell
[Environment]::SetEnvironmentVariable("VOOI_API_TOKEN", "your_token", "User")
```

The bot expects VOOI's streamable HTTP MCP endpoint:

```text
https://vooi-api-app.fly.dev/mcp
```

## Strategy

`FundingRegimeRotation` is an automated carry allocator. It continuously ranks cross-venue funding opportunities, opens a hedged long/short position only in the best stable regime, and rotates capital only when a materially better regime appears.

The bot:

- ranks VOOI funding strategies;
- rejects low-liquidity or unstable spikes;
- estimates slippage for both legs when using the real MCP backend;
- enforces minimum hold time before rotation;
- exits when a regime degrades or falls out of the ranking band;
- tracks candidate and paper positions in SQLite;
- attempts an immediate reduce-only cleanup if a live entry partially fills and the other leg is rejected;
- blocks live trading unless explicitly enabled in config.

## Commands

Read-only / paper:

```powershell
python -m vooi_bot scan --config config.example.toml
python -m vooi_bot propose --config config.example.toml
python -m vooi_bot paper --config config.example.toml --once
python -m vooi_bot run --config config.example.toml --interval-sec 900
python -m vooi_bot status --config config.example.toml
```

VOOI account checks:

```powershell
python -m vooi_bot accounts --config config.example.toml --json
python -m vooi_bot positions --config config.example.toml --json
python -m vooi_bot report --config config.example.toml
```

Emergency close for a single detected position:

```powershell
python -m vooi_bot flatten-position --config config.example.toml --exchange lighter --asset PENDLE --confirm FLATTEN_OPEN_POSITION
```

Guarded live, only after funding venues and setting `allow_live = true`:

```powershell
python -m vooi_bot propose --config config.example.toml --mode live
python -m vooi_bot live-once --config config.example.toml --confirm EXECUTE_LIVE_ORDERS
python -m vooi_bot live-run --config config.example.toml --confirm EXECUTE_LIVE_ORDERS --interval-sec 900
```

Live mode has three gates:

- `risk.allow_live = true`
- exact confirmation phrase
- positive available balances above `risk.min_total_available_usd`
- positive available margin on every venue needed by the selected two-leg trade

The default live sizing is now `risk.quote_size_usd = 40`, meaning roughly 40 USDC notional per leg. With `risk.max_leverage = 3` and `risk.min_margin_buffer_ratio = 1.10`, the bot requires about 14.67 USDC of free margin on each selected venue before opening a new position.
Before every new live entry, the bot calls `set_leverage` for each selected market using `risk.max_leverage`, so new positions should open at the configured leverage instead of venue defaults.

`live-run` stops on any unhandled trading/API/risk error. You can also stop it by creating:

```text
data/STOP_LIVE
```

For the first funded run, prefer:

```powershell
python -m vooi_bot live-run --config config.example.toml --confirm EXECUTE_LIVE_ORDERS --interval-sec 900 --iterations 1
```

## Secrets and Wallet Keys

Do not put a private key, seed phrase, or raw wallet secret in this repository.

The current trading flow uses the VOOI MCP server through `VOOI_API_TOKEN`. Order tools are exposed by VOOI MCP; the bot does not need a wallet private key in its config. If a future deposit/signing flow is needed, use an external wallet or signer. The optional `TRADING_WALLET_ADDRESS` is only a public address for deposit calldata or reporting.

This is not financial advice. Perps trading can lose money quickly, especially if one leg fills and the other fails.
The bot now tries to close that partial leg automatically, but the first live run should still be watched manually.

## Performance Reporting

When a managed live position is closed by the bot, it records a `live_position_stats` row in SQLite and the JSONL event log. The stats include:

- funding PnL captured from the live position snapshot immediately before close;
- price PnL from unrealized/realized PnL fields;
- net PnL, hold time, close reason, and return percentages;
- a linear estimate for larger `quote_size_usd` values such as 500 and 1000.

Show the summary with:

```powershell
python -m vooi_bot report --config config.example.toml
python -m vooi_bot report --config config.example.toml --json
python -m vooi_bot report --config config.example.toml --scale-usd 500 1000 5000
```
