"""
광고 레퍼런스 자동 수집기
--------------------------
- brands.json에 등록된 브랜드를 매일 자동 크롤링
- 광고 소재 + 랜딩페이지 스크린샷 세트로 저장
- output/ads_data.json에 누적, output/viewer.html로 확인
"""

import json
import time
import re
import os
import random
import hashlib
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote
from playwright.sync_api import sync_playwright

# ─── 설정 ───────────────────────────────────────────────
BRANDS_FILE   = Path("brands.json")
OUTPUT_DIR    = Path("output")
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
ADS_DATA_FILE = OUTPUT_DIR / "ads_data.json"
MAX_ADS_PER_BRAND = 10       # 브랜드당 최대 수집 광고 수
BRAND_DELAY   = (8, 15)      # 브랜드 사이 대기 (초, 랜덤)
AD_DELAY      = (2, 5)       # 광고 파싱 사이 대기 (초, 랜덤)
# ────────────────────────────────────────────────────────

OUTPUT_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def load_brands() -> list[dict]:
    with open(BRANDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_existing_ads() -> dict:
    """기존 수집 데이터 로드 (ad_id 기준 중복 방지)"""
    if ADS_DATA_FILE.exists():
        with open(ADS_DATA_FILE, encoding="utf-8") as f:
            ads = json.load(f)
        return {a["ad_id"]: a for a in ads}
    return {}


def save_ads(ads_dict: dict):
    ads_list = sorted(ads_dict.values(), key=lambda x: x.get("collected_at", ""), reverse=True)
    with open(ADS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(ads_list, f, ensure_ascii=False, indent=2)


def make_ad_id(brand_name: str, image_url: str, copy: str) -> str:
    raw = f"{brand_name}_{image_url}_{copy[:50]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def scrape_brand(page, brand: dict) -> list[dict]:
    """브랜드 1개 크롤링"""
    keyword = brand.get("search_keyword") or brand.get("name")
    url = (
        f"https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country=KR"
        f"&q={keyword}&search_type=keyword_unordered&media_type=all"
    )

    print(f"\n  🔍 [{brand['name']}] 검색 중...")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(random.uniform(4, 7))

    # 로그인 팝업 닫기
    for selector in ['[aria-label="닫기"]', '[aria-label="Close"]', '[data-testid="dialog-close-button"]']:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(1)
                break
        except Exception:
            pass

    # 스크롤해서 광고 로드
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 1000)")
        time.sleep(random.uniform(1.5, 3))

    # 광고 카드 수집
    ad_cards = page.locator('[data-testid="ad-archive-card"]').all()
    if not ad_cards:
        # fallback 셀렉터
        ad_cards = page.locator('div[class*="x1qjc9v5"]').all()

    print(f"  📦 광고 카드 {len(ad_cards)}개 발견")

    ads = []
    for i, card in enumerate(ad_cards[:MAX_ADS_PER_BRAND]):
        try:
            ad = {"brand": brand["name"], "brand_memo": brand.get("memo", "")}

            # 광고 카피
            try:
                ad["copy"] = card.locator("div[style*='white-space'] span").first.inner_text(timeout=2000).strip()[:300]
            except Exception:
                ad["copy"] = ""

            # 광고 이미지
            try:
                ad["image_url"] = card.locator("img").first.get_attribute("src", timeout=2000) or ""
            except Exception:
                ad["image_url"] = ""

            # 영상 썸네일 fallback
            if not ad["image_url"]:
                try:
                    ad["image_url"] = card.locator("video").first.get_attribute("poster", timeout=1000) or ""
                except Exception:
                    pass

            # 랜딩 URL 추출
            landing_url = ""
            try:
                links = card.locator("a[href]").all()
                for link in links:
                    href = link.get_attribute("href") or ""
                    # Meta 리다이렉트 URL 디코딩
                    if "l.facebook.com/l.php?u=" in href:
                        match = re.search(r"u=([^&]+)", href)
                        if match:
                            landing_url = unquote(match.group(1))
                            break
                    elif href.startswith("http") and "facebook.com" not in href and "instagram.com" not in href:
                        landing_url = href
                        break
            except Exception:
                pass
            ad["landing_url"] = landing_url

            # 게재 시작일
            try:
                ad["started"] = card.locator("*[class*='date']").first.inner_text(timeout=1000).strip()
            except Exception:
                ad["started"] = ""

            ad["screenshot"] = ""
            ad["collected_at"] = datetime.now().strftime("%Y-%m-%d")
            ad["ad_id"] = make_ad_id(brand["name"], ad["image_url"], ad["copy"])

            ads.append(ad)
            time.sleep(random.uniform(*AD_DELAY))

        except Exception as e:
            print(f"    ⚠️ 파싱 실패: {e}")
            continue

    print(f"  ✅ {len(ads)}개 수집")
    return ads


def screenshot_landings(ads: list[dict], existing_ids: set) -> list[dict]:
    """랜딩페이지 스크린샷 (신규 광고만)"""
    to_shoot = [a for a in ads if a.get("landing_url") and a["ad_id"] not in existing_ids]
    print(f"\n📸 신규 광고 랜딩 스크린샷: {len(to_shoot)}개")

    if not to_shoot:
        return ads

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled"
        ])
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )

        for ad in to_shoot:
            try:
                page = context.new_page()
                print(f"  → {ad['brand']} / {ad['landing_url'][:60]}")
                page.goto(ad["landing_url"], wait_until="domcontentloaded", timeout=20000)
                time.sleep(random.uniform(2, 4))

                # 쿠키/팝업 닫기
                for sel in ["button:has-text('동의')", "button:has-text('확인')", "button:has-text('닫기')", "[aria-label='Close']"]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=1500):
                            btn.click()
                            time.sleep(0.5)
                    except Exception:
                        pass

                shot_path = SCREENSHOTS_DIR / f"{ad['ad_id']}_landing.png"
                page.screenshot(path=str(shot_path), full_page=True, timeout=15000)
                ad["screenshot"] = str(shot_path)
                page.close()
                time.sleep(random.uniform(1, 3))

            except Exception as e:
                print(f"    ⚠️ 실패: {e}")
                try:
                    page.close()
                except Exception:
                    pass
                continue

        browser.close()

    return ads


