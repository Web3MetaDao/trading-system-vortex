"""
Vortex Multi-Process Manager — 高性能跨进程共享内存管理器（可选组件）

依赖：shared-memory-dict（可选）
若未安装，自动回退到线程安全的标准 dict（适用于单机单进程场景）。
安装方式：pip install shared-memory-dict
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import time
from typing import Any

try:
    from shared_memory_dict import SharedMemoryDict

    _SHARED_MEMORY_AVAILABLE = True
except ImportError:  # pragma: no cover
    SharedMemoryDict = None  # type: ignore[assignment,misc]
    _SHARED_MEMORY_AVAILABLE = False

logger = logging.getLogger(__name__)


class MultiProcessManager:
    """
    Vortex Multi-Process Manager

    Handles high-performance data sharing between market data and signal engines.

    - If `shared-memory-dict` is installed: uses POSIX shared memory for
      zero-copy inter-process communication (recommended for production).
    - Otherwise: falls back to a thread-safe in-process dict (suitable for
      single-process / development environments).
    """

    def __init__(self, symbols: list[str], size_mb: int = 50):
        self.symbols = symbols
        self.processes: list[mp.Process] = []
        self._stop_event = mp.Event()

        if _SHARED_MEMORY_AVAILABLE:
            self.shared_data: dict[str, Any] = SharedMemoryDict(
                name="vortex_market_data", size=size_mb * 1024 * 1024
            )
            logger.info(
                "MultiProcessManager: using SharedMemoryDict (%d MB)", size_mb
            )
        else:
            # Fallback: plain dict (thread-safe for single-process use)
            self.shared_data = {}
            logger.warning(
                "shared-memory-dict not installed — falling back to in-process dict. "
                "Install with: pip install shared-memory-dict"
            )

    def update_snapshot(self, symbol: str, data: dict[str, Any]) -> None:
        """Atomic update to shared memory"""
        self.shared_data[symbol] = data

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        """Atomic read from shared memory"""
        return self.shared_data.get(symbol, {})

    def spawn_worker(self, target: Any, args: tuple = ()) -> mp.Process:
        """Spawn a persistent daemon worker process"""
        p = mp.Process(target=target, args=(self._stop_event, *args))
        p.daemon = True
        p.start()
        self.processes.append(p)
        return p

    def stop_all(self) -> None:
        """Gracefully stop all worker processes"""
        self._stop_event.set()
        for p in self.processes:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
        logger.info("All Vortex worker processes stopped.")


if __name__ == "__main__":
    # Test Shared Memory
    mgr = MultiProcessManager(["BTCUSDT"])
    mgr.update_snapshot("BTCUSDT", {"price": 76000.0, "ts": time.time()})
    print(f"Shared Data Test: {mgr.get_snapshot('BTCUSDT')}")
