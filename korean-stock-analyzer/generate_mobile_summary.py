import os
import sys
import re
import glob
import shutil
from datetime import datetime

STOCK_RESEARCH_DIR = r"e:\antigravity-work\my-skill\Stock_Research"
DEFAULT_OUTPUT_LOCAL = os.path.join(STOCK_RESEARCH_DIR, "00_모바일_통합요약본.html")
DEFAULT_OUTPUT_GDRIVE = r"G:\내 드라이브\01.현탁\01.재테크(G)\02. 보유\00_모바일_통합요약본.html"


def clean_md(text: str) -> str:
    if not text:
        return "-"
    text = text.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.*?)`", r"<span class='code-badge'>\1</span>", text)
    return text.strip()


def extract_multiline_trigger(sec4_text: str, header_patterns: list, stop_patterns: list) -> str:
    """
    Extracts a trigger section (single-line or multi-line sub-bullets) from Section 4
    regardless of whether the colon ':' is inside or outside '**...**'.
    """
    lines = sec4_text.splitlines()
    start_idx = None
    first_line_remainder = ""

    for i, raw_line in enumerate(lines):
        s_line = raw_line.strip()
        if not s_line:
            continue
        for pat in header_patterns:
            m = re.match(pat, s_line, re.IGNORECASE)
            if m:
                start_idx = i
                first_line_remainder = m.group(1).strip() if m.lastindex and m.group(1) else ""
                break
        if start_idx is not None:
            break

    if start_idx is None:
        return "-"

    collected = []
    if first_line_remainder and first_line_remainder not in (":", "-", "*"):
        collected.append(clean_md(first_line_remainder))

    for j in range(start_idx + 1, len(lines)):
        s = lines[j].strip()
        if not s:
            if collected:
                continue
            else:
                continue

        # Stop if we hit a markdown heading, horizontal rule, HTML div, or next top-level section bullet
        if s.startswith("#") or s.startswith("---") or s.startswith("<div"):
            break

        hit_stop = False
        for sp in stop_patterns:
            if re.match(sp, s, re.IGNORECASE):
                hit_stop = True
                break
        if hit_stop:
            break

        # Clean bullet prefix (-, *, 1., 2., etc.)
        cleaned_item = re.sub(r"^(?:[-*•]|\d+\.)\s*", "", s).strip()
        if cleaned_item:
            collected.append(clean_md(cleaned_item))
        if len(collected) >= 8:
            break

    return "<br>".join(collected) if collected else "-"


def extract_opt_valuation_metric(comp_dir: str, p3_text: str) -> str:
    """
    Extracts the #1 ranked valuation metric (e.g. PBR, PER, EV/EBITDA, 배당수익률).
    First inspects Phase1_Report.md (Section 8 table / text), then falls back to Phase3_DCF.md.
    """
    p1_path = os.path.join(comp_dir, "3_Final_Reports", "Phase1_Report.md")
    if os.path.exists(p1_path):
        try:
            with open(p1_path, "r", encoding="utf-8") as f:
                p1_text = f.read()
            m_row = re.search(r"\|\s*\*\*?(PBR|PER|EV/EBITDA|배당수익률)[^\n|]*?\*\*\s*\|[^\n]*?\|\s*\*\*?1위\b", p1_text, re.IGNORECASE)
            if m_row:
                return m_row.group(1).upper()
            m_row2 = re.search(r"\|\s*(PBR|PER|EV/EBITDA|배당수익률)[^\n|]*?\|[^\n]*?\|\s*\*\*?1위\b", p1_text, re.IGNORECASE)
            if m_row2:
                return m_row2.group(1).upper()
            m_adopt = re.search(r"채택\s*지표[^\n]*?1순위[^\n]*?\b(PBR|PER|EV/EBITDA|배당수익률)\b", p1_text, re.IGNORECASE)
            if m_adopt:
                return m_adopt.group(1).upper()
        except Exception:
            pass

    m_p3 = re.search(r"1순위\s*지표[^\n*]*?\*\*?(?:Target\s*)?(PBR|PER|EV/EBITDA|배당수익률)\b", p3_text, re.IGNORECASE)
    if m_p3:
        return m_p3.group(1).upper()

    m_hdr = re.search(r"\|\s*(?:구분|시나리오)\s*\|[^\n]*?\b(PBR|PER|EV/EBITDA)\b", p3_text, re.IGNORECASE)
    if m_hdr:
        return m_hdr.group(1).upper()

    return "-"


def extract_company_summary(comp_dir: str, comp_name: str) -> dict:
    p3_path = os.path.join(comp_dir, "3_Final_Reports", "Phase3_DCF.md")
    thesis_path = os.path.join(comp_dir, "Weekly_Trend", "thesis_log.md")

    data = {
        "name": comp_name,
        "updated_at": "-",
        "opt_metric": "-",
        "s_curve_badge": "-",
        "hybrid_badge": "-",
        "free_call_option": False,
        "current_price": "-",
        "equity_growth": "-",
        "ev_growth": "-",
        "implied_growth": "-",
        "bull_target": "-",
        "base_target": "-",
        "tp1_target": "-",
        "bear_target": "-",
        "rr_ratio": "-",
        "stop_pin": "-",
        "buy_prices": "-",
        "primary_target": "-",
        "opinion": "-",
        "buy_trigger": "-",
        "sell_trigger": "-",
        "theses": [],
    }

    if not os.path.exists(p3_path):
        return data

    mtime = os.path.getmtime(p3_path)
    data["updated_at"] = datetime.fromtimestamp(mtime).strftime("%y.%m.%d")

    with open(p3_path, "r", encoding="utf-8") as f:
        text = f.read()

    # 0. Optimal Valuation Metric (Phase 1 / Phase 3)
    data["opt_metric"] = extract_opt_valuation_metric(comp_dir, text)

    # 1. S-Curve & Hybrid Badges & Free Call Option
    m_sc = re.search(r"\[S-Curve 국면 인증 뱃지\]\*\*:\s*(.+)", text)
    if m_sc:
        data["s_curve_badge"] = clean_md(m_sc.group(1))

    m_hb = re.search(r"\[하이브리드 밸류체인 지위\]\*\*:\s*(.+)", text)
    if m_hb:
        data["hybrid_badge"] = clean_md(m_hb.group(1))

    if re.search(r"(?:무상\s*콜옵션|0원\s*무상|할증\s*0%|sc_mult\s*=\s*(?:1\.00|1\.0|1\b)|신사업\s*익스포저\s*과대\s*차단|단독\s*매출\s*미공시)", text) or "무상 콜옵션" in data["s_curve_badge"]:
        data["free_call_option"] = True

    # 2. Current price from dcf-calculator-root or table
    m_root = re.search(r'id="dcf-calculator-root"[^>]*data-price="([0-9.,]+)"', text)
    if m_root:
        try:
            p_val = int(float(m_root.group(1).replace(",", "")))
            data["current_price"] = f"{p_val:,}원"
        except Exception:
            data["current_price"] = m_root.group(1) + "원"
    else:
        m_cp = re.search(r"\|\s*\*\*?현재 주가.*?\*\*\s*\|\s*([^|]+)\|", text)
        if m_cp:
            data["current_price"] = clean_md(m_cp.group(1))

    # Stop pin & Buy prices from root tag or text
    m_stop_tag = re.search(r'data-stop-pin="([0-9.,]+)"', text)
    if m_stop_tag:
        try:
            sp_val = int(float(m_stop_tag.group(1).replace(",", "")))
            data["stop_pin"] = f"{sp_val:,}원"
        except Exception:
            data["stop_pin"] = m_stop_tag.group(1) + "원"

    m_b1 = re.search(r'data-buy1="([0-9.,]+)"', text)
    m_b2 = re.search(r'data-buy2="([0-9.,]+)"', text)
    m_b3 = re.search(r'data-buy3="([0-9.,]+)"', text)
    if m_b1 and m_b2 and m_b3:
        try:
            b1_v = f"{int(float(m_b1.group(1).replace(',', ''))):,}원"
            b2_v = f"{int(float(m_b2.group(1).replace(',', ''))):,}원"
            b3_v = f"{int(float(m_b3.group(1).replace(',', ''))):,}원"
            data["buy_prices"] = f"1차 {b1_v} | 2차 {b2_v} | 3차 {b3_v}"
        except Exception:
            pass

    if data["stop_pin"] == "-":
        m_sp_text = re.search(r"(?:1단계\s*(?:초입\s*)?고정\s*안전핀|고정\s*바닥\s*안전핀|Hard\s*Stop\s*1)[^\n`*]*?`?([0-9,]{4,10}원)`?", text, re.IGNORECASE)
        if m_sp_text:
            data["stop_pin"] = m_sp_text.group(1)

    # 3. Implied growth rate (역DCF 요구성장률 — Equity & EV 병기 지원) — inspect Section 1
    sec1_match = re.search(r"##\s*1\..*?(?=##\s*2\.|\Z)", text, re.DOTALL)
    sec1_text = sec1_match.group(0) if sec1_match else text

    m_eq = re.search(r"유통시총\(Equity\)\s*기준\s*요구성장률[^\n|]*\|\s*\*\*?(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)", sec1_text)
    m_ev = re.search(r"실질\s*기업가치\(EV[^\n|]*\|\s*\*\*?(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)", sec1_text)
    if m_eq and m_ev:
        eq_val = m_eq.group(1).replace("`", "").replace("연간", "").strip()
        ev_val = m_ev.group(1).replace("`", "").replace("연간", "").strip()
        data["equity_growth"] = eq_val
        data["ev_growth"] = ev_val
        if eq_val != ev_val:
            data["implied_growth"] = clean_md(f"Equity {eq_val} | EV {ev_val}")
        else:
            data["implied_growth"] = clean_md(f"연간 {eq_val}")
    else:
        ig_table_pat = r"\|\s*\*\*?(?:①\s*)?\[?(?:보고\s*FCF|유통시총|실질\s*기업가치)[^\n|]*\|\s*(?:[^|\n%]*\s*\|\s*)?\*\*?(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)"
        m_ig = re.search(ig_table_pat, sec1_text)
        if not m_ig:
            ig_line_pats = [
                r"(?:유통주식수|DART\s*유통주식수|실질\s*유통주식수|실질\s*EV)[^\n|]*?요구(?:하는)?\s*(?:향후\s*\d+개?년\s*)?(?:FCF\s*)?(?:연평균\s*)?성장률[^\n]*?(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)",
                r"요구(?:하는)?\s*(?:향후\s*\d+개?년\s*)?(?:FCF\s*)?(?:연평균\s*)?성장률[은:\s]*\*\*?(?:DART\s*유통주식수\s*기준\s*)?(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)",
                r"요구(?:하는)?\s*(?:향후\s*\d+개?년\s*)?(?:FCF\s*)?(?:연평균\s*)?성장률[은:\s]*\*\*?[^\n*]*?(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)",
            ]
            candidates_ig = [re.search(p, sec1_text) for p in ig_line_pats]
            candidates_ig = [m for m in candidates_ig if m is not None]
            if candidates_ig:
                m_ig = min(candidates_ig, key=lambda m: m.start())
        if not m_ig:
            for fallback_pat in [r"(연간\s*`?[+\-−]?\d+(?:\.\d+)?%`?)", r"연평균\s*\*\*?([+\-−]?\d+(?:\.\d+)?%)\*\*?"]:
                m_ig = re.search(fallback_pat, sec1_text)
                if m_ig:
                    break
        if m_ig:
            raw_ig = m_ig.group(1).replace("`", "").strip()
            if not raw_ig.startswith("연간"):
                raw_ig = "연간 " + raw_ig
            data["implied_growth"] = clean_md(raw_ig)

    # 4. Bull / Base / TP1 / Bear targets from scenario table (supports both single price and Model B / Model A side-by-side, across PER/PBR/EV-EBITDA/Yield headers)
    sec3_match = re.search(r"##\s*3\..*?(?=##\s*(?:3\.5|4)\b|\Z)", text, re.DOTALL)
    sec3_text = sec3_match.group(0) if sec3_match else text

    def _format_price_upside_cell(raw_p_cell: str, raw_u_cell: str) -> str:
        if "/" in raw_p_cell and "/" in raw_u_cell:
            p_parts = [clean_md(x) for x in raw_p_cell.split("/") if x.strip()]
            u_parts = [clean_md(x) for x in raw_u_cell.split("/") if x.strip()]
            if len(p_parts) >= 2 and len(u_parts) >= 2:
                return f"🛡️B: {p_parts[0]} ({u_parts[0]})<br>🚀A: {p_parts[1]} ({u_parts[1]})"
        price_str = clean_md(raw_p_cell)
        upside_str = clean_md(raw_u_cell)
        return f"{price_str} ({upside_str})"

    for line in sec3_text.splitlines():
        s_line = line.strip()
        if not s_line.startswith("|") or "---" in s_line:
            continue
        cells = [c.strip() for c in s_line.strip("|").split("|")]
        if len(cells) < 5:
            continue

        first_col = re.sub(r"[*`]", "", cells[0]).strip()
        target_key = None
        if re.match(r"^Bull\b", first_col, re.IGNORECASE):
            target_key = "bull_target"
        elif re.match(r"^Base\b", first_col, re.IGNORECASE):
            target_key = "base_target"
        elif re.match(r"^TP1\b", first_col, re.IGNORECASE):
            target_key = "tp1_target"
        elif re.match(r"^Bear\b", first_col, re.IGNORECASE):
            target_key = "bear_target"

        if target_key and data[target_key] == "-":
            if "원" in cells[-2]:
                data[target_key] = _format_price_upside_cell(cells[-2], cells[-1])
            elif len(cells) >= 6 and "원" in cells[-3] and "%" in cells[-2]:
                data[target_key] = _format_price_upside_cell(cells[-3], cells[-2])

    # Extract primary target for quick action header
    if data["tp1_target"] != "-":
        clean_tp = re.sub(r"<br>.*", "", data["tp1_target"])
        data["primary_target"] = f"TP1: {clean_tp}"
    elif data["base_target"] != "-":
        clean_base = re.sub(r"<br>.*", "", data["base_target"])
        data["primary_target"] = f"Base: {clean_base}"

    if data["tp1_target"] != "-" and data["base_target"] != "-":
        data["base_target"] = f"{data['base_target']}<br><span class='rr-sub'>🎯 TP1(단기1차): {data['tp1_target']}</span>"

    # 5. Dual-Gate R:R Ratio (supports Gate 1 & Gate 2 across lines, with fallback to single R:R)
    m_g1 = re.search(r"Gate\s*1[\s\S]{0,250}?([0-9]+\.[0-9]+\s*:\s*1|음수|산출\s*불가|바닥권)", sec3_text, re.IGNORECASE)
    m_g2 = re.search(r"Gate\s*2[\s\S]{0,250}?([0-9]+\.[0-9]+\s*:\s*1|음수|산출\s*불가|관망|차단|진입차단)", sec3_text, re.IGNORECASE)
    if m_g1 and m_g2:
        g1_str = re.sub(r"\s+", "", m_g1.group(1))
        g2_str = re.sub(r"\s+", "", m_g2.group(1))
        data["rr_ratio"] = f"G1 {g1_str} | G2 {g2_str}"
    else:
        rr_patterns = [
            r"Base\s*(?:기준\s*)?(?:R:R|손익비|상승폭)[^\n]*?([0-9]+\.[0-9]+\s*:\s*1)",
            r"순수\s*Base\s*대\s*Bear\s*손익비[^\n]*?([0-9]+\.[0-9]+\s*:\s*1)",
            r"R:R\s*Ratio[\s\S]{0,350}?([0-9]+\.[0-9]+\s*:\s*1)",
            r"손익비[\s\S]{0,350}?([0-9]+\.[0-9]+\s*:\s*1)",
        ]
        for pat in rr_patterns:
            m_rr = re.search(pat, sec3_text, re.IGNORECASE)
            if m_rr:
                data["rr_ratio"] = re.sub(r"\s+", " ", m_rr.group(1)).strip()
                break

    # 6. Opinion / Buy Trigger / Sell Trigger in Section 4
    sec4_match = re.search(r"##\s*4\..*", text, re.DOTALL)
    sec4_text = sec4_match.group(0) if sec4_match else text

    m_op = re.search(r"[-*]\s*\*\*?(?:최종\s*)?투자의견\s*:?\*\*?\s*:?\s*(.+)", sec4_text)
    if not m_op:
        m_op = re.search(r"[-*]\s*\*\*?(?:손익비\s*)?판정\s*:?\*\*?\s*:?\s*([^\n]+)", sec3_text)
    m_band = re.search(r"[-*]\s*\*\*?목표(?:주가|가)\s*밴드\s*:?\*\*?\s*:?\s*(.+)", sec4_text)
    op_parts = []
    if m_op:
        op_parts.append(clean_md(m_op.group(1)))
    if m_band:
        op_parts.append("목표밴드: " + clean_md(m_band.group(1)))
    if op_parts:
        data["opinion"] = " | ".join(op_parts)

    buy_headers = [
        r"^(?:[-*•]|\d+\.)\s*\*\*?매수\s*트리거[^:*]*:?\*\*?\s*:?\s*(.*)",
        r"^(?:[-*•]|\d+\.)\s*\*\*?\[?신규\s*(?:진입|투자자)[^:*]*:?\*\*?\s*:?\s*(.*)",
        r"^(?:[-*•]|\d+\.)\s*\*\*?실전\s*포트폴리오\s*대응\s*지침\s*:?\*\*?\s*:?\s*(.*)",
    ]
    buy_stops = [
        r"^(?:[-*•]|\d+\.)\s*\*\*?(?:매도|손절|익절|차익|비중\s*축소)",
        r"^(?:[-*•]|\d+\.)\s*\*\*?핵심\s*모니터링",
    ]
    data["buy_trigger"] = extract_multiline_trigger(sec4_text, buy_headers, buy_stops)

    sell_headers = [
        r"^(?:[-*•]|\d+\.)\s*\*\*?(?:매도|손절|익절)[^:*]*트리거[^:*]*:?\*\*?\s*:?\s*(.*)",
        r"^(?:[-*•]|\d+\.)\s*\*\*?손절/비중\s*축소\s*기준[^:*]*:?\*\*?\s*:?\s*(.*)",
    ]
    sell_stops = [
        r"^(?:[-*•]|\d+\.)\s*\*\*?핵심\s*모니터링",
    ]
    data["sell_trigger"] = extract_multiline_trigger(sec4_text, sell_headers, sell_stops)

    # 7. Thesis Log
    if os.path.exists(thesis_path):
        with open(thesis_path, "r", encoding="utf-8") as tf:
            for line in tf:
                if "|" in line and ("가설" in line or "진행 중" in line or "검증 완료" in line):
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    if len(parts) >= 3 and parts[0] not in ("기록 일자", "기록일자", ":---"):
                        date_str = parts[0]
                        title_str = parts[1]
                        desc_str = parts[2]
                        if len(desc_str) > 110:
                            desc_str = desc_str[:110] + "..."
                        data["theses"].append(f"[{date_str}] {clean_md(title_str)}: {clean_md(desc_str)}")

    return data


