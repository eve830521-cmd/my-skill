"""
Valuation Indicator Backtester & Pipeline SSOT Engine for Korean Stock Analyzer (v4.2)
- Enforces Single Price Anchor (latest daily close)
- Enforces Controlling Shareholder Net Income/Equity (지배주주 순이익/지분) & Split/Bonus-issue Retroactive Adjustment
- [NEW v4.2 #1] Step 1.5 Book Value Decomposition & SOTP 20% Rule (본업가치 + 비영업자산 - 순차입금) + Core Operating Earnings/ROIC Sustainability Check
- [NEW v4.2 #2] Step 2.0 Mandatory 4-Regime Shift Gate (설비 CapEx, 자본구조, 비영업자산, 사업구성) before applying PER/PBR bands
- [NEW v4.2 #3] Cyclical Peak-Earnings × Peak-Multiple Trap Guard (Mid-Cycle EPS normalization & Peak Multiple compression)
- [NEW v4.2 #4] Multi-Stage Risk Causal Chain & Domino Contagion Diagnosis (1차 촉발 ➔ 2차 영업/재고 전이 ➔ 3차 재무/자본 희석)
- [NEW v4.2 #5] Popperian Hypothesis Falsification Kill-Switch & QoQ Delta Generator ("이 숫자가 나오면 약화" + 직전 분기 대비 QoQ)
- [NEW v4.2 #6] Pipeline SSOT (Single Source of Truth): Code computes 100% of numbers and exports valuation_metrics.json & auto_tables.md; AI focuses 100% on interpretation.
"""

import sys
import os
import glob
import sqlite3
import argparse
import urllib.request
import json
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch_naver_official_metrics(stock_code):
    """Fetches benchmark official metrics from Naver Mobile Integration API."""
    url = f"https://m.stock.naver.com/api/stock/{stock_code}/integration"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        data = json.loads(resp)
        metrics = {}
        for it in data.get("totalInfos", []):
            k = it.get("key", "").strip()
            v = it.get("value", "").strip()
            metrics[k] = v

        def parse_num(val_str):
            if not val_str or val_str in ("N/A", "-"):
                return None
            clean = (
                val_str.replace(",", "")
                .replace("배", "")
                .replace("원", "")
                .replace("%", "")
                .replace("억", "")
                .strip()
            )
            try:
                return float(clean)
            except Exception:
                return None

        res = {
            "stock_name": data.get("stockName"),
            "close": parse_num(metrics.get("현재가") or metrics.get("전일")),
            "bps": parse_num(metrics.get("BPS")),
            "pbr": parse_num(metrics.get("PBR")),
            "eps": parse_num(metrics.get("EPS")),
            "per": parse_num(metrics.get("PER")),
            "div_yield": parse_num(metrics.get("배당수익률")),
            "dps": parse_num(metrics.get("주당배당금")),
            "market_cap_str": metrics.get("시총"),
        }
        return res
    except Exception as e:
        print(f"[Warning] Failed to fetch Naver official metrics: {e}")
        return {}


def fetch_weekly_candles(stock_code, count=600):
    """Fetches weekly candlestick data from Naver Chart XML (up to 10-12 years)."""
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={stock_code}&timeframe=week&count={count}&requestType=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode("euc-kr", errors="replace")
        root = ET.fromstring(resp)
        chartdata = root.find("chartdata")
        if chartdata is None:
            return pd.DataFrame()
        items = chartdata.findall("item")
        data = []
        for item in items:
            parts = item.attrib.get("data", "").split("|")
            if len(parts) == 6:
                d_str, op, hi, lo, cl, vol = parts
                dt = datetime.strptime(d_str, "%Y%m%d")
                data.append({
                    "date": dt,
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "vol": float(vol),
                })
        df = pd.DataFrame(data)
        if not df.empty:
            df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[Error] Failed to fetch weekly candles: {e}")
        return pd.DataFrame()


def fetch_daily_candles(stock_code, count=150):
    """Fetches daily candlestick data from Naver Chart XML."""
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={stock_code}&timeframe=day&count={count}&requestType=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode("euc-kr", errors="replace")
        root = ET.fromstring(resp)
        chartdata = root.find("chartdata")
        if chartdata is None:
            return pd.DataFrame()
        items = chartdata.findall("item")
        data = []
        for item in items:
            parts = item.attrib.get("data", "").split("|")
            if len(parts) == 6:
                d_str, op, hi, lo, cl, vol = parts
                dt = datetime.strptime(d_str, "%Y%m%d")
                data.append({
                    "date": dt,
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "vol": float(vol),
                })
        df = pd.DataFrame(data)
        if not df.empty:
            df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[Error] Failed to fetch daily candles: {e}")
        return pd.DataFrame()


def _parse_dart_fnltt_list(raw_list):
    """
    Extracts key BS/IS items from OpenDART /fnlttSinglAcntAll.json list,
    prioritizing Controlling Equity & Profit, and also extracting Non-Operating Assets,
    PP&E (설비자산), Debt, Cash, and Revenue for v4.2 SOTP & 4-Regime Checks.
    """
    res = {
        "자본총계": 0.0,
        "지배자본": 0.0,
        "영업이익": 0.0,
        "당기순이익": 0.0,
        "지배순이익": 0.0,
        "EPS": 0.0,
        "매출액": 0.0,
        "비영업자산": 0.0,
        "유형자산": 0.0,
        "부채총계": 0.0,
    }
    found_id = set()
    non_op_items = 0.0

    for item in raw_list:
        acc_id = item.get("account_id", "")
        acc_nm = item.get("account_nm", "").replace(" ", "")
        sj = item.get("sj_div", "")
        val_str = item.get("thstrm_add_amount") or item.get("thstrm_amount") or ""
        if not val_str:
            continue
        try:
            val = float(str(val_str).replace(",", ""))
        except ValueError:
            continue

        def assign(k, by_id=False):
            if by_id:
                res[k] = val
                found_id.add(k)
            elif k not in found_id and res[k] == 0.0:
                res[k] = val

        if sj == "BS":
            if acc_id == "ifrs-full_EquityAttributableToOwnersOfParent":
                assign("지배자본", True)
            elif "지배기업소유주지분" in acc_nm or "지배기업의소유주" in acc_nm:
                assign("지배자본")
            elif acc_id == "ifrs-full_Equity":
                assign("자본총계", True)
            elif acc_nm == "자본총계":
                assign("자본총계")
            elif acc_id == "ifrs-full_PropertyPlantAndEquipment" or acc_nm == "유형자산":
                assign("유형자산", acc_id == "ifrs-full_PropertyPlantAndEquipment")
            elif acc_id == "ifrs-full_Liabilities" or acc_nm == "부채총계":
                assign("부채총계", acc_id == "ifrs-full_Liabilities")
            elif any(
                kw in acc_nm
                for kw in (
                    "투자부동산",
                    "관계기업투자",
                    "공동기업투자",
                    "종속기업,관계기업및공동기업투자",
                    "기타포괄손익-공정가치측정금융자산",
                    "장기금융상품",
                    "장기기타금융자산",
                )
            ):
                if val > 0:
                    non_op_items += val

        elif sj in ("IS", "CIS"):
            if acc_id == "ifrs-full_Revenue" or acc_nm in ("매출액", "영업수익", "수익(매출액)"):
                assign("매출액", acc_id == "ifrs-full_Revenue")
            elif acc_id == "dart_OperatingIncomeLoss":
                assign("영업이익", True)
            elif acc_nm in ("영업이익", "영업손실", "영업이익(손실)"):
                assign("영업이익")
            elif acc_id == "ifrs-full_ProfitLoss":
                assign("당기순이익", True)
            elif acc_nm in ("당기순이익", "당기순손실", "당기순이익(손실)"):
                assign("당기순이익")
            elif acc_id == "ifrs-full_ProfitLossAttributableToOwnersOfParent":
                assign("지배순이익", True)
            elif ("지배" in acc_nm) and ("순이익" in acc_nm or "손익" in acc_nm):
                assign("지배순이익")
            elif acc_id == "ifrs-full_BasicEarningsLossPerShare":
                assign("EPS", True)
            elif acc_nm in ("기본주당이익", "기본주당순이익", "기본주당순이익(손실)"):
                assign("EPS")

    res["비영업자산"] = non_op_items
    return res


def _reconstruct_from_cache_db(db_path, corp_code, stock_code, corp_name):
    """Reconstructs sheet2 (Quarterly), sheet3 (TTM), and sheet5 (Band) from cache.db api_cache with split-adjusted EPS."""
    reprt_to_q = {"11013": "1Q", "11012": "2Q", "11014": "3Q", "11011": "4Q"}
    q_order = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            "SELECT params, response FROM api_cache WHERE endpoint = '/fnlttSinglAcntAll.json' AND params LIKE ?",
            (f'%"corp_code": "{corp_code}"%',),
        )
        rows = c.fetchall()
        if not rows:
            conn.close()
            return None

        by_yq = {}
        for p_str, r_str in rows:
            try:
                p = json.loads(p_str)
                resp = json.loads(r_str)
            except Exception:
                continue
            if resp.get("status") != "000" or not resp.get("list"):
                continue
            yr = int(p.get("bsns_year", 0))
            q = reprt_to_q.get(p.get("reprt_code", ""))
            fs = p.get("fs_div", "CFS")
            if not yr or not q:
                continue
            parsed = _parse_dart_fnltt_list(resp["list"])
            if (yr, q) not in by_yq or fs == "CFS":
                by_yq[(yr, q)] = parsed

        if not by_yq:
            conn.close()
            return None

        sorted_keys = sorted(by_yq.keys(), key=lambda x: (x[0], q_order[x[1]]))
        df_raw = pd.DataFrame([{"Year": k[0], "Quarter": k[1], **by_yq[k]} for k in sorted_keys])

        base_ni_col = "지배순이익" if (df_raw["지배순이익"] != 0).any() else "당기순이익"
        df_raw["유통주식수"] = np.where(
            df_raw["EPS"] != 0,
            np.abs(df_raw[base_ni_col] / df_raw["EPS"]),
            np.nan,
        )
        df_raw["유통주식수"] = pd.Series(df_raw["유통주식수"]).ffill().bfill().fillna(0.0)
        latest_sh = (
            float(df_raw["유통주식수"].iloc[-1])
            if not df_raw.empty and df_raw["유통주식수"].iloc[-1] > 0
            else 0.0
        )
        df_raw["현재주식수"] = latest_sh if latest_sh > 0 else df_raw["유통주식수"]

        sheet2 = df_raw.copy()
        flow_cols = ["매출액", "영업이익", "당기순이익", "지배순이익"]
        for i in range(1, len(df_raw)):
            if df_raw.loc[i, "Year"] == df_raw.loc[i - 1, "Year"]:
                for col in flow_cols:
                    sheet2.loc[i, col] = df_raw.loc[i, col] - df_raw.loc[i - 1, col]
                q_ni = (
                    sheet2.loc[i, "지배순이익"]
                    if sheet2.loc[i, "지배순이익"] != 0
                    else sheet2.loc[i, "당기순이익"]
                )
                if latest_sh > 0:
                    sheet2.loc[i, "EPS"] = q_ni / latest_sh
            else:
                q_ni = (
                    sheet2.loc[i, "지배순이익"]
                    if sheet2.loc[i, "지배순이익"] != 0
                    else sheet2.loc[i, "당기순이익"]
                )
                if latest_sh > 0:
                    sheet2.loc[i, "EPS"] = q_ni / latest_sh

        sheet2 = sheet2.dropna(subset=["영업이익", "당기순이익", "지배순이익"], how="all").reset_index(drop=True)

        sheet3 = df_raw.copy()
        for i in range(len(df_raw)):
            cy, cq = df_raw.loc[i, "Year"], df_raw.loc[i, "Quarter"]
            if cq != "4Q":
                ly_4q = df_raw[(df_raw["Year"] == cy - 1) & (df_raw["Quarter"] == "4Q")]
                ly_cq = df_raw[(df_raw["Year"] == cy - 1) & (df_raw["Quarter"] == cq)]
                if not ly_4q.empty and not ly_cq.empty:
                    for col in flow_cols:
                        sheet3.loc[i, col] = (
                            df_raw.loc[i, col] + ly_4q.iloc[0][col] - ly_cq.iloc[0][col]
                        )
                else:
                    for col in flow_cols:
                        sheet3.loc[i, col] = np.nan
            ttm_ni = (
                sheet3.loc[i, "지배순이익"]
                if pd.notna(sheet3.loc[i, "지배순이익"]) and sheet3.loc[i, "지배순이익"] != 0
                else sheet3.loc[i, "당기순이익"]
            )
            if pd.notna(ttm_ni) and latest_sh > 0:
                sheet3.loc[i, "EPS"] = ttm_ni / latest_sh
            else:
                sheet3.loc[i, "EPS"] = np.nan

        sheet3 = sheet3.dropna(subset=["영업이익", "당기순이익", "지배순이익"], how="all").reset_index(drop=True)
        eq_col = np.where(sheet3["지배자본"] > 0, sheet3["지배자본"], sheet3["자본총계"])
        ni_col = np.where(sheet3["지배순이익"] != 0, sheet3["지배순이익"], sheet3["당기순이익"])
        sheet3["ROE"] = np.where(eq_col > 0, (ni_col / eq_col) * 100.0, np.nan)
        sheet3["BPS"] = np.where(sheet3["현재주식수"] > 0, eq_col / sheet3["현재주식수"], np.nan)
        sheet3["정상화EPS"] = np.where(
            sheet3["현재주식수"] > 0,
            (sheet3["영업이익"] * 0.79) / sheet3["현재주식수"],
            sheet3["EPS"],
        )

        sheet5 = pd.DataFrame()
        if stock_code:
            c.execute(
                "SELECT response FROM api_cache WHERE endpoint = 'fdr_prices' AND params = ?",
                (f"{str(stock_code).zfill(6)}_10y_weekly",),
            )
            prow = c.fetchone()
            pdf = pd.DataFrame()
            if prow:
                try:
                    plist = json.loads(prow[0])
                    pdf = pd.DataFrame(plist)
                    if not pdf.empty and "date" in pdf.columns and "price" in pdf.columns:
                        pdf["dt"] = pd.to_datetime(pdf["date"])
                except Exception:
                    pdf = pd.DataFrame()
            if pdf.empty:
                wdf = fetch_weekly_candles(str(stock_code).zfill(6), count=600)
                if not wdf.empty:
                    pdf = pd.DataFrame({
                        "date": wdf["date"].dt.strftime("%Y-%m-%d"),
                        "dt": wdf["date"],
                        "price": wdf["close"],
                    })
            if not pdf.empty and not sheet3.empty:
                q_end_map = {"1Q": "03-31", "2Q": "06-30", "3Q": "09-30", "4Q": "12-31"}
                s3_b = sheet3.copy()
                s3_b["rdate"] = pd.to_datetime(
                    s3_b["Year"].astype(str) + "-" + s3_b["Quarter"].map(q_end_map)
                )
                s3_b = s3_b.sort_values("rdate")
                pdf = pdf.sort_values("dt")
                merged = pd.merge_asof(
                    pdf,
                    s3_b[["rdate", "BPS", "정상화EPS", "ROE"]],
                    left_on="dt",
                    right_on="rdate",
                    direction="backward",
                )
                merged["PBR"] = np.where(merged["BPS"] > 0, merged["price"] / merged["BPS"], np.nan)
                merged["PER"] = np.where(
                    merged["정상화EPS"] > 0, merged["price"] / merged["정상화EPS"], np.nan
                )
                sheet5 = merged.dropna(subset=["BPS"]).reset_index(drop=True)

        conn.close()
        return {
            "source": f"{db_path} (corp_code={corp_code})",
            "corp_name": corp_name,
            "sheet2": sheet2,
            "sheet3": sheet3,
            "sheet5": sheet5,
        }
    except Exception:
        return None


