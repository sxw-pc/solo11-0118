import argparse
import json
import tempfile
from pathlib import Path
from .domain import found_import,custody_import,claim_import,bag_reconcile,claim_adjudicate
from .report import report,report_json

COMMANDS={"found-import":found_import,"custody-import":custody_import,"claim-import":claim_import,
          "bag-reconcile":bag_reconcile,"claim-adjudicate":claim_adjudicate}

def demo(workdir=None):
    root=Path(workdir or tempfile.mkdtemp(prefix="lostproperty-"))
    root.mkdir(parents=True,exist_ok=True)
    db=root/"data.db"
    fixtures=Path.cwd()/"fixtures"
    results={}
    for name in ("found-import","custody-import","claim-import"):
        func=COMMANDS[name]
        results[name]=func(db,fixtures/(name+"_normal.json"))
    return {"db":str(db),"results":results}

def decision_demo(workdir=None):
    root=Path(workdir or tempfile.mkdtemp(prefix="lostproperty-decisions-"));root.mkdir(parents=True,exist_ok=True)
    db=root/"data.db";f=Path.cwd()/"fixtures"
    found_import(db,f/"found-import_normal.json");custody_import(db,f/"custody-import_normal.json")
    custody_import(db,f/"custody-import_conflict.json")
    claim_import(db,f/"claim-import_normal.json")
    held=report(db)["claim_case"][0]["status"]
    bag_reconcile(db,f/"bag-reconcile_normal.json")
    returned=report(db)["claim_case"][0]["status"]
    return {"db":str(db),"before_reconciliation":held,"after_reconciliation":returned}

def main():
    parser=argparse.ArgumentParser()
    subs=parser.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        sub=subs.add_parser(name);sub.add_argument("--db",required=True);sub.add_argument("--file",required=True)
    d=subs.add_parser("demo");d.add_argument("--workdir")
    dd=subs.add_parser("demo-decisions");dd.add_argument("--workdir")
    r=subs.add_parser("report");r.add_argument("--db",required=True)
    args=parser.parse_args()
    if args.command=="demo":print(json.dumps(demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-decisions":print(json.dumps(decision_demo(args.workdir),ensure_ascii=False))
    elif args.command=="report":print(report_json(args.db))
    else:print(json.dumps(COMMANDS[args.command](args.db,args.file),ensure_ascii=False))
if __name__=="__main__":main()
