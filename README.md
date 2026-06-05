# Trading Tools

A shared repository for trading tools, scripts, and utilities developed for the Blohm Proptrading Group trade desk.

## Overview

This repository is maintained by Bob (Software Developer) and tested by Mike (Software Tester). It provides tools that Linda, Paul, and Tom use in daily trading operations.

## Repository Structure

```
trading-tools/
├── README.md           # This file
├── requirements.txt    # Python dependencies
├── tools/              # Trading tools and scripts
└── docs/               # Documentation and guides
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

## Tools

| Tool | Description | Read-only |
|------|-------------|-----------|
| `get_positions.py` | Current TWS positions | ✓ |
| `get_greeks.py` | Portfolio-level option Greeks | ✓ |
| `get_margin_usage.py` | Account margin usage | ✓ |
| `get_open_orders.py` | Open/pending orders | ✓ |
| `get_quote_snapshot.py` | Pre-trade quote, session, and liquidity snapshot | ✓ |
| `get_risk_breaches.py` | Portfolio risk limit breach detector | ✓ |
| `get_order_status.py` | Order lifecycle status and fill details | ✓ |
| `monitor_stops.py` | Stop and target order monitor | ✓ |
| `detect_crash_liquidity_breakdown.py` | PTJ Crash Playbook / Liquidity Breakdown signal detector | ✓ |
| `trade_proposal.py` | Structured trade proposal with pre-trade checks | — |
| `place_order.py` | Place orders with pre-trade risk gate | — |
| `cancel_order.py` | Cancel open orders | — |
| `modify_order.py` | Modify existing orders | — |
| `flatten_position.py` | Emergency position flatten | — |

## Docs

| Document | Covers |
|----------|--------|
| `docs/get_positions.md` | Position reader |
| `docs/get_greeks.md` | Greeks reader |
| `docs/get_margin_usage.md` | Margin usage reader |
| `docs/get_open_orders.md` | Open orders reader |
| `docs/get_quote_snapshot.md` | Quote snapshot |
| `docs/place_order.md` | Order placement |
| `docs/cancel_order.md` | Order cancellation |
| `docs/detect_crash_liquidity_breakdown.md` | Crash playbook detector — thresholds, JSON schema, failure modes |

---

## Team Summary

| Person | Role        | Access needed                                         |
|--------|-------------|-------------------------------------------------------|
| jb     | Owner       | Full admin access (already set up)                    |
| Bob    | Developer   | Push access via PAT (configured)                      |
| Mike   | Contributor | GitHub account + PAT (scope: `repo`) + added by jb   |
| Linda  | User        | git + Python, no GitHub account needed                |
| Paul   | User        | git + Python, no GitHub account needed                |
| Tom    | User        | git + Python, no GitHub account needed                |
