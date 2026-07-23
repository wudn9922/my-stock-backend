import os
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="自選股均線分析 API",
    description="提供股票均線分析、群組查詢與 Supabase 資料同步功能",
    version="1.1.0"
)


# =========================================================
# CORS 設定
# 保留原本允許所有來源的功能。
# 正式環境上線後，建議改成指定你的前端網址。
# =========================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# Supabase 設定
#
# 優先讀取環境變數：
# SUPABASE_URL
# SUPABASE_KEY
#
# 若環境變數不存在，則使用原本設定，避免影響現有功能。
# =========================================================
SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://bxhqpfeberqbtxymghyt.supabase.co/rest/v1"
).rstrip("/")

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "sb_publishable_eEJNM_96jblQ_90vpcYC0g_PzyGJNOK"
)


def get_supabase_headers(
    return_representation: bool = False
) -> dict:
    """
    建立 Supabase REST API 共用標頭。
    """

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    if return_representation:
        headers["Prefer"] = "return=representation"

    return headers


def get_response_error(response: requests.Response) -> str:
    """
    取得 Supabase 或外部 API 的錯誤內容。
    避免錯誤訊息過長。
    """

    try:
        error_text = response.text
    except Exception:
        error_text = "無法讀取錯誤內容"

    return (
        f"HTTP {response.status_code}: "
        f"{error_text[:500]}"
    )


# =========================================================
# 健康檢查
# =========================================================
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "自選股均線分析 API 正常運作",
        "docs": "/docs"
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "supabase_url": SUPABASE_URL,
        "message": "API 服務正常"
    }


# =========================================================
# 取得 Supabase 裡原本存在的所有群組
# 原功能完整保留
# =========================================================
@app.get("/api/groups")
def get_all_groups():
    try:
        headers = get_supabase_headers()

        response = requests.get(
            f"{SUPABASE_URL}/groups",
            headers=headers,
            params={
                "select": "name"
            },
            timeout=10
        )

        if response.status_code != 200:
            print(
                "⚠️ 讀取 Supabase 群組失敗：",
                get_response_error(response)
            )

            return {
                "groups": [],
                "message": "群組讀取失敗"
            }

        response_data = response.json()

        # 移除空白名稱與重複名稱，並排序
        names = sorted(
            list(
                {
                    str(item["name"]).strip()
                    for item in response_data
                    if item.get("name")
                    and str(item["name"]).strip()
                }
            )
        )

        return {
            "groups": names
        }

    except requests.Timeout:
        print("⚠️ Supabase 群組查詢逾時")

        return {
            "groups": [],
            "message": "Supabase 查詢逾時"
        }

    except Exception as error:
        print(f"⚠️ Supabase 群組查詢失敗：{error}")

        return {
            "groups": [],
            "message": "群組查詢發生錯誤"
        }


