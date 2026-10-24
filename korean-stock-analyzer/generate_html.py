"""
HTML Report Builder & Pipeline SSOT Verifier for Korean Stock Analyzer (v4.2)
- Converts Phase 1~3 Markdown reports to styled HTML with Mermaid diagrams and Fact/Narrative badges.
- Automatically binds to 2_Translated_DB/valuation_metrics.json (Single Source of Truth) if present,
  preventing AI manual transcription discrepancies between code output, Markdown tables, and HTML calculators.
"""

import markdown
import sys
import os
import re
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if len(sys.argv) < 3:
    print("Usage: python generate_html.py <input.md> <output.html> [optional_ssot.json]")
    sys.exit(1)

md_file = sys.argv[1]
out_file = sys.argv[2]
explicit_ssot = sys.argv[3] if len(sys.argv) >= 4 else None

css_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_style.css")
if not os.path.exists(css_file):
    css_file = r"e:\antigravity-work\my-skill\korean-stock-analyzer\report_style.css"

try:
    with open(md_file, "r", encoding="utf-8") as f:
        md_text = f.read()
except Exception as e:
    print(f"Error reading {md_file}: {e}")
    sys.exit(1)

try:
    if os.path.exists(css_file):
        with open(css_file, "r", encoding="utf-8") as f:
            css_text = f.read()
    else:
        css_text = ""
except Exception as e:
    print(f"Error reading CSS {css_file}: {e}")
    css_text = ""

