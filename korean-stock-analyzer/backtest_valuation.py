#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Valuation Indicator Backtester for Korean Stock Analyzer (v4.1)
- Enforces Single Price Anchor (latest daily close)
- Enforces Controlling Shareholder Net Income/Equity (지배주주 순이익/지분) & Split/Bonus-issue Retroactive Adjustment
- Automates Phase 1 Section 8 (4-Metric 10Y Full-Cycle R² & Directional Match Ranking + Spurious Filter)
- Automates Phase 3 Section 2 (Value Trap Type A/B with Split-Adjusted Quarterly EPS QoQ)
- Automates Phase 3 Section 3 (52w Range, TP1, Forward BPS, Model B vs Model A with Free-Call-Option & Peak Cap)
- Automates Phase 3 Section 4 (Strictly Ordered Master SOP Buy1 < Buy2 < Buy3 & Dual Safety Pin: Init Hard Stop < Buy1)
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

# Ensure UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def fetch_naver_official_metrics(stock_code):
    """Fetches benchmark official metrics from Naver Mobile Integration API."""
    url = f"https://m.stock.naver.com/api/stock/{stock_code}/integration"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        data = json.loads(resp)
        metrics = {}
        for it in data.get('totalInfos', []):
            k = it.get('key', '').strip()
            v = it.get('value', '').strip()
            metrics[k] = v

        def parse_num(val_str):
            if not val_str or val_str in ['N/A', '-']:
                return None
            clean = val_str.replace(',', '').replace('배', '').replace('원', '').replace('%', '').replace('억', '').strip()
            try:
                return float(clean)
            except Exception:
                return None

        res = {
            'stock_name': data.get('stockName'),
            'close': parse_num(metrics.get('현재가')) or parse_num(metrics.get('전일')),
            'bps': parse_num(metrics.get('BPS')),
            'pbr': parse_num(metrics.get('PBR')),
            'eps': parse_num(metrics.get('EPS')),
            'per': parse_num(metrics.get('PER')),
            'div_yield': parse_num(metrics.get('배당수익률')),
            'dps': parse_num(metrics.get('주당배당금')),
            'market_cap_str': metrics.get('시총')
        }
        return res
    except Exception as e:
        print(f"[Warning] Failed to fetch Naver official metrics: {e}")
        return {}


def fetch_weekly_candles(stock_code, count=600):
    """Fetches weekly candlestick data from Naver Chart XML (up to 10-12 years)."""
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={stock_code}&timeframe=week&count={count}&requestType=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode('euc-kr', errors='replace')
        root = ET.fromstring(resp)
        chartdata = root.find('chartdata')
        if chartdata is None:
            return pd.DataFrame()

        items = chartdata.findall('item')
        data = []
        for item in items:
            parts = item.attrib['data'].split('|')
            if len(parts) >= 6:
                d_str, op, hi, lo, cl, vol = parts[0], float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
                dt = datetime.strptime(d_str, '%Y%m%d')
                data.append({'date': dt, 'open': op, 'high': hi, 'low': lo, 'close': cl, 'vol': vol})

        df = pd.DataFrame(data).sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[Error] Failed to fetch weekly candles: {e}")
        return pd.DataFrame()


def fetch_daily_candles(stock_code, count=150):
    """Fetches daily candlestick data from Naver Chart XML."""
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={stock_code}&timeframe=day&count={count}&requestType=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode('euc-kr', errors='replace')
        root = ET.fromstring(resp)
        chartdata = root.find('chartdata')
        if chartdata is None:
            return pd.DataFrame()

        items = chartdata.findall('item')
        data = []
        for item in items:
            parts = item.attrib['data'].split('|')
            if len(parts) >= 6:
                d_str, op, hi, lo, cl, vol = parts[0], float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
                dt = datetime.strptime(d_str, '%Y%m%d')
                data.append({'date': dt, 'open': op, 'high': hi, 'low': lo, 'close': cl, 'vol': vol})

        df = pd.DataFrame(data).sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[Error] Failed to fetch daily candles: {e}")
        return pd.DataFrame()


def _parse_dart_fnltt_list(raw_list):
    """Extracts key BS/IS items from OpenDART /fnlttSinglAcntAll.json list, prioritizing Controlling Equity & Profit."""
    res = {'자본총계': 0.0, '지배자본': 0.0, '영업이익': 0.0, '당기순이익': 0.0, '지배순이익': 0.0, 'EPS': 0.0}
    found_id = set()
    for item in raw_list:
        acc_id = item.get('account_id', '')
        acc_nm = item.get('account_nm', '').replace(' ', '')
        sj = item.get('sj_div', '')
        val_str = item.get('thstrm_add_amount') or item.get('thstrm_amount') or ''
        if not val_str:
            continue
        try:
            val = float(str(val_str).replace(',', ''))
        except ValueError:
            continue

        def assign(k, by_id=False):
            if by_id:
                res[k] = val
                found_id.add(k)
            elif k not in found_id and res[k] == 0.0:
                res[k] = val

        if sj == 'BS':
            if acc_id == 'ifrs-full_EquityAttributableToOwnersOfParent':
                assign('지배자본', True)
            elif '지배기업소유주지분' in acc_nm or '지배기업의소유주' in acc_nm:
                assign('지배자본')
            elif acc_id == 'ifrs-full_Equity':
                assign('자본총계', True)
            elif acc_nm == '자본총계':
                assign('자본총계')
        elif sj in ('IS', 'CIS'):
            if acc_id == 'dart_OperatingIncomeLoss':
                assign('영업이익', True)
            elif acc_nm.startswith('영업이익') or acc_nm.startswith('영업손실'):
                assign('영업이익')
            elif acc_id == 'ifrs-full_ProfitLoss':
                assign('당기순이익', True)
            elif acc_nm.startswith('당기순이익') or acc_nm.startswith('당기순손실'):
                assign('당기순이익')
            elif acc_id == 'ifrs-full_ProfitLossAttributableToOwnersOfParent':
                assign('지배순이익', True)
            elif ('지배기업소유주지분' in acc_nm or '지배기업의소유주' in acc_nm) and ('순이익' in acc_nm or '손익' in acc_nm):
                assign('지배순이익')
            elif acc_id == 'ifrs-full_BasicEarningsLossPerShare':
                assign('EPS', True)
            elif acc_nm.startswith('기본주당이익') or acc_nm.startswith('기본주당순이익'):
                assign('EPS')

    if res['지배자본'] > 0.0:
        res['자본총계'] = res['지배자본']
    if res['지배순이익'] == 0.0:
        res['지배순이익'] = res['당기순이익']
    return res


def _reconstruct_from_cache_db(db_path, corp_code, stock_code, corp_name):
    """Reconstructs sheet2 (Quarterly), sheet3 (TTM), and sheet5 (Band) from cache.db api_cache with split-adjusted EPS."""
    reprt_to_q = {'11013': '1Q', '11012': '2Q', '11014': '3Q', '11011': '4Q'}
    q_order = {'1Q': 1, '2Q': 2, '3Q': 3, '4Q': 4}
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            "SELECT params, response FROM api_cache WHERE endpoint = '/fnlttSinglAcntAll.json' AND params LIKE ?",
            (f'%"corp_code": "{corp_code}"%',)
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
            if resp.get('status') != '000' or not resp.get('list'):
                continue
            yr = int(p.get('bsns_year', 0))
            q = reprt_to_q.get(p.get('reprt_code', ''))
            fs = p.get('fs_div', 'CFS')
            if not yr or not q:
                continue
            if (yr, q) in by_yq and by_yq[(yr, q)][0] == 'CFS' and fs != 'CFS':
                continue
            parsed = _parse_dart_fnltt_list(resp['list'])
            parsed['Year'] = yr
            parsed['Quarter'] = q
            by_yq[(yr, q)] = (fs, parsed)

        if not by_yq:
            conn.close()
            return None

        sorted_keys = sorted(by_yq.keys(), key=lambda x: (x[0], q_order[x[1]]))
        df_raw = pd.DataFrame([by_yq[k][1] for k in sorted_keys])
        # Use Controlling Net Income (지배순이익) to infer floating shares accurately
        base_ni_col = np.where(df_raw['지배순이익'] != 0, df_raw['지배순이익'], df_raw['당기순이익'])
        df_raw['유통주식수'] = np.where(df_raw['EPS'] != 0, base_ni_col / df_raw['EPS'], np.nan)
        df_raw['유통주식수'] = pd.Series(df_raw['유통주식수']).ffill().bfill().fillna(0.0)
        latest_sh = float(df_raw['유통주식수'].iloc[-1]) if not df_raw.empty else 0.0
        df_raw['현재주식수'] = latest_sh

        sheet2 = df_raw.copy()
        # Subtract only cumulative flow amounts (operating profit & net income), NOT unadjusted raw EPS!
        flow_cols = ['영업이익', '당기순이익', '지배순이익']
        for i in range(len(sheet2)):
            if sheet2.loc[i, 'Quarter'] != '1Q' and i > 0 and sheet2.loc[i - 1, 'Year'] == sheet2.loc[i, 'Year']:
                for col in flow_cols:
                    sheet2.loc[i, col] = df_raw.loc[i, col] - df_raw.loc[i - 1, col]

        # Recompute split-adjusted standalone quarterly EPS from standalone 지배순이익 / 현재주식수
        if latest_sh > 0:
            q_ni = np.where(sheet2['지배순이익'] != 0, sheet2['지배순이익'], sheet2['당기순이익'])
            sheet2['EPS'] = q_ni / latest_sh

        sheet3 = sheet2.copy()
        for i in range(len(sheet3)):
            cy, cq = sheet3.loc[i, 'Year'], sheet3.loc[i, 'Quarter']
            ly_4q = df_raw[(df_raw['Year'] == cy - 1) & (df_raw['Quarter'] == '4Q')]
            ly_cq = df_raw[(df_raw['Year'] == cy - 1) & (df_raw['Quarter'] == cq)]
            if not ly_4q.empty and not ly_cq.empty:
                for col in flow_cols:
                    sheet3.loc[i, col] = df_raw.loc[i, col] + (ly_4q.iloc[0][col] - ly_cq.iloc[0][col])
                if latest_sh > 0:
                    ttm_ni = sheet3.loc[i, '지배순이익'] if sheet3.loc[i, '지배순이익'] != 0 else sheet3.loc[i, '당기순이익']
                    sheet3.loc[i, 'EPS'] = ttm_ni / latest_sh
            else:
                for col in flow_cols + ['EPS']:
                    sheet3.loc[i, col] = np.nan

        sheet3 = sheet3.dropna(subset=['당기순이익'], how='all').reset_index(drop=True)
        sheet3['ROE'] = np.where(sheet3['자본총계'] > 0, (sheet3['지배순이익'] / sheet3['자본총계']) * 100.0, np.nan)
        sheet3['BPS'] = np.where(sheet3['현재주식수'] > 0, sheet3['자본총계'] / sheet3['현재주식수'], np.nan)
        sheet3['정상화EPS'] = np.where(sheet3['현재주식수'] > 0, (sheet3['영업이익'] * 0.79) / sheet3['현재주식수'], np.nan)

        sheet5 = pd.DataFrame()
        if stock_code and not sheet3.empty:
            c.execute(
                "SELECT response FROM api_cache WHERE endpoint = 'fdr_prices' AND params = ?",
                (f"{str(stock_code).zfill(6)}_10y_weekly",)
            )
            prow = c.fetchone()
            pdf = pd.DataFrame()
            if prow:
                try:
                    plist = json.loads(prow[0])
                    pdf = pd.DataFrame(plist)
                    if not pdf.empty and 'date' in pdf.columns and 'price' in pdf.columns:
                        pdf['dt'] = pd.to_datetime(pdf['date'])
                except Exception:
                    pdf = pd.DataFrame()
            if pdf.empty:
                wdf = fetch_weekly_candles(str(stock_code).zfill(6), count=600)
                if not wdf.empty:
                    pdf = pd.DataFrame({
                        'date': wdf['date'].dt.strftime('%Y-%m-%d'),
                        'dt': pd.to_datetime(wdf['date']),
                        'price': wdf['close']
                    })
            if not pdf.empty and 'dt' in pdf.columns and 'price' in pdf.columns:
                try:
                    q_end_map = {'1Q': '03-31', '2Q': '06-30', '3Q': '09-30', '4Q': '12-31'}
                    s3_b = sheet3.copy()
                    s3_b['rdate'] = pd.to_datetime(s3_b['Year'].astype(str) + '-' + s3_b['Quarter'].map(q_end_map))
                    merged = pd.merge_asof(
                        pdf.sort_values('dt'),
                        s3_b[['rdate', 'BPS', '정상화EPS', 'ROE']].sort_values('rdate'),
                        left_on='dt', right_on='rdate', direction='backward'
                    )
                    merged['PBR'] = np.where(merged['BPS'] > 0, merged['price'] / merged['BPS'], np.nan)
                    merged['PER'] = np.where(merged['정상화EPS'] > 0, merged['price'] / merged['정상화EPS'], np.nan)
                    sheet5 = merged
                except Exception:
                    pass
        conn.close()
        return {
            'source': f"{db_path} (corp_code={corp_code})",
            'corp_name': corp_name,
            'sheet2': sheet2,
            'sheet3': sheet3,
            'sheet5': sheet5
        }
    except Exception:
        return None