def build_mobile_html(items: list) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards_html = ""
    for item in items:
        theses_li = ""
        if item["theses"]:
            for th in item["theses"][-3:]:
                theses_li += f"<li>{th}</li>"
        else:
            theses_li = "<li>등록된 가설 로그 없음</li>"

        # Chips for header
        metric_chip = f'<span class="chip-metric">👑 {item["opt_metric"]} 1위</span>' if item["opt_metric"] != "-" else ""
        free_opt_chip = '<span class="chip-free-opt">🎁 무상콜옵션 (할증0%)</span>' if item["free_call_option"] else ""

        # Core Action Bar (Safety Pin & Target)
        action_bar_html = ""
        has_stop = item["stop_pin"] != "-"
        has_tgt = item["primary_target"] != "-"
        if has_stop or has_tgt:
            stop_sub = f"분할매수가: {item['buy_prices']}" if item["buy_prices"] != "-" else "일봉 종가 이탈 시 기계적 손절"
            tgt_sub = f"실전 손익비: {item['rr_ratio']}" if item["rr_ratio"] != "-" else "1차 목표가 도달 시 반타작 분할익절"
            action_bar_html = f"""
            <div class="core-action-bar">
                <div class="action-card stop-card">
                    <div class="act-label">🚨 1단계 고정 안전핀 (Hard Stop)</div>
                    <div class="act-val stop-val">{item['stop_pin']}</div>
                    <div class="act-sub">{stop_sub}</div>
                </div>
                <div class="action-card target-card">
                    <div class="act-label">🎯 1차 목표가 (Base / TP1)</div>
                    <div class="act-val target-val">{item['primary_target']}</div>
                    <div class="act-sub">{tgt_sub}</div>
                </div>
            </div>
            """

        # DCF Implied Growth Display (Dual chips if Equity & EV available)
        if item["equity_growth"] != "-" and item["ev_growth"] != "-":
            dcf_display = f"""
            <div class="growth-chips-row">
                <span class="growth-chip eq">주주(Eq) {item['equity_growth']}</span>
                <span class="growth-chip ev">실질(EV) {item['ev_growth']}</span>
            </div>
            <div class="rr-line">손익비: <strong>{item['rr_ratio']}</strong></div>
            """
        else:
            dcf_display = f"""
            <div class="m-val">{item['implied_growth']} <span class="rr-sub">(R:R {item['rr_ratio']})</span></div>
            """

        cards_html += f"""
        <div class="stock-card" data-name="{item['name']}">
            <div class="card-header">
                <div class="title-wrap">
                    <span class="stock-title">{item['name']}</span>
                    {metric_chip}
                    {free_opt_chip}
                    <span class="update-date">({item['updated_at']})</span>
                </div>
                <div class="price-pill">현재가: {item['current_price']}</div>
            </div>
            <div class="badges-row">
                <div class="badge-line">🧭 <strong>S-Curve:</strong> {item['s_curve_badge']}</div>
                <div class="badge-line">🛡️ <strong>밸류체인:</strong> {item['hybrid_badge']}</div>
            </div>
            {action_bar_html}
            <div class="metrics-grid">
                <div class="metric-box bull">
                    <div class="m-label">Bull (호황 목표가)</div>
                    <div class="m-val">{item['bull_target']}</div>
                </div>
                <div class="metric-box base">
                    <div class="m-label">Base (기본 적정가)</div>
                    <div class="m-val">{item['base_target']}</div>
                </div>
                <div class="metric-box bear">
                    <div class="m-label">Bear (하방 지지선)</div>
                    <div class="m-val">{item['bear_target']}</div>
                </div>
                <div class="metric-box dcf">
                    <div class="m-label">역DCF 실질 요구성장률 / 손익비</div>
                    {dcf_display}
                </div>
            </div>
            <div class="triggers-box">
                <div class="t-row"><strong>🎯 의견/밴드:</strong> {item['opinion']}</div>
                <div class="t-row buy-t"><strong>🔵 매수 트리거:</strong><br>{item['buy_trigger']}</div>
                <div class="t-row sell-t"><strong>🔴 매도 트리거:</strong><br>{item['sell_trigger']}</div>
            </div>
            <details class="thesis-details">
                <summary>💡 핵심 투자 가설 로그 보기 ({len(item['theses'])}건)</summary>
                <ul>{theses_li}</ul>
            </details>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <title>📱 내 보유·관심종목 모바일 1페이지 통합 요약본 (v4.1)</title>
    <style>
        :root {{
            --bg: #0b1120;
            --card: #151e32;
            --border: #24324f;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #38bdf8;
            --bull: #34d399;
            --base: #60a5fa;
            --bear: #f87171;
            --gold: #fbbf24;
            --purple: #c084fc;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            padding: 12px;
            font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
        }}
        .top-bar {{
            max-width: 760px;
            margin: 0 auto 14px auto;
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }}
        .top-bar h1 {{
            margin: 0 0 4px 0;
            font-size: 1.25rem;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .top-bar .sub {{
            font-size: 0.82rem;
            color: var(--muted);
            margin-bottom: 12px;
        }}
        .search-input {{
            width: 100%;
            padding: 10px 14px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: #0b1120;
            color: var(--text);
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-input:focus {{
            border-color: var(--accent);
        }}
        .container {{
            max-width: 760px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 14px;
        }}
        .stock-card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 15px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.28);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 10px;
            margin-bottom: 10px;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .title-wrap {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
        }}
        .stock-title {{
            font-size: 1.22rem;
            font-weight: 800;
            color: #fff;
            letter-spacing: -0.02em;
        }}
        .chip-metric {{
            background: rgba(96, 165, 250, 0.15);
            color: #93c5fd;
            border: 1px solid rgba(96, 165, 250, 0.35);
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 0.72rem;
            font-weight: 700;
        }}
        .chip-free-opt {{
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.35);
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 0.72rem;
            font-weight: 700;
        }}
        .update-date {{
            font-size: 0.75rem;
            color: var(--muted);
        }}
        .price-pill {{
            background: #0b1120;
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 4px 12px;
            border-radius: 999px;
            font-size: 0.88rem;
            font-weight: 800;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.15);
        }}
        .badges-row {{
            font-size: 0.8rem;
            background: rgba(11, 17, 32, 0.7);
            padding: 8px 10px;
            border-radius: 8px;
            margin-bottom: 10px;
            border: 1px solid rgba(36, 50, 79, 0.6);
        }}
        .badge-line {{ margin-bottom: 4px; line-height: 1.4; }}
        .badge-line:last-child {{ margin-bottom: 0; }}
        .code-badge {{
            background: rgba(56, 189, 248, 0.15);
            color: #7dd3fc;
            padding: 1px 6px;
            border-radius: 4px;
            font-size: 0.78rem;
        }}
        /* Core Action Bar: Safety Pin & Target */
        .core-action-bar {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 10px;
        }}
        .action-card {{
            border-radius: 10px;
            padding: 9px 11px;
            border: 1px solid var(--border);
        }}
        .stop-card {{
            background: linear-gradient(145deg, rgba(239, 68, 68, 0.1) 0%, rgba(15, 23, 42, 0.8) 100%);
            border-color: rgba(248, 113, 113, 0.4);
        }}
        .target-card {{
            background: linear-gradient(145deg, rgba(59, 130, 246, 0.1) 0%, rgba(15, 23, 42, 0.8) 100%);
            border-color: rgba(96, 165, 250, 0.4);
        }}
        .act-label {{
            font-size: 0.7rem;
            color: var(--muted);
            font-weight: 600;
        }}
        .act-val {{
            font-size: 1.05rem;
            font-weight: 800;
            margin: 2px 0;
            letter-spacing: -0.01em;
        }}
        .stop-val {{ color: var(--bear); }}
        .target-val {{ color: var(--base); }}
        .act-sub {{
            font-size: 0.7rem;
            color: #cbd5e1;
            word-break: keep-all;
            line-height: 1.3;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 10px;
        }}
        .metric-box {{
            background: #0b1120;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px 10px;
        }}
        .m-label {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 2px; }}
        .m-val {{ font-size: 0.9rem; font-weight: 700; line-height: 1.35; }}
        .bull .m-val {{ color: var(--bull); }}
        .base .m-val {{ color: var(--base); }}
        .bear .m-val {{ color: var(--bear); }}
        .growth-chips-row {{
            display: flex;
            gap: 5px;
            margin: 3px 0 2px 0;
            flex-wrap: wrap;
        }}
        .growth-chip {{
            padding: 1px 6px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
        }}
        .growth-chip.eq {{
            background: rgba(56, 189, 248, 0.15);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.3);
        }}
        .growth-chip.ev {{
            background: rgba(192, 132, 252, 0.15);
            color: #c084fc;
            border: 1px solid rgba(192, 132, 252, 0.3);
        }}
        .rr-line {{ font-size: 0.72rem; color: var(--muted); margin-top: 2px; }}
        .rr-sub {{ font-size: 0.75rem; color: var(--muted); font-weight: normal; }}
        .triggers-box {{
            font-size: 0.82rem;
            border-top: 1px dashed var(--border);
            padding-top: 8px;
        }}
        .t-row {{ margin-bottom: 7px; line-height: 1.45; }}
        .buy-t {{ color: #93c5fd; }}
        .sell-t {{ color: #fca5a5; }}
        .thesis-details {{
            margin-top: 8px;
            background: rgba(11, 17, 32, 0.6);
            border-radius: 8px;
            padding: 8px 10px;
            font-size: 0.8rem;
            border: 1px solid rgba(36, 50, 79, 0.5);
        }}
        .thesis-details summary {{
            cursor: pointer;
            font-weight: 700;
            color: var(--accent);
        }}
        .thesis-details ul {{
            margin: 8px 0 0 0;
            padding-left: 18px;
        }}
        .thesis-details li {{ margin-bottom: 5px; color: #cbd5e1; }}
        @media (max-width: 480px) {{
            .core-action-bar {{ grid-template-columns: 1fr; }}
            .metrics-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="top-bar">
        <h1>📱 내 투자 종목 모바일 1페이지 통합 요약본</h1>
        <div class="sub">총 {len(items)}개 분석 종목 | 최종 갱신: {now_str} | v4.1 회계·수리 무결성 & S-Curve 연동</div>
        <input type="text" id="searchBox" class="search-input" placeholder="🔍 종목명 검색 (예: 삼성전자, 하나머티리얼즈, GST...)" oninput="filterCards()">
    </div>
    <div class="container" id="cardContainer">
        {cards_html}
    </div>
    <script>
        function filterCards() {{
            const q = document.getElementById('searchBox').value.trim().toLowerCase();
            const cards = document.querySelectorAll('.stock-card');
            cards.forEach(c => {{
                const name = c.getAttribute('data-name').toLowerCase();
                const text = c.innerText.toLowerCase();
                c.style.display = (name.includes(q) || text.includes(q)) ? 'block' : 'none';
            }});
        }}
    </script>
</body>
</html>
"""
    return html


