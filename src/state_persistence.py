"""
SQLite 数据持久化模块

将交易状态、历史记录从 JSON 迁移到 SQLite，解决长期运行中的性能问题。
支持增量更新、事务管理和数据恢复。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class StatePersistence:
    """
    SQLite 数据持久化管理器

    功能：
    1. 自动创建和迁移数据库架构
    2. 支持事务性操作
    3. 增量更新头寸和交易记录
    4. 快速查询和统计
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """获取数据库连接上下文管理器"""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """初始化数据库架构"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 创建表：已平仓头寸
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS closed_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    size_usdt REAL NOT NULL,
                    entry_at TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    realized_pnl_usdt REAL NOT NULL,
                    realized_pnl_pct REAL NOT NULL,
                    exit_reason TEXT,
                    signal_grade TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, entry_at, closed_at)
                )
            """)

            # 创建表：开放头寸
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS open_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    size_usdt REAL NOT NULL,
                    entry_at TEXT NOT NULL,
                    signal_grade TEXT,
                    peak_pnl_pct REAL DEFAULT 0.0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建表：交易信号日志
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signal_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    market_state TEXT,
                    signal_grade TEXT,
                    signal_side TEXT,
                    score INTEGER,
                    reason TEXT,
                    blocked_reason TEXT,
                    snapshot_json TEXT,
                    metrics_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建表：风控事件
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    timestamp TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建表：性能统计（每日快照）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL UNIQUE,
                    closed_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    win_rate_pct REAL DEFAULT 0.0,
                    total_pnl_usdt REAL DEFAULT 0.0,
                    avg_pnl_pct REAL DEFAULT 0.0,
                    daily_stop_hit BOOLEAN DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建索引以加快查询
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_closed_positions_symbol
                ON closed_positions(symbol)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_closed_positions_closed_at
                ON closed_positions(closed_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_logs_symbol
                ON signal_logs(symbol)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_signal_logs_timestamp
                ON signal_logs(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_risk_events_timestamp
                ON risk_events(timestamp)
            """)

            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    def add_closed_position(self, position: dict) -> bool:
        """
        添加已平仓头寸

        Args:
            position: 头寸字典，包含 symbol, side, entry_price, exit_price, size_usdt,
                     entry_at, closed_at, realized_pnl_usdt, realized_pnl_pct, exit_reason, signal_grade

        Returns:
            是否成功添加
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO closed_positions
                    (symbol, side, entry_price, exit_price, size_usdt, entry_at, closed_at,
                     realized_pnl_usdt, realized_pnl_pct, exit_reason, signal_grade)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        position.get("symbol"),
                        position.get("side"),
                        float(position.get("entry_price", 0)),
                        float(position.get("exit_price", 0)),
                        float(position.get("size_usdt", 0)),
                        position.get("entry_at"),
                        position.get("closed_at"),
                        float(position.get("realized_pnl_usdt", 0)),
                        float(position.get("realized_pnl_pct", 0)),
                        position.get("exit_reason"),
                        position.get("signal_grade"),
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to add closed position: {e}")
            return False

    def update_open_position(self, symbol: str, current_price: float, peak_pnl_pct: float) -> bool:
        """
        更新开放头寸的当前价格和峰值 PnL

        Args:
            symbol: 交易标的
            current_price: 当前价格
            peak_pnl_pct: 峰值 PnL 百分比

        Returns:
            是否成功更新
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE open_positions
                    SET peak_pnl_pct = MAX(peak_pnl_pct, ?), updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = ?
                """,
                    (peak_pnl_pct, symbol),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to update open position {symbol}: {e}")
            return False

    def add_signal_log(self, signal_log: dict) -> bool:
        """
        记录交易信号

        Args:
            signal_log: 信号字典，包含 symbol, timestamp, market_state, signal_grade,
                       signal_side, score, reason, blocked_reason, snapshot_json, metrics_json

        Returns:
            是否成功添加
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO signal_logs
                    (symbol, timestamp, market_state, signal_grade, signal_side, score,
                     reason, blocked_reason, snapshot_json, metrics_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        signal_log.get("symbol"),
                        signal_log.get("timestamp"),
                        signal_log.get("market_state"),
                        signal_log.get("signal_grade"),
                        signal_log.get("signal_side"),
                        int(signal_log.get("score", 0)),
                        signal_log.get("reason"),
                        signal_log.get("blocked_reason"),
                        json.dumps(signal_log.get("snapshot", {})),
                        json.dumps(signal_log.get("metrics", {})),
                    ),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to add signal log: {e}")
            return False

    def add_risk_event(self, event_type: str, symbol: str | None, details: dict) -> bool:
        """
        记录风控事件

        Args:
            event_type: 事件类型（如 'daily_stop_loss', 'consecutive_loss_pause'）
            symbol: 交易标的（可选）
            details: 事件详情字典

        Returns:
            是否成功添加
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO risk_events
                    (event_type, symbol, timestamp, details_json)
                    VALUES (?, ?, ?, ?)
                """,
                    (
                        event_type,
                        symbol,
                        datetime.now(UTC).isoformat(),
                        json.dumps(details),
                    ),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to add risk event: {e}")
            return False

    def get_closed_positions_by_symbol(self, symbol: str, limit: int = 100) -> list[dict]:
        """
        查询特定标的的已平仓头寸

        Args:
            symbol: 交易标的
            limit: 返回记录数限制

        Returns:
            头寸列表
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM closed_positions
                    WHERE symbol = ?
                    ORDER BY closed_at DESC
                    LIMIT ?
                """,
                    (symbol, limit),
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to query closed positions for {symbol}: {e}")
            return []

    def get_today_performance(self, date: str) -> dict | None:
        """
        查询特定日期的性能统计

        Args:
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            性能统计字典或 None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM daily_performance
                    WHERE date = ?
                """,
                    (date,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to query daily performance for {date}: {e}")
            return None

    def upsert_daily_performance(self, date: str, performance: dict) -> bool:
        """
        更新或插入每日性能统计

        Args:
            date: 日期字符串 (YYYY-MM-DD)
            performance: 性能统计字典

        Returns:
            是否成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO daily_performance
                    (date, closed_trades, wins, losses, win_rate_pct, total_pnl_usdt, avg_pnl_pct, daily_stop_hit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        closed_trades = excluded.closed_trades,
                        wins = excluded.wins,
                        losses = excluded.losses,
                        win_rate_pct = excluded.win_rate_pct,
                        total_pnl_usdt = excluded.total_pnl_usdt,
                        avg_pnl_pct = excluded.avg_pnl_pct,
                        daily_stop_hit = excluded.daily_stop_hit,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        date,
                        int(performance.get("closed_trades", 0)),
                        int(performance.get("wins", 0)),
                        int(performance.get("losses", 0)),
                        float(performance.get("win_rate_pct", 0)),
                        float(performance.get("total_pnl_usdt", 0)),
                        float(performance.get("avg_pnl_pct", 0)),
                        int(performance.get("daily_stop_hit", False)),
                    ),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to upsert daily performance for {date}: {e}")
            return False

    def get_statistics(self, days: int = 30) -> dict:
        """
        获取最近 N 天的统计数据

        Args:
            days: 天数

        Returns:
            统计字典
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 总体统计
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) as total_closed,
                        SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) as total_wins,
                        SUM(CASE WHEN realized_pnl_usdt < 0 THEN 1 ELSE 0 END) as total_losses,
                        AVG(realized_pnl_pct) as avg_pnl_pct,
                        SUM(realized_pnl_usdt) as total_pnl_usdt
                    FROM closed_positions
                    WHERE closed_at >= datetime('now', '-' || ? || ' days')
                """,
                    (days,),
                )
                stats = dict(cursor.fetchone())

                # 按标的统计
                cursor.execute(
                    """
                    SELECT
                        symbol,
                        COUNT(*) as count,
                        SUM(CASE WHEN realized_pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(realized_pnl_pct) as avg_pnl_pct,
                        SUM(realized_pnl_usdt) as total_pnl_usdt
                    FROM closed_positions
                    WHERE closed_at >= datetime('now', '-' || ? || ' days')
                    GROUP BY symbol
                    ORDER BY total_pnl_usdt DESC
                """,
                    (days,),
                )
                symbol_stats = [dict(row) for row in cursor.fetchall()]

                return {
                    "period_days": days,
                    "overall": stats,
                    "by_symbol": symbol_stats,
                }
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {}

    def cleanup_old_logs(self, days_to_keep: int = 90) -> int:
        """
        清理旧的日志数据

        Args:
            days_to_keep: 保留天数

        Returns:
            删除的记录数
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 清理旧的信号日志
                cursor.execute(
                    """
                    DELETE FROM signal_logs
                    WHERE created_at < datetime('now', '-' || ? || ' days')
                """,
                    (days_to_keep,),
                )
                signal_deleted = cursor.rowcount

                # 清理旧的风控事件
                cursor.execute(
                    """
                    DELETE FROM risk_events
                    WHERE created_at < datetime('now', '-' || ? || ' days')
                """,
                    (days_to_keep,),
                )
                risk_deleted = cursor.rowcount

                conn.commit()
                total_deleted = signal_deleted + risk_deleted
                logger.info(f"Cleaned up {total_deleted} old log records")
                return total_deleted
        except Exception as e:
            logger.error(f"Failed to cleanup old logs: {e}")
            return 0