def load_dart_timeseries(
    stock_code, corp_name=None, stock_name_hint=None, my_list_root=r"e:\antigravity-work\my-stock\my-list"
):
    """
    Searches my-list/*/Data.xlsx or cache.db for the stock's DART historical time-series.
    Returns dict with 'source', 'corp_name', 'sheet2' (Quarterly), 'sheet3' (TTM), 'sheet5' (Band).
    """
    resolved_name = corp_name
    resolved_corp_code = None
    resolved_stock_code = str(stock_code).zfill(6) if stock_code else None
    db_path = os.path.join(my_list_root, "cache.db")

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            if resolved_stock_code:
                c.execute(
                    "SELECT corp_code, corp_name, stock_code FROM corp_codes WHERE stock_code = ?",
                    (resolved_stock_code,),
                )
                row = c.fetchone()
                if row:
                    resolved_corp_code = row[0]
                    if not resolved_name:
                        resolved_name = row[1]
            if not resolved_corp_code and corp_name:
                c.execute(
                    "SELECT corp_code, corp_name, stock_code FROM corp_codes WHERE corp_name = ?",
                    (corp_name,),
                )
                row = c.fetchone()
                if row:
                    resolved_corp_code = resolved_corp_code or row[0]
                    resolved_name = resolved_name or row[1]
                    if not resolved_stock_code and row[2] and row[2].strip():
                        resolved_stock_code = row[2].strip()
            conn.close()
        except Exception:
            pass

    if not resolved_name and stock_name_hint:
        resolved_name = stock_name_hint

    candidates = []
    for name_try in (corp_name, resolved_name, stock_name_hint):
        if not name_try:
            continue
        cand = os.path.join(my_list_root, name_try, "Data.xlsx")
        if os.path.exists(cand) and (name_try, cand) not in candidates:
            candidates.append((name_try, cand))

    if not candidates and os.path.exists(my_list_root):
        for path in glob.glob(os.path.join(my_list_root, "*", "Data.xlsx")):
            folder_name = os.path.basename(os.path.dirname(path))
            if (resolved_name and resolved_name in folder_name) or (
                stock_name_hint and stock_name_hint in folder_name
            ):
                candidates.append((folder_name, path))

    for cname, xlsx_path in candidates:
        try:
            xl = pd.ExcelFile(xlsx_path)
            s2 = xl.parse("Sheet2(Quarterly)") if "Sheet2(Quarterly)" in xl.sheet_names else pd.DataFrame()
            s3 = xl.parse("Sheet3(TTM)") if "Sheet3(TTM)" in xl.sheet_names else pd.DataFrame()
            s5 = xl.parse("Sheet5(Band)") if "Sheet5(Band)" in xl.sheet_names else pd.DataFrame()
            return {
                "source": xlsx_path,
                "corp_name": cname,
                "sheet2": s2,
                "sheet3": s3,
                "sheet5": s5,
            }
        except Exception as e:
            print(f"[Warning] Failed to read {xlsx_path}: {e}")

    if os.path.exists(db_path) and resolved_corp_code:
        cached_ts = _reconstruct_from_cache_db(
            db_path, resolved_corp_code, resolved_stock_code, resolved_name
        )
        if cached_ts:
            return cached_ts

    return {
        "source": None,
        "corp_name": resolved_name,
        "sheet2": pd.DataFrame(),
        "sheet3": pd.DataFrame(),
        "sheet5": pd.DataFrame(),
    }


def _get_split_adjusted_ttm_eps(row, latest_row):
    """
    Returns TTM EPS from row, automatically correcting for missing XBRL EPS tags (0.0),
    sign inversions from bonus issues, or stock splits (액면분할/무상증자), prioritizing 지배순이익.
    """
    raw_eps = row.get("EPS")
    norm_eps = row.get("정상화EPS")
    ni = (
        row.get("지배순이익")
        if pd.notna(row.get("지배순이익")) and float(row.get("지배순이익")) != 0
        else row.get("당기순이익")
    )
    if pd.isna(ni):
        return None
    ni = float(ni)
    curr_sh = float(latest_row.get("현재주식수", 0) or row.get("현재주식수", 0) or 0)
    ni_per_sh = (ni / curr_sh) if curr_sh > 0 else None

    raw_val = (
        float(raw_eps)
        if pd.notna(raw_eps) and float(raw_eps) != 0.0
        else (float(norm_eps) if pd.notna(norm_eps) else None)
    )
    if ni_per_sh is not None:
        if raw_val is None or (raw_val * ni_per_sh < 0):
            return ni_per_sh
        split_ratio = abs(raw_val / ni_per_sh) if ni_per_sh != 0 else 1.0
        if split_ratio > 1.35 or split_ratio < 0.65:
            return ni_per_sh
    return raw_val if raw_val is not None else ni_per_sh


def _get_split_adjusted_quarterly_eps(row, latest_sh):
    """
    Returns standalone quarterly EPS corrected for stock splits / bonus issues (e.g., KoMiCo 5x split in Q2).
    Prevents fake negative EPS caused by subtracting pre-split Q1 EPS from post-split H1 cumulative EPS.
    """
    raw_eps = row.get("EPS")
    ni = (
        float(row.get("지배순이익"))
        if pd.notna(row.get("지배순이익")) and float(row.get("지배순이익")) != 0.0
        else (float(row.get("당기순이익")) if pd.notna(row.get("당기순이익")) else 0.0)
    )
    sh = latest_sh if latest_sh > 0 else float(row.get("현재주식수", 0) or 0)
    ni_per_sh = (ni / sh) if sh > 0 else None
    raw_val = float(raw_eps) if pd.notna(raw_eps) and float(raw_eps) != 0.0 else None

    if ni_per_sh is not None:
        if raw_val is None or (raw_val * ni_per_sh < 0):
            return ni_per_sh
        ratio = abs(raw_val / ni_per_sh) if ni_per_sh != 0 else 1.0
        if ratio > 1.35 or ratio < 0.65:
            return ni_per_sh
    return raw_val if raw_val is not None else (ni_per_sh or 0.0)


def _detect_scurve_phase2_status(
    stock_code, corp_name=None, scurve_override=None, dart_exposure_override=None, early_ignition=False
):
    """
    Auto-detects S-Curve Phase 2 (10~40% golden sweet-spot & DART standalone theme exposure >= 30%),
    or Early Ignition Gate (--early-ignition).
    Enforces Free Call Option rule: if standalone new-tech revenue is undisclosed or <30% (and not early ignition), S-Curve boost is 0%.
    """
    code_str = str(stock_code).zfill(6) if stock_code else ""
    if scurve_override is False:
        return {
            "is_phase2": False,
            "is_early_ignition": False,
            "theme_name": "S-Curve 할증 미적용 (--no-s-curve)",
            "exposure_pct": 0.0,
            "source_pdf": None,
        }

    if early_ignition:
        exp_val = dart_exposure_override if dart_exposure_override is not None else 15.0
        return {
            "is_phase2": True,
            "is_early_ignition": True,
            "theme_name": "DART 선행 발화(Early Ignition: 수주잔고 YoY +30%↑/자기자본 10%↑ 증설 & 본업 마진 방어)",
            "exposure_pct": exp_val,
            "source_pdf": "DART 단일판매·공급계약 / 신규시설투자 공시 실측",
        }

    if dart_exposure_override is not None and dart_exposure_override < 30.0:
        return {
            "is_phase2": False,
            "is_early_ignition": False,
            "theme_name": f"DART 신기술 단독 익스포저 미달 ({dart_exposure_override:.1f}% < 30% 컷오프 → 무상 콜옵션 0% 처리)",
            "exposure_pct": dart_exposure_override,
            "source_pdf": None,
        }

    if scurve_override is True:
        exp_val = dart_exposure_override if dart_exposure_override is not None else 30.0
        return {
            "is_phase2": True,
            "is_early_ignition": False,
            "theme_name": "사용자/리포트 S-Curve Phase 2 지정 (--s-curve-phase2)",
            "exposure_pct": exp_val,
            "source_pdf": "Global_Trend 80p+ 심층 리포트 검증",
        }

    reg_path = r"e:\antigravity-work\korean cycle\00_megatrend\docs\s_curve_registry.json"
    if os.path.exists(reg_path):
        try:
            with open(reg_path, "r", encoding="utf-8") as f:
                reg = json.load(f)
            for th in reg.get("themes", []):
                stage = th.get("stage", "")
                pen = float(th.get("current_penetration_pct", 0.0))
                is_p2_stage = ("Phase 2" in stage) or (10.0 <= pen <= 40.0)
                vp = th.get("verification_pointer", {})
                src_pdf = f"{vp.get('source_pdf', '')} p.{vp.get('source_page', '')}".strip()

                for ex in th.get("excluded_stocks", []):
                    if ex.get("ticker") == code_str or (corp_name and ex.get("name") == corp_name):
                        return {
                            "is_phase2": False,
                            "is_early_ignition": False,
                            "theme_name": f"{th.get('theme_name')} [🚫 수혜 제외: {ex.get('reason', '익스포저 미달')}]",
                            "exposure_pct": 0.0,
                            "source_pdf": src_pdf,
                        }

                if is_p2_stage:
                    for tp in th.get("top_picks", []):
                        if tp.get("ticker") == code_str or (corp_name and tp.get("name") == corp_name):
                            exp_pct = (
                                dart_exposure_override
                                if dart_exposure_override is not None
                                else float(tp.get("dart_exposure_pct", 0.0) or 0.0)
                            )
                            standalone_ok = tp.get("standalone_disclosed", True)
                            if standalone_ok is False or exp_pct < 30.0:
                                return {
                                    "is_phase2": False,
                                    "is_early_ignition": False,
                                    "theme_name": f"{th.get('theme_name')} [무상 콜옵션: 신기술 단독 매출 미공시/30% 미달({exp_pct:.1f}%) → 대안 A S-Curve 할증 배제]",
                                    "exposure_pct": exp_pct,
                                    "source_pdf": src_pdf,
                                }
                            return {
                                "is_phase2": True,
                                "is_early_ignition": False,
                                "theme_name": f"{th.get('theme_name')} (침투율 {pen:.1f}%, {stage})",
                                "exposure_pct": exp_pct,
                                "source_pdf": src_pdf,
                            }
                    for hg in th.get("hidden_gems", []):
                        if hg.get("ticker") == code_str or (corp_name and hg.get("name") == corp_name):
                            exp_pct = dart_exposure_override if dart_exposure_override is not None else 40.0
                            return {
                                "is_phase2": True,
                                "is_early_ignition": False,
                                "theme_name": f"{th.get('theme_name')} [히든챔피언] (침투율 {pen:.1f}%, {stage})",
                                "exposure_pct": exp_pct,
                                "source_pdf": src_pdf,
                            }
        except Exception:
            pass

    verified_global_trend_p2 = {
        "058470": ("온디바이스 AI & NPU 고집적 R&D 리노핀/소켓 (Phase 2 스위트스팟)", 85.0, "26.08.28_신한_생성형AI와 온디바이스AI.pdf & 26.09.16.IM증권.소나기 피할 반도체.pdf"),
        "166090": ("3D NAND 극저온 식각 & 선단 공정 대구경 Si/SiC 부품 (Phase 2 스위트스팟)", 98.98, "26.09.29.LS증권.AI병목의 전이.pdf p.13"),
        "064760": ("반도체 식각 챔버용 Solid SiC Ring 더블 S-Curve (Phase 2 스위트스팟)", 89.8, "26.09.29.LS증권.AI병목의 전이.pdf p.13"),
        "095340": ("AI 가속기·NPU 대면적 실리콘 러버 테스트 소켓 (Phase 2 스위트스팟)", 80.0, "26.08.28_신한_생성형AI와 온디바이스AI.pdf"),
        "183300": ("선단 파운드리·HBM 초정밀 세정·코팅 (Phase 2 스위트스팟)", 85.0, "26.09.16.IM증권.소나기 피할 반도체.pdf"),
        "093520": ("AI 서버·방산 고성능 비메모리 FPGA 솔루션 (Phase 2 스위트스팟)", 82.0, "26.09.29.LS증권.AI병목의 전이.pdf"),
        "131970": ("차량용·온디바이스 AI SoC 웨이퍼 테스트 (Phase 2 스위트스팟)", 90.0, "26.08.28_신한_생성형AI와 온디바이스AI.pdf"),
        "005930": ("AI HBM3E/HBM4 및 선단 메모리 턴어라운드 (Phase 2 스위트스팟)", 50.0, "26.09.16.IM증권.소나기 피할 반도체.pdf"),
    }
    if code_str in verified_global_trend_p2:
        t_nm, exp_p, s_pdf = verified_global_trend_p2[code_str]
        if dart_exposure_override is not None:
            exp_p = dart_exposure_override
        return {
            "is_phase2": True,
            "is_early_ignition": False,
            "theme_name": t_nm,
            "exposure_pct": exp_p,
            "source_pdf": s_pdf,
        }

    return {
        "is_phase2": False,
        "is_early_ignition": False,
        "theme_name": "해당 없음 (전통 시클리컬 / 성숙기 박스권 BM / 단독 신기술 익스포저 30% 미달 무상 콜옵션)",
        "exposure_pct": dart_exposure_override or 0.0,
        "source_pdf": None,
    }


def compute_step1_5_balance_sheet_sotp(
    curr_price,
    distb_shares,
    bench,
    dart_ts,
    non_op_assets_eok=None,
    net_debt_eok=0.0,
    non_op_haircut=0.30,
):
    """
    [NEW v4.2 #1] Step 1.5: 장부가(Book Value) 구성 해부 및 SOTP(본업 + 지분/비영업자산 - 순부채) 분리 평가 게이트
    - 비영업자산(지분, 투자부동산 등)이 장부가(지배주주지분) 또는 시가총액의 20%를 초과하면
      멀티플 적용 전에 반드시 '본업가치 + 비영업자산가치(안전할인 후) - 순차입금'으로 분해.
    - 동시에 본업 영업이익(Core Operating Profit)과 본업 투하자본수익률(Core ROIC/ROE)이 유지되는지 검증.
    """
    print("\n======================================================================")
    print("🏛️ [Step 1.5 — 장부가 해부 & SOTP 20% 게이트] 비영업자산 분리 & 본업 이익 유지력 진단")
    print("======================================================================")

    s3 = dart_ts.get("sheet3", pd.DataFrame())
    official_bps = bench.get("bps") or 0.0
    official_eps = bench.get("eps") or 0.0

    latest_sh = float(distb_shares or 0)
    if latest_sh <= 0 and not s3.empty and "현재주식수" in s3.columns:
        latest_sh = float(s3["현재주식수"].iloc[-1] or 0)

    mcap_eok = (curr_price * latest_sh / 100000000.0) if (curr_price > 0 and latest_sh > 0) else 0.0
    book_eq_eok = (official_bps * latest_sh / 100000000.0) if (official_bps > 0 and latest_sh > 0) else 0.0

    # Auto-detect non-operating assets from DART sheet3 if not passed via CLI
    detected_non_op_eok = 0.0
    op_inc_now_eok = 0.0
    op_inc_min_3y_eok = 0.0
    if not s3.empty:
        if "비영업자산" in s3.columns and pd.notna(s3["비영업자산"].iloc[-1]):
            detected_non_op_eok = float(s3["비영업자산"].iloc[-1]) / 100000000.0
        if book_eq_eok <= 0:
            eq_col = "지배자본" if ("지배자본" in s3.columns and s3["지배자본"].iloc[-1] > 0) else "자본총계"
            if eq_col in s3.columns and pd.notna(s3[eq_col].iloc[-1]):
                book_eq_eok = float(s3[eq_col].iloc[-1]) / 100000000.0
        if "영업이익" in s3.columns:
            valid_op = s3["영업이익"].dropna()
            if not valid_op.empty:
                op_inc_now_eok = float(valid_op.iloc[-1]) / 100000000.0
                op_inc_min_3y_eok = float(valid_op.tail(12).min()) / 100000000.0

    used_non_op_eok = float(non_op_assets_eok) if non_op_assets_eok is not None else detected_non_op_eok
    ratio_vs_book_pct = (used_non_op_eok / book_eq_eok * 100.0) if book_eq_eok > 0 else 0.0
    ratio_vs_mcap_pct = (used_non_op_eok / mcap_eok * 100.0) if mcap_eok > 0 else 0.0
    max_non_op_ratio = max(ratio_vs_book_pct, ratio_vs_mcap_pct)
    sotp_triggered = max_non_op_ratio >= 20.0

    recognized_non_op_eok = round(used_non_op_eok * (1.0 - non_op_haircut), 2)
    core_book_eq_eok = max(0.0, book_eq_eok - used_non_op_eok)
    core_bps = round(official_bps * (core_book_eq_eok / book_eq_eok)) if book_eq_eok > 0 else round(official_bps)
    nopat_eok = round(op_inc_now_eok * 0.78, 2)
    core_roic_pct = round((nopat_eok / core_book_eq_eok) * 100.0, 2) if core_book_eq_eok > 0 else None
    core_profit_sustainable = (op_inc_now_eok > 0) and (op_inc_min_3y_eok >= 0) and (core_roic_pct is None or core_roic_pct >= 8.0)

    ev_eok = round(mcap_eok + net_debt_eok, 2) if mcap_eok > 0 else 0.0
    implied_core_op_ev_eok = round(ev_eok - recognized_non_op_eok, 2) if mcap_eok > 0 else 0.0

    print(f"    - 지배주주 장부가(Book Equity): {book_eq_eok:,.1f}억 원 | 유통 시가총액: {mcap_eok:,.1f}억 원")
    print(
        f"    - 비영업자산(지분·투자부동산 등): {used_non_op_eok:,.1f}억 원 "
        f"(장부 대비 {ratio_vs_book_pct:.1f}% / 시총 대비 {ratio_vs_mcap_pct:.1f}%)"
    )
    if sotp_triggered:
        print(
            f"    - 🚨 [SOTP 20% 게이트 발동]: 비영업자산 비중({max_non_op_ratio:.1f}% ≥ 20%) 초과!"
        )
        print(
            f"      ➔ 단순 연결 PER/PBR 일괄 적용 금지! 반드시 [① 본업 가치 + ② 비영업자산 안전가치({recognized_non_op_eok:,.1f}억, 할인율 {non_op_haircut*100:.0f}%) - ③ 순차입금({net_debt_eok:+,.1f}억)] SOTP로 분해 평가하십시오."
        )
        print(
            f"      ➔ 비영업자산 제거 후 본업 BPS: {core_bps:,.0f}원 (명목 BPS {official_bps:,.0f}원) | 시장 내재 본업 EV: {implied_core_op_ev_eok:,.1f}억 원"
        )
    else:
        print(
            f"    - 🟢 [SOTP 게이트 통과]: 비영업자산 비중({max_non_op_ratio:.1f}% < 20%)이 낮아 본업 영업자산 중심 장부로 확인됨."
        )

    roic_str = f"{core_roic_pct:.2f}%" if core_roic_pct is not None else "산출 대기(영업이익 연동)"
    sust_badge = "✅ [본업 이익 유지/견고]" if core_profit_sustainable else "⚠️ [본업 수익성 변동/취약 — 지분법·일회성 착시 주의]"
    print(
        f"    - 🔍 [본업 이익 유지력 판정]: TTM 영업이익 {op_inc_now_eok:,.1f}억 (NOPAT {nopat_eok:,.1f}억) | "
        f"3년 최저 영업이익 {op_inc_min_3y_eok:,.1f}억 | 본업 ROIC(비영업제외) {roic_str} ➔ {sust_badge}"
    )

    return {
        "sotp_triggered": sotp_triggered,
        "book_eq_eok": round(book_eq_eok, 2),
        "mcap_eok": round(mcap_eok, 2),
        "non_op_assets_eok": round(used_non_op_eok, 2),
        "recognized_non_op_eok": recognized_non_op_eok,
        "non_op_haircut": non_op_haircut,
        "non_op_ratio_book_pct": round(ratio_vs_book_pct, 2),
        "non_op_ratio_mcap_pct": round(ratio_vs_mcap_pct, 2),
        "non_op_max_ratio_pct": round(max_non_op_ratio, 2),
        "core_bps": core_bps,
        "nopat_eok": nopat_eok,
        "core_roic_pct": core_roic_pct,
        "core_profit_sustainable": core_profit_sustainable,
        "implied_core_op_ev_eok": implied_core_op_ev_eok,
    }


