import asyncio
import logging
from typing import Dict, Any, List
from cross_exchange_manager import CrossExchangeManager

logger = logging.getLogger(__name__)

class ArbitrageEngine:
    """
    Vortex Arbitrage Engine
    Identifies price discrepancies between multiple exchanges and executes risk-neutral trades.
    """
    def __init__(self, exchange_mgr: CrossExchangeManager, symbol: str = 'BTC/USDT'):
        self.exchange_mgr = exchange_mgr
        self.symbol = symbol
        self.min_profit_pct = 0.2  # 0.2% minimum profit after fees
        self.trade_amount = 0.001  # Minimum BTC amount for testing

    async def find_arbitrage_opportunities(self) -> List[Dict[str, Any]]:
        """Identify arbitrage pairs: (Buy Low, Sell High)"""
        tickers = await self.exchange_mgr.fetch_tickers(self.symbol)
        if len(tickers) < 2: return []
        
        opportunities = []
        names = list(tickers.keys())
        
        # Compare all pairs of exchanges
        for i in range(len(names)):
            for j in range(len(names)):
                if i == j: continue
                
                ex_buy = names[i]
                ex_sell = names[j]
                
                # Buy at Ask (lowest sell price), Sell at Bid (highest buy price)
                buy_price = tickers[ex_buy]['ask']
                sell_price = tickers[ex_sell]['bid']
                
                if buy_price and sell_price:
                    spread = sell_price - buy_price
                    profit_pct = (spread / buy_price) * 100
                    
                    if profit_pct > self.min_profit_pct:
                        opportunities.append({
                            "buy_exchange": ex_buy,
                            "sell_exchange": ex_sell,
                            "buy_price": buy_price,
                            "sell_price": sell_price,
                            "profit_pct": profit_pct
                        })
        return sorted(opportunities, key=lambda x: x['profit_pct'], reverse=True)

    async def run_arbitrage_loop(self, duration: int = 30):
        """Continuous arbitrage monitoring loop"""
        logger.info(f"--- Vortex Arbitrage Engine Started: {self.symbol} ---")
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < duration:
            try:
                opps = await self.find_arbitrage_opportunities()
                if opps:
                    top = opps[0]
                    logger.info(f"OPPORTUNITY FOUND: Buy {top['buy_exchange']} @ {top['buy_price']}, Sell {top['sell_exchange']} @ {top['sell_price']} | Profit: {top['profit_pct']:.4f}%")
                await asyncio.sleep(2)  # 2s polling
            except Exception as e:
                logger.error(f"Arbitrage Engine Error: {e}")
                await asyncio.sleep(5)
        logger.info("Arbitrage monitoring completed.")

if __name__ == "__main__":
    async def main():
        logging.basicConfig(level=logging.INFO)
        mgr = CrossExchangeManager()
        engine = ArbitrageEngine(mgr)
        await engine.run_arbitrage_loop(duration=10)
        await mgr.close()
    
    asyncio.run(main())
