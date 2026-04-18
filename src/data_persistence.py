import sqlite3
import pandas as pd
import os
import logging
from datetime import datetime, UTC
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class DataPersistence:
    """
    Vortex Data Persistence Layer
    Handles local storage of historical data for strategy training and backtesting.
    """
    def __init__(self, db_path: str = "/home/ubuntu/vortex/data/vortex_history.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create historical klines table"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS klines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    quote_volume REAL,
                    UNIQUE(symbol, open_time)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_symbol_time ON klines(symbol, open_time)')
            conn.commit()

    def save_klines(self, symbol: str, klines: List[Dict[str, Any]]):
        """Batch save klines to SQLite"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.DataFrame(klines)
            df['symbol'] = symbol.upper()
            df.to_sql('klines', conn, if_exists='append', index=False, method='multi', 
                      chunksize=1000)
            conn.commit()

    def load_history(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        """Load historical klines for analysis"""
        with sqlite3.connect(self.db_path) as conn:
            query = f"SELECT * FROM klines WHERE symbol = ? ORDER BY open_time DESC LIMIT ?"
            return pd.read_sql_query(query, conn, params=(symbol.upper(), limit))

if __name__ == "__main__":
    dp = DataPersistence()
    print("Database initialized at:", dp.db_path)
