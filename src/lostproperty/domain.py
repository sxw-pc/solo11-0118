import json
from datetime import datetime,timedelta
from .intake import ingest

def _time(value):
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None:raise ValueError("timezone_required")
    return dt

def found_import(path,file):
    def handler(db,data,source):
        result={"trips":0,"items":0,"held":0}
        for t in data["trips"]:
            _time(t["end_at"])
            old=db.execute("SELECT * FROM service_trip WHERE id=?",(t["id"],)).fetchone()
            values=(t["route"],t["end_at"],t["vehicle"])
            if old and tuple(old[k] for k in ("route","end_at","vehicle"))!=values:raise ValueError("trip_identity_conflict")
            if not old:
                db.execute("INSERT INTO service_trip VALUES (?,?,?,?,?)",(t["id"],*values,source));result["trips"]+=1
        for b in data.get("boardings",[]):
            old=db.execute("SELECT * FROM boarding_record WHERE token=?",(b["token"],)).fetchone()
            values=(b["trip"],b["seat"],b["holder"])
            if old and tuple(old[k] for k in ("trip_id","seat","holder_hint"))!=values:raise ValueError("boarding_identity_conflict")
            if not old:db.execute("INSERT INTO boarding_record VALUES (?,?,?,?,?)",(b["token"],*values,source))
        for item in data["items"]:
            trip=db.execute("SELECT * FROM service_trip WHERE id=?",(item["trip"],)).fetchone()
            found=_time(item["found_at"])
            status="ready" if trip and _time(trip["end_at"])<=found<=_time(trip["end_at"])+timedelta(hours=2) and len(item["features"])>=2 else "held"
            features=json.dumps(sorted(set(item["features"])))
            values=(item["trip"],item["seat"],item["found_at"],features,status)
            old=db.execute("SELECT * FROM found_item WHERE id=?",(item["id"],)).fetchone()
            if old and tuple(old[k] for k in ("trip_id","seat","found_at","features","status"))!=values:
                raise ValueError("found_item_identity_conflict")
            if not old:
                db.execute("INSERT INTO found_item VALUES (?,?,?,?,?,?,?)",(item["id"],*values,source));result["items"]+=1
            result["held"]+=status=="held"
        return result
    return ingest(path,"found",file,handler)

def custody_import(path,file):
    def handler(db,data,source):
        result={"bags":0,"handoffs":0,"broken":0}
        for bag in data["bags"]:
            items=sorted(set(bag["items"]))
            if len(items)!=len(bag["items"]) or not items:raise ValueError("invalid_bag_members")
            old=db.execute("SELECT * FROM custody_bag WHERE id=?",(bag["id"],)).fetchone()
            if old:
                prior=[r["item_id"] for r in db.execute("SELECT item_id FROM bag_item WHERE bag_id=? ORDER BY item_id",(bag["id"],))]
                if old["seal"]!=bag["seal"] or prior!=items:raise ValueError("bag_identity_conflict")
                continue
            for item in items:
                row=db.execute("SELECT status FROM found_item WHERE id=?",(item,)).fetchone()
                if not row or row["status"]!="ready":raise ValueError("item_not_ready")
            db.execute("INSERT INTO custody_bag VALUES (?,?,?,?,?)",(bag["id"],bag["seal"],bag["custodian"],"sealed",source))
            db.executemany("INSERT INTO bag_item VALUES (?,?)",[(bag["id"],item) for item in items]);result["bags"]+=1
        for hand in data.get("handoffs",[]):
            old=db.execute("SELECT * FROM handoff_event WHERE id=?",(hand["id"],)).fetchone()
            if old:
                if tuple(old[k] for k in ("bag_id","sequence","from_holder","to_holder","seal"))!=(hand["bag"],hand["sequence"],hand["from"],hand["to"],hand["seal"]):
                    raise ValueError("handoff_identity_conflict")
                continue
            bag=db.execute("SELECT * FROM custody_bag WHERE id=?",(hand["bag"],)).fetchone()
            count=db.execute("SELECT COUNT(*) FROM handoff_event WHERE bag_id=?",(hand["bag"],)).fetchone()[0]
            valid=bool(bag and bag["status"]=="sealed" and hand["sequence"]==count+1 and hand["from"]==bag["custodian"] and hand["seal"]==bag["seal"])
            status="accepted" if valid else "broken"
            db.execute("INSERT INTO handoff_event VALUES (?,?,?,?,?,?,?,?)",
                       (hand["id"],hand["bag"],hand["sequence"],hand["from"],hand["to"],hand["seal"],status,source))
            if bag:
                db.execute("UPDATE custody_bag SET custodian=?,status=? WHERE id=?",
                           (hand["to"] if valid else bag["custodian"],"sealed" if valid else "broken",hand["bag"]))
            result["handoffs"]+=1;result["broken"]+=not valid
        return result
    return ingest(path,"custody",file,handler)

