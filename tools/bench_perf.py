"""
VORTEX Trading System - 性能基准测试
测试信号引擎、风控引擎、缠论引擎的关键路径延迟和内存占用
"""
import gc
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, 'src')

import random

import pandas as pd

from chanlun_engine import ChanlunEngine
from market_data import MarketSnapshot
from risk_engine import RiskEngine
from signal_engine import SignalEngine


# ── 构造模拟数据 ──────────────────────────────────────────────
def make_klines(n=120, seed=42):
    random.seed(seed)
    price = 70000.0
    klines = []
    for i in range(n):
        price *= (1 + random.gauss(0, 0.005))
        high = price * random.uniform(1.001, 1.005)
        low = price * random.uniform(0.995, 0.999)
        klines.append({
            'open': price * 0.999,
            'high': high,
            'low': low,
            'close': price,
            'volume': random.uniform(100, 1000),
            'close_time': int(time.time() * 1000) + i * 3600000
        })
    return klines

def make_snapshot(klines):
    return MarketSnapshot(
        symbol='BTCUSDT',
        price=klines[-1]['close'],
        open_price=klines[-1]['open'],
        volume=5000.0,
        change_24h_pct=1.5,
        quote_volume=350000000.0,
        klines=klines,
        degraded=False,
    )

STRATEGY = {
    'signal_params': {
        'ema_fast_period': 8, 'ema_slow_period': 21,
        'ema_alignment_bonus': 2, 'pullback_bonus': 2, 'breakout_bonus': 2
    },
    'signal_levels': {'grade_a_min': 7, 'grade_b_min': 4},
    'market_states': ['S1','S2','S3','S4','S5'],
    'feature_flags': {
        'use_vwap_dev': False, 'use_intermarket_filter': False,
        'use_oi_change': False, 'use_funding_shift': False
    },
    'signal_features': {}, 'macro_filters': {},
    'derivatives_filters': {}, 'setup_filters': {},
}

RISK_CONFIG = {
    'capital_usdt': 10000, 'risk_per_trade_pct': 1.0,
    'stop_loss_pct': 2.5, 'take_profit_pct': 4.5,
    'grade_a_risk_multiplier': 1.5, 'grade_b_risk_multiplier': 0.8,
    'grade_a_max_position_pct': 15.0, 'grade_b_max_position_pct': 8.0,
    'max_open_positions': 3, 'max_total_exposure_pct': 30.0,
    'daily_stop_loss_pct': 5.0, 'consecutive_loss_pause': 3,
    'ema_exit_period': 12,
}

def bench(name, fn, n=200):
    """运行 n 次并收集延迟统计"""
    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000)
    s = sorted(latencies)
    print(f"\n[{name}] n={n}")
    print(f"  p50 = {statistics.median(s):.3f} ms")
    print(f"  p95 = {s[int(n*0.95)]:.3f} ms")
    print(f"  p99 = {s[int(n*0.99)]:.3f} ms")
    print(f"  max = {max(s):.3f} ms")
    return s

def mem_bench(name, fn):
    """测量单次调用的内存增量"""
    gc.collect()
    tracemalloc.start()
    fn()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"\n[{name}] Memory:")
    print(f"  current = {current/1024:.1f} KB")
    print(f"  peak    = {peak/1024:.1f} KB")

# ── 1. SignalEngine 延迟 ──────────────────────────────────────
print("=" * 60)
print("VORTEX Performance Benchmark")
print("=" * 60)

engine = SignalEngine()
klines_base = make_klines(120)
snap_base = make_snapshot(klines_base)
ctx_base = {
    'snapshot': snap_base, 'strategy': STRATEGY,
    'benchmark_snapshot': snap_base, 'intermarket': {},
    'derivatives': {}, 'data_health': {'status': 'ok'}
}

def run_signal():
    klines = make_klines(120, seed=random.randint(0, 9999))
    snap = make_snapshot(klines)
    ctx = {**ctx_base, 'snapshot': snap, 'benchmark_snapshot': snap}
    engine.evaluate('BTCUSDT', 'S2', ctx)

signal_latencies = bench("SignalEngine.evaluate()", run_signal, n=200)
mem_bench("SignalEngine.evaluate()", run_signal)

# ── 2. RiskEngine 延迟 ───────────────────────────────────────
risk_engine = RiskEngine(RISK_CONFIG)

def run_risk():
    risk_engine.size_position('A')

bench("RiskEngine.size_position()", run_risk, n=1000)

# ── 3. ChanlunEngine 延迟 ────────────────────────────────────
chanlun = ChanlunEngine()

def make_df(n=120):
    klines = make_klines(n, seed=random.randint(0, 9999))
    return pd.DataFrame(klines)

def run_chanlun():
    df = make_df(120)
    chanlun.evaluate_divergence(df)

bench("ChanlunEngine.evaluate_divergence()", run_chanlun, n=100)
mem_bench("ChanlunEngine.evaluate_divergence()", run_chanlun)

# ── 4. 吞吐量测试（并发信号评估） ────────────────────────────
import asyncio


async def concurrent_signal_eval(n_concurrent=10):
    import asyncio
    tasks = []
    for i in range(n_concurrent):
        klines = make_klines(120, seed=i)
        snap = make_snapshot(klines)
        ctx = {**ctx_base, 'snapshot': snap, 'benchmark_snapshot': snap}
        tasks.append(asyncio.get_event_loop().run_in_executor(None, engine.evaluate, 'BTCUSDT', 'S2', ctx))
    return await asyncio.gather(*tasks)

t0 = time.perf_counter()
asyncio.run(concurrent_signal_eval(20))
t1 = time.perf_counter()
print(f"\n[Concurrent SignalEngine x20] total={((t1-t0)*1000):.1f}ms | throughput={20/((t1-t0)):.0f} eval/s")

# ── 5. 幂等性缓存压力测试 ────────────────────────────────────
from execution_engine import ExecutionEngine

exec_engine = ExecutionEngine(mode='paper')

def run_idempotency_stress():
    for i in range(1000):
        key = f"ICE_test_{i}"
        exec_engine._idempotency_cache[key] = (f"order_{i}", time.time() - 90000)  # 已过期
    # 触发一次检查，应清理所有过期条目
    exec_engine._check_idempotency("ICE_new_key")

t0 = time.perf_counter()
run_idempotency_stress()
t1 = time.perf_counter()
print(f"\n[IdempotencyCache eviction (1000 entries)] time={(t1-t0)*1000:.2f}ms | remaining={len(exec_engine._idempotency_cache)}")

print("\n" + "=" * 60)
print("Benchmark complete.")
print("=" * 60)
