"""
Ojo de Dios - Global holding robot (simulation)

Every time it runs, the robot:
1. Downloads real closing prices for 22 global markets.
2. Loads the saved portfolio (or creates a new one on the first run).
3. Adds the monthly contribution on the first run of each month.
4. Buys markets that dropped 6% or more from their 60-day high.
5. Reinvests dividends in the weakest market (feedback loop).
6. Saves the portfolio, the daily history and the charts.
"""

import json
import os
from datetime import date

import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf


# ---- Robot settings ----
CAPITAL = 300                # starting cash in euros
INITIAL_BUY = 10             # euros per asset on the first run
MONTHLY_CONTRIBUTION = 30    # euros added on the first run of each month
FX_FEE = 0.0015              # 0.15% currency exchange fee
DIP_THRESHOLD = 0.06         # buy if it fell 6% or more
DIP_BUY = 10                 # euros per dip purchase
LOOKBACK = 42                # about 60 calendar days of trading
COOLDOWN_DAYS = 14           # wait 14 days before buying the same market again
WITHHOLDING = 0.15           # tax kept on dividends

MEMORY_FILE = "portfolio.json"
HISTORY_FILE = "history.csv"

ASSETS = {
    "NVDA": "Nvidia",
    "MSFT": "Microsoft",
    "GOOGL": "Google",
    "META": "Meta",
    "AMD": "AMD",
    "SPY": "S&P 500",
    "EWJ": "Japan",
    "INDA": "India",
    "EIDO": "Indonesia",
    "EWY": "South Korea",
    "MCHI": "China",
    "EWT": "Taiwan",
    "KSA": "Saudi Arabia",
    "UAE": "United Arab Emirates",
    "QAT": "Qatar",
    "EWZ": "Brazil",
    "EWW": "Mexico",
    "ARGT": "Argentina",
    "EWG": "Germany",
    "EDEN": "Denmark",
    "GLD": "Gold",
    "USO": "Oil",
}

# Chart colors (same as the Ojo de Dios design)
BG = "#070B14"
CALM = "#5CC8FF"
WATCH = "#F2B544"
BUY = "#FF7A3D"


# ---- Market data ----
def download_prices():
    """Download 3 months of closing prices and dividends for every asset."""
    price_history = {}
    div_history = {}
    prices = {}
    for ticker in ASSETS:
        data = yf.Ticker(ticker).history(period="3mo")
        price_history[ticker] = data["Close"]
        div_history[ticker] = data["Dividends"]
        prices[ticker] = data["Close"].iloc[-1]
        print(ASSETS[ticker], round(prices[ticker], 2))
    return prices, price_history, div_history


def get_exchange_rate():
    """Return how many US dollars 1 euro buys today."""
    fx = yf.Ticker("EURUSD=X").history(period="5d")
    eur_usd = fx["Close"].iloc[-1]
    print("1 EUR =", round(eur_usd, 4), "USD")
    return eur_usd


# ---- Buying ----
def buy(portfolio, ticker, euros, prices, eur_usd):
    """Spend some euros on one asset, paying the exchange fee."""
    fee = euros * FX_FEE
    usd = (euros - fee) * eur_usd
    portfolio["units"][ticker] = portfolio["units"].get(ticker, 0) + usd / prices[ticker]
    portfolio["total_fees"] = portfolio["total_fees"] + fee


# ---- Memory ----
def load_portfolio(today, prices, eur_usd):
    """Load the saved portfolio, or create a new one on the first run."""
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE) as f:
            portfolio = json.load(f)
        portfolio.setdefault("last_month", today[:7])
        print("Loaded portfolio from", portfolio["date"])
        return portfolio

    portfolio = {
        "date": today,
        "cash": CAPITAL,
        "units": {},
        "total_fees": 0,
        "last_dip_buy": {},
        "last_month": today[:7],
    }
    for ticker in ASSETS:
        buy(portfolio, ticker, INITIAL_BUY, prices, eur_usd)
        portfolio["cash"] = portfolio["cash"] - INITIAL_BUY
    print("First run: bought", len(portfolio["units"]), "assets")
    return portfolio


def save_portfolio(portfolio, today):
    """Write the portfolio to the memory file."""
    portfolio["date"] = today
    with open(MEMORY_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)
    print("Portfolio saved")


# ---- Rules ----
def add_monthly_contribution(portfolio, today):
    """Add the monthly contribution on the first run of a new month."""
    this_month = today[:7]
    if this_month != portfolio["last_month"]:
        portfolio["cash"] = portfolio["cash"] + MONTHLY_CONTRIBUTION
        portfolio["last_month"] = this_month
        print("Monthly contribution: +", MONTHLY_CONTRIBUTION, "EUR")


def find_drops(price_history, prices):
    """Return how much each asset is below its 60-day high (e.g. -0.08 = -8%)."""
    drops = {}
    for ticker in ASSETS:
        high = price_history[ticker].tail(LOOKBACK).max()
        drops[ticker] = prices[ticker] / high - 1
    return drops


