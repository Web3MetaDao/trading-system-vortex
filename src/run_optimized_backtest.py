import sys
import os
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).resolve().parent))

from backtest import build_snapshot, performance_from_trades, build_analysis, format_analysis_panel
from signal_engine_v2 import SignalEngineV2
from risk_engine_optimized import RiskEngine
from market_data import MarketDataClient
import pandas as pd
import json

def run_v2_backtest(symbol="BTCUSDT", limit=200):
    print(f"Starting Optimized Backtest (V2) for {symbol}...")
    
    # Initialize components
    client = MarketDataClient()
    signal_engine = SignalEngineV2()
    risk_config = {
        "capital_usdt": 1000.0,
        "risk_per_trade_pct": 1.0,
        "stop_loss_pct": 2.0,
        "grade_a_risk_multiplier": 1.5,
        "grade_b_risk_multiplier": 1.0,
        "max_open_positions": 3
    }
    risk_engine = RiskEngine(risk_config)
    
    # Get historical data
    klines = client.fetch_klines(symbol, "1h", limit=limit)
    if not klines:
        print("Failed to get klines")
        return
    
    trades = []
    equity = 1000.0
    
    # Simulation loop
    for i in range(30, len(klines)):
        current_klines = klines[:i+1]
        snapshot = build_snapshot(symbol, current_klines)
        
        # 1. Check Signal
        context = {"snapshot": snapshot, "strategy": {"signal_params": {"ema_fast_period": 20, "ema_slow_period": 50}}}
        decision = signal_engine.evaluate(symbol, "S3", context)
        
        if decision.grade in ["A", "B"] and decision.side == "LONG":
            # Simple simulation: buy at close, hold for 5 bars or hit stop/take profit
            entry_price = snapshot.price
            exit_price = klines[min(i+5, len(klines)-1)]["close"]
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            
            trades.append({
                "symbol": symbol,
                "side": "LONG",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "pnl_usdt": (equity * 0.1) * (pnl_pct / 100),
                "grade": decision.grade,
                "reason": decision.reason
            })
            
    # Summary
    perf = performance_from_trades(trades, 10, initial_capital=1000.0)
    print("\n" + "="*40)
    print("V2 OPTIMIZED BACKTEST RESULTS")
    print(f"Total Trades: {perf['closed_trades']}")
    print(f"Win Rate: {perf['win_rate_pct']}%")
    print(f"Total PnL: {perf['total_pnl_usdt']:.2f} USDT")
    print("="*40)

if __name__ == "__main__":
    run_v2_backtest()
