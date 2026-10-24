"""
Reverse DCF Calculator for Korean Stock Analyzer (v4.2)
Calculates implied FCF CAGR based on:
1. Floating Equity Market Cap (distb_stock_co * price) [Conservative Equity Baseline]
2. Enterprise Value (EV = Floating Market Cap + Preferred Market Cap + Net Debt) [Corporate Finance Standard]
3. Core Operating EV (SOTP Mode: EV - Validated Non-Operating Assets when Non-Op Assets >= 20% of Book/Market Cap)
Supports both Reported FCF (--fcf) and Normalized/Controlling FCF (--norm-fcf),
and exports deterministic results directly to Single Source of Truth (valuation_metrics.json)
to prevent AI manual transcription errors.
"""

import sys
import os
import json
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
        return None

    low = -0.99
    high = 10.0
    TOLERANCE = 1e-05
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
    non_op_assets_eok=0.0,
    non_op_haircut=0.30,
    controlling_equity_eok=0.0,
):
    """
    Full Reverse DCF analysis comparing:
    - Floating Shares Market Cap (Equity Value) vs Total Shares Market Cap
    - Enterprise Value (EV = Floating Market Cap + Preferred Market Cap + Net Debt)
    - Core Operating EV (SOTP: EV - Non-Operating Assets * (1 - haircut)) when Non-Op >= 20%
    - Reported FCF vs Normalized/Controlling FCF (if provided)
    """
    fcf_won = fcf_eok * 100000000.0
    target_val_distb = price * distb_shares
    target_val_total = price * total_shares
    target_ev_distb = (
        target_val_distb
        + (pref_mcap_eok * 100000000.0)
        + (net_debt_eok * 100000000.0)
    )

    mcap_distb_eok = round(target_val_distb / 100000000.0, 2)
    mcap_total_eok = round(target_val_total / 100000000.0, 2)
    ev_distb_eok = round(target_ev_distb / 100000000.0, 2)

    # SOTP Non-Operating Asset Decomposition Check (20% Rule)
    non_op_ratio_mcap_pct = (
        round((non_op_assets_eok / mcap_distb_eok) * 100.0, 2)
        if mcap_distb_eok > 0
        else 0.0
    )
    non_op_ratio_book_pct = (
        round((non_op_assets_eok / controlling_equity_eok) * 100.0, 2)
        if controlling_equity_eok > 0
        else 0.0
    )
    sotp_triggered = (non_op_ratio_mcap_pct >= 20.0) or (non_op_ratio_book_pct >= 20.0)
    recognized_non_op_eok = round(non_op_assets_eok * (1.0 - non_op_haircut), 2)
    core_op_ev_eok = round(ev_distb_eok - recognized_non_op_eok, 2)
    target_core_op_ev_won = max(0.0, core_op_ev_eok * 100000000.0)

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
        "market_cap_distb_eok": mcap_distb_eok,
        "market_cap_total_eok": mcap_total_eok,
        "ev_distb_eok": ev_distb_eok,
        "non_op_assets_eok": round(non_op_assets_eok, 2),
        "non_op_haircut": non_op_haircut,
        "recognized_non_op_eok": recognized_non_op_eok,
        "non_op_ratio_mcap_pct": non_op_ratio_mcap_pct,
        "non_op_ratio_book_pct": non_op_ratio_book_pct,
        "sotp_triggered": sotp_triggered,
        "core_op_ev_eok": core_op_ev_eok,
        "implied_g_distb_pct": None,
        "implied_g_total_pct": None,
        "implied_g_ev_pct": None,
        "implied_g_core_ev_pct": None,
        "norm_g_distb_pct": None,
        "norm_g_ev_pct": None,
        "norm_g_core_ev_pct": None,
    }

    if fcf_eok > 0:
        g_distb = find_implied_growth(target_val_distb, fcf_won, wacc, terminal_g, years)
        g_total = find_implied_growth(target_val_total, fcf_won, wacc, terminal_g, years)
        g_ev = (
            find_implied_growth(target_ev_distb, fcf_won, wacc, terminal_g, years)
            if target_ev_distb > 0
            else None
        )
        g_core_ev = (
            find_implied_growth(target_core_op_ev_won, fcf_won, wacc, terminal_g, years)
            if (sotp_triggered and target_core_op_ev_won > 0)
            else None
        )

        res["implied_g_distb_pct"] = round(g_distb * 100, 2) if g_distb is not None else None
        res["implied_g_total_pct"] = round(g_total * 100, 2) if g_total is not None else None
        res["implied_g_ev_pct"] = round(g_ev * 100, 2) if g_ev is not None else None
        res["implied_g_core_ev_pct"] = round(g_core_ev * 100, 2) if g_core_ev is not None else None

    if norm_fcf_eok is not None and norm_fcf_eok > 0:
        norm_won = norm_fcf_eok * 100000000.0
        ng_distb = find_implied_growth(target_val_distb, norm_won, wacc, terminal_g, years)
        ng_ev = (
            find_implied_growth(target_ev_distb, norm_won, wacc, terminal_g, years)
            if target_ev_distb > 0
            else None
        )
        ng_core_ev = (
            find_implied_growth(target_core_op_ev_won, norm_won, wacc, terminal_g, years)
            if (sotp_triggered and target_core_op_ev_won > 0)
            else None
        )
        res["norm_g_distb_pct"] = round(ng_distb * 100, 2) if ng_distb is not None else None
        res["norm_g_ev_pct"] = round(ng_ev * 100, 2) if ng_ev is not None else None
        res["norm_g_core_ev_pct"] = round(ng_core_ev * 100, 2) if ng_core_ev is not None else None

    return res


