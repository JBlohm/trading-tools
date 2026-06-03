# Trading Tools

A shared repository for trading tools, scripts, and utilities developed for the Blohm Proptrading Group trade desk.

## Overview

This repository is owned by jb (CEO) and maintained by Bob (Software Developer). Testing is performed by Mike and Scott. It provides tools that Linda, Paul, and Tom use in daily trading operations.

## Repository Structure

```
trading-tools/
├── README.md                        # This file
├── requirements.txt                 # Python dependencies
├── .gitignore
├── tools/                           # Trading tools and scripts
│   ├── cancel_order.py
│   ├── flatten_position.py
│   ├── get_exposure.py
│   ├── get_greeks.py
│   ├── get_liquidity_metrics.py
│   ├── get_margin_usage.py
│   ├── get_open_orders.py
│   ├── get_order_status.py
│   ├── get_pnl.py
│   ├── get_positions.py
│   ├── get_quote_snapshot.py
│   ├── get_risk_breaches.py
│   ├── get_risk_limits.py
│   ├── modify_order.py
│   ├── monitor_stops.py
│   ├── place_order.py
│   ├── pretrade_risk_check.py
│   ├── run_pretrade_stress.py
│   ├── trade_proposal.py
│   ├── connection_ids.json          # Broker connection configuration
│   └── risk_limits.json             # Risk limit parameters
├── docs/                            # Documentation and guides
│   ├── cancel_order.md
│   ├── get_greeks.md
│   ├── get_margin_usage.md
│   ├── get_open_orders.md
│   ├── get_positions.md
│   ├── get_quote_snapshot.md
│   └── place_order.md
├── tests/                           # Automated test suite
│   ├── test_cancel_order.py
│   ├── test_flatten_position.py
│   ├── test_get_greeks.py
│   ├── test_get_margin_usage.py
│   ├── test_get_open_orders.py
│   ├── test_get_order_status.py
│   ├── test_get_positions.py
│   ├── test_get_quote_snapshot.py
│   ├── test_get_risk_breaches.py
│   ├── test_modify_order.py
│   ├── test_monitor_stops.py
│   ├── test_place_order.py
│   ├── test_pretrade_risk_check.py
│   └── test_trade_proposal.py
├── option-trading-strategies/       # Option strategy playbooks
│   ├── README.md
│   ├── calendar_spread.md
│   ├── cash_secured_put.md
│   ├── covered_call.md
│   ├── diagonal_spread_pmcc.md
│   ├── iron_butterfly.md
│   ├── iron_condor.md
│   ├── long_call_vertical_spread.md
│   ├── long_put_vertical_spread.md
│   ├── short_call_vertical_spread.md
│   ├── short_put_vertical_spread.md
│   ├── short_straddle.md
│   ├── short_strangle.md
│   └── wheel_strategy.md
└── general-trading-strategies/      # Macro and general strategy playbooks
    ├── README.md
    ├── ptj_crash_playbook_liquidity_breakdown.md
    ├── ptj_macro_breakout_with_asymmetric_risk.md
    ├── ptj_rates_fx_policy_divergence.md
    ├── ptj_risk_overlay_for_all_strategies.md
    └── ptj_trader_execution_layer.md
```

---

## Instructions for Mike (Contributor)

Mike needs a GitHub account with write access and a Personal Access Token.

### One-time Setup

1. Go to https://github.com/settings/tokens/new
2. Token name: `trading-tools-contribute`, scope: `repo`
3. Clone the repository:
   ```bash
   git clone https://github.com/JBlohm/trading-tools.git
   cd trading-tools
   ```
4. When prompted for a password, use the PAT (not your GitHub password)
5. Optional: save credentials so you don't have to re-enter:
   ```bash
   git config --global credential.helper store
   ```

> **Note for jb:** You'll need to add Mike as a collaborator on GitHub. Go to  
> https://github.com/JBlohm/trading-tools/settings/access → "Add people" → enter Mike's GitHub username.

### Making Changes

```bash
git checkout -b feature/your-feature-name
# make your changes
git add .
git commit -m "Short description of change"
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub so the changes can be reviewed before merging.

---

## Instructions for Linda, Paul, and Tom (Users)

No GitHub account is needed — the repository is public. You only need git and Python installed.

### One-time Setup

```bash
git clone https://github.com/JBlohm/trading-tools.git
cd trading-tools
pip install -r requirements.txt   # run once requirements.txt exists
```

### Updating to the Latest Version

```bash
cd trading-tools
git pull
```

That's all you need. No credentials, no GitHub account required.

---

## Team Summary

| Person | Role        | Access needed                                         |
|--------|-------------|-------------------------------------------------------|
| jb     | Owner       | Full admin access (already set up)                    |
| Bob    | Developer   | Push access via PAT (configured)                      |
| Mike   | Contributor | GitHub account + PAT (scope: `repo`) + added by jb   |
| Scott  | Tester      | GitHub account + PAT (scope: `repo`) + added by jb   |
| Linda  | User        | git + Python, no GitHub account needed                |
| Paul   | User        | git + Python, no GitHub account needed                |
| Tom    | User        | git + Python, no GitHub account needed                |
