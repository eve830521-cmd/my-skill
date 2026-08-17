import markdown
import sys
import os

if len(sys.argv) != 3:
    print("Usage: python generate_html.py <input.md> <output.html>")
    sys.exit(1)

md_file = sys.argv[1]
out_file = sys.argv[2]
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
                    el.innerHTML = el.innerHTML.replace(/\[시장 (내러티브|주장)\]/g, "<span class='narrative-badge'>[시장 $1]</span>");
                    el.classList.add("narrative-block");
                }}
            }});
            
            // DCF Calculator
            const dcfRoot = document.getElementById('dcf-calculator-root');
            if (dcfRoot) {{
                const initPrice = Number(dcfRoot.getAttribute('data-price') || 100000).toLocaleString();
                const initShares = Number(dcfRoot.getAttribute('data-shares') || 10000000).toLocaleString();
                const initFcf = Number(dcfRoot.getAttribute('data-fcf') || 50000000000).toLocaleString();
                
                dcfRoot.innerHTML = '<div class="dcf-calc-container">' +
                    '<h3>⚡ Interactive Reverse DCF (5-Year Model)</h3>' +
                    '<div class="dcf-calc-grid">' +
                        '<div class="dcf-input-group">' +
                            '<label>현재 주가 (원)</label>' +
                            '<input type="text" id="dcf-input-price" value="' + initPrice + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>유통 주식수 (주)</label>' +
                            '<input type="text" id="dcf-input-shares" value="' + initShares + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>기준 FCF (원)</label>' +
                            '<input type="text" id="dcf-input-fcf" value="' + initFcf + '">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>할인율 (%, WACC)</label>' +
                            '<input type="number" id="dcf-input-wacc" value="10.0" step="0.1">' +
                        '</div>' +
                        '<div class="dcf-input-group">' +
                            '<label>영구성장률 (%, Terminal)</label>' +
                            '<input type="number" id="dcf-input-terminal" value="2.0" step="0.1">' +
                        '</div>' +
                    '</div>' +
                    '<div class="dcf-result-box" id="dcf-result-box">' +
                        '<div class="dcf-result-label">향후 5년 평균 요구성장률 (Implied Growth Rate)</div>' +
                        '<div class="dcf-result-value" id="dcf-result-value">계산 중...</div>' +
                        '<div class="dcf-result-msg" id="dcf-result-msg"></div>' +
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
                    const fcf = parseFormattedNum(elFcf.value);
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
                    let high = 5.0; // max 500%
                    let g = 0;
                    let found = false;
                    for (let iter = 0; iter < 100; iter++) {{
                        g = (low + high) / 2;
                        let pv = calcDCFValue(g);
                        if (Math.abs(pv - targetValue) / targetValue < 0.0001) {{
                            found = true;
                            break;
                        }}
                        if (pv > targetValue) {{
                            high = g;
                        }} else {{
                            low = g;
                        }}
                    }}

                    if (found || Math.abs(high - low) < 0.0001) {{
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
