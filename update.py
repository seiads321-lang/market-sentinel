#!/usr/bin/env python3
"""7-signal US market risk dashboard ETL. Never treats missing data as 'normal'.

Daily feeds update only when an observation date advances. Monthly releases update
only when the publisher releases a new observation period (or official revision).
"""
from __future__ import annotations
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from statistics import mean
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
DATA_DIR = ROOT / 'data'
SNAPSHOT = DATA_DIR / 'signals.json'
MONTHLY = DATA_DIR / 'monthly.json'
EPS_INBOX = DATA_DIR / 'eps_release.json'
HEADERS = {'User-Agent': 'MarketWarningDashboard/1.0 (+personal research; monthly data released only)'}

MONTHS = {m: i for i, m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'], 1)}
STATUS_LEVEL = {'unknown': -1, 'normal': 0, 'watch': 1, 'danger': 2}

SPECS = [
  ('curve','장단기 국채금리','일간','FRED T10Y2Y','https://fred.stlouisfed.org/series/T10Y2Y'),
  ('breadth','마켓 브레스','거래일','DeanFi · S&P 500 구성종목 (비공식)','https://github.com/DeanFinancials/deanfi-data'),
  ('credit','하이일드 신용 스프레드','일간','FRED BAMLH0A0HYM2','https://fred.stlouisfed.org/series/BAMLH0A0HYM2'),
  ('fed','연준 금리 인하 사이클','정책 발표 시','FRED DFEDTARU','https://fred.stlouisfed.org/series/DFEDTARU'),
  ('cape','CAPE·EPS 전망','월간 / 자료 발표 시','Robert Shiller + EPS 컨센서스','https://www.econ.yale.edu/~shiller/data.htm'),
  ('lei','미국 경기선행지수 LEI','월간','The Conference Board','https://www.conference-board.org/topics/us-leading-indicators/'),
  ('sector','방어주 중심 섹터 로테이션','거래일','Alpha Vantage · 섹터 ETF 종가','https://www.alphavantage.co/documentation/'),
]

def read_json(path, default):
    try:
        return json.loads(Path(path).read_text('utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def get_json(url, params=None, timeout=25):
    response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()

def get_fred(series_id):
    key = os.getenv('FRED_API_KEY', '').strip()
    if not key:
        raise RuntimeError('FRED_API_KEY 미설정')
    payload = get_json('https://api.stlouisfed.org/fred/series/observations', {
        'series_id': series_id, 'api_key': key, 'file_type': 'json',
        'sort_order': 'desc', 'limit': 450})
    out = []
    for row in reversed(payload.get('observations', [])):
        try:
            out.append({'date': row['date'], 'value': float(row['value'])})
        except (TypeError, ValueError, KeyError):
            continue
    if not out:
        raise ValueError(f'{series_id}: 관측값 없음')
    return out

def level_curve(data):
    if len(data) < 90:
        return ('unknown', '과거 90거래일 자료가 부족합니다.')
    x = data[-1]['value']
    # An inversion is necessary for a re-steepening (recross) event.
    previous = [d['value'] for d in data[-91:-1]]
    recent = [d['value'] for d in data[-31:-1]]
    recently_inverted = min(previous) < 0
    recross = recently_inverted and x >= 0 and min(recent) < 0
    if recross:
        return ('danger', '직전 30거래일 내 역전→재역전이 확인됐습니다. 침체가 확정된 것은 아닙니다.')
    if x < 0:
        return ('watch', '10년물–2년물 금리차가 여전히 음수입니다.')
    if recently_inverted:
        return ('watch', '최근 90거래일 중 역전 이력이 있습니다.')
    return ('normal', '최근 90거래일에 장단기 금리 역전이 확인되지 않습니다.')

def level_credit(data):
    if len(data) < 22:
        return ('unknown', '과거 데이터가 부족합니다.')
    v = data[-1]['value']
    change = v - data[-22]['value']
    if v >= 7.0:
        return ('danger', f'하이일드 OAS {v:.2f}%p · 최근 21거래일 {change:+.2f}%p')
    if v >= 5.0 or change >= 1.5:
        return ('watch', f'하이일드 OAS {v:.2f}%p · 최근 21거래일 {change:+.2f}%p')
    return ('normal', f'하이일드 OAS {v:.2f}%p · 최근 21거래일 {change:+.2f}%p')

def level_fed(data):
    if len(data) < 180:
        return ('unknown', '인하 사이클을 분석할 장기 이력이 부족합니다.')
    # A first cut is an event (not a proof of recession), after >=120 days without cuts.
    first_cuts = []
    for i in range(121, len(data)):
        a, b = data[i - 1]['value'], data[i]['value']
        if b < a - .01:
            prior = data[i - 121:i]
            if all(prior[j]['value'] >= prior[j-1]['value'] - .01 for j in range(1, len(prior))):
                first_cuts.append(data[i]['date'])
    if first_cuts:
        age = (dt.date.fromisoformat(data[-1]['date']) - dt.date.fromisoformat(first_cuts[-1])).days
        if 0 <= age <= 90:
            return ('watch', f'{first_cuts[-1]} 첫 인하 탐지 · 인하 이유는 경제지표와 함께 해석해야 합니다.')
    return ('normal', '최근 90일 이내 새 금리 인하 사이클 시작 신호는 없습니다.')

def level_breadth(b, ad):
    try:
        d = b['data']; ma = d['moving_averages']
        p50 = float(ma['above_50_day_ma']['percentage'])
        series = ad['data']; latest = series[-1]
        if latest['date'] != d['date']:
            raise ValueError('두 피드의 최종 거래일 불일치')
        ad20 = mean(float(x['ad_line']) for x in series[-20:])
        ad50 = mean(float(x['ad_line']) for x in series[-50:])
        cur = float(latest['ad_line'])
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as e:
        raise ValueError(f'시장폭 자료 불완전: {e}')
    if len(series) < 50:
        return ('unknown', '시장폭 이동평균 데이터 부족', None)
    if p50 < 40 and cur < ad20 and cur < ad50:
        level = 'danger'
    elif p50 < 50 or (cur < ad20 and cur < ad50):
        level = 'watch'
    else:
        level = 'normal'
    return (level, f'50일선 위 종목 {p50:.1f}% · A/D Line 20일선 {"하회" if cur < ad20 else "상회"}', p50)

def ingest_monthly(records, item):
    """Newly published periods only; same-period changes need an explicit official revision."""
    period = item['period']
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', period):
        raise ValueError('월간 기준월 YYYY-MM 형식이 아닙니다.')
    if not item.get('released_at') or not item.get('source'):
        raise ValueError('발표일 및 원천 링크가 필요합니다.')
    existing = next((i for i, old in enumerate(records) if old['period'] == period), None)
    if existing is not None:
        old = records[existing]
        if item.get('revision') is True and item['released_at'] > old['released_at']:
            records[existing] = {k:v for k,v in item.items() if k != 'revision'}
            return True
        return False
    if records and period < max(x['period'] for x in records):
        # Avoid silent insertion of unverified historical values.
        return False
    records.append({k:v for k,v in item.items() if k != 'revision'})
    records.sort(key=lambda x: x['period'])
    return True

def ingest_lei(monthly):
    url = 'https://www.conference-board.org/topics/us-leading-indicators/'
    r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
    text = BeautifulSoup(r.text, 'html.parser').get_text(' ', strip=True)
    idx = text.find('Latest Press Release')
    if idx < 0: raise ValueError('공식 LEI 발표 구역이 없습니다.')
    block = text[idx:idx + 2100]
    # Deliberately fail closed if the publisher changes the sentence structure.
    m = re.search(r'Leading Economic Index.{0,40}?\(LEI\) for the US.{0,130}?\bin (January|February|March|April|May|June|July|August|September|October|November|December) (20\d{2}) (?:at|to) (\d+(?:\.\d+)?)\b', block, re.I)
    stamp = re.search(r'Updated:\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(20\d{2})', block, re.I)
    if not (m and stamp):
        raise ValueError('공식 LEI 본문에서 기준월·수치·발표일을 안전하게 확인할 수 없습니다.')
    period = f'{m.group(2)}-{MONTHS[m.group(1).title()]:02d}'
    release = f'{stamp.group(3)}-{MONTHS[stamp.group(1).title()]:02d}-{int(stamp.group(2)):02d}'
    item = {'period':period, 'released_at':release, 'value':float(m.group(3)), 'source':url}
    return ingest_monthly(monthly['lei'], item)

def ingest_shiller(monthly):
    """Use official Yale monthly spreadsheet. If unavailable, keep prior release."""
    from io import BytesIO
    import pandas as pd
    url = 'https://www.econ.yale.edu/~shiller/data/ie_data.xls'
    r = requests.get(url, headers=HEADERS, timeout=40); r.raise_for_status()
    sheet = pd.read_excel(BytesIO(r.content), sheet_name=0, header=None)
    # Shiller data have a variable number of introductory header rows.
    header = None
    for i in range(min(len(sheet), 35)):
        row = [str(x).strip().upper() for x in sheet.iloc[i].tolist()]
        if 'DATE' in row and 'CAPE' in row:
            header = (i, row.index('DATE'), row.index('CAPE')); break
    if not header:
        raise ValueError('Shiller 파일에서 DATE/CAPE 열을 찾지 못했습니다.')
    _, dcol, ccol = header
    valid = []
    for j in range(header[0] + 1, len(sheet)):
        try:
            v = float(sheet.iat[j, dcol]); cape = float(sheet.iat[j, ccol])
            year = int(v); mon = round((v-year)*100)
            if 1871 <= year <= 2100 and 1 <= mon <= 12 and 0 < cape < 300:
                valid.append((f'{year}-{mon:02d}', cape))
        except (ValueError, TypeError):
            continue
    if not valid: raise ValueError('Shiller CAPE 월간 데이터 없음')
    period, cape = max(valid, key=lambda x:x[0])
    # Publication day not generally stamped in the spreadsheet; first-detection date.
    item = {'period':period, 'released_at':dt.date.today().isoformat(), 'value':cape,
            'source':'https://www.econ.yale.edu/~shiller/data.htm', 'release_date_type':'first_detected'}
    return ingest_monthly(monthly['cape'], item)

def ingest_eps(monthly):
    inbox = read_json(EPS_INBOX, {})
    if not inbox or not all(k in inbox for k in ('period','released_at','value','source')):
        return False
    if not isinstance(inbox['value'], (int,float)) or inbox['value'] <= 0:
        raise ValueError('EPS는 양의 숫자여야 합니다.')
    return ingest_monthly(monthly['eps'], inbox)

def sample_card(id, status, value, date, note, *, release_date=None, history=None):
    spec = next(s for s in SPECS if s[0] == id)
    return {'id': id, 'name': spec[1], 'frequency':spec[2], 'provider':spec[3],
            'source_url':spec[4], 'status':status, 'value':value, 'observation_date':date,
            'released_at':release_date, 'note':note, 'history':history or []}

def outdated(date, days=8):
    return (dt.datetime.now(dt.timezone.utc).date() - dt.date.fromisoformat(date)).days > days

def curve_card():
    series = get_fred('T10Y2Y')
    status, note = level_curve(series)
    if outdated(series[-1]['date']): status, note = 'unknown', '데이터가 8일 이상 지연되었습니다. ' + note
    return sample_card('curve',status,f"{series[-1]['value']:+.2f}%p",series[-1]['date'],note,history=series[-70:])

def credit_card():
    series = get_fred('BAMLH0A0HYM2')
    status, note = level_credit(series)
    if outdated(series[-1]['date']): status, note = 'unknown', '데이터 지연. '+note
    return sample_card('credit',status,f"{series[-1]['value']:.2f}%p",series[-1]['date'],note,history=series[-70:])

def fed_card():
    series = get_fred('DFEDTARU')
    status, note = level_fed(series)
    if outdated(series[-1]['date'], 10): status, note = 'unknown', '데이터 지연. '+note
    return sample_card('fed',status,f"{series[-1]['value']:.2f}%",series[-1]['date'],note,history=series[-90:])

def breadth_card():
    base='https://raw.githubusercontent.com/DeanFinancials/deanfi-data/main/advance-decline/'
    b = get_json(base+'daily_breadth.json'); ad=get_json(base+'ad_line_historical.json')
    level,note,p50=level_breadth(b, ad)
    date=b['data']['date']
    if outdated(date): level,note='unknown','비공식 원천 데이터 지연. '+note
    return sample_card('breadth',level,f'{p50:.1f}%' if p50 is not None else None,date,note,
             history=[{'date':x['date'],'value':x['ad_line']} for x in ad['data'][-70:]])

def lei_card(monthly):
    entries=monthly.get('lei',[])
    if not entries:return sample_card('lei','unknown',None,None,'공식 발표값이 아직 연결되지 않았습니다.')
    recent=entries[-1]; vals=[float(x['value']) for x in entries]
    streak=0
    for i in range(len(vals)-1,0,-1):
        if vals[i] < vals[i-1]:streak+=1
        else:break
    if len(vals)>=7:
        status='danger' if streak>=6 else 'watch' if streak>=3 else 'normal'
        note=f'월간 연속 하락 {streak}개월 · 매월 공식 발표 시에만 변경'
    else:
        status='unknown'
        note=f'월간 연속 하락 판정에 필요한 7개 월 자료 중 {len(vals)}개 확보 · 수치는 정상 반영 중'
    return sample_card('lei',status,f"{recent['value']:.1f}",recent['period'],note,
        release_date=recent['released_at'],history=[{'date':e['period'], 'value':e['value']} for e in entries[-24:]])

def cape_card(monthly):
    capes=monthly.get('cape',[]); eps=monthly.get('eps',[])
    capestr= f"CAPE {capes[-1]['value']:.1f}" if capes else 'CAPE 미연결'
    if capes and eps and len(capes)>=4 and len(eps)>=3:
        peak=max(x['value'] for x in capes[-4:])
        cape_fall=(peak - capes[-1]['value'])/peak
        eps_fall=(eps[-3]['value']-eps[-1]['value'])/eps[-3]['value']
        status='danger' if cape_fall > .05 and eps_fall > .03 else 'watch' if cape_fall > .03 and eps_fall > .01 else 'normal'
        note=f'최근 4개월 CAPE 고점 대비 {cape_fall*100:.1f}% 하락 · EPS 3개월 변화 {(-eps_fall)*100:+.1f}%'
    else:
        status='unknown'; note='CAPE 4개월·EPS 3개월의 공식/검증된 발표 이력이 모두 있어야 복합 신호를 판정합니다.'
    period=max([x[-1]['period'] for x in (capes,eps) if x],default=None)
    released=max([x[-1]['released_at'] for x in (capes,eps) if x],default=None)
    return sample_card('cape',status,capestr if capes else None,period,note,release_date=released,
        history=[{'date':e['period'],'value':e['value']} for e in capes[-24:]])

def etf_closes(symbol):
    key = os.getenv('ALPHAVANTAGE_API_KEY', '').strip()
    if not key: raise RuntimeError('ALPHAVANTAGE_API_KEY 미설정')
    payload=get_json('https://www.alphavantage.co/query',{'function':'TIME_SERIES_DAILY','symbol':symbol,'apikey':key,'outputsize':'compact'})
    rows=payload.get('Time Series (Daily)')
    if not isinstance(rows,dict):
        raise ValueError(f'{symbol}: ETF 시계열이 응답되지 않았습니다. API 제한/지원 범위를 확인하세요. '+str(payload.get('Note') or payload.get('Information') or ''))
    return {d:float(v['4. close']) for d,v in rows.items() if '4. close' in v}

def sector_card():
    tickers=['XLF','XLI','XLY','XLV','XLP','XLU']
    prices={}
    for j,symbol in enumerate(tickers):
        if j: time.sleep(13)  # free plan rate limits
        prices[symbol]=etf_closes(symbol)
    common=sorted(set.intersection(*(set(x.keys()) for x in prices.values())))
    if len(common)<22: raise ValueError('ETF 공통 거래일 22일 미만')
    day, old=common[-1],common[-22]
    returns={s:(prices[s][day]/prices[s][old]-1)*100 for s in tickers}
    cyc=mean(returns[s] for s in ['XLF','XLI','XLY'])
    defensive=mean(returns[s] for s in ['XLV','XLP','XLU'])
    diff=defensive-cyc
    status='danger' if diff>=5 and cyc < 0 else 'watch' if diff>=2.0 else 'normal'
    note=f'방어주 21거래일 평균 {defensive:+.1f}% / 경기민감주 {cyc:+.1f}% · 격차 {diff:+.1f}%p'
    if outdated(day):status,note='unknown','ETF 시세가 8일 이상 지연되었습니다. '+note
    return sample_card('sector',status,f'{diff:+.1f}%p',day,note,history=[{'date':day,'value':diff}])

def fallback_card(spec, previous=None, err=''):
    # Previous values retained only as historical reference; stale unverified statuses are unknown.
    if previous:
        previous=dict(previous)
        previous['status']='unknown'
        previous['note']=f'이번 수집 실패 · 이전 확인값 유지 (실시간 상태 아님). {err[:130]}'
        return previous
    return sample_card(spec[0],'unknown',None,None,f'데이터 연결 대기 · {err[:130]}')

def build():
    previous=read_json(SNAPSHOT,{})
    old_cards={c['id']:c for c in previous.get('cards',[])}
    monthly=read_json(MONTHLY,{'lei':[],'cape':[],'eps':[]})
    changed=[]; ingest_errors=[]
    for name,fx in [('LEI',ingest_lei),('CAPE',ingest_shiller),('EPS',ingest_eps)]:
        try:
            if fx(monthly): changed.append(f'{name} 신규 발표 데이터 수집')
        except Exception as e:
            ingest_errors.append(f'{name}: {str(e)[:130]}')
    save_json(MONTHLY,monthly)
    fn={'curve':curve_card,'breadth':breadth_card,'credit':credit_card,
        'fed':fed_card,'cape':lambda:cape_card(monthly),'lei':lambda:lei_card(monthly),
        'sector':sector_card}
    cards=[]
    for spec in SPECS:
        id=spec[0]
        try:
            c=fn[id]()
        except Exception as e:
            c=fallback_card(spec,old_cards.get(id),str(e))
            ingest_errors.append(f'{id}: {str(e)[:130]}')
        old=old_cards.get(id)
        if old and c['status']!=old['status']:
            changed.append(f'{c["name"]}: {old["status"]} → {c["status"]}')
        elif old and c['observation_date']!=old.get('observation_date') and c['observation_date']:
            changed.append(f'{c["name"]}: 관측값 {c["observation_date"]} 반영')
        cards.append(c)
    available=[c for c in cards if c['status']!='unknown']
    score=round(mean({'normal':0,'watch':50,'danger':100}[c['status']] for c in available)) if len(available)>=5 else None
    generated=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    snapshot={'generated_at':generated,'score':score,'coverage':len(available),'required_coverage':5,
              'cards':cards,'changes':changed,'warnings':ingest_errors,
              'method':'지표별 정상 0점·주의 50점·위험 100점 동등가중 평균. 7개 중 5개 미만 판정 시 점수 비공개. 검증되지 않은 참고 신호입니다.'}
    save_json(SNAPSHOT,snapshot)
    telegram(snapshot)
    return snapshot

def telegram(snapshot):
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); chat_id=os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat_id:return
    score='산출 보류' if snapshot['score'] is None else f"{snapshot['score']}/100"
    lines=[f'미국 증시 위험신호 | 일일 점검',f'종합 점수: {score} (판정 가능 {snapshot["coverage"]}/7)']
    for c in snapshot['cards']:
        flag={'normal':'정상','watch':'주의','danger':'위험','unknown':'미확인'}[c['status']]
        lines.append(f'{flag} | {c["name"]} | {c["value"] or "자료 없음"} | {c["observation_date"] or "-"}')
    if snapshot['changes']:lines.extend(['','변화:']+snapshot['changes'][:10])
    lines.append('※ 경기침체 예측 확정 또는 매매 권고 아님')
    try:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',data={'chat_id':chat_id,'text':'\n'.join(lines)},timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        print('텔레그램 발송 실패:',e)

if __name__=='__main__':
    result=build()
    print('업데이트:', result['generated_at'], '| 판정 가능:', result['coverage'], '| 점수:', result['score'])
    print('오류:',*result['warnings'],sep='\n - ')