# =========================================================
# 股票分析 API
#
# 原本參數全部保留：
# ticker
# ma1
# ma2
# ma3
# ma4
# group_name
#
# 新增選填參數：
# group_id
# line_user_id
#
# 因此舊的 API 呼叫方式仍可繼續使用。
# =========================================================
@app.get("/api/analyze")
def analyze_stock(
    ticker: str,
    ma1: int = 0,
    ma2: int = 0,
    ma3: int = 0,
    ma4: int = 0,
    group_name: str = "核心權值精選",
    group_id: Optional[str] = None,
    line_user_id: Optional[str] = None
):
    try:
        # -------------------------------------------------
        # 整理股票代號
        # -------------------------------------------------
        formatted_ticker = ticker.strip().upper()

        if not formatted_ticker:
            raise HTTPException(
                status_code=400,
                detail="請輸入股票代號！"
            )

        print(
            f"🔄 正在幫你抓取股票資料: "
            f"{formatted_ticker}"
        )

        # -------------------------------------------------
        # 均線參數驗證
        # -------------------------------------------------
        all_mas = [ma1, ma2, ma3, ma4]

        active_mas = [
            ma
            for ma in all_mas
            if ma and ma > 0
        ]

        if not active_mas:
            raise HTTPException(
                status_code=400,
                detail="請至少輸入一條大於 0 的均線天數！"
            )

        if len(set(active_mas)) != len(active_mas):
            raise HTTPException(
                status_code=400,
                detail="均線參數不可重複！"
            )

        if any(ma > 2000 for ma in active_mas):
            raise HTTPException(
                status_code=400,
                detail="均線天數不可超過 2000 天！"
            )

        # -------------------------------------------------
        # 根據最長均線決定抓取期間
        # -------------------------------------------------
        max_ma = max(active_mas)

        if max_ma <= 60:
            period = "6mo"
        elif max_ma <= 120:
            period = "1y"
        elif max_ma <= 240:
            period = "2y"
        else:
            period = "5y"

        # -------------------------------------------------
        # 取得 Yahoo Finance 股票資料
        # -------------------------------------------------
        stock = yf.Ticker(formatted_ticker)

        df = stock.history(
            period=period,
            auto_adjust=False
        )

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail=(
                    "找不到該股票代碼，"
                    "台股請確認有加上 .TW 或 .TWO"
                )
            )

        if "Close" not in df.columns:
            raise HTTPException(
                status_code=500,
                detail="股票資料中沒有收盤價欄位"
            )

        df = df.dropna(subset=["Close"])

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail="該股票沒有可用的收盤價資料"
            )

        latest_price = round(
            float(df["Close"].iloc[-1]),
            2
        )

        # -------------------------------------------------
        # 計算均線與多空分數
        # -------------------------------------------------
        score = 0
        ma_results = {}

        for index, ma in enumerate(all_mas, 1):
            if ma and ma > 0:
                column_name = f"MA_{ma}"

                df[column_name] = (
                    df["Close"]
                    .rolling(window=ma)
                    .mean()
                )

                raw_ma_value = df[column_name].iloc[-1]

                if pd.isna(raw_ma_value):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{ma}MA 天數過長，"
                            "目前取得的歷史資料不足計算"
                        )
                    )

                ma_value = round(
                    float(raw_ma_value),
                    2
                )

                ma_results[f"ma{index}"] = ma
                ma_results[f"ma{index}_val"] = ma_value

                if latest_price > ma_value:
                    score += 1
                elif latest_price < ma_value:
                    score -= 1

            else:
                ma_results[f"ma{index}"] = 0
                ma_results[f"ma{index}_val"] = None

        # -------------------------------------------------
        # 判斷多空趨勢
        # -------------------------------------------------
        if score > 0:
            status = f"🟢 多頭趨勢 (得分: +{score})"
        elif score < 0:
            status = f"🔴 空頭趨勢 (得分: {score})"
        else:
            status = "🟡 多空不明 (得分: 0)"

        # -------------------------------------------------
        # 同步到 Supabase
        #
        # 此區塊即使失敗，也不影響股票分析結果回傳，
        # 保留你原本的運作方式。
        # -------------------------------------------------
        database_sync_success = False
        database_sync_message = "尚未同步"

        try:
            headers = get_supabase_headers(
                return_representation=True
            )

            clean_group_name = (
                group_name.strip()
                if group_name
                else "核心權值精選"
            )

            clean_group_id = (
                group_id.strip()
                if group_id and group_id.strip()
                else None
            )

            clean_line_user_id = (
                line_user_id.strip()
                if line_user_id
                and line_user_id.strip()
                else None
            )

            # ---------------------------------------------
            # 如果前端有傳 group_id，直接使用。
            #
            # 如果沒有傳 group_id，保留原本功能：
            # 依 group_name 查詢群組，找不到就新增。
            # ---------------------------------------------
            resolved_group_id = clean_group_id

            if not resolved_group_id:
                group_response = requests.get(
                    f"{SUPABASE_URL}/groups",
                    headers=headers,
                    params={
                        "name": f"eq.{clean_group_name}",
                        "select": "id,name",
                        "limit": "1"
                    },
                    timeout=10
                )

                if group_response.status_code != 200:
                    raise RuntimeError(
                        "查詢群組失敗："
                        + get_response_error(
                            group_response
                        )
                    )

                group_data = group_response.json()

                if group_data:
                    resolved_group_id = str(
                        group_data[0]["id"]
                    )
                else:
                    insert_group_response = requests.post(
                        f"{SUPABASE_URL}/groups",
                        headers=headers,
                        json={
                            "name": clean_group_name
                        },
                        timeout=10
                    )

                    if insert_group_response.status_code not in (
                        200,
                        201
                    ):
                        raise RuntimeError(
                            "建立群組失敗："
                            + get_response_error(
                                insert_group_response
                            )
                        )

                    inserted_groups = (
                        insert_group_response.json()
                    )

                    if not inserted_groups:
                        raise RuntimeError(
                            "群組已建立，但沒有取得群組 ID"
                        )

                    resolved_group_id = str(
                        inserted_groups[0]["id"]
                    )

            # ---------------------------------------------
            # 建立股票查詢條件
            # ---------------------------------------------
            stock_query_params = {
                "ticker": f"eq.{formatted_ticker}",
                "group_id": f"eq.{resolved_group_id}",
                "select": (
                    "id,ticker,group_id,"
                    "line_user_id"
                ),
                "limit": "1"
            }

            # 有傳 line_user_id 時才加入使用者條件
            # 避免更新到其他使用者的股票
            if clean_line_user_id:
                stock_query_params["line_user_id"] = (
                    f"eq.{clean_line_user_id}"
                )

            stock_response = requests.get(
                f"{SUPABASE_URL}/stocks",
                headers=headers,
                params=stock_query_params,
                timeout=10
            )

            if stock_response.status_code != 200:
                raise RuntimeError(
                    "查詢股票設定失敗："
                    + get_response_error(
                        stock_response
                    )
                )

            existing_stocks = stock_response.json()

            # ---------------------------------------------
            # 建立新增／修改資料
            # ---------------------------------------------
            stock_payload = {
                "ticker": formatted_ticker,
                "group_id": resolved_group_id,
                "ma1": ma1,
                "ma2": ma2,
                "ma3": ma3,
                "ma4": ma4
            }

            # 有傳入使用者 ID 才寫入 line_user_id，
            # 確保舊版 API 呼叫仍然可以使用。
            if clean_line_user_id:
                stock_payload["line_user_id"] = (
                    clean_line_user_id
                )

            # ---------------------------------------------
            # 已存在：更新
            # 不存在：新增
            # ---------------------------------------------
            if existing_stocks:
                stock_id = existing_stocks[0]["id"]

                update_response = requests.patch(
                    f"{SUPABASE_URL}/stocks",
                    headers=headers,
                    params={
                        "id": f"eq.{stock_id}"
                    },
                    json=stock_payload,
                    timeout=10
                )

                if update_response.status_code not in (
                    200,
                    204
                ):
                    raise RuntimeError(
                        "更新股票設定失敗："
                        + get_response_error(
                            update_response
                        )
                    )

                database_sync_success = True
                database_sync_message = (
                    "Supabase 股票設定更新成功"
                )

            else:
                insert_stock_response = requests.post(
                    f"{SUPABASE_URL}/stocks",
                    headers=headers,
                    json=stock_payload,
                    timeout=10
                )

                if insert_stock_response.status_code not in (
                    200,
                    201
                ):
                    raise RuntimeError(
                        "新增股票設定失敗："
                        + get_response_error(
                            insert_stock_response
                        )
                    )

                database_sync_success = True
                database_sync_message = (
                    "Supabase 股票設定新增成功"
                )

            print(
                f"✅ {database_sync_message}："
                f"{formatted_ticker}"
            )

        except requests.Timeout:
            database_sync_message = (
                "Supabase 連線逾時"
            )

            print(
                f"⚠️ Supabase 寫入失敗："
                f"{database_sync_message}"
            )

        except Exception as db_error:
            database_sync_message = str(db_error)

            print(
                f"⚠️ Supabase 寫入失敗："
                f"{database_sync_message}"
            )

        # -------------------------------------------------
        # 回傳分析結果
        #
        # 原本欄位全部保留：
        # ticker
        # latest_price
        # status
        # ma_results
        # score
        #
        # 額外新增 database_sync，
        # 不會影響原本前端讀取。
        # -------------------------------------------------
        return {
            "ticker": formatted_ticker,
            "latest_price": latest_price,
            "status": status,
            "ma_results": ma_results,
            "score": score,
            "database_sync": {
                "success": database_sync_success,
                "message": database_sync_message
            }
        }

    except HTTPException as http_error:
        raise http_error

    except requests.Timeout:
        raise HTTPException(
            status_code=504,
            detail="股票資料服務連線逾時，請稍後再試"
        )

    except Exception as error:
        print(
            f"❌ 股票分析發生錯誤："
            f"{type(error).__name__}: {error}"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