def buy_the_dip(portfolio, drops, prices, eur_usd, today):
    """Buy the markets in the buy zone, deepest drop first."""
    dips = [(drop, ticker) for ticker, drop in drops.items() if drop <= -DIP_THRESHOLD]
    dips.sort()
    print("Markets in buy zone:", len(dips))

    for drop, ticker in dips:
        last = portfolio["last_dip_buy"].get(ticker)
        if last is not None and (date.fromisoformat(today) - date.fromisoformat(last)).days < COOLDOWN_DAYS:
            print("Bought recently:", ASSETS[ticker])
            continue

        if portfolio["cash"] >= DIP_BUY:
            buy(portfolio, ticker, DIP_BUY, prices, eur_usd)
            portfolio["cash"] = portfolio["cash"] - DIP_BUY
            portfolio["last_dip_buy"][ticker] = today
            print("Dip buy:", ASSETS[ticker], round(drop * 100, 1), "%")
        else:
            print("No cash for:", ASSETS[ticker], round(drop * 100, 1), "%")


def collect_dividends(portfolio, div_history, drops, prices, eur_usd, today):
    """Collect new dividends and reinvest them in the weakest market (feedback)."""
    prev_date = portfolio["date"]
    dividend_cash = 0
    for ticker in ASSETS:
        for day, amount in div_history[ticker].items():
            day = str(day.date())
            if amount > 0 and prev_date < day <= today:
                net = amount * portfolio["units"][ticker] * (1 - WITHHOLDING) / eur_usd
                dividend_cash = dividend_cash + net
                print("Dividend:", ASSETS[ticker], round(net, 4), "EUR")

    if dividend_cash > 0:
        weakest = min(drops, key=drops.get)
        buy(portfolio, weakest, dividend_cash, prices, eur_usd)
        print("Feedback: reinvested", round(dividend_cash, 4), "EUR in", ASSETS[weakest],
              round(drops[weakest] * 100, 1), "%")
    else:
        print("No new dividends")


# ---- Reporting ----
def portfolio_value(portfolio, prices, eur_usd):
    """Return the value of all the assets in euros (without cash)."""
    value = 0
    for ticker, units in portfolio["units"].items():
        value = value + units * prices[ticker] / eur_usd
    return value


def save_history(today, invested, cash):
    """Add today's portfolio value to the history file (once per day)."""
    total = invested + cash
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f:
            f.write("date,total,invested,cash\n")

    with open(HISTORY_FILE) as f:
        already_saved = today in f.read()

    if already_saved:
        print("History already has", today)
    else:
        with open(HISTORY_FILE, "a") as f:
            f.write(today + "," + str(round(total, 2)) + "," + str(round(invested, 2)) + "," + str(round(cash, 2)) + "\n")
        print("History updated:", round(total, 2), "EUR")


def style_chart(ax):
    """Dark Ojo de Dios look for a chart."""
    ax.set_facecolor(BG)
    ax.tick_params(colors="white")
    for side in ax.spines.values():
        side.set_visible(False)


def make_charts(drops, today):
    """Save the dip radar and the portfolio value charts as images."""
    # Chart 1: dip radar
    order = sorted(drops, key=drops.get)
    names = [ASSETS[t] for t in order]
    values = [drops[t] * 100 for t in order]
    colors = []
    for v in values:
        if v <= -DIP_THRESHOLD * 100:
            colors.append(BUY)
        elif v <= -3:
            colors.append(WATCH)
        else:
            colors.append(CALM)

    fig, ax = plt.subplots(figsize=(8, 7), facecolor=BG)
    style_chart(ax)
    ax.barh(names, values, color=colors)
    ax.axvline(-DIP_THRESHOLD * 100, color=WATCH, linestyle="--", linewidth=1)
    ax.invert_yaxis()
    ax.set_title("Ojo de Dios - Dip radar (" + today + ")", color="white")
    ax.set_xlabel("Drop from 60-day high (%)", color="white")
    plt.tight_layout()
    plt.savefig("dip_radar.png", dpi=150, facecolor=BG)
    plt.close()
    print("Chart saved: dip_radar.png")

    # Chart 2: portfolio value over time
    history = pd.read_csv(HISTORY_FILE)
    if len(history) < 2:
        print("Portfolio chart: needs at least 2 days of history")
        return

    fig, ax = plt.subplots(figsize=(9, 4), facecolor=BG)
    style_chart(ax)
    ax.plot(history["date"], history["total"], color=CALM, linewidth=2, marker="o")
    ax.set_title("Ojo de Dios - Portfolio value (EUR)", color="white")
    ax.grid(color="#1C2740")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig("portfolio_value.png", dpi=150, facecolor=BG)
    plt.close()
    print("Chart saved: portfolio_value.png")


# ---- Main program ----
def main():
    today = str(date.today())

    prices, price_history, div_history = download_prices()
    eur_usd = get_exchange_rate()

    portfolio = load_portfolio(today, prices, eur_usd)
    add_monthly_contribution(portfolio, today)

    drops = find_drops(price_history, prices)
    buy_the_dip(portfolio, drops, prices, eur_usd, today)
    collect_dividends(portfolio, div_history, drops, prices, eur_usd, today)

    invested = portfolio_value(portfolio, prices, eur_usd)
    print("Invested value:", round(invested, 2), "EUR")
    print("Cash:", round(portfolio["cash"], 2), "EUR")
    print("Total:", round(invested + portfolio["cash"], 2), "EUR")
    print("Fees paid so far:", round(portfolio["total_fees"], 2), "EUR")

    save_portfolio(portfolio, today)
    save_history(today, invested, portfolio["cash"])
    make_charts(drops, today)


if __name__ == "__main__":
    main()
