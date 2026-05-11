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


# ═══════════════════════════════════════════════════════════════
# 📚 策略知識庫 - 來源：阿儒阿育的交易室 YouTube 教學（2025）
# ═══════════════════════════════════════════════════════════════
#
# 【影片一】手動型態交易策略（g838P1EZfQY）
#   核心：找「轉折低點」支撐區間進場，等待 W 型態確認
#   進場條件：
#     1. 價格必須「戳破」支撐區間後再彈回（能起死回生的區間）
#     2. 第三次反彈高度必須超過第二次（越彈越有力）
#   停利：放在前次反彈的「前高」
#   停損：放在進場前幾次反彈的「最低點」
#   適用：任何幣種、任何時間週期、任何市場方向
#
# 【影片二】Pionex 合約網格機器人（7j0uHMCr9a4）
#   類型：合約網格，選「做多」或「做空」，不選「中性」
#   等差 vs 等比：直接選等差即可
#   範圍設定：
#     下限 = 圖表上前一個明顯低點（保守可抓更前一個）
#     上限 = 目前圖表最高點
#   格數：調整到每格利潤率落在 0.1%~0.5% 之間
#   資金：每個機器人不超過總本金的 5%
#   槓桿：可用槓桿降低門檻，但強平價必須在止損價外面
#   止盈：設在網格上限（突破上限自動關閉）
#   止損：設在網格下限外一點點（例如下限 0.1 → 止損 0.099）
#   挑幣：做多選漲幅排行榜前段、階梯式穩定上漲的幣
#   注意：強平價絕對不能落在網格範圍內
#
# 【影片三】合約槓桿做多策略（awEB-UO13PA）
#   核心：找「W（雙底）型態」進場做多
#   槓桿：最多 20 倍以內，新手不超過此限制
#   保證金模式：必須用「逐倉」，絕對不用「全倉」
#   進場：W 型態完整出現後，用市價單進場
#   時間週期：1小時線找不到就換 5 分鐘線
#   停利：W 型態高度 × 1 倍往上（1:1 盈虧比）
#   停損：W 型態最底部下方一點點（避開市場雜訊）
#   幣種：永續合約區任何幣種，找到 W 就進場
#   口訣：「出現W = 價格會上漲」（如同喝大冰奶必拉肚子）
#   風控：虧了要檢討，找出止損太近/太晚進場/W判斷錯誤等原因
#
# 【策略整合應用原則】
#   1. W型態偵測：連續低點中第三個低點高於第二個低點 → 做多訊號加分
#   2. 支撐區間確認：價格戳破支撐後反彈 → 進場可信度提升
#   3. 合約網格：震盪行情中，每格利潤目標 0.1%~0.5%
#   4. 資金控管：單筆不超過總資金 5%（現貨可放寬至 50%）
#   5. 槓桿上限：20 倍，逐倉模式，強平價必須在止損外
# ═══════════════════════════════════════════════════════════════

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
FUTURES_SYMBOLS   = ["BTC_USDT_PERP", "ETH_USDT_PERP", "SOL_USDT_PERP"]  # 合約幣種
DAILY_TARGET_RATE = 0.10   # 每日 10% 獲利目標
MAX_DRAWDOWN_RATE = 0.15   # 最大回撤 15% 保護
POSITION_WEIGHT   = 0.60   # 現貨單筆倉位 60%（本金 6:4 分配，現貨佔 60%）
FUTURES_WEIGHT    = 0.40   # 合約資金佔比 40%（本金 6:4 分配，合約佔 40%）
MAX_LEVERAGE      = 20     # 最高槓桿倍數（影片三建議上限）
TAKE_PROFIT_RATE  = 0.03   # 停利 +3%
STOP_LOSS_RATE    = 0.02   # 停損 -2%