def main(output_local: str = DEFAULT_OUTPUT_LOCAL):
    items = []
    if not os.path.exists(STOCK_RESEARCH_DIR):
        print(f"Directory not found: {STOCK_RESEARCH_DIR}")
        return

    for entry in sorted(os.listdir(STOCK_RESEARCH_DIR)):
        if entry == "Global_Trend":
            continue
        comp_dir = os.path.join(STOCK_RESEARCH_DIR, entry)
        if os.path.isdir(comp_dir):
            p3_file = os.path.join(comp_dir, "3_Final_Reports", "Phase3_DCF.md")
            if os.path.exists(p3_file):
                summary = extract_company_summary(comp_dir, entry)
                items.append(summary)

    html_out = build_mobile_html(items)
    with open(output_local, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[완료] 로컬 통합 요약본 생성: {output_local} (총 {len(items)}개 기업)")

    gdrive_dir = os.path.dirname(DEFAULT_OUTPUT_GDRIVE)
    if os.path.exists(gdrive_dir) and output_local == DEFAULT_OUTPUT_LOCAL:
        try:
            shutil.copy2(output_local, DEFAULT_OUTPUT_GDRIVE)
            print(f"[완료] 구글 드라이브 자동 복사: {DEFAULT_OUTPUT_GDRIVE}")
        except Exception as e:
            print(f"[참고] 구글 드라이브 복사 생략: {e}")


if __name__ == "__main__":
    out_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_LOCAL
    main(out_arg)

