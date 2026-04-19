import logging
import multiprocessing as mp
import time
from typing import Any

from shared_memory_dict import SharedMemoryDict

logger = logging.getLogger(__name__)


class MultiProcessManager:
    """
    Vortex Multi-Process Manager
    Handles high-performance data sharing between market data and signal engines.
    """

    def __init__(self, symbols: list[str], size_mb: int = 50):
        self.symbols = symbols
        # Initialize Shared Memory for Market Snapshots
        self.shared_data = SharedMemoryDict(name="vortex_market_data", size=size_mb * 1024 * 1024)
        self.processes = []
        self._stop_event = mp.Event()

    def update_snapshot(self, symbol: str, data: dict[str, Any]):
        """Atomic update to shared memory"""
        self.shared_data[symbol] = data

    def get_snapshot(self, symbol: str) -> dict[str, Any]:
        """Atomic read from shared memory"""
        return self.shared_data.get(symbol, {})

    def spawn_worker(self, target, args=()):
        """Spawn a persistent worker process"""
        p = mp.Process(target=target, args=(self._stop_event, *args))
        p.daemon = True
        p.start()
        self.processes.append(p)
        return p

    def stop_all(self):
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