def claim_import(path,file):
    def handler(db,data,source):
        result={"claims":0,"returned":0,"contested":0,"held":0}
        for c in data["claims"]:
            old=db.execute("SELECT * FROM claim_case WHERE id=?",(c["id"],)).fetchone()
            features=sorted(set(c["features"]))
            if old:
                prior=[r["feature"] for r in db.execute("SELECT feature FROM claim_evidence WHERE claim_id=? ORDER BY feature",(c["id"],))]
                if (old["item_id"],old["claimant"],old["trip_id"],old["ticket_token"],prior)!=(c["item"],c["claimant"],c["trip"],c["ticket"],features):
                    raise ValueError("claim_identity_conflict")
                continue
            db.execute("INSERT INTO claim_case VALUES (?,?,?,?,?,?,?,?)",(c["id"],c["item"],c["claimant"],c["trip"],c["ticket"],"pending","",source))
            db.executemany("INSERT INTO claim_evidence VALUES (?,?)",[(c["id"],f) for f in features])
            result["claims"]+=1
        for item in {c["item"] for c in data["claims"]}:
            found=db.execute("SELECT * FROM found_item WHERE id=?",(item,)).fetchone()
            claims=db.execute("SELECT * FROM claim_case WHERE item_id=? ORDER BY id",(item,)).fetchall()
            bag=db.execute("SELECT b.* FROM custody_bag b JOIN bag_item bi ON bi.bag_id=b.id WHERE bi.item_id=?",(item,)).fetchone()
            competing=len({c["claimant"] for c in claims})>1
            for claim in claims:
                reason=("competing_claims" if competing else "custody_broken" if not bag or bag["status"]!="sealed" else
                        "evidence_mismatch" if not _claim_proven(db,claim,found) else "returned")
                status="contested" if competing else "returned" if reason=="returned" else "held"
                db.execute("UPDATE claim_case SET status=?,reason=? WHERE id=?",(status,reason,claim["id"]))
                if status=="returned":
                    db.execute("UPDATE found_item SET status='returned' WHERE id=?",(item,))
                    if not db.execute("SELECT 1 FROM return_event WHERE item_id=?",(item,)).fetchone():
                        db.execute("INSERT INTO return_event(item_id,claim_id,bag_id,source_id) VALUES (?,?,?,?)",
                                   (item,claim["id"],bag["id"],source))
                result[status]+=1
        return result
    return ingest(path,"claim",file,handler)

def _claim_proven(db,claim,found):
    if not found or claim["trip_id"]!=found["trip_id"]:return False
    proof={r["feature"] for r in db.execute("SELECT feature FROM claim_evidence WHERE claim_id=?",(claim["id"],))}
    expected=set(json.loads(found["features"]))
    boarding=db.execute("SELECT * FROM boarding_record WHERE token=?",(claim["ticket_token"],)).fetchone()
    return bool(expected<=proof and boarding and boarding["trip_id"]==found["trip_id"] and
                boarding["seat"]==found["seat"] and boarding["holder_hint"]==claim["claimant"])

