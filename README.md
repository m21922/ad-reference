# 광고 레퍼런스 보드

Meta 광고 라이브러리에서 등록 브랜드의 광고 소재 + 랜딩 상세페이지를 매일 자동 수집합니다.

## 셋업 (최초 1회)

### 1. 이 레포 GitHub에 올리기
```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/[내계정]/ad-reference.git
git push -u origin main
```

### 2. GitHub Pages 켜기
Settings → Pages → Source: `main` 브랜치 `/output` 폴더 선택 → Save

이제 `https://[내계정].github.io/ad-reference/viewer.html` 에서 보드를 볼 수 있어요.

### 3. 브랜드 등록
`brands.json` 파일을 수정하세요:

```json
[
  {
    "name": "브랜드명",
    "search_keyword": "Meta 광고 라이브러리 검색 키워드",
    "memo": "경쟁사 / 참고"
  }
]
```

## 자동 실행
- 매일 **오전 10시 (KST)** 자동 수집
- GitHub → Actions 탭에서 수동 실행도 가능

## 로컬 테스트
```bash
pip install playwright
playwright install chromium
python collect_ads.py
# output/viewer.html 을 브라우저에서 열기
```
