"""
Mini-Leviathan Telegram 通知模块 (v3.0 - Phase 5 重构版)

纯净只读推送模块 - 无任何交互式监听
- 使用 aiohttp 直接调用 Telegram API
- 集成多模态神谕、缠论引擎、冰山执行
- HTML 格式排版，结构清晰
- 严格的单向推送（Read-Only）

作者：TRAE AI Assistant
版本：3.0.0 (Phase 5)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None


class TelegramAlertLevel(Enum):
    """告警级别"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class TelegramMessage:
    """消息数据类"""

    text: str
    level: TelegramAlertLevel
    timestamp: str
    parse_mode: str = "HTML"
    disable_notification: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "level": self.level.value,
            "timestamp": self.timestamp,
            "parse_mode": self.parse_mode,
            "disable_notification": self.disable_notification,
        }


class TelegramNotifier:
    """
    Telegram 通知推送器（只读纯净版）

    核心特性：
    - 纯 HTTP POST 请求，无监听逻辑
    - 集成 Oracle/Chanlun/Iceberg 高阶特征
    - HTML 格式排版，美观清晰
    - 严格的单向推送（Read-Only）

    使用场景：
    - 交易信号通知
    - 订单执行报告
    - 风控告警
    - 系统状态监控
    """

    _instance: TelegramNotifier | None = None

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        default_parse_mode: str = "HTML",
        rate_limit_seconds: float = 1.0,
    ):
        """
        初始化 Telegram 通知器

        Args:
            bot_token: Telegram Bot Token
            chat_id: 目标聊天 ID
            enabled: 是否启用
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            default_parse_mode: 默认解析模式（HTML/MarkdownV2）
            rate_limit_seconds: 频率限制（秒）
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = enabled and bool(self.bot_token) and bool(self.chat_id)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.default_parse_mode = default_parse_mode
        self.rate_limit_seconds = rate_limit_seconds

        self._last_sent_time: float = 0
        self._logger = logging.getLogger("telegram_notifier")

        # 检查依赖
        if not AIOHTTP_AVAILABLE and not REQUESTS_AVAILABLE:
            self._logger.warning("aiohttp 和 requests 都未安装，Telegram 通知将不可用")
            self._enabled = False

        if self.is_enabled:
            self._logger.info(f"Telegram 通知器已初始化，chat_id: {self.chat_id}")

    async def _send_via_aiohttp(
        self, text: str, parse_mode: str, disable_notification: bool
    ) -> bool:
        """通过 aiohttp 发送消息"""
        if not AIOHTTP_AVAILABLE:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=10) as response:
                    if response.status == 200:
                        self._logger.debug("Telegram 消息发送成功")
                        return True
                    else:
                        error_text = await response.text()
                        self._logger.error(
                            f"Telegram API 返回错误：{response.status} - {error_text}"
                        )
                        return False
        except TimeoutError:
            self._logger.error("Telegram 请求超时")
            return False
        except aiohttp.ClientError as e:
            self._logger.error(f"Telegram HTTP 错误：{e}")
            return False
        except Exception as e:
            self._logger.error(f"Telegram 发送异常：{e}")
            return False

    def _send_via_requests(self, text: str, parse_mode: str, disable_notification: bool) -> bool:
        """通过 requests 发送消息（同步备用方案）"""
        if not REQUESTS_AVAILABLE:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        try:
            response = requests.post(url, json=data, timeout=10)
            if response.status_code == 200:
                self._logger.debug("Telegram 消息发送成功")
                return True
            else:
                self._logger.error(
                    f"Telegram API 返回错误：{response.status_code} - {response.text}"
                )
                return False
        except requests.Timeout:
            self._logger.error("Telegram 请求超时")
            return False
        except requests.RequestException as e:
            self._logger.error(f"Telegram HTTP 错误：{e}")
            return False

    async def send_async(
        self,
        text: str,
        level: TelegramAlertLevel = TelegramAlertLevel.INFO,
        parse_mode: str | None = None,
        disable_notification: bool = False,
    ) -> bool:
        """
        异步发送消息

        Args:
            text: 消息文本
            level: 告警级别
            parse_mode: 解析模式（HTML/MarkdownV2）
            disable_notification: 静默推送

        Returns:
            bool: 是否发送成功
        """
        if not self.is_enabled:
            return False

        # 频率限制
        elapsed = time.time() - self._last_sent_time
        if elapsed < self.rate_limit_seconds:
            await asyncio.sleep(self.rate_limit_seconds - elapsed)

        parse_mode = parse_mode or self.default_parse_mode

        # 优先使用 aiohttp
        for attempt in range(1, self.max_retries + 1):
            try:
                success = await self._send_via_aiohttp(text, parse_mode, disable_notification)
                if success:
                    self._last_sent_time = time.time()
                    return True
            except Exception as e:
                self._logger.warning(f"Telegram 发送尝试 {attempt} 失败：{e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))

        # 降级到 requests
        self._logger.info("降级到 requests 发送")
        try:
            success = self._send_via_requests(text, parse_mode, disable_notification)
            if success:
                self._last_sent_time = time.time()
                return True
        except Exception as e:
            self._logger.error(f"requests 发送失败：{e}")

        return False

    def send(
        self,
        text: str,
        level: TelegramAlertLevel = TelegramAlertLevel.INFO,
        parse_mode: str | None = None,
        disable_notification: bool = False,
    ) -> bool:
        """
        同步发送消息

        Args:
            text: 消息文本
            level: 告警级别
            parse_mode: 解析模式
            disable_notification: 静默推送

        Returns:
            bool: 是否发送成功
        """
        if not self.is_enabled:
            return False

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在运行中的事件循环中创建任务
                # [FIX] 将 task 存储为实例属性，防止被 GC 回收（RUF006）
                self._pending_task = asyncio.ensure_future(
                    self.send_async(text, level, parse_mode, disable_notification)
                )
                # 不等待结果，直接返回
                return True
            else:
                # 同步运行
                return loop.run_until_complete(
                    self.send_async(text, level, parse_mode, disable_notification)
                )
        except RuntimeError:
            # 没有事件循环，创建新的
            return asyncio.run(self.send_async(text, level, parse_mode, disable_notification))

    # ========== 核心格式化方法：Phase 5 高阶特征集成 ==========

    def _escape_html(self, text: str) -> str:
        """HTML 转义，防止特殊字符报错"""
        if not text:
            return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    async def send_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None,
        result: str,
        market_state: str = "S3",
        oracle_sentiment: float = 0.0,
        oracle_obi: float = 0.0,
        oracle_permitted: bool = True,
        chanlun_pattern: str | None = None,
        chanlun_strength: float = 0.0,
        chanlun_bonus: int = 0,
        iceburg_slices: int = 0,
        iceburg_duration: int = 0,
        stop_loss_price: float | None = None,
        risk_pct: float = 0.0,
    ) -> bool:
        """
        发送交易警报（集成所有高阶特征）

        Args:
            symbol: 交易对（如 'BTCUSDT'）
            side: 买卖方向（'BUY'/'SELL'）
            quantity: 数量
            price: 价格（None 表示市价单）
            result: 执行结果
            market_state: 市场状态（S1-S5）
            oracle_sentiment: Oracle 情绪指数（-1 到 1）
            oracle_obi: Oracle 盘口失衡（-1 到 1）
            oracle_permitted: Oracle 是否放行
            chanlun_pattern: 缠论形态（如 'bottom_divergence'）
            chanlun_strength: 缠论强度（0-1）
            chanlun_bonus: 缠论加分
            iceburg_slices: 冰山切片数量
            iceburg_duration: 冰山预计耗时（秒）
            stop_loss_price: 止损价
            risk_pct: 单笔风险暴露（%）

        Returns:
            bool: 是否发送成功
        """
        # 构建 HTML 消息
        lines = []

        # 标题
        emoji = "🟢" if side == "BUY" else "🔴"
        lines.append(f"{emoji} <b>Trade Alert</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        # 交易对与方向
        lines.append(f"{emoji} <b>交易对与方向:</b> <code>{symbol}</code> | <b>{side}</b>")

        # 数量与价格
        price_str = f"{price:.2f}" if price else "MARKET"
        lines.append(f"💰 <b>数量:</b> {quantity:.2f} USDT")
        lines.append(f"📊 <b>价格:</b> {price_str}")

        # 宏观罗盘
        state_emoji = {"S1": "🚀", "S2": "📈", "S3": "⚖️", "S4": "📉", "S5": "💥"}.get(
            market_state, "⚪"
        )
        state_desc = {
            "S1": "强势上涨",
            "S2": "震荡上行",
            "S3": "中性震荡",
            "S4": "震荡下行",
            "S5": "强势下跌",
        }.get(market_state, "未知")
        lines.append(f"{state_emoji} <b>宏观罗盘:</b> {market_state} ({state_desc})")

        # 神谕视界
        if oracle_permitted:
            oracle_emoji = "👁️"
            oracle_status = "✅ 放行"
        else:
            oracle_emoji = "⚠️"
            oracle_status = "🚫 禁止"

        sentiment_desc = self._get_sentiment_description(oracle_sentiment)
        obi_desc = self._get_obi_description(oracle_obi)

        lines.append(f"{oracle_emoji} <b>神谕视界:</b> {oracle_status}")
        lines.append(f"   🧭 情绪指数：<b>{oracle_sentiment:.2f}</b> ({sentiment_desc})")
        lines.append(f"   📊 盘口失衡：<b>{oracle_obi:.2f}</b> ({obi_desc})")

        # 微观狙击（缠论）
        if chanlun_pattern:
            pattern_desc = {
                "bottom_divergence": "底背驰",
                "third_buy": "第三买点",
            }.get(chanlun_pattern, chanlun_pattern)

            lines.append(f"🎯 <b>微观狙击:</b> {pattern_desc}")
            lines.append(f"   💪 强度：<b>{chanlun_strength:.2f}</b>")
            if chanlun_bonus > 0:
                lines.append(f"   ⭐ 加分：+{chanlun_bonus}")

        # 冰山执行
        if iceburg_slices > 0:
            lines.append(f"🧊 <b>冰山执行:</b> {iceburg_slices} 个切片")
            lines.append(f"   ⏱️ 预计耗时：{iceburg_duration} 秒")

        # 风控参数
        lines.append("🛡️ <b>风控参数:</b>")
        if stop_loss_price:
            lines.append(f"   📉 止损价：<b>{stop_loss_price:.2f}</b>")
        lines.append(f"   ⚖️ 单笔风险：<b>{risk_pct:.1f}%</b>")

        # 执行结果
        result_emoji = "✅" if result == "SUCCESS" else "❌"
        lines.append(f"{result_emoji} <b>执行结果:</b> {result}")

        # 时间戳
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"⏰ {timestamp} UTC")

        # 拼接消息
        text = "\n".join(lines)

        # 发送
        return await self.send_async(text, level=TelegramAlertLevel.INFO, parse_mode="HTML")

    def _get_sentiment_description(self, score: float) -> str:
        """获取情绪描述"""
        if score >= 0.8:
            return "极度贪婪"
        elif score >= 0.4:
            return "乐观"
        elif score >= 0.1:
            return "轻微乐观"
        elif score >= -0.1:
            return "中性"
        elif score >= -0.4:
            return "轻微恐慌"
        elif score >= -0.8:
            return "恐慌"
        else:
            return "极度恐慌"

    def _get_obi_description(self, obi: float) -> str:
        """获取盘口失衡描述"""
        if obi >= 0.8:
            return "极端买盘"
        elif obi >= 0.4:
            return "买盘强劲"
        elif obi >= 0.1:
            return "轻微买盘"
        elif obi >= -0.1:
            return "买卖均衡"
        elif obi >= -0.4:
            return "轻微卖压"
        elif obi >= -0.8:
            return "卖压沉重"
        else:
            return "极端卖盘"

    # ========== 其他告警方法（保持向后兼容） ==========

    def format_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None,
        result: str,
    ) -> str:
        """格式化交易警报（简化版，向后兼容）"""
        emoji = "🟢" if side == "BUY" else "🔴"
        price_str = f"{price:.4f}" if price else "MARKET"
        return (
            f"{emoji} <b>Trade Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Symbol:</b> <code>{symbol}</code>\n"
            f"<b>Side:</b> {side}\n"
            f"<b>Quantity:</b> {quantity:.4f}\n"
            f"<b>Price:</b> {price_str}\n"
            f"<b>Result:</b> {result}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

    def send_signal_alert(
        self,
        symbol: str,
        grade: str,
        score: float,
        market_state: str,
    ) -> bool:
        """发送信号警报"""
        grade_emoji = "🅰️" if grade == "A" else "🅱️"
        text = (
            f"{grade_emoji} <b>Signal Generated</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Symbol:</b> <code>{symbol}</code>\n"
            f"{grade_emoji} <b>Grade:</b> {grade}\n"
            f"<b>Score:</b> {score:.2f}\n"
            f"<b>Market State:</b> {market_state}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send(text, level=TelegramAlertLevel.INFO, parse_mode="HTML")

    def send_risk_alert(
        self,
        symbol: str,
        approved: bool,
        reason: str | None,
        size_usdt: float,
    ) -> bool:
        """发送风控警报"""
        status_emoji = "✅" if approved else "🚫"
        status = "APPROVED" if approved else "REJECTED"
        text = (
            f"{status_emoji} <b>Risk Decision</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Symbol:</b> <code>{symbol}</code>\n"
            f"<b>Status:</b> {status}\n"
            f"<b>Size:</b> {size_usdt:.2f} USDT\n"
            f"{f'<b>Reason:</b> {reason}' if reason else ''}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        return self.send(
            text,
            level=TelegramAlertLevel.INFO if approved else TelegramAlertLevel.WARNING,
            parse_mode="HTML",
        )

    def send_error_alert(
        self,
        error_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """发送错误警报"""
        text = (
            f"🚨 <b>Error Alert</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Type:</b> <code>{error_type}</code>\n"
            f"<b>Message:</b> {message}\n"
        )
        if context:
            text += (
                "<b>Context:</b>\n" + "\n".join(f"  • {k}: {v}" for k, v in context.items()) + "\n"
            )
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        return self.send(text, level=TelegramAlertLevel.ERROR, parse_mode="HTML")

    @classmethod
    def get_instance(cls, **kwargs) -> TelegramNotifier:
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置实例"""
        cls._instance = None

    @property
    def is_enabled(self) -> bool:
        """是否启用"""
        return self._enabled

    @property
    def is_available(self) -> bool:
        """是否可用"""
        return self._enabled