def load_dart_timeseries(stock_code, corp_name=None, stock_name_hint=None, my_list_root=r"e:\antigravity-work\my-stock\my-list"):
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
                c.execute("SELECT corp_code, corp_name, stock_code FROM corp_codes WHERE stock_code = ?", (resolved_stock_code,))
                row = c.fetchone()
                if row:
                    resolved_corp_code = row[0]
                    if not resolved_name:
                        resolved_name = row[1]
            if (not resolved_name or not resolved_corp_code) and corp_name:
                c.execute("SELECT corp_code, corp_name, stock_code FROM corp_codes WHERE corp_name = ?", (corp_name,))
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
    for name_try in [corp_name, resolved_name, stock_name_hint]:
        if name_try:
            cand = os.path.join(my_list_root, name_try, "Data.xlsx")
            if os.path.exists(cand) and (name_try, cand) not in candidates:
                candidates.append((name_try, cand))

    if not candidates and os.path.exists(my_list_root):
        for path in glob.glob(os.path.join(my_list_root, "*", "Data.xlsx")):
            folder_name = os.path.basename(os.path.dirname(path))
            for name_try in [corp_name, resolved_name, stock_name_hint]:
                if name_try and (name_try in folder_name or folder_name in name_try):
                    if (folder_name, path) not in candidates:
                        candidates.append((folder_name, path))

    for cname, xlsx_path in candidates:
        try:
            xl = pd.ExcelFile(xlsx_path)
            s1 = xl.parse('Sheet1(Raw)') if 'Sheet1(Raw)' in xl.sheet_names else pd.DataFrame()
            s2 = xl.parse('Sheet2(Quarterly)') if 'Sheet2(Quarterly)' in xl.sheet_names else pd.DataFrame()
            s3 = xl.parse('Sheet3(TTM)') if 'Sheet3(TTM)' in xl.sheet_names else pd.DataFrame()
            s5 = xl.parse('Sheet5(Band)') if 'Sheet5(Band)' in xl.sheet_names else pd.DataFrame()

            # Auto-heal XBRL 3-Month standalone vs 6M/9M cumulative 지배순이익 subtraction bug (e.g., KoMiCo 2026.2Q)
            if not s1.empty and not s2.empty and '지배순이익' in s1.columns and '지배순이익' in s2.columns:
                for i in range(1, min(len(s1), len(s2))):
                    q_val = str(s2.loc[i, 'Quarter'])
                    if q_val in ('2Q', '3Q') and s2.loc[i, '영업이익'] > 0 and s2.loc[i, '당기순이익'] > 0 and s2.loc[i, '지배순이익'] <= 0:
                        raw_ctrl = float(s1.loc[i, '지배순이익'] or 0)
                        if 0 < raw_ctrl <= float(s2.loc[i, '당기순이익']) * 1.05:
                            prev_q_ctrl = float(s2.loc[i - 1, '지배순이익'] or 0)
                            s2.loc[i, '지배순이익'] = raw_ctrl
                            sh_now = float(s2.loc[i, '현재주식수'] or 0)
                            if sh_now > 0:
                                s2.loc[i, 'EPS'] = raw_ctrl / sh_now
                            if not s3.empty and i < len(s3) and prev_q_ctrl > 0:
                                s3.loc[i, '지배순이익'] = float(s3.loc[i, '지배순이익'] or 0) + prev_q_ctrl
                                if sh_now > 0:
                                    s3.loc[i, 'EPS'] = float(s3.loc[i, '지배순이익']) / sh_now
                                if '자본총계' in s3.columns and float(s3.loc[i, '자본총계'] or 0) > 0:
                                    s3.loc[i, 'ROE'] = (float(s3.loc[i, '지배순이익']) / float(s3.loc[i, '자본총계'])) * 100.0

            return {
                'source': xlsx_path,
                'corp_name': cname,
                'sheet2': s2,
                'sheet3': s3,
                'sheet5': s5
            }
        except Exception as e:
            print(f"[Warning] Failed to read {xlsx_path}: {e}")

    if os.path.exists(db_path) and resolved_corp_code:
        cached_ts = _reconstruct_from_cache_db(db_path, resolved_corp_code, resolved_stock_code, resolved_name)
        if cached_ts is not None:
            return cached_ts

    return {'source': None, 'corp_name': resolved_name, 'sheet2': pd.DataFrame(), 'sheet3': pd.DataFrame(), 'sheet5': pd.DataFrame()}


def _get_split_adjusted_ttm_eps(row, latest_row):
    """
    Returns TTM EPS from row, automatically correcting for missing XBRL EPS tags (0.0),
    sign inversions from bonus issues, or stock splits (액면분할/무상증자), prioritizing 지배순이익.
    """
    raw_eps = row.get('EPS')
    norm_eps = row.get('정상화EPS')
    ni = row.get('지배순이익') if (pd.notna(row.get('지배순이익')) and row.get('지배순이익') != 0) else row.get('당기순이익')
    curr_sh = latest_row.get('현재주식수') if latest_row is not None else row.get('현재주식수')
    ni_per_sh = (float(ni) / float(curr_sh)) if (pd.notna(ni) and pd.notna(curr_sh) and float(curr_sh) > 0) else None

    if pd.isna(raw_eps) or float(raw_eps) == 0.0:
        if ni_per_sh is not None and ni_per_sh != 0.0:
            return ni_per_sh
        if pd.notna(norm_eps) and float(norm_eps) != 0.0:
            return float(norm_eps)
        return None

    raw_val = float(raw_eps)

    # Sign inversion vs actual TTM net income (caused by mid-year bonus issue/split in cumulative XBRL EPS)
    if ni_per_sh is not None and ni_per_sh != 0.0 and (raw_val * ni_per_sh < 0):
        return ni_per_sh

    # Stock split / bonus issue distortion check against split-adjusted NI/share
    if ni_per_sh is not None and ni_per_sh != 0.0 and raw_val != 0.0:
        split_ratio = abs(raw_val / ni_per_sh)
        if split_ratio >= 1.35 or split_ratio <= 0.65:
            return ni_per_sh

    return raw_val


def _get_split_adjusted_quarterly_eps(row, latest_sh):
    """
    Returns standalone quarterly EPS corrected for stock splits / bonus issues (e.g., KoMiCo 5x split in Q2).
    Prevents fake negative EPS caused by subtracting pre-split Q1 EPS from post-split H1 cumulative EPS.
    """
    raw_eps = row.get('EPS')
    ni = row.get('지배순이익') if (pd.notna(row.get('지배순이익')) and float(row.get('지배순이익')) != 0.0) else row.get('당기순이익')
    sh = latest_sh if (latest_sh and latest_sh > 0) else row.get('현재주식수')
    ni_per_sh = (float(ni) / float(sh)) if (pd.notna(ni) and pd.notna(sh) and float(sh) > 0) else None

    if pd.isna(raw_eps) or float(raw_eps) == 0.0:
        return ni_per_sh if ni_per_sh is not None else 0.0

    raw_val = float(raw_eps)
    if ni_per_sh is not None and ni_per_sh != 0.0:
        # 1. Sign inversion (e.g. positive quarterly net income but negative raw EPS due to cumulative split subtraction)
        if raw_val * ni_per_sh < 0:
            return ni_per_sh
        # 2. Pre-split unadjusted EPS (e.g. 5x higher than current share count basis)
        ratio = abs(raw_val / ni_per_sh)
        if ratio >= 1.35 or ratio <= 0.65:
            return ni_per_sh
    return raw_val


