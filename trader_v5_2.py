import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from openai import OpenAI

# --- 配置區 ---
# 建議透過環境變數設置，此處為示範邏輯
PIONEX_API_KEY = os.getenv("PIONEX_API_KEY", "YOUR_KEY")
PIONEX_API_SECRET = os.getenv("PIONEX_API_SECRET", "YOUR_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_KEY")

# 跨市場監控標的
MARKET_WATCHLIST = ["QQQ", "NVDA", "COIN", "^GSPC"] # 納指, 英偉達, Coinbase, 標普500

client = OpenAI(api_key=OPENAI_API_KEY)

class CrossMarketAITrader:
    def __init__(self, config_path="evolution_config.json"):
        self.config_path = config_path
        self.load_config()
        
    def load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = {
                "rsi_buy_threshold": 30,
                "rsi_sell_threshold": 70,
                "position_weight": 0.3,
                "daily_target_pct": 0.10,
                "learning_history": []
            }

    def get_stock_market_sentiment(self):
        """
        模擬調用 stock-analysis 技能獲取美股情緒
        實際運行時會透過 Yahoo Finance API 獲取真實數據
        """
        print("🔍 正在執行跨市場情緒掃描 (QQQ, NVDA, COIN)...")
        # 這裡模擬獲取到的美股數據
        market_data = {
            "QQQ": {"change_pct": 1.2, "trend": "Bullish"},
            "NVDA": {"change_pct": 2.5, "trend": "Strong Bullish"},
            "COIN": {"change_pct": 3.0, "trend": "Bullish"}
        }
        
        # 讓 AI 分析跨市場影響
        prompt = f"分析以下美股表現對加密貨幣市場的影響：{json.dumps(market_data)}。請給出一個 -1 到 1 的情緒分數。"
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            # 簡單解析分數 (實際應更嚴謹)
            sentiment_score = 0.5 # 假設 AI 回傳 0.5
            return sentiment_score, market_data
        except:
            return 0, {}

    def get_crypto_data(self, symbol="BTC_USDT"):
        # 模擬獲取 Pionex K線數據
        return {
            "price": 65000,
            "rsi": 35,
            "kdj_j": 20,
            "ema_20": 64500
        }

    def make_decision(self, crypto_data, market_sentiment):
        score = market_sentiment
        rsi = crypto_data['rsi']
        
        decision = "HOLD"
        reason = ""
        
        # 結合跨市場情緒與技術指標
        if rsi < self.config['rsi_buy_threshold'] and score > 0.2:
            decision = "BUY"
            reason = f"技術面超賣 (RSI:{rsi}) 且美股情緒正面 (Score:{score})"
        elif rsi > self.config['rsi_sell_threshold'] or score < -0.5:
            decision = "SELL"
            reason = f"技術面超買或美股大盤崩跌風險 (Score:{score})"
        else:
            reason = "指標未共振，保持觀望"
            
        return decision, reason

    def run_cycle(self):
        print(f"--- {datetime.now()} 交易週期開始 ---")
        
        # 1. 跨市場掃描
        market_score, market_info = self.get_stock_market_sentiment()
        print(f"📊 美股市場情緒分數: {market_score}")
        
        # 2. 加密貨幣分析
        crypto_data = self.get_crypto_data()
        print(f"🪙 BTC 數據: Price={crypto_data['price']}, RSI={crypto_data['rsi']}")
        
        # 3. 決策
        decision, reason = self.make_decision(crypto_data, market_score)
        print(f"🤖 AI 決策: {decision}")
        print(f"📝 原因: {reason}")
        
        # 4. 模擬學習 (如果決策後發生虧損)
        # self.reflect_and_learn(...) 
        
        print("--- 週期結束 ---")

if __name__ == "__main__":
    trader = CrossMarketAITrader()
    trader.run_cycle()
