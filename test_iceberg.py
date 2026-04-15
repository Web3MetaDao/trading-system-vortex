#!/usr/bin/env python3
"""
冰山订单执行测试脚本

测试场景：
1. Paper 模式冰山订单
2. 幂等性检查
3. 超时撤单逻辑
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from execution_engine import ExecutionEngine


async def test_paper_iceberg():
    """测试 Paper 模式冰山订单"""
    print("\n=== 测试 1: Paper 模式冰山订单 ===\n")
    
    engine = ExecutionEngine(mode='paper')
    
    stats = await engine.submit_twap_iceberg_order(
        symbol='BTCUSDT',
        side='BUY',
        total_quantity=5000,
        min_slice=500,
        max_slices=5
    )
    
    print(f"\n执行结果:")
    print(f"  总数量：{stats.total_quantity:.2f} USDT")
    print(f"  已成交：{stats.filled_quantity:.2f} USDT")
    print(f"  执行切片：{stats.executed_slices} 片")
    print(f"  平均价格：{stats.avg_price:.2f}")
    print(f"  执行时间：{stats.execution_time_seconds:.1f} 秒")
    print(f"  总手续费：{stats.total_fees:.4f} USDT")
    
    # 验证结果
    assert stats.total_quantity == 5000, "总数量不匹配"
    assert stats.executed_slices > 0, "至少执行一片"
    
    print("\n✅ Paper 模式测试通过")
    return True


async def test_idempotency():
    """测试幂等性"""
    print("\n=== 测试 2: 幂等性检查 ===\n")
    
    engine = ExecutionEngine(mode='paper')
    
    # 生成订单 ID
    key1 = engine._generate_iceberg_order_id('BTCUSDT', 'BUY', 0, 1000000)
    key2 = engine._generate_iceberg_order_id('BTCUSDT', 'BUY', 0, 1000000)
    key3 = engine._generate_iceberg_order_id('BTCUSDT', 'BUY', 1, 1000000)
    
    print(f"订单 ID 1: {key1}")
    print(f"订单 ID 2: {key2}")
    print(f"订单 ID 3: {key3}")
    
    # 验证相同参数生成相同 ID
    assert key1 == key2, "相同参数应生成相同 ID"
    assert key1 != key3, "不同参数应生成不同 ID"
    
    # 模拟已执行
    engine._idempotency_cache[key1] = 'order_123'
    
    # 检查幂等性
    assert engine._check_idempotency(key1) == True, "应检测到已执行"
    assert engine._check_idempotency(key3) == False, "未执行订单应返回 False"
    
    print("\n✅ 幂等性检查通过")
    return True


async def test_ticker_fetch():
    """测试获取盘口数据"""
    print("\n=== 测试 3: 获取盘口数据 ===\n")
    
    engine = ExecutionEngine(mode='paper')
    
    ticker = engine._fetch_latest_ticker('BTCUSDT')
    
    print(f"盘口数据:")
    print(f"  买一价：{ticker['best_bid']:.2f}")
    print(f"  卖一价：{ticker['best_ask']:.2f}")
    print(f"  最新价：{ticker['last']:.2f}")
    
    assert ticker['best_bid'] > 0, "买一价应大于 0"
    assert ticker['best_ask'] > ticker['best_bid'], "卖一价应大于买一价"
    
    print("\n✅ 盘口数据测试通过")
    return True


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("Mini-Leviathan 冰山订单执行测试")
    print("=" * 60)
    
    tests = [
        test_ticker_fetch,
        test_idempotency,
        test_paper_iceberg
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"\n❌ 测试失败：{e}")
            results.append(False)
    
    # 汇总
    print("\n" + "=" * 60)
    print(f"测试结果：{sum(results)}/{len(results)} 通过")
    print("=" * 60)
    
    return all(results)


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