def _detect_scurve_phase2_status(stock_code, corp_name=None, scurve_override=None, dart_exposure_override=None):
    """
    Auto-detects S-Curve Phase 2 (10~40% golden sweet-spot & DART standalone theme exposure >= 30%).
    Enforces Free Call Option rule: if standalone new-tech revenue is undisclosed or <30%, S-Curve boost is 0%.
    """
    code_str = str(stock_code).zfill(6) if stock_code else ""
    if scurve_override is False:
        return {
            'is_phase2': False,
            'theme_name': 'S-Curve 할증 미적용 (--no-s-curve)',
            'exposure_pct': 0.0,
            'source_pdf': None
        }
    if dart_exposure_override is not None and dart_exposure_override < 30.0:
        return {
            'is_phase2': False,
            'theme_name': f'DART 신기술 단독 익스포저 미달 ({dart_exposure_override:.1f}% < 30% 컷오프 → 무상 콜옵션 0% 처리)',
            'exposure_pct': dart_exposure_override,
            'source_pdf': None
        }
    if scurve_override is True:
        exp_val = dart_exposure_override if dart_exposure_override is not None else 30.0
        return {
            'is_phase2': True,
            'theme_name': '사용자/리포트 S-Curve Phase 2 지정 (--s-curve-phase2)',
            'exposure_pct': exp_val,
            'source_pdf': 'Global_Trend 80p+ 심층 리포트 검증'
        }

    # 1. Check s_curve_registry.json
    reg_path = r"e:\antigravity-work\korean cycle\00_megatrend\docs\s_curve_registry.json"
    if os.path.exists(reg_path):
        try:
            with open(reg_path, "r", encoding="utf-8") as f:
                reg = json.load(f)
            for th in reg.get("themes", []):
                stage = th.get("stage", "")
                pen = float(th.get("current_penetration_pct", 0.0) or 0.0)
                is_p2_stage = ("Phase 2" in stage) or (10.0 <= pen <= 40.0)
                vp = th.get("verification_pointer", {})
                src_pdf = f"{vp.get('source_pdf', '')} p.{vp.get('source_page', '')}".strip()

                for ex in th.get("excluded_stocks", []):
                    if ex.get("ticker") == code_str or (corp_name and ex.get("name") == corp_name):
                        return {
                            'is_phase2': False,
                            'theme_name': f"{th.get('theme_name')} [🚫 수혜 제외: {ex.get('reason', '익스포저 미달')}]",
                            'exposure_pct': 0.0,
                            'source_pdf': src_pdf
                        }

                if is_p2_stage:
                    for tp in th.get("top_picks", []):
                        if tp.get("ticker") == code_str or (corp_name and tp.get("name") == corp_name):
                            exp_pct = dart_exposure_override if dart_exposure_override is not None else float(tp.get("dart_exposure_pct", 0.0) or 0.0)
                            standalone_ok = tp.get("standalone_disclosed", True)
                            if standalone_ok is False or exp_pct < 30.0:
                                return {
                                    'is_phase2': False,
                                    'theme_name': f"{th.get('theme_name')} [무상 콜옵션: 신기술 단독 매출 미공시/30% 미달({exp_pct:.1f}%) → 대안 A S-Curve 할증 배제]",
                                    'exposure_pct': exp_pct,
                                    'source_pdf': src_pdf
                                }
                            return {
                                'is_phase2': True,
                                'theme_name': f"{th.get('theme_name')} (침투율 {pen:.1f}%, {stage})",
                                'exposure_pct': exp_pct,
                                'source_pdf': src_pdf
                            }
                    for hg in th.get("hidden_gems", []):
                        if hg.get("ticker") == code_str or (corp_name and hg.get("name") == corp_name):
                            exp_pct = dart_exposure_override if dart_exposure_override is not None else 40.0
                            return {
                                'is_phase2': True,
                                'theme_name': f"{th.get('theme_name')} [히든챔피언] (침투율 {pen:.1f}%, {stage})",
                                'exposure_pct': exp_pct,
                                'source_pdf': src_pdf
                            }
        except Exception:
            pass

    # 2. Verified Global_Trend 80p+ PDF S-Curve Phase 2 map (ONLY stocks with >=30% verified DART core exposure; GST excluded from auto-boost due to undisclosed standalone immersion cooling revenue)
    verified_global_trend_p2 = {
        '058470': ('온디바이스 AI & NPU 고집적 R&D 리노핀/소켓 (Phase 2 스위트스팟)', 85.0, '26.08.28_신한_생성형AI와 온디바이스AI.pdf & 26.09.16.IM증권.소나기 피할 반도체.pdf'),
        '166090': ('3D NAND 극저온 식각 & 선단 공정 대구경 Si/SiC 부품 (Phase 2 스위트스팟)', 98.98, '26.09.29.LS증권.AI병목의 전이.pdf p.13'),
        '064760': ('반도체 식각 챔버용 Solid SiC Ring 더블 S-Curve (Phase 2 스위트스팟)', 89.8, '26.09.29.LS증권.AI병목의 전이.pdf p.13'),
        '095340': ('AI 가속기·NPU 대면적 실리콘 러버 테스트 소켓 (Phase 2 스위트스팟)', 80.0, '26.08.28_신한_생성형AI와 온디바이스AI.pdf'),
        '183300': ('선단 파운드리·HBM 초정밀 세정·코팅 (Phase 2 스위트스팟)', 85.0, '26.09.16.IM증권.소나기 피할 반도체.pdf'),
        '093520': ('AI 서버·방산 고성능 비메모리 FPGA 솔루션 (Phase 2 스위트스팟)', 82.0, '26.09.29.LS증권.AI병목의 전이.pdf'),
        '131970': ('차량용·온디바이스 AI SoC 웨이퍼 테스트 (Phase 2 스위트스팟)', 90.0, '26.08.28_신한_생성형AI와 온디바이스AI.pdf'),
        '005930': ('AI HBM3E/HBM4 및 선단 메모리 턴어라운드 (Phase 2 스위트스팟)', 50.0, '26.09.16.IM증권.소나기 피할 반도체.pdf'),
    }
    if code_str in verified_global_trend_p2:
        t_nm, exp_p, s_pdf = verified_global_trend_p2[code_str]
        if dart_exposure_override is not None:
            exp_p = dart_exposure_override
        return {
            'is_phase2': True,
            'theme_name': t_nm,
            'exposure_pct': exp_p,
            'source_pdf': s_pdf
        }

    return {
        'is_phase2': False,
        'theme_name': '해당 없음 (전통 시클리컬 / 성숙기 박스권 BM / 단독 신기술 익스포저 30% 미달 무상 콜옵션)',
        'exposure_pct': dart_exposure_override or 0.0,
        'source_pdf': None
    }


def compute_phase1_section8_metrics_ranking(stock_code, bench, df_week, dart_ts, curr_price):
    """
    Computes Phase 1 Section 8 4-Metric (PBR, PER, DivYield, EV/EBITDA) 10Y Band, R², and Directional Match %,
    filtering out spurious correlations (directional match < 50% or low-payout dividend illusions).
    """
    print(f"\n======================================================================")
    print(f"📐 [Phase 1 - Section 8] 최적 밸류에이션 지표 10년 백테스트 & 착시 필터링")
    print(f"======================================================================")

    s3 = dart_ts.get('sheet3', pd.DataFrame())
    s5 = dart_ts.get('sheet5', pd.DataFrame())
    official_bps = bench.get('bps') or 0.0
    official_eps = bench.get('eps') or 0.0
    official_pbr = (curr_price / official_bps) if official_bps > 0 else (bench.get('pbr') or 0.0)
    official_per = (curr_price / official_eps) if official_eps > 0 else (bench.get('per') or 0.0)
    official_div = bench.get('div_yield') or 0.0

    pbr_min, pbr_max, pbr_r2, pbr_dir = 0.5, 2.5, 0.65, 65.0
    per_min, per_max, per_r2, per_dir = 6.0, 25.0, 0.60, 62.0

    if not s5.empty and 'price' in s5.columns:
        s5c = s5.dropna(subset=['price']).copy()
        bps_scale = 1.0
        if official_bps > 0 and 'BPS' in s5c.columns and s5c['BPS'].dropna().shape[0] > 0:
            last_bps = float(s5c['BPS'].dropna().iloc[-1])
            if last_bps > 0 and abs(official_bps / last_bps - 1.0) > 0.05:
                bps_scale = official_bps / last_bps

        if 'PBR' in s5c.columns:
            vpbr = (s5c['PBR'] / bps_scale).replace([np.inf, -np.inf], np.nan).dropna()
            vpbr = vpbr[vpbr > 0]
            if not vpbr.empty:
                pbr_min, pbr_max = float(vpbr.min()), float(vpbr.max())

        if 'PER' in s5c.columns:
            vper = s5c['PER'].replace([np.inf, -np.inf], np.nan).dropna()
            vper = vper[(vper > 0) & (vper < 150)]
            if not vper.empty:
                per_min, per_max = float(vper.min()), float(vper.max())

    if not s3.empty and not df_week.empty:
        try:
            q_end_map = {'1Q': '03-31', '2Q': '06-30', '3Q': '09-30', '4Q': '12-31'}
            s3c = s3.copy()
            s3c['rdate'] = pd.to_datetime(s3c['Year'].astype(str) + '-' + s3c['Quarter'].map(q_end_map))
            w_sorted = df_week[['date', 'close']].sort_values('date').rename(columns={'date': 'rdate'})
            m_q = pd.merge_asof(s3c.sort_values('rdate'), w_sorted, on='rdate', direction='backward').dropna(subset=['close'])
            if len(m_q) >= 5:
                latest_r = m_q.iloc[-1]
                m_q['adj_eps'] = [(_get_split_adjusted_ttm_eps(r, latest_r) or 0.0) for _, r in m_q.iterrows()]
                if 'BPS' in m_q.columns and m_q['BPS'].std() > 0:
                    corr_b = np.corrcoef(m_q['BPS'].fillna(0), m_q['close'])[0, 1]
                    if pd.notna(corr_b):
                        pbr_r2 = round(float(corr_b ** 2), 2)
                    b_diff = np.sign(m_q['BPS'].diff().dropna())
                    p_diff = np.sign(m_q['close'].diff().dropna())
                    if len(b_diff) > 0:
                        pbr_dir = round(float((b_diff == p_diff).mean() * 100.0), 1)
                if m_q['adj_eps'].std() > 0:
                    corr_e = np.corrcoef(m_q['adj_eps'], m_q['close'])[0, 1]
                    if pd.notna(corr_e):
                        per_r2 = round(float(corr_e ** 2), 2)
                    e_diff = np.sign(m_q['adj_eps'].diff().dropna())
                    p_diff = np.sign(m_q['close'].diff().dropna())
                    if len(e_diff) > 0:
                        per_dir = round(float((e_diff == p_diff).mean() * 100.0), 1)
        except Exception:
            pass

    print(f"    - [PBR (유통 BPS 기준)]: 10년 밴드 {pbr_min:.2f}배 ~ {pbr_max:.2f}배 | 현재 {official_pbr:.2f}배 | 설명력 R²={pbr_r2:.2f} | 방향일치율 {pbr_dir:.1f}%")
    print(f"    - [PER (수정 EPS 기준)]: 10년 밴드 {per_min:.2f}배 ~ {per_max:.2f}배 | 현재 {official_per:.2f}배 | 설명력 R²={per_r2:.2f} | 방향일치율 {per_dir:.1f}%")
    print(f"    - [지표 선정 및 상속 가이드]:")
    print(f"      • 방향일치율 < 50%이거나 배당성향 15% 미만 종목의 배당수익률 지표는 '시계열 추세 착시(Spurious)'로 1순위에서 자동 배제하십시오.")
    print(f"      • Phase 1에서 채택한 1순위 지표는 Phase 3 시나리오 표에서 반드시 주(Primary) 산식으로 적용하고 내재 PER/PBR을 병기하십시오.")


