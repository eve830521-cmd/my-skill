"""
High-Speed PDF Parser & Anchor Keyword Verifier for Korean Stock Analyzer (v4.2)
- Supports direct text extraction by page limit or keyword search.
- Supports --verify-anchor mode to deterministically verify source_page and anchor_keyword
  from s_curve_registry.json (No-Blind-Citation enforcement).
"""

import sys
import os
import json
import argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
                if search_keyword.lower() not in text.lower():
                    continue
                print(f"\n--- [Page {i + 1}] (Keyword: '{search_keyword}') ---")
                print(text[:2000])
            else:
                print(f"\n--- [Page {i + 1}] ---")
                print(text[:1500])
    except Exception as e:
        print(f"Error reading PDF: {e}")


def verify_anchor_in_pdf(pdf_path, page_num, anchor_keywords):
    """
    Deterministically checks whether all tokens in anchor_keywords exist on page_num (1-indexed).
    Returns a dict suitable for automated pipeline verification.
    """
    if not os.path.exists(pdf_path):
        return {"verified": False, "reason": f"File not found: {pdf_path}", "page": page_num}

    try:
        reader = pypdf.PdfReader(pdf_path)
        if page_num < 1 or page_num > len(reader.pages):
            return {
                "verified": False,
                "reason": f"Page {page_num} out of bounds (1..{len(reader.pages)})",
                "page": page_num,
            }
        page_text = reader.pages[page_num - 1].extract_text() or ""
        norm_text = " ".join(page_text.split()).lower()
        tokens = [t.strip() for t in anchor_keywords.split() if t.strip()]
        missing = [t for t in tokens if t.lower() not in norm_text]
        return {
            "verified": len(missing) == 0,
            "page": page_num,
            "matched_tokens": [t for t in tokens if t.lower() in norm_text],
            "missing_tokens": missing,
            "excerpt": " ".join(page_text.split())[:400],
        }
    except Exception as e:
        return {"verified": False, "reason": str(e), "page": page_num}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_pdf.py <pdf_path> [max_pages] [search_keyword]")
        print("   or: python parse_pdf.py <pdf_path> --verify-page <page_num> --anchor <keywords>")
        sys.exit(1)

    if "--verify-page" in sys.argv:
        p_parser = argparse.ArgumentParser()
        p_parser.add_argument("pdf_path")
        p_parser.add_argument("--verify-page", type=int, required=True)
        p_parser.add_argument("--anchor", type=str, required=True)
        args = p_parser.parse_args()
        v_res = verify_anchor_in_pdf(args.pdf_path, args.verify_page, args.anchor)
        print(json.dumps(v_res, ensure_ascii=False, indent=2))
        sys.exit(0 if v_res.get("verified") else 2)

    path = sys.argv[1]
    pages = int(sys.argv[2]) if (len(sys.argv) > 2 and sys.argv[2].isdigit()) else None
    keyword = (
        sys.argv[3]
        if len(sys.argv) > 3
        else (sys.argv[2] if (len(sys.argv) > 2 and not sys.argv[2].isdigit()) else None)
    )

    extract_pdf(path, pages, keyword)