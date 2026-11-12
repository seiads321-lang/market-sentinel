# Market Sentinel — 미국 증시 7대 위험신호 웹앱

휴대폰에 맞춘 정적 웹 대시보드입니다. GitHub Pages에 배포하고 GitHub Actions가 **매일 한국시간 오전 9시** 신규 자료를 확인합니다. 원천에 신규 관측치가 없으면 **이전 기준일과 값이 유지**됩니다. 텔레그램 알림은 선택 사항입니다.

## 빠른 시작

1. GitHub에 **새 저장소**를 만들고 이 폴더 안의 파일을 저장소 `main` 브랜치의 최상위에 올립니다. (`.github/workflows/` 폴더 포함)
2. GitHub 저장소 **Settings → Secrets and variables → Actions → New repository secret**에 `FRED_API_KEY`와 `ALPHAVANTAGE_API_KEY`를 등록합니다.
   - FRED: <https://fred.stlouisfed.org/docs/api/api_key.html>
   - Alpha Vantage: <https://www.alphavantage.co/support/#api-key>
3. GitHub **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 설정합니다.
4. **Actions → Daily signal update → Run workflow**를 한 번 실행합니다. 성공 후 **Deploy site to GitHub Pages**가 실행되고, Pages URL에서 대시보드를 볼 수 있습니다.
5. 선택: 텔레그램 봇 토큰 `TELEGRAM_BOT_TOKEN`과 개인 `TELEGRAM_CHAT_ID`를 시크릿으로 추가하면 업데이트 실행 때마다 일일 신호 요약을 받습니다. 시크릿은 웹페이지에 노출되지 않습니다.

> GitHub Actions 예약 실행은 정확히 정시를 보장하지 않습니다. 미국 휴장일과 공급자 데이터 지연이 있을 수 있습니다. 데이터 수집 실패 시 이전 수치는 참고용으로 남고 **신호 상태를 '미확인'으로 변경**합니다.

## 데이터 원칙

| 지표 | 공급자 | 반영 기준 | 기본 판단 |
|---|---|---|---|
| 장단기금리 | FRED T10Y2Y | 신규 거래일 | 90거래일 이내 역전 및 재역전 |
| 시장폭 | DeanFi의 공개 S&P500 구성종목 데이터 | 신규 거래일 | 50일선 위 비율과 A/D Line 20·50일선 |
| 하이일드 스프레드 | FRED BAMLH0A0HYM2 | 신규 거래일 | 5%p 주의, 7%p 위험 (참고 기준) |
| 연준 | FRED DFEDTARU | 목표금리가 변할 때 | 120일 이상 인하 없다가 첫 인하 후 90일 주의 |
| CAPE+EPS | Robert Shiller / EPS 검증 입력 | 새 발표 기준월 | CAPE 고점 후 하락 + EPS 하향 동시 발생 |
| LEI | The Conference Board | **새 공식 월간 발표 시에만** | 3개월 연속 하락 주의, 6개월 연속 하락 위험 |
| 섹터 로테이션 | Alpha Vantage 가격: XLF·XLI·XLY vs XLV·XLP·XLU | 신규 거래일 | 방어주 21거래일 상대 강세 |

이 7가지 분류 규칙은 모델링을 위해 설정한 **실험적 참고 임계치**입니다. 사후검증 없는 경기침체 예측이나 투자 권유가 아닙니다. 점수는 확률이 아닙니다. 필수 자료가 확인된 지표 **최소 5개**가 있어야 종합 점수를 표시합니다.

### 월간 지표 업데이트 규칙

`data/monthly.json`에 세 원천의 기록(`lei`, `cape`, `eps`)을 각각 저장합니다.