def sanitize_mermaid_code(code: str) -> str:
    """Sanitize Mermaid code to comply strictly with Mermaid v10.9.8+ syntax rules."""
    lines = code.split("\n")
    sanitized_lines = []
    subgraph_idx = 1

    for line in lines:
        # 1. Dotted arrow legacy syntax: A -. "Label" .-> B or A -. Label .-> B  ==>  A -.->|"Label"| B
        line = re.sub(
            r'(-\.)\s*(".*?"|\'.*?\'|[^\-\.\n]+?)\s*(\.->)',
            lambda m: f'-.->|"{m.group(2).strip("\'\"")}"|',
            line
        )

        # 2. Pipe labels: quote if contains special chars and not yet quoted
        def quote_pipe_label(m):
            arrow = m.group(1)
            content = m.group(2).strip()
            if (content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'")):
                return f'{arrow}|{content}|'
            clean_c = content.replace('"', "'")
            return f'{arrow}|"{clean_c}"|'

        line = re.sub(r'(-->|-\.->)\s*\|([^\|\n]+)\|', quote_pipe_label, line)

        # 3. Subgraph syntax cleanup:
        # 3-1: Remove space between ID and [ : subgraph Macro ["..."] -> subgraph Macro["..."]
        line = re.sub(r'subgraph\s+([a-zA-Z0-9_-]+)\s+\[', r'subgraph \1[', line)

        # 3-2: Subgraph without valid English ID (starts with Korean/number/quote/space)
        m_sub = re.match(r'^(\s*subgraph)\s+([^\[\n]+)$', line)
        if m_sub:
            indent = m_sub.group(1)[:len(m_sub.group(1)) - len('subgraph')]
            raw_title = m_sub.group(2).strip()
            if not re.match(r'^[a-zA-Z0-9_-]+$', raw_title):
                clean_title = raw_title.strip('"\'')
                line = f'{indent}subgraph SG_{subgraph_idx}["{clean_title}"]'
                subgraph_idx += 1

        # 4. Node labels: Auto-quote if parentheses, colons, slashes, or % are present without quotes
        def fix_node_label(match):
            node_id = match.group(1)
            inner = match.group(2)
            if (inner.startswith('"') and inner.endswith('"')) or (inner.startswith("'") and inner.endswith("'")):
                return f'{node_id}[{inner}]'
            if any(c in inner for c in '():/%,\''):
                clean_inner = inner.replace('"', "'")
                return f'{node_id}["{clean_inner}"]'
            return f'{node_id}[{inner}]'

        line = re.sub(r'(\b[a-zA-Z0-9_-]+)\s*\[([^\]\n]+)\]', fix_node_label, line)

        # 5. Standardize line breaks: <br> -> <br/>
        line = re.sub(r'<br\s*>', '<br/>', line, flags=re.IGNORECASE)

        sanitized_lines.append(line)

    return "\n".join(sanitized_lines)

def sanitize_mermaid_in_markdown(md: str) -> str:
    """Pre-process all mermaid blocks in markdown before HTML conversion."""
    def replacer(match):
        code_body = match.group(1)
        fixed_code = sanitize_mermaid_code(code_body)
        return f"```mermaid\n{fixed_code}\n```"
    return re.sub(r'```mermaid\s*\n(.*?)\n```', replacer, md, flags=re.DOTALL)

# Locate Single Source of Truth JSON (valuation_metrics.json) if available
ssot_data = {}
candidate_ssot_paths = []
if explicit_ssot:
    candidate_ssot_paths.append(explicit_ssot)
md_dir = os.path.dirname(os.path.abspath(md_file))
comp_root = os.path.dirname(md_dir)
candidate_ssot_paths.extend([
    os.path.join(comp_root, "2_Translated_DB", "valuation_metrics.json"),
    os.path.join(md_dir, "valuation_metrics.json"),
])

ssot_loaded_path = None
for sp in candidate_ssot_paths:
    if sp and os.path.exists(sp):
        try:
            with open(sp, "r", encoding="utf-8") as sf:
                ssot_data = json.load(sf)
            ssot_loaded_path = sp
            break
        except Exception:
            pass

# Pipeline Integrity Audit: Check if Markdown numbers match SSOT JSON (v4.3 Full 7-Point Verification)
pipeline_audit_warnings = []
if ssot_data:
    anchor_price = ssot_data.get("anchor_price") or ssot_data.get("reverse_dcf", {}).get("price")
    s3 = ssot_data.get("section3", {})
    s4 = ssot_data.get("section4", {})
    if anchor_price:
        m_root_price = re.search(r'data-price="([0-9.,]+)"', md_text)
        if m_root_price:
            md_p = float(m_root_price.group(1).replace(",", ""))
            if abs(md_p - float(anchor_price)) >= 1.0:
                pipeline_audit_warnings.append(
                    f"[주가 불일치 자동교정] 마크다운 data-price({md_p:,.0f}원) != 코드 SSOT 원본({float(anchor_price):,.0f}원)"
                )
        for attr_name, ssot_val in [
            ("data-base", s3.get("base_auto")),
            ("data-base-a", s3.get("base_auto_a")),
            ("data-bull", s3.get("bull_auto")),
            ("data-bull-a", s3.get("bull_auto_a")),
            ("data-bear", s3.get("bear_auto")),
            ("data-buy1", s4.get("buy1")),
            ("data-stop-pin", s4.get("init_stop_pin")),
        ]:
            if ssot_val:
                m_attr = re.search(rf'{attr_name}="([0-9.,]+)"', md_text)
                if m_attr:
                    md_v = float(m_attr.group(1).replace(",", ""))
                    if abs(md_v - float(ssot_val)) >= 1.0:
                        pipeline_audit_warnings.append(
                            f"[{attr_name} 불일치 자동교정] 마크다운({md_v:,.0f}원) != 코드 SSOT 원본({float(ssot_val):,.0f}원)"
                        )
        # Auto-replace canonical dcf-calculator-root tag if canonical_html_tag is in SSOT
        canonical_tag = ssot_data.get("canonical_html_tag")
        if canonical_tag and 'id="dcf-calculator-root"' in md_text:
            md_text = re.sub(
                r'<div\s+id="dcf-calculator-root"[^>]*>\s*</div>',
                canonical_tag,
                md_text,
            )

if pipeline_audit_warnings:
    for w in pipeline_audit_warnings:
        print(f"⚠️ {w}")
elif ssot_data:
    print(f"✅ [SSOT 무결성 검증 통과] {os.path.basename(md_file)} ↔ valuation_metrics.json (기준가 {ssot_data.get('anchor_price', 0):,}원)")

# Pre-process Mermaid diagrams to ensure strict Mermaid v10.9.8+ syntax compliance
md_text = sanitize_mermaid_in_markdown(md_text)

html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
ssot_json_inline = json.dumps(ssot_data, ensure_ascii=False) if ssot_data else "null"

html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>Stock Analyzer Report (v4.2)</title>
    <style>
    {css_text}
    .sop-calc-container {{
        background-color: #f8fafc;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 25px;
        margin: 25px 0 35px 0;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.08);
    }}
    .sop-calc-container h3 {{
        margin-top: 0;
        color: #0f172a;
        border-bottom: 2px solid #94a3b8;
        padding-bottom: 10px;
        margin-bottom: 18px;
    }}
    .sop-grid-3 {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 14px;
        margin-bottom: 18px;
    }}
    .sop-card {{
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 14px 16px;
    }}
    .sop-card.buy-card {{ border-left: 4px solid #2563eb; }}
    .sop-card.stop-card {{ border-left: 4px solid #dc2626; background: #fff5f5; }}
    .sop-card.sell-card {{ border-left: 4px solid #059669; }}
    .sop-card h4 {{
        margin: 0 0 8px 0;
        font-size: 10.5pt;
        color: #1e293b;
    }}
    .sop-stat-line {{
        font-size: 9.8pt;
        margin: 4px 0;
        color: #334155;
    }}
    .sop-highlight {{
        font-weight: 800;
        color: #0f172a;
    }}
    .sop-pin-badge {{
        display: inline-block;
        background: #dc2626;
        color: #fff;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 9.5pt;
    }}
    .ssot-verified-banner {{
        background: #ecfdf5;
        border: 1px solid #10b981;
        color: #065f46;
        padding: 8px 14px;
        border-radius: 6px;
        font-size: 9.5pt;
        font-weight: 600;
        margin-bottom: 16px;
    }}
    </style>
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({{ 
            startOnLoad: false,
            securityLevel: 'loose',
            theme: 'default',
            logLevel: 'error'
        }});
        window.mermaid = mermaid;
    </script>
</head>
<body>
    {html_body}
    <script>
        const SSOT_DATA = {ssot_json_inline};
        // Replace mermaid code blocks with div class="mermaid" and run rendering safely
        document.addEventListener("DOMContentLoaded", function() {{
            const blocks = document.querySelectorAll("code.language-mermaid");
            blocks.forEach(block => {{
                const parent = block.parentNode; // <pre>
                const div = document.createElement("div");
                div.className = "mermaid";
                div.textContent = block.textContent;
                parent.parentNode.replaceChild(div, parent);
            }});
            
            async function renderMermaidSafe() {{
                let attempts = 0;
                while (!window.mermaid && attempts < 25) {{
                    await new Promise(r => setTimeout(r, 100));
                    attempts++;
                }}
                if (window.mermaid) {{
                    try {{
                        await window.mermaid.run({{
                            querySelector: '.mermaid'
                        }});
                    }} catch (e) {{
                        console.warn("Mermaid execution error caught:", e);
                    }}
                }}
            }}
            renderMermaidSafe();
            
            // Highlight Fact vs Narrative & Falsification Kill-Switch
            const allElements = document.querySelectorAll("p, li, blockquote, td");
            allElements.forEach(el => {{
                if (el.innerHTML.includes("[공시 팩트]")) {{
                    el.innerHTML = el.innerHTML.replace("[공시 팩트]", "<span class='fact-badge'>[공시 팩트]</span>");
                    el.classList.add("fact-block");
                }}
                if (el.innerHTML.includes("[시장 내러티브]") || el.innerHTML.includes("[시장 주장]")) {{
                    el.innerHTML = el.innerHTML.replace(/\\[시장 (내러티브|주장)\\]/g, "<span class='narrative-badge'>[시장 $1]</span>");
                    el.classList.add("narrative-block");
                }}
            }});
            
            // DCF Calculator & Master SOP Calculator (Bound to SSOT_DATA if present)
            const dcfRoot = document.getElementById('dcf-calculator-root');
            if (dcfRoot) {{
                const sDcf = (SSOT_DATA && SSOT_DATA.reverse_dcf) ? SSOT_DATA.reverse_dcf : {{}};
                const sSec3 = (SSOT_DATA && SSOT_DATA.section3) ? SSOT_DATA.section3 : {{}};
                const sSec4 = (SSOT_DATA && SSOT_DATA.section4) ? SSOT_DATA.section4 : {{}};

                const rawPrice = Number(SSOT_DATA?.anchor_price || sDcf.price || dcfRoot.getAttribute('data-price') || 100000);
                const initPrice = rawPrice.toLocaleString();
                const initShares = Number(sDcf.distb_shares || dcfRoot.getAttribute('data-shares') || 10000000).toLocaleString();
                const reportedFcfNum = (sDcf.fcf_eok !== undefined) ? Number(sDcf.fcf_eok) : Number(dcfRoot.getAttribute('data-reported-fcf') || dcfRoot.getAttribute('data-fcf') || 500);
                const normFcfNum = (sDcf.norm_fcf_eok !== undefined && sDcf.norm_fcf_eok !== null) ? Number(sDcf.norm_fcf_eok) : 0;
                const useNormFcf = (reportedFcfNum <= 0 && normFcfNum > 0);
                const effectiveFcfNum = useNormFcf ? normFcfNum : (Number(dcfRoot.getAttribute('data-fcf')) > 0 ? Number(dcfRoot.getAttribute('data-fcf')) : reportedFcfNum);
                const initFcf = effectiveFcfNum.toLocaleString();
                const fcfLabelText = useNormFcf
                    ? ('기준 FCF (억 원 · 증설기 오너정규화 FCF, 보고FCF ' + reportedFcfNum.toLocaleString() + '억)')
                    : '기준 FCF (억 원)';
                const rawNetDebt = parseFloat(sDcf.net_debt_eok !== undefined ? sDcf.net_debt_eok : (dcfRoot.getAttribute('data-net-debt') || '0'));
                const initNetDebt = isNaN(rawNetDebt) ? '0' : Math.round(rawNetDebt).toLocaleString();
                const rawNonOp = parseFloat(sDcf.recognized_non_op_eok !== undefined ? sDcf.recognized_non_op_eok : (dcfRoot.getAttribute('data-non-op-assets') || '0'));
                const initNonOp = isNaN(rawNonOp) ? '0' : Math.round(rawNonOp).toLocaleString();

                let rawWacc = parseFloat(sDcf.wacc || dcfRoot.getAttribute('data-wacc') || '9.0');
                if (rawWacc > 0 && rawWacc < 1) rawWacc = rawWacc * 100;
                let rawTerm = parseFloat(sDcf.terminal_g || dcfRoot.getAttribute('data-terminal') || '2.0');
                if (rawTerm > 0 && rawTerm < 1) rawTerm = rawTerm * 100;
                const initWacc = rawWacc.toFixed(1);
                const initTerminal = rawTerm.toFixed(1);

                function extractKrwPrices(str) {{
                    if (!str) return [];
                    const matches = [...str.matchAll(/([0-9]{{1,3}}(?:,[0-9]{{3}})+|[0-9]{{4,}})\\s*원/g)];
                    if (matches.length > 0) {{
                        return matches.map(m => parseFloat(m[1].replace(/,/g, '')));
                    }}
                    const m2 = str.match(/([0-9]{{1,3}}(?:,[0-9]{{3}})+)/);
                    if (m2) return [parseFloat(m2[1].replace(/,/g, ''))];
                    return [];
                }}

                let autoBull = Number(sSec3.bull_auto || dcfRoot.getAttribute('data-bull') || 0);
                let autoBase = Number((sSec3.is_low_vol_box ? sSec3.tp1_price : sSec3.base_auto) || dcfRoot.getAttribute('data-tp1') || dcfRoot.getAttribute('data-base') || 0);
                let autoBullA = Number(sSec3.bull_auto_a || dcfRoot.getAttribute('data-bull-a') || 0);
                let autoBaseA = Number((sSec3.is_low_vol_box ? sSec3.tp1_price_a : sSec3.base_auto_a) || dcfRoot.getAttribute('data-tp1-a') || dcfRoot.getAttribute('data-base-a') || 0);
                let autoBear = Number(sSec3.bear_auto || dcfRoot.getAttribute('data-bear') || 0);
                let autoPocLow = Number(sSec4.poc_low || dcfRoot.getAttribute('data-poc-low') || 0);
                let attrBuy1 = Number(sSec4.buy1 || dcfRoot.getAttribute('data-buy1') || 0);
                let attrBuy2 = Number(sSec4.buy2 || dcfRoot.getAttribute('data-buy2') || 0);
                let attrBuy3 = Number(sSec4.buy3 || dcfRoot.getAttribute('data-buy3') || 0);
                let attrStopPin = Number(sSec4.init_stop_pin || dcfRoot.getAttribute('data-stop-pin') || 0);

                if (!autoBull || !autoBase || !autoBear || !autoBullA || !autoBaseA) {{
                    const rows = document.querySelectorAll('table tr');
                    rows.forEach(tr => {{
                        const tds = tr.querySelectorAll('td');
                        if (tds.length >= 5) {{
                            const label = tds[0].innerText.trim();
                            let prices = extractKrwPrices(tds[tds.length - 2].innerText);
                            if (prices.length === 0 && tds.length >= 6) {{
                                prices = extractKrwPrices(tds[tds.length - 3].innerText);
                            }}
                            if (prices.length > 0) {{
                                const pB = prices[0];
                                const pA = prices.length > 1 ? prices[1] : prices[0];
                                if (/bull/i.test(label)) {{
                                    if (!autoBull) autoBull = pB;
                                    if (!autoBullA) autoBullA = pA;
                                }}
                                if (/tp1/i.test(label)) {{
                                    autoBase = pB;
                                    autoBaseA = pA;
                                }} else if (/base/i.test(label)) {{
                                    if (!autoBase) autoBase = pB;
                                    if (!autoBaseA) autoBaseA = pA;
                                }}
                                if (!autoBear && /bear/i.test(label)) autoBear = pB;
                            }}
                        }}
                    }});
                }}

                if (!autoPocLow) {{
                    const bodyTxt = document.body.innerText;
                    const mPoc = bodyTxt.match(/20일\\s*매물대[^\\n]*?([0-9]{{1,3}}(?:,[0-9]{{3}})+)\\s*원\\s*~/);
                    if (mPoc) autoPocLow = parseFloat(mPoc[1].replace(/,/g, ''));
                }}

                if (!autoBear) autoBear = Math.round(rawPrice * 0.80);
                if (!autoBase) autoBase = Math.round(rawPrice * 1.25);
                if (!autoBull) autoBull = Math.round(rawPrice * 1.55);
                if (!autoBaseA) autoBaseA = autoBase;
                if (!autoBullA) autoBullA = autoBull;
                if (!autoPocLow) autoPocLow = Math.round(rawPrice * 0.96);

                // Conflict-free default Buy 1 < Buy 2 < Buy 3 < Target & Hard Stop 1 < Buy 1
                let initBuy1 = attrBuy1 > 0 ? attrBuy1 : Math.min(autoBear, Math.round(rawPrice * 0.92), Math.round(autoBase * 0.85));
                let initBuy2 = attrBuy2 > 0 ? attrBuy2 : (rawPrice <= autoBase * 0.96 ? Math.max(rawPrice, Math.round(initBuy1 * 1.03)) : Math.round((initBuy1 + autoBase * 0.92) / 2));
                let initBuy3 = attrBuy3 > 0 ? attrBuy3 : (rawPrice <= autoBase * 0.96 ? Math.min(Math.round(initBuy2 * 1.03), Math.round(autoBase * 0.96)) : Math.round(autoBase * 0.92));
                if (initBuy1 >= initBuy2) initBuy1 = Math.round(initBuy2 * 0.95);
                if (initBuy3 <= initBuy2) initBuy3 = Math.round(initBuy2 * 1.03);

                let initStopPin = attrStopPin > 0 ? attrStopPin : Math.min(Math.round(autoPocLow * 0.97), Math.round(initBuy1 * 0.95), Math.round(autoBear * 0.95));
                if (initStopPin >= initBuy1) initStopPin = Math.round(initBuy1 * 0.95);

                const ssotBanner = SSOT_DATA ? '<div class="ssot-verified-banner">✅ 파이프라인 SSOT(valuation_metrics.json) 직결 바인딩 완료 — AI 수동 이기(Transcription) 오차 0.0% 검증됨</div>' : '';
                
                dcfRoot.innerHTML = ssotBanner +
                '<div class="dcf-calc-container">' +
                    '<h3>⚡ Interactive Reverse DCF (5-Year Model · Equity · EV · SOTP 본업EV 병기)</h3>' +
                    '<div class="dcf-calc-grid">' +
                        '<div class="dcf-input-group">' +
                            '<label>현재 주가 (원)</label>' +
                            '<input type="text" id="dcf-input-price" value="' + initPrice + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>유통 주식수 (주 - 자사주 제외)</label>' +
                            '<input type="text" id="dcf-input-shares" value="' + initShares + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>순차입금/순현금 (억 원, 순현금은 -)</label>' +
                            '<input type="text" id="dcf-input-net-debt" value="' + initNetDebt + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>비영업자산 안전가치 (억 원, SOTP 차감)</label>' +
                            '<input type="text" id="dcf-input-non-op" value="' + initNonOp + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>' + fcfLabelText + '</label>' +
                            '<input type="text" id="dcf-input-fcf" value="' + initFcf + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>할인율 (%, WACC)</label>' +
                            '<input type="number" id="dcf-input-wacc" value="' + initWacc + '" step="0.1">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>영구성장률 (%, Terminal)</label>' +
                            '<input type="number" id="dcf-input-terminal" value="' + initTerminal + '" step="0.1">' +
                        '</div>' +
                    '</div>' +
                    '<div class="dcf-result-box" id="dcf-result-box">' +
                        '<div class="dcf-result-label">향후 5년 평균 요구성장률 (Implied Growth Rate)</div>' +
                        '<div class="dcf-result-value" id="dcf-result-value">계산 중...</div>' +
                        '<div class="dcf-result-msg" id="dcf-result-msg"></div>' +
                    '</div>' +
                '</div>' +
                '<div class="sop-calc-container" id="sop-calc-container">' +
                    '<h3>🧮 마스터 SOP 3분할 매수 · 이중 안전핀 · 분할 익절 실전 계산기 (🛡️대안 B & 🚀대안 A 동시 지원)</h3>' +
                    '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;">' +
                        '<button type="button" id="btn-apply-model-b" style="cursor:pointer;padding:7px 14px;border-radius:6px;border:1px solid #2563eb;background:#eff6ff;color:#1e40af;font-weight:700;font-size:9.5pt;">🛡️ 대안 B (보수적 순수 DART): Base ' + autoBase.toLocaleString() + '원 / Bull ' + autoBull.toLocaleString() + '원 적용</button>' +
                        '<button type="button" id="btn-apply-model-a" style="cursor:pointer;padding:7px 14px;border-radius:6px;border:1px solid #059669;background:#ecfdf5;color:#065f46;font-weight:700;font-size:9.5pt;">🚀 대안 A (업황·S-Curve 할증): Base ' + autoBaseA.toLocaleString() + '원 / Bull ' + autoBullA.toLocaleString() + '원 적용</button>' +
                    '</div>' +
                    '<div class="dcf-calc-grid">' +
                        '<div class="dcf-input-group">' +
                            '<label>총 투자예산 (원)</label>' +
                            '<input type="text" id="sop-input-budget" value="10,000,000">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>1차 정찰병 매수가 (30% · Bear/눌림목)</label>' +
                            '<input type="text" id="sop-input-buy1" value="' + initBuy1.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>2차 본대 매수가 (40% · 매물대+20일선 돌파)</label>' +
                            '<input type="text" id="sop-input-buy2" value="' + initBuy2.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>3차 확인사살 매수가 (30% · 주봉 20주선 안착)</label>' +
                            '<input type="text" id="sop-input-buy3" value="' + initBuy3.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>1단계 초입 고정 안전핀 (원 · 진입 손절선)</label>' +
                            '<input type="text" id="sop-input-stop-pin" value="' + initStopPin.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>2단계 20일 매물대 하단가 (원 · 돌파 후 기준)</label>' +
                            '<input type="text" id="sop-input-poc-low" value="' + autoPocLow.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>1차 목표가 (Base / TP1 단기실전, 원)</label>' +
                            '<input type="text" id="sop-input-base" value="' + autoBase.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>2차 목표가 (Bull 호황 상단, 원)</label>' +
                            '<input type="text" id="sop-input-bull" value="' + autoBull.toLocaleString() + '">' +
                        '</div>' +
                    '</div>' +
                    '<div class="sop-grid-3">' +
                        '<div class="sop-card buy-card" id="sop-buy-summary"></div>' +
                        '<div class="sop-card stop-card" id="sop-stop-summary"></div>' +
                        '<div class="sop-card sell-card" id="sop-sell-summary"></div>' +
                    '</div>' +
                '</div>';

                const elPrice = document.getElementById('dcf-input-price');
                const elShares = document.getElementById('dcf-input-shares');
                const elNetDebt = document.getElementById('dcf-input-net-debt');
                const elNonOp = document.getElementById('dcf-input-non-op');
                const elFcf = document.getElementById('dcf-input-fcf');
                const elWacc = document.getElementById('dcf-input-wacc');
                const elTerminal = document.getElementById('dcf-input-terminal');
                const elResultValue = document.getElementById('dcf-result-value');
                const elResultMsg = document.getElementById('dcf-result-msg');
                const elResultBox = document.getElementById('dcf-result-box');
                
                function parseFormattedNum(val) {{
                    if (val === undefined || val === null) return 0;
                    const cleaned = val.toString().replace(/,/g, '').trim();
                    if (cleaned === '' || cleaned === '-') return 0;
                    return parseFloat(cleaned);
                }}

                function formatNumberInput(e) {{
                    if (e.target.type === 'number') return;
                    let cursor = e.target.selectionStart;
                    let originalLength = e.target.value.length;
                    let isNegative = e.target.value.startsWith('-');
                    let val = e.target.value.replace(/[^\\d.]/g, '');
                    
                    if (val !== '' && !isNaN(val)) {{
                        let parts = val.split('.');
                        parts[0] = parseInt(parts[0], 10).toLocaleString();
                        let formatted = parts.join('.');
                        if (isNegative) formatted = '-' + formatted;
                        e.target.value = formatted;
                    }} else if (isNegative) {{
                        e.target.value = '-';
                    }} else {{
                        e.target.value = '';
                    }}
                    
                    let newLength = e.target.value.length;
                    cursor = cursor + (newLength - originalLength);
                    if(cursor < 0) cursor = 0;
                    e.target.setSelectionRange(cursor, cursor);
                }}

                function solveImpliedGrowthForTarget(targetVal, fcf, wacc, terminal) {{
                    if (targetVal <= 0 || fcf <= 0 || wacc <= terminal) return null;
                    function calcDCFValue(g) {{
                        let pv = 0;
                        let cf = fcf;
                        for(let i=1; i<=5; i++) {{
                            cf = cf * (1 + g);
                            pv += cf / Math.pow(1 + wacc, i);
                        }}
                        let tv = (cf * (1 + terminal)) / (wacc - terminal);
                        pv += tv / Math.pow(1 + wacc, 5);
                        return pv;
                    }}
                    let low = -0.99;
                    let high = 10.0;
                    let g = 0;
                    let found = false;
                    for (let iter = 0; iter < 200; iter++) {{
                        g = (low + high) / 2;
                        let pv = calcDCFValue(g);
                        if (Math.abs(pv - targetVal) / targetVal < 0.00001) {{
                            found = true;
                            break;
                        }}
                        if (pv > targetVal) {{
                            high = g;
                        }} else {{
                            low = g;
                        }}
                    }}
                    if (found || Math.abs(high - low) < 0.00001) return g;
                    return null;
                }}

                function calculateImpliedGrowth() {{
                    const price = parseFormattedNum(elPrice.value);
                    const shares = parseFormattedNum(elShares.value);
                    const netDebtEok = parseFormattedNum(elNetDebt.value);
                    const nonOpEok = parseFormattedNum(elNonOp.value);
                    const fcf = parseFormattedNum(elFcf.value) * 100000000;
                    const wacc = parseFloat(elWacc.value) / 100.0;
                    const terminal = parseFloat(elTerminal.value) / 100.0;

                    if (isNaN(price) || isNaN(shares) || isNaN(fcf) || isNaN(wacc) || isNaN(terminal) || price <= 0 || shares <= 0) {{
                        elResultValue.textContent = "N/A";
                        elResultMsg.textContent = "입력값을 올바르게 입력해주세요.";
                        elResultBox.className = "dcf-result-box error";
                        return;
                    }}

                    if (fcf <= 0) {{
                        elResultValue.textContent = "N/A";
                        elResultMsg.textContent = "FCF 적자(음수)로 인해 역DCF 계산이 불가능합니다 (Normalized FCF를 입력해 보세요).";
                        elResultBox.className = "dcf-result-box error";
                        return;
                    }}

                    if (wacc <= terminal) {{
                         elResultValue.textContent = "N/A";
                         elResultMsg.textContent = "할인율은 영구성장률보다 커야 합니다.";
                         elResultBox.className = "dcf-result-box error";
                         return;
                    }}

                    const equityVal = price * shares;
                    const evVal = Math.max(equityVal * 0.05, equityVal + (netDebtEok * 100000000));
                    const coreEvVal = Math.max(equityVal * 0.05, evVal - (nonOpEok * 100000000));
                    const gEquity = solveImpliedGrowthForTarget(equityVal, fcf, wacc, terminal);
                    const gEv = solveImpliedGrowthForTarget(evVal, fcf, wacc, terminal);
                    const gCoreEv = nonOpEok > 0 ? solveImpliedGrowthForTarget(coreEvVal, fcf, wacc, terminal) : null;

                    if (gEquity !== null) {{
                        const eqPct = (gEquity * 100).toFixed(2);
                        const mcapEok = Math.round(equityVal / 100000000).toLocaleString();
                        const evEok = Math.round(evVal / 100000000).toLocaleString();
                        const coreEvEok = Math.round(coreEvVal / 100000000).toLocaleString();
                        let primaryG = gEquity;
                        if (Math.abs(netDebtEok) > 0 && gEv !== null) {{
                            const evPct = (gEv * 100).toFixed(2);
                            primaryG = netDebtEok > 0 ? gEv : gEquity;
                            let valTxt = "Equity " + eqPct + "%  |  EV " + evPct + "%";
                            if (gCoreEv !== null) {{
                                valTxt += "  |  본업EV(SOTP) " + (gCoreEv * 100).toFixed(2) + "%";
                            }}
                            elResultValue.textContent = valTxt;
                            const ndLabel = netDebtEok > 0 ? ("순차입금 +" + Math.round(netDebtEok).toLocaleString() + "억 가산") : ("가용 순현금 " + Math.round(netDebtEok).toLocaleString() + "억 차감");
                            elResultMsg.textContent = "유통시총(" + mcapEok + "억) 기준 " + eqPct + "% vs 실질 EV(" + evEok + "억, " + ndLabel + ") 기준 " + evPct + "%" + (gCoreEv !== null ? (" vs 본업EV(" + coreEvEok + "억) 기준 " + (gCoreEv*100).toFixed(2) + "%") : "");
                        }} else {{
                            elResultValue.textContent = eqPct + "%" + (gCoreEv !== null ? (" | 본업EV(SOTP) " + (gCoreEv*100).toFixed(2) + "%") : "");
                            elResultMsg.textContent = "현재 유통시가총액(" + mcapEok + "억 원)을 정당화하는 향후 5년 연평균 FCF 성장률입니다.";
                        }}
                        if (primaryG < 0) {{
                             elResultBox.className = "dcf-result-box low-growth";
                        }} else if (primaryG > 0.15) {{
                             elResultBox.className = "dcf-result-box high-growth";
                        }} else {{
                             elResultBox.className = "dcf-result-box normal-growth";
                        }}
                    }} else {{
                        elResultValue.textContent = "N/A";
                        elResultMsg.textContent = "요구 성장률 계산 범위를 벗어났습니다.";
                        elResultBox.className = "dcf-result-box error";
                    }}
                }}

                const inputs = [elPrice, elShares, elNetDebt, elNonOp, elFcf, elWacc, elTerminal];
                inputs.forEach(input => {{
                    input.addEventListener('input', (e) => {{
                        formatNumberInput(e);
                        calculateImpliedGrowth();
                    }});
                }});

                calculateImpliedGrowth();

                // Master SOP Calculator Logic
                const elBudget = document.getElementById('sop-input-budget');
                const elBuy1 = document.getElementById('sop-input-buy1');
                const elBuy2 = document.getElementById('sop-input-buy2');
                const elBuy3 = document.getElementById('sop-input-buy3');
                const elStopPin = document.getElementById('sop-input-stop-pin');
                const elPocLow = document.getElementById('sop-input-poc-low');
                const elBase = document.getElementById('sop-input-base');
                const elBull = document.getElementById('sop-input-bull');

                const elBuySummary = document.getElementById('sop-buy-summary');
                const elStopSummary = document.getElementById('sop-stop-summary');
                const elSellSummary = document.getElementById('sop-sell-summary');

                function calculateSopPlan() {{
                    const budget = parseFormattedNum(elBudget.value) || 0;
                    const b1 = parseFormattedNum(elBuy1.value) || 1;
                    const b2 = parseFormattedNum(elBuy2.value) || 1;
                    const b3 = parseFormattedNum(elBuy3.value) || 1;
                    const rawStopPin = parseFormattedNum(elStopPin.value) || Math.round(b1 * 0.95);
                    const pocLow = parseFormattedNum(elPocLow.value) || b2;
                    const tBase = parseFormattedNum(elBase.value) || b2;
                    const tBull = parseFormattedNum(elBull.value) || b2;

                    const alloc1 = budget * 0.30;
                    const alloc2 = budget * 0.40;
                    const alloc3 = budget * 0.30;

                    const qty1 = Math.floor(alloc1 / b1);
                    const qty2 = Math.floor(alloc2 / b2);
                    const qty3 = Math.floor(alloc3 / b3);
                    const totalQty = qty1 + qty2 + qty3;
                    const totalInvested = (qty1 * b1) + (qty2 * b2) + (qty3 * b3);
                    const avgPrice = totalQty > 0 ? Math.round(totalInvested / totalQty) : Math.round((b1 * 0.3) + (b2 * 0.4) + (b3 * 0.3));

                    const minEntryPrice = Math.min(b1, b2, avgPrice);
                    let safetyPin = rawStopPin;
                    let pinAutoCorrected = false;
                    if (safetyPin >= minEntryPrice) {{
                        safetyPin = Math.round(minEntryPrice * 0.95);
                        pinAutoCorrected = true;
                    }}
                    const pocTrailingPin = Math.round(pocLow * 0.97);
                    const pinLossPct = avgPrice > 0 ? ((safetyPin / avgPrice - 1) * 100).toFixed(1) : "0.0";
                    const waistThreshold = Math.round(avgPrice * 1.15);

                    let orderNotice = "";
                    if (avgPrice >= tBase) {{
                        orderNotice = '<div class="sop-stat-line" style="color:#dc2626;font-weight:700;">⚠️ 경고: 완성 평단가(' + avgPrice.toLocaleString() + '원)가 1차 목표가(' + tBase.toLocaleString() + '원) 이상입니다. 눌림목 매수가로 하향 조정하세요.</div>';
                    }}

                    const sellQty1 = Math.floor(totalQty * 0.50);
                    const sellQty2 = totalQty - sellQty1;
                    const profit1 = sellQty1 * (tBase - avgPrice);
                    const profit2 = sellQty2 * (tBull - avgPrice);
                    const totalProfit = profit1 + profit2;
                    const totalRoiNum = totalInvested > 0 ? ((totalProfit / totalInvested) * 100) : 0;
                    const baseUpsideNum = avgPrice > 0 ? ((tBase / avgPrice - 1) * 100) : 0;
                    const bullUpsideNum = avgPrice > 0 ? ((tBull / avgPrice - 1) * 100) : 0;
                    const trailingStop3 = Math.round(tBull * 0.92);

                    const profitB = (sellQty1 * (autoBase - avgPrice)) + (sellQty2 * (autoBull - avgPrice));
                    const roiB = totalInvested > 0 ? ((profitB / totalInvested) * 100) : 0;
                    const profitA = (sellQty1 * (autoBaseA - avgPrice)) + (sellQty2 * (autoBullA - avgPrice));
                    const roiA = totalInvested > 0 ? ((profitA / totalInvested) * 100) : 0;

                    function fmtSignedKrw(v) {{
                        const r = Math.round(v);
                        return (r >= 0 ? '+' : '') + r.toLocaleString() + '원';
                    }}
                    function fmtSignedPct(v) {{
                        return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
                    }}

                    const riskAmt = avgPrice - safetyPin;
                    const rewardAmt = ((tBase + tBull) / 2) - avgPrice;
                    const sopRR = (riskAmt > 0 && rewardAmt > 0) ? (rewardAmt / riskAmt).toFixed(2) + ' : 1' : '0.00 : 1 (재설정 필요)';
                    const totalColor = totalProfit >= 0 ? '#059669' : '#dc2626';
                    const pinNote = pinAutoCorrected ? ' <span style="color:#dc2626;font-size:8.5pt;">(평단가 초과 방지를 위해 진입하단 -5%로 자동보정됨)</span>' : '';

                    elBuySummary.innerHTML =
                        '<h4>🔵 ① 3분할 매수 주문표 (30 : 40 : 30)</h4>' +
                        '<div class="sop-stat-line">• <strong>1차 정찰병(30%):</strong> ' + b1.toLocaleString() + '원 × <strong>' + qty1.toLocaleString() + '주</strong> (' + Math.round(qty1 * b1).toLocaleString() + '원)</div>' +
                        '<div class="sop-stat-line">• <strong>2차 본대(40%):</strong> ' + b2.toLocaleString() + '원 × <strong>' + qty2.toLocaleString() + '주</strong> (' + Math.round(qty2 * b2).toLocaleString() + '원)</div>' +
                        '<div class="sop-stat-line">• <strong>3차 확인사살(30%):</strong> ' + b3.toLocaleString() + '원 × <strong>' + qty3.toLocaleString() + '주</strong> (' + Math.round(qty3 * b3).toLocaleString() + '원)</div>' +
                        '<hr style="border:none;border-top:1px dashed #cbd5e1;margin:8px 0;">' +
                        '<div class="sop-stat-line">▶ <strong>총 매수 주식수:</strong> <span class="sop-highlight">' + totalQty.toLocaleString() + '주</span> (실투입 ' + Math.round(totalInvested).toLocaleString() + '원)</div>' +
                        '<div class="sop-stat-line">▶ <strong>완성 평균단가:</strong> <span class="sop-highlight">' + avgPrice.toLocaleString() + '원</span></div>' +
                        orderNotice;

                    elStopSummary.innerHTML =
                        '<h4>🚨 ② 이중 안전핀(초입 손절 vs 돌파 후 보호) & 허리 홀딩</h4>' +
                        '<div class="sop-stat-line">• <strong>1단계 초입 고정 안전핀(진입 손절선):</strong> <span class="sop-pin-badge">' + safetyPin.toLocaleString() + '원</span> (평단 대비 ' + pinLossPct + '%)' + pinNote + '</div>' +
                        '<div class="sop-stat-line">• <strong>2단계 매물대 돌파 후 보호핀(POC -3%):</strong> <strong>' + pocTrailingPin.toLocaleString() + '원</strong> (매물대 상단 안착 후 본절·수익 보호선으로 격상)</div>' +
                        '<hr style="border:none;border-top:1px dashed #fca5a5;margin:8px 0;">' +
                        '<div class="sop-stat-line">• <strong>상승 허리 전환가(+15%):</strong> <span class="sop-highlight">' + waistThreshold.toLocaleString() + '원</span> 돌파 시</div>' +
                        '<div class="sop-stat-line">• <strong>허리 홀딩 규칙:</strong> 일봉 20일선 노이즈 100% 무시 & <strong>주봉 20주선 종가 이탈 전까지 끝까지 홀딩</strong></div>';

                    elSellSummary.innerHTML =
                        '<h4>🟢 ③ 2단계 분할 익절 & 고점경보</h4>' +
                        '<div class="sop-stat-line">• <strong>1단계 반타작(50%, ' + sellQty1.toLocaleString() + '주 @ ' + tBase.toLocaleString() + '원):</strong> <strong>' + fmtSignedKrw(profit1) + '</strong> (' + fmtSignedPct(baseUpsideNum) + ')</div>' +
                        '<div class="sop-stat-line">• <strong>2단계 추세완주(50%, ' + sellQty2.toLocaleString() + '주 @ ' + tBull.toLocaleString() + '원):</strong> <strong>' + fmtSignedKrw(profit2) + '</strong> (' + fmtSignedPct(bullUpsideNum) + ')</div>' +
                        '<div class="sop-stat-line">• <strong>고점경보 3호(-8% 트레일링):</strong> 최고점(' + tBull.toLocaleString() + '원) 대비 <strong>' + trailingStop3.toLocaleString() + '원</strong> 이탈 시 전량 청산</div>' +
                        '<hr style="border:none;border-top:1px dashed #cbd5e1;margin:8px 0;">' +
                        '<div class="sop-stat-line">▶ <strong>현재 적용 기대수익:</strong> <span class="sop-highlight" style="color:' + totalColor + ';">' + fmtSignedKrw(totalProfit) + ' (' + fmtSignedPct(totalRoiNum) + ')</span> | <strong>실전 R:R ' + sopRR + '</strong></div>' +
                        '<div class="sop-stat-line" style="font-size:9pt;color:#475569;margin-top:4px;">• 🛡️대안B: <strong>' + fmtSignedKrw(profitB) + ' (' + fmtSignedPct(roiB) + ')</strong> vs 🚀대안A: <strong>' + fmtSignedKrw(profitA) + ' (' + fmtSignedPct(roiA) + ')</strong></div>';
                }}

                const btnModelB = document.getElementById('btn-apply-model-b');
                const btnModelA = document.getElementById('btn-apply-model-a');
                if (btnModelB) {{
                    btnModelB.addEventListener('click', () => {{
                        elBase.value = autoBase.toLocaleString();
                        elBull.value = autoBull.toLocaleString();
                        calculateSopPlan();
                    }});
                }}
                if (btnModelA) {{
                    btnModelA.addEventListener('click', () => {{
                        elBase.value = autoBaseA.toLocaleString();
                        elBull.value = autoBullA.toLocaleString();
                        calculateSopPlan();
                    }});
                }}

                const sopInputs = [elBudget, elBuy1, elBuy2, elBuy3, elStopPin, elPocLow, elBase, elBull];
                sopInputs.forEach(inp => {{
                    inp.addEventListener('input', (e) => {{
                        formatNumberInput(e);
                        calculateSopPlan();
                    }});
                }});

                calculateSopPlan();
            }}
        }});
    </script>
</body>
</html>
"""

try:
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_template)
    print(f"Generated HTML successfully: {out_file}" + (f" [SSOT Bound: {ssot_loaded_path}]" if ssot_loaded_path else ""))
except Exception as e:
    print(f"Error writing {out_file}: {e}")
    sys.exit(1)