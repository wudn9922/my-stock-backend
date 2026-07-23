import os
import re
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
import uvicorn

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


# =========================================================
# FastAPI 初始化
# =========================================================
app = FastAPI(
    title="自選股均線分析 API",
    description="提供股票均線分析、群組查詢與 Supabase 資料同步",
    version="1.2.0"
)


# =========================================================
# CORS
#
# 保留允許 GitHub Pages、LIFF 與其他前端呼叫的能力。
# =========================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# Render Secret／Environment Variables
#
# 支援：
# SUPABASE_URL=https://你的專案.supabase.co
#
# 或：
# SUPABASE_URL=https://你的專案.supabase.co/rest/v1
#
# 金鑰支援以下任一名稱：
# SUPABASE_KEY
# SUPABASE_ANON_KEY
# =========================================================
RAW_SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_KEY = (
    os.getenv("SUPABASE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)


def normalize_supabase_url(url: str) -> str:
    """
    將 Supabase URL 統一轉成 REST API 網址。

    可接受：
    https://project.supabase.co

    或：
    https://project.supabase.co/rest/v1
    """

    clean_url = url.strip().rstrip("/")

    if not clean_url:
        return ""

    if clean_url.endswith("/rest/v1"):
        return clean_url

    return f"{clean_url}/rest/v1"


SUPABASE_URL = normalize_supabase_url(
    RAW_SUPABASE_URL
)


# =========================================================
# 共用工具
# =========================================================
def check_supabase_config() -> None:
    """
    確認 Render Secret 是否已設定。
    """

    if not SUPABASE_URL:
        raise RuntimeError(
            "尚未設定 SUPABASE_URL"
        )

    if not SUPABASE_KEY:
        raise RuntimeError(
            "尚未設定 SUPABASE_KEY 或 SUPABASE_ANON_KEY"
        )


def get_supabase_headers(
    return_representation: bool = False
) -> dict:
    """
    建立 Supabase REST API Header。
    """

    check_supabase_config()

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    if return_representation:
        headers["Prefer"] = "return=representation"

    return headers


def get_response_error(
    response: requests.Response
) -> str:
    """
    整理 Supabase 或外部服務錯誤訊息。
    """

    try:
        response_text = response.text
    except Exception:
        response_text = "無法取得錯誤內容"

    return (
        f"HTTP {response.status_code}: "
        f"{response_text[:800]}"
    )


def clean_optional_text(
    value: Optional[str]
) -> Optional[str]:
    """
    清理選填文字。
    """

    if value is None:
        return None

    cleaned_value = str(value).strip()

    return cleaned_value or None


def get_history_period(max_ma: int) -> str:
    """
    根據最長均線決定 Yahoo Finance 歷史資料期間。
    """

    if max_ma <= 60:
        return "6mo"

    if max_ma <= 120:
        return "1y"

    if max_ma <= 240:
        return "2y"

    if max_ma <= 500:
        return "5y"

    return "max"


# =========================================================
# 首頁與健康檢查
# =========================================================
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "自選股均線分析 API 正常運作",
        "version": "1.2.0",
        "docs": "/docs"
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "message": "API 服務正常",
        "supabase_url_configured": bool(
            SUPABASE_URL
        ),
        "supabase_key_configured": bool(
            SUPABASE_KEY
        )
    }


# =========================================================
# 取得所有群組
#
# 原本功能保留：
# 回傳格式仍然包含：
# {
#     "groups": [...]
# }
# =========================================================
@app.get("/api/groups")
def get_all_groups():
    try:
        headers = get_supabase_headers()

        response = requests.get(
            f"{SUPABASE_URL}/groups",
            headers=headers,
            params={
                "select": "name",
                "order": "name.asc"
            },
            timeout=10
        )

        if response.status_code != 200:
            print(
                "⚠️ 讀取群組失敗：",
                get_response_error(response)
            )

            return {
                "groups": [],
                "message": "群組讀取失敗"
            }

        response_data = response.json()

        group_names = sorted({
            str(item["name"]).strip()
            for item in response_data
            if item.get("name")
            and str(item["name"]).strip()
        })

        return {
            "groups": group_names
        }

    except requests.Timeout:
        print("⚠️ Supabase 群組查詢逾時")

        return {
            "groups": [],
            "message": "Supabase 群組查詢逾時"
        }

    except Exception as error:
        print(
            f"⚠️ Supabase 群組查詢失敗：{error}"
        )

        return {
            "groups": [],
            "message": str(error)
        }


# =========================================================
# 解析或建立群組
#
# 若有傳 group_id：
# 直接使用 group_id。
#
# 若沒有傳 group_id：
# 保留原本功能，使用 group_name 查詢 groups，
# 找不到時建立新群組。
# =========================================================
def resolve_group_id(
    group_id: Optional[str],
    group_name: str,
    headers: dict
) -> str:
    clean_group_id = clean_optional_text(
        group_id
    )

    if clean_group_id:
        return clean_group_id

    clean_group_name = (
        str(group_name).strip()
        if group_name
        else "核心權值精選"
    )

    if not clean_group_name:
        clean_group_name = "核心權值精選"

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
        return str(group_data[0]["id"])

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

    return str(inserted_groups[0]["id"])


# =========================================================
# 將股票設定同步至 Supabase
#
# 資料辨識方式：
# line_user_id + ticker + group_id
#
# 因此：
# 2330.TW + tw_g1
# 2330.TW + tw_g2
#
# 可以同時存在。
# =========================================================
def sync_stock_to_supabase(
    formatted_ticker: str,
    ma1: int,
    ma2: int,
    ma3: int,
    ma4: int,
    group_name: str,
    group_id: Optional[str],
    line_user_id: Optional[str]
) -> dict:
    try:
        headers = get_supabase_headers(
            return_representation=True
        )

        clean_line_user_id = clean_optional_text(
            line_user_id
        )

        resolved_group_id = resolve_group_id(
            group_id=group_id,
            group_name=group_name,
            headers=headers
        )

        # ---------------------------------------------
        # 查詢同一使用者、同一股票、同一群組的資料
        # ---------------------------------------------
        query_params = {
            "ticker": f"eq.{formatted_ticker}",
            "group_id": f"eq.{resolved_group_id}",
            "select": (
                "id,ticker,group_id,line_user_id"
            ),
            "limit": "1"
        }

        if clean_line_user_id:
            query_params["line_user_id"] = (
                f"eq.{clean_line_user_id}"
            )
        else:
            # 舊版沒有傳入 line_user_id 時，
            # 只查詢 line_user_id 為 null 的舊資料，
            # 避免更新到其他使用者。
            query_params["line_user_id"] = (
                "is.null"
            )

        stock_response = requests.get(
            f"{SUPABASE_URL}/stocks",
            headers=headers,
            params=query_params,
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

        stock_payload = {
            "ticker": formatted_ticker,
            "group_id": resolved_group_id,
            "ma1": ma1,
            "ma2": ma2,
            "ma3": ma3,
            "ma4": ma4
        }

        if clean_line_user_id:
            stock_payload["line_user_id"] = (
                clean_line_user_id
            )

        # ---------------------------------------------
        # 已存在：依 id 精確更新
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

            return {
                "success": True,
                "action": "updated",
                "message": "Supabase 股票設定更新成功",
                "group_id": resolved_group_id
            }

        # ---------------------------------------------
        # 不存在：新增一筆
        # ---------------------------------------------
        insert_response = requests.post(
            f"{SUPABASE_URL}/stocks",
            headers=headers,
            json=stock_payload,
            timeout=10
        )

        if insert_response.status_code not in (
            200,
            201
        ):
            raise RuntimeError(
                "新增股票設定失敗："
                + get_response_error(
                    insert_response
                )
            )

        return {
            "success": True,
            "action": "inserted",
            "message": "Supabase 股票設定新增成功",
            "group_id": resolved_group_id
        }

    except requests.Timeout:
        return {
            "success": False,
            "action": None,
            "message": "Supabase 連線逾時"
        }

    except Exception as error:
        return {
            "success": False,
            "action": None,
            "message": str(error)
        }


# =========================================================
# 股票均線分析
#
# 原本參數保留：
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
        # ---------------------------------------------
        # 股票代號驗證
        # ---------------------------------------------
        formatted_ticker = (
            str(ticker)
            .strip()
            .upper()
        )

        if not formatted_ticker:
            raise HTTPException(
                status_code=400,
                detail="請輸入股票代號！"
            )

        ticker_pattern = re.compile(
            r"^[A-Z0-9.^_-]{1,30}$"
        )

        if not ticker_pattern.fullmatch(
            formatted_ticker
        ):
            raise HTTPException(
                status_code=400,
                detail="股票代號格式不正確"
            )

        print(
            f"🔄 正在抓取股票資料："
            f"{formatted_ticker}"
        )

        # ---------------------------------------------
        # 均線參數驗證
        # ---------------------------------------------
        all_mas = [
            ma1,
            ma2,
            ma3,
            ma4
        ]

        if any(ma < 0 for ma in all_mas):
            raise HTTPException(
                status_code=400,
                detail="均線天數不可小於 0"
            )

        active_mas = [
            ma
            for ma in all_mas
            if ma > 0
        ]

        if not active_mas:
            raise HTTPException(
                status_code=400,
                detail=(
                    "請至少輸入一條大於 0 "
                    "的均線天數！"
                )
            )

        if len(set(active_mas)) != len(
            active_mas
        ):
            raise HTTPException(
                status_code=400,
                detail="均線參數不可重複！"
            )

        if any(
            ma > 2000
            for ma in active_mas
        ):
            raise HTTPException(
                status_code=400,
                detail="均線天數不可超過 2000 天！"
            )

        max_ma = max(active_mas)

        period = get_history_period(
            max_ma
        )

        # ---------------------------------------------
        # 取得 Yahoo Finance 資料
        # ---------------------------------------------
        stock = yf.Ticker(
            formatted_ticker
        )

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
                detail="股票資料缺少 Close 欄位"
            )

        df = df.dropna(
            subset=["Close"]
        )

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail="沒有可用的收盤價資料"
            )

        latest_price = round(
            float(df["Close"].iloc[-1]),
            2
        )

        # ---------------------------------------------
        # 計算均線
        # ---------------------------------------------
        score = 0
        ma_results = {}

        for index, ma in enumerate(
            all_mas,
            start=1
        ):
            if ma > 0:
                column_name = f"MA_{ma}"

                df[column_name] = (
                    df["Close"]
                    .rolling(window=ma)
                    .mean()
                )

                raw_ma_value = (
                    df[column_name].iloc[-1]
                )

                if pd.isna(raw_ma_value):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{ma}MA 天數過長，"
                            "歷史資料不足計算"
                        )
                    )

                ma_value = round(
                    float(raw_ma_value),
                    2
                )

                ma_results[
                    f"ma{index}"
                ] = ma

                ma_results[
                    f"ma{index}_val"
                ] = ma_value

                if latest_price > ma_value:
                    score += 1
                elif latest_price < ma_value:
                    score -= 1

            else:
                ma_results[
                    f"ma{index}"
                ] = 0

                ma_results[
                    f"ma{index}_val"
                ] = None

        # ---------------------------------------------
        # 判斷趨勢
        # ---------------------------------------------
        if score > 0:
            status = (
                f"🟢 多頭趨勢 "
                f"(得分: +{score})"
            )
        elif score < 0:
            status = (
                f"🔴 空頭趨勢 "
                f"(得分: {score})"
            )
        else:
            status = (
                "🟡 多空不明 "
                "(得分: 0)"
            )

        # ---------------------------------------------
        # 同步 Supabase
        #
        # 同步失敗不影響股票分析結果。
        # ---------------------------------------------
        database_sync = sync_stock_to_supabase(
            formatted_ticker=formatted_ticker,
            ma1=ma1,
            ma2=ma2,
            ma3=ma3,
            ma4=ma4,
            group_name=group_name,
            group_id=group_id,
            line_user_id=line_user_id
        )

        if database_sync["success"]:
            print(
                "✅ "
                + database_sync["message"]
                + "："
                + formatted_ticker
            )
        else:
            print(
                "⚠️ Supabase 同步失敗："
                + database_sync["message"]
            )

        # ---------------------------------------------
        # 回傳分析結果
        #
        # 原本欄位全部保留。
        # ---------------------------------------------
        return {
            "ticker": formatted_ticker,
            "latest_price": latest_price,
            "status": status,
            "ma_results": ma_results,
            "score": score,
            "database_sync": database_sync
        }

    except HTTPException as http_error:
        raise http_error

    except requests.Timeout:
        raise HTTPException(
            status_code=504,
            detail=(
                "股票資料服務連線逾時，"
                "請稍後再試"
            )
        )

    except Exception as error:
        print(
            "❌ 股票分析錯誤："
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


# =========================================================
# Render 啟動入口
#
# 支援 Render Start Command 使用：
# python main.py
#
# 也支援：
# uvicorn main:app --host 0.0.0.0 --port $PORT
# =========================================================
if __name__ == "__main__":
    render_port = int(
        os.getenv("PORT", "8000")
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=render_port
    )
