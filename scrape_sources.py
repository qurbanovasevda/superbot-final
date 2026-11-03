"""Collect a bounded read-only Phase 2 snapshot; does not switch the main chat."""
import argparse
import json
from pathlib import Path
from app.live_sources import collect, save_snapshot, SNAPSHOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--per-category',type=int,choices=range(1,201),default=150)
    parser.add_argument('--pages',type=int,choices=range(1,13),default=7)
    parser.add_argument('--cinemas',type=int,choices=range(1,21),default=20)
    parser.add_argument('--output',type=Path,default=SNAPSHOT)
    args=parser.parse_args()
    print('Collecting Birmarket and CompZone products and dated CinemaPlus/CineMastercard showtimes. No Gemini key is used.',flush=True)
    data=collect(per_category=args.per_category,pages=args.pages,cinemas=args.cinemas,progress=lambda message:print(message,flush=True))
    data=save_snapshot(data,args.output)
    print(json.dumps({'products':len(data['products']),'showtimes':len(data['showtimes']),'sources':data['sources']},ensure_ascii=False,indent=2))
    print('Saved:',args.output)
    print('Start Phase 2 with python run_live.py on port 8001. Flight API requests are separate from this scraping command.')
    return 0 if any(s['status']=='ok' for s in data['sources'].values()) else 1


if __name__=='__main__':raise SystemExit(main())
