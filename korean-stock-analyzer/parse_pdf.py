import sys
import os

# Ensure UTF-8 output even in Windows cmd/PowerShell with CP949
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pypdf

def extract_pdf(pdf_path, max_pages=None, search_keyword=None):
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        return
    
    try:
        reader = pypdf.PdfReader(pdf_path)
        total_pages = len(reader.pages)
        print(f"=== Total Pages: {total_pages} for {os.path.basename(pdf_path)} ===")
        
        pages_to_read = range(min(total_pages, max_pages)) if max_pages else range(total_pages)
        
        for i in pages_to_read:
            text = reader.pages[i].extract_text() or ""
            if search_keyword:
                if search_keyword.lower() in text.lower():
                    print(f"\n--- [Page {i+1}] (Keyword: '{search_keyword}') ---")
                    print(text[:2000])
            else:
                print(f"\n--- [Page {i+1}] ---")
                print(text[:1500])
    except Exception as e:
        print(f"Error reading PDF: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_pdf.py <pdf_path> [max_pages] [search_keyword]")
        sys.exit(1)
    
    path = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    keyword = sys.argv[3] if len(sys.argv) > 3 else (sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].isdigit() else None)
    
    extract_pdf(path, pages, keyword)
