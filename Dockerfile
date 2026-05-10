FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 複製依賴清單並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有源碼
COPY . .

# 建立資料目錄（用於持久化 evolution_config.json）
RUN mkdir -p /app/data

# 啟動 AI 交易員
CMD ["python", "trader_final.py"]