def build_viewer(all_ads: list[dict]):
    """광고 소재 + 상세페이지 탭 뷰어 HTML 생성"""

    # 브랜드별 그룹핑
    brands = {}
    for ad in all_ads:
        b = ad["brand"]
        if b not in brands:
            brands[b] = []
        brands[b].append(ad)

    # 브랜드 필터 버튼
    brand_filters = '<button class="filter active" onclick="filterBrand(this, \'all\')">전체</button>'
    for b in brands:
        brand_filters += f'<button class="filter" onclick="filterBrand(this, \'{b}\')">{b}</button>'

    # 카드 HTML
    cards_html = ""
    for ad in all_ads:
        img_src = ad.get("image_url", "")
        has_shot = bool(ad.get("screenshot")) and Path(ad["screenshot"]).exists()
        shot_src = ad["screenshot"].replace("\\", "/") if has_shot else ""
        landing_url = ad.get("landing_url", "")
        uid = ad["ad_id"]

        cards_html += f"""
        <div class="card" data-brand="{ad['brand']}">
          <div class="card-top">
            <span class="brand-tag">{ad['brand']}</span>
            <span class="date">{ad.get('collected_at','')}</span>
          </div>
          <div class="tabs">
            <button class="tab active" onclick="switchTab(this,'{uid}-c')">광고 소재</button>
            <button class="tab" onclick="switchTab(this,'{uid}-l')">상세페이지</button>
          </div>
          <div class="panels">
            <div class="panel active" id="{uid}-c">
              {"<img src='"+img_src+"' alt='creative'>" if img_src else "<div class='empty-panel'>이미지 없음</div>"}
            </div>
            <div class="panel" id="{uid}-l">
              {"<img src='"+shot_src+"' alt='landing'>" if has_shot else "<div class='empty-panel'>"+("랜딩 URL 없음" if not landing_url else "스크린샷 준비 중")+"</div>"}
              {"<a href='"+landing_url+"' target='_blank' class='open-link'>↗ 상세페이지 열기</a>" if landing_url else ""}
            </div>
          </div>
          {"<p class='copy'>"+ad['copy'][:120]+"</p>" if ad.get('copy') else ""}
        </div>"""

    updated = datetime.now().strftime("%Y.%m.%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>광고 레퍼런스 보드</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'Pretendard',sans-serif;background:#0c0c0c;color:#e0e0e0;min-height:100vh}}

header{{padding:24px 32px;border-bottom:1px solid #1e1e1e;display:flex;align-items:center;gap:12px}}
header h1{{font-size:16px;font-weight:600;color:#fff;letter-spacing:-0.3px}}
header .updated{{font-size:12px;color:#444;margin-left:auto}}

.filters{{padding:16px 32px;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid #1a1a1a}}
.filter{{background:#161616;border:1px solid #2a2a2a;color:#666;font-size:12px;padding:5px 14px;border-radius:20px;cursor:pointer;transition:all .15s;font-family:inherit}}
.filter:hover{{color:#aaa;border-color:#444}}
.filter.active{{background:#fff;color:#000;border-color:#fff}}

.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1px;background:#1a1a1a}}

.card{{background:#0c0c0c;display:flex;flex-direction:column;transition:opacity .2s}}
.card.hidden{{display:none}}

.card-top{{padding:10px 14px 6px;display:flex;align-items:center;justify-content:space-between}}
.brand-tag{{font-size:11px;font-weight:600;color:#666;text-transform:uppercase;letter-spacing:.5px}}
.date{{font-size:11px;color:#333}}

.tabs{{display:flex;border-bottom:1px solid #1a1a1a;border-top:1px solid #1a1a1a}}
.tab{{flex:1;background:none;border:none;color:#3a3a3a;font-size:12px;padding:7px;cursor:pointer;transition:color .15s;font-family:inherit}}
.tab:hover{{color:#777}}
.tab.active{{color:#e0e0e0;box-shadow:inset 0 -2px 0 #e0e0e0}}

.panels{{flex:1}}
.panel{{display:none;flex-direction:column}}
.panel.active{{display:flex}}
.panel img{{width:100%;height:260px;object-fit:cover;object-position:top;display:block}}
.empty-panel{{height:260px;display:flex;align-items:center;justify-content:center;color:#2a2a2a;font-size:13px;background:#090909}}

.open-link{{display:block;padding:7px 14px;font-size:11px;color:#444;text-decoration:none;border-top:1px solid #1a1a1a;transition:color .15s}}
.open-link:hover{{color:#aaa}}

.copy{{padding:8px 14px 12px;font-size:12px;color:#3a3a3a;line-height:1.5;border-top:1px solid #161616;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}

.no-data{{grid-column:1/-1;padding:80px;text-align:center;color:#2a2a2a;font-size:14px}}
</style>
</head>
<body>

<header>
  <h1>광고 레퍼런스 보드</h1>
  <span style="font-size:12px;color:#333">{len(all_ads)}개 광고</span>
  <span class="updated">업데이트 {updated}</span>
</header>

<div class="filters">
  {brand_filters}
</div>

<div class="grid" id="grid">
  {"".join([cards_html]) if all_ads else '<div class="no-data">수집된 광고가 없어요. collect_ads.py를 먼저 실행해주세요.</div>'}
</div>

<script>
function switchTab(btn, panelId) {{
  const card = btn.closest('.card');
  card.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  card.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(panelId).classList.add('active');
}}

function filterBrand(btn, brand) {{
  document.querySelectorAll('.filter').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.card').forEach(card => {{
    if (brand === 'all' || card.dataset.brand === brand) {{
      card.classList.remove('hidden');
    }} else {{
      card.classList.add('hidden');
    }}
  }});
}}
</script>
</body>
</html>"""

    viewer_path = OUTPUT_DIR / "viewer.html"
    with open(viewer_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n🎉 뷰어 생성: {viewer_path}")


def main():
    print(f"=== 광고 레퍼런스 수집기 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    brands = load_brands()
    existing = load_existing_ads()
    existing_ids = set(existing.keys())
    print(f"브랜드 {len(brands)}개 / 기존 광고 {len(existing)}개")

    new_ads = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ])
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ko-KR",
        )
        page = context.new_page()

        for i, brand in enumerate(brands):
            ads = scrape_brand(page, brand)
            # 신규 광고만 추가
            for ad in ads:
                if ad["ad_id"] not in existing_ids:
                    new_ads.append(ad)

            # 브랜드 사이 딜레이 (마지막 브랜드 제외)
            if i < len(brands) - 1:
                delay = random.uniform(*BRAND_DELAY)
                print(f"  ⏱ 다음 브랜드까지 {delay:.0f}초 대기...")
                time.sleep(delay)

        browser.close()

    print(f"\n✨ 신규 광고 {len(new_ads)}개 발견")

    # 랜딩 스크린샷
    new_ads = screenshot_landings(new_ads, existing_ids)

    # 기존 데이터에 병합
    for ad in new_ads:
        existing[ad["ad_id"]] = ad

    # 저장
    save_ads(existing)
    print(f"💾 총 {len(existing)}개 저장 완료")

    # 뷰어 빌드
    all_ads = sorted(existing.values(), key=lambda x: x.get("collected_at", ""), reverse=True)
    build_viewer(all_ads)


if __name__ == "__main__":
    main()
