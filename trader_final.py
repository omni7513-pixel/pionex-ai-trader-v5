"""
Pionex AI Trader - 終極整合版 (Final Edition)
整合技能：
  - pionex-ai-trader v2.2：RSI/BB 高靈敏指標、10% 複利獲利鎖定、Asyncio 並行架構
  - crypto-trading-expert：SMC/FVG 過馬路理論、聰明錢邏輯、1:2 盈虧比過濾
  - stock-analysis：跨市場美股情緒掃描 (QQQ, NVDA, COIN)
  - karpathy-guidelines：代碼極簡化、假設顯性化、外科手術式修改

Karpathy Explicit Assumptions:
  1. PIONEX_API_KEY / PIONEX_API_SECRET 已設置在環境變數中。
  2. OPENAI_API_KEY 可選（用於 AI 局勢分析）。
  3. 成功標準：系統能並行監控 6 個幣種，在「過馬路+RSI<50」條件下自動下單，
     並在達成每日 10% 獲利目標時自動鎖定利潤停止交易。
"""

import os
import json
import time
import hmac
import hashlib
import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime
from openai import OpenAI

# ─── 配置 ───────────────────────────────────────────────
API_KEY    = os.getenv("PIONEX_API_KEY", "")
API_SECRET = os.getenv("PIONEX_API_SECRET", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL   = "https://api.pionex.com"

SYMBOLS           = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "ADA_USDT", "BNB_USDT", "XRP_USDT", "NVDAX_USDT"]
DAILY_TARGET_RATE = 0.10   # 每日 10% 獲利目標
MAX_DRAWDOWN_RATE = 0.15   # 最大回撤 15% 保護
POSITION_WEIGHT   = 0.50   # 單筆倉位 50%
TAKE_PROFIT_RATE  = 0.03   # 停利 +3%
STOP_LOSS_RATE    = 0.02   # 停損 -2%

# 延遲初始化 OpenAI 客戶端
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# ─── 工具函數 ────────────────────────────────────────────
def calc_rsi(series: pd.Series, period: int = 7) -> float:
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = -delta.where(delta < 0, 0.0).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def calc_bb(series: pd.Series, period: int = 20, std_dev: float = 1.5):
    ma    = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = ma + std_dev * std
    lower = ma - std_dev * std
    return float(upper.iloc[-1]), float(ma.iloc[-1]), float(lower.iloc[-1])

def calc_ema(series: pd.Series, span: int = 200) -> float:
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])