def compute_step2_0_regime_change_check(dart_ts, sotp_res, rerating_date=None):
    """
    [NEW v4.2 #2] Step 2.0: PER·PBR 밴드 적용 전 '4대 레짐 변화 체크(설비·자본구조·비영업자산·사업구성)' 고정 게이트
    과거 3~10년 밴드를 기계적으로 대입하기 전에 기업 체질이 구조적으로 바뀌었는지 4대 축을 정량 점검합니다.
    """
    print("\n======================================================================")
    print("🔄 [Step 2.0 — 4대 레짐 변화 고정 체크] 멀티플 밴드 적용 전 체질 변화 검증")
    print("======================================================================")

    s3 = dart_ts.get("sheet3", pd.DataFrame())
    flags = []

    # 1. 설비(CapEx / 유형자산) 레짐
    ppe_chg_pct = None
    if not s3.empty and "유형자산" in s3.columns:
        vppe = s3["유형자산"].replace(0, np.nan).dropna()
        if len(vppe) >= 9:
            ppe_now = float(vppe.iloc[-1])
            ppe_2y = float(vppe.iloc[-9])
            if ppe_2y > 0:
                ppe_chg_pct = ((ppe_now - ppe_2y) / ppe_2y) * 100.0
                if ppe_chg_pct >= 30.0:
                    flags.append(f"① 설비레짐(2년 유형자산 +{ppe_chg_pct:.1f}% 대규모 증설 — 감가상각 선반영 후 영업레버리지 구간 여부 확인)")
                elif ppe_chg_pct <= -15.0:
                    flags.append(f"① 설비레짐(2년 유형자산 {ppe_chg_pct:.1f}% 축소 — 설비 노후화/매각 여부 확인)")

    # 2. 자본구조(부채 / 주식수 희석) 레짐
    debt_chg_pct = None
    if not s3.empty and "부채총계" in s3.columns and "자본총계" in s3.columns:
        s3_clean = s3.dropna(subset=["자본총계"])
        if len(s3_clean) >= 9:
            de_now = (float(s3_clean["부채총계"].iloc[-1]) / max(1.0, float(s3_clean["자본총계"].iloc[-1]))) * 100.0
            de_2y = (float(s3_clean["부채총계"].iloc[-9]) / max(1.0, float(s3_clean["자본총계"].iloc[-9]))) * 100.0
            debt_chg_pct = de_now - de_2y
            if abs(debt_chg_pct) >= 25.0:
                flags.append(f"② 자본구조레짐(부채비율 {de_2y:.1f}% → {de_now:.1f}%, {debt_chg_pct:+.1f}%p 변동)")

    # 3. 비영업자산 레짐
    if sotp_res.get("sotp_triggered"):
        flags.append(
            f"③ 비영업자산레짐(비영업자산 비중 {sotp_res['non_op_max_ratio_pct']:.1f}% ≥ 20% — 명목 PBR 저평가 착시 주의)"
        )

    # 4. 사업구성(Business Mix / OPM & ROE 레벨) 레짐
    opm_shift_pctp = None
    if not s3.empty and "영업이익" in s3.columns and "매출액" in s3.columns:
        s3_m = s3[(s3["매출액"] > 0)].copy()
        if len(s3_m) >= 9:
            opm_now = (float(s3_m["영업이익"].iloc[-1]) / float(s3_m["매출액"].iloc[-1])) * 100.0
            opm_hist = float(((s3_m["영업이익"].iloc[:-4] / s3_m["매출액"].iloc[:-4]) * 100.0).median())
            opm_shift_pctp = opm_now - opm_hist
            if abs(opm_shift_pctp) >= 4.0:
                dir_lbl = "고마진 신사업 믹스 개선" if opm_shift_pctp > 0 else "본업 마진 훼손/경쟁 심화"
                flags.append(f"④ 사업구성레짐(OPM 과거중위 {opm_hist:.1f}% → 현재 {opm_now:.1f}%, {opm_shift_pctp:+.1f}%p [{dir_lbl}])")

    if rerating_date:
        flags.append(f"⑤ 지정 리레이팅 분기점({rerating_date}) 이후 Post-Rerating 멀티플 우선 적용")

    regime_changed = len(flags) > 0
    print(f"    - [1] 설비(CapEx·유형자산) 변화: {f'2년 {ppe_chg_pct:+.1f}%' if ppe_chg_pct is not None else 'DART 주석 설비증설/감가상각 대조 필수'}")
    print(f"    - [2] 자본구조(순부채·CB/유증) 변화: {f'부채비율 2년 {debt_chg_pct:+.1f}%p' if debt_chg_pct is not None else '순차입금·오버행(CB/BW) 희석 대조 필수'}")
    print(f"    - [3] 비영업자산(지분·부동산) 비중: 최대 {sotp_res.get('non_op_max_ratio_pct', 0.0):.1f}% ({'SOTP 분리 필수' if sotp_res.get('sotp_triggered') else '본업 중심'})")
    print(f"    - [4] 사업구성(OPM·매출믹스) 변화: {f'과거 중위 대비 OPM {opm_shift_pctp:+.1f}%p' if opm_shift_pctp is not None else 'DART 부문별 매출 믹스 대조 필수'}")

    if regime_changed:
        print("    - ⚡ [레짐 변화 감지 요약]: " + " / ".join(flags))
    else:
        print("    - 🟢 [레짐 안정]: 4대 레짐 급변 신호 없음 (과거 3~10년 밸류에이션 밴드 연속성 유효)")

    print("    - 📌 [저평가/고평가 판단 시 의무 추가 검토 지침]:")
    print("      • 저평가(밴드 하단) 판정 시: ① 노후 설비 CapEx 부담, ② 차입금/CB 희석, ③ 비영업자산 착시, ④ 레거시 사양화에 의한 '정당한 디레이팅(가치함정)'인지 반드시 반증 검토할 것.")
    print("      • 고평가(밴드 상단) 판정 시: ① 신규 증설 가동 효과, ② 순현금 전환, ③ 고마진 신사업 비중 확대(≥30%)에 의한 '구조적 리레이팅'인지 추가 검토할 것.")

    return {
        "regime_changed": regime_changed,
        "ppe_chg_2y_pct": round(ppe_chg_pct, 2) if ppe_chg_pct is not None else None,
        "debt_ratio_chg_2y_pctp": round(debt_chg_pct, 2) if debt_chg_pct is not None else None,
        "opm_shift_pctp": round(opm_shift_pctp, 2) if opm_shift_pctp is not None else None,
        "flags": flags,
    }


def compute_phase1_section8_metrics_ranking(stock_code, bench, df_week, dart_ts, curr_price):
    """
    Computes Phase 1 Section 8 4-Metric (PBR, PER, DivYield, EV/EBITDA) 10Y Band, R², and Directional Match %,
    filtering out spurious correlations (directional match < 50% or low-payout dividend illusions).
    """
    print("\n======================================================================")
    print("📐 [Phase 1 - Section 8] 최적 밸류에이션 지표 10년 백테스트 & 착시 필터링")
    print("======================================================================")

    s3 = dart_ts.get("sheet3", pd.DataFrame())
    s5 = dart_ts.get("sheet5", pd.DataFrame())
    official_bps = bench.get("bps") or 0.0
    official_eps = bench.get("eps") or 0.0
    official_pbr = (curr_price / official_bps) if official_bps > 0 else (bench.get("pbr") or 0.0)
    official_per = (curr_price / official_eps) if official_eps > 0 else (bench.get("per") or 0.0)
    official_div = bench.get("div_yield") or 0.0

    pbr_min, pbr_max, pbr_r2, pbr_dir = 0.5, 2.5, 0.65, 65.0
    per_min, per_max, per_r2, per_dir = 6.0, 25.0, 0.60, 62.0

    if not s5.empty and "price" in s5.columns:
        s5c = s5.dropna(subset=["price"]).copy()
        bps_scale = 1.0
        if official_bps > 0 and "BPS" in s5c.columns and s5c["BPS"].dropna().shape[0] > 0:
            last_bps = float(s5c["BPS"].dropna().iloc[-1])
            if last_bps > 0 and abs(official_bps / last_bps - 1.0) > 0.05:
                bps_scale = official_bps / last_bps
        if "PBR" in s5c.columns:
            vpbr = (s5c["PBR"].replace([np.inf, -np.inf], np.nan).dropna()) / bps_scale
            vpbr = vpbr[vpbr > 0]
            if not vpbr.empty:
                pbr_min, pbr_max = float(vpbr.min()), float(vpbr.max())
        if "PER" in s5c.columns:
            vper = s5c["PER"].replace([np.inf, -np.inf], np.nan).dropna()
            vper = vper[(vper > 0) & (vper < 150)]
            if not vper.empty:
                per_min, per_max = float(vper.min()), float(vper.max())

    if not s3.empty and not df_week.empty:
        try:
            q_end_map = {"1Q": "03-31", "2Q": "06-30", "3Q": "09-30", "4Q": "12-31"}
            s3c = s3.copy()
            s3c["rdate"] = pd.to_datetime(
                s3c["Year"].astype(str) + "-" + s3c["Quarter"].map(q_end_map)
            )
            w_sorted = df_week[["date", "close"]].sort_values("date").rename(columns={"date": "rdate"})
            m_q = pd.merge_asof(
                s3c.sort_values("rdate"), w_sorted, on="rdate", direction="backward"
            ).dropna(subset=["close"])
            if len(m_q) >= 5:
                latest_r = m_q.iloc[-1]
                m_q["adj_eps"] = [
                    _get_split_adjusted_ttm_eps(r, latest_r) or 0.0 for _, r in m_q.iterrows()
                ]
                if "BPS" in m_q.columns and m_q["BPS"].std() > 0:
                    corr_b = np.corrcoef(m_q["BPS"].fillna(0), m_q["close"])[0, 1]
                    if pd.notna(corr_b):
                        pbr_r2 = round(float(corr_b ** 2), 2)
                    b_diff = np.sign(m_q["BPS"].diff().dropna())
                    p_diff = np.sign(m_q["close"].diff().dropna())
                    if len(b_diff) > 0:
                        pbr_dir = round(float((b_diff == p_diff).mean() * 100.0), 1)
                if m_q["adj_eps"].std() > 0:
                    corr_e = np.corrcoef(m_q["adj_eps"], m_q["close"])[0, 1]
                    if pd.notna(corr_e):
                        per_r2 = round(float(corr_e ** 2), 2)
                    e_diff = np.sign(m_q["adj_eps"].diff().dropna())
                    p_diff = np.sign(m_q["close"].diff().dropna())
                    if len(e_diff) > 0:
                        per_dir = round(float((e_diff == p_diff).mean() * 100.0), 1)
        except Exception:
            pass

    # Spurious correlation filter & Primary metric selection
    pbr_valid = pbr_dir >= 50.0
    per_valid = per_dir >= 50.0
    if pbr_valid and (not per_valid or pbr_r2 >= per_r2):
        primary_metric = "PBR"
    elif per_valid:
        primary_metric = "PER"
    else:
        primary_metric = "PBR"

    print(
        f"    - [PBR (유통 BPS 기준)]: 10년 밴드 {pbr_min:.2f}배 ~ {pbr_max:.2f}배 | 현재 {official_pbr:.2f}배 | 설명력 R²={pbr_r2:.2f} | 방향일치율 {pbr_dir:.1f}%"
    )
    print(
        f"    - [PER (수정 EPS 기준)]: 10년 밴드 {per_min:.2f}배 ~ {per_max:.2f}배 | 현재 {official_per:.2f}배 | 설명력 R²={per_r2:.2f} | 방향일치율 {per_dir:.1f}%"
    )
    print(f"    - [코드 자동 선정 1순위 주 지표]: 👑 {primary_metric} (Phase 3 시나리오 표 강제 상속)")
    print("    - [지표 선정 및 상속 가이드]:")
    print(
        "      • 방향일치율 < 50%이거나 배당성향 15% 미만 종목의 배당수익률 지표는 '시계열 추세 착시(Spurious)'로 1순위에서 자동 배제하십시오."
    )
    print(
        "      • Phase 1에서 채택한 1순위 지표는 Phase 3 시나리오 표에서 반드시 주(Primary) 산식으로 적용하고 내재 PER/PBR을 병기하십시오."
    )
    return {
        "primary_metric": primary_metric,
        "pbr_min": round(pbr_min, 2),
        "pbr_max": round(pbr_max, 2),
        "pbr_r2": pbr_r2,
        "pbr_dir": pbr_dir,
        "per_min": round(per_min, 2),
        "per_max": round(per_max, 2),
        "per_r2": per_r2,
        "per_dir": per_dir,
        "official_pbr": round(official_pbr, 2),
        "official_per": round(official_per, 2),
        "official_div": round(official_div, 2),
    }


