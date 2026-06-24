from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import requests  # 🟢 新增：用於與 Supabase REST API 連線

app = FastAPI()

# 允許所有外網網頁連線
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🟢 Supabase 金鑰設定
SUPABASE_URL = "https://bxhqpfeberqbtxymghyt.supabase.co/rest/v1"
SUPABASE_KEY = "sb_publishable_eEJNM_96jblQ_90vpcYC0g_PzyGJNOK"

@app.get("/api/analyze")
def analyze_stock(ticker: str, ma1: int = 20, ma2: int = 60, group_name: str = "核心權值精選"):
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

        # =========================================================================
        # 📡 新增：同步將資料存入 Supabase 雲端資料庫 (採取安全防護，不影響原本網頁輸出)
        # =========================================================================
        try:
            formatted_ticker = ticker.strip().upper()
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            
            # Step 1: 檢查此群組是否存在
            group_res = requests.get(f"{SUPABASE_URL}/groups?name=eq.{group_name}", headers=headers, timeout=5)
            if group_res.status_code == 200 and group_res.json():
                group_id = group_res.json()[0]['id']
                # 更新該群組的自訂均線
                requests.patch(f"{SUPABASE_URL}/groups?id=eq.{group_id}", json={"ma1": ma1, "ma2": ma2}, headers=headers, timeout=5)
            else:
                # 建立新群組
                new_group = {"name": group_name, "ma1": ma1, "ma2": ma2}
                ins_res = requests.post(f"{SUPABASE_URL}/groups", json=new_group, headers=headers, timeout=5)
                group_id = ins_res.json()[0]['id']
                
            # Step 2: 檢查這檔股票是否已經存在於該群組中（避免重複新增）
            stock_res = requests.get(f"{SUPABASE_URL}/stocks?ticker=eq.{formatted_ticker}&group_id=eq.{group_id}", headers=headers, timeout=5)
            if stock_res.status_code == 200 and not stock_res.json():
                # 不存在才寫入
                new_stock = {"ticker": formatted_ticker, "group_id": group_id}
                requests.post(f"{SUPABASE_URL}/stocks", json=new_stock, headers=headers, timeout=5)
                print(f"🚀 雲端同步成功：{formatted_ticker} 已綁定至 【{group_name}】")
                
        except Exception as db_err:
            print(f"⚠️ Supabase 背景寫入失敗，但仍照常輸出即時分析。原因: {db_err}")

        # =========================================================================

        return {
            "ticker": ticker.upper(),
            "latest_price": latest_price,
            "ma1_setting": ma1,
            "ma1_value": ma1_val,
            "ma2_setting": ma2,
            "ma2_value": ma2_val,
            "status": status
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
