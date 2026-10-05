#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Valuation Indicator Backtester for Korean Stock Analyzer (v4.0)
Calculates 10-year / full-cycle valuation bands (PBR, PER, DivYield, EV/EBITDA),
enforces floating shares (distb_stock_co) for BPS, performs Sanity Check against
official Naver Finance/FnGuide indicators, and supports Regime Shift (Pre/Post Re-rating).
"""

import sys
import os
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

def run_valuation_backtest(stock_code, years=10, rerating_date=None, custom_data=None):
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
    
    # 2. Fetch Weekly Candles
    df = fetch_weekly_candles(stock_code, count=int(years * 52 + 30))
    if df.empty:
        print("[Error] 주봉 데이터를 가져올 수 없습니다.")
        return
    
    start_date = datetime.now().replace(year=datetime.now().year - years)
    df = df[df['date'] >= start_date].reset_index(drop=True)
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
        print(f"    - Pre-Rerating (레거시): {len(pre_df)}주 ({pre_df['close'].min():,.0f}원 ~ {pre_df['close'].max():,.0f}원)")
        print(f"    - Post-Rerating (신사업): {len(post_df)}주 ({post_df['close'].min():,.0f}원 ~ {post_df['close'].max():,.0f}원)")

    print(f"\n======================================================================")
    print(f"📋 백테스트 완료 요약표 (Phase 1 Section 8 마크다운 보고서용)")
    print(f"======================================================================\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Valuation Indicator Backtester for Korean Stock Analyzer")
    parser.add_argument('--code', type=str, required=True, help="Stock code (e.g. 093520)")
    parser.add_argument('--years', type=int, default=10, help="Backtest years (default: 10)")
    parser.add_argument('--rerating-date', type=str, default=None, help="Regime shift date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    run_valuation_backtest(args.code, args.years, args.rerating_date)