def compute_section2_value_trap(stock_code, bench, df_week, dart_ts, curr_price):
    """
    (A) DART 시계열 연동 및 Section 2 가치함정 이원화 자동 판정 + 직전 분기(QoQ) 실적 및 반증 임계치(Kill-Switch) 산출
    """
    print("\n======================================================================")
    print("🚨 [Phase 3 - Section 2] DART 시계열 연동 · 가치함정(유형 A/B) · QoQ 반증 임계치 자동 판정")
    print("======================================================================")

    s2 = dart_ts.get("sheet2", pd.DataFrame())
    s3 = dart_ts.get("sheet3", pd.DataFrame())
    s5 = dart_ts.get("sheet5", pd.DataFrame())
    src = dart_ts.get("source")
    official_bps = bench.get("bps")
    official_eps = bench.get("eps")

    eps_2y_chg = None
    bps_2y_chg = None
    curr_roe = None
    mid_cycle_roe = None
    mid_cycle_eps = None
    controlling_ratio_pct = 100.0
    qoq_eps_positive = None
    qoq_eps_detail = "N/A"
    qoq_rev_pct = None
    qoq_op_pct = None
    qoq_summary_str = "N/A"
    kill_switch_guide = "다음 분기 영업이익 QoQ -15% 이상 역성장 또는 지배주주 EPS 적자 전환 시 가설 [약화] 강등"
    op_yoy_q = None
    op_yoy_ttm = None
    op_yoy_used = None
    op_yoy_detail = "N/A"

    pbr_cycle_max = pbr_3y_max = pbr_3y_mean = pbr_3y_p20 = pbr_3y_p65 = pbr_3y_p90 = None
    per_3y_max = per_3y_mean = per_3y_p20 = per_3y_p65 = per_3y_p90 = None

    curr_eps_used = official_eps
    curr_bps_used = official_bps
    latest_sh = 0.0

    if not s3.empty:
        s3_valid = s3.dropna(subset=["BPS"], how="all").reset_index(drop=True)
        if len(s3_valid) >= 1:
            latest_row = s3_valid.iloc[-1]
            latest_sh = float(latest_row.get("현재주식수", 0) or 0)
            curr_eps_dart = _get_split_adjusted_ttm_eps(latest_row, latest_row)
            curr_bps_dart = latest_row.get("BPS")
            if pd.notna(latest_row.get("ROE")):
                curr_roe = float(latest_row["ROE"])
            if "ROE" in s3_valid.columns:
                vroe = s3_valid["ROE"].dropna()
                if not vroe.empty:
                    mid_cycle_roe = float(vroe.tail(20).median())
            if curr_eps_dart is not None and curr_eps_dart != 0:
                curr_eps_used = float(curr_eps_dart)
            if pd.notna(curr_bps_dart) and curr_bps_dart > 0:
                curr_bps_used = float(curr_bps_dart)

            # Mid-cycle EPS (5Y median split-adjusted TTM EPS)
            adj_eps_list = [
                _get_split_adjusted_ttm_eps(r, latest_row)
                for _, r in s3_valid.tail(20).iterrows()
            ]
            adj_eps_list = [x for x in adj_eps_list if x is not None and x > 0]
            if adj_eps_list:
                mid_cycle_eps = float(np.median(adj_eps_list))

            tot_ni = latest_row.get("당기순이익")
            ctrl_ni = latest_row.get("지배순이익")
            if pd.notna(tot_ni) and pd.notna(ctrl_ni) and float(tot_ni) > 0 and float(ctrl_ni) > 0:
                controlling_ratio_pct = min(100.0, round((float(ctrl_ni) / float(tot_ni)) * 100.0, 2))

            idx_2y = max(0, len(s3_valid) - 9)
            row_2y = s3_valid.iloc[idx_2y]
            eps_2y_ago = _get_split_adjusted_ttm_eps(row_2y, latest_row)
            bps_2y_ago = row_2y.get("BPS")

            if curr_eps_dart is not None and eps_2y_ago is not None and abs(eps_2y_ago) > 0:
                eps_2y_chg = ((float(curr_eps_dart) - float(eps_2y_ago)) / abs(float(eps_2y_ago))) * 100.0
            if pd.notna(curr_bps_dart) and pd.notna(bps_2y_ago) and abs(bps_2y_ago) > 0:
                bps_2y_chg = ((float(curr_bps_dart) - float(bps_2y_ago)) / abs(float(bps_2y_ago))) * 100.0

            if len(s3_valid) >= 5 and "영업이익" in s3_valid.columns:
                op_ttm_now = latest_row.get("영업이익")
                op_ttm_1y = s3_valid.iloc[-5].get("영업이익")
                if pd.notna(op_ttm_now) and pd.notna(op_ttm_1y) and abs(op_ttm_1y) > 0:
                    op_yoy_ttm = ((float(op_ttm_now) - float(op_ttm_1y)) / abs(float(op_ttm_1y))) * 100.0

    # Standalone Quarterly QoQ Analysis (EPS, Operating Profit, Revenue) + Kill-Switch Thresholds
    if not s2.empty:
        if "EPS" in s2.columns or "지배순이익" in s2.columns:
            s2_v = s2.dropna(subset=["영업이익"], how="all").reset_index(drop=True)
            if len(s2_v) >= 2:
                r_now = s2_v.iloc[-1]
                r_prev = s2_v.iloc[-2]
                sh_ref = latest_sh if latest_sh > 0 else float(r_now.get("현재주식수", 0) or 0)
                q_now = _get_split_adjusted_quarterly_eps(r_now, sh_ref)
                q_prev = _get_split_adjusted_quarterly_eps(r_prev, sh_ref)
                q_label_now = f"{int(r_now['Year'])}.{r_now['Quarter']}"
                q_label_prev = f"{int(r_prev['Year'])}.{r_prev['Quarter']}"
                qoq_eps_positive = (q_now > 0 and q_now >= q_prev * 0.95) or (q_now > q_prev)
                status_tag = (
                    "QoQ 반등/흑자유지 ✅"
                    if (q_now > 0 and q_now >= q_prev)
                    else ("흑자 지속(소폭조정) 🟢" if q_now > 0 else "적자 전환/지속 🚨")
                )
                qoq_eps_detail = f"[액분·무증 소급보정 지배주주 EPS] {q_label_prev} {q_prev:,.1f}원 → {q_label_now} {q_now:,.1f}원 ({status_tag})"

                # QoQ Operating Income & Revenue
                op_now_q = float(r_now.get("영업이익", 0) or 0) / 100000000.0
                op_prev_q = float(r_prev.get("영업이익", 0) or 0) / 100000000.0
                if abs(op_prev_q) > 0:
                    qoq_op_pct = round(((op_now_q - op_prev_q) / abs(op_prev_q)) * 100.0, 1)
                rev_now_q = float(r_now.get("매출액", 0) or 0) / 100000000.0
                rev_prev_q = float(r_prev.get("매출액", 0) or 0) / 100000000.0
                if abs(rev_prev_q) > 0:
                    qoq_rev_pct = round(((rev_now_q - rev_prev_q) / abs(rev_prev_q)) * 100.0, 1)

                qoq_parts = []
                if qoq_rev_pct is not None:
                    qoq_parts.append(f"매출액 {rev_prev_q:,.1f}억→{rev_now_q:,.1f}억(QoQ {qoq_rev_pct:+.1f}%)")
                if qoq_op_pct is not None:
                    qoq_parts.append(f"영업이익 {op_prev_q:,.1f}억→{op_now_q:,.1f}억(QoQ {qoq_op_pct:+.1f}%)")
                qoq_parts.append(f"수정EPS {q_prev:,.0f}원→{q_now:,.0f}원")
                qoq_summary_str = f"[{q_label_prev} → {q_label_now}] " + " | ".join(qoq_parts)

                # Pre-committed Falsification Kill-Switch Thresholds (85% of current OP or 0 EPS floor)
                op_kill_floor = round(op_now_q * 0.85, 1) if op_now_q > 0 else 0.0
                eps_kill_floor = round(q_now * 0.80, 0) if q_now > 0 else 0.0
                kill_switch_guide = (
                    f"차기 분기 영업이익 < {op_kill_floor:,.1f}억 원(직전분기 대비 -15% 하회) "
                    f"또는 분기 지배주주 EPS < {eps_kill_floor:,.0f}원 기록 시 핵심 가설 즉시 [약화] 강등"
                )

        if "영업이익" in s2.columns:
            s2_op = s2.dropna(subset=["영업이익"]).reset_index(drop=True)
            if len(s2_op) >= 5:
                r_latest = s2_op.iloc[-1]
                ly_yr = int(r_latest["Year"]) - 1
                ly_q = r_latest["Quarter"]
                r_yoy_match = s2_op[(s2_op["Year"] == ly_yr) & (s2_op["Quarter"] == ly_q)]
                if not r_yoy_match.empty:
                    op_q_now = float(r_latest["영업이익"])
                    op_q_prev = float(r_yoy_match.iloc[-1]["영업이익"])
                    if op_q_prev > 0:
                        op_yoy_q = ((op_q_now - op_q_prev) / op_q_prev) * 100.0
                    elif op_q_prev <= 0 and op_q_now > 0:
                        op_yoy_q = 100.0
                    elif op_q_prev < 0 and op_q_now <= op_q_prev:
                        op_yoy_q = -50.0
                    q_lbl = f"{int(r_latest['Year'])}.{ly_q}"
                    prev_lbl = f"{ly_yr}.{ly_q}"
                    ttm_str = f" | TTM 영업이익 YoY: {op_yoy_ttm:+.1f}%" if op_yoy_ttm is not None else ""
                    op_yoy_detail = (
                        f"분기({prev_lbl}→{q_lbl}) {op_yoy_q:+.1f}% ({op_q_prev/1e8:,.1f}억 → {op_q_now/1e8:,.1f}억){ttm_str}"
                        if op_yoy_q is not None
                        else "N/A"
                    )

    op_yoy_used = op_yoy_q if op_yoy_q is not None else op_yoy_ttm
    if op_yoy_detail == "N/A" and op_yoy_ttm is not None:
        op_yoy_detail = f"TTM 영업이익 YoY {op_yoy_ttm:+.1f}%"

    if not s5.empty:
        s5_copy = s5.copy()
        if "date" in s5_copy.columns:
            s5_copy["dt"] = pd.to_datetime(s5_copy["date"].astype(str), errors="coerce")
        else:
            s5_copy["dt"] = pd.NaT
        if s5_copy["dt"].notna().any():
            max_dt = s5_copy["dt"].max()
            cutoff_3y = max_dt - pd.DateOffset(years=3)
            s5_3y = s5_copy[s5_copy["dt"] >= cutoff_3y].copy()
        else:
            s5_3y = s5_copy.tail(min(len(s5_copy), 156)).copy()

        bps_scale = 1.0
        if (
            official_bps
            and "BPS" in s5_3y.columns
            and pd.notna(s5_3y["BPS"].iloc[-1])
            and s5_3y["BPS"].iloc[-1] > 0
        ):
            ratio = official_bps / float(s5_3y["BPS"].iloc[-1])
            if abs(ratio - 1.0) > 0.05:
                bps_scale = ratio
                curr_bps_used = official_bps

        valid_pbr_all = (
            s5_copy["PBR"].dropna() / bps_scale
            if "PBR" in s5_copy.columns
            else pd.Series(dtype=float)
        )
        valid_pbr_all = valid_pbr_all[valid_pbr_all > 0]
        if not valid_pbr_all.empty:
            pbr_cycle_max = float(valid_pbr_all.max())

        valid_pbr = (
            s5_3y["PBR"].dropna() / bps_scale if "PBR" in s5_3y.columns else pd.Series(dtype=float)
        )
        valid_pbr = valid_pbr[valid_pbr > 0]
        if not valid_pbr.empty:
            pbr_3y_max = float(valid_pbr.max())
            pbr_3y_mean = float(valid_pbr.mean())
            pbr_3y_p20 = float(valid_pbr.quantile(0.20))
            pbr_3y_p65 = float(valid_pbr.quantile(0.65))
            pbr_3y_p90 = float(valid_pbr.quantile(0.90))

        valid_per = s5_3y["PER"].dropna() if "PER" in s5_3y.columns else pd.Series(dtype=float)
        valid_per = valid_per[(valid_per > 0) & (valid_per < 200)]
        if not valid_per.empty:
            per_3y_max = float(valid_per.max())
            per_3y_mean = float(valid_per.mean())
            per_3y_p20 = float(valid_per.quantile(0.20))
            per_3y_p65 = float(valid_per.quantile(0.65))
            per_3y_p90 = float(valid_per.quantile(0.90))

    if pbr_cycle_max is None and not df_week.empty and official_bps and official_bps > 0:
        pbr_cycle_max = float(df_week["close"].max() / official_bps)

    if pbr_3y_max is None and not df_week.empty and official_bps and official_bps > 0:
        w3y = df_week.tail(156)
        pbr_series = w3y["close"] / official_bps
        pbr_3y_max = float(pbr_series.max())
        pbr_3y_mean = float(pbr_series.mean())
        pbr_3y_p20 = float(pbr_series.quantile(0.20))
        pbr_3y_p65 = float(pbr_series.quantile(0.65))
        pbr_3y_p90 = float(pbr_series.quantile(0.90))

    if per_3y_max is None and not df_week.empty and official_eps and official_eps > 0:
        w3y = df_week.tail(156)
        per_series = w3y["close"] / official_eps
        per_3y_max = float(per_series.max())
        per_3y_mean = float(per_series.mean())
        per_3y_p20 = float(per_series.quantile(0.20))
        per_3y_p65 = float(per_series.quantile(0.65))
        per_3y_p90 = float(per_series.quantile(0.90))

    if curr_roe is None and official_eps is not None and official_bps and official_bps > 0:
        curr_roe = (official_eps / official_bps) * 100.0

    w_ma20 = (
        float(df_week["close"].rolling(20).mean().iloc[-1])
        if len(df_week) >= 20
        else curr_price
    )
    w_ma60 = (
        float(df_week["close"].rolling(60).mean().iloc[-1])
        if len(df_week) >= 60
        else curr_price
    )
    is_weekly_aligned = w_ma20 >= w_ma60
    align_str = (
        f"정배열 (20주선 {w_ma20:,.0f}원 ≥ 60주선 {w_ma60:,.0f}원) 📈"
        if is_weekly_aligned
        else f"역배열 (20주선 {w_ma20:,.0f}원 < 60주선 {w_ma60:,.0f}원) 📉"
    )

    print(f"    - 데이터 소스: {src if src else '네이버 공식 벤치마크 + 주봉 시계열 폴백'}")
    print(f"    - 지배주주 순이익 귀속률:   {controlling_ratio_pct:.1f}% (연결 순이익 대비 지배주주 귀속 비율 — 시나리오 EPS 산출 시 필수 곱셈!)")
    print(f"    - 최근 2년 누적 EPS 증감률: {f'{eps_2y_chg:+.2f}%' if eps_2y_chg is not None else 'N/A (단기 시계열 폴백)'}")
    print(f"    - 최근 2년 누적 BPS 증감률: {f'{bps_2y_chg:+.2f}%' if bps_2y_chg is not None else 'N/A (단기 시계열 폴백)'}")
    print(f"    - 최근 영업이익 YoY 추이:   {op_yoy_detail}")
    print(f"    - 최근 분기 EPS QoQ 추이:  {qoq_eps_detail}")
    print(f"    - 📊 [직전 분기 대비(QoQ) 종합]: {qoq_summary_str}")
    print(f"    - 🚨 [가설 약화/반증 기준(Kill-Switch)]: {kill_switch_guide}")
    print(f"    - 현재 ROE (지배주주 기준): {f'{curr_roe:.2f}%' if curr_roe is not None else 'N/A'}" + (f" (5년 중위 Mid-Cycle ROE: {mid_cycle_roe:.2f}%)" if mid_cycle_roe is not None else ""))

    if pbr_3y_max is not None:
        cyc_str = f" (10년 풀사이클 최고 {pbr_cycle_max:.2f}배)" if pbr_cycle_max is not None else ""
        print(
            f"    - 과거 3년 PBR 밴드: 최고 {pbr_3y_max:.2f}배{cyc_str} | 평균 {pbr_3y_mean:.2f}배 | 하위20% {pbr_3y_p20:.2f}배 | 상위65% {pbr_3y_p65:.2f}배 | 상위90% {pbr_3y_p90:.2f}배"
        )
    if per_3y_max is not None:
        print(
            f"    - 과거 3년 PER 밴드: 최고 {per_3y_max:.2f}배 | 평균 {per_3y_mean:.2f}배 | 하위20% {per_3y_p20:.2f}배 | 상위65% {per_3y_p65:.2f}배 | 상위90% {per_3y_p90:.2f}배"
        )
    print(f"    - 주봉 20MA vs 60MA 배열:  {align_str}")

    has_bubble_or_stagnant_bps = (pbr_3y_max is not None and pbr_3y_max >= 2.8) or (
        pbr_cycle_max is not None
        and pbr_cycle_max >= 2.8
        and (bps_2y_chg is None or bps_2y_chg < 15.0)
    )
    is_type_b = (
        has_bubble_or_stagnant_bps
        and (eps_2y_chg is not None and eps_2y_chg <= -30.0)
        and (curr_roe is not None and curr_roe < 8.5)
    )

    if is_type_b:
        verdict = "🔴 [유형 B: 구조적 버블 붕괴 가치함정 (Structural Value Trap)]"
        peak_pbr_desc = (
            f"과거 3년 최고 PBR({pbr_3y_max:.2f}배 ≥ 2.8배)"
            if (pbr_3y_max is not None and pbr_3y_max >= 2.8)
            else f"사이클 최고 PBR({pbr_cycle_max:.2f}배 ≥ 2.8배 & 2년 BPS 증감 {bps_2y_chg:+.1f}% < +15%)"
        )
        action_guide = (
            f"{peak_pbr_desc} + 2년 누적 EPS({eps_2y_chg:+.1f}% ≤ -30%) + 현재 ROE({curr_roe:.2f}% < 8.5%) 동시 해당!\n"
            f"      ➔ 과거 호황 멀티플 전면 폐기 | Bear 지지선 = PBR 0.75배 청산가치({(official_bps or curr_bps_used or 0)*0.75:,.0f}원) 강제 고정 | 경상 영업이익 및 분기 수정 EPS QoQ 반등 공시 확인 전까지 신규 매수(1차 정찰병 포함) 원천 보류!"
        )
    else:
        verdict = "🟢 [유형 A: 일시적 사이클 저점 / 정상 영업 궤도 (Cyclical Trough / Normal)]"
        reasons = []
        if bps_2y_chg is not None and bps_2y_chg >= 15.0:
            reasons.append(f"2년 누적 BPS 증감률 {bps_2y_chg:+.1f}% (≥ +15%)")
        if pbr_3y_max is not None and pbr_3y_max < 2.8:
            reasons.append(f"과거 3년 최고 PBR {pbr_3y_max:.2f}배 (< 2.8배)")
        if curr_roe is not None and curr_roe >= 8.5:
            reasons.append(f"현재 ROE {curr_roe:.2f}% (≥ 8.5% 건전)")
        if eps_2y_chg is not None and eps_2y_chg > -30.0:
            reasons.append(f"2년 EPS 증감률 {eps_2y_chg:+.1f}% (> -30% 방어)")
        reason_str = " / ".join(reasons) if reasons else "구조적 버블 붕괴 요건 미해당"
        action_guide = (
            f"판정 근거: {reason_str}\n"
            f"      ➔ 가치함정 멀티플 페널티 면제 & Bear 지하실(하위 20% PBR/배당) 보호막 정상 가동"
        )

    print(f"\n    ▶ Section 2 최종 판정: {verdict}")
    print(f"      {action_guide}")

    return {
        "is_type_b": is_type_b,
        "verdict": verdict,
        "controlling_ratio_pct": controlling_ratio_pct,
        "qoq_eps_positive": qoq_eps_positive,
        "qoq_eps_detail": qoq_eps_detail,
        "qoq_rev_pct": qoq_rev_pct,
        "qoq_op_pct": qoq_op_pct,
        "qoq_summary_str": qoq_summary_str,
        "kill_switch_guide": kill_switch_guide,
        "eps_2y_chg": eps_2y_chg,
        "bps_2y_chg": bps_2y_chg,
        "op_yoy_q": op_yoy_q,
        "op_yoy_ttm": op_yoy_ttm,
        "op_yoy_used": op_yoy_used,
        "op_yoy_detail": op_yoy_detail,
        "curr_roe": curr_roe,
        "mid_cycle_roe": mid_cycle_roe,
        "mid_cycle_eps": mid_cycle_eps,
        "pbr_cycle_max": pbr_cycle_max,
        "pbr_3y_max": pbr_3y_max,
        "pbr_3y_mean": pbr_3y_mean,
        "pbr_3y_p20": pbr_3y_p20,
        "pbr_3y_p65": pbr_3y_p65,
        "pbr_3y_p90": pbr_3y_p90,
        "per_3y_max": per_3y_max,
        "per_3y_mean": per_3y_mean,
        "per_3y_p20": per_3y_p20,
        "per_3y_p65": per_3y_p65,
        "per_3y_p90": per_3y_p90,
        "curr_eps_used": official_eps or curr_eps_used,
        "curr_bps_used": official_bps or curr_bps_used,
    }


