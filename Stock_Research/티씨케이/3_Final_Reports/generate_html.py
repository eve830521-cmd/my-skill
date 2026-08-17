import markdown
import sys
import os

md_file = r'e:\antigravity-work\Stock_Research\티씨케이\3_Final_Reports\티씨케이_Master_Report.md'
css_file = r'e:\antigravity-work\my-skill\korean-stock-analyzer\report_style.css'
out_file = r'e:\antigravity-work\Stock_Research\티씨케이\3_Final_Reports\티씨케이_Master_Report.html'

with open(md_file, 'r', encoding='utf-8') as f:
    md_text = f.read()

with open(css_file, 'r', encoding='utf-8') as f:
    css_text = f.read()

html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])

html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>티씨케이 Master Report</title>
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
        }});
    </script>
</body>
</html>
"""

with open(out_file, 'w', encoding='utf-8') as f:
    f.write(html_template)

print(f"Generated HTML successfully: {out_file}")