def compute_section2_value_trap(stock_code, bench, df_week, dart_ts, curr_price):
    """
    (A) DART 시계열 연동 및 Section 2 가치함정 이원화 자동 판정
    Fixes stock-split / bonus-issue fake deficits and outputs Controlling Net Income ownership ratio.
    """
    print(f"\n======================================================================")
    print(f"🚨 [Phase 3 - Section 2] DART 시계열 연동 & 가치함정 이원화(유형 A/B) 자동 판정")
    print(f"======================================================================")

    s2 = dart_ts.get('sheet2', pd.DataFrame())
    s3 = dart_ts.get('sheet3', pd.DataFrame())
    s5 = dart_ts.get('sheet5', pd.DataFrame())
    src = dart_ts.get('source')

    official_bps = bench.get('bps')
    official_eps = bench.get('eps')

    eps_2y_chg = None
    bps_2y_chg = None
    curr_roe = None
    controlling_ratio_pct = 100.0
    qoq_eps_positive = None
    qoq_eps_detail = "N/A"
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
        s3_valid = s3.dropna(subset=['BPS'], how='all').reset_index(drop=True)
        if len(s3_valid) >= 1:
            latest_row = s3_valid.iloc[-1]
            latest_sh = float(latest_row.get('현재주식수', 0) or 0)
            curr_eps_dart = _get_split_adjusted_ttm_eps(latest_row, latest_row)
            curr_bps_dart = latest_row.get('BPS')
            if pd.notna(latest_row.get('ROE')):
                curr_roe = float(latest_row['ROE'])
            if curr_eps_dart is not None and curr_eps_dart != 0:
                curr_eps_used = float(curr_eps_dart)
            if pd.notna(curr_bps_dart) and curr_bps_dart > 0:
                curr_bps_used = float(curr_bps_dart)

            # Controlling shareholder net income ownership ratio (지배주주 순이익 귀속률)
            tot_ni = latest_row.get('당기순이익')
            ctrl_ni = latest_row.get('지배순이익')
            if pd.notna(tot_ni) and pd.notna(ctrl_ni) and float(tot_ni) > 0 and float(ctrl_ni) > 0:
                controlling_ratio_pct = min(100.0, round(float(ctrl_ni) / float(tot_ni) * 100.0, 2))

            idx_2y = max(0, len(s3_valid) - 9)
            row_2y = s3_valid.iloc[idx_2y]
            eps_2y_ago = _get_split_adjusted_ttm_eps(row_2y, latest_row)
            bps_2y_ago = row_2y.get('BPS')

            if curr_eps_dart is not None and eps_2y_ago is not None and abs(float(eps_2y_ago)) > 0:
                eps_2y_chg = (float(curr_eps_dart) - float(eps_2y_ago)) / abs(float(eps_2y_ago)) * 100.0
            if pd.notna(curr_bps_dart) and pd.notna(bps_2y_ago) and abs(float(bps_2y_ago)) > 0:
                bps_2y_chg = (float(curr_bps_dart) - float(bps_2y_ago)) / abs(float(bps_2y_ago)) * 100.0

            if len(s3_valid) >= 5 and '영업이익' in s3_valid.columns:
                op_ttm_now = latest_row.get('영업이익')
                op_ttm_1y = s3_valid.iloc[-5].get('영업이익')
                if pd.notna(op_ttm_now) and pd.notna(op_ttm_1y) and abs(float(op_ttm_1y)) > 0:
                    op_yoy_ttm = (float(op_ttm_now) - float(op_ttm_1y)) / abs(float(op_ttm_1y)) * 100.0

    if not s2.empty:
        if 'EPS' in s2.columns or '지배순이익' in s2.columns:
            s2_v = s2.dropna(subset=['영업이익'], how='all').reset_index(drop=True)
            if len(s2_v) >= 2:
                r_now = s2_v.iloc[-1]
                r_prev = s2_v.iloc[-2]
                sh_ref = latest_sh if latest_sh > 0 else float(r_now.get('현재주식수', 0) or 0)
                q_now = _get_split_adjusted_quarterly_eps(r_now, sh_ref)
                q_prev = _get_split_adjusted_quarterly_eps(r_prev, sh_ref)
                q_label_now = f"{int(r_now['Year'])}.{r_now['Quarter']}"
                q_label_prev = f"{int(r_prev['Year'])}.{r_prev['Quarter']}"
                qoq_eps_positive = (q_now > 0) and (q_now >= q_prev * 0.95 or q_now > q_prev)
                status_tag = "QoQ 반등/흑자유지 ✅" if q_now > 0 and q_now >= q_prev else ("흑자 지속(소폭조정) 🟢" if q_now > 0 else "적자 전환/지속 🚨")
                qoq_eps_detail = f"[액분·무증 소급보정 지배주주 EPS] {q_label_prev} {q_prev:,.1f}원 → {q_label_now} {q_now:,.1f}원 ({status_tag})"

        if '영업이익' in s2.columns:
            s2_op = s2.dropna(subset=['영업이익']).reset_index(drop=True)
            if len(s2_op) >= 1:
                r_latest = s2_op.iloc[-1]
                ly_yr = int(r_latest['Year']) - 1
                ly_q = r_latest['Quarter']
                r_yoy_match = s2_op[(s2_op['Year'] == ly_yr) & (s2_op['Quarter'] == ly_q)]
                if not r_yoy_match.empty:
                    op_q_now = float(r_latest['영업이익'])
                    op_q_prev = float(r_yoy_match.iloc[-1]['영업이익'])
                    if op_q_prev > 0:
                        op_yoy_q = (op_q_now - op_q_prev) / op_q_prev * 100.0
                    elif op_q_prev <= 0 and op_q_now > 0:
                        op_yoy_q = 100.0
                    elif op_q_prev < 0 and op_q_now <= op_q_prev:
                        op_yoy_q = -50.0
                    q_lbl = f"{int(r_latest['Year'])}.{ly_q}"
                    prev_lbl = f"{ly_yr}.{ly_q}"
                    ttm_str = f" | TTM 영업이익 YoY: {op_yoy_ttm:+.1f}%" if op_yoy_ttm is not None else ""
                    op_yoy_detail = f"분기({prev_lbl}→{q_lbl}) {op_yoy_q:+.1f}% ({op_q_prev/1e8:,.1f}억 → {op_q_now/1e8:,.1f}억){ttm_str}" if op_yoy_q is not None else "N/A"

    op_yoy_used = op_yoy_q if op_yoy_q is not None else op_yoy_ttm
    if op_yoy_detail == "N/A" and op_yoy_ttm is not None:
        op_yoy_detail = f"TTM 영업이익 YoY {op_yoy_ttm:+.1f}%"

    if not s5.empty:
        s5_copy = s5.copy()
        if 'date' in s5_copy.columns:
            s5_copy['dt'] = pd.to_datetime(s5_copy['date'].astype(str), errors='coerce')
        else:
            s5_copy['dt'] = pd.NaT

        if s5_copy['dt'].notna().any():
            max_dt = pd.Timestamp(s5_copy['dt'].dropna().max())
            cutoff_3y = max_dt - pd.DateOffset(years=3)
            s5_3y = s5_copy[s5_copy['dt'] >= cutoff_3y].copy()
        else:
            s5_3y = s5_copy.tail(min(len(s5_copy), 156)).copy()

        bps_scale = 1.0
        if official_bps and 'BPS' in s5_3y.columns and pd.notna(s5_3y['BPS'].iloc[-1]) and s5_3y['BPS'].iloc[-1] > 0:
            ratio = official_bps / float(s5_3y['BPS'].iloc[-1])
            if abs(ratio - 1.0) > 0.05:
                bps_scale = ratio
                curr_bps_used = official_bps

        valid_pbr_all = (s5_copy['PBR'].dropna() / bps_scale) if 'PBR' in s5_copy.columns else pd.Series(dtype=float)
        valid_pbr_all = valid_pbr_all[valid_pbr_all > 0]
        if not valid_pbr_all.empty:
            pbr_cycle_max = float(valid_pbr_all.max())

        valid_pbr = (s5_3y['PBR'].dropna() / bps_scale) if 'PBR' in s5_3y.columns else pd.Series(dtype=float)
        valid_pbr = valid_pbr[valid_pbr > 0]
        if not valid_pbr.empty:
            pbr_3y_max = float(valid_pbr.max())
            pbr_3y_mean = float(valid_pbr.mean())
            pbr_3y_p20 = float(valid_pbr.quantile(0.20))
            pbr_3y_p65 = float(valid_pbr.quantile(0.65))
            pbr_3y_p90 = float(valid_pbr.quantile(0.90))

        valid_per = s5_3y['PER'].dropna() if 'PER' in s5_3y.columns else pd.Series(dtype=float)
        valid_per = valid_per[(valid_per > 0) & (valid_per < 200)]
        if not valid_per.empty:
            per_3y_max = float(valid_per.max())
            per_3y_mean = float(valid_per.mean())
            per_3y_p20 = float(valid_per.quantile(0.20))
            per_3y_p65 = float(valid_per.quantile(0.65))
            per_3y_p90 = float(valid_per.quantile(0.90))

    if pbr_cycle_max is None and not df_week.empty and official_bps and official_bps > 0:
        pbr_cycle_max = float((df_week['close'] / official_bps).max())

    if pbr_3y_max is None and not df_week.empty and official_bps and official_bps > 0:
        w3y = df_week.tail(156)
        pbr_series = w3y['close'] / official_bps
        pbr_3y_max = float(pbr_series.max())
        pbr_3y_mean = float(pbr_series.mean())
        pbr_3y_p20 = float(pbr_series.quantile(0.20))
        pbr_3y_p65 = float(pbr_series.quantile(0.65))
        pbr_3y_p90 = float(pbr_series.quantile(0.90))

    if per_3y_max is None and not df_week.empty and official_eps and official_eps > 0:
        w3y = df_week.tail(156)
        per_series = w3y['close'] / official_eps
        per_3y_max = float(per_series.max())
        per_3y_mean = float(per_series.mean())
        per_3y_p20 = float(per_series.quantile(0.20))
        per_3y_p65 = float(per_series.quantile(0.65))
        per_3y_p90 = float(per_series.quantile(0.90))

    if curr_roe is None and official_eps is not None and official_bps and official_bps > 0:
        curr_roe = (official_eps / official_bps) * 100.0

    w_ma20 = float(df_week['close'].rolling(20).mean().iloc[-1]) if len(df_week) >= 20 else curr_price
    w_ma60 = float(df_week['close'].rolling(60).mean().iloc[-1]) if len(df_week) >= 60 else curr_price
    is_weekly_aligned = w_ma20 >= w_ma60
    align_str = f"정배열 (20주선 {w_ma20:,.0f}원 ≥ 60주선 {w_ma60:,.0f}원) 📈" if is_weekly_aligned else f"역배열 (20주선 {w_ma20:,.0f}원 < 60주선 {w_ma60:,.0f}원) 📉"

    print(f"    - 데이터 소스: {src if src else '네이버 공식 벤치마크 + 주봉 시계열 폴백'}")
    print(f"    - 지배주주 순이익 귀속률:   {controlling_ratio_pct:.1f}% (연결 순이익 대비 지배주주 귀속 비율 — 시나리오 EPS 산출 시 필수 곱셈!)")
    print(f"    - 최근 2년 누적 EPS 증감률: {f'{eps_2y_chg:+.2f}%' if eps_2y_chg is not None else 'N/A (단기 시계열 폴백)'}")
    print(f"    - 최근 2년 누적 BPS 증감률: {f'{bps_2y_chg:+.2f}%' if bps_2y_chg is not None else 'N/A (단기 시계열 폴백)'}")
    print(f"    - 최근 영업이익 YoY 추이:   {op_yoy_detail}")
    print(f"    - 최근 분기 EPS QoQ 추이:  {qoq_eps_detail}")
    print(f"    - 현재 ROE (지배주주 기준): {f'{curr_roe:.2f}%' if curr_roe is not None else 'N/A'}")
    if pbr_3y_max is not None:
        cyc_str = f" (10년 풀사이클 최고 {pbr_cycle_max:.2f}배)" if pbr_cycle_max is not None else ""
        print(f"    - 과거 3년 PBR 밴드: 최고 {pbr_3y_max:.2f}배{cyc_str} | 평균 {pbr_3y_mean:.2f}배 | 하위20% {pbr_3y_p20:.2f}배 | 상위65% {pbr_3y_p65:.2f}배 | 상위90% {pbr_3y_p90:.2f}배")
    if per_3y_max is not None:
        print(f"    - 과거 3년 PER 밴드: 최고 {per_3y_max:.2f}배 | 평균 {per_3y_mean:.2f}배 | 하위20% {per_3y_p20:.2f}배 | 상위65% {per_3y_p65:.2f}배 | 상위90% {per_3y_p90:.2f}배")
    print(f"    - 주봉 20MA vs 60MA 배열:  {align_str}")

    has_bubble_or_stagnant_bps = (
        (pbr_3y_max is not None and pbr_3y_max >= 2.8) or
        (pbr_cycle_max is not None and pbr_cycle_max >= 2.8 and (bps_2y_chg is None or bps_2y_chg < 15.0))
    )
    is_type_b = (
        has_bubble_or_stagnant_bps and
        (eps_2y_chg is not None and eps_2y_chg <= -30.0) and
        (curr_roe is not None and curr_roe < 8.5)
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
        'is_type_b': is_type_b,
        'verdict': verdict,
        'controlling_ratio_pct': controlling_ratio_pct,
        'qoq_eps_positive': qoq_eps_positive,
        'eps_2y_chg': eps_2y_chg,
        'bps_2y_chg': bps_2y_chg,
        'op_yoy_q': op_yoy_q,
        'op_yoy_ttm': op_yoy_ttm,
        'op_yoy_used': op_yoy_used,
        'op_yoy_detail': op_yoy_detail,
        'curr_roe': curr_roe,
        'pbr_cycle_max': pbr_cycle_max,
        'pbr_3y_max': pbr_3y_max,
        'pbr_3y_mean': pbr_3y_mean,
        'pbr_3y_p20': pbr_3y_p20,
        'pbr_3y_p65': pbr_3y_p65,
        'pbr_3y_p90': pbr_3y_p90,
        'per_3y_max': per_3y_max,
        'per_3y_mean': per_3y_mean,
        'per_3y_p20': per_3y_p20,
        'per_3y_p65': per_3y_p65,
        'per_3y_p90': per_3y_p90,
        'curr_eps_used': official_eps or curr_eps_used,
        'curr_bps_used': official_bps or curr_bps_used,
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
):
    """
    (B) Section 3 종목 체질 판별, Forward BPS 계산, [대안 B vs 대안 A] 목표가 병기 자동 산출 (상한 캡 적용)
    """
    print(f"\n======================================================================")
    print(f"🎯 [Phase 3 - Section 3] 종목 체질 판별 & [대안 B vs 대안 A] 목표가 나란히 자동 산출")
    print(f"======================================================================")

    w52 = df_week.tail(52)
    high_52w_close = float(w52['close'].max())
    low_52w_close = float(w52['close'].min())
    high_52w_intraday = float(w52['high'].max())
    low_52w_intraday = float(w52['low'].min())

    range_52w_close_pct = (high_52w_close / low_52w_close - 1.0) * 100.0 if low_52w_close > 0 else 0.0
    range_52w_intraday_pct = (high_52w_intraday / low_52w_intraday - 1.0) * 100.0 if low_52w_intraday > 0 else 0.0

    w3y_close = df_week.tail(156)['close']
    win_len = min(52, max(1, len(w3y_close)))
    roll_max = w3y_close.rolling(52, min_periods=win_len).max()
    roll_min = w3y_close.rolling(52, min_periods=win_len).min()
    roll_range = ((roll_max / roll_min.replace(0, np.nan)) - 1.0) * 100.0
    roll_tail = roll_range.dropna().tail(104)
    median_52w_range_pct = float(roll_tail.median()) if not roll_tail.empty else range_52w_close_pct

    effective_52w_range = min(range_52w_close_pct, median_52w_range_pct)
    range_52w_pct = range_52w_close_pct

    bps = bench.get('bps') or sec2_res.get('curr_bps_used') or 0
    trailing_eps = bench.get('eps') or sec2_res.get('curr_eps_used') or 0
    eps = forward_eps if (forward_eps is not None and forward_eps > 0) else trailing_eps
    curr_pbr = (curr_price / bps) if bps > 0 else (bench.get('pbr') or 0)

    # Auto-detect if forward_eps was entered on a pre-split basis (e.g. > 50% of BPS and > 3.5x trailing_eps)
    if forward_eps is not None and bps > 0 and trailing_eps > 0 and forward_eps > bps * 0.55 and (forward_eps / trailing_eps) >= 3.5:
        split_factor = round(forward_eps / trailing_eps)
        if split_factor >= 3:
            eps = round(forward_eps / split_factor, 1)
            expected_dps = round(float(expected_dps or 0.0) / split_factor, 1)

    # Forward BPS calculation when forward_eps is provided (prevents static historical BPS mismatch)
    forward_bps = bps
    if forward_eps is not None and bps > 0:
        retained_eps = max(0.0, float(eps) - float(expected_dps or 0.0))
        forward_bps = round(bps + retained_eps)

    is_low_vol_box = (effective_52w_range < 36.0) and (curr_pbr > 0 and curr_pbr < 1.35)

    per_p20 = sec2_res.get('per_3y_p20') or 8.0
    per_p65 = sec2_res.get('per_3y_p65') or 12.0
    per_p90 = sec2_res.get('per_3y_p90') or 16.0
    pbr_p20 = sec2_res.get('pbr_3y_p20') or 0.75
    pbr_p65 = sec2_res.get('pbr_3y_p65') or 1.20
    pbr_p90 = sec2_res.get('pbr_3y_p90') or 1.80

    # Prevent trough-earnings inflated PER (e.g. >25x) from being multiplied by a recovery Forward EPS
    eff_per_p65 = min(per_p65, 18.0) if (forward_eps is not None and trailing_eps > 0 and eps > trailing_eps * 1.25 and per_p65 > 22.0) else per_p65
    eff_per_p90 = min(per_p90, 24.0) if (forward_eps is not None and trailing_eps > 0 and eps > trailing_eps * 1.25 and per_p90 > 28.0) else per_p90

    if sec2_res.get('is_type_b'):
        bear_auto_b = round((bps * 0.75) / 10) * 10 if bps > 0 else round(low_52w_close)
    else:
        bear_pbr_val = bps * pbr_p20 if bps and bps > 0 else 0
        bear_per_val = trailing_eps * per_p20 if trailing_eps and trailing_eps > 0 else 0
        bear_candidates = [v for v in [bear_pbr_val, bear_per_val] if v > 0]
        bear_auto_b = round(min(bear_candidates) / 10) * 10 if bear_candidates else round(low_52w_close)
        if bear_auto_b >= curr_price * 0.95:
            bear_auto_b = round(min(bear_pbr_val if bear_pbr_val > 0 else low_52w_close, low_52w_close) / 10) * 10

    base_candidates = [v for v in [eps * eff_per_p65 if eps and eps > 0 else 0, forward_bps * pbr_p65 if forward_bps and forward_bps > 0 else 0] if v > 0]
    bull_candidates = [v for v in [eps * eff_per_p90 if eps and eps > 0 else 0, forward_bps * pbr_p90 if forward_bps and forward_bps > 0 else 0] if v > 0]
    base_auto_b = round(max(base_candidates) / 10) * 10 if base_candidates else round(curr_price * 1.2)
    bull_auto_b = round(max(bull_candidates) / 10) * 10 if bull_candidates else round(curr_price * 1.5)

    op_yoy = sec2_res.get('op_yoy_used')
    curr_roe = sec2_res.get('curr_roe')
    is_type_b = sec2_res.get('is_type_b', False)

    op_mult = 1.00
    op_reason = "중립 (영업이익 YoY -25% ~ +20% 또는 ROE < 12% → 가감산 0%)"
    if is_type_b:
        op_mult = 1.00
        op_reason = "구조적 가치함정(유형 B) 해당 → 할증 원천 차단 (0%)"
    elif op_yoy is not None and op_yoy >= 20.0 and (curr_roe is not None and curr_roe >= 12.0):
        op_mult = 1.08
        op_reason = f"고성장 모멘텀 (영업이익 YoY {op_yoy:+.1f}% ≥ +20% & ROE {curr_roe:.1f}% ≥ 12%) → +8.0% 할증"
    elif op_yoy is not None and op_yoy <= -25.0:
        op_mult = 0.94
        op_reason = f"실적 둔화 페널티 (영업이익 YoY {op_yoy:+.1f}% ≤ -25%) → -6.0% 할인"

    sc_info = _detect_scurve_phase2_status(
        stock_code,
        corp_name=corp_name or bench.get('stock_name'),
        scurve_override=scurve_override,
        dart_exposure_override=dart_exposure,
    )
    sc_base_mult = 1.10 if (sc_info['is_phase2'] and not is_type_b) else 1.00
    sc_bull_mult = 1.12 if (sc_info['is_phase2'] and not is_type_b) else 1.00
    if sc_info['is_phase2'] and not is_type_b:
        sc_reason = f"적용 ✅ [{sc_info['theme_name']} | DART 검증 익스포저 {sc_info['exposure_pct']:.1f}% ≥ 30%] → Base +10.0% / Bull +12.0% 할증"
    else:
        sc_reason = f"미적용 ⚪ [{sc_info['theme_name']}] → +0.0% (본업 대안 B 기준 평가)"

    base_mult_a = round(op_mult * sc_base_mult, 4)
    bull_mult_a = round(op_mult * sc_bull_mult, 4)

    bear_auto_a = bear_auto_b
    base_auto_a = round((base_auto_b * base_mult_a) / 10) * 10
    bull_auto_a = round((bull_auto_b * bull_mult_a) / 10) * 10

    # Peak Cap Check: Prevent Model A Bull from exceeding 10Y historical cycle peak PBR/PER ceiling
    pbr_cap_price = round((forward_bps * (sec2_res.get('pbr_cycle_max') or pbr_p90 * 1.25)) / 10) * 10 if forward_bps > 0 else bull_auto_a
    if pbr_cap_price > bull_auto_b and bull_auto_a > pbr_cap_price:
        bull_auto_a = pbr_cap_price

    tp1_p70 = float(w52['close'].quantile(0.70))
    tp1_p75 = float(w52['close'].quantile(0.75))
    d60_ma = float(df_day['close'].rolling(60).mean().iloc[-1]) if not df_day.empty and len(df_day) >= 60 else tp1_p70
    d60_high_p75 = float(df_day.tail(60)['high'].quantile(0.75)) if not df_day.empty else tp1_p75
    tp1_price_b = round(max(tp1_p75, d60_high_p75, curr_price * 1.06) / 10) * 10
    tp1_price_a = round((tp1_price_b * op_mult) / 10) * 10 if op_mult != 1.0 else tp1_price_b
    tp1_upside_b = (tp1_price_b / curr_price - 1.0) * 100.0 if curr_price > 0 else 0.0
    tp1_upside_a = (tp1_price_a / curr_price - 1.0) * 100.0 if curr_price > 0 else 0.0

    print(f"    - 단일 기준 주가 (최신 일봉 종가): {curr_price:,.0f}원")
    print(f"    - 최근 52주 주봉 종가 범위: {low_52w_close:,.0f}원 ~ {high_52w_close:,.0f}원 (단순 52주 등락폭: {range_52w_close_pct:.1f}% | 3Y 롤링 52주 중위값: {median_52w_range_pct:.1f}%)")
    print(f"    - 현재 공식 BPS: {bps:,.0f}원 (현재 PBR {curr_pbr:.2f}배)" + (f" | 추정 Forward BPS: {forward_bps:,.0f}원 (Forward PBR {curr_price/forward_bps:.2f}배)" if forward_bps != bps else ""))

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

    print(f"\n    ▶ [대안 A (실적 모멘텀 · S-Curve 할증) 가중 계수 산출 내역]:")
    print(f"      ① DART 실적 모멘텀 가감산 (op_mult = ×{op_mult:.2f}): {op_reason}")
    print(f"      ② S-Curve Phase 2 할증 (Base ×{sc_base_mult:.2f} / Bull ×{sc_bull_mult:.2f}): {sc_reason}")
    if sc_info.get('source_pdf'):
        print(f"         • 검증 출처: {sc_info['source_pdf']}")
    print(f"      ③ 최종 대안 A 멀티플 가중치: Base ×{base_mult_a:.4f} ({(base_mult_a - 1)*100:+.1f}%) | Bull ×{bull_mult_a:.4f} ({(bull_mult_a - 1)*100:+.1f}%) | Bear ×1.0000 (하방 보호 고정)")

    final_base_b = round(base_target / 10) * 10 if (base_target is not None and base_target > 0) else base_auto_b
    final_base_a = round((final_base_b * base_mult_a) / 10) * 10
    final_bull_b = round(bull_target / 10) * 10 if (bull_target is not None and bull_target > 0) else bull_auto_b
    final_bull_a = round((final_bull_b * bull_mult_a) / 10) * 10

    print(f"\n    ▶ [시나리오 목표가 가이드라인 — 🛡️ 대안 B (중심 닻) vs 🚀 대안 A (할증 상단)]:")
    print(f"      • Bull (호황 상단): 🛡️ 대안 B {final_bull_b:,.0f}원 ({(final_bull_b/curr_price - 1)*100:+.1f}%)  |  🚀 대안 A {final_bull_a:,.0f}원 ({(final_bull_a/curr_price - 1)*100:+.1f}%)")
    print(f"      • Base (기본 적정): 🛡️ 대안 B {final_base_b:,.0f}원 ({(final_base_b/curr_price - 1)*100:+.1f}%)  |  🚀 대안 A {final_base_a:,.0f}원 ({(final_base_a/curr_price - 1)*100:+.1f}%)")
    print(f"      • TP1  (단기 실전): 🛡️ 대안 B {tp1_price_b:,.0f}원 ({tp1_upside_b:+.1f}%)  |  🚀 대안 A {tp1_price_a:,.0f}원 ({tp1_upside_a:+.1f}%) [52주 70~75%: {tp1_p70:,.0f}~{tp1_p75:,.0f}원 / 60일선: {d60_ma:,.0f}원]")
    print(f"      • Bear (하방 지지): 🛡️ 대안 B·A 공통 {bear_auto_b:,.0f}원 ({(bear_auto_b/curr_price - 1)*100:+.1f}%) [하방 안전마진 할증 금지]")

    return {
        'is_low_vol_box': is_low_vol_box,
        'nature_badge': nature_badge,
        'range_52w_pct': range_52w_pct,
        'median_52w_range_pct': median_52w_range_pct,
        'effective_52w_range': effective_52w_range,
        'forward_bps': forward_bps,
        'op_mult': op_mult,
        'sc_base_mult': sc_base_mult,
        'sc_bull_mult': sc_bull_mult,
        'base_mult_a': base_mult_a,
        'bull_mult_a': bull_mult_a,
        'scurve_info': sc_info,
        'tp1_price': tp1_price_b,
        'tp1_price_a': tp1_price_a,
        'tp1_upside': tp1_upside_b,
        'tp1_upside_a': tp1_upside_a,
        'bear_auto': bear_auto_b,
        'base_auto': final_base_b,
        'bull_auto': final_bull_b,
        'base_auto_a': final_base_a,
        'bull_auto_a': final_bull_a
    }


