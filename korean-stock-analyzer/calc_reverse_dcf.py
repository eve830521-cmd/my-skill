#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reverse DCF Calculator for Korean Stock Analyzer (v4.0)
Calculates implied FCF CAGR based on market price, floating shares (distb_stock_co),
total shares (istc_totqy), FCF, WACC, and terminal growth rate.
"""

import sys
import argparse

def calc_dcf_value(fcf, g, wacc, terminal_g, years=5):
    """
    Computes Enterprise/Equity DCF Value given an initial FCF and annual growth rate g.
    Years 1 to 'years': FCF grows at rate g each year, discounted at wacc.
    Terminal value at year 'years': Gordon Growth model (terminal_g) discounted back.
    """
    pv = 0.0
    cf = fcf
    for i in range(1, years + 1):
        cf = cf * (1.0 + g)
        pv += cf / ((1.0 + wacc) ** i)
    
    # Terminal value based on final year cash flow
    tv = (cf * (1.0 + terminal_g)) / (wacc - terminal_g)
    pv += tv / ((1.0 + wacc) ** years)
    return pv

def find_implied_growth(target_value, fcf, wacc, terminal_g=0.02, years=5):
    """
    Finds the implied growth rate g such that calc_dcf_value == target_value.
    Uses binary search. Returns None if FCF <= 0.
    """
    if fcf <= 0:
        return None
    
    if wacc <= terminal_g:
        raise ValueError("WACC must be strictly greater than terminal growth rate.")
    
    low = -0.99
    high = 10.0  # Supports up to 1000% growth
    
    for _ in range(200):
        g = (low + high) / 2.0
        pv = calc_dcf_value(fcf, g, wacc, terminal_g, years)
        if abs(pv - target_value) / max(target_value, 1.0) < 0.00001:
            return g
        if pv > target_value:
            high = g
        else:
            low = g
            
    return g

def analyze_reverse_dcf(price, distb_shares, total_shares, fcf_eok, wacc=0.09, terminal_g=0.02, years=5):
    """
    Full Reverse DCF analysis comparing Floating Shares vs Total Shares.
    fcf_eok: Free Cash Flow in 100 million KRW (억원).
    """
    fcf_won = fcf_eok * 1e8
    target_val_distb = price * distb_shares
    target_val_total = price * total_shares
    
    if fcf_eok <= 0:
        return {
            "status": "deficit",
            "fcf_eok": fcf_eok,
            "distb_shares": distb_shares,
            "total_shares": total_shares,
            "treasury_shares": total_shares - distb_shares,
            "price": price,
            "wacc": wacc,
            "terminal_g": terminal_g,
            "years": years,
            "market_cap_distb_eok": round(target_val_distb / 1e8, 2),
            "market_cap_total_eok": round(target_val_total / 1e8, 2),
            "implied_g_distb_pct": None,
            "implied_g_total_pct": None
        }
        
    g_distb = find_implied_growth(target_val_distb, fcf_won, wacc, terminal_g, years)
    g_total = find_implied_growth(target_val_total, fcf_won, wacc, terminal_g, years)
    
    return {
        "status": "success",
        "price": price,
        "fcf_eok": fcf_eok,
        "distb_shares": distb_shares,
        "total_shares": total_shares,
        "treasury_shares": total_shares - distb_shares,
        "wacc": wacc,
        "terminal_g": terminal_g,
        "years": years,
        "market_cap_distb_eok": round(target_val_distb / 1e8, 2),
        "market_cap_total_eok": round(target_val_total / 1e8, 2),
        "implied_g_distb_pct": round(g_distb * 100, 2) if g_distb is not None else None,
        "implied_g_total_pct": round(g_total * 100, 2) if g_total is not None else None,
    }

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Reverse DCF calculation tool")
    parser.add_argument("--price", type=float, required=True, help="Current stock price (KRW)")
    parser.add_argument("--distb-shares", type=int, required=True, help="Floating shares (distb_stock_co, excluding treasury)")
    parser.add_argument("--total-shares", type=int, required=True, help="Total issued shares (istc_totqy)")
    parser.add_argument("--fcf", type=float, required=True, help="Base FCF in 억 원 (100M KRW)")
    parser.add_argument("--wacc", type=float, default=0.09, help="Discount rate (WACC, default: 0.09)")
    parser.add_argument("--terminal", type=float, default=0.02, help="Terminal growth rate (default: 0.02)")
    parser.add_argument("--years", type=int, default=5, help="Projection years (default: 5)")
    
    args = parser.parse_args()
    res = analyze_reverse_dcf(args.price, args.distb_shares, args.total_shares, args.fcf, args.wacc, args.terminal, args.years)
    
    print("=" * 60)
    print("📈 역DCF 정밀 분석 결과 (Korean Stock Analyzer)")
    print("=" * 60)
    print(f"현재 주가: {res['price']:,}원")
    print(f"총 발행주식수: {res['total_shares']:,}주 (명목 시총: {res['market_cap_total_eok']:,}억 원)")
    print(f"공시 유통주식수: {res['distb_shares']:,}주 (자사주: {res['treasury_shares']:,}주 차감)")
    print(f"실질 유통 시가총액: {res['market_cap_distb_eok']:,}억 원")
    print(f"기준 FCF: {res['fcf_eok']}억 원 | WACC: {res['wacc']*100:.1f}% | 영구성장률: {res['terminal_g']*100:.1f}% | 기간: {res['years']}년")
    print("-" * 60)
    if res['status'] == 'deficit':
        print("결과: FCF 적자(음수)로 인해 역DCF 계산 불가 (P/B, EV/EBITDA 대체 권장)")
    else:
        print(f"▶ [유통주식수 기준] 요구 FCF 성장률: 연간 {res['implied_g_distb_pct']}%")
        print(f"▶ [총발행주식 기준] 요구 FCF 성장률: 연간 {res['implied_g_total_pct']}%")
    print("=" * 60)
