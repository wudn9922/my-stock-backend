from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = "https://bxhqpfeberqbtxymghyt.supabase.co/rest/v1"
SUPABASE_KEY = "sb_publishable_eEJNM_96jblQ_90vpcYC0g_PzyGJNOK"

@app.get("/api/analyze")
def analyze_stock(ticker: str, ma1: int = 0, ma2: int = 0, ma3: int = 0, ma4: int = 0, group_name: str = "核心權值精選"):
    try:
        print(f"🔄 正在幫你抓取股票資料: {ticker}")
        
        # 🟢 1. 過濾出有輸入且大於 0 的有效均線
        active_mas = [ma for ma in [ma1, ma2, ma3, ma4] if ma and ma > 0]
        if not active_mas:
            raise HTTPException(status_code=400, detail="請至少輸入一條大於 0 的均線天數！")
        
        stock = yf.Ticker(ticker)
        
        # 根據最大的均線天數動態決定抓取歷史資料範圍
        max_ma = max(active_mas)
        if max_ma <= 60:
            period = "6mo"
        elif max_ma <= 120:
            period = "1y"
        elif max_ma <= 240:
            period = "2y"
        else:
            period = "5y"
            
        df = stock.history(period=period)
        if df.empty:
            raise HTTPException(status_code=400, detail="找不到該股票代碼，台股請確保有加 .TW")

        df = df.dropna(subset=['Close'])
        latest_price = round(df['Close'].iloc[-1], 2)

        # 🟢 2. 計分制多空邏輯核心
        score = 0
        ma_results = {}
        
        for i, ma in enumerate([ma1, ma2, ma3, ma4], 1):
            if ma and ma > 0:
                df[f'MA_{ma}'] = df['Close'].rolling(window=ma).mean()
                ma_val = round(df[f'MA_{ma}'].iloc[-1], 2)
                
                if pd.isna(ma_val):
                    raise HTTPException(status_code=400, detail=f"{ma}MA 天數過長，歷史資料不足計算")
                
                ma_results[f"ma{i}"] = ma
                ma_results[f"ma{i}_val"] = ma_val
                
                # 股價在均線上 +1，在均線下 -1
                if latest_price > ma_val:
                    score += 1
                elif latest_price < ma_val:
                    score -= 1
            else:
                ma_results[f"ma{i}"] = 0
                ma_results[f"ma{i}_val"] = None

        # 🟢 3. 根據總分判定多空趨勢
        if score > 0:
            status = f"🟢 多頭趨勢 (得分: +{score})"
        elif score < 0:
            status = f"🔴 空頭趨勢 (得分: {score})"
        else:
            status = f"🟡 多空不明 (得分: 0)"

        # =========================================================================
        # 📡 同步將資料存入 Supabase 雲端資料庫
        # =========================================================================
        try:
            formatted_ticker = ticker.strip().upper()
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            
            group_data = {"name": group_name, "ma1": ma1, "ma2": ma2, "ma3": ma3, "ma4": ma4}
            
            group_res = requests.get(f"{SUPABASE_URL}/groups?name=eq.{group_name}", headers=headers, timeout=5)
            if group_res.status_code == 200 and group_res.json():
                group_id = group_res.json()[0]['id']
                requests.patch(f"{SUPABASE_URL}/groups?id=eq.{group_id}", json=group_data, headers=headers, timeout=5)
            else:
                ins_res = requests.post(f"{SUPABASE_URL}/groups", json=group_data, headers=headers, timeout=5)
                group_id = ins_res.json()[0]['id']
                
            stock_res = requests.get(f"{SUPABASE_URL}/stocks?ticker=eq.{formatted_ticker}&group_id=eq.{group_id}", headers=headers, timeout=5)
            if stock_res.status_code == 200 and not stock_res.json():
                new_stock = {"ticker": formatted_ticker, "group_id": group_id}
                requests.post(f"{SUPABASE_URL}/stocks", json=new_stock, headers=headers, timeout=5)
                print(f"🚀 雲端同步成功：{formatted_ticker} 已綁定至 【{group_name}】")
                
        except Exception as db_err:
            print(f"⚠️ Supabase 背景同步失敗（請確認欄位是否補齊），原因: {db_err}")
        # =========================================================================

        return {
            "ticker": ticker.upper(),
            "latest_price": latest_price,
            "status": status,
            "ma_results": ma_results,
            "score": score
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