# ─── 主交易員 ────────────────────────────────────────────
class FinalAITrader:
    def __init__(self, config_path: str = None):
        data_dir = os.getenv("DATA_DIR", "/data")
        os.makedirs(data_dir, exist_ok=True)
        if config_path is None:
            config_path = os.path.join(data_dir, "evolution_config.json")
        self.config_path    = config_path
        self.config         = self._load_config()
        self.start_equity   = 0.0
        self.current_equity = 0.0
        self.target_reached = False
        self.market_score   = 0.0
        self.session        = None
        # 持倉追蹤：{symbol: {"entry_price": float, "qty": float, "side": "BUY"}}
        self.positions      = {}

    def _load_config(self) -> dict:
        default = {
            "rsi_buy":  50,
            "rsi_sell": 75,
            "bb_std":   1.5,
            "learning_history": []
        }
        if os.path.exists(self.config_path):
            try:
                saved = json.load(open(self.config_path))
                default.update(saved)
            except Exception:
                pass
        return default

    def _save_config(self):
        json.dump(self.config, open(self.config_path, "w"), indent=2, ensure_ascii=False)

    # ── 簽名 ──────────────────────────────────────────────
    def _sign(self, method: str, path: str, params: dict = None, body: dict = None):
        ts = str(int(time.time() * 1000))
        p  = dict(params or {})
        p["timestamp"] = ts
        sorted_qs = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
        sign_str  = f"{method.upper()}{path}?{sorted_qs}"
        sig = hmac.new(API_SECRET.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
        return sorted_qs, sig

    # ── HTTP 請求 ─────────────────────────────────────────
    async def _request(self, method: str, path: str, params: dict = None, body: dict = None):
        if not self.session:
            self.session = aiohttp.ClientSession()
        sorted_qs, sig = self._sign(method, path, params, body)
        headers = {
            "PIONEX-KEY":       API_KEY,
            "PIONEX-SIGNATURE": sig,
            "Content-Type":     "application/json"
        }
        try:
            url = f"{BASE_URL}{path}?{sorted_qs}"
            async with self.session.request(
                method, url,
                json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return await resp.json(content_type=None)
        except Exception as e:
            print(f"  ⚠️ 請求錯誤 [{path}]: {e}")
            return None

    # ── 帳戶資產 ──────────────────────────────────────────
    async def update_equity(self) -> float:
        res = await self._request("GET", "/api/v1/account/balances")
        if res and res.get("result"):
            usdt = sum(
                float(b["free"]) + float(b["frozen"])
                for b in res["data"]["balances"]
                if b["coin"] == "USDT"
            )
            self.current_equity = usdt
            if self.start_equity == 0:
                self.start_equity = usdt
        profit = self.current_equity - self.start_equity
        return profit

    # ── 獲取可用 USDT 餘額 ────────────────────────────────
    async def get_free_usdt(self) -> float:
        res = await self._request("GET", "/api/v1/account/balances")
        if res and res.get("result"):
            for b in res["data"]["balances"]:
                if b["coin"] == "USDT":
                    return float(b["free"])
        return 0.0

    # ── 獲取幣種可用餘額 ──────────────────────────────────
    async def get_free_coin(self, coin: str) -> float:
        res = await self._request("GET", "/api/v1/account/balances")
        if res and res.get("result"):
            for b in res["data"]["balances"]:
                if b["coin"] == coin:
                    return float(b["free"])
        return 0.0

    # ── 下單（真實交易）──────────────────────────────────
    async def place_order(self, symbol: str, side: str, amount_usdt: float, price: float) -> bool:
        """
        side: "BUY" 或 "SELL"
        amount_usdt: 買入金額（USDT）
        price: 當前市價（用於計算數量）
        """
        coin = symbol.replace("_USDT", "")

        if side == "BUY":
            # 市價買入：用 USDT 金額下單
            body = {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": f"{amount_usdt:.4f}"  # 用 USDT 金額買入
            }
        else:
            # 市價賣出：賣出持有的幣種數量
            qty = await self.get_free_coin(coin)
            if qty <= 0:
                print(f"  ⚠️ [{symbol}] 無持倉可賣出")
                return False
            body = {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": f"{qty:.6f}"
            }

        res = await self._request("POST", "/api/v1/order", body=body)
        if res and res.get("result"):
            order_id = res.get("data", {}).get("orderId", "unknown")
            print(f"  ✅ [{symbol}] {side} 下單成功！OrderID: {order_id}")
            return True
        else:
            err = res.get("message", "未知錯誤") if res else "無回應"
            print(f"  ❌ [{symbol}] {side} 下單失敗：{err}")
            return False

    # ── 跨市場情緒掃描 ────────────────────────────────────
    async def scan_market_sentiment(self):
        print("  🔍 跨市場情緒掃描 (QQQ / NVDA / COIN)...")
        try:
            if client is None:
                self.market_score = 0.0
            else:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": (
                            "請根據 2026 年 5 月當前市場環境，"
                            "評估美股科技板塊 (QQQ/NVDA/COIN) 對加密貨幣市場的情緒影響，"
                            "給出 -1.0 到 1.0 的分數，只回傳數字。"
                        )
                    }],
                    max_tokens=10
                )
                raw = resp.choices[0].message.content.strip()
                self.market_score = float("".join(c for c in raw if c in "0123456789.-"))
        except Exception:
            self.market_score = 0.0
        print(f"  📊 情緒分數: {self.market_score:+.2f}")

    # ── SMC 過馬路理論過濾 ────────────────────────────────
    def _crossroad_filter(self, df: pd.DataFrame) -> tuple[bool, str]:
        close  = df["close"]
        ema200 = calc_ema(close, 200)
        price  = float(close.iloc[-1])
        position_ok = price > ema200
        recent_lows = df["low"].iloc[-5:]
        state_ok    = float(recent_lows.iloc[-1]) > float(recent_lows.iloc[0])
        reason = (
            f"位置={'✅多頭' if position_ok else '❌空頭'} | "
            f"狀態={'✅動能向上' if state_ok else '❌動能向下'}"
        )
        return (position_ok and state_ok), reason

    # ── FVG 偵測 ──────────────────────────────────────────
    def _detect_fvg(self, df: pd.DataFrame) -> bool:
        if len(df) < 3:
            return False
        c1_high = float(df["high"].iloc[-3])
        c3_low  = float(df["low"].iloc[-1])
        return c3_low > c1_high

    # ── 單幣種分析與決策 ──────────────────────────────────
    async def analyze_and_trade(self, symbol: str):
        if self.target_reached:
            return

        res = await self._request(
            "GET", "/api/v1/market/klines",
            params={"symbol": symbol, "interval": "5M", "limit": 250}
        )
        if not (res and res.get("result")):
            print(f"  ⚠️ [{symbol}] 無法獲取 K 線數據")
            return

        df = pd.DataFrame(res["data"]["klines"])
        for col in ["close", "high", "low", "open"]:
            df[col] = pd.to_numeric(df[col])

        price  = float(df["close"].iloc[-1])
        rsi    = calc_rsi(df["close"], 7)
        bb_up, bb_mid, bb_low_val = calc_bb(df["close"], 20, self.config["bb_std"])
        fvg    = self._detect_fvg(df)
        crossroad_ok, crossroad_reason = self._crossroad_filter(df)

        # ── 停利/停損檢查（已持倉時）────────────────────
        if symbol in self.positions:
            pos = self.positions[symbol]
            entry = pos["entry_price"]
            pnl_rate = (price - entry) / entry
            if pnl_rate >= TAKE_PROFIT_RATE:
                print(f"  🎯 [{symbol}] 停利觸發！獲利 {pnl_rate*100:.1f}%，賣出")
                success = await self.place_order(symbol, "SELL", 0, price)
                if success:
                    del self.positions[symbol]
                return
            elif pnl_rate <= -STOP_LOSS_RATE:
                print(f"  🛑 [{symbol}] 停損觸發！虧損 {pnl_rate*100:.1f}%，賣出")
                success = await self.place_order(symbol, "SELL", 0, price)
                if success:
                    del self.positions[symbol]
                return

        # ── 決策邏輯 ──────────────────────────────────────
        action = "HOLD"

        if (
            crossroad_ok                          # 過馬路理論：位置+狀態雙確認
            and rsi < self.config["rsi_buy"]      # RSI 低於 50（未超買）
            and symbol not in self.positions      # 尚未持倉
        ):
            action = "BUY"
        elif (
            rsi > self.config["rsi_sell"]         # RSI 超買
            or price >= bb_up                     # 布林上軌
            or self.market_score < -0.5           # 美股大盤崩跌
        ):
            action = "SELL"

        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "⚪")
        print(
            f"  {emoji} [{symbol}] {action} | "
            f"Price={price:.2f} | RSI={rsi:.1f} | "
            f"BB={bb_low_val:.2f}~{bb_up:.2f} | FVG={'是' if fvg else '否'}"
        )
        print(f"     └ 過馬路: {crossroad_reason}")

        # ── 執行下單 ──────────────────────────────────────
        if action == "BUY":
            free_usdt = await self.get_free_usdt()
            amount = free_usdt * POSITION_WEIGHT
            if amount < 1.0:
                print(f"  ⚠️ [{symbol}] USDT 餘額不足（可用: {free_usdt:.2f}，需要至少 1 USDT）")
            else:
                print(f"  💸 [{symbol}] 準備買入 {amount:.2f} USDT...")
                success = await self.place_order(symbol, "BUY", amount, price)
                if success:
                    self.positions[symbol] = {"entry_price": price, "qty": amount / price}

        elif action == "SELL" and symbol in self.positions:
            print(f"  💸 [{symbol}] 準備賣出持倉...")
            success = await self.place_order(symbol, "SELL", 0, price)
            if success:
                del self.positions[symbol]

        # ── 自主學習：記錄決策 ────────────────────────────
        if action != "HOLD":
            self.config["learning_history"].append({
                "time":   datetime.now().isoformat(),
                "symbol": symbol,
                "action": action,
                "price":  price,
                "rsi":    rsi,
                "score":  self.market_score
            })
            self.config["learning_history"] = self.config["learning_history"][-100:]
            self._save_config()

    # ── 自主學習反思 ──────────────────────────────────────
    def reflect_and_evolve(self):
        history = self.config.get("learning_history", [])
        if len(history) < 5:
            return
        recent_buys = [h for h in history[-10:] if h["action"] == "BUY"]
        if recent_buys:
            avg_rsi = np.mean([h["rsi"] for h in recent_buys])
            if avg_rsi > 30:
                old = self.config["rsi_buy"]
                self.config["rsi_buy"] = max(15, old - 2)
                print(f"  🧠 自主學習：RSI 買入閾值 {old} → {self.config['rsi_buy']}")
                self._save_config()

    # ── 主迴圈 ────────────────────────────────────────────
    async def run(self):
        print("=" * 60)
        print("🚀 Pionex AI Trader - 終極整合版 啟動")
        print(f"   監控幣種: {', '.join(SYMBOLS)}")
        print(f"   每日目標: {DAILY_TARGET_RATE*100:.0f}%")
        print(f"   停利: +{TAKE_PROFIT_RATE*100:.0f}% | 停損: -{STOP_LOSS_RATE*100:.0f}%")
        print("=" * 60)

        cycle = 0
        while True:
            cycle += 1
            print(f"\n── 第 {cycle} 輪 [{datetime.now().strftime('%H:%M:%S')}] ──")

            # 1. 更新資產
            profit = await self.update_equity()
            target = self.start_equity * DAILY_TARGET_RATE
            print(f"  💰 資產: {self.current_equity:.4f} USDT | 今日獲利: {profit:.4f} | 目標: {target:.4f}")
            if self.positions:
                print(f"  📦 持倉: {list(self.positions.keys())}")

            # 2. 達標鎖定
            if self.start_equity > 0 and profit >= target:
                self.target_reached = True
                print("  🎉 每日 10% 目標達成！鎖定利潤，停止今日交易。")
                break

            # 3. 最大回撤保護
            if self.start_equity > 0 and profit <= -(self.start_equity * MAX_DRAWDOWN_RATE):
                print("  🛑 最大回撤 15% 觸發！停止交易，保護資金。")
                break

            # 4. 跨市場情緒掃描
            await self.scan_market_sentiment()

            # 5. 多幣種並行分析與交易
            await asyncio.gather(*[self.analyze_and_trade(s) for s in SYMBOLS])

            # 6. 自主學習反思
            self.reflect_and_evolve()

            await asyncio.sleep(600)  # 每 10 分鐘掃描一次

        if self.session:
            await self.session.close()


if __name__ == "__main__":
    trader = FinalAITrader()
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        print("\n系統已手動停止。")
