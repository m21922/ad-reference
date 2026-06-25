"""
광고 레퍼런스 자동 수집기 v2
- Meta 광고 라이브러리 크롤링
- 광고 소재 이미지 + 랜딩URL + 상세페이지 스크린샷
"""

import json
import time
import re
import random
import hashlib
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote, quote
from playwright.sync_api import sync_playwright

BRANDS_FILE     = Path("brands.json")
OUTPUT_DIR      = Path("output")
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
ADS_DATA_FILE   = OUTPUT_DIR / "ads_data.json"
MAX_ADS_PER_BRAND = 10
BRAND_DELAY     = (8, 15)

OUTPUT_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def load_brands():
    with open(BRANDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_existing_ads():
    if ADS_DATA_FILE.exists():
        with open(ADS_DATA_FILE, encoding="utf-8") as f:
            ads = json.load(f)
        return {a["ad_id"]: a for a in ads}
    return {}


def save_ads(ads_dict):
    ads_list = sorted(ads_dict.values(), key=lambda x: x.get("collected_at", ""), reverse=True)
    with open(ADS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(ads_list, f, ensure_ascii=False, indent=2)


def make_ad_id(brand, img, copy):
    return hashlib.md5(f"{brand}_{img}_{copy[:50]}".encode()).hexdigest()[:12]


def scrape_brand(page, brand):
    keyword = brand.get("search_keyword") or brand["name"]
    encoded = quote(keyword)
    url = (
        f"https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type=all&country=KR"
        f"&q={encoded}&search_type=keyword_unordered&media_type=all"
    )

    print(f"\n  🔍 [{brand['name']}] 검색: {keyword}")
    page.goto(url, wait_until="networkidle", timeout=40000)
    time.sleep(random.uniform(5, 8))

    # 팝업 닫기
    for sel in ['[aria-label="닫기"]', '[aria-label="Close"]', '[data-testid="dialog-close-button"]', 'div[role="dialog"] [role="button"]']:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(1)
                break
        except Exception:
            pass

    # 스크롤
    for _ in range(4):
        page.evaluate("window.scrollBy(0, 800)")
        time.sleep(random.uniform(1.5, 2.5))

    # 페이지 HTML 일부 출력 (디버그)
    html = page.content()
    print(f"  📄 페이지 길이: {len(html)} chars")

    # 광고 카드 찾기 - 여러 셀렉터 시도
    card_selectors = [
        '[data-testid="ad-archive-card"]',
        'div._7jyr',
        'div[class*="x1qjc9v5"][class*="x78zum5"]',
        'div[class*="xh8yej3"]',
    ]

    ad_cards = []
    for sel in card_selectors:
        cards = page.locator(sel).all()
        if len(cards) > 0:
            print(f"  ✅ 셀렉터 '{sel}' → {len(cards)}개")
            ad_cards = cards
            break
        else:
            print(f"  ❌ 셀렉터 '{sel}' → 0개")

    if not ad_cards:
        print(f"  ⚠️ 광고 카드를 찾지 못함. 스냅샷 저장...")
        page.screenshot(path=str(OUTPUT_DIR / f"debug_{brand['name']}.png"))
        # HTML 일부 저장
        with open(OUTPUT_DIR / f"debug_{brand['name']}.html", "w", encoding="utf-8") as f:
            f.write(html[:50000])
        return []

    print(f"  📦 {len(ad_cards)}개 카드 파싱 시작")
    ads = []

    for i, card in enumerate(ad_cards[:MAX_ADS_PER_BRAND]):
        try:
            ad = {"brand": brand["name"], "brand_memo": brand.get("memo", "")}

            # 카피 텍스트
            for copy_sel in [
                'div[style*="white-space: pre-wrap"] span',
                'div[style*="white-space:pre-wrap"] span',
                'div._4bl9',
                'span[class*="x193iq5w"]',
            ]:
                try:
                    text = card.locator(copy_sel).first.inner_text(timeout=1500).strip()
                    if text:
                        ad["copy"] = text[:300]
                        break
                except Exception:
                    pass
            if "copy" not in ad:
                ad["copy"] = ""

            # 이미지 URL
            ad["image_url"] = ""
            try:
                imgs = card.locator("img").all()
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    if src and ("fbcdn" in src or "cdninstagram" in src or src.startswith("http")):
                        ad["image_url"] = src
                        break
            except Exception:
                pass

            # 영상 썸네일 fallback
            if not ad["image_url"]:
                try:
                    poster = card.locator("video").first.get_attribute("poster", timeout=1000)
                    if poster:
                        ad["image_url"] = poster
                except Exception:
                    pass

            # 랜딩 URL
            ad["landing_url"] = ""
            try:
                links = card.locator("a[href]").all()
                for link in links:
                    href = link.get_attribute("href") or ""
                    if "l.facebook.com/l.php?u=" in href:
                        m = re.search(r"u=([^&]+)", href)
                        if m:
                            ad["landing_url"] = unquote(m.group(1))
                            break
                    elif (href.startswith("http")
                          and "facebook.com" not in href
                          and "instagram.com" not in href):
                        ad["landing_url"] = href
                        break
            except Exception:
                pass

            ad["started"] = ""
            ad["screenshot"] = ""
            ad["collected_at"] = datetime.now().strftime("%Y-%m-%d")
            ad["ad_id"] = make_ad_id(brand["name"], ad["image_url"], ad["copy"])

            ads.append(ad)
            print(f"    [{i+1}] img={'O' if ad['image_url'] else 'X'} landing={'O' if ad['landing_url'] else 'X'} copy={ad['copy'][:30]!r}")
            time.sleep(random.uniform(1, 2))

        except Exception as e:
            print(f"    ⚠️ [{i+1}] 파싱 오류: {e}")
            continue

    print(f"  ✅ {len(ads)}개 수집 완료")
    return ads


def screenshot_landings(ads, existing_ids):
    to_shoot = [a for a in ads if a.get("landing_url") and a["ad_id"] not in existing_ids]
    print(f"\n📸 랜딩 스크린샷: {len(to_shoot)}개")
    if not to_shoot:
        return ads

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ])
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        for ad in to_shoot:
            try:
                page = context.new_page()
                print(f"  → {ad['landing_url'][:70]}")
                page.goto(ad["landing_url"], wait_until="domcontentloaded", timeout=20000)
                time.sleep(random.uniform(2, 3))
                shot = SCREENSHOTS_DIR / f"{ad['ad_id']}_landing.png"
                page.screenshot(path=str(shot), full_page=True, timeout=15000)
                ad["screenshot"] = str(shot)
                page.close()
                time.sleep(random.uniform(1, 2))
            except Exception as e:
                print(f"    ⚠️ {e}")
                try:
                    page.close()
                except Exception:
                    pass
        browser.close()
    return ads


