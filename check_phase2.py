"""Acceptance smoke checks for an already-running local Phase 2 server.

--run-live creates fresh sessions and can use Gemini/provider free quotas.
No API key is read by this script. No refresh, booking or payment is requested.
"""
import argparse
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import statistics
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class CheckStopped(Exception):pass


def request_json(base,path,data=None):
    body=None if data is None else json.dumps(data).encode('utf-8')
    req=Request(base+path,data=body,headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=120) as response:return json.load(response)


def records(body):
    if not isinstance(body,dict) or not isinstance(body.get('results',[]),list):return []
    return [row for part in body.get('results',[]) if isinstance(part,dict) and isinstance(part.get('items'),list) for row in part['items']]


def check_response(body,domain=None,language=None,no_cards=False):
    problems=[]
    if not isinstance(body,dict):return ['invalid_response']
    if body.get('error'):problems.append('backend_error')
    if not isinstance(body.get('message'),str) or not body['message'].strip():problems.append('empty_message')
    parts=body.get('results')
    if not isinstance(parts,list) or any(not isinstance(p,dict) or not isinstance(p.get('items'),list) for p in parts):return problems+['invalid_results']
    if domain and domain not in {p.get('domain') for p in parts}:problems.append('missing_domain')
    if language and body.get('response_language')!=language:problems.append('wrong_language')
    items=records(body)
    if no_cards and items:problems.append('unexpected_old_results')
    for row in items:
        if not isinstance(row,dict):problems.append('invalid_record');continue
        if re.fullmatch(r'[PFS]\d+',str(row.get('id',''))):problems.append('mock_id_in_phase2')
        link=urlsplit(str(row.get('source_url','')))
        if link.scheme!='https' or not link.hostname or link.username or link.password:problems.append('missing_or_unsafe_source_link')
        if row.get('data_mode') not in ('scraped','api_cache','api_live'):problems.append('missing_source_mode')
        if 'price_azn' in row:
            try:
                amount=Decimal(str(row['price_azn']))
                if not amount.is_finite() or amount<0:raise ValueError
            except (ValueError,InvalidOperation):problems.append('invalid_price')
            if row.get('currency')!='AZN':problems.append('unconfirmed_azn')
        if row.get('can_reserve') is True:problems.append('real_booking_enabled')
    return sorted(set(problems))


