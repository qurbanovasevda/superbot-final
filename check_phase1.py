"""Read-only conversation smoke checks against an already running local server."""
import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# These are development acceptance scenarios, not a held-out quality benchmark.
SCENARIOS = [
    [('Flights from Baku to Istanbul on Friday', ['Flights']),
     ('What is playing tonight?', ['Cinema']),
     ('Headphones under 100 AZN', ['Shopping'])],
    [('What is playing tonight?', ['Cinema']), ('Show me laptops.', ['Shopping']),
     ('Okay, and the sci-fi one, any seats on Saturday?', ['Follow-up','Ambiguous','Cinema'])],
    [('Show me laptops.', ['Shopping']), ('The second one, is it in stock?', ['Follow-up'])],
    [('Flights to Istanbul on 6 November.', ['Flights']), ('The cheaper one, is it direct?', ['Follow-up'])],
    [('I need a flight to Istanbul on 6 November and a backpack for the trip.', ['Mixed'])],
    [('Show me something good.', ['Ambiguous'])],
    [('What is the weather in Ganja?', ['Out of scope'])],
    [('Book two seats.', ['Ambiguous'])],
]


def request_json(base, path, data=None):
    payload=None if data is None else json.dumps(data).encode('utf-8')
    req=Request(base+path, data=payload, headers={'Content-Type':'application/json'})
    with urlopen(req, timeout=180) as response:
        return json.load(response)


def check_response(body, expected):
    """Separate routing labels from minimal behavior; never certify answer quality."""
    problems=[]
    if body.get('error'):problems.append('backend_error')
    if not isinstance(body.get('message'),str) or not body['message'].strip():
        problems.append('empty_message')
    parts=body.get('results',[])
    if not isinstance(parts,list):
        return False, ['invalid_results']
    if expected==['Mixed']:
        domains={p.get('domain') for p in parts if isinstance(p,dict)}
        if not {'Flights','Shopping'}.issubset(domains):problems.append('missing_mixed_domain')
    elif expected in (['Flights'],['Shopping'],['Cinema']):
        if not any(isinstance(p,dict) and p.get('items') for p in parts):
            problems.append('missing_search_results')
    elif expected in (['Ambiguous'],['Out of scope']):
        if any(isinstance(p,dict) and p.get('items') for p in parts):
            problems.append('unexpected_records')
    if body.get('category') not in expected:problems.append('routing_label')
    return not problems, problems


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--run-live',action='store_true',help='Send 14 test messages; uses Gemini quota in llm mode.')
    parser.add_argument('--output',type=Path,default=Path('phase1_live_results.json'))
    args=parser.parse_args()
    base=args.url.rstrip('/')
    address=urlparse(base)
    if address.scheme not in ('http','https') or address.hostname not in ('127.0.0.1','localhost','::1') or address.path or address.query or address.fragment or address.username or address.password:
        parser.error('--url must be a local server origin, such as http://127.0.0.1:8000')
    try:
        health=request_json(base,'/health')
    except (OSError,URLError,ValueError):
        print('Cannot reach the local server. Start python run.py and try again.')
        return 2
    print('Server mode:',health.get('chat_mode'),'| Gemini configured:',health.get('llm_configured'))
    if not args.run_live:
        print('Health only. Add --run-live to run 14 messages in 8 fresh sessions.')
        return 0
    if health.get('chat_mode')=='llm' and not health.get('llm_configured'):
        print('Gemini key is not configured on the server. No test messages sent.')
        return 2
    report={'created_at':datetime.now(timezone.utc).isoformat(),
            'chat_mode':health.get('chat_mode'),'reference_date':health.get('reference_date'),
            'scope':'Development smoke checks; manual semantic review still required.', 'turns':[]}
    stopped=False
    for scenario, turns in enumerate(SCENARIOS,1):
        try:
            session=request_json(base,'/api/sessions',{})['session_id']
        except (OSError,URLError,ValueError,KeyError):
            report['transport_error']='Cannot create session';stopped=True;break
        for message,expected in turns:
            start=time.perf_counter()
            try:
                body=request_json(base,f'/api/sessions/{session}/chat',{'message':message})
                if not isinstance(body,dict):raise ValueError('Invalid response')
                passed,issues=check_response(body,expected)
            except (OSError,URLError,ValueError,TypeError,AttributeError):
                body={};passed=False;issues=['transport_or_invalid_response']
            elapsed=round(time.perf_counter()-start,2)
            # Persist only known mock prompts and selected response fields, never
            # provider error bodies, keys, environment values or session identifiers.
            row={'scenario':scenario,'prompt':message,'expected_categories':expected,
                 'actual_category':body.get('category'),'seconds':elapsed,
                 'passed':passed,'issues':issues,
                 'record_ids':[i.get('id') for p in body.get('results',[]) if isinstance(p,dict)
                               for i in p.get('items',[]) if isinstance(i,dict)]}
            report['turns'].append(row)
            print(f"Scenario {scenario}: {'PASS' if passed else 'FAIL'} | {elapsed}s | {row['actual_category']}",flush=True)
            if body.get('error') or 'transport_or_invalid_response' in issues:
                stopped=True;break
        if stopped:break
    rows=report['turns']
    times=[r['seconds'] for r in rows]
    report['summary']={'planned_turns':14,'completed_turns':len(rows),
                       'passed_turns':sum(r['passed'] for r in rows),'stopped_early':stopped,
                       'median_seconds':round(statistics.median(times),2) if times else None,
                       'max_seconds':max(times) if times else None}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary'],indent=2))
    print('Saved:',args.output)
    print('Review factual correctness and clarification quality manually; PASS is only a smoke check.')
    return 0 if len(rows)==14 and all(r['passed'] for r in rows) else 1


if __name__=='__main__':
    raise SystemExit(main())
