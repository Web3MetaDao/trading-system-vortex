"""
配置中心化管理模块

将所有魔法数字集中管理，支持：
1. 配置验证和默认值
2. 动态配置重载
3. 配置版本管理
4. 环境变量覆盖
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SignalConfig:
    """信号引擎配置"""
    ema_fast_period: int = 20
    ema_slow_period: int = 50
    recent_window: int = 20
    momentum_strong_min_pct: float = 2.0
    momentum_positive_min_pct: float = 0.8
    momentum_negative_max_pct: float = -0.8
    momentum_weak_max_pct: float = -2.0
    close_near_high_min: float = 0.7
    close_near_low_max: float = 0.3
    high_quote_volume_min: float = 500_000_000
    excess_intraday_volatility_min_pct: float = 8.0
    ema_alignment_bonus: int = 2
    breakout_bonus: int = 1
    pullback_bonus: int = 1
    breakdown_penalty: int = 2
    vwap_lookback_bars: int = 24
    vwap_extreme_zscore: float = 2.0
    vwap_mean_revert_zscore: float = 1.5
    vwap_breakout_zscore: float = 0.5
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskConfig:
    """风控引擎配置"""
    capital_usdt: float = 100.0
    risk_per_trade_pct: float = 1.0
    stop_loss_pct: float = 2.5
    take_profit_pct: float = 4.5
    trailing_stop_pct: float = 1.5
    grade_a_risk_multiplier: float = 1.5
    grade_b_risk_multiplier: float = 0.8
    grade_a_max_position_pct: float = 15.0
    grade_b_max_position_pct: float = 8.0
    max_open_positions: int = 2
    max_a_positions: int = 1
    max_b_positions: int = 2
    max_total_exposure_pct: float = 30.0
    daily_stop_loss_pct: float = 0.0
    consecutive_loss_pause: int = 0
    ema_exit_period: int = 20
    ema_exit_buffer_pct: float = 0.5  # 新增：EMA 退出缓冲
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DataConfig:
    """数据配置"""
    state_interval: str = "4h"
    state_limit: int = 120
    signal_interval: str = "1h"
    signal_limit: int = 120
    benchmark_symbol: str = "BTCUSDT"
    performance_recent_n: int = 10
    data_health_check_interval_seconds: int = 300
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PersistenceConfig:
    """持久化配置"""
    enable_sqlite: bool = True
    db_path: str = "state.db"
    cleanup_old_logs_days: int = 90
    cleanup_interval_hours: int = 24
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeatureFlags:
    """功能开关"""
    use_vwap_dev: bool = False
    use_intermarket_filter: bool = False
    use_oi_change: bool = False
    use_funding_shift: bool = False
    use_oracle_macro_filter: bool = False
    use_sqlite_persistence: bool = True
    enable_logging: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)


class ConfigManager:
    """
    配置中心化管理器
    
    功能：
    1. 加载和验证配置
    2. 提供默认值
    3. 支持环境变量覆盖
    4. 配置版本管理
    """
    
    def __init__(self, config_dir: Path | str = None):
        self.config_dir = Path(config_dir) if config_dir else Path.cwd()
        self.signal_config = SignalConfig()
        self.risk_config = RiskConfig()
        self.data_config = DataConfig()
        self.persistence_config = PersistenceConfig()
        self.feature_flags = FeatureFlags()
        self.version = "1.0.0"
        self._load_from_env()
    
    def _load_from_env(self):
        """从环境变量加载配置"""
        import os
        
        # 信号配置
        if val := os.getenv("SIGNAL_EMA_FAST_PERIOD"):
            self.signal_config.ema_fast_period = int(val)
        if val := os.getenv("SIGNAL_EMA_SLOW_PERIOD"):
            self.signal_config.ema_slow_period = int(val)
        if val := os.getenv("SIGNAL_VWAP_LOOKBACK_BARS"):
            self.signal_config.vwap_lookback_bars = int(val)
        
        # 风控配置
        if val := os.getenv("RISK_CAPITAL_USDT"):
            self.risk_config.capital_usdt = float(val)
        if val := os.getenv("RISK_STOP_LOSS_PCT"):
            self.risk_config.stop_loss_pct = float(val)
        if val := os.getenv("RISK_TAKE_PROFIT_PCT"):
            self.risk_config.take_profit_pct = float(val)
        if val := os.getenv("RISK_EMA_EXIT_BUFFER_PCT"):
            self.risk_config.ema_exit_buffer_pct = float(val)
        
        # 数据配置
        if val := os.getenv("DATA_STATE_INTERVAL"):
            self.data_config.state_interval = val
        if val := os.getenv("DATA_SIGNAL_INTERVAL"):
            self.data_config.signal_interval = val
        
        # 功能开关
        if val := os.getenv("FEATURE_USE_VWAP_DEV"):
            self.feature_flags.use_vwap_dev = val.lower() in ("true", "1", "yes")
        if val := os.getenv("FEATURE_USE_ORACLE_MACRO"):
            self.feature_flags.use_oracle_macro_filter = val.lower() in ("true", "1", "yes")
        if val := os.getenv("FEATURE_USE_SQLITE"):
            self.feature_flags.use_sqlite_persistence = val.lower() in ("true", "1", "yes")
        
        logger.info("Configuration loaded from environment variables")
    
    def load_from_file(self, config_file: Path | str) -> bool:
        """
        从 JSON 文件加载配置
        
        Args:
            config_file: 配置文件路径
        
        Returns:
            是否成功加载
        """
        try:
            config_file = Path(config_file)
            if not config_file.exists():
                logger.warning(f"Config file not found: {config_file}")
                return False
            
            with open(config_file, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
            
            # 加载各个配置部分
            if "signal" in config_dict:
                for key, value in config_dict["signal"].items():
                    if hasattr(self.signal_config, key):
                        setattr(self.signal_config, key, value)
            
            if "risk" in config_dict:
                for key, value in config_dict["risk"].items():
                    if hasattr(self.risk_config, key):
                        setattr(self.risk_config, key, value)
            
            if "data" in config_dict:
                for key, value in config_dict["data"].items():
                    if hasattr(self.data_config, key):
                        setattr(self.data_config, key, value)
            
            if "persistence" in config_dict:
                for key, value in config_dict["persistence"].items():
                    if hasattr(self.persistence_config, key):
                        setattr(self.persistence_config, key, value)
            
            if "feature_flags" in config_dict:
                for key, value in config_dict["feature_flags"].items():
                    if hasattr(self.feature_flags, key):
                        setattr(self.feature_flags, key, value)
            
            if "version" in config_dict:
                self.version = config_dict["version"]
            
            logger.info(f"Configuration loaded from {config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to load config from {config_file}: {e}")
            return False
    
    def save_to_file(self, config_file: Path | str) -> bool:
        """
        保存配置到 JSON 文件
        
        Args:
            config_file: 配置文件路径
        
        Returns:
            是否成功保存
        """
        try:
            config_file = Path(config_file)
            config_file.parent.mkdir(parents=True, exist_ok=True)
            
            config_dict = {
                "version": self.version,
                "signal": self.signal_config.to_dict(),
                "risk": self.risk_config.to_dict(),
                "data": self.data_config.to_dict(),
                "persistence": self.persistence_config.to_dict(),
                "feature_flags": self.feature_flags.to_dict(),
            }
            
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Configuration saved to {config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config to {config_file}: {e}")
            return False
    
    def validate(self) -> tuple[bool, list[str]]:
        """
        验证配置的合理性
        
        Returns:
            (是否有效, 错误消息列表)
        """
        errors = []
        
        # 验证信号配置
        if self.signal_config.ema_fast_period >= self.signal_config.ema_slow_period:
            errors.append("ema_fast_period must be less than ema_slow_period")
        if self.signal_config.recent_window <= 0:
            errors.append("recent_window must be positive")
        
        # 验证风控配置
        if self.risk_config.capital_usdt <= 0:
            errors.append("capital_usdt must be positive")
        if self.risk_config.stop_loss_pct <= 0:
            errors.append("stop_loss_pct must be positive")
        if self.risk_config.take_profit_pct <= 0:
            errors.append("take_profit_pct must be positive")
        if self.risk_config.max_open_positions <= 0:
            errors.append("max_open_positions must be positive")
        
        # 验证数据配置
        if self.data_config.state_limit <= 0:
            errors.append("state_limit must be positive")
        if self.data_config.signal_limit <= 0:
            errors.append("signal_limit must be positive")
        
        return len(errors) == 0, errors
    
    def get_summary(self) -> str:
        """获取配置摘要"""
        lines = [
            "=== Configuration Summary ===",
            f"Version: {self.version}",
            "",
            "Signal Config:",
            f"  EMA: fast={self.signal_config.ema_fast_period}, slow={self.signal_config.ema_slow_period}",
            f"  VWAP: lookback={self.signal_config.vwap_lookback_bars} bars",
            "",
            "Risk Config:",
            f"  Capital: {self.risk_config.capital_usdt} USDT",
            f"  Stop Loss: {self.risk_config.stop_loss_pct}%",
            f"  Take Profit: {self.risk_config.take_profit_pct}%",
            f"  EMA Exit Buffer: {self.risk_config.ema_exit_buffer_pct}%",
            f"  Max Open Positions: {self.risk_config.max_open_positions}",
            "",
            "Data Config:",
            f"  State Interval: {self.data_config.state_interval}",
            f"  Signal Interval: {self.data_config.signal_interval}",
            "",
            "Feature Flags:",
            f"  VWAP Dev: {self.feature_flags.use_vwap_dev}",
            f"  Intermarket Filter: {self.feature_flags.use_intermarket_filter}",
            f"  OI Change: {self.feature_flags.use_oi_change}",
            f"  Funding Shift: {self.feature_flags.use_funding_shift}",
            f"  Oracle Macro Filter: {self.feature_flags.use_oracle_macro_filter}",
            f"  SQLite Persistence: {self.feature_flags.use_sqlite_persistence}",
        ]
        return "\n".join(lines)
