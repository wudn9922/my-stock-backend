from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware  # 🟢 新增：導入安全通行證
import yfinance as yf
import pandas as pd

app = FastAPI()

# 🟢 新增：允許所有外網網頁（包括你的 GitHub Pages）連線進來
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允許所有來源
    allow_credentials=True,
    allow_methods=["*"],  # 允許所有 GET/POST 方法
    allow_headers=["*"],
)

@app.get("/api/analyze")
def analyze_stock(ticker: str, ma1: int = 20, ma2: int = 60):
    try:
        print(f"🔄 正在幫你抓取股票資料: {ticker}")
        stock = yf.Ticker(ticker)
        df = stock.history(period="3mo") # 確保抓取足夠計算均線的歷史資料
        
        if len(df) < max(ma1, ma2):
            df = stock.history(period="6mo")
            
        if df.empty:
            raise HTTPException(status_code=400, detail="找不到該股票代碼，台股請確保有加 .TW")

        df[f'MA_{ma1}'] = df['Close'].rolling(window=ma1).mean()
        df[f'MA_{ma2}'] = df['Close'].rolling(window=ma2).mean()

        latest_price = round(df['Close'].iloc[-1], 2)
        ma1_val = round(df[f'MA_{ma1}'].iloc[-1], 2)
        ma2_val = round(df[f'MA_{ma2}'].iloc[-1], 2)

        if pd.isna(ma1_val) or pd.isna(ma2_val):
            raise HTTPException(status_code=400, detail="均線天數過長，歷史資料不足計算")

        if ma1_val > ma2_val:
            status = "🟢 多頭排列 (短均 > 長均)"
        elif ma1_val < ma2_val:
            status = "🔴 空頭排列 (短均 < 長均)"
        else:
            status = "🟡 均線糾纏 (觀望為宜)"

        return {
            "ticker": ticker.upper(),
            "latest_price": latest_price,
            "ma1_setting": ma1,
            "ma1_value": ma1_val,
            "ma2_setting": ma2,
            "ma2_value": ma2_val,
            "status": status
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
