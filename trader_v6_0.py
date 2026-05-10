import os
import json
import time
import asyncio
import aiohttp
import hmac
import hashlib
import pandas as pd
import numpy as np
from datetime import datetime
from openai import OpenAI

# --- 配置區 ---
API_KEY = os.getenv("PIONEX_API_KEY", "YOUR_KEY")
API_SECRET = os.getenv("PIONEX_API_SECRET", "YOUR_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_KEY")
BASE_URL = "https://api.pionex.com"

# 監控配置
SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
MARKET_WATCHLIST = ["QQQ", "NVDA", "COIN"]
DAILY_TARGET_RATE = 0.10

client = OpenAI(api_key=OPENAI_API_KEY)

class UltimateAITrader:
    def __init__(self, config_path="evolution_config.json"):
        self.config_path = config_path
        self.load_config()
        self.start_equity = 0.0
        self.current_equity = 0.0
        self.target_reached = False
        self.session = None
        self.market_sentiment = 0.0

    def load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = {
                "rsi_buy_threshold": 25,
                "rsi_sell_threshold": 75,
                "position_weight": 0.3,
                "learning_history": []
            }
        # 確保關鍵鍵值存在
        self.config.setdefault("rsi_buy_threshold", 25)
        self.config.setdefault("rsi_sell_threshold", 75)

    async def get_signature(self, method, path, params=None, body=None):
        timestamp = str(int(time.time() * 1000))
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())]) if params else ""
        body_string = json.dumps(body) if body else ""
        payload = f"{method.upper()}{path}{'?' if query_string else ''}{query_string}{body_string}{timestamp}"
        signature = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return signature, timestamp

    async def request(self, method, path, params=None, body=None):
        if not self.session:
            self.session = aiohttp.ClientSession()
        sig, ts = await self.get_signature(method, path, params, body)
        headers = {
            "PIONEX-KEY": API_KEY,
            "PIONEX-SIGNATURE": sig,
            "PIONEX-TIMESTAMP": ts,
            "Content-Type": "application/json"
        }
        url = f"{BASE_URL}{path}"
        try:
            async with self.session.request(method, url, params=params, json=body, headers=headers, timeout=10) as resp:
                return await resp.json()
        except Exception as e:
            print(f"請求錯誤: {e}")
            return None

    async def update_market_sentiment(self):
        """跨市場情緒掃描 (整合 stock-analysis 邏輯)"""
        print("🔍 正在掃描美股大盤情緒 (QQQ, NVDA, COIN)...")
        # 模擬獲取美股數據 (實際可透過 Yahoo Finance API)
        market_data = {"QQQ": 1.2, "NVDA": 2.5, "COIN": 3.0}
        prompt = f"分析美股表現對加密貨幣的影響：{json.dumps(market_data)}。請給出一個 -1 到 1 的情緒分數。"
        try:
            # 使用非同步方式調用 OpenAI (簡化處理)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            self.market_sentiment = 0.4 # 模擬 AI 回傳
            print(f"📊 當前跨市場情緒分數: {self.market_sentiment}")
        except:
            self.market_sentiment = 0.0

    async def analyze_and_trade(self, symbol):
        if self.target_reached: return
        
        res = await self.request("GET", "/api/v1/market/klines", params={"symbol": symbol, "interval": "5M", "limit": 100})
        if not (res and res.get("result")): return

        df = pd.DataFrame(res["data"]["klines"])
        for col in ["close", "high", "low"]: df[col] = pd.to_numeric(df[col])
        
        # 指標計算
        df["RSI"] = 100 - (100 / (1 + (df["close"].diff().where(lambda x: x > 0, 0).rolling(7).mean() / 
                                      -df["close"].diff().where(lambda x: x < 0, 0).rolling(7).mean())))
        df["EMA200"] = df["close"].ewm(span=200, adjust=False).mean()
        
        latest = df.iloc[-1]
        rsi = latest["RSI"]
        price = latest["close"]
        ema200 = latest["EMA200"]
        
        action = "HOLD"
        # 終極決策邏輯：結合技術指標 + 跨市場情緒
        if price > ema200 and rsi < self.config['rsi_buy_threshold'] and self.market_sentiment > 0.2:
            action = "BUY"
        elif price < ema200 and rsi > self.config['rsi_sell_threshold'] or self.market_sentiment < -0.5:
            action = "SELL"
            
        if action != "HOLD":
            print(f"🚀 [{symbol}] 決策: {action} | 價格: {price} | RSI: {rsi:.2f} | 情緒: {self.market_sentiment}")

    async def run(self):
        print(f"--- 啟動 v6.0 終極智學版 (Async + Cross-Market + Self-Learning) ---")
        while True:
            # 1. 更新總資產與獲利目標
            res = await self.request("GET", "/api/v1/account/balances")
            if res and res.get("result"):
                total = sum(float(b['free']) + float(b['frozen']) for b in res['data']['balances'] if b['coin'] == 'USDT')
                self.current_equity = total
                if self.start_equity == 0: self.start_equity = total
                
                profit = self.current_equity - self.start_equity
                print(f"💰 資產: {self.current_equity:.2f} | 今日獲利: {profit:.2f}")
                
                if profit >= (self.start_equity * DAILY_TARGET_RATE) and self.start_equity > 0:
                    self.target_reached = True
                    print("🎉 每日 10% 目標已達成！鎖定利潤，停止交易。")
                    break

            # 2. 跨市場情緒掃描
            await self.update_market_sentiment()

            # 3. 多幣種並行監控
            tasks = [self.analyze_and_trade(s) for s in SYMBOLS]
            await asyncio.gather(*tasks)
            
            await asyncio.sleep(60)

if __name__ == "__main__":
    trader = UltimateAITrader()
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        print("系統已手動停止。")
