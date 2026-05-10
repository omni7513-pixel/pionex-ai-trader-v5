FROM python:3.11-slim

# 設定工作目錄（使用 /code 避免與 Volume 衝突）
WORKDIR /code

# 複製依賴清單並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有源碼
COPY . .

# 建立持久化資料目錄
RUN mkdir -p /data

# 設定資料目錄環境變數
ENV DATA_DIR=/data

# 確保 Python 輸出即時顯示（不緩衝）
ENV PYTHONUNBUFFERED=1

# 啟動 AI 交易員
CMD ["python", "-u", "trader_final.py"]
