import multiprocessing as mp
import time
import os
import logging
from multi_proc_manager import MultiProcessManager
from data_persistence import DataPersistence
from ml_signal_filter import MLSignalFilter
from dynamic_risk_manager import DynamicRiskManager
from market_data import MarketDataClient
from signal_engine_v2 import SignalEngineV2

# Configure Global Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("VortexV3")

def market_data_worker(stop_event, symbols, mgr):
    """Worker: High-frequency market data ingestion"""
    logger.info("Market Data Worker started.")
    client = MarketDataClient()
    dp = DataPersistence()
    
    while not stop_event.is_set():
        try:
            for symbol in symbols:
                snapshot = client.fetch_snapshot(symbol, kline_limit=100)
                if snapshot and snapshot.price:
                    # Update Shared Memory
                    mgr.update_snapshot(symbol, {
                        "price": snapshot.price,
                        "klines": snapshot.klines,
                        "change_24h_pct": snapshot.change_24h_pct,
                        "ts": time.time()
                    })
                    # Persistent Storage (Every 1m)
                    if int(time.time()) % 60 == 0:
                        dp.save_klines(symbol, snapshot.klines[-1:])
            time.sleep(1)  # 1s polling
        except Exception as e:
            logger.error(f"Market Data Error: {e}")
            time.sleep(5)

def signal_risk_worker(stop_event, symbols, mgr):
    """Worker: Parallel Signal & Risk Execution"""
    logger.info("Signal/Risk Worker started.")
    signal_engine = SignalEngineV2()
    ml_filter = MLSignalFilter()
    risk_manager = DynamicRiskManager({"capital_usdt": 1000.0, "max_risk_pct": 1.5})
    
    while not stop_event.is_set():
        try:
            for symbol in symbols:
                data = mgr.get_snapshot(symbol)
                if not data: continue
                
                # Mock context for Signal Engine
                context = {"snapshot": type('obj', (object,), data), "strategy": {}}
                decision = signal_engine.evaluate(symbol, "S1", context)
                
                if decision.grade in ["A", "B"]:
                    # ML Confidence Boost
                    ml_prob = ml_filter.predict_confidence(decision.explain)
                    if ml_prob > 0.6:
                        logger.info(f"ML CONFIRMED [{symbol}] {decision.side} - Confidence: {ml_prob:.2f}")
                        # Dynamic Risk Sizing
                        risk_decision = risk_manager.calculate_dynamic_levels(data['price'], 500.0, decision.side)
                        if risk_decision.approved:
                            logger.info(f"EXECUTE [{symbol}] {decision.side} Size: {risk_decision.position_size_usdt} USDT")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Signal Worker Error: {e}")
            time.sleep(5)

def start_vortex_v3():
    symbols = ["BTCUSDT", "ETHUSDT"]
    mgr = MultiProcessManager(symbols)
    
    # Spawn Parallel Workers
    mgr.spawn_worker(market_data_worker, (symbols, mgr))
    mgr.spawn_worker(signal_risk_worker, (symbols, mgr))
    
    logger.info("--- Vortex V3 High-Performance Platform Started ---")
    logger.info("Architecture: Multi-Process Shared Memory")
    logger.info("Intelligence: ML Signal Filter Integrated")
    logger.info("Persistence: SQLite Historical Database Enabled")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mgr.stop_all()

if __name__ == "__main__":
    start_vortex_v3()