def compute_section3_stock_nature_and_tp1(
    stock_code,
    corp_name,
    bench,
    df_week,
    df_day,
    sec2_res,
    curr_price,
    base_target=None,
    bull_target=None,
    scurve_override=None,
    dart_exposure=None,
    forward_eps=None,
    expected_dps=0.0,
    early_ignition=False,
    forward_eps_g=None,
    sector_type="A",
    sotp_res=None,
):
    """
    (B) Section 3 종목 체질 판별, 사이클주 '피크 이익 × 높은 멀티플' 함정 차단(Peak-Cycle Trap Guard),
    Forward BPS 계산, [대안 B vs 대안 A] 목표가 병기 자동 산출 (상한 캡 적용)
    """
    print("\n======================================================================")
    print("🎯 [Phase 3 - Section 3] 종목 체질 · 사이클 피크 함정 차단 · [대안 B vs 대안 A] 목표가 산출")
    print("======================================================================")

    w52 = df_week.tail(52)
    high_52w_close = float(w52["close"].max())
    low_52w_close = float(w52["close"].min())
    high_52w_intraday = float(w52["high"].max())
    low_52w_intraday = float(w52["low"].min())

    range_52w_close_pct = (
        ((high_52w_close / low_52w_close) - 1.0) * 100.0 if low_52w_close > 0 else 0.0
    )
    range_52w_intraday_pct = (
        ((high_52w_intraday / low_52w_intraday) - 1.0) * 100.0 if low_52w_intraday > 0 else 0.0
    )

    w3y_close = df_week.tail(156)["close"]
    win_len = min(52, max(1, len(w3y_close)))
    roll_max = w3y_close.rolling(52, min_periods=win_len).max()
    roll_min = w3y_close.rolling(52, min_periods=win_len).min()
    roll_range = ((roll_max / roll_min.replace(0, np.nan)) - 1.0) * 100.0
    roll_tail = roll_range.dropna().tail(104)
    median_52w_range_pct = (
        float(roll_tail.median()) if not roll_tail.empty else range_52w_close_pct
    )

    effective_52w_range = min(range_52w_close_pct, median_52w_range_pct)
    range_52w_pct = range_52w_close_pct

    bps = bench.get("bps") or sec2_res.get("curr_bps_used") or 0
    trailing_eps = bench.get("eps") or sec2_res.get("curr_eps_used") or 0
    eps = forward_eps if (forward_eps is not None and forward_eps > 0) else trailing_eps
    curr_pbr = (curr_price / bps) if bps > 0 else (bench.get("pbr") or 0)

    if (
        forward_eps is not None
        and bps > 0
        and trailing_eps > 0
        and forward_eps > bps * 0.55
        and (forward_eps / trailing_eps) >= 3.5
    ):
        split_factor = round(forward_eps / trailing_eps)
        if split_factor >= 3:
            eps = round(forward_eps / split_factor, 1)
            expected_dps = round(float(expected_dps or 0.0) / split_factor, 1)

    forward_bps = bps
    if forward_eps is not None and bps > 0:
        retained_eps = max(0.0, float(eps) - float(expected_dps or 0.0))
        forward_bps = round(bps + retained_eps)

    is_low_vol_box = (effective_52w_range < 36.0) and (0 < curr_pbr < 1.35)

    per_p20 = sec2_res.get("per_3y_p20") or 8.0
    per_p65 = sec2_res.get("per_3y_p65") or 12.0
    per_p90 = sec2_res.get("per_3y_p90") or 16.0
    pbr_p20 = sec2_res.get("pbr_3y_p20") or 0.75
    pbr_p65 = sec2_res.get("pbr_3y_p65") or 1.20
    pbr_p90 = sec2_res.get("pbr_3y_p90") or 1.80

    eff_per_p65 = (
        min(per_p65, 18.0)
        if (forward_eps is not None and trailing_eps > 0 and eps > trailing_eps * 1.25 and per_p65 > 22.0)
        else per_p65
    )
    eff_per_p90 = (
        min(per_p90, 24.0)
        if (forward_eps is not None and trailing_eps > 0 and eps > trailing_eps * 1.25 and per_p90 > 28.0)
        else per_p90
    )

    # [NEW v4.2 #3] Cyclical "Peak Earnings × Peak Multiple" Trap Guard
    mid_cycle_eps = sec2_res.get("mid_cycle_eps")
    mid_cycle_roe = sec2_res.get("mid_cycle_roe")
    curr_roe = sec2_res.get("curr_roe")
    is_cyclical_sector = str(sector_type).upper() in ("A", "B")
    is_peak_earnings = False
    peak_trap_reason = ""
    eps_for_valuation = eps

    if is_cyclical_sector:
        if mid_cycle_eps and mid_cycle_eps > 0 and eps >= mid_cycle_eps * 1.35:
            is_peak_earnings = True
            peak_trap_reason = f"현재/추정 EPS({eps:,.0f}원) ≥ 5년 Mid-Cycle 중위 EPS({mid_cycle_eps:,.0f}원) × 1.35배"
        elif mid_cycle_roe and curr_roe and (curr_roe >= mid_cycle_roe + 5.0) and curr_roe >= 15.0:
            is_peak_earnings = True
            peak_trap_reason = f"현재 ROE({curr_roe:.1f}%) ≥ 5년 Mid-Cycle 중위 ROE({mid_cycle_roe:.1f}%) + 5.0%p"

    if is_peak_earnings:
        # Compress target PER on peak earnings or use Mid-Cycle Normalized EPS * Mean PER
        compressed_base_per = min(eff_per_p65, max(per_p20 * 1.15, (per_p20 + (sec2_res.get("per_3y_mean") or 12.0)) / 2.0))
        compressed_bull_per = min(eff_per_p90, (sec2_res.get("per_3y_mean") or 14.0) * 1.10)
        eff_per_p65 = round(compressed_base_per, 2)
        eff_per_p90 = round(compressed_bull_per, 2)

    if sec2_res.get("is_type_b"):
        bear_auto_b = round((bps * 0.75) / 10) * 10 if bps > 0 else round(low_52w_close)
    else:
        bear_pbr_val = bps * pbr_p20 if bps and bps > 0 else 0
        bear_per_val = trailing_eps * per_p20 if trailing_eps and trailing_eps > 0 else 0
        bear_candidates = [v for v in (bear_pbr_val, bear_per_val) if v > 0]
        bear_auto_b = (
            round(min(bear_candidates) / 10) * 10 if bear_candidates else round(low_52w_close)
        )
        if bear_auto_b >= curr_price * 0.95:
            bear_auto_b = round(min(bear_pbr_val if bear_pbr_val > 0 else low_52w_close, low_52w_close) / 10) * 10

    base_candidates = [
        v
        for v in (
            eps_for_valuation * eff_per_p65 if eps_for_valuation and eps_for_valuation > 0 else 0,
            forward_bps * pbr_p65 if forward_bps and forward_bps > 0 else 0,
        )
        if v > 0
    ]
    bull_candidates = [
        v
        for v in (
            eps_for_valuation * eff_per_p90 if eps_for_valuation and eps_for_valuation > 0 else 0,
            forward_bps * pbr_p90 if forward_bps and forward_bps > 0 else 0,
        )
        if v > 0
    ]

    # If peak earnings trap is triggered and mid_cycle_eps is available, cap PER valuation by Mid-Cycle EPS * Mean PER
    if is_peak_earnings and mid_cycle_eps and mid_cycle_eps > 0:
        mean_per = sec2_res.get("per_3y_mean") or 12.0
        mid_cycle_base_val = mid_cycle_eps * mean_per
        if base_candidates:
            base_candidates = [min(base_candidates[0], mid_cycle_base_val * 1.15)] + base_candidates[1:]

    base_auto_b = (
        round((sum(base_candidates) / len(base_candidates)) / 10) * 10
        if base_candidates
        else round(curr_price * 1.20)
    )
    bull_auto_b = (
        round((sum(bull_candidates) / len(bull_candidates)) / 10) * 10
        if bull_candidates
        else round(curr_price * 1.50)
    )

    op_yoy = sec2_res.get("op_yoy_used")
    is_type_b = sec2_res.get("is_type_b", False)
    op_mult = 1.0
    op_reason = "중립 (영업이익 YoY -25% ~ +20% 또는 ROE < 12% → 가감산 0%)"
    if is_type_b:
        op_mult = 1.0
        op_reason = "구조적 가치함정(유형 B) 해당 → 할증 원천 차단 (0%)"
    elif is_peak_earnings:
        op_mult = 1.0
        op_reason = f"🚨 사이클 피크 이익 구간({peak_trap_reason}) → 고점 멀티플 중복 할증(+8%) 강제 차단 (0.0%)"
    elif op_yoy is not None:
        if op_yoy >= 20.0 and (curr_roe is not None and curr_roe >= 12.0):
            op_mult = 1.08
            op_reason = f"고성장 모멘텀 (영업이익 YoY {op_yoy:+.1f}% ≥ +20% & ROE {curr_roe:.1f}% ≥ 12%) → +8.0% 할증"
        elif op_yoy <= -25.0:
            op_mult = 0.94
            op_reason = f"실적 둔화 페널티 (영업이익 YoY {op_yoy:+.1f}% ≤ -25%) → -6.0% 할인"

    sc_info = _detect_scurve_phase2_status(
        stock_code,
        corp_name=corp_name or bench.get("stock_name"),
        scurve_override=scurve_override,
        dart_exposure_override=dart_exposure,
        early_ignition=early_ignition,
    )
    sc_base_mult = 1.0
    sc_bull_mult = 1.0
    if sc_info["is_phase2"] and not is_type_b:
        if sc_info.get("is_early_ignition"):
            # Early Ignition guardrail: requires non-deteriorating OP YoY >= -5%
            if op_yoy is not None and op_yoy < -5.0:
                sc_base_mult = 1.0
                sc_bull_mult = 1.0
                sc_reason = f"미적용 🚫 [선행 발화 가드레일 미충족: 영업이익 YoY {op_yoy:+.1f}% < -5%] → 할증 0%"
            else:
                sc_base_mult = 1.15
                sc_bull_mult = 1.20
                sc_reason = f"적용 🔥 [{sc_info['theme_name']}] → Base +15.0% / Bull +20.0% 선행 발화 할증"
        else:
            sc_base_mult = 1.10
            sc_bull_mult = 1.12
            sc_reason = f"적용 ✅ [{sc_info['theme_name']} | DART 검증 익스포저 {sc_info['exposure_pct']:.1f}% ≥ 30%] → Base +10.0% / Bull +12.0% 할증"
    else:
        sc_reason = f"미적용 ⚪ [{sc_info['theme_name']}] → +0.0% (본업 대안 B 기준 평가)"

    base_mult_a = round(op_mult * sc_base_mult, 4)
    bull_mult_a = round(op_mult * sc_bull_mult, 4)
    bear_auto_a = bear_auto_b

    base_auto_a = round((base_auto_b * base_mult_a) / 10) * 10
    bull_auto_a = round((bull_auto_a if False else bull_auto_b * bull_mult_a) / 10) * 10

    # Optional PEG(0.8) Dynamic Rerating with dual hard cap (<= 1.45x Model B)
    if forward_eps_g is not None and forward_eps_g > 0 and eps > 0 and sc_info["is_phase2"] and not is_type_b:
        capped_g = min(float(forward_eps_g), 35.0)
        peg_target_per = capped_g * 0.80
        peg_price = round((eps * peg_target_per) / 10) * 10
        base_auto_a = min(max(base_auto_a, peg_price), round((base_auto_b * 1.45) / 10) * 10)

    pbr_cap_price = (
        round((forward_bps * (sec2_res.get("pbr_cycle_max") or (pbr_p90 * 1.25))) / 10) * 10
        if forward_bps > 0
        else bull_auto_a
    )
    if pbr_cap_price > bull_auto_b and bull_auto_a > pbr_cap_price:
        bull_auto_a = pbr_cap_price

    tp1_p70 = float(w52["close"].quantile(0.70))
    tp1_p75 = float(w52["close"].quantile(0.75))
    d60_ma = (
        float(df_day["close"].rolling(60).mean().iloc[-1])
        if (not df_day.empty and len(df_day) >= 60)
        else tp1_p70
    )
    d60_high_p75 = (
        float(df_day.tail(60)["high"].quantile(0.75))
        if not df_day.empty
        else tp1_p75
    )
    tp1_price_b = round(max(tp1_p75, d60_high_p75, curr_price * 1.06) / 10) * 10
    tp1_price_a = (
        round((tp1_price_b * op_mult) / 10) * 10 if op_mult != 1.0 else tp1_price_b
    )
    tp1_upside_b = ((tp1_price_b / curr_price) - 1.0) * 100.0 if curr_price > 0 else 0.0
    tp1_upside_a = ((tp1_price_a / curr_price) - 1.0) * 100.0 if curr_price > 0 else 0.0

    print(f"    - 단일 기준 주가 (최신 일봉 종가): {curr_price:,.0f}원")
    print(
        f"    - 최근 52주 주봉 종가 범위: {low_52w_close:,.0f}원 ~ {high_52w_close:,.0f}원 (단순 52주 등락폭: {range_52w_close_pct:.1f}% | 3Y 롤링 52주 중위값: {median_52w_range_pct:.1f}%)"
    )
    print(
        f"    - 현재 공식 BPS: {bps:,.0f}원 (현재 PBR {curr_pbr:.2f}배)"
        + (
            f" | 추정 Forward BPS: {forward_bps:,.0f}원 (Forward PBR {curr_price/forward_bps:.2f}배)"
            if forward_bps != bps
            else ""
        )
    )

    if is_peak_earnings:
        print(
            f"    - 🚨 [사이클 고점 이익 함정 경보 (Peak-Earnings × Peak-Multiple Guard)]: {peak_trap_reason}"
        )
        print(
            f"      ➔ 호황 정점 이익에 높은 멀티플(p65~p90)을 곱하는 고전적 함정을 차단하기 위해 압축 PER(Base {eff_per_p65:.2f}배 / Bull {eff_per_p90:.2f}배) 및 Mid-Cycle 정상화 이익 캡을 강제 적용했습니다."
        )
    else:
        print("    - 🟢 [사이클 고점 함정 체크]: 피크 이익 과열 구간 아님 (정상 멀티플 밴드 적용)")

    if sotp_res and sotp_res.get("sotp_triggered"):
        core_bps_v = sotp_res.get("core_bps", bps)
        non_op_per_sh = max(0, round((bps - core_bps_v) * (1.0 - sotp_res.get("non_op_haircut", 0.30))))
        core_op_per_sh_base = round(core_bps_v * pbr_p65)
        sotp_fair_base = round((core_op_per_sh_base + non_op_per_sh) / 10) * 10
        print(
            f"    - 🏛️ [SOTP 3단 분해 주당가치 (비영업자산 {sotp_res.get('non_op_max_ratio_pct', 0):.1f}% ≥ 20% 강제 적용)]:"
        )
        print(
            f"      ➔ ① 본업 주당가치({core_op_per_sh_base:,}원 = 본업BPS {core_bps_v:,}원 × PBR {pbr_p65:.2f}배) + ② 비영업지분·부동산 안전가치({non_op_per_sh:,}원, 30% 할인) = SOTP Base 적정가 {sotp_fair_base:,}원"
        )

    if is_low_vol_box:
        nature_badge = "🛡️ [저변동 고배당 박스권주 (52주 등락폭 < 36% & PBR < 1.35배)]"
        nature_desc = (
            f"유효 52주 등락폭({effective_52w_range:.1f}% < 36%) & PBR({curr_pbr:.2f}배 < 1.35배) 해당!\n"
            f"      ➔ 시나리오 표에 이론적 Base/Bull과 별개로 [TP1 단기 박스권 1차 실전 목표가: 대안B {tp1_price_b:,.0f}원({tp1_upside_b:+.1f}%) | 대안A {tp1_price_a:,.0f}원({tp1_upside_a:+.1f}%)] 행을 반드시 추가하십시오!"
        )
    else:
        nature_badge = "🚀 [성장·사이클 추세주 (52주 등락폭 >= 36%)]"
        nature_desc = (
            f"유효 52주 등락폭({effective_52w_range:.1f}%) 또는 PBR({curr_pbr:.2f}배) 기준 추세/사이클 종목 해당!\n"
            f"      ➔ Phase 1 1순위 채택 지표 기반 [🛡️ 대안 B(보수적 순수 DART 중심 닻) | 🚀 대안 A(실적·S-Curve 할증 상단)] 목표가를 나란히 병기합니다."
        )

    print(f"\n    ▶ Section 3 종목 체질 판정: {nature_badge}")
    print(f"      {nature_desc}")

    print("\n    ▶ [대안 A (실적 모멘텀 · S-Curve 할증) 가중 계수 산출 내역]:")
    print(f"      ① DART 실적 모멘텀 가감산 (op_mult = ×{op_mult:.2f}): {op_reason}")
    print(
        f"      ② S-Curve Phase 2 할증 (Base ×{sc_base_mult:.2f} / Bull ×{sc_bull_mult:.2f}): {sc_reason}"
    )
    if sc_info.get("source_pdf"):
        print(f"         • 검증 출처: {sc_info['source_pdf']}")
    print(
        f"      ③ 최종 대안 A 멀티플 가중치: Base ×{base_mult_a:.4f} ({(base_mult_a-1)*100:+.1f}%) | Bull ×{bull_mult_a:.4f} ({(bull_mult_a-1)*100:+.1f}%) | Bear ×1.0000 (하방 보호 고정)"
    )

    final_base_b = (
        round(base_target / 10) * 10
        if (base_target is not None and base_target > 0)
        else base_auto_b
    )
    final_base_a = round((final_base_b * base_mult_a) / 10) * 10
    final_bull_b = (
        round(bull_target / 10) * 10
        if (bull_target is not None and bull_target > 0)
        else bull_auto_b
    )
    final_bull_a = round((final_bull_b * bull_mult_a) / 10) * 10

    print("\n    ▶ [시나리오 목표가 가이드라인 — 🛡️ 대안 B (중심 닻) vs 🚀 대안 A (할증 상단)]:")
    print(
        f"      • Bull (호황 상단): 🛡️ 대안 B {final_bull_b:,.0f}원 ({(final_bull_b/curr_price-1)*100:+.1f}%)  |  🚀 대안 A {final_bull_a:,.0f}원 ({(final_bull_a/curr_price-1)*100:+.1f}%)"
    )
    print(
        f"      • Base (기본 적정): 🛡️ 대안 B {final_base_b:,.0f}원 ({(final_base_b/curr_price-1)*100:+.1f}%)  |  🚀 대안 A {final_base_a:,.0f}원 ({(final_base_a/curr_price-1)*100:+.1f}%)"
    )
    print(
        f"      • TP1  (단기 실전): 🛡️ 대안 B {tp1_price_b:,.0f}원 ({tp1_upside_b:+.1f}%)  |  🚀 대안 A {tp1_price_a:,.0f}원 ({tp1_upside_a:+.1f}%) [52주 70~75%: {tp1_p70:,.0f}~{tp1_p75:,.0f}원 / 60일선: {d60_ma:,.0f}원]"
    )
    print(
        f"      • Bear (하방 지지): 🛡️ 대안 B·A 공통 {bear_auto_b:,.0f}원 ({(bear_auto_b/curr_price-1)*100:+.1f}%) [하방 안전마진 할증 금지]"
    )

    return {
        "is_low_vol_box": is_low_vol_box,
        "nature_badge": nature_badge,
        "range_52w_pct": round(range_52w_pct, 2),
        "median_52w_range_pct": round(median_52w_range_pct, 2),
        "effective_52w_range": round(effective_52w_range, 2),
        "forward_bps": forward_bps,
        "is_peak_trap": is_peak_earnings,
        "peak_trap_reason": peak_trap_reason,
        "op_mult": op_mult,
        "sc_base_mult": sc_base_mult,
        "sc_bull_mult": sc_bull_mult,
        "base_mult_a": base_mult_a,
        "bull_mult_a": bull_mult_a,
        "scurve_info": sc_info,
        "tp1_price": tp1_price_b,
        "tp1_price_a": tp1_price_a,
        "tp1_upside": round(tp1_upside_b, 2),
        "tp1_upside_a": round(tp1_upside_a, 2),
        "bear_auto": bear_auto_b,
        "base_auto": final_base_b,
        "bull_auto": final_bull_b,
        "base_auto_a": final_base_a,
        "bull_auto_a": final_bull_a,
    }


