#!/usr/bin/env python3
"""
60 天完整回测（后台运行版）
- 自动发送 Telegram 报告
- 支持长时间运行
"""

import os
import sys

sys.path.insert(0, "/Users/micheal/Documents/trading system/src")

# 加载环境变量
from pathlib import Path  # noqa: E402

env_path = Path("/Users/micheal/Documents/trading system/.env")
if env_path.exists():
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

import asyncio  # noqa: E402

from backtest_runner import BacktestConfig, BacktestRunner  # noqa: E402

# 配置回测参数
config = BacktestConfig(
    symbols=["BTCUSDT"],
    interval="1h",
    lookback_bars=60 * 24,  # 60 天 * 24 小时 = 1440 根
    initial_capital=10000.0,
    position_size_usdt=100.0,
)

print("=" * 80)
print("Mini-Leviathan 60 天完整回测")
print("=" * 80)
print("\n回测参数:")
print(f"  标的：{config.symbols}")
print(f"  周期：{config.interval}")
print(f"  K 线数量：{config.lookback_bars} 根")
print(f"  初始资金：${config.initial_capital:.2f}")
print(f"  单笔仓位：${config.position_size_usdt:.2f}")
print("\n⏱️ 预计耗时：30-60 分钟")
print("📱 完成后自动发送 Telegram 报告")
print("\n开始回测...\n")

# 运行回测
runner = BacktestRunner(config)
metrics = runner.run_backtest()

# 打印结果
print("\n" + "=" * 80)
print("回测结果")
print("=" * 80)

print("\n💰 资金状况:")
print(f"  初始资金：${config.initial_capital:.2f}")
print(f"  最终资金：${runner.current_capital:.2f}")
print(f"  总盈亏：${metrics.total_pnl:.2f} ({metrics.total_pnl_percent:.2f}%)")

print("\n📊 交易统计:")
print(f"  总交易数：{metrics.total_trades}")
print(f"  盈利：{metrics.winning_trades} | 亏损：{metrics.losing_trades}")
print(f"  胜率：{metrics.win_rate:.2f}%")

print("\n📊 绩效指标:")
print(f"  平均盈利：${metrics.avg_win:.2f}")
print(f"  平均亏损：${metrics.avg_loss:.2f}")
print(f"  盈亏比：{metrics.profit_factor:.2f}")
print(f"  最大回撤：{metrics.max_drawdown:.1f}%")

if runner.trades:
    print("\n最近 5 笔交易:")
    for trade in runner.trades[-5:]:
        emoji = "✅" if trade["pnl"] > 0 else "❌"
        print(f"  {emoji} {trade['symbol']} {trade['direction']} | PnL: ${trade['pnl']:.2f}")

print("\n" + "=" * 80)

# 发送 Telegram 报告
if runner.telegram and runner.telegram.is_enabled:
    print("\n📱 正在发送回测报告到 Telegram...")

    async def send_report():
        # 构建详细报告
        from datetime import UTC, datetime

        report_lines = [
            "📈 *60 天回测报告*",
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC",
            "",
            f"💰 初始资金：${config.initial_capital:.2f}",
            f"📊 最终资金：${runner.current_capital:.2f}",
            f"📉 总盈亏：${metrics.total_pnl:.2f} ({metrics.total_pnl_percent:.2f}%)",
            "",
            "📊 *交易统计*:",
            f"  总交易数：{metrics.total_trades}",
            f"  盈利：{metrics.winning_trades} | 亏损：{metrics.losing_trades}",
            f"  胜率：{metrics.win_rate:.1f}%",
            "",
            "📊 *绩效指标*:",
            f"  平均盈利：${metrics.avg_win:.2f}",
            f"  平均亏损：${metrics.avg_loss:.2f}",
            f"  盈亏比：{metrics.profit_factor:.2f}",
            f"  最大回撤：{metrics.max_drawdown:.1f}%",
            "",
            "📊 *历史对比（100 天回测）*:",
            "  胜率：72.73%",
            "  总收益率：+42.3%",
            "",
        ]

        if runner.trades:
            report_lines.append("最近 5 笔交易:")
            for trade in runner.trades[-5:]:
                emoji = "✅" if trade["pnl"] > 0 else "❌"
                report_lines.append(
                    f"  {emoji} {trade['symbol']} {trade['direction']} | PnL: ${trade['pnl']:.2f}"
                )

        report_text = "\n".join(report_lines)
        success = await runner.telegram.send_async(report_text, parse_mode="Markdown")
        return success

    try:
        result = asyncio.run(send_report())
        if result:
            print("✅ 回测报告已发送到 Telegram")
        else:
            print("❌ Telegram 发送失败")
    except Exception as e:
        print(f"❌ 发送异常：{e}")

print("\n✅ 60 天回测完成！")
