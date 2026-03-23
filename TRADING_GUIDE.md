# AAItrade Trading Guide

## What Each Setting Means

### **Stop Loss %**
- **What it does**: Auto-exit a position if it loses this much.
- **Example**: Buy RELIANCE at ₹100 with 3% stop loss → exits at ₹97 or below.
- **Why**: Safety net to limit losses on bad trades.
- **Range**: 0.5% to 15%

### **Take Profit %**
- **What it does**: Auto-exit a position if it gains this much.
- **Example**: Buy TCS at ₹3,000 with 5% take profit → exits at ₹3,150.
- **Why**: Locks in gains before mood changes or market reverses.
- **Range**: 1% to 30%

### **Max Open Positions**
- **What it does**: Max NUMBER OF DIFFERENT STOCKS you can hold at once.
- **Important**: NOT about share count — about variety.
- **Example**: Max = 5 means you can hold RELIANCE + TCS + INFY + HDFCBANK + WIPRO together, but NOT a 6th stock.
- **Why**: Prevents over-diversification; keeps focus tight.
- **Range**: 1 to 10 stocks

### **Max Per Trade %**
- **What it does**: Max amount of your capital you can commit to ANY SINGLE BUY.
- **Example**: Capital = ₹10,000, Max per trade = 20% → max ₹2,000 per BUY.
- **Important**: This is PER BUY ORDER, not total.
- **Example continuation**: You can buy RELIANCE for ₹2,000 AND later buy TCS for ₹2,000 (two separate trades, both under the limit).
- **Why**: Prevents gambling your entire account on one stock.
- **Range**: 5% to 50%

### **Max Deployed %**
- **What it does**: Total % of your capital that can be tied up in open positions (combined).
- **Example**: Capital = ₹10,000, Max deployed = 80% → max ₹8,000 can be in stocks at once.
- **Example breakdown**:
  - RELIANCE position costs ₹3,000
  - TCS position costs ₹2,500
  - INFY position costs ₹2,000
  - **Total deployed = ₹7,500** (under 80% limit ✓)
  - You still have ₹2,500 cash available
- **Why**: Keeps emergency cash on hand for exits or opportunities.
- **Range**: 30% to 100%

### **Daily Loss Limit %**
- **What it does**: HALT ALL TRADING FOR THE DAY if cumulative losses hit this %.
- **Example**: Capital = ₹10,000, Daily limit = 5% → if you lose ₹500 today, no more trades until tomorrow.
- **Why**: Hard circuit breaker to prevent emotional revenge trading on bad days.
- **Range**: 1% to 20%

### **Profit Reinvestment %**
- **What it does**: When you sell a stock at PROFIT, how much of that profit goes back into trading capital vs. gets locked away.
- **Example**:
  - Sell RELIANCE for ₹100 profit
  - Reinvestment = 0% → all ₹100 is locked (can't trade with it)
  - Reinvestment = 100% → all ₹100 goes back to free cash (can buy with it)
  - Reinvestment = 50% → ₹50 locked, ₹50 available to trade
- **Why**: Controls growth vs. safety. High reinvestment = compound growth but more risk. Low = preserve gains.
- **Range**: 0% to 100%

---

## Trading Modes Explained

| Mode | Risk | Stop Loss | Take Profit | Max Positions | Max Per Trade | Max Deployed | Daily Limit |
|------|------|-----------|-------------|---------------|---------------|--------------|-------------|
| **Safe** | Low | 2% | 4% | 4 | 15% | 90% | 3% |
| **Balanced** | Medium | 3% | 5% | 5 | 20% | 90% | 5% |
| **Aggressive** | High | 5% | 8% | 6 | 25% | 90% | 8% |
| **Custom** | User-defined | Your choice | Your choice | Your choice | Your choice | Your choice | Your choice |

---

## Execution Modes

### **Paper Trading**
- Simulates all trades with NO REAL MONEY
- Good for learning and testing
- Uses historical data
- No actual orders on Zerodha

### **Live Trading**
- REAL MONEY on your Zerodha account
- Uses actual market prices
- Orders executed on NSE
- P&L is real

---

## Session Isolation (Important!)

**Each session is completely isolated:**
- Session 1's portfolio does NOT mix with Session 2's portfolio
- They each have their own ₹, stocks, and trading history
- If both sessions hold the SAME stock (e.g., both hold RELIANCE), Zerodha sees one combined position
  - This is normal and expected
  - The system will warn you if this happens

---

## Recovery & Persistence

**Sessions survive:**
- ✅ Server restarts
- ✅ Code updates (via git pull)
- ✅ Network disconnects
- ✅ Crashes (auto-restart within 10s)

**What happens on restart:**
1. Server reads DB to find all active/paused/closing sessions
2. Rebuilds each session from saved state (capital, positions, decisions)
3. Resumes trading loop at next market opening
4. For live sessions: syncs with Zerodha holdings to fix any discrepancies

---

## Creating Your First Session

1. Go to **http://localhost:8000**
2. Click **Command Center** (Settings icon)
3. Click **New Session**
4. Fill in:
   - **Session Name**: e.g., "live-aggressive" or "paper-balanced"
   - **Starting Capital**: your ₹ (e.g., 10000 for ₹10,000)
   - **Execution Mode**: "Paper" (safe) or "Live" (real money)
   - **Trading Mode**: Pick safe/balanced/aggressive OR custom for full control
   - **AI Model**: Haiku (fast, cheap) or Sonnet (smarter)
   - **Profit Reinvestment %**: 0-100 (what % of profits to reinvest)
5. Click **Start Session**

---

## Changing Settings Live (During a Session)

You can change **Profit Reinvestment %** while a session is running:
1. Find the session in **Command Center** → **Active Sessions**
2. Click the dropdown arrow next to it
3. Enter new % and click **Apply**
4. Takes effect on next trade

---

## Zerodha Token Setup

Every morning before 9:15 AM IST:
1. Go to **Command Center** → **Kite Access Token** section
2. Visit: `https://kite.trade/connect/login?api_key=9dz93b78apapfn1l&v=3`
3. Log in to your Zerodha account
4. Copy the `request_token` from the URL bar
5. Paste it in the dashboard input
6. Click **Update**

Done — no restart needed!

---

## Monitoring Your Session

- **Overview page**: See all active sessions, capital, P&L, positions
- **Command Center**: Control (start/stop/pause/resume/close sessions)
- **Dashboard tabs**: See trade history, decisions, P&L details, journal notes

---

## When Things Go Wrong

**Server is down?**
- Check status: `ssh -i ~/AAItrade/server/ssh-key-2026-03-13.key ubuntu@68.233.98.35 "sudo systemctl status aaitrade"`
- Restart: `ssh ... "sudo systemctl restart aaitrade"`
- Logs: `ssh ... "sudo journalctl -u aaitrade -f"`

**Dashboard won't load?**
- Tunnel dropped: Restart it with `launchctl stop com.aaitrade.tunnel && launchctl start com.aaitrade.tunnel`
- Server down: Check status above

**Lost money in live session?**
- Check session P&L in **Overview** or **Command Center**
- See individual trades in **Dashboard** → **Trades** tab
- Review Claude's decisions in **Decisions** tab
- Check Zerodha web directly to confirm portfolio state
