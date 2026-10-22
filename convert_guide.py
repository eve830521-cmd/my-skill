# -*- coding: utf-8 -*-
import markdown
import re
import os
import sys

md_path = r'E:\antigravity-work\my-skill\이용방법.md'
html_path = r'E:\antigravity-work\my-skill\이용방법.html'

with open(md_path, 'r', encoding='utf-8') as f:
    raw_md = f.read()

# Convert markdown to html with extra extensions
html_body = markdown.markdown(raw_md, extensions=['extra', 'tables', 'fenced_code', 'toc', 'nl2br'])

# Custom replacement for callout blocks (> **...**)
def format_callouts(text):
    text = re.sub(
        r'<blockquote>\s*<p>\s*<strong>💡(.*?)</strong>(.*?)</p>\s*</blockquote>',
        r'<div class="callout callout-tip"><div class="callout-header"><span class="callout-icon">💡</span><strong>\1</strong></div><div class="callout-body"><p>\2</p></div></div>',
        text,
        flags=re.DOTALL
    )
    text = re.sub(
        r'<blockquote>\s*<p>(.*?)</p>\s*</blockquote>',
        r'<div class="callout"><div class="callout-body"><p>\1</p></div></div>',
        text,
        flags=re.DOTALL
    )
    return text

html_body = format_callouts(html_body)