def run_checks(base,flight_date,requester=request_json):
    report={'scope':'Development smoke check, not a factual benchmark or Phase 2 completion certificate.',
            'created_at':datetime.now(timezone.utc).isoformat(),'turns':[],'stopped_early':False}
    health=requester(base,'/health');status=requester(base,'/api/live/status')
    if health.get('phase')!=2:raise ValueError('Phase 2 is required; no test messages were sent.')
    if not health.get('llm_configured'):raise ValueError('Gemini is not configured; no test messages were sent.')
    report['source_status']=status.get('sources',{});report['flight_api']=status.get('flight_api',{})
    month=flight_date.strftime('%Y-%m');month_name=flight_date.strftime('%B %Y')
    sid=requester(base,'/api/sessions',{})['session_id']
    original_ids=[];current_ids=[];flight_ids=[]

    def send(label,text,domain=None,language=None,no_cards=False,coverage=False,verify=None):
        start=time.perf_counter()
        try:
            body=requester(base,f'/api/sessions/{sid}/chat',{'message':text})
            issues=check_response(body,domain,language,no_cards)
            if not issues and verify:issues.extend(verify(body))
        except (OSError,ValueError,TypeError,KeyError):body={};issues=['transport_or_format_error']
        warning='no_source_results' if coverage and not issues and not records(body) else None
        verdict='FAIL' if issues else 'WARN' if warning else 'PASS'
        row={'check':label,'input':text,'verdict':verdict,'issues':issues,'warning':warning,
             'seconds':round(time.perf_counter()-start,2),'category':body.get('category'),
             'message':str(body.get('message',''))[:4000],'timings':body.get('timings',{}),
             'records':[{k:r.get(k) for k in ('id','price_azn','currency','departure_local','date','source','fetched_at','data_mode','price_kind','baggage_kg','seats_available')} for r in records(body) if isinstance(r,dict)]}
        report['turns'].append(row)
        print(f"{label}: {verdict} | {row['seconds']}s"+(f" | {','.join(issues)}" if issues else f' | {warning}' if warning else ''),flush=True)
        if issues:print('  Response:',row['message'][:1000],flush=True)
        if 'backend_error' in issues or 'transport_or_format_error' in issues:raise CheckStopped
        return body

    try:
        first=send('English products','Show me laptops','Shopping','en',coverage=True)
        original_ids=[r['id'] for r in records(first)]
        second=send('AZ product parity','Noutbukları göstər','Shopping','az',coverage=True,
            verify=lambda b:[] if [r['id'] for r in records(b)]==original_ids else ['language_search_ids_differ'])
        current_ids=[r['id'] for r in records(second)]
        if current_ids:
            cheapest_id=min(records(second),key=lambda r:Decimal(str(r['price_azn'])))['id']
            send('Cheapest details','Ən ucuzunun detalları','Shopping','az',verify=lambda b:[] if len(records(b))==1 and records(b)[0]['id']==cheapest_id else ['wrong_detail_reference'])
            send('Comparison','İlk ikisini müqayisə et',domain='Shopping' if len(current_ids)>=2 else None,
                no_cards=len(current_ids)<2,verify=lambda b:[] if len(current_ids)<2 or [r['id'] for r in records(b)]==current_ids[:2] else ['wrong_comparison_ids'])
        send('Russian products','Покажи ноутбуки','Shopping','ru',coverage=True)
        send('Cinema topic switch','What movies are on tomorrow','Cinema','en',coverage=True)
        send('Restore product context','Əvvəlki məhsulları göstər','Shopping','az',coverage=True,
            verify=lambda b:[] if [r['id'] for r in records(b)]==original_ids else ['product_context_lost'])
        flight=send('Exact-date flight',f'Flights from Baku to Istanbul on {flight_date.isoformat()}','Flights','en',coverage=True,
            verify=lambda b:[] if all(r.get('departure_local','').startswith(flight_date.isoformat()) for r in records(b)) else ['wrong_flight_date'])
        flight_ids=[r['id'] for r in records(flight) if r.get('departure_local')]
        if flight_ids:
            send('Baggage reference','Ən ucuzunun baqajı neçə kilodur?','Flights','az',
                verify=lambda b:[] if all(r.get('baggage_kg') is None for r in records(b)) or status.get('flight_api',{}).get('supports_baggage') else ['invented_baggage_allowance'])
        send('Month flight calendar',f'Show flights from Baku to Istanbul in {month_name}','Flights','en',coverage=True,
            verify=lambda b:[] if all(r.get('departure_local','').startswith(month) for r in records(b)) else ['wrong_flight_month'])
        send('Russian small talk','kaqdela',language='ru',no_cards=True)
        send('Mixed domains',f'Find laptops and flights from Baku to Istanbul on {flight_date.isoformat()}',
            verify=lambda b:[] if {'Shopping','Flights'}.issubset({p.get('domain') for p in b.get('results',[])}) else ['mixed_domain_missing'])
        send('Unrelated input','asdfgh qwerty',no_cards=True,verify=lambda b:[] if b.get('category') in ('Ambiguous','Out of scope') else ['unrelated_input_routing'])
        send('Out of scope','Explain quantum physics',no_cards=True,verify=lambda b:[] if b.get('category')=='Out of scope' else ['out_of_scope_routing'])
    except CheckStopped:report['stopped_early']=True
    times=[r['seconds'] for r in report['turns']]
    report['summary']={'completed':len(times),'passed':sum(r['verdict']=='PASS' for r in report['turns']),
        'warnings':sum(r['verdict']=='WARN' for r in report['turns']), 'failed':sum(r['verdict']=='FAIL' for r in report['turns']),
        'flight_configured':bool(status.get('flight_api',{}).get('configured')),'stopped_early':report['stopped_early'],
        'median_seconds':round(statistics.median(times),2),'max_seconds':max(times)}
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8001')
    parser.add_argument('--run-live',action='store_true')
    parser.add_argument('--date',default=(datetime.now(timezone(timedelta(hours=4))).date()+timedelta(days=17)).isoformat())
    parser.add_argument('--output',type=Path,default=Path('phase2_live_results.json'))
    args=parser.parse_args();base=args.url.rstrip('/');url=urlsplit(base)
    if url.scheme not in ('http','https') or url.hostname not in ('localhost','127.0.0.1','::1') or url.path or url.query or url.fragment or url.username or url.password:
        parser.error('--url must be a local server origin.')
    try:
        day=date.fromisoformat(args.date)
        if not args.run_live:
            health=request_json(base,'/health')
            print('Phase:',health.get('phase'),'| Gemini configured:',health.get('llm_configured'))
            print('Health only. Add --run-live to send up to 14 messages; Gemini/provider quotas may be used.')
            return 0 if health.get('phase')==2 else 2
        report=run_checks(base,day)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(str(exc) if isinstance(exc,ValueError) else 'Cannot reach/check the local server. Start python run_live.py first.')
        return 2
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary'],indent=2))
    print('Saved:',args.output)
    print('WARN means missing data coverage. Manually review facts, source links and clarification quality; PASS is not a completion certificate.')
    return 0 if not report['summary']['failed'] and not report['summary']['warnings'] and report['summary']['flight_configured'] else 1


if __name__=='__main__':raise SystemExit(main())
