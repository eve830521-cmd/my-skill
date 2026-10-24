"""
Portfolio Manager & Batch Update Pipeline for Korean Stock Analyzer (v4.2.3)
- Manages holding vs watchlist classification in portfolio_status.json.
- Prioritizes stocks newly upgraded from watchlist to holding.
- Automatically re-generates mobile 1-page summary upon completion.
"""

import os
import sys
import json
import argparse
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_RESEARCH_DIR = os.path.join(SKILL_DIR, "..", "Stock_Research")
PORTFOLIO_STATUS_PATH = os.path.join(STOCK_RESEARCH_DIR, "portfolio_status.json")


def load_portfolio_status() -> dict:
    if os.path.exists(PORTFOLIO_STATUS_PATH):
        try:
            with open(PORTFOLIO_STATUS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[경고] portfolio_status.json 로드 실패: {e}")
    return {"updated_at": datetime.now().isoformat(), "items": {}}


def save_portfolio_status(data: dict):
    data["updated_at"] = datetime.now().isoformat()
    with open(PORTFOLIO_STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[저장 완료] {PORTFOLIO_STATUS_PATH}")


def print_status(data: dict):
    items = data.get("items", {})
    holdings = [k for k, v in items.items() if v.get("status") == "holding"]
    watchlists = [k for k, v in items.items() if v.get("status") == "watchlist"]

    print("=" * 65)
    print(" 📊 Korean Stock Analyzer 포트폴리오 관리 현황")
    print("=" * 65)
    print(f"💼 보유 종목 (총 {len(holdings)}개):")
    for name in sorted(holdings):
        meta = items[name]
        last_up = meta.get("last_updated", "-")
        chg_at = meta.get("status_changed_at", "-")
        is_upgraded = (chg_at[:10] > last_up[:10]) if (chg_at != "-" and last_up != "-") else False
        upgraded_badge = " [🔥최근 관심➔보유 승격]" if is_upgraded else ""
        print(f"  • {name:<12} (최종갱신: {last_up}){upgraded_badge}")

    print(f"\n👀 관심 종목 (총 {len(watchlists)}개) [업데이트 동결]:")
    for name in sorted(watchlists):
        meta = items[name]
        last_up = meta.get("last_updated", "-")
        print(f"  • {name:<12} (최종갱신: {last_up})")
    print("=" * 65)


def set_status(data: dict, comp_name: str, new_status: str):
    if new_status not in ("holding", "watchlist"):
        print(f"[오류] 잘못된 상태 값입니다: '{new_status}'. 'holding' 또는 'watchlist'만 허용됩니다.")
        return

    items = data.setdefault("items", {})
    prev_meta = items.get(comp_name, {})
    prev_status = prev_meta.get("status", "unknown")

    now_iso = datetime.now().isoformat()
    if comp_name not in items:
        items[comp_name] = {
            "status": new_status,
            "status_changed_at": now_iso,
            "last_updated": "-",
        }
    else:
        items[comp_name]["status"] = new_status
        if prev_status != new_status:
            items[comp_name]["status_changed_at"] = now_iso

    save_portfolio_status(data)

    if prev_status == "watchlist" and new_status == "holding":
        print(f"🎉 [{comp_name}] 관심 ➔ 보유 승격 완료!")
        print(f"   차기 업데이트 실행 시 최우선 갱신(Full Clean Rebuild) 대상으로 자동 분류됩니다.")
    else:
        print(f"[{comp_name}] 상태가 '{prev_status}' ➔ '{new_status}'(으)로 변경되었습니다.")


def sync_from_text(data: dict, text: str):
    """Parses exported text/JSON from mobile summary [📋 분류 상태 복사]."""
    items = data.setdefault("items", {})
    now_iso = datetime.now().isoformat()
    parsed_count = 0

    # Try JSON parse first
    json_candidate = None
    if "{" in text and "}" in text:
        try:
            start_idx = text.find("{")
            end_idx = text.rfind("}") + 1
            json_candidate = json.loads(text[start_idx:end_idx])
        except Exception:
            json_candidate = None

    if json_candidate and isinstance(json_candidate, dict):
        h_list = json_candidate.get("holding", [])
        w_list = json_candidate.get("watchlist", [])
        for name in h_list:
            if name in items and items[name].get("status") == "watchlist":
                items[name]["status_changed_at"] = now_iso
            items.setdefault(name, {})["status"] = "holding"
            parsed_count += 1
        for name in w_list:
            items.setdefault(name, {})["status"] = "watchlist"
            parsed_count += 1
    else:
        # Fallback regex parsing
        h_match = re.search(r"보유[^\n:]*:\s*([^\n]+)", text)
        w_match = re.search(r"관심[^\n:]*:\s*([^\n]+)", text)
        if h_match:
            for name in [x.strip() for x in h_match.group(1).split(",") if x.strip()]:
                if name in items and items[name].get("status") == "watchlist":
                    items[name]["status_changed_at"] = now_iso
                items.setdefault(name, {})["status"] = "holding"
                parsed_count += 1
        if w_match:
            for name in [x.strip() for x in w_match.group(1).split(",") if x.strip()]:
                items.setdefault(name, {})["status"] = "watchlist"
                parsed_count += 1

    if parsed_count > 0:
        save_portfolio_status(data)
        print(f"[동기화 완료] 총 {parsed_count}개 종목의 보유/관심 분류가 정상 반영되었습니다.")
    else:
        print("[경고] 인식 가능한 보유/관심 텍스트 또는 JSON 형식을 찾을 수 없습니다.")


def run_portfolio_update(data: dict):
    items = data.get("items", {})
    holdings = [k for k, v in items.items() if v.get("status") == "holding"]
    watchlists = [k for k, v in items.items() if v.get("status") == "watchlist"]

    print("\n🚀 [포트폴리오 분석 파이프라인 가동]")
    print(f"• 업데이트 대상 (보유 종목): {len(holdings)}개")
    print(f"• 업데이트 스킵 (관심 종목): {len(watchlists)}개 -> {', '.join(watchlists) if watchlists else '없음'}")

    # Check for newly upgraded stocks (watchlist -> holding)
    upgraded_targets = []
    regular_targets = []

    for name in holdings:
        meta = items[name]
        chg_at = meta.get("status_changed_at", "")
        last_up = meta.get("last_updated", "")
        if chg_at and last_up and (chg_at[:10] > last_up[:10]):
            upgraded_targets.append(name)
        else:
            regular_targets.append(name)

    if upgraded_targets:
        print(f"\n🔥 [우선순위 1 — 관심➔보유 신규 승격 종목 (Full Rebuild 대상)]: {', '.join(upgraded_targets)}")
    if regular_targets:
        print(f"📈 [우선순위 2 — 기존 보유 종목 (정기 델타 업데이트 대상)]: {', '.join(regular_targets)}")

    # Update mobile summary automatically
    print("\n📱 [모바일 1페이지 통합요약본 최신화 실행 중...]")
    gen_script = os.path.join(SKILL_DIR, "generate_mobile_summary.py")
    if os.path.exists(gen_script):
        ret = os.system(f'python "{gen_script}"')
        if ret == 0:
            print("✅ 모바일 통합 요약본 최신 갱신 및 구글 드라이브 동기화 완수!")
        else:
            print(f"❌ 모바일 요약본 생성 스크립트 에러 코드: {ret}")


def main():
    parser = argparse.ArgumentParser(description="Korean Stock Analyzer Portfolio Manager & Batch Updater")
    parser.add_argument("--status", action="store_true", help="현재 포트폴리오(보유/관심) 분류 현황 출력")
    parser.add_argument("--set-status", nargs=2, metavar=("COMP_NAME", "STATUS"), help="종목 분류 변경 (예: --set-status 매커스 holding)")
    parser.add_argument("--sync-text", type=str, help="모바일 요약본에서 복사한 분류 텍스트/JSON 동기화")
    parser.add_argument("--run", action="store_true", help="보유 종목 대상 파이프라인 업데이트 실행")

    args = parser.parse_args()
    data = load_portfolio_status()

    if args.status:
        print_status(data)
    elif args.set_status:
        comp_name, new_stat = args.set_status
        set_status(data, comp_name, new_stat)
        # re-generate mobile summary immediately
        os.system(f'python "{os.path.join(SKILL_DIR, "generate_mobile_summary.py")}"')
    elif args.sync_text:
        sync_from_text(data, args.sync_text)
        os.system(f'python "{os.path.join(SKILL_DIR, "generate_mobile_summary.py")}"')
    elif args.run:
        run_portfolio_update(data)
    else:
        print_status(data)
        run_portfolio_update(data)


if __name__ == "__main__":
    main()
