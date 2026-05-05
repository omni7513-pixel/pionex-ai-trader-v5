
# Zeabur 部署指南 - Pionex AI Trader v5.1

這份指南將協助您將 AI 交易員部署到 Zeabur 實現 24/7 運行。

## 1. 準備工作
1. 將此資料夾內的所有檔案上傳到您的 GitHub 倉庫。
2. 在 Zeabur 點擊 **Deploy Service** 並選擇該 GitHub 倉庫。

## 2. 環境變數設置 (Variables)
在 Zeabur 的服務設置中添加以下變數：
- `PIONEX_API_KEY`: 您的 Pionex API Key
- `PIONEX_API_SECRET`: 您的 Pionex API Secret
- `OPENAI_API_KEY`: 您的 OpenAI API Key
- `PYTHONUNBUFFERED`: `1` (確保日誌能即時顯示)

## 3. 持久化存儲 (重要：保留 AI 學習進度)
由於 AI 會不斷學習並更新 `evolution_config.json`，請務必執行以下步驟：
1. 在 Zeabur 服務頁面點擊 **Storage**。
2. 點擊 **Add Volume**。
3. 掛載路徑設定為：`/app/data` (或腳本所在的目錄)。
4. **注意**：我已將腳本中的路徑修改為優先讀取環境變數或當前目錄。

## 4. 啟動命令
Zeabur 會自動識別 `Procfile`。如果沒有自動識別，請在 **Start Command** 手動輸入：
`python trader_v5_1.py --live`
