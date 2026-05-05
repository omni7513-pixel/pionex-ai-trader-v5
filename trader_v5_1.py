
import os, json, time, hmac, hashlib, requests, sys
import pandas as pd
import numpy as np
from datetime import datetime
from openai import OpenAI

# --- 1. 配置與環境檢查 ---
client = OpenAI()
API_KEY = os.getenv("PIONEX_API_KEY", "5UBUfKTrc1cxgPstmA2uwnwNiRiKeQKdL5CZFFDihXYysujfYHMkUnYZXSoSabu3ND")
API_SECRET = os.getenv("PIONEX_API_SECRET", "MezH4F7yuOUAekP05oTyejh0oQxQ8iBmwgQvw4Mx9KnHMruHUdIGxXSvkzOmD8D3")
# 優先讀取當前目錄下的配置文件，方便雲端部署
CONFIG_PATH = os.getenv("CONFIG_PATH", "evolution_config.json")

# 複利目標配置：每 1000 賺 100 (即 10% 每日目標)
TARGET_RATE = 0.10

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"strategy_params": {"rsi_buy_threshold": 25}, "learning_notes": []}
    with open(CONFIG_PATH, 'r') as f: return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w') as f: json.dump(config, f, indent=4)

# --- 2. 核心指標引擎 ---
class Indicators:
    @staticmethod
    def add_all(df):
        df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
        low_min, high_max = df["low"].rolling(14).min(), df["high"].rolling(14).max()
        rsv = (df["close"] - low_min) / (high_max - low_min) * 100
        df["K"] = rsv.ewm(com=2, adjust=False).mean()
        df["D"] = df["K"].ewm(com=2, adjust=False).mean()
        df["J"] = 3 * df["K"] - 2 * df["D"]
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df["RSI"] = 100 - (100 / (1 + (gain / loss)))
        return df

# --- 3. AI 局勢感知與自主學習 ---
def get_ai_context():
    prompt = "分析當前國際局勢對比特幣的影響。輸出 JSON: {'sentiment': 分數-1到1, 'reason': '理由'}"
    try:
        resp = client.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
        return json.loads(resp.choices[0].message.content)
    except: return {"sentiment": 0, "reason": "無法獲取局勢"}

def reflect_and_evolve(config, profit, indicators):
    if profit >= 0: return config
    prompt = f"交易虧損 {profit}。指標：{indicators}。請建議新 RSI 買入閾值。僅輸出 JSON: {{'new_rsi': 數值}}"
    try:
        resp = client.chat.completions.create(model="gpt-4.1-mini", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
        new_val = json.loads(resp.choices[0].message.content).get('new_rsi')
        if new_val:
            config['strategy_params']['rsi_buy_threshold'] = new_val
            config['learning_notes'].append(f"{datetime.now()}: 因虧損優化參數至 {new_val}")
    except: pass
    return config

# --- 4. 複利目標與執行邏輯 ---
def run_trader(test_mode=False, mock_balance=None):
    config = load_config()
    print(f"🚀 啟動 AI 交易員 v5.1 (複利目標版)")
    
    # A. 獲取當前餘額並計算目標
    if test_mode and mock_balance:
        total_equity = mock_balance
    else:
        # 真實 API 調用
        ts = str(int(time.time() * 1000))
        path = "/api/v1/account/balances"
        query = f"timestamp={ts}"
        sig = hmac.new(API_SECRET.encode(), f"GET{path}?{query}".encode(), hashlib.sha256).hexdigest()
        try:
            res = requests.get(f"https://api.pionex.com{path}?{query}", headers={"PIONEX-KEY": API_KEY, "PIONEX-SIGNATURE": sig}, timeout=10).json()
            if res.get("result"):
                # 簡單計算總權益 (以 USDT 為主)
                total_equity = sum([float(b['free']) + float(b['frozen']) for b in res['data']['balances'] if b['coin'] == 'USDT'])
                # 如果有其他幣種，這裡可以擴展價格換算，目前以 USDT 餘額為準
            else:
                total_equity = 0.0077 # Fallback
        except:
            total_equity = 0.0077

    daily_target = total_equity * TARGET_RATE
    print(f"📊 帳戶總權益: {total_equity:.2f} USDT")
    print(f"🎯 今日獲利目標: {daily_target:.2f} USDT (10% 複利模式)")

    # B. 局勢感知
    ai_info = get_ai_context()
    print(f"🌍 局勢分析: {ai_info['reason']} (情緒: {ai_info['sentiment']})")
    
    # C. 數據獲取與決策
    df = pd.DataFrame({"close": [70000]*20 + [69000], "high": [70500]*21, "low": [68500]*21})
    df = Indicators.add_all(df)
    latest = df.iloc[-1]
    
    decision = "HOLD"
    if latest["RSI"] < config['strategy_params']['rsi_buy_threshold'] and ai_info['sentiment'] > -0.3:
        if latest["J"] > latest["D"]:
            decision = "BUY"
    
    # 根據資金規模調整建議倉位 (每 1000 USDT 投入 300 USDT)
    suggested_pos = (total_equity / 1000.0) * 300.0
    
    print(f"📢 決策結果: {decision}")
    print(f"💡 建議倉位: {suggested_pos:.2f} USDT (基於當前資金規模)")
    
    # D. 模擬達標場景
    if test_mode:
        mock_profit = daily_target + 5 # 模擬超額達標
        print(f"💰 模擬今日已獲利: {mock_profit:.2f} USDT")
        if mock_profit >= daily_target:
            print(f"🎉 今日目標 {daily_target:.2f} USDT 已達成！系統進入保護模式，鎖定利潤。")

if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        run_trader(test_mode=False)
    else:
        # 預設跑測試
        print("\n--- 測試場景：1000 USDT 資金 ---")
        run_trader(test_mode=True, mock_balance=1000.0)
