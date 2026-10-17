#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reverse DCF Calculator for Korean Stock Analyzer (v4.1)
Calculates implied FCF CAGR based on:
1. Floating Equity Market Cap (distb_stock_co * price) [Conservative Equity Baseline]
2. Enterprise Value (EV = Floating Market Cap + Preferred Market Cap + Net Debt) [Corporate Finance Standard]
Supports both Reported FCF (--fcf) and Normalized/Controlling FCF (--norm-fcf).
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
    Uses binary search. Returns None if FCF <= 0, target_value <= 0, WACC <= terminal_g, or convergence fails.
    """
    if fcf <= 0 or target_value <= 0:
        return None

    if wacc <= terminal_g:
        return None  # graceful return instead of crash

    low = -0.99
    high = 10.0  # Supports up to 1000% growth
    TOLERANCE = 0.00001
    MAX_ITER = 200

    g = 0.0
    for _ in range(MAX_ITER):
        g = (low + high) / 2.0
        pv = calc_dcf_value(fcf, g, wacc, terminal_g, years)
        if abs(pv - target_value) / max(target_value, 1.0) < TOLERANCE:
            return g
        if pv > target_value:
            high = g
        else:
            low = g

    if abs(high - low) < 0.001:
        return g
    return None


def analyze_reverse_dcf(
    price,
    distb_shares,
    total_shares,
    fcf_eok,
    wacc=0.09,
    terminal_g=0.02,
    years=5,
    net_debt_eok=0.0,
    pref_mcap_eok=0.0,
    norm_fcf_eok=None,
):
    """
    Full Reverse DCF analysis comparing:
    - Floating Shares Market Cap (Equity Value) vs Total Shares Market Cap
    - Enterprise Value (EV = Floating Market Cap + Preferred Market Cap + Net Debt)
    - Reported FCF vs Normalized/Controlling FCF (if provided)
    """
    fcf_won = fcf_eok * 1e8
    target_val_distb = price * distb_shares
    target_val_total = price * total_shares
    target_ev_distb = target_val_distb + (pref_mcap_eok * 1e8) + (net_debt_eok * 1e8)

    res = {
        "status": "deficit" if fcf_eok <= 0 else "success",
        "price": price,
        "fcf_eok": fcf_eok,
        "norm_fcf_eok": norm_fcf_eok,
        "distb_shares": distb_shares,
        "total_shares": total_shares,
        "treasury_shares": total_shares - distb_shares,
        "wacc": wacc,
        "terminal_g": terminal_g,
        "years": years,
        "net_debt_eok": round(net_debt_eok, 2),
        "pref_mcap_eok": round(pref_mcap_eok, 2),
        "market_cap_distb_eok": round(target_val_distb / 1e8, 2),
        "market_cap_total_eok": round(target_val_total / 1e8, 2),
        "ev_distb_eok": round(target_ev_distb / 1e8, 2),
        "implied_g_distb_pct": None,
        "implied_g_total_pct": None,
        "implied_g_ev_pct": None,
        "norm_g_distb_pct": None,
        "norm_g_ev_pct": None,
    }

    if fcf_eok > 0:
        g_distb = find_implied_growth(target_val_distb, fcf_won, wacc, terminal_g, years)
        g_total = find_implied_growth(target_val_total, fcf_won, wacc, terminal_g, years)
        g_ev = find_implied_growth(target_ev_distb, fcf_won, wacc, terminal_g, years) if target_ev_distb > 0 else None
        res["implied_g_distb_pct"] = round(g_distb * 100, 2) if g_distb is not None else None
        res["implied_g_total_pct"] = round(g_total * 100, 2) if g_total is not None else None
        res["implied_g_ev_pct"] = round(g_ev * 100, 2) if g_ev is not None else None

    if norm_fcf_eok is not None and norm_fcf_eok > 0:
        norm_won = norm_fcf_eok * 1e8
        ng_distb = find_implied_growth(target_val_distb, norm_won, wacc, terminal_g, years)
        ng_ev = find_implied_growth(target_ev_distb, norm_won, wacc, terminal_g, years) if target_ev_distb > 0 else None
        res["norm_g_distb_pct"] = round(ng_distb * 100, 2) if ng_distb is not None else None
        res["norm_g_ev_pct"] = round(ng_ev * 100, 2) if ng_ev is not None else None

    return res


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Reverse DCF calculation tool (Equity & EV Dual-Mode)")
    parser.add_argument("--price", type=float, required=True, help="Current stock price (KRW, latest daily close)")
    parser.add_argument("--distb-shares", type=int, required=True, help="Floating shares (distb_stock_co, excluding treasury)")
    parser.add_argument("--total-shares", type=int, required=True, help="Total issued shares (istc_totqy)")
    parser.add_argument("--fcf", type=float, required=True, help="Base/Reported FCF in 억 원 (100M KRW)")
    parser.add_argument("--norm-fcf", type=float, default=None, help="Optional Normalized / Controlling-interest FCF in 억 원")
    parser.add_argument("--net-debt", type=float, default=0.0, help="Net Debt in 억 원 (+ for Net Debt, - for Net Cash excluding operational payables)")
    parser.add_argument("--pref-mcap", type=float, default=0.0, help="Preferred stock market cap in 억 원 (default: 0.0)")
    parser.add_argument("--wacc", type=float, default=0.09, help="Discount rate (WACC, default: 0.09)")
    parser.add_argument("--terminal", type=float, default=0.02, help="Terminal growth rate (default: 0.02)")
    parser.add_argument("--years", type=int, default=5, help="Projection years (default: 5)")

    args = parser.parse_args()
    res = analyze_reverse_dcf(
        args.price,
        args.distb_shares,
        args.total_shares,
        args.fcf,
        args.wacc,
        args.terminal,
        args.years,
        net_debt_eok=args.net_debt,
        pref_mcap_eok=args.pref_mcap,
        norm_fcf_eok=args.norm_fcf,
    )

    print("=" * 68)
    print("📈 역DCF 정밀 분석 결과 (Korean Stock Analyzer v4.1 — Equity & EV 병기)")
    print("=" * 68)
    print(f"현재 주가 (단일 앵커): {res['price']:,.0f}원")
    print(f"총 발행주식수:         {res['total_shares']:,}주 (명목 시총: {res['market_cap_total_eok']:,.2f}억 원)")
    print(f"공시 유통주식수:       {res['distb_shares']:,}주 (자사주: {res['treasury_shares']:,}주 차감)")
    print(f"실질 유통 시가총액:    {res['market_cap_distb_eok']:,.2f}억 원")
    if res["pref_mcap_eok"] != 0.0 or res["net_debt_eok"] != 0.0:
        nd_label = f"순차입금 +{res['net_debt_eok']:,.2f}억" if res["net_debt_eok"] >= 0 else f"가용 순현금 {res['net_debt_eok']:,.2f}억"
        pref_label = f" + 우선주시총 {res['pref_mcap_eok']:,.2f}억" if res["pref_mcap_eok"] > 0 else ""
        print(f"실질 기업가치 (EV):    {res['ev_distb_eok']:,.2f}억 원 (유통시총{pref_label} + {nd_label})")
    norm_str = f" | 정규화/지배주주 FCF: {res['norm_fcf_eok']:,.1f}억 원" if res["norm_fcf_eok"] is not None else ""
    print(f"기준 FCF: {res['fcf_eok']:,.1f}억 원{norm_str} | WACC: {res['wacc']*100:.1f}% | 영구성장률: {res['terminal_g']*100:.1f}% | 기간: {res['years']}년")
    print("-" * 68)

    if res["status"] == "deficit" and (res["norm_fcf_eok"] is None or res["norm_fcf_eok"] <= 0):
        print("결과: FCF 적자(음수)로 인해 역DCF 계산 불가 (P/B, EV/EBITDA 대체 플랜B 권장)")
    elif args.wacc <= args.terminal:
        print("결과: 할인율(WACC)이 영구성장률 이하여서 계산 불가. WACC > 영구성장률이어야 합니다.")
    else:
        if res["implied_g_distb_pct"] is not None:
            print(f"▶ ① [보고 FCF ({res['fcf_eok']:,.1f}억) | 유통시총(Equity) 기준] 요구 성장률: 연간 {res['implied_g_distb_pct']:+.2f}% (총주식 기준: {res['implied_g_total_pct']:+.2f}%)")
        if (res["net_debt_eok"] != 0.0 or res["pref_mcap_eok"] != 0.0) and res["implied_g_ev_pct"] is not None:
            ev_note = "차입기업 필수 기준" if res["net_debt_eok"] > 0 else "주주환원 30%+ 검증 시 유효"
            print(f"▶ ② [보고 FCF ({res['fcf_eok']:,.1f}억) | 실질 기업가치(EV) 기준] 요구 성장률: 연간 {res['implied_g_ev_pct']:+.2f}% ({ev_note})")
        if res["norm_fcf_eok"] is not None:
            if res["norm_g_distb_pct"] is not None:
                print(f"▶ ③ [정규화 FCF ({res['norm_fcf_eok']:,.1f}억) | 유통시총(Equity) 기준] 요구 성장률: 연간 {res['norm_g_distb_pct']:+.2f}%")
            if (res["net_debt_eok"] != 0.0 or res["pref_mcap_eok"] != 0.0) and res["norm_g_ev_pct"] is not None:
                print(f"▶ ④ [정규화 FCF ({res['norm_fcf_eok']:,.1f}억) | 실질 기업가치(EV) 기준] 요구 성장률: 연간 {res['norm_g_ev_pct']:+.2f}%")
    print("=" * 68)