def build_viewer(all_ads):
    brands = {}
    for ad in all_ads:
        b = ad["brand"]
        if b not in brands:
            brands[b] = []
        brands[b].append(ad)

    brand_filters = '<button class="filter active" onclick="filterBrand(this,\'all\')">전체</button>'
    for b in brands:
        brand_filters += f'<button class="filter" onclick="filterBrand(this,\'{b}\')">{b}</button>'

    cards_html = ""
    for ad in all_ads:
        uid = ad["ad_id"]
        img = ad.get("image_url", "")
        has_shot = bool(ad.get("screenshot")) and Path(ad["screenshot"]).exists()
        shot = ad["screenshot"].replace("\\", "/") if has_shot else ""
        url = ad.get("landing_url", "")

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
              {"<img src='"+img+"'>" if img else "<div class='empty'>이미지 없음</div>"}
            </div>
            <div class="panel" id="{uid}-l">
              {"<img src='"+shot+"'>" if has_shot else "<div class='empty'>"+("랜딩 URL 없음" if not url else "스크린샷 준비 중")+"</div>"}
              {"<a href='"+url+"' target='_blank' class='open-link'>↗ 상세페이지 열기</a>" if url else ""}
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
body{{font-family:-apple-system,'Pretendard',sans-serif;background:#0c0c0c;color:#e0e0e0}}
header{{padding:24px 32px;border-bottom:1px solid #1e1e1e;display:flex;align-items:center;gap:12px}}
header h1{{font-size:16px;font-weight:600;color:#fff}}
header .updated{{font-size:12px;color:#444;margin-left:auto}}
.filters{{padding:16px 32px;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid #1a1a1a}}
.filter{{background:#161616;border:1px solid #2a2a2a;color:#666;font-size:12px;padding:5px 14px;border-radius:20px;cursor:pointer;transition:all .15s;font-family:inherit}}
.filter:hover{{color:#aaa;border-color:#444}}
.filter.active{{background:#fff;color:#000;border-color:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1px;background:#1a1a1a}}
.card{{background:#0c0c0c;display:flex;flex-direction:column}}
.card.hidden{{display:none}}
.card-top{{padding:10px 14px 6px;display:flex;align-items:center;justify-content:space-between}}
.brand-tag{{font-size:11px;font-weight:600;color:#666;text-transform:uppercase;letter-spacing:.5px}}
.date{{font-size:11px;color:#333}}
.tabs{{display:flex;border-bottom:1px solid #1a1a1a;border-top:1px solid #1a1a1a}}
.tab{{flex:1;background:none;border:none;color:#3a3a3a;font-size:12px;padding:7px;cursor:pointer;font-family:inherit}}
.tab:hover{{color:#777}}
.tab.active{{color:#e0e0e0;box-shadow:inset 0 -2px 0 #e0e0e0}}
.panels{{flex:1}}
.panel{{display:none;flex-direction:column}}
.panel.active{{display:flex}}
.panel img{{width:100%;height:260px;object-fit:cover;object-position:top;display:block}}
.empty{{height:260px;display:flex;align-items:center;justify-content:center;color:#2a2a2a;font-size:13px;background:#090909}}
.open-link{{display:block;padding:7px 14px;font-size:11px;color:#444;text-decoration:none;border-top:1px solid #1a1a1a}}
.open-link:hover{{color:#aaa}}
.copy{{padding:8px 14px 12px;font-size:12px;color:#3a3a3a;line-height:1.5;border-top:1px solid #161616;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
</style>
</head>
<body>
<header>
  <h1>광고 레퍼런스 보드</h1>
  <span style="font-size:12px;color:#333">{len(all_ads)}개 광고</span>
  <span class="updated">업데이트 {updated}</span>
</header>
<div class="filters">{brand_filters}</div>
<div class="grid">{"".join([cards_html]) if all_ads else '<div style="padding:80px;text-align:center;color:#333">수집된 광고 없음</div>'}</div>
<script>
function switchTab(btn,panelId){{
  const card=btn.closest('.card');
  card.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  card.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(panelId).classList.add('active');
}}
function filterBrand(btn,brand){{
  document.querySelectorAll('.filter').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.card').forEach(card=>{{
    if(brand==='all'||card.dataset.brand===brand)card.classList.remove('hidden');
    else card.classList.add('hidden');
  }});
}}
</script>
</body>
</html>"""

    viewer_path = OUTPUT_DIR / "viewer.html"
    with open(viewer_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n🎉 뷰어: {viewer_path}")


def main():
    print(f"=== 광고 레퍼런스 수집기 v2 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    brands = load_brands()
    existing = load_existing_ads()
    existing_ids = set(existing.keys())
    print(f"브랜드 {len(brands)}개 / 기존 {len(existing)}개")

    new_ads = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-web-security",
        ])
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        # 자동화 감지 우회
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = context.new_page()

        for i, brand in enumerate(brands):
            ads = scrape_brand(page, brand)
            for ad in ads:
                if ad["ad_id"] not in existing_ids:
                    new_ads.append(ad)
            if i < len(brands) - 1:
                delay = random.uniform(8, 15)
                print(f"  ⏱ {delay:.0f}초 대기...")
                time.sleep(delay)

        browser.close()

    print(f"\n✨ 신규 {len(new_ads)}개")
    new_ads = screenshot_landings(new_ads, existing_ids)

    for ad in new_ads:
        existing[ad["ad_id"]] = ad

    save_ads(existing)
    print(f"💾 총 {len(existing)}개 저장")

    all_ads = sorted(existing.values(), key=lambda x: x.get("collected_at", ""), reverse=True)
    build_viewer(all_ads)


if __name__ == "__main__":
    main()