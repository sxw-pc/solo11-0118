import argparse
import json
import tempfile
from pathlib import Path
from .domain import (found_import,custody_import,claim_import,bag_reconcile,claim_adjudicate,
                     clearing_register,clear_observe_import,contact_import,review_import)
from .report import report,report_json

COMMANDS={"found-import":found_import,"custody-import":custody_import,"claim-import":claim_import,
          "bag-reconcile":bag_reconcile,"claim-adjudicate":claim_adjudicate,
          "clearing-register":clearing_register,"clear-observe-import":clear_observe_import,
          "contact-import":contact_import,"review-import":review_import}

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

def source_demo(workdir=None):
    """Same find, two station procedures: the visit list differs."""
    root=Path(workdir or tempfile.mkdtemp(prefix="lostproperty-source-"));root.mkdir(parents=True,exist_ok=True)
    fixtures=Path.cwd()/"fixtures"/"scenarios"
    out={}
    for label,procedure in (("cabin_camera","route18_camera_procedure.json"),
                            ("dual_attested","route18_dual_procedure.json")):
        db=root/(label+".db")
        clearing_register(db,fixtures/procedure)
        found_import(db,fixtures/"reuse_r1_r2_found.json")
        clear_observe_import(db,fixtures/"clearance_camera_supervisor.json")
        row=report(db)["source_reviews"][0]
        out[label]={"procedure":row["procedure"],
                    "excluded":[c["trip_id"] for c in row["candidates"] if c["status"]=="excluded"],
                    "remaining":[c["trip_id"] for c in row["candidates"] if c["status"]=="possible"],
                    "contacts":[[c["trip"],c["holder"]] for c in row["contacts"]]}
    return {"db":str(root),"reviews":out}

def main():
    parser=argparse.ArgumentParser()
    subs=parser.add_subparsers(dest="command",required=True)
    for name in COMMANDS:
        sub=subs.add_parser(name);sub.add_argument("--db",required=True);sub.add_argument("--file",required=True)
    d=subs.add_parser("demo");d.add_argument("--workdir")
    dd=subs.add_parser("demo-decisions");dd.add_argument("--workdir")
    sd=subs.add_parser("demo-source-review");sd.add_argument("--workdir")
    r=subs.add_parser("report");r.add_argument("--db",required=True)
    args=parser.parse_args()
    if args.command=="demo":print(json.dumps(demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-decisions":print(json.dumps(decision_demo(args.workdir),ensure_ascii=False))
    elif args.command=="demo-source-review":print(json.dumps(source_demo(args.workdir),ensure_ascii=False))
    elif args.command=="report":print(report_json(args.db))
    else:print(json.dumps(COMMANDS[args.command](args.db,args.file),ensure_ascii=False))
if __name__=="__main__":main()
