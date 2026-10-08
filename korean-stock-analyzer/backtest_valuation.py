#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Valuation Indicator Backtester for Korean Stock Analyzer (v4.0)
Calculates 10-year / full-cycle valuation bands (PBR, PER, DivYield, EV/EBITDA),
enforces floating shares (distb_stock_co) for BPS, performs Sanity Check against
official Naver Finance/FnGuide indicators, supports Regime Shift (Pre/Post Re-rating),
and automates Phase 3 Section 2 (Value Trap Type A/B), Section 3 (52w Range & TP1),
and Section 4 (Master SOP Daily/Weekly Technical Indicators).
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
            except:
                return None

        res = {
            'stock_name': data.get('stockName'),
            'close': parse_num(metrics.get('전일')) or parse_num(metrics.get('현재가')),
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
    """Extracts key BS/IS items from OpenDART /fnlttSinglAcntAll.json list."""
    res = {'자본총계': 0.0, '영업이익': 0.0, '당기순이익': 0.0, '지배순이익': 0.0, 'EPS': 0.0}
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
            if acc_id == 'ifrs-full_Equity':
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
            elif ('지배기업소유주지분' in acc_nm or '지배기업의소유주' in acc_nm) and '당기순이익' in acc_nm:
                assign('지배순이익')
            elif acc_id == 'ifrs-full_BasicEarningsLossPerShare':
                assign('EPS', True)
            elif acc_nm.startswith('기본주당이익') or acc_nm.startswith('기본주당순이익'):
                assign('EPS')
    if res['지배순이익'] == 0.0:
        res['지배순이익'] = res['당기순이익']
    return res


def _reconstruct_from_cache_db(db_path, corp_code, stock_code, corp_name):
    """Reconstructs sheet2 (Quarterly), sheet3 (TTM), and sheet5 (Band) from cache.db api_cache."""
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
        df_raw['유통주식수'] = np.where(df_raw['EPS'] != 0, df_raw['당기순이익'] / df_raw['EPS'], np.nan)
        df_raw['유통주식수'] = pd.Series(df_raw['유통주식수']).ffill().bfill().fillna(0.0)
        latest_sh = float(df_raw['유통주식수'].iloc[-1]) if not df_raw.empty else 0.0
        df_raw['현재주식수'] = latest_sh

        sheet2 = df_raw.copy()
        flow_cols = ['영업이익', '당기순이익', '지배순이익', 'EPS']
        for i in range(len(sheet2)):
            if sheet2.loc[i, 'Quarter'] != '1Q' and i > 0 and sheet2.loc[i - 1, 'Year'] == sheet2.loc[i, 'Year']:
                for col in flow_cols:
                    sheet2.loc[i, col] = df_raw.loc[i, col] - df_raw.loc[i - 1, col]

        sheet3 = sheet2.copy()
        for i in range(len(sheet3)):
            cy, cq = sheet3.loc[i, 'Year'], sheet3.loc[i, 'Quarter']
            ly_4q = df_raw[(df_raw['Year'] == cy - 1) & (df_raw['Quarter'] == '4Q')]
            ly_cq = df_raw[(df_raw['Year'] == cy - 1) & (df_raw['Quarter'] == cq)]
            if not ly_4q.empty and not ly_cq.empty:
                for col in flow_cols:
                    sheet3.loc[i, col] = df_raw.loc[i, col] + (ly_4q.iloc[0][col] - ly_cq.iloc[0][col])
            else:
                for col in flow_cols:
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

    # 1. Resolve corp_name & corp_code via cache.db if available
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

    # 2. Check candidate Data.xlsx paths
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
            s2 = xl.parse('Sheet2(Quarterly)') if 'Sheet2(Quarterly)' in xl.sheet_names else pd.DataFrame()
            s3 = xl.parse('Sheet3(TTM)') if 'Sheet3(TTM)' in xl.sheet_names else pd.DataFrame()
            s5 = xl.parse('Sheet5(Band)') if 'Sheet5(Band)' in xl.sheet_names else pd.DataFrame()
            return {
                'source': xlsx_path,
                'corp_name': cname,
                'sheet2': s2,
                'sheet3': s3,
                'sheet5': s5
            }
        except Exception as e:
            print(f"[Warning] Failed to read {xlsx_path}: {e}")

    # 3. Fallback to cache.db api_cache if Data.xlsx is not present
    if os.path.exists(db_path) and resolved_corp_code:
        cached_ts = _reconstruct_from_cache_db(db_path, resolved_corp_code, resolved_stock_code, resolved_name)
        if cached_ts is not None:
            return cached_ts

    return {'source': None, 'corp_name': resolved_name, 'sheet2': pd.DataFrame(), 'sheet3': pd.DataFrame(), 'sheet5': pd.DataFrame()}


def _get_split_adjusted_ttm_eps(row, latest_row):
    """
    Returns TTM EPS from row, automatically correcting for missing XBRL EPS tags (0.0),
    sign inversions from bonus issues, or stock splits (액면분할/무상증자).
    """
    raw_eps = row.get('EPS')
    norm_eps = row.get('정상화EPS')
    ni = row.get('당기순이익') if pd.notna(row.get('당기순이익')) and row.get('당기순이익') != 0 else row.get('지배순이익')
    curr_sh = latest_row.get('현재주식수') if latest_row is not None else row.get('현재주식수')
    ni_per_sh = (float(ni) / float(curr_sh)) if (pd.notna(ni) and pd.notna(curr_sh) and float(curr_sh) > 0) else None

    # 1. Missing XBRL tag (EPS == 0 or NaN)
    if pd.isna(raw_eps) or float(raw_eps) == 0.0:
        if ni_per_sh is not None and ni_per_sh != 0.0:
            return ni_per_sh
        if pd.notna(norm_eps) and float(norm_eps) != 0.0:
            return float(norm_eps)
        return None

    raw_val = float(raw_eps)

    # 2. Sign inversion vs actual TTM net income (caused by mid-year bonus issue in cumulative XBRL EPS)
    if ni_per_sh is not None and ni_per_sh != 0.0 and (raw_val * ni_per_sh < 0):
        return ni_per_sh

    # 3. Stock split / bonus issue distortion check against split-adjusted NI/share or 정상화EPS
    if ni_per_sh is not None and ni_per_sh > 0 and raw_val > 0:
        latest_raw = latest_row.get('EPS') if latest_row is not None else None
        latest_ni = latest_row.get('당기순이익') if (latest_row is not None and pd.notna(latest_row.get('당기순이익'))) else None
        latest_ni_sh = (float(latest_ni) / float(curr_sh)) if (latest_ni is not None and pd.notna(curr_sh) and float(curr_sh) > 0) else None
        if pd.notna(latest_raw) and float(latest_raw) > 0 and latest_ni_sh and latest_ni_sh > 0:
            # If latest_raw matches latest_ni_sh closely, but past raw_val is >= 1.35x off from ni_per_sh, a split occurred
            if 0.75 <= (float(latest_raw) / latest_ni_sh) <= 1.33:
                split_ratio = raw_val / ni_per_sh
                if split_ratio >= 1.35 or split_ratio <= 0.65:
                    return ni_per_sh

    return raw_val


def _detect_scurve_phase2_status(stock_code, corp_name=None, scurve_override=None):
    """
    Auto-detects S-Curve Phase 2 (10~40% golden sweet-spot & DART theme exposure >= 30%)
    from korean cycle/00_megatrend/docs/s_curve_registry.json and verified Global_Trend 80p+ PDFs.
    """
    code_str = str(stock_code).zfill(6) if stock_code else ""
    if scurve_override is True:
        return {
            'is_phase2': True,
            'theme_name': '사용자/리포트 S-Curve Phase 2 강제 지정 (--s-curve-phase2)',
            'exposure_pct': 30.0,
            'source_pdf': 'Global_Trend 80p+ 심층 리포트 검증'
        }
    if scurve_override is False:
        return {
            'is_phase2': False,
            'theme_name': 'S-Curve 할증 미적용 (--no-s-curve)',
            'exposure_pct': 0.0,
            'source_pdf': None
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

                # Check excluded first
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
                            exp_pct = float(tp.get("dart_exposure_pct", 30.0) or 30.0)
                            if exp_pct >= 30.0:
                                return {
                                    'is_phase2': True,
                                    'theme_name': f"{th.get('theme_name')} (침투율 {pen:.1f}%, {stage})",
                                    'exposure_pct': exp_pct,
                                    'source_pdf': src_pdf
                                }
                    for hg in th.get("hidden_gems", []):
                        if hg.get("ticker") == code_str or (corp_name and hg.get("name") == corp_name):
                            return {
                                'is_phase2': True,
                                'theme_name': f"{th.get('theme_name')} [히든챔피언] (침투율 {pen:.1f}%, {stage})",
                                'exposure_pct': 40.0,
                                'source_pdf': src_pdf
                            }
        except Exception:
            pass

    # 2. Verified Global_Trend 80p+ PDF S-Curve Phase 2 map (for stocks with >=30% core DART exposure)
    verified_global_trend_p2 = {
        '083450': ('AIDC 액침냉각 & 친환경 칠러/스크러버 (Phase 2 스위트스팟)', 65.0, '26.09.02_유안타_AIDC 다음 승부처 - 네트워크와 냉각.pdf & 26.09.29.LS증권.AI병목의 전이.pdf'),
        '058470': ('온디바이스 AI & NPU 고집적 R&D 리노핀/소켓 (Phase 2 스위트스팟)', 85.0, '26.08.28_신한_생성형AI와 온디바이스AI.pdf & 26.09.16.IM증권.소나기 피할 반도체.pdf'),
        '166090': ('3D NAND 극저온 식각 & 선단 공정 대구경 Si/SiC Ring (Phase 2 스위트스팟)', 85.0, '26.09.29.LS증권.AI병목의 전이.pdf p.13'),
        '013030': ('북미 LNG·FLNG·해양플랜트 초고압 계장용 피팅 (Phase 2 스위트스팟)', 90.0, '26.08.21_하나_조선해양 LNG 피팅 밸류체인.pdf'),
        '064760': ('반도체 식각 챔버용 Solid SiC Ring 더블 S-Curve (Phase 2 스위트스팟)', 89.8, '26.09.29.LS증권.AI병목의 전이.pdf p.13'),
        '095340': ('AI 가속기·NPU 대면적 실리콘 러버 테스트 소켓 (Phase 2 스위트스팟)', 80.0, '26.08.28_신한_생성형AI와 온디바이스AI.pdf'),
        '183300': ('선단 파운드리·HBM 초정밀 세정·코팅 (Phase 2 스위트스팟)', 85.0, '26.09.16.IM증권.소나기 피할 반도체.pdf'),
        '093520': ('AI 서버·방산 고성능 비메모리 FPGA 솔루션 (Phase 2 스위트스팟)', 82.0, '26.09.29.LS증권.AI병목의 전이.pdf'),
        '131970': ('차량용·온디바이스 AI SoC 웨이퍼 테스트 (Phase 2 스위트스팟)', 90.0, '26.08.28_신한_생성형AI와 온디바이스AI.pdf'),
        '005930': ('AI HBM3E/HBM4 및 선단 메모리 턴어라운드 (Phase 2 스위트스팟)', 50.0, '26.09.16.IM증권.소나기 피할 반도체.pdf'),
    }
    if code_str in verified_global_trend_p2:
        t_nm, exp_p, s_pdf = verified_global_trend_p2[code_str]
        return {
            'is_phase2': True,
            'theme_name': t_nm,
            'exposure_pct': exp_p,
            'source_pdf': s_pdf
        }

    return {
        'is_phase2': False,
        'theme_name': '해당 없음 (전통 시클리컬 / 성숙기 박스권 BM / 익스포저 30% 미달)',
        'exposure_pct': 0.0,
        'source_pdf': None
    }


def compute_section2_value_trap(stock_code, bench, df_week, dart_ts):
    """
    (A) DART 시계열 연동 및 Section 2 가치함정 이원화 자동 판정
    Computes 2Y cumulative EPS/BPS growth, Quarterly/TTM Operating Profit YoY growth (for Model A),
    3Y PBR/PER percentiles, current ROE, weekly 20MA vs 60MA alignment, and classifies into Type A vs Type B.
    """
    print(f"\n======================================================================")
    print(f"🚨 [Phase 3 - Section 2] DART 시계열 연동 & 가치함정 이원화(유형 A/B) 자동 판정")
    print(f"======================================================================")

    s2 = dart_ts.get('sheet2', pd.DataFrame())
    s3 = dart_ts.get('sheet3', pd.DataFrame())
    s5 = dart_ts.get('sheet5', pd.DataFrame())
    src = dart_ts.get('source')

    curr_price = float(df_week['close'].iloc[-1]) if not df_week.empty else (bench.get('close') or 0)
    official_bps = bench.get('bps')
    official_eps = bench.get('eps')

    eps_2y_chg = None
    bps_2y_chg = None
    curr_roe = None
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

    if not s3.empty:
        s3_valid = s3.dropna(subset=['BPS'], how='all').reset_index(drop=True)
        if len(s3_valid) >= 1:
            latest_row = s3_valid.iloc[-1]
            curr_eps_dart = _get_split_adjusted_ttm_eps(latest_row, latest_row)
            curr_bps_dart = latest_row.get('BPS')
            if pd.notna(latest_row.get('ROE')):
                curr_roe = float(latest_row['ROE'])
            if curr_eps_dart is not None and curr_eps_dart != 0:
                curr_eps_used = float(curr_eps_dart)
            if pd.notna(curr_bps_dart) and curr_bps_dart > 0:
                curr_bps_used = float(curr_bps_dart)

            # 2 years ago = 8 quarters back (or earliest available if < 9 rows)
            idx_2y = max(0, len(s3_valid) - 9)
            row_2y = s3_valid.iloc[idx_2y]
            eps_2y_ago = _get_split_adjusted_ttm_eps(row_2y, latest_row)
            bps_2y_ago = row_2y.get('BPS')

            if curr_eps_dart is not None and eps_2y_ago is not None and abs(float(eps_2y_ago)) > 0:
                eps_2y_chg = (float(curr_eps_dart) - float(eps_2y_ago)) / abs(float(eps_2y_ago)) * 100.0
            if pd.notna(curr_bps_dart) and pd.notna(bps_2y_ago) and abs(float(bps_2y_ago)) > 0:
                bps_2y_chg = (float(curr_bps_dart) - float(bps_2y_ago)) / abs(float(bps_2y_ago)) * 100.0

            # TTM Operating Profit YoY (1 year ago = 4 quarters back)
            if len(s3_valid) >= 5 and '영업이익' in s3_valid.columns:
                op_ttm_now = latest_row.get('영업이익')
                op_ttm_1y = s3_valid.iloc[-5].get('영업이익')
                if pd.notna(op_ttm_now) and pd.notna(op_ttm_1y) and abs(float(op_ttm_1y)) > 0:
                    op_yoy_ttm = (float(op_ttm_now) - float(op_ttm_1y)) / abs(float(op_ttm_1y)) * 100.0

    if not s2.empty:
        if 'EPS' in s2.columns:
            s2_v = s2.dropna(subset=['EPS']).reset_index(drop=True)
            if len(s2_v) >= 2:
                r_now = s2_v.iloc[-1]
                r_prev = s2_v.iloc[-2]
                q_now = float(r_now['EPS'])
                q_prev = float(r_prev['EPS'])
                if q_now == 0.0 and pd.notna(r_now.get('지배순이익')) and pd.notna(r_now.get('현재주식수')) and float(r_now['현재주식수']) > 0:
                    q_now = float(r_now['지배순이익']) / float(r_now['현재주식수'])
                if q_prev == 0.0 and pd.notna(r_prev.get('지배순이익')) and pd.notna(r_prev.get('현재주식수')) and float(r_prev['현재주식수']) > 0:
                    q_prev = float(r_prev['지배순이익']) / float(r_prev['현재주식수'])
                q_label_now = f"{int(r_now['Year'])}.{r_now['Quarter']}"
                q_label_prev = f"{int(r_prev['Year'])}.{r_prev['Quarter']}"
                qoq_eps_positive = (q_now > q_prev) and (q_now > 0)
                qoq_eps_detail = f"{q_label_prev} {q_prev:,.0f}원 → {q_label_now} {q_now:,.0f}원 ({'QoQ 반등 ✅' if qoq_eps_positive else 'QoQ 둔화/적자 ⚠️'})"

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
                        op_yoy_q = 100.0  # 흑자전환
                    elif op_q_prev < 0 and op_q_now <= op_q_prev:
                        op_yoy_q = -50.0  # 적자지속/확대
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

        # Adjust PBR to official floating BPS ratio if treasury shares caused a gap
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

    # Fallback to Naver official metrics + weekly candles if not in my-list
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

    # Weekly 20MA vs 60MA alignment
    w_ma20 = float(df_week['close'].rolling(20).mean().iloc[-1]) if len(df_week) >= 20 else curr_price
    w_ma60 = float(df_week['close'].rolling(60).mean().iloc[-1]) if len(df_week) >= 60 else curr_price
    is_weekly_aligned = w_ma20 >= w_ma60
    align_str = f"정배열 (20주선 {w_ma20:,.0f}원 ≥ 60주선 {w_ma60:,.0f}원) 📈" if is_weekly_aligned else f"역배열 (20주선 {w_ma20:,.0f}원 < 60주선 {w_ma60:,.0f}원) 📉"

    print(f"    - 데이터 소스: {src if src else '네이버 공식 벤치마크 + 주봉 시계열 폴백'}")
    print(f"    - 최근 2년 누적 EPS 증감률: {f'{eps_2y_chg:+.2f}%' if eps_2y_chg is not None else 'N/A (단기 시계열 폴백)'}")
    print(f"    - 최근 2년 누적 BPS 증감률: {f'{bps_2y_chg:+.2f}%' if bps_2y_chg is not None else 'N/A (단기 시계열 폴백)'}")
    print(f"    - 최근 영업이익 YoY 추이:   {op_yoy_detail}")
    print(f"    - 최근 분기 EPS QoQ 추이:  {qoq_eps_detail}")
    print(f"    - 현재 ROE (연환산/TTM):    {f'{curr_roe:.2f}%' if curr_roe is not None else 'N/A'}")
    if pbr_3y_max is not None:
        cyc_str = f" (10년 풀사이클 최고 {pbr_cycle_max:.2f}배)" if pbr_cycle_max is not None else ""
        print(f"    - 과거 3년 PBR 밴드: 최고 {pbr_3y_max:.2f}배{cyc_str} | 평균 {pbr_3y_mean:.2f}배 | 하위20% {pbr_3y_p20:.2f}배 | 상위65% {pbr_3y_p65:.2f}배 | 상위90% {pbr_3y_p90:.2f}배")
    if per_3y_max is not None:
        print(f"    - 과거 3년 PER 밴드: 최고 {per_3y_max:.2f}배 | 평균 {per_3y_mean:.2f}배 | 하위20% {per_3y_p20:.2f}배 | 상위65% {per_3y_p65:.2f}배 | 상위90% {per_3y_p90:.2f}배")
    print(f"    - 주봉 20MA vs 60MA 배열:  {align_str}")

    # Classification Rule:
    # Type B: (past 3Y max PBR >= 2.8x OR (10Y cycle max PBR >= 2.8x AND 2Y BPS growth < +15%))
    #         AND 2Y EPS change <= -30% AND ROE < 8.5%
    # Type A: 2Y BPS growth >= +15% or not meeting Type B structural collapse
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
            f"      ➔ 과거 호황 멀티플 전면 폐기 | Bear 지지선 = PBR 0.75배 청산가치({(official_bps or curr_bps_used or 0)*0.75:,.0f}원) 강제 고정 | 분기 EPS QoQ > 0 확인 전 매수 차단!"
        )
    else:
        verdict = "🟢 [유형 A: 일시적 사이클 저점 (Cyclical Trough)]"
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


def compute_section3_stock_nature_and_tp1(stock_code, corp_name, bench, df_week, df_day, sec2_res, base_target=None, bull_target=None, scurve_override=None):
    """
    (B) Section 3 종목 체질(52주 변동폭 36% 기준) 판별 및 [대안 B vs 대안 A] 목표가 병기 자동 산출
    - 대안 B (보수적 순수 DART): 과거 3년 PER/PBR 분위수(Bear 20% / Base 65% / Bull 90%) 순수 적용
    - 대안 A (업황 모멘텀·S-Curve 할증):
      ① DART 영업이익 YoY >= +20% & ROE >= 12% 시 +8% 할증 / 영업이익 YoY <= -25% 시 -6% 할인
      ② S-Curve Phase 2 (침투율 10~40% & 테마 매출 30%+) 검증 시 Base +10% / Bull +12% 추가 할증
    """
    print(f"\n======================================================================")
    print(f"🎯 [Phase 3 - Section 3] 종목 체질 판별 & [대안 B vs 대안 A] 목표가 나란히 자동 산출")
    print(f"======================================================================")

    w52 = df_week.tail(52)
    curr_price = float(df_week['close'].iloc[-1])
    high_52w_close = float(w52['close'].max())
    low_52w_close = float(w52['close'].min())
    high_52w_intraday = float(w52['high'].max())
    low_52w_intraday = float(w52['low'].min())

    range_52w_close_pct = (high_52w_close / low_52w_close - 1.0) * 100.0 if low_52w_close > 0 else 0.0
    range_52w_intraday_pct = (high_52w_intraday / low_52w_intraday - 1.0) * 100.0 if low_52w_intraday > 0 else 0.0

    # 3Y rolling 52-week close range median (filters out single-week anomaly spikes on box stocks)
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
    eps = bench.get('eps') or sec2_res.get('curr_eps_used') or 0
    curr_pbr = (curr_price / bps) if bps > 0 else (bench.get('pbr') or 0)

    is_low_vol_box = (effective_52w_range < 36.0) and (curr_pbr > 0 and curr_pbr < 1.35)

    # 1. 대안 B (순수 DART 3Y 분위수 기준): max(EPS * 3Y PER quantile, BPS * 3Y PBR quantile)
    per_p20 = sec2_res.get('per_3y_p20') or 8.0
    per_p65 = sec2_res.get('per_3y_p65') or 12.0
    per_p90 = sec2_res.get('per_3y_p90') or 16.0
    pbr_p20 = sec2_res.get('pbr_3y_p20') or 0.75
    pbr_p65 = sec2_res.get('pbr_3y_p65') or 1.20
    pbr_p90 = sec2_res.get('pbr_3y_p90') or 1.80

    if sec2_res.get('is_type_b'):
        bear_auto_b = round((bps * 0.75) / 10) * 10 if bps > 0 else round(low_52w_close)
    else:
        bear_candidates = [v for v in [eps * per_p20 if eps and eps > 0 else 0, bps * pbr_p20 if bps and bps > 0 else 0] if v > 0]
        bear_auto_b = round(max(bear_candidates) / 10) * 10 if bear_candidates else round(low_52w_close)

    base_candidates = [v for v in [eps * per_p65 if eps and eps > 0 else 0, bps * pbr_p65 if bps and bps > 0 else 0] if v > 0]
    bull_candidates = [v for v in [eps * per_p90 if eps and eps > 0 else 0, bps * pbr_p90 if bps and bps > 0 else 0] if v > 0]
    base_auto_b = round(max(base_candidates) / 10) * 10 if base_candidates else round(curr_price * 1.2)
    bull_auto_b = round(max(bull_candidates) / 10) * 10 if bull_candidates else round(curr_price * 1.5)

    # 2. 대안 A 가중 계수 산출 (실적 모멘텀 YoY + S-Curve Phase 2 할증)
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

    sc_info = _detect_scurve_phase2_status(stock_code, corp_name=corp_name or bench.get('stock_name'), scurve_override=scurve_override)
    sc_base_mult = 1.10 if (sc_info['is_phase2'] and not is_type_b) else 1.00
    sc_bull_mult = 1.12 if (sc_info['is_phase2'] and not is_type_b) else 1.00
    if sc_info['is_phase2'] and not is_type_b:
        sc_reason = f"적용 ✅ [{sc_info['theme_name']} | 익스포저 {sc_info['exposure_pct']:.0f}% ≥ 30%] → Base +10.0% / Bull +12.0% 할증"
    else:
        sc_reason = f"미적용 ⚪ [{sc_info['theme_name']}] → +0.0%"

    base_mult_a = round(op_mult * sc_base_mult, 4)
    bull_mult_a = round(op_mult * sc_bull_mult, 4)

    bear_auto_a = bear_auto_b  # 하방 안전마진(Bear)은 보수적 원칙에 따라 할증 금지
    base_auto_a = round((base_auto_b * base_mult_a) / 10) * 10
    bull_auto_a = round((bull_auto_b * bull_mult_a) / 10) * 10

    # TP1 short-term box target: 52w 70~75th percentile realized price or 60-day high resistance
    tp1_p70 = float(w52['close'].quantile(0.70))
    tp1_p75 = float(w52['close'].quantile(0.75))
    d60_ma = float(df_day['close'].rolling(60).mean().iloc[-1]) if not df_day.empty and len(df_day) >= 60 else tp1_p70
    d60_high_p75 = float(df_day.tail(60)['high'].quantile(0.75)) if not df_day.empty else tp1_p75
    tp1_price_b = round(max(tp1_p75, d60_high_p75, curr_price * 1.06) / 10) * 10
    tp1_price_a = round((tp1_price_b * op_mult) / 10) * 10 if op_mult != 1.0 else tp1_price_b
    tp1_upside_b = (tp1_price_b / curr_price - 1.0) * 100.0 if curr_price > 0 else 0.0
    tp1_upside_a = (tp1_price_a / curr_price - 1.0) * 100.0 if curr_price > 0 else 0.0

    print(f"    - 최근 52주 주봉 종가 범위: {low_52w_close:,.0f}원 ~ {high_52w_close:,.0f}원 (단순 52주 등락폭: {range_52w_close_pct:.1f}% | 3Y 롤링 52주 중위값: {median_52w_range_pct:.1f}%)")
    print(f"    - 최근 52주 장중 고저 범위: {low_52w_intraday:,.0f}원 ~ {high_52w_intraday:,.0f}원 (장중 등락폭: {range_52w_intraday_pct:.1f}%)")
    print(f"    - 현재 공식 PBR:            {curr_pbr:.2f}배")

    if is_low_vol_box:
        nature_badge = "🛡️ [저변동 고배당 박스권주 (52주 등락폭 < 36% & PBR < 1.35배)]"
        nature_desc = (
            f"유효 52주 등락폭({effective_52w_range:.1f}% < 36% [단순 {range_52w_close_pct:.1f}%, 3Y중위 {median_52w_range_pct:.1f}%]) & PBR({curr_pbr:.2f}배 < 1.35배) 해당!\n"
            f"      ➔ 시나리오 표에 이론적 Base/Bull과 별개로 [TP1 단기 박스권 1차 실전 목표가: 대안B {tp1_price_b:,.0f}원({tp1_upside_b:+.1f}%) | 대안A {tp1_price_a:,.0f}원({tp1_upside_a:+.1f}%)] 행을 반드시 추가하십시오!"
        )
    else:
        nature_badge = "🚀 [성장·사이클 추세주 (52주 등락폭 >= 36%)]"
        nature_desc = (
            f"유효 52주 등락폭({effective_52w_range:.1f}% [단순 {range_52w_close_pct:.1f}%, 3Y중위 {median_52w_range_pct:.1f}%]) 또는 PBR({curr_pbr:.2f}배) 기준 추세/사이클 종목 해당!\n"
            f"      ➔ max(EPS × 3Y PER 분위수, BPS × 3Y PBR 분위수) 기반 [대안 B(순수 DART) | 대안 A(실적·S-Curve 할증)] 목표가를 나란히 병기합니다."
        )

    print(f"\n    ▶ Section 3 종목 체질 판정: {nature_badge}")
    print(f"      {nature_desc}")

    print(f"\n    ▶ [대안 A (실적 모멘텀 · S-Curve 할증) 가중 계수 산출 내역]:")
    print(f"      ① DART 실적 모멘텀 가감산 (op_mult = ×{op_mult:.2f}): {op_reason}")
    print(f"      ② S-Curve Phase 2 할증 (Base ×{sc_base_mult:.2f} / Bull ×{sc_bull_mult:.2f}): {sc_reason}")
    if sc_info.get('source_pdf'):
        print(f"         • 검증 출처: {sc_info['source_pdf']}")
    print(f"      ③ 최종 대안 A 멀티플 가중치: Base ×{base_mult_a:.4f} ({(base_mult_a - 1)*100:+.1f}%) | Bull ×{bull_mult_a:.4f} ({(bull_mult_a - 1)*100:+.1f}%) | Bear ×1.0000 (하방 보호 고정)")

    print(f"\n    ▶ [3Y 밴드 자동 산출 가이드라인 가격 — 🛡️ 대안 B (순수 DART) vs 🚀 대안 A (업황·S-Curve 할증) 나란히 비교]:")
    print(f"      • Bull (호황 상단): 🛡️ 대안 B {bull_auto_b:,.0f}원 ({(bull_auto_b/curr_price - 1)*100:+.1f}%)  |  🚀 대안 A {bull_auto_a:,.0f}원 ({(bull_auto_a/curr_price - 1)*100:+.1f}%)")
    print(f"      • Base (기본 적정): 🛡️ 대안 B {base_auto_b:,.0f}원 ({(base_auto_b/curr_price - 1)*100:+.1f}%)  |  🚀 대안 A {base_auto_a:,.0f}원 ({(base_auto_a/curr_price - 1)*100:+.1f}%)")
    print(f"      • TP1  (단기 실전): 🛡️ 대안 B {tp1_price_b:,.0f}원 ({tp1_upside_b:+.1f}%)  |  🚀 대안 A {tp1_price_a:,.0f}원 ({tp1_upside_a:+.1f}%) [52주 70~75%: {tp1_p70:,.0f}~{tp1_p75:,.0f}원 / 60일선: {d60_ma:,.0f}원]")
    print(f"      • Bear (하방 지지): 🛡️ 대안 B·A 공통 {bear_auto_b:,.0f}원 ({(bear_auto_b/curr_price - 1)*100:+.1f}%) [하방 안전마진 할증 금지]")

    final_base_b = base_auto_b
    final_base_a = base_auto_a
    final_bull_b = bull_auto_b
    final_bull_a = bull_auto_a

    if base_target is not None and base_target > 0:
        base_b_in = round(base_target / 10) * 10
        base_a_in = round((base_target * base_mult_a) / 10) * 10
        final_base_b = base_b_in
        final_base_a = base_a_in
        print(f"\n    ▶ [바텀업 입력 시나리오 목표가 대비 🛡️ 대안 B vs 🚀 대안 A 자동 변환표]:")
        print(f"      • 입력 Base 목표가: 🛡️ 대안 B {base_b_in:,.0f}원 ({(base_b_in/curr_price - 1)*100:+.1f}%)  |  🚀 대안 A {base_a_in:,.0f}원 ({(base_a_in/curr_price - 1)*100:+.1f}%)")
        if bull_target is not None and bull_target > 0:
            bull_b_in = round(bull_target / 10) * 10
            bull_a_in = round((bull_target * bull_mult_a) / 10) * 10
            final_bull_b = bull_b_in
            final_bull_a = bull_a_in
            print(f"      • 입력 Bull 목표가: 🛡️ 대안 B {bull_b_in:,.0f}원 ({(bull_b_in/curr_price - 1)*100:+.1f}%)  |  🚀 대안 A {bull_a_in:,.0f}원 ({(bull_a_in/curr_price - 1)*100:+.1f}%)")

    return {
        'is_low_vol_box': is_low_vol_box,
        'nature_badge': nature_badge,
        'range_52w_pct': range_52w_pct,
        'median_52w_range_pct': median_52w_range_pct,
        'effective_52w_range': effective_52w_range,
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


def compute_section4_master_sop_indicators(df_day, df_week):
    """
    (C) Section 4 마스터 SOP 정예 보조지표 실측값 자동 출력 (timeframe=day & timeframe=week)
    """
    print(f"\n======================================================================")
    print(f"📐 [Phase 3 - Section 4] 마스터 SOP 정예 보조지표 실측값 (일봉 & 주봉)")
    print(f"======================================================================")

    res = {}

    # --- 1. Daily Indicators (count=150) ---
    if not df_day.empty:
        curr_d_close = float(df_day['close'].iloc[-1])
        curr_d_date = df_day['date'].iloc[-1].strftime('%Y-%m-%d')
        d_ma5 = float(df_day['close'].rolling(5, min_periods=1).mean().iloc[-1])
        d_ma20 = float(df_day['close'].rolling(20, min_periods=1).mean().iloc[-1])
        d_ma60 = float(df_day['close'].rolling(60, min_periods=1).mean().iloc[-1])

        vol_ma20 = float(df_day['vol'].rolling(20, min_periods=1).mean().iloc[-1])
        curr_vol = float(df_day['vol'].iloc[-1])
        vol_ratio = (curr_vol / vol_ma20) if vol_ma20 > 0 else 0.0

        # 20-day Volume Profile (5 bins POC)
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
        hard_stop_pin = int(round(poc_low * 0.97))

        # Slow Stochastic (12, 5, 5)
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
        print(f"      • 2차 본대 진입 시 고정 바닥 안전핀(매물대 하단 -3%): {hard_stop_pin:,}원")
        print(f"    - 일봉 이동평균선: 5일선 {int(round(d_ma5)):,}원 | 20일선 {int(round(d_ma20)):,}원 | 60일선 {int(round(d_ma60)):,}원")
        print(f"    - 거래량 분석:     20일 평균 거래량 {int(round(vol_ma20)):,}주 | 당일 거래량 {int(round(curr_vol)):,}주 (배수 vol_ratio: {vol_ratio:.2f}배)")
        print(f"    - 일봉 Slow Stochastic(12,5,5): %K = {curr_k:.1f}% / %D = {curr_d:.1f}% ({'골든크로스/상승우위' if curr_k >= curr_d else '데드크로스/조정중'})")

        res.update({
            'd_close': int(round(curr_d_close)),
            'poc_low': poc_low,
            'poc_high': poc_high,
            'poc_pct': poc_pct,
            'hard_stop_pin': hard_stop_pin,
            'd_ma5': int(round(d_ma5)),
            'd_ma20': int(round(d_ma20)),
            'd_ma60': int(round(d_ma60)),
            'vol_ma20': int(round(vol_ma20)),
            'vol_ratio': vol_ratio,
            'stoch_k': curr_k,
            'stoch_d': curr_d
        })

    # --- 2. Weekly Indicators (count=600) ---
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

        # Weekly MACD (12, 26, 9)
        ema12 = df_week['close'].ewm(span=12, adjust=False).mean()
        ema26 = df_week['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - sig_line
        curr_macd = float(macd_line.iloc[-1])
        curr_sig = float(sig_line.iloc[-1])
        curr_hist = float(macd_hist.iloc[-1])

        # Weekly MFI (14)
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

        # Weekly RSI (14) - Wilder's smoothing
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

        # High-alert check
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

    return res


def run_valuation_backtest(stock_code, years=10, rerating_date=None, corp_name=None, base_target=None, bull_target=None, scurve_override=None, custom_data=None):
    print(f"\n======================================================================")
    print(f"📊 [Korean Stock Analyzer v4.0] 4대 밸류에이션 지표 10년 풀사이클 백테스트")
    print(f"   종목코드: {stock_code} | 분석 기간: 최근 {years}년")
    print(f"======================================================================")
    
    # 1. Official Benchmark Check
    bench = fetch_naver_official_metrics(stock_code)
    if bench:
        print(f"\n[1] 🏛️ 제도권 공식 벤치마크 (네이버 금융 / FnGuide / KRX 기준):")
        close_str = f"{bench['close']:,.0f}원" if bench.get('close') is not None else "N/A"
        bps_str = f"{bench['bps']:,.0f}원" if bench.get('bps') is not None else "N/A"
        pbr_str = f"{bench['pbr']:.2f}배" if bench.get('pbr') is not None else "N/A"
        eps_str = f"{bench['eps']:,.0f}원" if bench.get('eps') is not None else "N/A"
        per_str = f"{bench['per']:.2f}배" if bench.get('per') is not None else "N/A"
        div_str = f"{bench['div_yield']:.2f}%" if bench.get('div_yield') is not None else "N/A"
        dps_str = f"{bench['dps']:,.0f}원" if bench.get('dps') is not None else "N/A"
        print(f"    - 현재 주가: {close_str}")
        print(f"    - 공식 BPS:  {bps_str} (자사주 제외 실질 유통주식수 기준)")
        print(f"    - 공식 PBR:  {pbr_str}")
        print(f"    - 공식 EPS:  {eps_str} (기본 주당순이익 기준)")
        print(f"    - 공식 PER:  {per_str}")
        print(f"    - 공식 배당: {div_str} (DPS: {dps_str})")
    
    # 2. Fetch Weekly Candles (600 candles) & Daily Candles (150 candles)
    df_full_week = fetch_weekly_candles(stock_code, count=max(600, int(years * 52 + 30)))
    df_day = fetch_daily_candles(stock_code, count=150)
    if df_full_week.empty:
        print("[Error] 주봉 데이터를 가져올 수 없습니다.")
        return
    
    start_date = (pd.Timestamp.now() - pd.DateOffset(years=years)).to_pydatetime()
    df = df_full_week[df_full_week['date'] >= start_date].reset_index(drop=True)
    if df.empty:
        df = df_full_week.copy()
    print(f"\n[2] 📈 수집된 주봉 데이터셋: {len(df)}개 캔들 ({df['date'].min().strftime('%Y-%m-%d')} ~ {df['date'].max().strftime('%Y-%m-%d')})")
    print(f"    - 10년 역사적 최저가(Trough): {df['close'].min():,.0f}원 ({df.loc[df['close'].idxmin(), 'date'].strftime('%Y-%m-%d')})")
    print(f"    - 10년 역사적 최고가(Peak):   {df['close'].max():,.0f}원 ({df.loc[df['close'].idxmax(), 'date'].strftime('%Y-%m-%d')})")
    print(f"    - 최신 주봉 종가:             {df['close'].iloc[-1]:,.0f}원")
    
    # 3. Sanity Check Alert
    if bench.get('bps') is not None:
        curr_price = df['close'].iloc[-1]
        calc_pbr = curr_price / bench['bps']
        official_pbr = bench.get('pbr')
        print(f"\n[3] 🛡️ Sanity Check (유통 BPS 검증):")
        print(f"    - 현재 주가({curr_price:,.0f}원) ÷ 공식 BPS({bench['bps']:,.0f}원) = PBR {calc_pbr:.2f}배")
        if official_pbr is not None:
            diff_pct = abs(calc_pbr - official_pbr) / official_pbr * 100
            if diff_pct > 5.0:
                print(f"    ⚠️ [경고] 공식 PBR({official_pbr:.2f}배)과 오차 {diff_pct:.1f}% 발생! 분모(주식수) 매핑 확인 필요.")
            else:
                print(f"    ✅ [검증 통과] 제도권 공식 PBR({official_pbr:.2f}배)과 정합성 100% 일치.")
    
    # 4. Regime Shift Check
    if rerating_date:
        print(f"\n[4] ⚡ 레짐 시프트(Regime Shift) 분기점 설정: {rerating_date}")
        pre_df = df[df['date'] < datetime.strptime(rerating_date, '%Y-%m-%d')]
        post_df = df[df['date'] >= datetime.strptime(rerating_date, '%Y-%m-%d')]
        if not pre_df.empty:
            print(f"    - Pre-Rerating (레거시): {len(pre_df)}주 ({pre_df['close'].min():,.0f}원 ~ {pre_df['close'].max():,.0f}원)")
        if not post_df.empty:
            print(f"    - Post-Rerating (신사업): {len(post_df)}주 ({post_df['close'].min():,.0f}원 ~ {post_df['close'].max():,.0f}원)")

    # 5. Automated Phase 3 Modules (A, B, C)
    dart_ts = load_dart_timeseries(stock_code, corp_name=corp_name, stock_name_hint=bench.get('stock_name'))
    sec2_res = compute_section2_value_trap(stock_code, bench, df_full_week, dart_ts)
    sec3_res = compute_section3_stock_nature_and_tp1(
        stock_code, corp_name or dart_ts.get('corp_name'), bench, df_full_week, df_day, sec2_res,
        base_target=base_target, bull_target=bull_target, scurve_override=scurve_override
    )
    sec4_res = compute_section4_master_sop_indicators(df_day, df_full_week)

    print(f"\n======================================================================")
    print(f"📋 백테스트 & Phase 3 마스터 SOP 자동 연산 완료 (Section 2·3·4 즉시 주입용)")
    print(f"   • HTML 계산기 태그 예시: data-bear=\"{sec3_res['bear_auto']}\" data-base=\"{sec3_res['base_auto']}\" data-bull=\"{sec3_res['bull_auto']}\" data-base-a=\"{sec3_res['base_auto_a']}\" data-bull-a=\"{sec3_res['bull_auto_a']}\" data-poc-low=\"{sec4_res.get('poc_low', 0)}\"")
    print(f"======================================================================\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Valuation Indicator Backtester for Korean Stock Analyzer")
    parser.add_argument('--code', type=str, required=True, help="Stock code (e.g. 093520)")
    parser.add_argument('--years', type=int, default=10, help="Backtest years (default: 10)")
    parser.add_argument('--rerating-date', type=str, default=None, help="Regime shift date (YYYY-MM-DD)")
    parser.add_argument('--corp-name', type=str, default=None, help="Optional corporation name in my-list (e.g. 하나머티리얼즈)")
    parser.add_argument('--base-target', type=float, default=None, help="Optional Base scenario target price (KRW, Model B)")
    parser.add_argument('--bull-target', type=float, default=None, help="Optional Bull scenario target price (KRW, Model B)")
    parser.add_argument('--s-curve-phase2', action='store_true', help="Force enable S-Curve Phase 2 (+10%% Base / +12%% Bull) boost")
    parser.add_argument('--no-s-curve', action='store_true', help="Force disable S-Curve Phase 2 boost")
    args = parser.parse_args()

    sc_override = True if args.s_curve_phase2 else (False if args.no_s_curve else None)
    run_valuation_backtest(args.code, args.years, args.rerating_date, args.corp_name, args.base_target, args.bull_target, sc_override)