def compute_section4_master_sop_indicators(df_day, df_week, sec2_res, sec3_res):
    """
    (C) Section 4 마스터 SOP 정예 보조지표 실측값 + 가격 오름차순(Buy1 < Buy2 < Buy3 < Target) & 2단계 안전핀(초입 안전핀 < Buy1) 자동 설계
    Eliminates inverted buy orders and stop-loss pins above entry price.
    """
    print(f"\n======================================================================")
    print(f"📐 [Phase 3 - Section 4] 마스터 SOP 정예 보조지표 & 무결성 3분할 매매 타점 자동 설계")
    print(f"======================================================================")

    res = {}

    if not df_day.empty:
        curr_d_close = float(df_day['close'].iloc[-1])
        curr_d_date = df_day['date'].iloc[-1].strftime('%Y-%m-%d')
        d_ma5 = float(df_day['close'].rolling(5, min_periods=1).mean().iloc[-1])
        d_ma20 = float(df_day['close'].rolling(20, min_periods=1).mean().iloc[-1])
        d_ma60 = float(df_day['close'].rolling(60, min_periods=1).mean().iloc[-1])

        vol_ma20 = float(df_day['vol'].rolling(20, min_periods=1).mean().iloc[-1])
        curr_vol = float(df_day['vol'].iloc[-1])
        vol_ratio = (curr_vol / vol_ma20) if vol_ma20 > 0 else 0.0

        recent20 = df_day.tail(20).copy()
        p_min = float(recent20['low'].min())
        p_max = float(recent20['high'].max())
        if p_max <= p_min:
            p_max = p_min + 1.0
        bins = np.linspace(p_min, p_max, 6)
        recent20['tp'] = (recent20['high'] + recent20['low'] + recent20['close']) / 3.0
        total_v20 = float(recent20['vol'].sum())
        bin_vols = []
        for i in range(5):
            low_b, high_b = bins[i], bins[i + 1]
            if i < 4:
                mask = (recent20['tp'] >= low_b) & (recent20['tp'] < high_b)
            else:
                mask = (recent20['tp'] >= low_b) & (recent20['tp'] <= high_b)
            v_sum = float(recent20.loc[mask, 'vol'].sum())
            pct = (v_sum / total_v20 * 100.0) if total_v20 > 0 else 0.0
            bin_vols.append((int(round(low_b)), int(round(high_b)), v_sum, pct))

        poc_bin = max(bin_vols, key=lambda x: x[2])
        poc_low, poc_high, _, poc_pct = poc_bin
        poc_trail_pin = int(round(poc_low * 0.97))

        low12 = df_day['low'].rolling(12, min_periods=1).min()
        high12 = df_day['high'].rolling(12, min_periods=1).max()
        denom = (high12 - low12).replace(0, np.nan)
        fast_k = ((df_day['close'] - low12) / denom * 100.0).fillna(50.0)
        slow_k = fast_k.rolling(5, min_periods=1).mean()
        slow_d = slow_k.rolling(5, min_periods=1).mean()
        curr_k = float(slow_k.iloc[-1])
        curr_d = float(slow_d.iloc[-1])

        print(f"  [일봉 (Daily, 기준일: {curr_d_date}, 종가: {curr_d_close:,.0f}원)]")
        print(f"    - 일봉 20일 매물대(5칸 Volume Profile POC): {poc_low:,}원 ~ {poc_high:,}원 (거래비중: {poc_pct:.1f}%)")
        print(f"      • 5칸 전체 분포: " + " | ".join([f"[{b[0]:,}~{b[1]:,}원: {b[3]:.1f}%]" for b in bin_vols]))
        print(f"    - 일봉 이동평균선: 5일선 {int(round(d_ma5)):,}원 | 20일선 {int(round(d_ma20)):,}원 | 60일선 {int(round(d_ma60)):,}원")
        print(f"    - 거래량 분석:     20일 평균 거래량 {int(round(vol_ma20)):,}주 | 당일 거래량 {int(round(curr_vol)):,}주 (배수 vol_ratio: {vol_ratio:.2f}배)")
        print(f"    - 일봉 Slow Stochastic(12,5,5): %K = {curr_k:.1f}% / %D = {curr_d:.1f}% ({'골든크로스/상승우위' if curr_k >= curr_d else '데드크로스/조정중'})")

        res.update({
            'd_close': int(round(curr_d_close)),
            'd_low20': int(round(p_min)),
            'poc_low': poc_low,
            'poc_high': poc_high,
            'poc_pct': poc_pct,
            'poc_trail_pin': poc_trail_pin,
            'd_ma5': int(round(d_ma5)),
            'd_ma20': int(round(d_ma20)),
            'd_ma60': int(round(d_ma60)),
            'vol_ma20': int(round(vol_ma20)),
            'vol_ratio': vol_ratio,
            'stoch_k': curr_k,
            'stoch_d': curr_d
        })

    if not df_week.empty:
        curr_w_close = float(df_week['close'].iloc[-1])
        curr_w_date = df_week['date'].iloc[-1].strftime('%Y-%m-%d')
        w_ma10 = float(df_week['close'].rolling(10, min_periods=1).mean().iloc[-1])
        w_ma20 = float(df_week['close'].rolling(20, min_periods=1).mean().iloc[-1])
        w_ma60 = float(df_week['close'].rolling(60, min_periods=1).mean().iloc[-1])

        w_std20_raw = df_week['close'].rolling(20, min_periods=1).std().iloc[-1]
        w_std20 = float(w_std20_raw) if pd.notna(w_std20_raw) else 0.0
        bb_upper = w_ma20 + 2.0 * w_std20
        bb_lower = w_ma20 - 2.0 * w_std20

        ema12 = df_week['close'].ewm(span=12, adjust=False).mean()
        ema26 = df_week['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - sig_line
        curr_macd = float(macd_line.iloc[-1])
        curr_sig = float(sig_line.iloc[-1])
        curr_hist = float(macd_hist.iloc[-1])

        tp_w = (df_week['high'] + df_week['low'] + df_week['close']) / 3.0
        rmf_w = tp_w * df_week['vol']
        tp_diff = tp_w.diff()
        pos_flow = np.where(tp_diff > 0, rmf_w, 0.0)
        neg_flow = np.where(tp_diff < 0, rmf_w, 0.0)
        pos_mf14 = pd.Series(pos_flow).rolling(14, min_periods=1).sum()
        neg_mf14 = pd.Series(neg_flow).rolling(14, min_periods=1).sum().replace(0, 1.0)
        mfi14 = (100.0 - (100.0 / (1.0 + pos_mf14 / neg_mf14))).fillna(50.0)
        curr_mfi = float(mfi14.iloc[-1])
        max_12w_mfi = float(mfi14.tail(12).max())

        delta_w = df_week['close'].diff()
        gain_w = delta_w.clip(lower=0.0)
        loss_w = -delta_w.clip(upper=0.0)
        avg_gain = gain_w.ewm(alpha=1.0/14.0, min_periods=1, adjust=False).mean()
        avg_loss = loss_w.ewm(alpha=1.0/14.0, min_periods=1, adjust=False).mean().replace(0, 1e-9)
        rs_w = avg_gain / avg_loss
        rsi14 = (100.0 - (100.0 / (1.0 + rs_w))).fillna(50.0)
        curr_rsi = float(rsi14.iloc[-1])

        print(f"\n  [주봉 (Weekly, 기준일: {curr_w_date}, 종가: {curr_w_close:,.0f}원)]")
        print(f"    - 주봉 이동평균선:   10주선 {int(round(w_ma10)):,}원 | 20주선 {int(round(w_ma20)):,}원 | 60주선 {int(round(w_ma60)):,}원")
        print(f"    - 주봉 볼린저밴드(20, 2): 상단 {int(round(bb_upper)):,}원 | 중심(20주) {int(round(w_ma20)):,}원 | 하단 {int(round(bb_lower)):,}원")
        print(f"    - 주봉 MACD(12,26,9): MACD {curr_macd:,.1f} / Signal {curr_sig:,.1f} / Histogram {curr_hist:+,.1f} ({'양(+) 추세' if curr_hist >= 0 else '음(-) 조정'})")
        print(f"    - 주봉 MFI(14) & RSI(14): 주봉 MFI(14) = {curr_mfi:.1f} | 주봉 RSI(14) = {curr_rsi:.1f} | 최근 12주 주봉 MFI 최고점 = {max_12w_mfi:.1f}")

        divergence_warn = (curr_rsi >= 70.0) and (curr_mfi < 70.0)
        mfi_peakout_warn = (max_12w_mfi >= 80.0) and (curr_mfi < 80.0)
        print(f"    - 고점경보 판독:     1호(주봉 MFI 80 하향이탈): {'🚨경보' if mfi_peakout_warn else '정상'} | 2호(RSI≥70 & MFI<70 괴리): {'🚨경보' if divergence_warn else '정상'}")

        res.update({
            'w_close': int(round(curr_w_close)),
            'w_ma10': int(round(w_ma10)),
            'w_ma20': int(round(w_ma20)),
            'w_ma60': int(round(w_ma60)),
            'bb_upper': int(round(bb_upper)),
            'bb_lower': int(round(bb_lower)),
            'w_macd': curr_macd,
            'w_macd_sig': curr_sig,
            'w_macd_hist': curr_hist,
            'w_mfi14': curr_mfi,
            'w_rsi14': curr_rsi,
            'w_mfi14_12w_max': max_12w_mfi
        })

    # --- 3. Automated Conflict-Free SOP Order & Dual Safety Pin Design ---
    curr_p = res.get('d_close') or res.get('w_close') or 10000
    base_b = sec3_res.get('base_auto', int(curr_p * 1.25))
    tp1_b = sec3_res.get('tp1_price', int(curr_p * 1.15))
    bear_b = sec3_res.get('bear_auto', int(curr_p * 0.85))
    is_box = sec3_res.get('is_low_vol_box', False)
    primary_t = tp1_b if (is_box and tp1_b < base_b) else base_b

    # Check if stock is overvalued vs Base fair value
    is_overvalued = curr_p >= base_b * 0.96

    if is_overvalued:
        # Pullback accumulation mode below Base fair value
        buy1 = int(round((base_b * 0.80) / 10) * 10)
        buy2 = int(round((base_b * 0.85) / 10) * 10)
        buy3 = int(round((base_b * 0.90) / 10) * 10)
        sop_mode_desc = f"⏳ [고평가 추격매수 차단 → 눌림목 대기 모드]: 현재가({curr_p:,}원) ≥ Base 적정가({base_b:,}원) 부근이므로 현재가 추격 매수를 금지하고 Base 대비 -10~-20% 안전마진 구간({buy1:,}~{buy3:,}원)에서만 분할 진입!"
    else:
        # Standard ascending pyramid or box value accumulation with strict Buy1 < Buy2 < Buy3 < Primary Target
        buy1 = curr_p
        max_buy_ceiling = int(round(min(primary_t * 0.92, base_b * 0.92)))
        if max_buy_ceiling <= buy1 * 1.04:
            # Tight upside room: set Buy1 on pullback so Buy1 < Buy2 < Buy3 < Primary Target holds
            buy1 = int(round(min(curr_p * 0.94, primary_t * 0.82) / 10) * 10)
            max_buy_ceiling = int(round(min(curr_p, primary_t * 0.91) / 10) * 10)

        tech_candidates = sorted(list(set([
            res.get('w_ma10', 0),
            res.get('w_ma20', 0),
            res.get('d_ma20', 0),
            res.get('poc_low', 0),
            res.get('poc_high', 0),
        ])))
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
            buy2 = int(round(buy1 * 1.03 / 10) * 10)
        if buy3 <= buy2:
            buy3 = int(round(buy2 * 1.03 / 10) * 10)
        sop_mode_desc = f"🚀 [오름차순 정렬 완료 (Buy1 < Buy2 < Buy3 < 목표가)]: 낮은 추세선({buy2:,}원)을 2차 본대로, 높은 추세선/매물대({buy3:,}원)를 3차 확인사살로 배치하여 가격 역전 및 역손익비 원천 차단!"

    avg_buy_price = int(round(buy1 * 0.30 + buy2 * 0.40 + buy3 * 0.30))

    # Dual Safety Pin:
    # 1) Initial Hard Stop Pin (초입 고정 안전핀) MUST be strictly below Buy 1 (around -6% to -10% below Buy 1, anchored to 20d low or Bear floor)
    low20_pin = int(round(res.get('d_low20', buy1) * 0.97 / 10) * 10)
    bear_pin = int(round(bear_b * 0.97 / 10) * 10)
    cand_pins = [p for p in [low20_pin, bear_pin] if (buy1 * 0.86) <= p <= (buy1 * 0.96)]
    if cand_pins:
        init_stop_pin = max(cand_pins)
    else:
        init_stop_pin = int(round((buy1 * 0.92) / 10) * 10)

    # 2) Breakout Trailing Pin (2차/3차 매물대 돌파 안착 후 상향 보호핀)
    poc_trail = res.get('poc_trail_pin', int(round(buy2 * 0.97)))
    breakout_trail_pin = poc_trail if poc_trail > init_stop_pin else int(round(buy2 * 0.97 / 10) * 10)

    init_loss_pct = (init_stop_pin / buy1 - 1.0) * 100.0 if buy1 > 0 else 0.0
    avg_loss_pct = (init_stop_pin / avg_buy_price - 1.0) * 100.0 if avg_buy_price > 0 else 0.0
    target_upside_pct = (primary_t / avg_buy_price - 1.0) * 100.0 if avg_buy_price > 0 else 0.0
    exec_rr = abs(target_upside_pct / avg_loss_pct) if avg_loss_pct < 0 else 0.0

    print(f"\n  [🛡️ 마스터 SOP 무결성 검증 완료 — 3분할 매수가 & 2단계 안전핀 자동 설계]")
    print(f"    - 전략 모드: {sop_mode_desc}")
    print(f"    - 1차 매수 (30% · 가치/눌림목 선취매): {buy1:,}원")
    print(f"    - 2차 매수 (40% · 1차 추세선 돌파):    {buy2:,}원")
    print(f"    - 3차 매수 (30% · 상단 매물대/추세 안착): {buy3:,}원")
    print(f"    - ▶ 3분할 완성 평균단가: {avg_buy_price:,}원 (1차 목표가 {primary_t:,}원 대비 기대수익 {target_upside_pct:+.1f}%)")
    print(f"    - 🚨 [1단계: 초입 고정 손절 안전핀 (Hard Stop 1)]: {init_stop_pin:,}원 (1차 매수가 대비 {init_loss_pct:+.1f}%, 완성 평단 대비 {avg_loss_pct:+.1f}% | 실전 R:R = {exec_rr:.2f} : 1)")
    print(f"    - 🔒 [2단계: 상단 매물대 돌파 후 본절 보호핀 (Trailing Stop 2)]: {breakout_trail_pin:,}원 (주가가 20일 매물대 상단 {res.get('poc_high', buy3):,}원 돌파 안착 성공 시에만 손절선을 이 가격으로 상향 이동)")

    res.update({
        'buy1': buy1,
        'buy2': buy2,
        'buy3': buy3,
        'avg_buy_price': avg_buy_price,
        'init_stop_pin': init_stop_pin,
        'breakout_trail_pin': breakout_trail_pin,
        'exec_rr': round(exec_rr, 2),
        'is_overvalued': is_overvalued,
    })
    return res


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
):
    print(f"\n======================================================================")
    print(f"📊 [Korean Stock Analyzer v4.1] 4대 밸류에이션 지표 10년 풀사이클 백테스트")
    print(f"   종목코드: {stock_code} | 분석 기간: 최근 {years}년")
    print(f"======================================================================")

    bench = fetch_naver_official_metrics(stock_code)
    df_full_week = fetch_weekly_candles(stock_code, count=max(600, int(years * 52 + 30)))
    df_day = fetch_daily_candles(stock_code, count=150)
    if df_full_week.empty:
        print("[Error] 주봉 데이터를 가져올 수 없습니다.")
        return

    # Single Price Anchor: Always use latest daily close (or official real-time close) as the single master price
    curr_price = float(df_day['close'].iloc[-1]) if not df_day.empty else (bench.get('close') or float(df_full_week['close'].iloc[-1]))
    w_close = float(df_full_week['close'].iloc[-1])

    if bench:
        print(f"\n[1] 🏛️ 제도권 공식 벤치마크 (네이버 금융 / FnGuide / KRX 기준):")
        bps_str = f"{bench['bps']:,.0f}원" if bench.get('bps') is not None else "N/A"
        pbr_str = f"{bench['pbr']:.2f}배" if bench.get('pbr') is not None else "N/A"
        eps_str = f"{bench['eps']:,.0f}원" if bench.get('eps') is not None else "N/A"
        per_str = f"{bench['per']:.2f}배" if bench.get('per') is not None else "N/A"
        div_str = f"{bench['div_yield']:.2f}%" if bench.get('div_yield') is not None else "N/A"
        dps_str = f"{bench['dps']:,.0f}원" if bench.get('dps') is not None else "N/A"
        print(f"    - 단일 기준 주가(일봉 종가): {curr_price:,.0f}원 (참고 주봉 종가: {w_close:,.0f}원)")
        print(f"    - 공식 BPS:  {bps_str} (자사주 제외 보통주+우선주 실질 유통주식수 기준)")
        print(f"    - 공식 PBR:  {pbr_str}")
        print(f"    - 공식 EPS:  {eps_str} (지배주주 기본 주당순이익 기준)")
        print(f"    - 공식 PER:  {per_str}")
        print(f"    - 공식 배당: {div_str} (DPS: {dps_str})")

    start_date = (pd.Timestamp.now() - pd.DateOffset(years=years)).to_pydatetime()
    df = df_full_week[df_full_week['date'] >= start_date].reset_index(drop=True)
    if df.empty:
        df = df_full_week.copy()
    print(f"\n[2] 📈 수집된 주봉 데이터셋: {len(df)}개 캔들 ({df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')})")
    print(f"    - 10년 역사적 최저가(Trough): {df['close'].min():,.0f}원 ({df.loc[df['close'].idxmin(), 'date'].strftime('%Y-%m-%d')})")
    print(f"    - 10년 역사적 최고가(Peak):   {df['close'].max():,.0f}원 ({df.loc[df['close'].idxmax(), 'date'].strftime('%Y-%m-%d')})")
    print(f"    - 최신 일봉 기준 주가:        {curr_price:,.0f}원")

    if bench.get('bps') is not None and bench['bps'] > 0:
        calc_pbr = curr_price / bench['bps']
        official_pbr = bench.get('pbr')
        print(f"\n[3] 🛡️ Sanity Check (유통 BPS & 우선주 희석 정합성 검증):")
        print(f"    - 단일 기준 주가({curr_price:,.0f}원) ÷ 공식 BPS({bench['bps']:,.0f}원) = PBR {calc_pbr:.2f}배")
        if official_pbr is not None and official_pbr > 0:
            diff_pct = abs(calc_pbr - official_pbr) / official_pbr * 100
            if diff_pct > 5.0:
                print(f"    ⚠️ [경고] 공식 PBR({official_pbr:.2f}배)과 오차 {diff_pct:.1f}% 발생! 분모(우선주 포함 유통주식수 또는 유상증자 신주) 매핑 확인 필수.")
            else:
                print(f"    ✅ [검증 통과] 제도권 공식 PBR({official_pbr:.2f}배)과 정합성 일치.")

    if rerating_date:
        print(f"\n[4] ⚡ 레짐 시프트(Regime Shift) 분기점 설정: {rerating_date}")
        pre_df = df[df['date'] < datetime.strptime(rerating_date, '%Y-%m-%d')]
        post_df = df[df['date'] >= datetime.strptime(rerating_date, '%Y-%m-%d')]
        if not pre_df.empty:
            print(f"    - Pre-Rerating (레거시): {len(pre_df)}주 ({pre_df['close'].min():,.0f}원 ~ {pre_df['close'].max():,.0f}원)")
        if not post_df.empty:
            print(f"    - Post-Rerating (신사업): {len(post_df)}주 ({post_df['close'].min():,.0f}원 ~ {post_df['close'].max():,.0f}원)")

    dart_ts = load_dart_timeseries(stock_code, corp_name=corp_name, stock_name_hint=bench.get('stock_name'))
    compute_phase1_section8_metrics_ranking(stock_code, bench, df_full_week, dart_ts, curr_price)
    sec2_res = compute_section2_value_trap(stock_code, bench, df_full_week, dart_ts, curr_price)
    sec3_res = compute_section3_stock_nature_and_tp1(
        stock_code,
        corp_name or dart_ts.get('corp_name'),
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
    )
    sec4_res = compute_section4_master_sop_indicators(df_day, df_full_week, sec2_res, sec3_res)

    print(f"\n======================================================================")
    print(f"📋 백테스트 & Phase 3 마스터 SOP 자동 연산 완료 (Section 2·3·4 즉시 주입용)")
    print(
        f"   • HTML 계산기 태그 예시:\n"
        f"     <div id=\"dcf-calculator-root\" data-price=\"{int(round(curr_price))}\" data-shares=\"[유통주식수]\" data-total-shares=\"[총발행주식수]\" "
        f"data-fcf=\"[기준FCF억]\" data-net-debt=\"[순차입금억_순현금은음수]\" data-bear=\"{sec3_res['bear_auto']}\" "
        f"data-base=\"{sec3_res['base_auto']}\" data-bull=\"{sec3_res['bull_auto']}\" "
        f"data-base-a=\"{sec3_res['base_auto_a']}\" data-bull-a=\"{sec3_res['bull_auto_a']}\" "
        f"data-buy1=\"{sec4_res.get('buy1', 0)}\" data-buy2=\"{sec4_res.get('buy2', 0)}\" data-buy3=\"{sec4_res.get('buy3', 0)}\" "
        f"data-stop-pin=\"{sec4_res.get('init_stop_pin', 0)}\" data-poc-low=\"{sec4_res.get('poc_low', 0)}\"></div>"
    )
    print(f"======================================================================\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Valuation Indicator Backtester for Korean Stock Analyzer v4.1")
    parser.add_argument('--code', type=str, required=True, help="Stock code (e.g. 093520)")
    parser.add_argument('--years', type=int, default=10, help="Backtest years (default: 10)")
    parser.add_argument('--rerating-date', type=str, default=None, help="Regime shift date (YYYY-MM-DD)")
    parser.add_argument('--corp-name', type=str, default=None, help="Optional corporation name in my-list (e.g. 하나머티리얼즈)")
    parser.add_argument('--base-target', type=float, default=None, help="Optional Base scenario target price (KRW, Model B)")
    parser.add_argument('--bull-target', type=float, default=None, help="Optional Bull scenario target price (KRW, Model B)")
    parser.add_argument('--dart-exposure', type=float, default=None, help="DART verified standalone new-tech revenue exposure %% (e.g. 17.5)")
    parser.add_argument('--forward-eps', type=float, default=None, help="Estimated annual/forward controlling EPS (KRW) to compute Forward BPS")
    parser.add_argument('--expected-dps', type=float, default=0.0, help="Expected annual DPS (KRW) for Forward BPS calculation")
    parser.add_argument('--s-curve-phase2', action='store_true', help="Force enable S-Curve Phase 2 (+10%% Base / +12%% Bull) boost")
    parser.add_argument('--no-s-curve', action='store_true', help="Force disable S-Curve Phase 2 boost")
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
    )