def compute_risk_causal_chain_diagnosis(dart_ts, sotp_res, sec2_res, net_debt_eok=0.0):
    """
    [NEW v4.2 #4] 리스크 간 인과단계(1차 촉발 ➔ 2차 영업/운전자본 전이 ➔ 3차 재무/자본 타격) 연결관계 진단
    """
    print("\n======================================================================")
    print("⛓️ [Step 3.5 — 리스크 인과단계 & 도미노 연쇄 구조] 1차 촉발 ➔ 2차 영업 ➔ 3차 재무 전이 진단")
    print("======================================================================")

    op_yoy = sec2_res.get("op_yoy_used")
    qoq_op = sec2_res.get("qoq_op_pct")
    has_net_debt = net_debt_eok > 0

    stage1 = "전방 산업 수요 둔화 / 고객사 재고 조정 또는 경쟁사 단가 인하(ASP 하락)"
    stage2 = (
        f"수주/출하(Q) 둔화 ➔ 고정비(감가상각·인건비) 디레버리지로 영업이익 급감(현재 QoQ {f'{qoq_op:+.1f}%' if qoq_op is not None else '모니터링'}, YoY {f'{op_yoy:+.1f}%' if op_yoy is not None else '모니터링'}) 및 재고/매출채권 회전일수 상승"
    )
    stage3 = (
        f"영업현금흐름(OCF) 축소 ➔ 순차입금({net_debt_eok:+,.1f}억) 이자부담 가중·CB 리픽싱/풋옵션 또는 배당 축소 ➔ 멀티플 하단(Bear) 회귀"
        if has_net_debt
        else f"영업현금흐름(OCF) 둔화 ➔ 순현금({abs(net_debt_eok):,.1f}억) 버퍼 소진 및 S-Curve/성장 멀티플 프리미엄 박탈 ➔ PBR/배당 하단 지지선 테스트"
    )

    print(f"    - [1차 촉발 단계 (Macro/Demand Trigger)]: {stage1}")
    print(f"    - [2차 영업 전이 단계 (Operational/Margin Domino)]: {stage2}")
    print(f"    - [3차 재무·밸류 타격 단계 (Financial/Valuation Impact)]: {stage3}")
    print("    - 📌 [보고서 작성 지침]: Phase 1 Section 6 및 Phase 3 Section 3.5에서 리스크를 단순 병렬 나열하지 말고 위 1차➔2차➔3차 인과 사슬(Domino Chain)과 전이 임계치 숫자로 연결해 기술하십시오.")

    return {
        "stage1_trigger": stage1,
        "stage2_operational": stage2,
        "stage3_financial": stage3,
    }


