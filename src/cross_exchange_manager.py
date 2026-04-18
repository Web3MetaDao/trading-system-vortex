import ccxt.async_support as ccxt
import asyncio
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class CrossExchangeManager:
    """
    Vortex Cross-Exchange Manager
    Handles asynchronous connections to multiple exchanges using CCXT.
    """
    def __init__(self, exchange_configs: Dict[str, Dict[str, Any]] = None):
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        configs = exchange_configs or {'binance': {}, 'okx': {}, 'bybit': {}}
        for name, config in configs.items():
            exchange_class = getattr(ccxt, name.lower())
            self.exchanges[name] = exchange_class({
                'apiKey': config.get('apiKey'),
                'secret': config.get('secret'),
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })

    async def fetch_tickers(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Fetch real-time tickers from all configured exchanges in parallel"""
        tasks = []
        names = []
        for name, exchange in self.exchanges.items():
            tasks.append(exchange.fetch_ticker(symbol))
            names.append(name)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        tickers = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.error(f"Error fetching ticker from {name}: {result}")
            else:
                tickers[name] = result
        return tickers

    async def close(self):
        for exchange in self.exchanges.values():
            await exchange.close()

if __name__ == "__main__":
    async def test():
        mgr = CrossExchangeManager()
        tickers = await mgr.fetch_tickers('BTC/USDT')
        for ex, t in tickers.items():
            print(f"{ex}: Bid={t['bid']}, Ask={t['ask']}")
        await mgr.close()
    
    asyncio.run(test())