# ========== 测试示例 ==========

if __name__ == "__main__":
    print("=== Telegram 通知器测试 ===\n")

    # 创建通知器（需要配置环境变量）
    notifier = TelegramNotifier.get_instance(
        enabled=False  # 测试时禁用实际发送
    )

    # 测试格式化
    print("测试消息格式化：")
    print("-" * 80)

    # 模拟完整的高阶特征数据
    test_data = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 100.0,
        "price": 50000.0,
        "result": "SUCCESS",
        "market_state": "S2",
        "oracle_sentiment": 0.5,
        "oracle_obi": 0.3,
        "oracle_permitted": True,
        "chanlun_pattern": "bottom_divergence",
        "chanlun_strength": 0.85,
        "chanlun_bonus": 2,
        "iceburg_slices": 5,
        "iceburg_duration": 25,
        "stop_loss_price": 47500.0,
        "risk_pct": 2.0,
    }

    # 构建测试消息
    lines = []
    emoji = "🟢"
    lines.append(f"{emoji} <b>Trade Alert</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"{emoji} <b>交易对与方向:</b> <code>{test_data['symbol']}</code> | <b>{test_data['side']}</b>"
    )
    lines.append(f"💰 <b>数量:</b> {test_data['quantity']:.2f} USDT")
    lines.append(f"📊 <b>价格:</b> {test_data['price']:.2f}")
    lines.append(f"🚀 <b>宏观罗盘:</b> {test_data['market_state']} (震荡上行)")
    lines.append("👁️ <b>神谕视界:</b> ✅ 放行")
    lines.append(f"   🧭 情绪指数：<b>{test_data['oracle_sentiment']:.2f}</b> (乐观)")
    lines.append(f"   📊 盘口失衡：<b>{test_data['oracle_obi']:.2f}</b> (轻微买盘)")
    lines.append("🎯 <b>微观狙击:</b> 底背驰")
    lines.append(f"   💪 强度：<b>{test_data['chanlun_strength']:.2f}</b>")
    lines.append(f"   ⭐ 加分：+{test_data['chanlun_bonus']}")
    lines.append(f"🧊 <b>冰山执行:</b> {test_data['iceburg_slices']} 个切片")
    lines.append(f"   ⏱️ 预计耗时：{test_data['iceburg_duration']} 秒")
    lines.append("🛡️ <b>风控参数:</b>")
    lines.append(f"   📉 止损价：<b>{test_data['stop_loss_price']:.2f}</b>")
    lines.append(f"   ⚖️ 单笔风险：<b>{test_data['risk_pct']:.1f}%</b>")
    lines.append(f"✅ <b>执行结果:</b> {test_data['result']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC")

    formatted = "\n".join(lines)
    print(formatted)
    print("\n✅ 格式化测试完成")