def compute_section4_master_sop_indicators(df_day, df_week, sec2_res, sec3_res):
    """
    (C) Section 4 마스터 SOP 정예 보조지표 실측값 + 가격 오름차순(Buy1 < Buy2 < Buy3 < Target) & 2단계 안전핀(초입 안전핀 < Buy1) 자동 설계
    Supports both Track 1 (Pullback Value/Cyclical) and Track 2 (S-Curve Leader Breakout).
    """
    print("\n======================================================================")
    print("📐 [Phase 3 - Section 4] 마스터 SOP 정예 보조지표 & 무결성 3분할 매매 타점 자동 설계")
    print("======================================================================")

    res = {}
    if not df_day.empty:
        curr_d_close = float(df_day["close"].iloc[-1])
        curr_d_date = df_day["date"].iloc[-1].strftime("%Y-%m-%d")
        d_ma5 = float(df_day["close"].rolling(5, min_periods=1).mean().iloc[-1])
        d_ma20 = float(df_day["close"].rolling(20, min_periods=1).mean().iloc[-1])
        d_ma60 = float(df_day["close"].rolling(60, min_periods=1).mean().iloc[-1])

        vol_ma20 = float(df_day["vol"].rolling(20, min_periods=1).mean().iloc[-1])
        curr_vol = float(df_day["vol"].iloc[-1])
        vol_ratio = (curr_vol / vol_ma20) if vol_ma20 > 0 else 0.0

        recent20 = df_day.tail(20).copy()
        p_min = float(recent20["low"].min())
        p_max = float(recent20["high"].max())
        if p_max <= p_min:
            p_max = p_min + 1.0
        bins = np.linspace(p_min, p_max, 6)
        recent20["tp"] = (recent20["high"] + recent20["low"] + recent20["close"]) / 3.0
        total_v20 = float(recent20["vol"].sum())
        bin_vols = []
        for i in range(5):
            low_b, high_b = bins[i], bins[i + 1]
            if i < 4:
                mask = (recent20["tp"] >= low_b) & (recent20["tp"] < high_b)
            else:
                mask = (recent20["tp"] >= low_b) & (recent20["tp"] <= high_b)
            v_sum = float(recent20.loc[mask, "vol"].sum())
            pct = (v_sum / total_v20 * 100.0) if total_v20 > 0 else 0.0
            bin_vols.append((int(round(low_b)), int(round(high_b)), v_sum, pct))

        poc_bin = max(bin_vols, key=lambda x: x[2])
        poc_low, poc_high, _, poc_pct = poc_bin
        poc_trail_pin = int(round(poc_low * 0.97))

        low12 = df_day["low"].rolling(12, min_periods=1).min()
        high12 = df_day["high"].rolling(12, min_periods=1).max()
        denom = (high12 - low12).replace(0, np.nan)
        fast_k = ((df_day["close"] - low12) / denom * 100.0).fillna(50.0)
        slow_k = fast_k.rolling(5, min_periods=1).mean()
        slow_d = slow_k.rolling(5, min_periods=1).mean()
        curr_k = float(slow_k.iloc[-1])
        curr_d = float(slow_d.iloc[-1])

        print(f"  [일봉 (Daily, 기준일: {curr_d_date}, 종가: {curr_d_close:,.0f}원)]")
        print(
            f"    - 일봉 20일 매물대(5칸 Volume Profile POC): {poc_low:,}원 ~ {poc_high:,}원 (거래비중: {poc_pct:.1f}%)"
        )
        print(
            "      • 5칸 전체 분포: "
            + " | ".join([f"[{b[0]:,}~{b[1]:,}원: {b[3]:.1f}%]" for b in bin_vols])
        )
        print(f"    - 일봉 이동평균선: 5일선 {int(round(d_ma5)):,}원 | 20일선 {int(round(d_ma20)):,}원 | 60일선 {int(round(d_ma60)):,}원")
        print(
            f"    - 거래량 분석:     20일 평균 거래량 {int(round(vol_ma20)):,}주 | 당일 거래량 {int(round(curr_vol)):,}주 (배수 vol_ratio: {vol_ratio:.2f}배)"
        )
        print(
            f"    - 일봉 Slow Stochastic(12,5,5): %K = {curr_k:.1f}% / %D = {curr_d:.1f}% ({'골든크로스/상승우위' if curr_k >= curr_d else '데드크로스/조정중'})"
        )

        res.update({
            "d_close": int(round(curr_d_close)),
            "d_low20": int(round(p_min)),
            "poc_low": poc_low,
            "poc_high": poc_high,
            "poc_pct": round(poc_pct, 1),
            "poc_trail_pin": poc_trail_pin,
            "d_ma5": int(round(d_ma5)),
            "d_ma20": int(round(d_ma20)),
            "d_ma60": int(round(d_ma60)),
            "vol_ma20": int(round(vol_ma20)),
            "vol_ratio": round(vol_ratio, 2),
            "stoch_k": round(curr_k, 1),
            "stoch_d": round(curr_d, 1),
        })

    if not df_week.empty:
        curr_w_close = float(df_week["close"].iloc[-1])
        curr_w_date = df_week["date"].iloc[-1].strftime("%Y-%m-%d")
        w_ma10 = float(df_week["close"].rolling(10, min_periods=1).mean().iloc[-1])
        w_ma20 = float(df_week["close"].rolling(20, min_periods=1).mean().iloc[-1])
        w_ma60 = float(df_week["close"].rolling(60, min_periods=1).mean().iloc[-1])

        w_std20_raw = df_week["close"].rolling(20, min_periods=1).std().iloc[-1]
        w_std20 = float(w_std20_raw) if pd.notna(w_std20_raw) else 0.0
        bb_upper = w_ma20 + 2.0 * w_std20
        bb_lower = w_ma20 - 2.0 * w_std20

        ema12 = df_week["close"].ewm(span=12, adjust=False).mean()
        ema26 = df_week["close"].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - sig_line
        curr_macd = float(macd_line.iloc[-1])
        curr_sig = float(sig_line.iloc[-1])
        curr_hist = float(macd_hist.iloc[-1])

        tp_w = (df_week["high"] + df_week["low"] + df_week["close"]) / 3.0
        rmf_w = tp_w * df_week["vol"]
        tp_diff = tp_w.diff()
        pos_flow = np.where(tp_diff > 0, rmf_w, 0.0)
        neg_flow = np.where(tp_diff < 0, rmf_w, 0.0)
        pos_mf14 = pd.Series(pos_flow).rolling(14, min_periods=1).sum()
        neg_mf14 = pd.Series(neg_flow).rolling(14, min_periods=1).sum().replace(0, 1.0)
        mfi14 = (100.0 - (100.0 / (1.0 + (pos_mf14 / neg_mf14)))).fillna(50.0)
        curr_mfi = float(mfi14.iloc[-1])
        max_12w_mfi = float(mfi14.tail(12).max())

        delta_w = df_week["close"].diff()
        gain_w = delta_w.clip(lower=0.0)
        loss_w = (-delta_w.clip(upper=0.0))
        avg_gain = gain_w.ewm(alpha=1 / 14, min_periods=1, adjust=False).mean()
        avg_loss = loss_w.ewm(alpha=1 / 14, min_periods=1, adjust=False).mean().replace(0, 1e-9)
        rs_w = avg_gain / avg_loss
        rsi14 = (100.0 - (100.0 / (1.0 + rs_w))).fillna(50.0)
        curr_rsi = float(rsi14.iloc[-1])

        divergence_warn = (curr_rsi >= 70.0) and (curr_mfi < 70.0)
        mfi_peakout_warn = (max_12w_mfi >= 80.0) and (curr_mfi < 80.0)

        print(f"\n  [주봉 (Weekly, 기준일: {curr_w_date}, 종가: {curr_w_close:,.0f}원)]")
        print(
            f"    - 주봉 이동평균선:   10주선 {int(round(w_ma10)):,}원 | 20주선 {int(round(w_ma20)):,}원 | 60주선 {int(round(w_ma60)):,}원"
        )
        print(
            f"    - 주봉 볼린저밴드(20, 2): 상단 {int(round(bb_upper)):,}원 | 중심(20주) {int(round(w_ma20)):,}원 | 하단 {int(round(bb_lower)):,}원"
        )
        print(
            f"    - 주봉 MACD(12,26,9): MACD {curr_macd:,.1f} / Signal {curr_sig:,.1f} / Histogram {curr_hist:+,.1f} ({'양(+) 추세' if curr_hist >= 0 else '음(-) 조정'})"
        )
        print(
            f"    - 주봉 MFI(14) & RSI(14): 주봉 MFI(14) = {curr_mfi:.1f} | 주봉 RSI(14) = {curr_rsi:.1f} | 최근 12주 주봉 MFI 최고점 = {max_12w_mfi:.1f}"
        )
        print(
            f"    - 고점경보 판독:     1호(주봉 MFI 80 하향이탈): {'🚨경보' if mfi_peakout_warn else '정상'} | 2호(RSI≥70 & MFI<70 괴리): {'🚨경보' if divergence_warn else '정상'}"
        )

        res.update({
            "w_close": int(round(curr_w_close)),
            "w_ma10": int(round(w_ma10)),
            "w_ma20": int(round(w_ma20)),
            "w_ma60": int(round(w_ma60)),
            "bb_upper": int(round(bb_upper)),
            "bb_lower": int(round(bb_lower)),
            "w_macd": round(curr_macd, 1),
            "w_macd_sig": round(curr_sig, 1),
            "w_macd_hist": round(curr_hist, 1),
            "w_mfi14": round(curr_mfi, 1),
            "w_rsi14": round(curr_rsi, 1),
            "w_mfi14_12w_max": round(max_12w_mfi, 1),
        })

    curr_p = res.get("d_close") or res.get("w_close") or 10000
    base_b = sec3_res.get("base_auto", int(round(curr_p * 1.25)))
    base_a = sec3_res.get("base_auto_a", base_b)
    tp1_b = sec3_res.get("tp1_price", int(round(curr_p * 1.15)))
    bear_b = sec3_res.get("bear_auto", int(round(curr_p * 0.85)))
    is_box = sec3_res.get("is_low_vol_box", False)
    primary_t = tp1_b if (is_box and tp1_b < base_b) else base_b

    # Check if Track 2 (S-Curve Leader Breakout) applies
    sc_info = sec3_res.get("scurve_info", {})
    w_ma60_val = res.get("w_ma60", curr_p)
    disparity_60w = (curr_p / w_ma60_val * 100.0) if w_ma60_val > 0 else 100.0
    overheated = (disparity_60w > 140.0) or (
        disparity_60w > 125.0 and res.get("w_mfi14_12w_max", 50.0) >= 80.0 and res.get("w_mfi14", 50.0) < 75.0
    )

    is_track2 = (
        sc_info.get("is_phase2", False)
        and (curr_p >= primary_t * 0.96)
        and (curr_p <= base_a * 0.90)
        and not overheated
    )
    is_overvalued = (curr_p >= primary_t * 0.96) and not is_track2

    if is_overvalued:
        buy1 = int(round((base_b * 0.80) / 10) * 10)
        buy2 = int(round((base_b * 0.85) / 10) * 10)
        buy3 = int(round((base_b * 0.90) / 10) * 10)
        sop_mode_desc = f"⏳ [고평가 추격매수 차단 → 눌림목 대기 모드]: 현재가({curr_p:,}원) ≥ Base 적정가({base_b:,}원) 부근이므로 현재가 추격 매수를 금지하고 Base 대비 -10~-20% 안전마진 구간({buy1:,}~{buy3:,}원)에서만 분할 진입!"
    elif is_track2:
        primary_t = base_a
        buy1 = int(round((curr_p * 0.97) / 10) * 10)
        buy2 = int(round(curr_p / 10) * 10)
        buy3 = int(round(min(curr_p * 1.03, base_a * 0.92) / 10) * 10)
        sop_mode_desc = f"🔥 [Track 2: S-Curve 주도주 돌파 분할매수 모드]: 대안 B({base_b:,}원) 돌파 후 대안 A({base_a:,}원) 대비 안전마진 확보 & 과열 경보 없음(60주 이격도 {disparity_60w:.1f}%)"
    else:
        max_buy_ceiling = int(round(min(curr_p * 0.92, base_b * 0.85) if curr_p > primary_t else min(primary_t * 0.92, curr_p * 1.04)))
        buy1 = int(round(min(bear_b, curr_p * 0.94, primary_t * 0.82) / 10) * 10)
        if max_buy_ceiling <= buy1 * 1.04:
            max_buy_ceiling = int(round(min(primary_t * 0.91, max(curr_p, buy1 * 1.08)) / 10) * 10)

        tech_candidates = sorted(
            list(
                set([
                    res.get("w_ma10", 0),
                    res.get("w_ma20", 0),
                    res.get("d_ma20", 0),
                    res.get("poc_low", 0),
                    res.get("poc_high", 0),
                    curr_p,
                ])
            )
        )
        valid_steps = [lvl for lvl in tech_candidates if (buy1 * 1.015) <= lvl <= max_buy_ceiling]
        if len(valid_steps) >= 2:
            buy2 = int(round(valid_steps[0] / 10) * 10)
            buy3 = int(round(valid_steps[-1] / 10) * 10)
            if buy3 <= buy2:
                buy3 = int(round(((buy2 + max_buy_ceiling) / 2) / 10) * 10)
        elif len(valid_steps) == 1:
            buy2 = int(round(valid_steps[0] / 10) * 10)
            buy3 = int(round(((buy2 + max_buy_ceiling) / 2) / 10) * 10)
        else:
            buy2 = int(round((buy1 + (max_buy_ceiling - buy1) * 0.45) / 10) * 10)
            buy3 = int(round((buy1 + (max_buy_ceiling - buy1) * 0.85) / 10) * 10)

        if buy2 <= buy1:
            buy2 = int(round((buy1 * 1.03) / 10) * 10)
        if buy3 <= buy2:
            buy3 = int(round((buy2 * 1.03) / 10) * 10)

        sop_mode_desc = f"🚀 [Track 1: 오름차순 정렬 완료 (Buy1 < Buy2 < Buy3 < 목표가)]: 낮은 추세선({buy2:,}원)을 2차 본대로, 높은 추세선/매물대({buy3:,}원)를 3차 확인사살로 배치하여 가격 역전 및 역손익비 원천 차단!"

    avg_buy_price = int(round(buy1 * 0.30 + buy2 * 0.40 + buy3 * 0.30))

    low20_pin = int(round((res.get("d_low20", buy1) * 0.97) / 10) * 10)
    bear_pin = int(round((bear_b * 0.95) / 10) * 10)
    cand_pins = [p for p in (low20_pin, bear_pin) if (buy1 * 0.86) <= p <= (buy1 * 0.97)]
    if cand_pins:
        init_stop_pin = max(cand_pins)
    else:
        init_stop_pin = int(round((buy1 * 0.95) / 10) * 10)

    poc_trail = res.get("poc_trail_pin", int(round(buy2 * 0.97)))
    breakout_trail_pin = (
        poc_trail if poc_trail > init_stop_pin else int(round((buy2 * 0.97) / 10) * 10)
    )

    init_loss_pct = ((init_stop_pin / buy1) - 1.0) * 100.0 if buy1 > 0 else 0.0
    avg_loss_pct = ((init_stop_pin / avg_buy_price) - 1.0) * 100.0 if avg_buy_price > 0 else 0.0
    target_upside_pct = ((primary_t / avg_buy_price) - 1.0) * 100.0 if avg_buy_price > 0 else 0.0
    exec_rr = (target_upside_pct / abs(avg_loss_pct)) if avg_loss_pct < 0 else 0.0

    # Gate 1 (Fundamental R:R) & Gate 2 (Execution R:R)
    base_up_curr = ((primary_t / curr_p) - 1.0) * 100.0 if curr_p > 0 else 0.0
    bear_dn_curr = abs(((bear_b / curr_p) - 1.0) * 100.0) if (curr_p > 0 and bear_b < curr_p) else 1.0
    gate1_rr = round(base_up_curr / max(bear_dn_curr, 0.5), 2) if base_up_curr > 0 else 0.0

    print("\n  [🛡️ 마스터 SOP 무결성 검증 완료 — 3분할 매수가 & 2단계 안전핀 자동 설계]")
    print(f"    - 전략 모드: {sop_mode_desc}")
    print(f"    - 1차 매수 (30% · 가치/눌림목 선취매): {buy1:,}원")
    print(f"    - 2차 매수 (40% · 1차 추세선 돌파):    {buy2:,}원")
    print(f"    - 3차 매수 (30% · 상단 매물대/추세 안착): {buy3:,}원")
    print(
        f"    - ▶ 3분할 완성 평균단가: {avg_buy_price:,}원 (1차 목표가 {primary_t:,}원 대비 기대수익 {target_upside_pct:+.1f}%)"
    )
    print(
        f"    - 🚨 [1단계: 초입 고정 손절 안전핀 (Hard Stop 1)]: {init_stop_pin:,}원 (1차 매수가 대비 {init_loss_pct:+.1f}%, 완성 평단 대비 {avg_loss_pct:+.1f}% | Gate 1 R:R = {gate1_rr:.2f} : 1 | Gate 2 실전 R:R = {exec_rr:.2f} : 1)"
    )
    print(
        f"    - 🔒 [2단계: 상단 매물대 돌파 후 본절 보호핀 (Trailing Stop 2)]: {breakout_trail_pin:,}원 (주가가 20일 매물대 상단 {res.get('poc_high', buy3):,}원 돌파 안착 성공 시에만 손절선을 이 가격으로 상향 이동)"
    )

    res.update({
        "buy1": buy1,
        "buy2": buy2,
        "buy3": buy3,
        "avg_buy_price": avg_buy_price,
        "init_stop_pin": init_stop_pin,
        "breakout_trail_pin": breakout_trail_pin,
        "gate1_rr": gate1_rr,
        "exec_rr": round(exec_rr, 2),
        "is_overvalued": is_overvalued,
        "is_track2": is_track2,
    })
    return res