full_html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Antigravity 시나리오별 실전 이용방법 & 액션플랜 (v4.2.2)</title>
    <link rel="preconnect" href="https://cdn.jsdelivr.net">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css">
    <style>
        :root {{
            --bg-color: #0b0f17;
            --surface-color: #121824;
            --surface-hover: #1a2233;
            --border-color: #232d40;
            --text-primary: #e6edf3;
            --text-secondary: #94a3b8;
            --accent-blue: #3b82f6;
            --accent-cyan: #06b6d4;
            --accent-green: #10b981;
            --accent-purple: #8b5cf6;
            --accent-amber: #f59e0b;
            --code-bg: #070a10;
            --table-header-bg: #182234;
            --table-row-even: #121824;
            --table-row-odd: #0e131d;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            line-height: 1.75;
            font-size: 15px;
            padding: 40px 20px;
            -webkit-font-smoothing: antialiased;
        }}

        .container {{
            max-width: 1040px;
            margin: 0 auto;
            background: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 48px;
            box-shadow: 0 16px 48px rgba(0, 0, 0, 0.45);
        }}

        /* Header / Hero */
        .doc-header {{
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 24px;
            margin-bottom: 32px;
        }}

        .badge-row {{
            display: flex;
            gap: 10px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.3px;
        }}

        .badge-primary {{
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.35);
        }}

        .badge-version {{
            background: rgba(139, 92, 246, 0.15);
            color: #a78bfa;
            border: 1px solid rgba(139, 92, 246, 0.35);
        }}

        .badge-success {{
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.35);
        }}

        h1 {{
            font-size: 28px;
            font-weight: 700;
            color: #ffffff;
            margin-bottom: 14px;
            line-height: 1.35;
        }}

        /* Headings */
        h2 {{
            font-size: 21px;
            font-weight: 700;
            color: #ffffff;
            margin-top: 44px;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        h3 {{
            font-size: 17px;
            font-weight: 600;
            color: #60a5fa;
            margin-top: 32px;
            margin-bottom: 14px;
        }}

        h4 {{
            font-size: 15px;
            font-weight: 600;
            color: #ffffff;
            margin-top: 22px;
            margin-bottom: 10px;
        }}

        p {{
            margin-bottom: 16px;
        }}

        /* Lists */
        ul, ol {{
            margin-bottom: 18px;
            padding-left: 24px;
        }}

        li {{
            margin-bottom: 8px;
        }}

        /* Callouts */
        .callout {{
            background: rgba(59, 130, 246, 0.08);
            border-left: 4px solid var(--accent-blue);
            border-radius: 10px;
            padding: 18px 22px;
            margin: 24px 0;
            border: 1px solid rgba(59, 130, 246, 0.2);
            border-left-width: 4px;
        }}

        .callout.callout-tip {{
            background: rgba(6, 182, 212, 0.08);
            border-left-color: var(--accent-cyan);
            border-color: rgba(6, 182, 212, 0.2);
            border-left-width: 4px;
        }}

        .callout-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            color: #22d3ee;
            font-size: 15px;
            margin-bottom: 8px;
        }}

        .callout-body p {{
            margin-bottom: 0;
            color: var(--text-primary);
        }}

        /* Tables */
        .table-wrapper {{
            overflow-x: auto;
            margin: 24px 0;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13.5px;
            text-align: left;
        }}

        th {{
            background-color: var(--table-header-bg);
            color: #ffffff;
            font-weight: 600;
            padding: 14px 16px;
            border-bottom: 2px solid var(--border-color);
            white-space: nowrap;
        }}

        td {{
            padding: 13px 16px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: top;
            line-height: 1.65;
        }}

        tr:nth-child(even) {{
            background-color: var(--table-row-even);
        }}

        tr:nth-child(odd) {{
            background-color: var(--table-row-odd);
        }}

        tr:hover {{
            background-color: var(--surface-hover);
        }}

        /* Code & Pre */
        code {{
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            background-color: rgba(148, 163, 184, 0.15);
            color: #f87171;
            padding: 3px 7px;
            border-radius: 5px;
            font-size: 13px;
        }}

        pre {{
            background-color: var(--code-bg);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 16px 20px;
            overflow-x: auto;
            margin: 16px 0 20px 0;
            position: relative;
        }}

        pre code {{
            background: none;
            color: #4ade80;
            padding: 0;
            font-size: 13.5px;
            line-height: 1.6;
            display: block;
        }}

        /* Horizontal Rule */
        hr {{
            border: none;
            border-top: 1px solid var(--border-color);
            margin: 36px 0;
        }}

        /* Print Optimization */
        @media print {{
            body {{
                background-color: #ffffff !important;
                color: #1e293b !important;
                padding: 0 !important;
                font-size: 11pt !important;
            }}
            .container {{
                max-width: 100% !important;
                border: none !important;
                padding: 0 !important;
                box-shadow: none !important;
                background: #ffffff !important;
            }}
            h1 {{
                font-size: 18pt !important;
                color: #1e3a8a !important;
                page-break-after: avoid;
            }}
            h2 {{
                font-size: 14pt !important;
                color: #1e3a8a !important;
                border-bottom: 2px solid #cbd5e1 !important;
                page-break-after: avoid;
                margin-top: 24pt !important;
            }}
            h3 {{
                font-size: 12pt !important;
                color: #2563eb !important;
                page-break-after: avoid;
                margin-top: 16pt !important;
            }}
            .badge {{
                border: 1px solid #cbd5e1 !important;
                color: #334155 !important;
                background: #f1f5f9 !important;
            }}
            .callout {{
                background: #f8fafc !important;
                border-left: 4px solid #0284c7 !important;
                color: #1e293b !important;
                border: 1px solid #e2e8f0 !important;
                border-left-width: 4px !important;
            }}
            .callout-header {{ color: #0369a1 !important; }}
            .callout-body p {{ color: #1e293b !important; }}
            .table-wrapper {{
                border: 1px solid #cbd5e1 !important;
                box-shadow: none !important;
            }}
            table {{
                font-size: 9pt !important;
            }}
            th {{
                background-color: #f1f5f9 !important;
                color: #0f172a !important;
                border-bottom: 2px solid #cbd5e1 !important;
            }}
            td {{
                border-bottom: 1px solid #e2e8f0 !important;
                color: #334155 !important;
            }}
            tr:nth-child(even), tr:nth-child(odd) {{
                background-color: #ffffff !important;
            }}
            pre {{
                background-color: #f8fafc !important;
                border: 1px solid #cbd5e1 !important;
                color: #0f172a !important;
            }}
            pre code {{
                color: #15803d !important;
            }}
            code {{
                background-color: #f1f5f9 !important;
                color: #b91c1c !important;
                border: 1px solid #e2e8f0 !important;
            }}
            tr, .callout, pre {{
                page-break-inside: avoid;
            }}
            @page {{
                size: A4;
                margin: 12mm 15mm 12mm 15mm;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="doc-header">
            <div class="badge-row">
                <span class="badge badge-primary">Antigravity Guide</span>
                <span class="badge badge-version">korean-stock-analyzer v4.2.2</span>
                <span class="badge badge-success">Gemini 4.0 Argon</span>
            </div>
        </div>
        <div class="content">
            {html_body}
        </div>
    </div>
    <script>
        // Wrap tables with responsive wrapper
        document.querySelectorAll('table').forEach(tbl => {{
            const wrapper = document.createElement('div');
            wrapper.className = 'table-wrapper';
            tbl.parentNode.insertBefore(wrapper, tbl);
            wrapper.appendChild(tbl);
        }});
    </script>
</body>
</html>'''

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(full_html)

print(f'Generated HTML: {html_path} ({os.path.getsize(html_path):,} bytes)')
