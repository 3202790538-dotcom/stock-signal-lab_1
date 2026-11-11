"""Reproduce the public-price-only strategy study; never reads broker files."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'backend'))
from strategy import build_strategy_study


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-annual-return',type=float,default=50)
    parser.add_argument('--max-drawdown',type=float,default=30)
    args=parser.parse_args()
    # Select only public OHLC history; no holdings, balances or account metrics
    # are passed to the study or saved in its report.
    cached=json.loads((ROOT/'backend/data/dashboard.json').read_text())
    stocks=[{'symbol':s['symbol'],'history':s['history']} for s in cached['stocks']]
    study=build_strategy_study(stocks,args.target_annual_return,args.max_drawdown)
    study['generatedAt']=datetime.now(timezone.utc).isoformat()
    study['dataProvenance']={'source':cached.get('marketSource'),'marketAsOf':cached.get('marketAsOf'),
                             'publicHistorySha256':hashlib.sha256(json.dumps(stocks,sort_keys=True).encode()).hexdigest()}
    path=ROOT/'backend/data/strategy-study.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(study,ensure_ascii=False,allow_nan=False,indent=2))
    tmp.replace(path)
    keys=['startDate','endDate','totalReturnPct','annualReturnPct','maxDrawdownPct','closedTradeCount','meetsTargets']
    print(json.dumps({'parameters':study['parameters'],'trainingQualifiedCount':study['trainingQualifiedCount'],
                      'training':{k:study['training'].get(k) for k in keys},
                      'validation':{k:study['validation'].get(k) for k in keys},
                      'benchmark':{k:study['benchmark'].get(k) for k in keys[:5]},
                      'baseline':{k:study['baseline'].get(k) for k in keys[:6]},
                      'costStress':{k:study['costStress'].get(k) for k in keys[:6]},
                      'conclusion':study['conclusion'],'output':str(path)},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