def update_ssot_json(json_path, dcf_res):
    """
    [v4.3] Writes or merges Reverse DCF calculation output into the Single Source of Truth JSON file
    and synchronizes canonical_html_tag (and auto_tables.md) so that if reported FCF <= 0 due to expansion
    CapEx but Owner Normalized FCF > 0, data-fcf is automatically bound to norm_fcf_eok.
    """
    import re

    payload = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            payload = {}

    payload["reverse_dcf"] = dcf_res
    fcf_v = float(dcf_res.get("fcf_eok") or 0.0)
    norm_fcf_v = float(dcf_res.get("norm_fcf_eok") or 0.0)
    eff_fcf_v = norm_fcf_v if (fcf_v <= 0 and norm_fcf_v > 0) else fcf_v

    tag = payload.get("canonical_html_tag", "")
    if tag and 'id="dcf-calculator-root"' in tag:
        tag = re.sub(r'data-fcf="[^"]*"', f'data-fcf="{eff_fcf_v:.1f}"', tag)
        if "data-reported-fcf=" in tag:
            tag = re.sub(r'data-reported-fcf="[^"]*"', f'data-reported-fcf="{fcf_v:.1f}"', tag)
        else:
            tag = tag.replace(
                f'data-fcf="{eff_fcf_v:.1f}"',
                f'data-fcf="{eff_fcf_v:.1f}" data-reported-fcf="{fcf_v:.1f}"',
            )
        payload["canonical_html_tag"] = tag

        auto_md_path = os.path.join(os.path.dirname(os.path.abspath(json_path)), "auto_tables.md")
        if os.path.exists(auto_md_path):
            try:
                with open(auto_md_path, "r", encoding="utf-8") as mf:
                    md_txt = mf.read()
                md_txt = re.sub(
                    r'<div id="dcf-calculator-root"[^>]*></div>',
                    tag,
                    md_txt,
                )
                with open(auto_md_path, "w", encoding="utf-8") as mf:
                    mf.write(md_txt)
            except Exception:
                pass

    os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Reverse DCF calculation tool (v4.3 — Equity, EV & SOTP Core EV Mode + SSOT JSON Export)"
    )
    parser.add_argument("--price", type=float, required=True, help="Current stock price (KRW, latest daily close)")
    parser.add_argument("--distb-shares", type=int, required=True, help="Floating shares (distb_stock_co, excluding treasury)")
    parser.add_argument("--total-shares", type=int, required=True, help="Total issued shares (istc_totqy)")
    parser.add_argument("--fcf", type=float, required=True, help="Base/Reported FCF in 억 원 (100M KRW)")
    parser.add_argument("--norm-fcf", type=float, default=None, help="Optional Normalized / Controlling-interest FCF in 억 원")
    parser.add_argument("--net-debt", type=float, default=0.0, help="Net Debt in 억 원 (+ for Net Debt, - for Net Cash excluding operational payables)")
    parser.add_argument("--pref-mcap", type=float, default=0.0, help="Preferred stock market cap in 억 원 (default: 0.0)")
    parser.add_argument("--non-op-assets", type=float, default=0.0, help="Non-operating assets (investment property, affiliate stakes, etc.) in 억 원")
    parser.add_argument("--non-op-haircut", type=float, default=0.30, help="Discount rate (haircut) applied to non-operating assets (default: 0.30 = 30%% discount)")
    parser.add_argument("--ctrl-equity", type=float, default=0.0, help="Controlling shareholder equity (지배주주지분) in 억 원 for 20%% SOTP check")
    parser.add_argument("--wacc", type=float, default=0.09, help="Discount rate (WACC, default: 0.09)")
    parser.add_argument("--terminal", type=float, default=0.02, help="Terminal growth rate (default: 0.02)")
    parser.add_argument("--years", type=int, default=5, help="Projection years (default: 5)")
    parser.add_argument("--export-json", type=str, default=None, help="Path to valuation_metrics.json (Single Source of Truth)")

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
        non_op_assets_eok=args.non_op_assets,
        non_op_haircut=args.non_op_haircut,
        controlling_equity_eok=args.ctrl_equity,
    )

    if args.export_json:
        update_ssot_json(args.export_json, res)

    print("====================================================================")
    print("📈 역DCF 정밀 분석 결과 (Korean Stock Analyzer v4.2 — Equity · EV · SOTP 본업EV 병기)")
    print("====================================================================")
    print(f"현재 주가 (단일 앵커): {res['price']:,.0f}원")
    print(f"총 발행주식수:         {res['total_shares']:,}주 (명목 시총: {res['market_cap_total_eok']:,.2f}억 원)")
    print(f"공시 유통주식수:       {res['distb_shares']:,}주 (자사주: {res['treasury_shares']:,}주 차감)")
    print(f"실질 유통 시가총액:    {res['market_cap_distb_eok']:,.2f}억 원")

    if res["pref_mcap_eok"] or res["net_debt_eok"]:
        nd_label = (
            f"순차입금 +{res['net_debt_eok']:,.1f}억"
            if res["net_debt_eok"] > 0
            else f"가용 순현금 {res['net_debt_eok']:,.1f}억"
        )
        pref_label = (
            f" + 우선주시총 {res['pref_mcap_eok']:,.1f}억"
            if res["pref_mcap_eok"]
            else ""
        )
        print(f"실질 기업가치 (EV):    {res['ev_distb_eok']:,.2f}억 원 (유통시총{pref_label} + {nd_label})")

    if res["non_op_assets_eok"] > 0:
        sotp_badge = "🚨 [20% 초과 — SOTP 분리 대상]" if res["sotp_triggered"] else "🟢 [20% 미만 — 일반 영업자산 중심]"
        print(
            f"비영업자산(지분·부동산): 장부가 {res['non_op_assets_eok']:,.1f}억 원 "
            f"(시총 대비 {res['non_op_ratio_mcap_pct']:.1f}%, 지배자본 대비 {res['non_op_ratio_book_pct']:.1f}%) {sotp_badge}"
        )
        if res["sotp_triggered"]:
            print(
                f"본업 기업가치(Core EV): {res['core_op_ev_eok']:,.2f}억 원 "
                f"(EV {res['ev_distb_eok']:,.1f}억 - 비영업자산 안전가치 {res['recognized_non_op_eok']:,.1f}억 [할인율 {res['non_op_haircut']*100:.0f}%])"
            )

    norm_str = (
        f" | 정규화/지배주주 FCF: {res['norm_fcf_eok']:,.1f}억 원"
        if res["norm_fcf_eok"] is not None
        else ""
    )
    print(
        f"기준 FCF: {res['fcf_eok']:,.1f}억 원{norm_str} | WACC: {res['wacc']*100:.1f}% | "
        f"영구성장률: {res['terminal_g']*100:.1f}% | 기간: {res['years']}년"
    )
    print("--------------------------------------------------------------------")

    if res["status"] == "deficit" and (res["norm_fcf_eok"] is None or res["norm_fcf_eok"] <= 0):
        print("결과: FCF 적자(음수)로 인해 역DCF 계산 불가 (P/B, EV/EBITDA 대체 플랜B 권장)")
    elif args.wacc <= args.terminal:
        print("결과: 할인율(WACC)이 영구성장률 이하여서 계산 불가. WACC > 영구성장률이어야 합니다.")
    else:
        if res["implied_g_distb_pct"] is not None:
            print(
                f"▶ ① [보고 FCF ({res['fcf_eok']:,.1f}억) | 유통시총(Equity) 기준] 요구 성장률: "
                f"연간 {res['implied_g_distb_pct']:+.2f}% (총주식 기준: {res['implied_g_total_pct']:+.2f}%)"
            )
        if (res["net_debt_eok"] or res["pref_mcap_eok"]) and res["implied_g_ev_pct"] is not None:
            ev_note = "차입기업 필수 기준" if res["net_debt_eok"] > 0 else "주주환원 30%+ 검증 시 유효"
            print(
                f"▶ ② [보고 FCF ({res['fcf_eok']:,.1f}억) | 실질 기업가치(EV) 기준] 요구 성장률: "
                f"연간 {res['implied_g_ev_pct']:+.2f}% ({ev_note})"
            )
        if res["sotp_triggered"] and res["implied_g_core_ev_pct"] is not None:
            print(
                f"▶ ②-SOTP [보고 FCF ({res['fcf_eok']:,.1f}억) | 비영업자산 차감 본업 EV({res['core_op_ev_eok']:,.1f}억) 기준] 요구 성장률: "
                f"연간 {res['implied_g_core_ev_pct']:+.2f}%"
            )
        if res["norm_fcf_eok"] is not None and res["norm_g_distb_pct"] is not None:
            print(
                f"▶ ③ [정규화 FCF ({res['norm_fcf_eok']:,.1f}억) | 유통시총(Equity) 기준] 요구 성장률: "
                f"연간 {res['norm_g_distb_pct']:+.2f}%"
            )
            if (res["net_debt_eok"] or res["pref_mcap_eok"]) and res["norm_g_ev_pct"] is not None:
                print(
                    f"▶ ④ [정규화 FCF ({res['norm_fcf_eok']:,.1f}억) | 실질 기업가치(EV) 기준] 요구 성장률: "
                    f"연간 {res['norm_g_ev_pct']:+.2f}%"
                )
            if res["sotp_triggered"] and res["norm_g_core_ev_pct"] is not None:
                print(
                    f"▶ ④-SOTP [정규화 FCF ({res['norm_fcf_eok']:,.1f}억) | 본업 EV({res['core_op_ev_eok']:,.1f}억) 기준] 요구 성장률: "
                    f"연간 {res['norm_g_core_ev_pct']:+.2f}%"
                )
    print("====================================================================")