import markdown
import sys
import os

if len(sys.argv) != 3:
    print("Usage: python generate_html.py <input.md> <output.html>")
    sys.exit(1)

md_file = sys.argv[1]
out_file = sys.argv[2]
css_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'report_style.css')
if not os.path.exists(css_file):
    css_file = r'e:\antigravity-work\my-skill\korean-stock-analyzer\report_style.css'

try:
    with open(md_file, 'r', encoding='utf-8') as f:
        md_text = f.read()
except Exception as e:
    print(f"Error reading {md_file}: {e}")
    sys.exit(1)

try:
    with open(css_file, 'r', encoding='utf-8') as f:
        css_text = f.read()
except Exception as e:
    print(f"Error reading CSS {css_file}: {e}")
    css_text = ""

# Convert markdown to HTML
html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])

html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>Stock Analyzer Report</title>
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
    </style>
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({{ startOnLoad: true }});
    </script>
</head>
<body>
    {html_body}
    <script>
        // Replace mermaid code blocks with div class="mermaid"
        document.addEventListener("DOMContentLoaded", function() {{
            const blocks = document.querySelectorAll("code.language-mermaid");
            blocks.forEach(block => {{
                const parent = block.parentNode; // <pre>
                const div = document.createElement("div");
                div.className = "mermaid";
                div.textContent = block.textContent;
                parent.parentNode.replaceChild(div, parent);
            }});
            
            // Highlight Fact vs Narrative
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
            
            // DCF Calculator & Master SOP Calculator
            const dcfRoot = document.getElementById('dcf-calculator-root');
            if (dcfRoot) {{
                const rawPrice = Number(dcfRoot.getAttribute('data-price') || 100000);
                const initPrice = rawPrice.toLocaleString();
                const initShares = Number(dcfRoot.getAttribute('data-shares') || 10000000).toLocaleString();
                const initFcf = Number(dcfRoot.getAttribute('data-fcf') || 500).toLocaleString();
                let rawWacc = parseFloat(dcfRoot.getAttribute('data-wacc') || '9.0');
                if (rawWacc > 0 && rawWacc < 1) rawWacc = rawWacc * 100;
                let rawTerm = parseFloat(dcfRoot.getAttribute('data-terminal') || '2.0');
                if (rawTerm > 0 && rawTerm < 1) rawTerm = rawTerm * 100;
                const initWacc = rawWacc.toFixed(1);
                const initTerminal = rawTerm.toFixed(1);

                // Auto-detect Bull / Base / TP1 / Bear / 20d POC low from attributes or Phase 3 scenario table
                function extractFirstKrwPrice(str) {{
                    if (!str) return null;
                    const m = str.match(/([0-9]{{1,3}}(?:,[0-9]{{3}})+|[0-9]{{4,}})\\s*원/);
                    if (m) return parseFloat(m[1].replace(/,/g, ''));
                    const m2 = str.match(/([0-9]{{1,3}}(?:,[0-9]{{3}})+)/);
                    if (m2) return parseFloat(m2[1].replace(/,/g, ''));
                    return null;
                }}

                let autoBull = Number(dcfRoot.getAttribute('data-bull') || 0);
                let autoBase = Number(dcfRoot.getAttribute('data-tp1') || dcfRoot.getAttribute('data-base') || 0);
                let autoBear = Number(dcfRoot.getAttribute('data-bear') || 0);
                let autoPocLow = Number(dcfRoot.getAttribute('data-poc-low') || 0);

                if (!autoBull || !autoBase || !autoBear) {{
                    const rows = document.querySelectorAll('table tr');
                    rows.forEach(tr => {{
                        const tds = tr.querySelectorAll('td');
                        if (tds.length >= 5) {{
                            const label = tds[0].innerText.trim();
                            // Find the target price column (usually tds[tds.length - 2], or tds[tds.length - 3] if 2 upside columns exist)
                            let priceCandidate = extractFirstKrwPrice(tds[tds.length - 2].innerText);
                            if (!priceCandidate && tds.length >= 6) {{
                                priceCandidate = extractFirstKrwPrice(tds[tds.length - 3].innerText);
                            }}
                            if (priceCandidate) {{
                                if (!autoBull && /bull/i.test(label)) autoBull = priceCandidate;
                                if (/tp1/i.test(label)) autoBase = priceCandidate;
                                else if (!autoBase && /base/i.test(label)) autoBase = priceCandidate;
                                if (!autoBear && /bear/i.test(label)) autoBear = priceCandidate;
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
                if (!autoPocLow) autoPocLow = Math.round(rawPrice * 0.96);

                const initBuy1 = autoBear;
                const initBuy2 = rawPrice;
                const initBuy3 = Math.round(rawPrice * 1.03);
                
                dcfRoot.innerHTML = '<div class="dcf-calc-container">' +
                    '<h3>⚡ Interactive Reverse DCF (5-Year Model)</h3>' +
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
                            '<label>기준 FCF (억 원)</label>' +
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
                    '<h3>🧮 마스터 SOP 3분할 매수 · 고정 안전핀 · 분할 익절 실전 계산기</h3>' +
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
                            '<label>2차 진입일 20일 매물대 하단가 (원)</label>' +
                            '<input type="text" id="sop-input-poc-low" value="' + autoPocLow.toLocaleString() + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>3차 확인사살 매수가 (30% · 주봉 20주선 안착)</label>' +
                            '<input type="text" id="sop-input-buy3" value="' + initBuy3.toLocaleString() + '">' +
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
                const elFcf = document.getElementById('dcf-input-fcf');
                const elWacc = document.getElementById('dcf-input-wacc');
                const elTerminal = document.getElementById('dcf-input-terminal');
                const elResultValue = document.getElementById('dcf-result-value');
                const elResultMsg = document.getElementById('dcf-result-msg');
                const elResultBox = document.getElementById('dcf-result-box');
                
                function parseFormattedNum(val) {{
                    return parseFloat(val.toString().replace(/,/g, ''));
                }}

                function formatNumberInput(e) {{
                    // Skip for number types
                    if (e.target.type === 'number') return;
                    
                    let cursor = e.target.selectionStart;
                    let originalLength = e.target.value.length;
                    
                    // Allow minus sign at the beginning for FCF
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

                function calculateImpliedGrowth() {{
                    const price = parseFormattedNum(elPrice.value);
                    const shares = parseFormattedNum(elShares.value);
                    const fcf = parseFormattedNum(elFcf.value) * 100000000;
                    const wacc = parseFloat(elWacc.value) / 100.0;
                    const terminal = parseFloat(elTerminal.value) / 100.0;

                    if (isNaN(price) || isNaN(shares) || isNaN(fcf) || isNaN(wacc) || isNaN(terminal)) {{
                        elResultValue.textContent = "N/A";
                        elResultMsg.textContent = "입력값을 올바르게 입력해주세요.";
                        elResultBox.className = "dcf-result-box error";
                        return;
                    }}

                    if (fcf <= 0) {{
                        elResultValue.textContent = "N/A";
                        elResultMsg.textContent = "FCF 적자(음수)로 인해 역DCF 계산이 불가능합니다.";
                        elResultBox.className = "dcf-result-box error";
                        return;
                    }}

                    if (wacc <= terminal) {{
                         elResultValue.textContent = "N/A";
                         elResultMsg.textContent = "할인율은 영구성장률보다 커야 합니다.";
                         elResultBox.className = "dcf-result-box error";
                         return;
                    }}

                    const targetValue = price * shares;
                    
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
                    let high = 10.0; // max 1000% — unified with Python
                    let g = 0;
                    let found = false;
                    for (let iter = 0; iter < 200; iter++) {{
                        g = (low + high) / 2;
                        let pv = calcDCFValue(g);
                        if (Math.abs(pv - targetValue) / targetValue < 0.00001) {{
                            found = true;
                            break;
                        }}
                        if (pv > targetValue) {{
                            high = g;
                        }} else {{
                            low = g;
                        }}
                    }}

                    if (found || Math.abs(high - low) < 0.00001) {{
                        const growthPercent = (g * 100).toFixed(2);
                        elResultValue.textContent = growthPercent + "%";
                        elResultMsg.textContent = "현재 시가총액(" + Math.round(targetValue / 100000000).toLocaleString() + "억 원)을 정당화하는 성장률입니다.";
                        if (g < 0) {{
                             elResultBox.className = "dcf-result-box low-growth";
                        }} else if (g > 0.15) {{
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

                const inputs = [elPrice, elShares, elFcf, elWacc, elTerminal];
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
                const elPocLow = document.getElementById('sop-input-poc-low');
                const elBuy3 = document.getElementById('sop-input-buy3');
                const elBase = document.getElementById('sop-input-base');
                const elBull = document.getElementById('sop-input-bull');

                const elBuySummary = document.getElementById('sop-buy-summary');
                const elStopSummary = document.getElementById('sop-stop-summary');
                const elSellSummary = document.getElementById('sop-sell-summary');

                function calculateSopPlan() {{
                    const budget = parseFormattedNum(elBudget.value) || 0;
                    const b1 = parseFormattedNum(elBuy1.value) || 1;
                    const b2 = parseFormattedNum(elBuy2.value) || 1;
                    const pocLow = parseFormattedNum(elPocLow.value) || b2;
                    const b3 = parseFormattedNum(elBuy3.value) || 1;
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

                    // Fixed safety pin = 20-day POC bottom * 0.97 (-3%)
                    const safetyPin = Math.round(pocLow * 0.97);
                    const pinLossPct = avgPrice > 0 ? ((safetyPin / avgPrice - 1) * 100).toFixed(1) : "0.0";
                    const waistThreshold = Math.round(avgPrice * 1.15);

                    // 2-Stage Profit Taking (50% at Base/TP1, 50% at Bull)
                    const sellQty1 = Math.floor(totalQty * 0.50);
                    const sellQty2 = totalQty - sellQty1;
                    const profit1 = sellQty1 * (tBase - avgPrice);
                    const profit2 = sellQty2 * (tBull - avgPrice);
                    const totalProfit = profit1 + profit2;
                    const totalRoiNum = totalInvested > 0 ? ((totalProfit / totalInvested) * 100) : 0;
                    const baseUpsideNum = avgPrice > 0 ? ((tBase / avgPrice - 1) * 100) : 0;
                    const bullUpsideNum = avgPrice > 0 ? ((tBull / avgPrice - 1) * 100) : 0;
                    const trailingStop3 = Math.round(tBull * 0.92);

                    function fmtSignedKrw(v) {{
                        const r = Math.round(v);
                        return (r >= 0 ? '+' : '') + r.toLocaleString() + '원';
                    }}
                    function fmtSignedPct(v) {{
                        return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
                    }}

                    const riskAmt = Math.max(1, avgPrice - safetyPin);
                    const rewardAmt = Math.max(0, ((tBase + tBull) / 2) - avgPrice);
                    const sopRR = (rewardAmt / riskAmt).toFixed(2);
                    const totalColor = totalProfit >= 0 ? '#059669' : '#dc2626';

                    elBuySummary.innerHTML =
                        '<h4>🔵 ① 3분할 매수 주문표 (30 : 40 : 30)</h4>' +
                        '<div class="sop-stat-line">• <strong>1차 정찰병(30%):</strong> ' + b1.toLocaleString() + '원 × <strong>' + qty1.toLocaleString() + '주</strong> (' + Math.round(qty1 * b1).toLocaleString() + '원)</div>' +
                        '<div class="sop-stat-line">• <strong>2차 본대(40%):</strong> ' + b2.toLocaleString() + '원 × <strong>' + qty2.toLocaleString() + '주</strong> (' + Math.round(qty2 * b2).toLocaleString() + '원)</div>' +
                        '<div class="sop-stat-line">• <strong>3차 확인사살(30%):</strong> ' + b3.toLocaleString() + '원 × <strong>' + qty3.toLocaleString() + '주</strong> (' + Math.round(qty3 * b3).toLocaleString() + '원)</div>' +
                        '<hr style="border:none;border-top:1px dashed #cbd5e1;margin:8px 0;">' +
                        '<div class="sop-stat-line">▶ <strong>총 매수 주식수:</strong> <span class="sop-highlight">' + totalQty.toLocaleString() + '주</span> (실투입 ' + Math.round(totalInvested).toLocaleString() + '원)</div>' +
                        '<div class="sop-stat-line">▶ <strong>완성 평균단가:</strong> <span class="sop-highlight">' + avgPrice.toLocaleString() + '원</span></div>';

                    elStopSummary.innerHTML =
                        '<h4>🚨 ② 초입 고정 안전핀 & 허리 홀딩</h4>' +
                        '<div class="sop-stat-line">• <strong>고정 바닥 안전핀(매물대 하단 -3%):</strong> <span class="sop-pin-badge">' + safetyPin.toLocaleString() + '원</span> (평단 대비 ' + pinLossPct + '%)</div>' +
                        '<div class="sop-stat-line">• <strong>초입 손절 규칙:</strong> 수익률 +15%(' + waistThreshold.toLocaleString() + '원) 도달 전 안전핀 종가 이탈 시 기계적 손절</div>' +
                        '<hr style="border:none;border-top:1px dashed #fca5a5;margin:8px 0;">' +
                        '<div class="sop-stat-line">• <strong>상승 허리 전환가(+15%):</strong> <span class="sop-highlight">' + waistThreshold.toLocaleString() + '원</span> 돌파 시</div>' +
                        '<div class="sop-stat-line">• <strong>허리 홀딩 규칙:</strong> 일봉 20일선 노이즈 100% 무시 & <strong>주봉 20주선 종가 이탈 전까지 끝까지 홀딩</strong></div>';

                    elSellSummary.innerHTML =
                        '<h4>🟢 ③ 2단계 분할 익절 & 고점경보</h4>' +
                        '<div class="sop-stat-line">• <strong>1단계 반타작(50%, ' + sellQty1.toLocaleString() + '주 @ ' + tBase.toLocaleString() + '원):</strong> <strong>' + fmtSignedKrw(profit1) + '</strong> (' + fmtSignedPct(baseUpsideNum) + ')</div>' +
                        '<div class="sop-stat-line">• <strong>2단계 추세완주(50%, ' + sellQty2.toLocaleString() + '주 @ ' + tBull.toLocaleString() + '원):</strong> <strong>' + fmtSignedKrw(profit2) + '</strong> (' + fmtSignedPct(bullUpsideNum) + ')</div>' +
                        '<div class="sop-stat-line">• <strong>고점경보 3호(-8% 트레일링):</strong> 최고점(' + tBull.toLocaleString() + '원) 대비 <strong>' + trailingStop3.toLocaleString() + '원</strong> 이탈 시 전량 청산</div>' +
                        '<hr style="border:none;border-top:1px dashed #cbd5e1;margin:8px 0;">' +
                        '<div class="sop-stat-line">▶ <strong>총 기대수익:</strong> <span class="sop-highlight" style="color:' + totalColor + ';">' + fmtSignedKrw(totalProfit) + ' (' + fmtSignedPct(totalRoiNum) + ')</span> | <strong>실전 R:R ' + sopRR + ' : 1</strong></div>';
                }}

                const sopInputs = [elBudget, elBuy1, elBuy2, elPocLow, elBuy3, elBase, elBull];
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
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(html_template)
    print(f"Generated HTML successfully: {out_file}")
except Exception as e:
    print(f"Error writing {out_file}: {e}")
    sys.exit(1)