def export_pipeline_ssot(
    export_json_path,
    stock_code,
    corp_name,
    curr_price,
    distb_shares,
    total_shares,
    fcf_eok,
    net_debt_eok,
    bench,
    sotp_res,
    regime_res,
    sec8_res,
    sec2_res,
    sec3_res,
    risk_res,
    sec4_res,
):
    """
    [NEW v4.2 #6] Pipeline SSOT (Single Source of Truth) Exporter
    Writes 100% of code-computed numbers to valuation_metrics.json and generates ready-to-paste
    Markdown tables (auto_tables.md) so AI never transcribes numbers by hand.
    """
    existing_dcf = {}
    if os.path.exists(export_json_path):
        try:
            with open(export_json_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_dcf = old_data.get("reverse_dcf", {})
        except Exception:
            pass

    d_sh = int(distb_shares or existing_dcf.get("distb_shares") or 0)
    t_sh = int(total_shares or existing_dcf.get("total_shares") or d_sh)
    fcf_v = float(fcf_eok if fcf_eok is not None else (existing_dcf.get("fcf_eok") or 0.0))
    nd_v = float(net_debt_eok if net_debt_eok is not None else (existing_dcf.get("net_debt_eok") or 0.0))

    canonical_html_tag = (
        f'<div id="dcf-calculator-root" '
        f'data-price="{int(curr_price)}" '
        f'data-shares="{d_sh}" '
        f'data-total-shares="{t_sh}" '
        f'data-fcf="{fcf_v:.1f}" '
        f'data-net-debt="{nd_v:.1f}" '
        f'data-non-op-assets="{sotp_res.get("recognized_non_op_eok", 0.0):.1f}" '
        f'data-bear="{sec3_res["bear_auto"]}" '
        f'data-base="{sec3_res["base_auto"]}" '
        f'data-bull="{sec3_res["bull_auto"]}" '
        f'data-base-a="{sec3_res["base_auto_a"]}" '
        f'data-bull-a="{sec3_res["bull_auto_a"]}" '
        f'data-tp1="{sec3_res["tp1_price"]}" '
        f'data-tp1-a="{sec3_res["tp1_price_a"]}" '
        f'data-buy1="{sec4_res.get("buy1", 0)}" '
        f'data-buy2="{sec4_res.get("buy2", 0)}" '
        f'data-buy3="{sec4_res.get("buy3", 0)}" '
        f'data-stop-pin="{sec4_res.get("init_stop_pin", 0)}" '
        f'data-poc-low="{sec4_res.get("poc_low", 0)}"></div>'
    )

    payload = {
        "version": "4.2",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stock_code": str(stock_code).zfill(6),
        "corp_name": corp_name,
        "anchor_price": int(curr_price),
        "primary_metric": sec8_res.get("primary_metric", "PBR"),
        "bench": bench,
        "sotp": sotp_res,
        "regime": regime_res,
        "section8": sec8_res,
        "section2": sec2_res,
        "section3": sec3_res,
        "cyclical_guard": {
            "is_peak_trap": sec3_res.get("is_peak_trap", False),
            "peak_trap_reason": sec3_res.get("peak_trap_reason", ""),
        },
        "risk_causal_chain": risk_res,
        "section4": sec4_res,
        "reverse_dcf": existing_dcf,
        "canonical_html_tag": canonical_html_tag,
    }

    os.makedirs(os.path.dirname(os.path.abspath(export_json_path)), exist_ok=True)
    with open(export_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Also generate auto_tables.md in the same folder for zero-error copy/include
    auto_md_path = os.path.join(os.path.dirname(os.path.abspath(export_json_path)), "auto_tables.md")
    b_up_b = (sec3_res["base_auto"] / curr_price - 1.0) * 100.0
    b_up_a = (sec3_res["base_auto_a"] / curr_price - 1.0) * 100.0
    u_up_b = (sec3_res["bull_auto"] / curr_price - 1.0) * 100.0
    u_up_a = (sec3_res["bull_auto_a"] / curr_price - 1.0) * 100.0
    d_dn = (sec3_res["bear_auto"] / curr_price - 1.0) * 100.0

    tp1_row = ""
    if sec3_res.get("is_low_vol_box"):
        tp1_row = f"| **TP1 (단기 박스권 1차)** | - | 52주 75% 저항대 | 박스권 상단 목표 | 🛡️ **{sec3_res['tp1_price']:,}원** / 🚀 **{sec3_res['tp1_price_a']:,}원** | 🛡️ **{sec3_res['tp1_upside']:+.1f}%** / 🚀 **{sec3_res['tp1_upside_a']:+.1f}%** |\n"

    auto_md = f"""<!-- [AUTO-GENERATED BY backtest_valuation.py v4.2 — DO NOT EDIT NUMBERS MANUALLY] -->

### 📌 [코드 자동 생성 1] SOTP 장부 분해 & 4대 레짐 체크 요약표
| 검증 항목 | 코드 실측 수치 | 판정 결과 (Code Verdict) |
| :--- | :--- | :--- |
| **단일 기준 주가 (Anchor Price)** | **`{int(curr_price):,}원`** | 전 Phase 1~3 100% 동일 고정 |
| **지배주주 순이익 귀속률** | **`{sec2_res['controlling_ratio_pct']:.1f}%`** | 비지배지분 차감 완료 |
| **Step 1.5 비영업자산 비중 (SOTP 20% 룰)** | 장부 대비 **`{sotp_res['non_op_ratio_book_pct']:.1f}%`** / 시총 대비 **`{sotp_res['non_op_ratio_mcap_pct']:.1f}%`** | {'🚨 **SOTP 분리 평가 발동** (본업BPS ' + f"{sotp_res['core_bps']:,}원)" if sotp_res['sotp_triggered'] else '🟢 **본업 자산 중심 (<20%)**'} |
| **Step 1.5 본업 이익 유지력 (Core ROIC)** | TTM 영업이익 **`{sotp_res['nopat_eok']/0.78:,.1f}억`** (NOPAT **`{sotp_res['nopat_eok']:,.1f}억`**) | {'✅ **본업 이익 유지 견고**' if sotp_res['core_profit_sustainable'] else '⚠️ **본업 수익성 변동 주의**'} |
| **Step 2.0 4대 레짐 변화 체크** | {' / '.join(regime_res['flags']) if regime_res['flags'] else '설비·자본·비영업·믹스 급변 없음'} | {'🔄 **레짐 변화 반영 필수**' if regime_res['regime_changed'] else '🟢 **과거 밴드 연속성 유효**'} |
| **Step 3.0 사이클 고점 함정 가드레일** | {sec3_res['peak_trap_reason'] if sec3_res['is_peak_trap'] else '피크 이익 과열 미해당'} | {'🚨 **피크 멀티플 제한 & Mid-Cycle 캡 발동**' if sec3_res['is_peak_trap'] else '🟢 **정상 멀티플 적용**'} |
| **직전 분기 대비 (QoQ) 실적 추이** | {sec2_res['qoq_summary_str']} | {sec2_res['qoq_eps_detail']} |
| **가설 반증 임계치 (Kill-Switch)** | **{sec2_res['kill_switch_guide']}** | 미달 시 가설 즉시 [약화] 강등 |

### 📌 [코드 자동 생성 2] Phase 3 6열 시나리오 밸류에이션 표 (주 지표: {sec8_res['primary_metric']})
| 시나리오 | 확률 (고정) | 핵심 드라이버 가정 (AI 해석 영역) | 적용 멀티플 (코드 산출) | 적정주가 (🛡️대안 B / 🚀대안 A) | 현재가 대비 등락률 (🛡️대안 B / 🚀대안 A) |
| :--- | :---: | :--- | :--- | :--- | :--- |
| **Bull (호황 상단)** | **25%** | [AI가 전방 호황/가동률 상단 서술] | {sec8_res['primary_metric']} 상위 90% (대안A ×{sec3_res['bull_mult_a']:.2f}) | 🛡️ **{sec3_res['bull_auto']:,}원** / 🚀 **{sec3_res['bull_auto_a']:,}원** | 🛡️ **{u_up_b:+.1f}%** / 🚀 **{u_up_a:+.1f}%** |
| **Base (기본 적정)** | **50%** | [AI가 컨센서스/본업 유지 서술] | {sec8_res['primary_metric']} 상위 65% (대안A ×{sec3_res['base_mult_a']:.2f}) | 🛡️ **{sec3_res['base_auto']:,}원** / 🚀 **{sec3_res['base_auto_a']:,}원** | 🛡️ **{b_up_b:+.1f}%** / 🚀 **{b_up_a:+.1f}%** |
{tp1_row}| **Bear (하방 지지)** | **25%** | [AI가 전방 둔화/가동률 하락 서술] | 하방 지지선 (할증 0% 고정) | 🛡️ **{sec3_res['bear_auto']:,}원** / 🚀 **{sec3_res['bear_auto']:,}원** | 🛡️ **{d_dn:+.1f}%** / 🚀 **{d_dn:+.1f}%** |

### 📌 [코드 자동 생성 3] HTML 계산기 직결 태그 (플레이스홀더 0건)
{canonical_html_tag}
"""
    with open(auto_md_path, "w", encoding="utf-8") as mf:
        mf.write(auto_md)

    print(f"\n  [✅ 파이프라인 SSOT 저장 완료]:")
    print(f"    - 숫자 원본 JSON: {export_json_path}")
    print(f"    - 마크다운 자동 표: {auto_md_path}")
    return canonical_html_tag


def run_valuation_backtest(
    stock_code,
    years=10,
    rerating_date=None,
    corp_name=None,
    base_target=None,
    bull_target=None,
    scurve_override=None,
    dart_exposure=None,
    forward_eps=None,
    expected_dps=0.0,
    early_ignition=False,
    forward_eps_g=None,
    sector_type="A",
    non_op_assets=None,
    net_debt=0.0,
    distb_shares=0,
    total_shares=0,
    fcf=0.0,
    export_json=None,
):
    print("\n======================================================================")
    print(f"📊 [Korean Stock Analyzer v4.2] 4대 밸류에이션 지표 {years}년 풀사이클 백테스트 & SSOT 엔진")
    print(f"   종목코드: {stock_code} | 분석 기간: 최근 {years}년 | 업종유형: Type {sector_type}")
    print("======================================================================")

    bench = fetch_naver_official_metrics(stock_code)
    df_full_week = fetch_weekly_candles(stock_code, count=max(int(years * 52 + 30), 600))
    df_day = fetch_daily_candles(stock_code, count=150)

    if df_full_week.empty:
        print("[Error] 주봉 데이터를 가져올 수 없습니다.")
        return

    curr_price = (
        float(df_day["close"].iloc[-1])
        if not df_day.empty
        else (bench.get("close") or float(df_full_week["close"].iloc[-1]))
    )
    w_close = float(df_full_week["close"].iloc[-1])

    if bench:
        print("\n[1] 🏛️ 제도권 공식 벤치마크 (네이버 금융 / FnGuide / KRX 기준):")
        bps_str = f"{bench['bps']:,.0f}원" if bench.get("bps") else "N/A"
        pbr_str = f"{bench['pbr']:.2f}배" if bench.get("pbr") else "N/A"
        eps_str = f"{bench['eps']:,.0f}원" if bench.get("eps") else "N/A"
        per_str = f"{bench['per']:.2f}배" if bench.get("per") else "N/A"
        div_str = f"{bench['div_yield']:.2f}%" if bench.get("div_yield") else "N/A"
        dps_str = f"{bench['dps']:,.0f}원" if bench.get("dps") else "N/A"

        print(f"    - 단일 기준 주가(일봉 종가): {curr_price:,.0f}원 (참고 주봉 종가: {w_close:,.0f}원)")
        print(f"    - 공식 BPS:  {bps_str} (자사주 제외 보통주+우선주 실질 유통주식수 기준)")
        print(f"    - 공식 PBR:  {pbr_str}")
        print(f"    - 공식 EPS:  {eps_str} (지배주주 기본 주당순이익 기준)")
        print(f"    - 공식 PER:  {per_str}")
        print(f"    - 공식 배당: {div_str} (DPS: {dps_str})")

    start_date = (pd.Timestamp.now() - pd.DateOffset(years=years)).to_pydatetime()
    df = df_full_week[df_full_week["date"] >= start_date].reset_index(drop=True)
    if df.empty:
        df = df_full_week.copy()

    print(
        f"\n[2] 📈 수집된 주봉 데이터셋: {len(df)}개 캔들 ({df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')})"
    )
    print(
        f"    - {years}년 역사적 최저가(Trough): {df['close'].min():,.0f}원 ({df.loc[df['close'].idxmin(), 'date'].strftime('%Y-%m-%d')})"
    )
    print(
        f"    - {years}년 역사적 최고가(Peak):   {df['close'].max():,.0f}원 ({df.loc[df['close'].idxmax(), 'date'].strftime('%Y-%m-%d')})"
    )
    print(f"    - 최신 일봉 기준 주가:        {curr_price:,.0f}원")

    if bench.get("bps") and bench["bps"] > 0:
        calc_pbr = curr_price / bench["bps"]
        official_pbr = bench.get("pbr")
        print("\n[3] 🛡️ Sanity Check (유통 BPS & 우선주 희석 정합성 검증):")
        print(
            f"    - 단일 기준 주가({curr_price:,.0f}원) ÷ 공식 BPS({bench['bps']:,.0f}원) = PBR {calc_pbr:.2f}배"
        )
        if official_pbr is not None and official_pbr > 0:
            diff_pct = abs(calc_pbr - official_pbr) / official_pbr * 100
            if diff_pct > 5.0:
                print(
                    f"    ⚠️ [경고] 공식 PBR({official_pbr:.2f}배)과 오차 {diff_pct:.1f}% 발생! 분모(우선주 포함 유통주식수 또는 유상증자 신주) 매핑 확인 필수."
                )
            else:
                print(f"    ✅ [검증 통과] 제도권 공식 PBR({official_pbr:.2f}배)과 정합성 일치.")

    if rerating_date:
        print(f"\n[4] ⚡ 레짐 시프트(Regime Shift) 분기점 설정: {rerating_date}")
        pre_df = df[df["date"] < datetime.strptime(rerating_date, "%Y-%m-%d")]
        post_df = df[df["date"] >= datetime.strptime(rerating_date, "%Y-%m-%d")]
        if not pre_df.empty:
            print(
                f"    - Pre-Rerating (레거시): {len(pre_df)}주 ({pre_df['close'].min():,.0f}원 ~ {pre_df['close'].max():,.0f}원)"
            )
        if not post_df.empty:
            print(
                f"    - Post-Rerating (신사업): {len(post_df)}주 ({post_df['close'].min():,.0f}원 ~ {post_df['close'].max():,.0f}원)"
            )

    dart_ts = load_dart_timeseries(
        stock_code, corp_name=corp_name, stock_name_hint=bench.get("stock_name")
    )
    resolved_corp_name = corp_name or dart_ts.get("corp_name") or bench.get("stock_name") or stock_code

    # [v4.2 Step 1.5] Book Value Decomposition & SOTP 20% Check
    sotp_res = compute_step1_5_balance_sheet_sotp(
        curr_price,
        distb_shares,
        bench,
        dart_ts,
        non_op_assets_eok=non_op_assets,
        net_debt_eok=net_debt,
    )

    # [v4.2 Step 2.0] 4-Regime Change Check before applying valuation bands
    regime_res = compute_step2_0_regime_change_check(
        dart_ts, sotp_res, rerating_date=rerating_date
    )

    # [Phase 1 Section 8] 4-Metric Backtest & Spurious Filter
    sec8_res = compute_phase1_section8_metrics_ranking(
        stock_code, bench, df_full_week, dart_ts, curr_price
    )

    # [Phase 3 Section 2] Value Trap + QoQ + Falsification Kill-Switch
    sec2_res = compute_section2_value_trap(
        stock_code, bench, df_full_week, dart_ts, curr_price
    )

    # [Phase 3 Section 3] Stock Nature + Cyclical Peak-Multiple Trap Guard + Model B/A
    sec3_res = compute_section3_stock_nature_and_tp1(
        stock_code,
        resolved_corp_name,
        bench,
        df_full_week,
        df_day,
        sec2_res,
        curr_price,
        base_target=base_target,
        bull_target=bull_target,
        scurve_override=scurve_override,
        dart_exposure=dart_exposure,
        forward_eps=forward_eps,
        expected_dps=expected_dps,
        early_ignition=early_ignition,
        forward_eps_g=forward_eps_g,
        sector_type=sector_type,
        sotp_res=sotp_res,
    )

    # [v4.2 Step 3.5] Multi-Stage Risk Causal Chain Diagnosis
    risk_res = compute_risk_causal_chain_diagnosis(
        dart_ts, sotp_res, sec2_res, net_debt_eok=net_debt
    )

    # [Phase 3 Section 4] Master SOP Indicators & Dual Safety Pin
    sec4_res = compute_section4_master_sop_indicators(
        df_day, df_full_week, sec2_res, sec3_res
    )

    # [v4.2 Pipeline SSOT] Export valuation_metrics.json & auto_tables.md
    if not export_json and resolved_corp_name:
        default_sr_dir = os.path.join(
            r"e:\antigravity-work\my-skill\Stock_Research",
            str(resolved_corp_name),
            "2_Translated_DB",
        )
        if os.path.exists(os.path.dirname(default_sr_dir)):
            export_json = os.path.join(default_sr_dir, "valuation_metrics.json")

    if export_json:
        canonical_tag = export_pipeline_ssot(
            export_json,
            stock_code,
            resolved_corp_name,
            curr_price,
            distb_shares,
            total_shares,
            fcf,
            net_debt,
            bench,
            sotp_res,
            regime_res,
            sec8_res,
            sec2_res,
            sec3_res,
            risk_res,
            sec4_res,
        )
    else:
        canonical_tag = (
            f'<div id="dcf-calculator-root" data-price="{int(curr_price)}" '
            f'data-shares="{distb_shares or "[유통주식수]"}" data-total-shares="{total_shares or "[총발행주식수]"}" '
            f'data-fcf="{fcf or "[기준FCF억]"}" data-net-debt="{net_debt}" '
            f'data-bear="{sec3_res["bear_auto"]}" data-base="{sec3_res["base_auto"]}" data-bull="{sec3_res["bull_auto"]}" '
            f'data-base-a="{sec3_res["base_auto_a"]}" data-bull-a="{sec3_res["bull_auto_a"]}" '
            f'data-buy1="{sec4_res.get("buy1", 0)}" data-buy2="{sec4_res.get("buy2", 0)}" data-buy3="{sec4_res.get("buy3", 0)}" '
            f'data-stop-pin="{sec4_res.get("init_stop_pin", 0)}" data-poc-low="{sec4_res.get("poc_low", 0)}"></div>'
        )

    print("\n======================================================================")
    print("📋 백테스트 & Phase 3 마스터 SOP 자동 연산 완료 (코드=숫자 연산 / AI=맥락 해석 분리)")
    print(f"   • HTML 계산기 태그 (복사 전용):\n     {canonical_tag}")
    print("======================================================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Valuation Indicator Backtester & Pipeline SSOT Engine for Korean Stock Analyzer v4.2"
    )
    parser.add_argument("--code", type=str, required=True, help="Stock code (e.g. 093520)")
    parser.add_argument("--years", type=int, default=10, help="Backtest years (default: 10)")
    parser.add_argument("--rerating-date", type=str, default=None, help="Regime shift date (YYYY-MM-DD)")
    parser.add_argument("--corp-name", type=str, default=None, help="Optional corporation name in my-list (e.g. 하나머티리얼즈)")
    parser.add_argument("--sector-type", type=str, default="A", help="Sector type: A (Continuous/Cyclical), B (Order/Equipment), C (Platform/Agency), D (Brand/Consumer)")
    parser.add_argument("--base-target", type=float, default=None, help="Optional Base scenario target price (KRW, Model B)")
    parser.add_argument("--bull-target", type=float, default=None, help="Optional Bull scenario target price (KRW, Model B)")
    parser.add_argument("--dart-exposure", type=float, default=None, help="DART verified standalone new-tech revenue exposure %% (e.g. 17.5)")
    parser.add_argument("--forward-eps", type=float, default=None, help="Estimated annual/forward controlling EPS (KRW) to compute Forward BPS")
    parser.add_argument("--forward-eps-g", type=float, default=None, help="Estimated Forward EPS growth rate %% for PEG(0.8) dynamic rerating")
    parser.add_argument("--expected-dps", type=float, default=0.0, help="Expected annual DPS (KRW) for Forward BPS calculation")
    parser.add_argument("--non-op-assets", type=float, default=None, help="Non-operating assets (investment property, affiliate stakes) in 억 원 for Step 1.5 SOTP 20%% check")
    parser.add_argument("--net-debt", type=float, default=0.0, help="Net debt in 억 원 (+ for net debt, - for net cash)")
    parser.add_argument("--distb-shares", type=int, default=0, help="Floating shares (distb_stock_co) for SSOT HTML tag generation")
    parser.add_argument("--total-shares", type=int, default=0, help="Total issued shares (istc_totqy) for SSOT HTML tag generation")
    parser.add_argument("--fcf", type=float, default=0.0, help="Base FCF in 억 원 for SSOT HTML tag generation")
    parser.add_argument("--export-json", type=str, default=None, help="Explicit output path for valuation_metrics.json (Single Source of Truth)")
    parser.add_argument("--s-curve-phase2", action="store_true", help="Force enable S-Curve Phase 2 (+10%% Base / +12%% Bull) boost")
    parser.add_argument("--early-ignition", action="store_true", help="Enable DART Early Ignition S-Curve gate (+15%% Base / +20%% Bull)")
    parser.add_argument("--no-s-curve", action="store_true", help="Force disable S-Curve Phase 2 boost")

    args = parser.parse_args()
    sc_override = True if args.s_curve_phase2 else (False if args.no_s_curve else None)

    run_valuation_backtest(
        args.code,
        args.years,
        args.rerating_date,
        args.corp_name,
        args.base_target,
        args.bull_target,
        sc_override,
        dart_exposure=args.dart_exposure,
        forward_eps=args.forward_eps,
        expected_dps=args.expected_dps,
        early_ignition=args.early_ignition,
        forward_eps_g=args.forward_eps_g,
        sector_type=args.sector_type,
        non_op_assets=args.non_op_assets,
        net_debt=args.net_debt,
        distb_shares=args.distb_shares,
        total_shares=args.total_shares,
        fcf=args.fcf,
        export_json=args.export_json,
    )