- 수집기가 매일 실행돼도 **원천의 `period`(기준월)이 기존과 같으면 값 변경 없음**.
- 공식 원천의 **새로운 기준월과 발표일**이 확인되면 새 기록을 추가합니다.
- 원천이 과거 값을 수정한 경우도 **새로운 공식 발표와 `revision: true`**가 있어야 같은 월 기록을 수정할 수 있습니다. 일반 재수집으로 바뀌지 않습니다.
- 정식 발표가 늦어졌다면 기준월은 그대로이며, 화면의 `발표일`과 `관측 기준`도 변하지 않습니다.
- Shiller CAPE Excel에는 명시적인 발표일이 없으므로 `released_at`은 **이 대시보드가 신규 월 자료를 처음 확인한 날짜**이며, JSON의 `release_date_type: first_detected`에 구분합니다.
- LEI는 공식 발표 웹페이지에서 기준월·수치·발표일을 보수적으로 파싱합니다. 문구가 바뀌어 확인에 실패하면 **기존 기록을 유지**합니다. 초기 데이터는 공식 발표가 확인된 **2026년 9월 99.6 (2026-10-22 발표)** 한 건으로 시작합니다. 연속 6개월 하락을 분석하려면 최소 7개월의 확정 월별 관측값이 필요하므로 그전에는 `미확인`으로 표시합니다.

### EPS 입력 방법

공개 검증 가능한 S&P 500 EPS 컨센서스 시계열은 소스별 접근 제한과 정의 차이가 있어 임의의 수치로 대체하지 않습니다. 검증된 제공자 데이터를 확보한 경우 `data/eps_release.json`에 **발표된 월에만** 아래 형식으로 입력하고, Actions를 수동 실행하세요.

```json
{
  "period": "2026-11",
  "released_at": "2026-11-20",
  "value": 300.0,
  "source": "https://검증된-제공자-출처.example"
}
```

**중요:** 위의 `300.0`, 발표일, URL은 문서 형식을 설명하는 예시이며 실제 데이터가 아닙니다. 이력 부족 시 복합 신호는 `미확인`으로 표시됩니다.

## 주요 파일

- `index.html`: 휴대폰 대응 대시보드. 값·기준일·발표일·근거·신호 상태 표시, 필터, CSV 내보내기
- `update.py`: 자동 수집 및 보수적 신호 산정. 텔레그램 요약 알림 지원
- `data/signals.json`: 웹사이트가 읽는 가장 최근 자동 점검 결과
- `data/monthly.json`: 월간 공식 발표값 스냅샷 (월별 신호 산정용)
- `data/eps_release.json`: 월간 EPS 컨센서스 검증 입력 (선택)
- `.github/workflows/daily.yml`: 매일 09:00 KST 자동 수집
- `.github/workflows/pages.yml`: GitHub Pages 게시
- `tests/test_update.py`: 월간 동결·공식 수정·자료 부족 검증

## HTML 바로 열기 (미리보기 전용)

`index.html`을 더블클릭하면 파일에 포함된 초기 스냅샷을 미리보기로 표시합니다. 브라우저 `file://` 환경에서는 자동 갱신이 되지 않으며 실시간 신호를 뜻하지 않습니다. GitHub Pages에 배포하면 `data/signals.json`을 읽어 매일 업데이트된 값으로 표시합니다.

## 로컬 보기

```bash
python -m http.server 8000
```

브라우저에서 `http://localhost:8000` 접속. 처음의 미확인값은 연결 전 상태이며 시뮬레이션 데이터가 아닙니다.

데이터 수집을 로컬에서 테스트하려면 환경변수 `FRED_API_KEY`, `ALPHAVANTAGE_API_KEY`를 설정하고 다음을 실행하세요.

```bash
pip install -r requirements.txt
python update.py
python -m unittest discover -s tests
```

## 운영상 유의사항

- 시장폭은 **비공식 GitHub 공개 피드**를 사용하며 배포처가 언제든지 변경되거나 중단될 수 있습니다. 공식 거래소 데이터 계약이 있으면 교체를 권장합니다.
- FRED·Alpha Vantage·Shiller·Conference Board의 서비스 약관과 이용 제한을 확인하세요. 무료 API의 사용량 제한 또는 요금 정책은 달라질 수 있습니다.
- LEI 공식 발표 페이지에 변경이 생기면 파서 보정이 필요할 수 있습니다. 특히 장기 이력은 구독이 필요할 수 있습니다.
- **초기 데이터가 불완전한 상태에서는 종합 위험 점수가 나오지 않는 것이 정상입니다.** 정확한 위험도 표시보다 확인되지 않은 데이터를 사실로 표시하지 않는 것을 우선합니다.
- 이 코드는 자동 저장/매일 요약 알림을 포함하지만 **24시간 실시간 감시나 즉시 푸시 알림을 보장하지 않습니다.**