# 延遲初始化 OpenAI 客戶端
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# ─── 工具函數 ────────────────────────────────────────────
def calc_rsi(series: pd.Series, period: int = 7) -> float:
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = -delta.where(delta < 0, 0.0).rolling(period).mean()
    # 美股休市時 close 全部相同，gain=loss=0 → NaN；改為回傳 50（中性）
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if last_gain == 0 and last_loss == 0:
        return 50.0
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
        # 餘額快取：update_equity 時更新，get_free_usdt/get_free_coin 直接讀取
        self._balances: dict = {}

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
            # 同時更新餘額快取，供 get_free_usdt/get_free_coin 使用（避免重複請求）
            self._balances = {b["coin"]: b for b in res["data"]["balances"]}
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
        # 優先讀快取（update_equity 已取得），避免重複 API 請求
        if "USDT" in self._balances:
            return float(self._balances["USDT"].get("free", 0))
        res = await self._request("GET", "/api/v1/account/balances")
        if res and res.get("result"):
            for b in res["data"]["balances"]:
                if b["coin"] == "USDT":
                    return float(b["free"])
        return 0.0

    # ── 獲取幣種可用餘額 ──────────────────────────────────
    async def get_free_coin(self, coin: str) -> float:
        # 優先讀快取（update_equity 已取得），避免重複 API 請求
        if coin in self._balances:
            return float(self._balances[coin].get("free", 0))
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

        res = await self._request("POST", "/api/v1/trade/order", body=body)
        if res and res.get("result"):
            order_id = res.get("data", {}).get("orderId", "unknown")
            print(f"  ✅ [{symbol}] {side} 下單成功！OrderID: {order_id}")
            return True
        else:
            if res:
                err = res.get("message") or res.get("code") or str(res)
            else:
                err = "無回應（網路錯誤或超時）"
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


    # ── W 型態偵測（影片三策略）────────────────────────────
    def _detect_w_pattern(self, df: pd.DataFrame) -> tuple[bool, str]:
        """
        偵測 W（雙底）型態：
        - 找最近 20 根 K 線內的三個連續低點
        - 條件：第三個低點 > 第二個低點（越彈越有力）
        - 來源：阿儒阿育交易室 awEB-UO13PA
        """
        if len(df) < 20:
            return False, "資料不足"
        lows = df["low"].iloc[-20:].values
        # 找局部低點（比左右兩根都低）
        local_lows = []
        for i in range(1, len(lows) - 1):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                local_lows.append((i, lows[i]))
        if len(local_lows) < 3:
            return False, f"局部低點不足（找到{len(local_lows)}個）"
        # 取最近三個低點
        p1, p2, p3 = local_lows[-3], local_lows[-2], local_lows[-1]
        # W 條件：第三個低點高於第二個低點（越彈越有力）
        w_ok = p3[1] > p2[1]
        reason = f"低點序列: {p1[1]:.2f}→{p2[1]:.2f}→{p3[1]:.2f} {'✅W型態' if w_ok else '❌非W'}"
        return w_ok, reason

    # ── 支撐區間確認（影片一策略）──────────────────────────
    def _check_support_bounce(self, df: pd.DataFrame) -> bool:
        """
        確認價格是否「戳破支撐後反彈」：
        - 近5根K線中有低於前20根最低點的情況，但收盤回到上方
        - 來源：阿儒阿育交易室 g838P1EZfQY
        """
        if len(df) < 25:
            return False
        support = float(df["low"].iloc[-25:-5].min())
        recent = df.iloc[-5:]
        pierced = any(float(row["low"]) < support for _, row in recent.iterrows())
        recovered = float(df["close"].iloc[-1]) > support
        return pierced and recovered

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
        if np.isnan(rsi):
            print(f"  ⚠️ [{symbol}] RSI=nan，K線數據不足，跳過")
            return
        # 美股休市偵測：最後 5 筆 close 完全相同 → 市場凍結，跳過
        if df["close"].iloc[-5:].nunique() == 1:
            print(f"  ⏸️ [{symbol}] 美股休市（價格凍結），跳過本輪")
            return
        bb_up, bb_mid, bb_low_val = calc_bb(df["close"], 20, self.config["bb_std"])
        # NaN 防護：數據不足時 BB 可能為 NaN，用價格 ±1% 替代
        if np.isnan(bb_up):
            bb_up, bb_mid, bb_low_val = price * 1.01, price, price * 0.99
        fvg    = self._detect_fvg(df)
        # W 型態偵測（影片三：合約槓桿做多策略）
        w_pattern, w_reason = self._detect_w_pattern(df)
        # 支撐區間確認（影片一：手動型態交易策略）
        support_bounce = self._check_support_bounce(df)
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

        # W型態或支撐反彈可降低 RSI 門檻（影片一/三策略加分）
        rsi_threshold = self.config["rsi_buy"]
        if w_pattern or support_bounce:
            rsi_threshold = min(rsi_threshold + 10, 65)  # W型態出現時放寬 RSI 門檻
        if (
            crossroad_ok                          # 過馬路理論：位置+狀態雙確認
            and rsi < rsi_threshold               # RSI 門檻（W型態時可放寬）
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
            f"BB={bb_low_val:.2f}~{bb_up:.2f} | FVG={'是' if fvg else '否'} | "
            f"W={'✅' if w_pattern else '❌'} | 支撐反彈={'✅' if support_bounce else '❌'}"
        )
        print(f"     └ 過馬路: {crossroad_reason}")
        print(f"     └ W型態: {w_reason}")

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
            old = self.config["rsi_buy"]
            # RSI 平均偏高（>50）→ 進場太晚，提高門檻讓條件更嚴格
            if avg_rsi > 50:
                self.config["rsi_buy"] = min(65, old + 2)
            # RSI 平均偏低（<30）→ 進場時機好，可放寬門檻
            elif avg_rsi < 30:
                self.config["rsi_buy"] = max(35, old - 2)
            if self.config["rsi_buy"] != old:
                print(f"  🧠 自主學習：RSI 買入閾值 {old} → {self.config['rsi_buy']} (avg_rsi={avg_rsi:.1f})")
                self._save_config()

    # ── 主迴圈 ────────────────────────────────────────────

    # ════════════════════════════════════════════════════════════
    # 🔥 合約槓桿模組（本金 40%，自動判斷槓桿倍數）
    # 策略來源：阿儒阿育交易室 awEB-UO13PA（W型態做多）
    # ════════════════════════════════════════════════════════════

    def _calc_leverage(self, rsi: float, w_pattern: bool, market_score: float) -> int:
        """
        自動判斷槓桿倍數（最高 20x，最低 3x）
        邏輯：
          - 基礎槓桿 5x
          - W型態出現：+5x（強訊號加碼）
          - RSI < 35（超賣）：+3x
          - RSI < 45：+2x
          - 市場情緒 > 0.5（美股強勢）：+2x
          - 市場情緒 < -0.3（美股弱勢）：-3x（降槓桿）
          - 上限 20x，下限 3x
        """
        lev = 5
        if w_pattern:
            lev += 5
        if rsi < 35:
            lev += 3
        elif rsi < 45:
            lev += 2
        if market_score > 0.5:
            lev += 2
        elif market_score < -0.3:
            lev -= 3
        return max(3, min(MAX_LEVERAGE, lev))

    async def _set_futures_leverage(self, symbol: str, leverage: int) -> bool:
        """設定合約槓桿倍數"""
        try:
            body = {"symbol": symbol, "leverage": str(leverage)}
            resp = await self._request("POST", "/uapi/v1/account/leverage", body=body)
            return resp.get("result", False)
        except Exception as e:
            print(f"  ⚠️ 設定槓桿失敗 [{symbol}]: {e}")
            return False

    async def _get_futures_balance(self) -> float:
        """取得合約帳戶 USDT 餘額"""
        try:
            resp = await self._request("GET", "/uapi/v1/account/balances")
            balances = resp.get("data", {}).get("balances", [])
            for b in balances:
                if b.get("coin") == "USDT":
                    return float(b.get("free", 0))
        except Exception:
            pass
        return 0.0

    async def _get_futures_positions(self) -> list:
        """取得目前合約持倉"""
        try:
            resp = await self._request("GET", "/uapi/v1/account/positions")
            return resp.get("data", {}).get("positions", [])
        except Exception:
            return []

    async def _place_futures_order(self, symbol: str, side: str, size: float, price: float) -> bool:
        """下合約市價單（MARKET_QTY）"""
        try:
            body = {
                "symbol": symbol,
                "side": side,          # "BUY" or "SELL"
                "type": "MARKET_QTY",
                "size": f"{size:.6f}",
                "reduceOnly": False
            }
            resp = await self._request("POST", "/uapi/v1/trade/order", body=body)
            if resp.get("result"):
                order_id = resp.get("data", {}).get("orderId", "?")
                print(f"  ✅ 合約{side}成功 [{symbol}] size={size:.6f} | OrderID={order_id}")
                return True
            else:
                print(f"  ❌ 合約{side}失敗 [{symbol}]: {resp}")
                return False
        except Exception as e:
            print(f"  ❌ 合約下單異常 [{symbol}]: {e}")
            return False

    async def analyze_and_trade_futures(self, symbol: str, futures_budget: float):
        """
        合約槓桿交易主函數
        - symbol: 合約幣種（如 BTC_USDT_PERP）
        - futures_budget: 分配給合約的 USDT 預算
        策略：W型態 + 過馬路 + 自動槓桿
        """
        # 取得對應現貨 K 線（合約用現貨價格分析）
        spot_symbol = symbol.replace("_PERP", "")
        res = await self._request(
            "GET", "/api/v1/market/klines",
            params={"symbol": spot_symbol, "interval": "5M", "limit": 250}
        )
        if not (res and res.get("result")):
            print(f"  ⚠️ 合約 [{symbol}] 取得K線失敗")
            return
        df = pd.DataFrame(res["data"]["klines"])
        for col in ["close", "high", "low", "open"]:
            df[col] = pd.to_numeric(df[col])

        price  = float(df["close"].iloc[-1])
        rsi    = calc_rsi(df["close"], 7)
        if np.isnan(rsi):
            print(f"  ⚠️ 合約 [{symbol}] RSI=nan，K線數據不足，跳過")
            return
        crossroad_ok, crossroad_reason = self._crossroad_filter(df)
        w_pattern, w_reason = self._detect_w_pattern(df)
        support_bounce = self._check_support_bounce(df)

        # 計算自動槓桿倍數
        leverage = self._calc_leverage(rsi, w_pattern, self.market_score)

        # 檢查現有合約持倉
        pos_key = f"FUTURES_{symbol}"
        if pos_key in self.positions:
            pos = self.positions[pos_key]
            entry = pos["entry_price"]
            pnl_rate = (price - entry) / entry
            print(f"  📊 合約持倉 [{symbol}] 入場={entry:.2f} 現價={price:.2f} PnL={pnl_rate*100:.1f}% (槓桿{pos['leverage']}x)")
            # 停利：+3%（實際獲利 = pnl_rate × leverage）
            if pnl_rate >= TAKE_PROFIT_RATE:
                print(f"  🎯 合約停利！PnL={pnl_rate*100:.1f}% 槓桿{pos['leverage']}x → 實際{pnl_rate*pos['leverage']*100:.1f}%，平倉")
                size = pos.get("size", 0)
                if size > 0:
                    success = await self._place_futures_order(symbol, "SELL", size, price)
                    if success:
                        del self.positions[pos_key]
                return
            # 停損：-2%
            elif pnl_rate <= -STOP_LOSS_RATE:
                print(f"  🛑 合約停損！PnL={pnl_rate*100:.1f}%，平倉")
                size = pos.get("size", 0)
                if size > 0:
                    success = await self._place_futures_order(symbol, "SELL", size, price)
                    if success:
                        del self.positions[pos_key]
                return

        # 進場條件：過馬路 + (W型態 OR 支撐反彈) + RSI 未超買
        rsi_threshold = 55 if (w_pattern or support_bounce) else 45
        should_enter = (
            crossroad_ok
            and rsi < rsi_threshold
            and pos_key not in self.positions
        )

        emoji = "🟢" if should_enter else "⚪"
        print(
            f"  {emoji} 合約 [{symbol}] {('BUY' if should_enter else 'HOLD')} | "
            f"Price={price:.2f} | RSI={rsi:.1f} | 槓桿={leverage}x | "
            f"W={'✅' if w_pattern else '❌'} | 支撐反彈={'✅' if support_bounce else '❌'}"
        )
        print(f"     └ 過馬路: {crossroad_reason}")

        if should_enter:
            # 計算倉位大小：預算 × 槓桿 / 價格 = 合約數量
            margin = futures_budget * (1 / len(FUTURES_SYMBOLS))  # 平均分配給每個合約幣種
            if margin < 1.0:
                print(f"  ⚠️ 合約 [{symbol}] 保證金不足（{margin:.2f} USDT）")
                return
            contract_size = (margin * leverage) / price
            # 設定槓桿
            await self._set_futures_leverage(symbol, leverage)
            # 下單
            success = await self._place_futures_order(symbol, "BUY", contract_size, price)
            if success:
                self.positions[pos_key] = {
                    "entry_price": price,
                    "size": contract_size,
                    "leverage": leverage,
                    "margin": margin
                }
                print(f"     └ 合約開倉: 保證金={margin:.2f} USDT | 槓桿={leverage}x | 合約量={contract_size:.6f}")

    async def run(self):
        print("=" * 60)
        print("🚀 Pionex AI Trader - 終極整合版 啟動")
        print(f"   現貨幣種 (60%): {', '.join(SYMBOLS)}")
        print(f"   合約幣種 (40%): {', '.join(FUTURES_SYMBOLS)} | 最高槓桿: {MAX_LEVERAGE}x")
        print(f"   每日目標: {DAILY_TARGET_RATE*100:.0f}% | 停利: +{TAKE_PROFIT_RATE*100:.0f}% | 停損: -{STOP_LOSS_RATE*100:.0f}%")
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

            # 5. 多幣種並行分析與交易（現貨 60% + 合約 40%）
            # 計算合約預算（總資產的 40%）
            futures_budget = self.current_equity * FUTURES_WEIGHT
            spot_tasks = [self.analyze_and_trade(s) for s in SYMBOLS]
            futures_tasks = [self.analyze_and_trade_futures(s, futures_budget) for s in FUTURES_SYMBOLS]
            await asyncio.gather(*(spot_tasks + futures_tasks))

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