def bag_reconcile(path,file):
    """Restore custody after a seal break only with two witnesses and full inventory."""
    def handler(db,data,source):
        result={"reconciled":0,"claims_recovered":0}
        for item in data["reconciliations"]:
            old=db.execute("SELECT * FROM bag_reconciliation WHERE id=?",(item["id"],)).fetchone()
            observed=sorted(set(item["items"]))
            values=(item["bag"],item["new_seal"],item["witness1"],item["witness2"],json.dumps(observed),item["reason"])
            if old:
                if tuple(old[k] for k in ("bag_id","new_seal","witness1","witness2","observed_items","reason"))!=values:
                    raise ValueError("reconciliation_identity_conflict")
                continue
            bag=db.execute("SELECT * FROM custody_bag WHERE id=?",(item["bag"],)).fetchone()
            expected=[r[0] for r in db.execute("SELECT item_id FROM bag_item WHERE bag_id=? ORDER BY item_id",(item["bag"],))]
            if (not bag or bag["status"]!="broken" or observed!=expected or not item["new_seal"] or
                item["witness1"]==item["witness2"] or not item["reason"].strip()):
                raise ValueError("reconciliation_evidence_insufficient")
            db.execute("INSERT INTO bag_reconciliation VALUES (?,?,?,?,?,?,?,?)",(item["id"],*values,source))
            db.execute("UPDATE custody_bag SET seal=?,status='sealed' WHERE id=?",(item["new_seal"],item["bag"]))
            result["reconciled"]+=1
            for found_id in expected:
                claims=db.execute("SELECT * FROM claim_case WHERE item_id=?",(found_id,)).fetchall()
                found=db.execute("SELECT * FROM found_item WHERE id=?",(found_id,)).fetchone()
                if len(claims)==1 and claims[0]["status"]=="held" and _claim_proven(db,claims[0],found):
                    db.execute("UPDATE claim_case SET status='returned',reason='custody_reconciled' WHERE id=?",(claims[0]["id"],))
                    db.execute("UPDATE found_item SET status='returned' WHERE id=?",(found_id,))
                    db.execute("INSERT INTO return_event(item_id,claim_id,bag_id,source_id) VALUES (?,?,?,?)",
                               (found_id,claims[0]["id"],item["bag"],source))
                    result["claims_recovered"]+=1
        return result
    return ingest(path,"reconcile",file,handler)

def claim_adjudicate(path,file):
    """Resolve competing claimants using a trip-seat boarding token and item evidence."""
    def handler(db,data,source):
        result={"returned":0}
        for item in data["decisions"]:
            old=db.execute("SELECT * FROM claim_adjudication WHERE id=?",(item["id"],)).fetchone()
            if old:
                if (old["item_id"],old["winner_claim"],old["reason"])!=(item["item"],item["winner"],item["reason"]):
                    raise ValueError("adjudication_identity_conflict")
                continue
            found=db.execute("SELECT * FROM found_item WHERE id=?",(item["item"],)).fetchone()
            claims=db.execute("SELECT * FROM claim_case WHERE item_id=?",(item["item"],)).fetchall()
            winner=next((c for c in claims if c["id"]==item["winner"]),None)
            bag=db.execute("SELECT b.* FROM custody_bag b JOIN bag_item bi ON bi.bag_id=b.id WHERE bi.item_id=?",
                           (item["item"],)).fetchone()
            if (len({c["claimant"] for c in claims})<2 or not winner or not bag or bag["status"]!="sealed" or
                not item["reason"].strip() or not _claim_proven(db,winner,found)):
                raise ValueError("claim_adjudication_unproven")
            if db.execute("SELECT 1 FROM return_event WHERE item_id=?",(item["item"],)).fetchone():
                raise ValueError("already_returned")
            db.execute("INSERT INTO claim_adjudication VALUES (?,?,?,?,?)",(item["id"],item["item"],item["winner"],item["reason"],source))
            for claim in claims:
                status="returned" if claim["id"]==winner["id"] else "rejected"
                db.execute("UPDATE claim_case SET status=?,reason=? WHERE id=?",
                           (status,"verified_boarding_claim" if status=="returned" else "competing_claim_lost",claim["id"]))
            db.execute("UPDATE found_item SET status='returned' WHERE id=?",(item["item"],))
            db.execute("INSERT INTO return_event(item_id,claim_id,bag_id,source_id) VALUES (?,?,?,?)",
                       (item["item"],winner["id"],bag["id"],source))
            result["returned"]+=1
        return result
    return ingest(path,"claim-adjudication",file,handler)
