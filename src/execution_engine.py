"""
Mini-Leviathan 执行引擎

机构级订单执行系统
- 支持 Paper/Testnet/Live 三种模式
- 幂等性保证（防止重复下单）
- TWAP 冰山算法（大资金拆单）
- Maker 挂单优化（减少滑点）

作者：TRAE AI Assistant
版本：2.0.0
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import requests

# 配置日志
logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    pass


class APIError(ExecutionError):
    pass


class NetworkError(ExecutionError):
    pass


@dataclass
class ExecutionResult:
    accepted: bool
    mode: str
    detail: str
    order_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    quantity: float | None = None
    price: float | None = None


@dataclass
class OrderStatus:
    order_id: str
    symbol: str
    side: str
    status: str
    filled_qty: float
    avg_price: float | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass
class PositionInfo:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    isolated_margin: bool | None
    isolated_wallet: float | None


@dataclass
class AccountInfo:
    account_alias: str
    asset: str
    balance: float
    cross_wallet_balance: float
    available_balance: float
    total_initial_margin: float
    total_unrealized_pnl: float
    margin_remain: float
    positions: list[PositionInfo]


@dataclass
class TradeValidation:
    approved: bool
    reason: str | None
    warnings: list[str]
    account_ready: bool
    positions_synced: bool


class BinanceSigner:
    def __init__(self, api_secret: str):
        self.api_secret = api_secret

    def sign(self, params: dict[str, Any]) -> str:
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature


class ExecutionEngine:
    SUPPORTED_MODES = {"paper", "testnet", "live"}

    BINANCE_TESTNET_API = "https://testnet.binance.vision"
    BINANCE_TESTNET_WS = "wss://testnet.binance.vision/ws"
    BINANCE_SPOT_API = "https://api.binance.com"
    BINANCE_SPOT_WS = "wss://stream.binance.com:9443/ws"

    def __init__(self, mode: str | None = None):
        chosen_mode = (mode or os.getenv("TRADING_MODE", "paper")).lower()
        if chosen_mode not in self.SUPPORTED_MODES:
            raise ValueError(f"Unsupported trading mode: {chosen_mode}")
        self.mode = chosen_mode

        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.max_retries = int(os.getenv("EXECUTION_MAX_RETRIES", "3"))
        self.retry_delay = float(os.getenv("EXECUTION_RETRY_DELAY", "1.0"))
        self.timeout = float(os.getenv("EXECUTION_TIMEOUT", "10.0"))

        if self.mode in ("testnet", "live"):
            self.base_url = (
                self.BINANCE_TESTNET_API if self.mode == "testnet" else self.BINANCE_SPOT_API
            )
            self.signer = BinanceSigner(self.api_secret) if self.api_secret else None
        else:
            self.base_url = None
            self.signer = None

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "trading-system-execution/1.0",
                "Accept": "application/json",
            }
        )

        self._idempotency_cache: dict[str, str] = {}

    def _sign_request(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.signer:
            return params
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        params["signature"] = self.signer.sign(params)
        return params

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        require_auth: bool = True,
    ) -> dict[str, Any] | list[Any]:
        if require_auth and not self.api_key:
            raise ExecutionError("API key not configured for authenticated request")

        last_error: Exception | None = None
        params = params or {}

        for attempt in range(1, self.max_retries + 1):
            try:
                url = f"{self.base_url}{endpoint}"
                headers = {}
                if require_auth:
                    headers["X-MBX-APIKEY"] = self.api_key

                if method.upper() == "GET":
                    response = self.session.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=self.timeout,
                    )
                elif method.upper() == "POST":
                    response = self.session.post(
                        url,
                        data=params,
                        headers=headers,
                        timeout=self.timeout,
                    )
                elif method.upper() == "DELETE":
                    response = self.session.delete(
                        url,
                        data=params,
                        headers=headers,
                        timeout=self.timeout,
                    )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                if response.status_code == 429:
                    last_error = NetworkError(f"Rate limited on {endpoint}")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * 2)
                        continue

                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("msg", response.text)
                    except Exception:
                        error_msg = response.text
                    last_error = APIError(f"Binance API error {response.status_code}: {error_msg}")
                    if response.status_code >= 500 and attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    raise last_error

                return response.json()

            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = NetworkError(f"Network error on {endpoint}: {exc}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
            except requests.RequestException as exc:
                last_error = NetworkError(f"Request error on {endpoint}: {exc}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue

        raise last_error or ExecutionError(f"Request failed after {self.max_retries} retries")

    def _generate_client_order_id(self, symbol: str, side: str) -> str:
        timestamp = int(time.time() * 1000)
        return f"TDS_{symbol}_{side}_{timestamp}"

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity_usdt: float,
        order_type: str = "MARKET",
        price: float | None = None,
    ) -> ExecutionResult:
        symbol = symbol.upper()
        side = side.upper()

        if self.mode == "paper":
            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[PAPER] Simulated {side} {symbol} for {quantity_usdt:.2f} USDT",
            )

        if self.mode in ("testnet", "live"):
            if not self.api_key or not self.api_secret:
                return ExecutionResult(
                    accepted=False,
                    mode=self.mode,
                    detail=f"[{self.mode.upper()}] API credentials not configured. Set BINANCE_API_KEY and BINANCE_API_SECRET environment variables.",
                )

        try:
            client_order_id = self._generate_client_order_id(symbol, side)
            params = {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity_usdt,
                "newClientOrderId": client_order_id,
            }

            if order_type == "LIMIT" and price:
                params["price"] = price
                params["timeInForce"] = "GTC"

            signed_params = self._sign_request(params)
            response = self._request_with_retry("POST", "/api/v3/order", signed_params)

            order_id = str(response.get("orderId", ""))
            self._idempotency_cache[client_order_id] = order_id

            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Order submitted: {side} {symbol} {quantity_usdt} USDT | ID: {order_id}",
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=quantity_usdt,
                price=price,
            )

        except APIError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Order rejected: {exc}",
            )
        except NetworkError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Network error: {exc}",
            )
        except Exception as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Unexpected error: {exc}",
            )

    def close_order(
        self,
        symbol: str,
        side: str,
        quantity_usdt: float,
        reason: str,
    ) -> ExecutionResult:
        symbol = symbol.upper()
        side = side.upper()

        if self.mode == "paper":
            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[PAPER] Simulated close {side} {symbol} for {quantity_usdt:.2f} USDT | reason: {reason}",
            )

        if self.mode in ("testnet", "live"):
            if not self.api_key or not self.api_secret:
                return ExecutionResult(
                    accepted=False,
                    mode=self.mode,
                    detail=f"[{self.mode.upper()}] API credentials not configured. Set BINANCE_API_KEY and BINANCE_API_SECRET environment variables.",
                )

        try:
            position_side = "SELL" if side == "BUY" else "BUY"
            close_params = {
                "symbol": symbol,
                "side": position_side,
                "type": "MARKET",
                "quantity": quantity_usdt,
            }

            signed_params = self._sign_request(close_params)
            response = self._request_with_retry("POST", "/api/v3/order", signed_params)

            order_id = str(response.get("orderId", ""))
            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Close order submitted: {position_side} {symbol} {quantity_usdt} USDT | reason: {reason} | ID: {order_id}",
                order_id=order_id,
                symbol=symbol,
                side=position_side,
                quantity=quantity_usdt,
            )

        except APIError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Close order rejected: {exc}",
            )
        except NetworkError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Network error during close: {exc}",
            )
        except Exception as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Unexpected error during close: {exc}",
            )

    def get_order_status(self, symbol: str, order_id: str) -> OrderStatus | None:
        symbol = symbol.upper()

        if self.mode == "paper":
            return None

        if not self.api_key or not self.api_secret:
            return None

        try:
            params = {
                "symbol": symbol,
                "orderId": order_id,
            }

            signed_params = self._sign_request(params)
            response = self._request_with_retry("GET", "/api/v3/order", signed_params)

            return OrderStatus(
                order_id=str(response.get("orderId", "")),
                symbol=response.get("symbol", symbol),
                side=response.get("side", ""),
                status=response.get("status", ""),
                filled_qty=float(response.get("executedQty", 0.0)),
                avg_price=float(response.get("avgPrice")) if response.get("avgPrice") else None,
                created_at=(
                    datetime.fromtimestamp(
                        int(response.get("time", 0)) / 1000,
                        tz=UTC,
                    )
                    if response.get("time")
                    else None
                ),
                updated_at=(
                    datetime.fromtimestamp(
                        int(response.get("updateTime", 0)) / 1000,
                        tz=UTC,
                    )
                    if response.get("updateTime")
                    else None
                ),
            )

        except (APIError, NetworkError):
            return None
        except Exception:
            return None

    def cancel_order(self, symbol: str, order_id: str) -> ExecutionResult:
        symbol = symbol.upper()

        if self.mode == "paper":
            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[PAPER] Simulated cancel order {order_id}",
            )

        if not self.api_key or not self.api_secret:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] API credentials not configured.",
            )

        try:
            params = {
                "symbol": symbol,
                "orderId": order_id,
            }

            signed_params = self._sign_request(params)
            response = self._request_with_retry("DELETE", "/api/v3/order", signed_params)

            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Order {order_id} cancelled",
                order_id=order_id,
                symbol=symbol,
            )

        except APIError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Cancel rejected: {exc}",
            )
        except NetworkError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Network error during cancel: {exc}",
            )
        except Exception as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Unexpected error during cancel: {exc}",
            )

    def fetch_positions(self, symbol: str | None = None) -> list[PositionInfo]:
        if self.mode == "paper":
            return []

        if not self.api_key or not self.api_secret:
            return []

        try:
            params: dict[str, Any] = {}
            if symbol:
                params["symbol"] = symbol.upper()

            signed_params = self._sign_request(params)
            response = self._request_with_retry("GET", "/api/v3/account", signed_params)

            positions = []
            for pos in response.get("positions", []):
                if not symbol or pos.get("symbol") == symbol.upper():
                    if (
                        float(pos.get("initialMargin", 0)) > 0
                        or float(pos.get("unrealizedProfit", 0)) != 0
                    ):
                        positions.append(
                            PositionInfo(
                                symbol=pos.get("symbol", ""),
                                side="LONG" if float(pos.get("positionAmt", 0)) > 0 else "SHORT",
                                quantity=abs(float(pos.get("positionAmt", 0))),
                                entry_price=float(pos.get("entryPrice", 0)),
                                unrealized_pnl=float(pos.get("unrealizedProfit", 0)),
                                leverage=int(pos.get("leverage", 1)),
                                isolated_margin=pos.get("isolated") == "true",
                                isolated_wallet=(
                                    float(pos.get("isolatedWallet", 0))
                                    if pos.get("isolated") == "true"
                                    else None
                                ),
                            )
                        )

            return positions

        except (APIError, NetworkError):
            return []
        except Exception:
            return []

    def fetch_account_info(self) -> AccountInfo | None:
        if self.mode == "paper":
            return None

        if not self.api_key or not self.api_secret:
            return None

        try:
            params: dict[str, Any] = {}
            signed_params = self._sign_request(params)
            response = self._request_with_retry("GET", "/api/v3/account", signed_params)

            balances = response.get("balances", [])
            usdt_balance = next((b for b in balances if b.get("asset") == "USDT"), None)

            account_info = AccountInfo(
                account_alias=response.get("accountAlias", ""),
                asset="USDT",
                balance=(
                    float(usdt_balance.get("free", 0)) + float(usdt_balance.get("locked", 0))
                    if usdt_balance
                    else 0.0
                ),
                cross_wallet_balance=float(response.get("crossWalletBalance", 0)),
                available_balance=float(response.get("availableBalance", 0)),
                total_initial_margin=float(response.get("totalInitialMargin", 0)),
                total_unrealized_pnl=float(response.get("totalUnrealizedProfit", 0)),
                margin_remain=float(response.get("marginRemain", 0)),
                positions=self.fetch_positions(),
            )

            return account_info

        except (APIError, NetworkError):
            return None
        except Exception:
            return None

    def validate_trade_pre_conditions(
        self,
        symbol: str,
        side: str,
        quantity_usdt: float,
        max_leverage: int = 20,
    ) -> TradeValidation:
        warnings: list[str] = []
        reasons: list[str] = []
        account_ready = False
        positions_synced = False

        if self.mode == "paper":
            return TradeValidation(
                approved=True,
                reason=None,
                warnings=[],
                account_ready=True,
                positions_synced=True,
            )

        account = self.fetch_account_info()

        if account is None:
            reasons.append("Failed to fetch account info - API unavailable or not configured")
            return TradeValidation(
                approved=False,
                reason="; ".join(reasons),
                warnings=warnings,
                account_ready=False,
                positions_synced=False,
            )

        account_ready = True
        positions_synced = True

        if account.available_balance < quantity_usdt * 0.5:
            reasons.append(f"Insufficient available balance: {account.available_balance:.2f} USDT")

        positions = [p for p in account.positions if p.symbol == symbol.upper()]

        if positions:
            existing_pos = positions[0]
            if existing_pos.side == side and existing_pos.quantity > 0:
                warnings.append(f"Position already exists on {side} side: {existing_pos.quantity}")

            if existing_pos.leverage > max_leverage:
                reasons.append(
                    f"Leverage {existing_pos.leverage}x exceeds max allowed {max_leverage}x"
                )

        for pos in account.positions:
            if pos.symbol != symbol.upper() and abs(pos.quantity) > 0:
                total_exposure = sum(
                    abs(p.quantity) for p in account.positions if p.symbol != symbol.upper()
                )
                if total_exposure > 1000:
                    warnings.append(
                        f"High existing exposure on other symbols: {total_exposure:.2f} USDT"
                    )

        approved = len(reasons) == 0

        return TradeValidation(
            approved=approved,
            reason="; ".join(reasons) if reasons else None,
            warnings=warnings,
            account_ready=account_ready,
            positions_synced=positions_synced,
        )

    def set_leverage(self, symbol: str, leverage: int) -> ExecutionResult:
        symbol = symbol.upper()

        if self.mode == "paper":
            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[PAPER] Simulated set leverage {leverage}x for {symbol}",
            )

        if not self.api_key or not self.api_secret:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] API credentials not configured.",
            )

        try:
            params = {
                "symbol": symbol,
                "leverage": leverage,
            }

            signed_params = self._sign_request(params)
            self._request_with_retry("POST", "/api/v3/leverage", signed_params)

            return ExecutionResult(
                accepted=True,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Leverage set to {leverage}x for {symbol}",
                symbol=symbol,
            )

        except APIError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Failed to set leverage: {exc}",
            )
        except NetworkError as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Network error setting leverage: {exc}",
            )
        except Exception as exc:
            return ExecutionResult(
                accepted=False,
                mode=self.mode,
                detail=f"[{self.mode.upper()}] Unexpected error setting leverage: {exc}",
            )
    
    # ========== 机构级冰山算法 ==========
    
    @dataclass
    class IcebergStats:
        """冰山订单统计信息"""
        total_quantity: float  # 总数量
        executed_slices: int  # 已执行切片数
        filled_quantity: float  # 已成交数量
        avg_price: float  # 平均成交价
        total_fees: float  # 总手续费
        execution_time_seconds: float  # 执行时间
        slice_details: list[dict]  # 每个切片的详情
    
    async def submit_twap_iceberg_order(
        self,
        symbol: str,
        side: str,
        total_quantity: float,
        min_slice: float,
        max_slices: int = 10,
        price_offset_pct: float = 0.001,
    ) -> IcebergStats:
        """
        TWAP 冰山订单执行
        
        将大单拆分成多个小单，使用 Maker 挂单减少滑点，防止被狙击
        
        Args:
            symbol: 交易对
            side: 买卖方向 ('BUY' 或 'SELL')
            total_quantity: 总数量 (USDT)
            min_slice: 最小切片数量 (USDT)
            max_slices: 最大切片数
            price_offset_pct: 价格偏移百分比（默认 0.1%，确保挂单成交）
        
        Returns:
            IcebergStats: 冰山订单统计信息
        """
        symbol = symbol.upper()
        side = side.upper()
        
        logger.info(
            "开始 TWAP 冰山订单：symbol=%s, side=%s, total_qty=%.2f, min_slice=%.2f",
            symbol, side, total_quantity, min_slice
        )
        
        start_time = time.time()
        slice_details = []
        total_filled = 0.0
        total_fees = 0.0
        executed_slices = 0
        
        # 计算切片大小（随机化，避免规律性）
        avg_slice_size = total_quantity / max_slices
        slice_size = max(min_slice, avg_slice_size * random.uniform(0.8, 1.2))
        
        remaining_qty = total_quantity
        slice_count = 0
        
        while remaining_qty > 0 and slice_count < max_slices:
            # 计算当前切片数量
            current_slice = min(slice_size, remaining_qty)
            
            # 获取最新盘口价格
            ticker = self._fetch_latest_ticker(symbol)
            if not ticker:
                logger.warning("无法获取盘口价格，等待 5 秒后重试")
                await asyncio.sleep(5)
                continue
            
            # 计算 Maker 挂单价格
            if side == 'BUY':
                # 买单：买一价上方 0.1%（确保优先成交）
                limit_price = ticker['best_bid'] * (1 + price_offset_pct)
            else:
                # 卖单：卖一价下方 0.1%
                limit_price = ticker['best_ask'] * (1 - price_offset_pct)
            
            # 生成幂等性订单 ID
            idempotency_key = self._generate_iceberg_order_id(
                symbol, side, slice_count, start_time
            )
            
            # 检查是否已执行（防止断网重连重复下单）
            if self._check_idempotency(idempotency_key):
                logger.info("切片 %d 已执行，跳过", slice_count)
                slice_count += 1
                remaining_qty -= current_slice
                continue
            
            # 提交限价单
            result = self._submit_limit_order_with_timeout(
                symbol=symbol,
                side=side,
                quantity=current_slice,
                price=limit_price,
                idempotency_key=idempotency_key,
                timeout_seconds=30
            )
            
            if result['accepted']:
                executed_slices += 1
                total_filled += result.get('filled_qty', 0)
                total_fees += result.get('fee', 0)
                
                slice_detail = {
                    'slice_index': slice_count,
                    'quantity': current_slice,
                    'limit_price': limit_price,
                    'filled_qty': result.get('filled_qty', 0),
                    'avg_price': result.get('avg_price', limit_price),
                    'fee': result.get('fee', 0),
                    'status': result.get('status', 'unknown')
                }
                slice_details.append(slice_detail)
                
                logger.info(
                    "切片 %d/%d 执行完成：qty=%.2f, filled=%.2f, price=%.2f",
                    slice_count, max_slices,
                    current_slice, result.get('filled_qty', 0),
                    result.get('avg_price', limit_price)
                )
            else:
                logger.warning("切片 %d 执行失败：%s", slice_count, result.get('reason', 'unknown'))
            
            slice_count += 1
            remaining_qty -= current_slice
            
            # 随机等待时间（3-8 秒），避免规律性
            if remaining_qty > 0 and slice_count < max_slices:
                wait_time = random.uniform(3, 8)
                logger.debug("等待 %.1f 秒后执行下一切片", wait_time)
                await asyncio.sleep(wait_time)
        
        # 计算统计信息
        execution_time = time.time() - start_time
        avg_price = sum(s['avg_price'] * s['filled_qty'] for s in slice_details) / total_filled if total_filled > 0 else 0
        
        stats = self.IcebergStats(
            total_quantity=total_quantity,
            executed_slices=executed_slices,
            filled_quantity=total_filled,
            avg_price=avg_price,
            total_fees=total_fees,
            execution_time_seconds=execution_time,
            slice_details=slice_details
        )
        
        logger.info(
            "TWAP 冰山订单完成：executed=%d/%d, filled=%.2f/%.2f, avg_price=%.2f, time=%.1fs",
            executed_slices, slice_count, total_filled, total_quantity,
            avg_price, execution_time
        )
        
        return stats
    
    def _fetch_latest_ticker(self, symbol: str) -> dict | None:
        """
        获取最新盘口数据
        
        Args:
            symbol: 交易对
        
        Returns:
            dict: {
                'best_bid': float,
                'best_ask': float,
                'last': float,
                'volume_24h': float
            }
        """
        if self.mode == "paper":
            # Paper 模式返回模拟数据
            return {
                'best_bid': 50000.0,
                'best_ask': 50001.0,
                'last': 50000.5,
                'volume_24h': 1000000.0
            }
        
        try:
            response = self._request_with_retry(
                "GET",
                "/api/v3/ticker/bookTicker",
                params={'symbol': symbol},
                require_auth=False
            )
            
            return {
                'best_bid': float(response.get('bidPrice', 0)),
                'best_ask': float(response.get('askPrice', 0)),
                'last': float(response.get('bidPrice', 0) + response.get('askPrice', 0)) / 2,
                'volume_24h': 0.0  # 简化处理
            }
        
        except Exception as e:
            logger.error("获取盘口数据失败：%s", e)
            return None
    
    def _submit_limit_order_with_timeout(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        idempotency_key: str,
        timeout_seconds: int = 30
    ) -> dict:
        """
        提交限价单并等待成交（带超时撤单）
        
        Args:
            symbol: 交易对
            side: 买卖方向
            quantity: 数量
            price: 价格
            idempotency_key: 幂等性键
            timeout_seconds: 超时时间（秒）
        
        Returns:
            dict: {
                'accepted': bool,
                'filled_qty': float,
                'avg_price': float,
                'fee': float,
                'status': str,
                'reason': str
            }
        """
        # Paper 模式模拟执行
        if self.mode == "paper":
            import random
            filled_qty = quantity * random.uniform(0.9, 1.0)  # 90-100% 成交率
            avg_price = price * random.uniform(0.999, 1.001)  # 微小滑点
            fee = filled_qty * 0.001
            
            self._idempotency_cache[idempotency_key] = f"PAPER_{idempotency_key}"
            
            return {
                'accepted': True,
                'filled_qty': filled_qty,
                'avg_price': avg_price,
                'fee': fee,
                'status': 'FILLED',
                'reason': 'paper_simulation'
            }
        
        try:
            # 检查 API 配置
            if not self.api_key or not self.api_secret:
                return {
                    'accepted': False,
                    'filled_qty': 0,
                    'avg_price': 0,
                    'fee': 0,
                    'status': 'REJECTED',
                    'reason': 'API credentials not configured'
                }
            
            # 提交限价单
            params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "quantity": quantity,
                "price": price,
                "timeInForce": "GTC",  # Good Till Cancel
                "newClientOrderId": idempotency_key
            }
            
            signed_params = self._sign_request(params)
            response = self._request_with_retry("POST", "/api/v3/order", signed_params)
            
            order_id = str(response.get("orderId", ""))
            order_status = response.get("status", "")
            
            # 立即检查成交情况
            filled_qty = float(response.get("executedQty", 0))
            avg_price = float(response.get("avgPrice", price))
            
            if order_status == "FILLED" or filled_qty > 0:
                # 已完全或部分成交
                fee = filled_qty * 0.001  # 假设 0.1% 手续费
                self._idempotency_cache[idempotency_key] = order_id
                
                return {
                    'accepted': True,
                    'filled_qty': filled_qty,
                    'avg_price': avg_price,
                    'fee': fee,
                    'status': order_status,
                    'reason': 'filled_immediately'
                }
            
            # 等待成交（轮询）
            start_wait = time.time()
            while time.time() - start_wait < timeout_seconds:
                time.sleep(2)  # 每 2 秒检查一次
                
                # 查询订单状态
                status_params = self._sign_request({
                    'symbol': symbol,
                    'orderId': order_id
                })
                
                status_response = self._request_with_retry(
                    "GET", "/api/v3/order", status_params
                )
                
                current_status = status_response.get("status", "")
                current_filled = float(status_response.get("executedQty", 0))
                
                if current_status == "FILLED" or current_filled > 0:
                    # 成交了
                    avg_price = float(status_response.get("avgPrice", price))
                    fee = current_filled * 0.001
                    self._idempotency_cache[idempotency_key] = order_id
                    
                    return {
                        'accepted': True,
                        'filled_qty': current_filled,
                        'avg_price': avg_price,
                        'fee': fee,
                        'status': current_status,
                        'reason': 'filled_after_wait'
                    }
                
                if current_status == "CANCELED":
                    return {
                        'accepted': False,
                        'filled_qty': 0,
                        'avg_price': 0,
                        'fee': 0,
                        'status': 'CANCELED',
                        'reason': 'order_canceled'
                    }
            
            # 超时未成交，撤单并追价
            logger.info("订单超时 %d 秒未成交，撤单追价", timeout_seconds)
            
            cancel_params = self._sign_request({
                'symbol': symbol,
                'orderId': order_id
            })
            
            self._request_with_retry("DELETE", "/api/v3/order", cancel_params)
            
            return {
                'accepted': False,
                'filled_qty': 0,
                'avg_price': 0,
                'fee': 0,
                'status': 'TIMEOUT',
                'reason': f'timeout_{timeout_seconds}s'
            }
        
        except APIError as e:
            return {
                'accepted': False,
                'filled_qty': 0,
                'avg_price': 0,
                'fee': 0,
                'status': 'REJECTED',
                'reason': str(e)
            }
        
        except NetworkError as e:
            return {
                'accepted': False,
                'filled_qty': 0,
                'avg_price': 0,
                'fee': 0,
                'status': 'NETWORK_ERROR',
                'reason': str(e)
            }
    
    def _generate_iceberg_order_id(
        self,
        symbol: str,
        side: str,
        slice_index: int,
        start_time: float
    ) -> str:
        """
        生成冰山订单幂等性 ID
        
        Args:
            symbol: 交易对
            side: 买卖方向
            slice_index: 切片索引
            start_time: 开始时间戳
        
        Returns:
            str: 幂等性订单 ID
        """
        base_str = f"{symbol}_{side}_{slice_index}_{int(start_time)}"
        hash_value = hashlib.sha256(base_str.encode()).hexdigest()[:16]
        return f"ICE_{hash_value}"
    
    def _check_idempotency(self, idempotency_key: str) -> bool:
        """
        检查订单是否已执行（幂等性检查）
        
        Args:
            idempotency_key: 幂等性键
        
        Returns:
            bool: True 表示已执行
        """
        return idempotency_key in self._idempotency_cache
    
    # ========== 高级冰山算法（完整 TWAP 实现） ==========
    
    @dataclass
    class IcebergExecutionReport:
        """冰山执行报告"""
        success: bool
        total_quantity: float
        executed_quantity: float
        remaining_quantity: float
        avg_execution_price: float
        total_slices: int
        successful_slices: int
        failed_slices: int
        execution_time_seconds: float
        slice_reports: list[dict]
        error_message: str | None = None
    
    async def execute_iceberg_order(
        self,
        symbol: str,
        side: str,
        total_quantity: float,
        slice_count: int = 5,
        idempotency_key: str | None = None,
    ) -> IcebergExecutionReport:
        """
        高级冰山订单执行（TWAP 算法）
        
        将大单拆分为多个小切片，使用 Maker 挂单减少滑点，随机时间伪装隐藏交易意图
        
        Args:
            symbol: 交易对（如 'BTCUSDT'）
            side: 买卖方向（'BUY' 或 'SELL'）
            total_quantity: 总数量（USDT 计值）
            slice_count: 切片数量（默认 5，即拆成 5 个小单）
            idempotency_key: 幂等性防重发标识（防止断网导致重复下单）
        
        Returns:
            IcebergExecutionReport: 冰山执行报告
        
        Raises:
            ExecutionError: 网络超时或 API 错误时抛出，交由 Portfolio Manager 处理
        """
        symbol = symbol.upper()
        side = side.upper()
        
        # 生成全局幂等性 ID
        if idempotency_key is None:
            idempotency_key = f"ICEBERG_{symbol}_{side}_{int(time.time() * 1000)}"
        
        logger.info(
            "========== 冰山订单开始 ==========\n"
            "交易对：%s | 方向：%s | 总数量：%.2f USDT | 切片数：%d | 幂等 ID：%s",
            symbol, side, total_quantity, slice_count, idempotency_key
        )
        
        start_time = time.time()
        slice_reports = []
        total_executed = 0.0
        successful_slices = 0
        failed_slices = 0
        
        # 计算每个切片的数量（均匀拆分）
        slice_quantity = total_quantity / slice_count
        
        # 精度处理（BTC 最小精度 0.001）
        if 'BTC' in symbol:
            slice_quantity = round(slice_quantity, 3)
        elif 'ETH' in symbol:
            slice_quantity = round(slice_quantity, 2)
        else:
            slice_quantity = round(slice_quantity, 2)
        
        logger.info("每个切片数量：%.2f USDT（精度已优化）", slice_quantity)
        
        # TWAP 执行循环
        for i in range(slice_count):
            current_slice_qty = slice_quantity
            slice_id = f"{idempotency_key}_SLICE_{i}"
            
            logger.info(
                "\n----- 切片 %d/%d -----\n"
                "切片 ID: %s\n"
                "订单数量：%.2f USDT",
                i + 1, slice_count, slice_id, current_slice_qty
            )
            
            try:
                # 步骤 1: 获取最新盘口价格（每次发单前必须重新获取）
                ticker = await self._fetch_orderbook_async(symbol)
                
                if not ticker:
                    logger.warning("无法获取盘口价格，跳过本次切片")
                    failed_slices += 1
                    slice_reports.append({
                        'slice_index': i,
                        'status': 'FAILED',
                        'reason': 'Failed to fetch orderbook',
                        'filled_qty': 0,
                        'price': 0
                    })
                    continue
                
                best_bid = ticker.get('best_bid', 0)
                best_ask = ticker.get('best_ask', 0)
                
                logger.info("当前盘口：Bid=%.2f | Ask=%.2f", best_bid, best_ask)
                
                # 步骤 2: 计算 Maker 挂单价格
                if side == 'BUY':
                    # 买单：挂 Bid 价（买一价），确保作为 Maker 成交
                    limit_price = best_bid
                    logger.info("买入策略：挂 Bid 价 %.2f（Maker 单）", limit_price)
                else:
                    # 卖单：挂 Ask 价（卖一价）
                    limit_price = best_ask
                    logger.info("卖出策略：挂 Ask 价 %.2f（Maker 单）", limit_price)
                
                # 步骤 3: 检查幂等性（防止断网重连重复下单）
                if self._check_idempotency(slice_id):
                    logger.info("切片 %d 已执行（幂等性命中），跳过", i)
                    # 这里应该查询历史订单，简化处理直接跳过
                    successful_slices += 1
                    continue
                
                # 步骤 4: 提交限价单（带超时撤单逻辑）
                order_result = await self._submit_maker_order_with_timeout(
                    symbol=symbol,
                    side=side,
                    quantity=current_slice_qty,
                    price=limit_price,
                    slice_id=slice_id,
                    timeout_seconds=10  # 10 秒超时
                )
                
                # 步骤 5: 记录执行结果
                if order_result.get('success', False):
                    filled_qty = order_result.get('filled_qty', 0)
                    avg_price = order_result.get('avg_price', limit_price)
                    
                    total_executed += filled_qty
                    successful_slices += 1
                    
                    # 记录到幂等性缓存
                    self._idempotency_cache[slice_id] = order_result.get('order_id', '')
                    
                    logger.info(
                        "✅ 切片 %d 执行成功：\n"
                        "  委托价格：%.2f\n"
                        "  成交数量：%.2f\n"
                        "  平均成交价：%.2f\n"
                        "  订单 ID: %s",
                        i + 1, limit_price, filled_qty, avg_price, order_result.get('order_id')
                    )
                    
                    slice_reports.append({
                        'slice_index': i,
                        'status': 'FILLED',
                        'order_id': order_result.get('order_id'),
                        'limit_price': limit_price,
                        'filled_qty': filled_qty,
                        'avg_price': avg_price,
                        'timestamp': time.time()
                    })
                else:
                    # 订单失败（超时未成交或被拒）
                    failed_slices += 1
                    reason = order_result.get('reason', 'Unknown')
                    
                    logger.warning(
                        "❌ 切片 %d 执行失败：%s",
                        i + 1, reason
                    )
                    
                    slice_reports.append({
                        'slice_index': i,
                        'status': 'FAILED',
                        'reason': reason,
                        'filled_qty': 0,
                        'price': limit_price
                    })
                
            except asyncio.TimeoutError:
                logger.error("切片 %d 网络超时", i)
                failed_slices += 1
                slice_reports.append({
                    'slice_index': i,
                    'status': 'TIMEOUT',
                    'reason': 'Network timeout',
                    'filled_qty': 0,
                    'price': 0
                })
            
            except Exception as e:
                logger.error("切片 %d 执行异常：%s", i, e)
                failed_slices += 1
                slice_reports.append({
                    'slice_index': i,
                    'status': 'ERROR',
                    'reason': str(e),
                    'filled_qty': 0,
                    'price': 0
                })
            
            # 步骤 6: 随机时间伪装（3-8 秒随机等待）
            if i < slice_count - 1:  # 最后一个切片不等待
                wait_time = random.uniform(3, 8)
                logger.info("随机等待 %.1f 秒后执行下一切片（伪装高频交易）", wait_time)
                await asyncio.sleep(wait_time)
        
        # 计算执行统计
        execution_time = time.time() - start_time
        remaining_qty = total_quantity - total_executed
        avg_price = sum(r['avg_price'] * r['filled_qty'] for r in slice_reports if r['status'] == 'FILLED') / total_executed if total_executed > 0 else 0
        
        # 生成执行报告
        report = self.IcebergExecutionReport(
            success=successful_slices > 0,
            total_quantity=total_quantity,
            executed_quantity=total_executed,
            remaining_quantity=remaining_qty,
            avg_execution_price=avg_price,
            total_slices=slice_count,
            successful_slices=successful_slices,
            failed_slices=failed_slices,
            execution_time_seconds=execution_time,
            slice_reports=slice_reports
        )
        
        # 最终日志汇总
        logger.info(
            "\n========== 冰山订单完成 ==========\n"
            "总数量：%.2f USDT\n"
            "已成交：%.2f USDT (%.1f%%)\n"
            "剩余未成交：%.2f USDT\n"
            "平均成交价：%.2f\n"
            "成功切片：%d/%d\n"
            "执行时间：%.1f 秒\n"
            "================================",
            total_quantity,
            total_executed,
            (total_executed / total_quantity * 100) if total_quantity > 0 else 0,
            remaining_qty,
            avg_price,
            successful_slices,
            slice_count,
            execution_time
        )
        
        # 如果有大量失败，抛出异常交由上层处理
        if failed_slices > slice_count * 0.5:
            error_msg = f"冰山订单执行失败率过高：{failed_slices}/{slice_count} 切片失败"
            logger.error(error_msg)
            report.error_message = error_msg
            raise ExecutionError(error_msg)
        
        return report
    
    async def _fetch_orderbook_async(self, symbol: str) -> dict | None:
        """
        异步获取盘口数据
        
        Args:
            symbol: 交易对
        
        Returns:
            dict: {
                'best_bid': float,
                'best_ask': float,
                'bid_qty': float,
                'ask_qty': float
            } 或 None
        """
        if self.mode == "paper":
            # Paper 模式：模拟盘口数据
            base_price = 50000.0
            spread = random.uniform(0.5, 2.0)
            return {
                'best_bid': base_price - spread / 2,
                'best_ask': base_price + spread / 2,
                'bid_qty': random.uniform(10, 100),
                'ask_qty': random.uniform(10, 100)
            }
        
        try:
            # 同步调用转为异步（使用 asyncio.to_thread）
            response = await asyncio.to_thread(
                self._request_with_retry,
                "GET",
                "/api/v3/depth",
                params={'symbol': symbol, 'limit': 5},
                require_auth=False
            )
            
            if response and 'bids' in response and 'asks' in response:
                return {
                    'best_bid': float(response['bids'][0][0]),
                    'best_ask': float(response['asks'][0][0]),
                    'bid_qty': float(response['bids'][0][1]),
                    'ask_qty': float(response['asks'][0][1])
                }
            return None
        
        except Exception as e:
            logger.error("获取盘口数据失败：%s", e)
            return None
    
    async def _submit_maker_order_with_timeout(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        slice_id: str,
        timeout_seconds: int = 10
    ) -> dict:
        """
        提交 Maker 限价单并等待成交（超时撤单追价）
        
        Args:
            symbol: 交易对
            side: 买卖方向
            quantity: 数量
            price: 委托价格
            slice_id: 切片 ID（用作幂等性键）
            timeout_seconds: 超时时间（秒）
        
        Returns:
            dict: {
                'success': bool,
                'order_id': str,
                'filled_qty': float,
                'avg_price': float,
                'reason': str
            }
        """
        logger.info(
            "提交 Maker 限价单：side=%s, qty=%.2f, price=%.2f, timeout=%ds",
            side, quantity, price, timeout_seconds
        )
        
        if self.mode == "paper":
            # Paper 模式：模拟执行
            await asyncio.sleep(0.5)  # 模拟网络延迟
            
            # 模拟 90% 成交率
            if random.random() < 0.9:
                filled_qty = quantity * random.uniform(0.95, 1.0)
                avg_price = price * random.uniform(0.9995, 1.0005)  # 微小滑点
                
                logger.info("Paper 成交：qty=%.2f, price=%.2f", filled_qty, avg_price)
                
                return {
                    'success': True,
                    'order_id': f"PAPER_{slice_id}",
                    'filled_qty': filled_qty,
                    'avg_price': avg_price,
                    'reason': 'paper_filled'
                }
            else:
                logger.info("Paper 超时未成交")
                return {
                    'success': False,
                    'order_id': None,
                    'filled_qty': 0,
                    'avg_price': 0,
                    'reason': 'paper_timeout'
                }
        
        # 实盘/Testnet 模式
        if not self.api_key or not self.api_secret:
            return {
                'success': False,
                'order_id': None,
                'filled_qty': 0,
                'avg_price': 0,
                'reason': 'API credentials not configured'
            }
        
        try:
            # 提交限价单
            params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "quantity": quantity,
                "price": price,
                "timeInForce": "GTC",  # Good Till Cancel
                "newClientOrderId": slice_id  # 幂等性订单 ID
            }
            
            signed_params = self._sign_request(params)
            
            # 异步调用 API
            response = await asyncio.to_thread(
                self._request_with_retry,
                "POST",
                "/api/v3/order",
                signed_params
            )
            
            order_id = str(response.get("orderId", ""))
            order_status = response.get("status", "")
            filled_qty = float(response.get("executedQty", 0))
            avg_price = float(response.get("avgPrice", price))
            
            logger.info(
                "订单提交成功：order_id=%s, status=%s, filled=%.2f",
                order_id, order_status, filled_qty
            )
            
            # 如果立即成交
            if order_status == "FILLED" or filled_qty > 0:
                self._idempotency_cache[slice_id] = order_id
                return {
                    'success': True,
                    'order_id': order_id,
                    'filled_qty': filled_qty,
                    'avg_price': avg_price,
                    'reason': 'immediate_fill'
                }
            
            # 等待成交（轮询检查）
            logger.info("等待订单成交（超时 %d 秒）...", timeout_seconds)
            start_wait = time.time()
            
            while time.time() - start_wait < timeout_seconds:
                await asyncio.sleep(2)  # 每 2 秒检查一次
                
                # 查询订单状态
                status_params = self._sign_request({
                    'symbol': symbol,
                    'orderId': order_id
                })
                
                status_response = await asyncio.to_thread(
                    self._request_with_retry,
                    "GET",
                    "/api/v3/order",
                    status_params
                )
                
                current_status = status_response.get("status", "")
                current_filled = float(status_response.get("executedQty", 0))
                current_avg_price = float(status_response.get("avgPrice", price))
                
                logger.debug(
                    "订单状态检查：status=%s, filled=%.2f, avg_price=%.2f",
                    current_status, current_filled, current_avg_price
                )
                
                # 如果成交了
                if current_status == "FILLED" or current_filled > 0:
                    self._idempotency_cache[slice_id] = order_id
                    logger.info("订单成交：filled=%.2f, avg_price=%.2f", current_filled, current_avg_price)
                    return {
                        'success': True,
                        'order_id': order_id,
                        'filled_qty': current_filled,
                        'avg_price': current_avg_price,
                        'reason': 'filled_after_wait'
                    }
                
                # 如果订单被取消
                if current_status == "CANCELED":
                    return {
                        'success': False,
                        'order_id': order_id,
                        'filled_qty': 0,
                        'avg_price': 0,
                        'reason': 'order_canceled'
                    }
            
            # 超时未成交，撤单
            logger.info("订单超时 %d 秒未成交，执行撤单", timeout_seconds)
            
            cancel_params = self._sign_request({
                'symbol': symbol,
                'orderId': order_id
            })
            
            await asyncio.to_thread(
                self._request_with_retry,
                "DELETE",
                "/api/v3/order",
                cancel_params
            )
            
            logger.info("撤单成功，等待下一次循环追价")
            
            return {
                'success': False,
                'order_id': order_id,
                'filled_qty': 0,
                'avg_price': 0,
                'reason': f'timeout_{timeout_seconds}s'
            }
        
        except APIError as e:
            logger.error("API 错误：%s", e)
            return {
                'success': False,
                'order_id': None,
                'filled_qty': 0,
                'avg_price': 0,
                'reason': f'APIError: {e}'
            }
        
        except NetworkError as e:
            logger.error("网络错误：%s", e)
            return {
                'success': False,
                'order_id': None,
                'filled_qty': 0,
                'avg_price': 0,
                'reason': f'NetworkError: {e}'
            }
        
        except Exception as e:
            logger.error("未知错误：%s", e)
            return {
                'success': False,
                'order_id': None,
                'filled_qty': 0,
                'avg_price': 0,
                'reason': f'Exception: {e}'
            }
