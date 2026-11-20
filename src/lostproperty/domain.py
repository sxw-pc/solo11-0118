import hashlib
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
            # The clearing-proof procedure in force for the route is pinned when
            # the find is first ingested. A procedure registered later never
            # applies retroactively to this item.
            procedure=_pinned_procedure(db,trip) if trip else None
            values=(item["trip"],item["seat"],item["found_at"],features,status)
            old=db.execute("SELECT * FROM found_item WHERE id=?",(item["id"],)).fetchone()
            if old and tuple(old[k] for k in ("trip_id","seat","found_at","features","status"))!=values:
                raise ValueError("found_item_identity_conflict")
            if not old:
                db.execute("INSERT INTO found_item(id,trip_id,seat,found_at,features,status,source_id,procedure_id) VALUES (?,?,?,?,?,?,?,?)",
                           (item["id"],*values,source,procedure["id"] if procedure else None));result["items"]+=1
            result["held"]+=status=="held"
        _refresh_reviews(db,None,source)
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


# ---------------------------------------------------------------------------
# Lost-property source review: which trip's riders should be visited?
#
# This never decides who owns an item or returns it. It only narrows, per
# physical (vehicle, seat), which adjacent trips remain possible sources and
# lists the boarding_record holders that staff should actually contact.
# ---------------------------------------------------------------------------

# The clearing-proof procedures this station recognises. A route enables one;
# the method string below is what an observation must cite to be usable.
CABIN_CAMERA="cabin_camera_continuous"          # continuous approved on-board video
DUAL_ATTESTED="dual_attested_cleaning"          # two cleaners physically check the seat
METHODS={CABIN_CAMERA:{"supervisor_only":True,"min_witnesses":0},
         DUAL_ATTESTED:{"supervisor_only":False,"min_witnesses":2}}


def _sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pinned_procedure(db,trip):
    """The procedure in force for the trip's route when the find is ingested.

    A station switches procedures by registering another one for the route;
    the most recent registration before intake is the one in force. It is
    pinned onto the found item so later registrations never apply backwards.
    """
    if not trip:return None
    return db.execute("SELECT * FROM clearing_procedure WHERE route=? ORDER BY rowid DESC,id DESC LIMIT 1",
                      (trip["route"],)).fetchone()


def _window_end(trip):
    """Latest instant a trip could still have carried the item.

    While the find is inside the trip's end-plus-two-hour intake window, the
    trip remains a plausible source. Clearing evidence and item-entry records
    pull a trip out earlier; nothing pushes it later.
    """
    return _time(trip["end_at"])+timedelta(hours=2)


def _segments_cover(segments,start,end):
    """True only if the union of continuous spans covers start..end with no gap.

    Interrupted footage/cleaning is represented as several spans or a span
    narrower than the claimed interval; either fails this check, so an
    interrupted observation can never be stretched into "empty throughout".
    """
    spans=sorted((_time(a),_time(b)) for a,b in segments if _time(a)<_time(b))
    cursor=start
    for a,b in spans:
        if b<cursor:continue
        if a>cursor:return False
        cursor=max(cursor,b)
        if cursor>=end:return True
    return cursor>=end


def clearing_register(path,file):
    """Register the clearing-proof procedure a station route uses."""
    def handler(db,data,source):
        result={"procedures":0}
        for p in data["procedures"]:
            old=db.execute("SELECT * FROM clearing_procedure WHERE id=?",(p["id"],)).fetchone()
            values=(p["route"],p["method"],p["approved_by"],p["activated_at"])
            if p["method"] not in METHODS:raise ValueError("unknown_clearing_method")
            if not p["approved_by"].strip() or not p["activated_at"].strip():raise ValueError("clearing_procedure_unapproved")
            if old:
                if tuple(old[k] for k in ("route","method","approved_by","activated_at"))!=values:
                    raise ValueError("clearing_procedure_identity_conflict")
                continue
            if db.execute("SELECT 1 FROM clearing_procedure WHERE route=? AND (id=? OR method=?)",
                          (p["route"],p["id"],p["method"])).fetchone():
                raise ValueError("clearing_procedure_identity_conflict")
            db.execute("INSERT INTO clearing_procedure VALUES (?,?,?,?,?,?)",(p["id"],*values,source));result["procedures"]+=1
        return result
    return ingest(path,"clearing",file,handler)


def clear_observe_import(path,file):
    """Record empty-seat observations, or an item's entry into a seat."""
    def handler(db,data,source):
        result={"observations":0,"entries":0}
        for o in data.get("observations",[]):
            old=db.execute("SELECT * FROM clearing_observation WHERE id=?",(o["id"],)).fetchone()
            if o["method"] not in METHODS:raise ValueError("unknown_clearing_method")
            segments=sorted([[_time(a).isoformat(),_time(b).isoformat()] for a,b in o["segments"]])
            if not segments:raise ValueError("clearing_observation_incomplete")
            start,end=_time(o["observed_from"]),_time(o["observed_to"])
            if start>=end:raise ValueError("clearing_observation_incomplete")
            result_value=o.get("result","empty")
            if result_value not in ("empty","occupied"):raise ValueError("invalid_clearing_result")
            witnesses=sorted(set(o.get("witnesses",[])))
            if not (o.get("device","") or "").strip() and not witnesses:
                raise ValueError("clearing_observation_unattributed")
            # "empty" is only a *record*; whether it is usable as proof is
            # decided in the review against the pinned procedure.
            complete=_segments_cover(segments,start,end)
            digest=_sha256(json.dumps({"id":o["id"],"vehicle":o["vehicle"],"seat":o["seat"],"route":o["route"],
                                       "method":o["method"],"observed_from":o["observed_from"],"observed_to":o["observed_to"],
                                       "segments":segments,"device":o.get("device","") or "","witnesses":witnesses,
                                       "attested_by":o.get("attested_by","") or "","result":result_value},sort_keys=True))
            values=(o["vehicle"],o["seat"],o["route"],o["method"],o["observed_from"],o["observed_to"],
                    json.dumps(segments),o.get("device","") or "",json.dumps(witnesses),
                    o.get("attested_by","") or "",digest,result_value)
            if old:
                prior=(old["vehicle"],old["seat"],old["route"],old["method"],old["observed_from"],old["observed_to"],
                       old["device"],old["witnesses"],old["attested_by"],old["digest"],old["result"])
                current=(o["vehicle"],o["seat"],o["route"],o["method"],o["observed_from"],o["observed_to"],
                         o.get("device","") or "",json.dumps(witnesses),o.get("attested_by","") or "",digest,result_value)
                if prior!=current or json.loads(old["segments"])!=segments:
                    raise ValueError("clearing_observation_identity_conflict")
                continue
            db.execute("INSERT INTO clearing_observation(id,vehicle,seat,route,method,observed_from,observed_to,segments,"
                       "device,witnesses,attested_by,digest,result,source_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (o["id"],*values,source))
            if not complete:
                # Keep the incomplete record; it is visible but unusable.
                result.setdefault("incomplete",0);result["incomplete"]+=1
            result["observations"]+=1
        for e in data.get("entries",[]):
            old=db.execute("SELECT * FROM item_entry WHERE id=?",(e["id"],)).fetchone()
            values=(e["item"],e["trip"],e["vehicle"],e["seat"],e["at"],e["observer"],e.get("device","") or "")
            digest=_sha256(json.dumps({"id":e["id"],"item":e["item"],"trip":e["trip"],"at":e["at"]},sort_keys=True))
            if old:
                if tuple(old[k] for k in ("item_id","trip_id","vehicle","seat","at","observer","device"))!=values or old["digest"]!=digest:
                    raise ValueError("item_entry_identity_conflict")
                continue
            db.execute("INSERT INTO item_entry VALUES (?,?,?,?,?,?,?,?,?,?)",(e["id"],*values,digest,source))
            result["entries"]+=1
        _refresh_reviews(db,None,source)
        return result
    return ingest(path,"clearing",file,handler)


def contact_import(path,file):
    """Record staff having contacted riders; later visits become corrections."""
    def handler(db,data,source):
        result={"contacts":0}
        for c in data["contacts"]:
            old=db.execute("SELECT * FROM contact_event WHERE id=?",(c["id"],)).fetchone()
            values=(c["item"],c["trip"],c["token"],c["holder"],c["contacted_at"],c.get("staff",""),c.get("channel",""))
            if old:
                if tuple(old[k] for k in ("item_id","trip_id","token","holder","contacted_at","staff","channel"))!=values:
                    raise ValueError("contact_identity_conflict")
                continue
            boarding=db.execute("SELECT * FROM boarding_record WHERE token=?",(c["token"],)).fetchone()
            if not boarding or boarding["trip_id"]!=c["trip"] or boarding["holder_hint"]!=c["holder"]:
                raise ValueError("contact_boarding_mismatch")
            db.execute("INSERT INTO contact_event VALUES (?,?,?,?,?,?,?,?,?)",(c["id"],*values,source));result["contacts"]+=1
        _refresh_reviews(db,None,source)
        return result
    return ingest(path,"contact",file,handler)


def review_import(path,file):
    """Recompute source review after late evidence, allowing re-import."""
    def handler(db,data,source):
        result={"items":0}
        for item_id in data["items"]:
            if not db.execute("SELECT 1 FROM found_item WHERE id=?",(item_id,)).fetchone():
                raise ValueError("unknown_item")
            _refresh_reviews(db,item_id,source);result["items"]+=1
        return result
    return ingest(path,"review",file,handler)


# -- review computation -----------------------------------------------------

def _usable_observation(o,procedure):
    """Why an observation can/cannot cut a trip under the pinned procedure.

    Returns (applicable, reason). The method must be the one the route pinned,
    an "empty" record must be gapless, and the procedure's attestation rules
    (supervisor sign-off for video, two distinct witnesses for manual) apply.
    """
    if not procedure:return False,"no_procedure_pinned"
    if o["method"]!=procedure["method"]:return False,"method_not_in_force"
    if o["result"]!="empty":return False,"observation_not_empty"
    segments=json.loads(o["segments"])
    if not _segments_cover(segments,_time(o["observed_from"]),_time(o["observed_to"])):
        return False,"observation_gap"
    witnesses=json.loads(o["witnesses"])
    if len(witnesses)<METHODS[procedure["method"]]["min_witnesses"]:return False,"witnesses_insufficient"
    if METHODS[procedure["method"]]["supervisor_only"] and not o["attested_by"].strip():
        return False,"attestation_required"
    return True,""


def _candidate_trips(db,vehicle,found):
    """Trips on the same vehicle that could still have carried the item.

    A trip is plausible while the find falls inside its end-plus-two-hour intake
    window (the same window the intake check uses). Nothing else narrows the
    set here: with no trustworthy clearing and no item-entry record every
    in-window trip stays a candidate.
    """
    rows=db.execute("SELECT * FROM service_trip WHERE vehicle=? ORDER BY end_at,id",(vehicle,)).fetchall()
    return [r for r in rows if _time(r["end_at"])<=found<=_window_end(r)]


def _compute_review(db,item_id,source_id):
    item=db.execute("SELECT * FROM found_item WHERE id=?",(item_id,)).fetchone()
    if not item:raise ValueError("unknown_item")
    claimed=db.execute("SELECT * FROM service_trip WHERE id=?",(item["trip_id"],)).fetchone()
    if not claimed:return None
    vehicle=claimed["vehicle"];seat=item["seat"];found=_time(item["found_at"])
    procedure=db.execute("SELECT * FROM clearing_procedure WHERE id=?",(item["procedure_id"],)).fetchone() if item["procedure_id"] else None

    trips=_candidate_trips(db,vehicle,found)
    observations=[r for r in db.execute("SELECT * FROM clearing_observation WHERE vehicle=? AND seat=? ORDER BY observed_from,id",
                                         (vehicle,seat))]
    entries=[r for r in db.execute("SELECT * FROM item_entry WHERE item_id=?",(item_id,))]

    candidates={};evidence=[];usable=[]
    for t in trips:candidates[t["id"]]={"trip":t,"status":"possible","reason":"no_clearing_or_entry_record"}

    # Only a clearing observation that the route's pinned procedure accepts can
    # cut anything. Everything else is kept as evidence with the reason it was
    # not applied, so the report can show why a trip was or was not excluded.
    for o in observations:
        ok,reason=_usable_observation(o,procedure)
        if ok and o["route"] and claimed["route"] and o["route"]!=claimed["route"]:
            ok,reason=False,"route_mismatch"
        if ok:usable.append(o)
        evidence.append(("clearing_observation",o["id"],int(ok),reason))

    # Which accepted cleanings are themselves contradicted by another record?
    # A cleaning that overlaps an item entry, or overlaps an observation that
    # saw the seat occupied, cannot be relied on to cut anything.
    conflicts={}
    for o in usable:
        s,e=_time(o["observed_from"]),_time(o["observed_to"])
        if any(_time(en["at"])>=s and _time(en["at"])<=e and en["seat"]==seat and en["vehicle"]==vehicle for en in entries):
            conflicts[o["id"]]="item_entry"
        elif any(o2["id"]!=o["id"] and o2["result"]=="occupied" and o2["seat"]==seat and o2["vehicle"]==vehicle
                 and _time(o2["observed_from"])<e and _time(o2["observed_to"])>s for o2 in observations):
            conflicts[o["id"]]="occupied_observation"

    for t in trips:
        end=_time(t["end_at"])
        # Only a complete cleaning starting at/after this trip ended and ending
        # by the find can prove the seat was emptied after the trip. A
        # conflicting cleaning still names the reason the trip was not cut.
        cuts=[o for o in usable if _time(o["observed_from"])>=end and _time(o["observed_to"])<=found]
        clean=[o for o in cuts if o["id"] not in conflicts]
        if clean:
            candidates[t["id"]]={"trip":t,"status":"excluded","kind":"clearing",
                                 "evidence":clean[0]["id"],"reason":"seat_cleared_after_trip"}
            continue
        # An item-entry record placing the item into this exact seat after the
        # trip ended is equally direct evidence the trip was not the source.
        # The entry's own trip identifies when it entered; any candidate trip
        # that had already ended by then is cut.
        ent=[e for e in entries
             if e["seat"]==seat and e["vehicle"]==vehicle
             and _time(e["at"])>=end and _time(e["at"])<=found]
        if ent:
            candidates[t["id"]]={"trip":t,"status":"excluded","kind":"item_entry",
                                 "evidence":ent[0]["id"],"reason":"item_entered_after_trip"}
            continue
        if cuts:
            candidates[t["id"]]={"trip":t,"status":"possible",
                                 "reason":"conflicting_evidence:"+conflicts[cuts[0]["id"]]}

    for oid,clash in conflicts.items():
        evidence.append(("conflict",oid,0,clash))

    # Contact list: one row per boarding_record at this seat on each possible
    # trip. Different people sharing a seat number on different trips are kept
    # apart by token; the same token is never merged across trips either.
    contacts=[]
    for cid,c in candidates.items():
        if c["status"]!="possible":continue
        rows=db.execute("SELECT * FROM boarding_record WHERE trip_id=? AND seat=? ORDER BY token",(cid,seat)).fetchall()
        if not rows:c["reason"]+=";no_boarding_record"
        for r in rows:contacts.append({"trip":cid,"token":r["token"],"holder":r["holder_hint"]})

    return {"item":item,"vehicle":vehicle,"seat":seat,"procedure":procedure,
            "candidates":candidates,"contacts":contacts,"evidence":evidence,"found":found}


def _refresh_reviews(db,item_id,source_id):
    ids=[item_id] if item_id else [r["id"] for r in db.execute("SELECT id FROM found_item ORDER BY id")]
    for iid in ids:
        if iid is None:continue
        r=_compute_review(db,iid,source_id)
        if not r:continue
        procedure=r["procedure"]["id"] if r["procedure"] else None
        payload={"procedure":procedure,
                 "candidates":{k:(v["status"],v.get("kind"),v.get("evidence"),v["reason"]) for k,v in r["candidates"].items()},
                 "contacts":sorted((c["trip"],c["token"],c["holder"]) for c in r["contacts"]),
                 # Evidence that could not narrow the set still belongs in the
                 # audit trail, so why-it-did-not-apply is part of the state.
                 "evidence":sorted((k,e,int(ok),reason) for k,e,ok,reason in r["evidence"])}
        signature=_sha256(json.dumps(payload,sort_keys=True))
        last=db.execute("SELECT * FROM source_review WHERE item_id=? ORDER BY revision DESC LIMIT 1",(iid,)).fetchone()
        revision=1
        if last:
            if last["signature"]==signature:continue
            revision=last["revision"]+1
        review_id=db.execute("INSERT INTO source_review(item_id,revision,procedure_id,signature,source_id) VALUES (?,?,?,?,?)",
                             (iid,revision,procedure,signature,source_id)).lastrowid
        for cid,c in r["candidates"].items():
            db.execute("INSERT INTO source_review_candidate VALUES (?,?,?,?,?,?)",
                       (review_id,cid,c["status"],c.get("kind"),c.get("evidence"),c["reason"]))
        for kind,eid,ok,reason in r["evidence"]:
            db.execute("INSERT OR REPLACE INTO source_review_evidence VALUES (?,?,?,?,?)",(review_id,kind,eid,ok,reason))
        _revised_contacts(db,review_id,r["contacts"],last["id"] if last else None)


def _revised_contacts(db,review_id,contacts,prior_review_id):
    """Note which contact-list entries a new revision added or removed.

    A contacted person is never deleted: contact_event keeps the history and
    the correction rows say how the revised list differs from the previous
    one. This runs only when a genuinely new revision is written, so repeated
    identical imports do not manufacture corrections.
    """
    if prior_review_id is None:return
    prior={(r["token"],r["trip_id"]) for r in db.execute(
        "SELECT b.token,c.trip_id FROM source_review_candidate c JOIN boarding_record b ON b.trip_id=c.trip_id "
        "JOIN found_item i ON i.id=? WHERE c.review_id=? AND c.status='possible' AND b.seat=i.seat",
        (db.execute("SELECT item_id FROM source_review WHERE id=?",(review_id,)).fetchone()[0],prior_review_id))}
    current={(c["token"],c["trip"]) for c in contacts}
    item_id=db.execute("SELECT item_id FROM source_review WHERE id=?",(review_id,)).fetchone()[0]
    for token,trip in sorted(current-prior):
        holder=next(c["holder"] for c in contacts if c["token"]==token and c["trip"]==trip)
        db.execute("INSERT OR IGNORE INTO contact_correction(item_id,review_id,trip_id,token,holder,note) VALUES (?,?,?,?,?,?)",
                   (item_id,review_id,trip,token,holder,"added_by_revision"))
    for token,trip in sorted(prior-current):
        boarding=db.execute("SELECT holder_hint FROM boarding_record WHERE token=?",(token,)).fetchone()
        db.execute("INSERT OR IGNORE INTO contact_correction(item_id,review_id,trip_id,token,holder,note) VALUES (?,?,?,?,?,?)",
                   (item_id,review_id,trip,token,boarding["holder_hint"] if boarding else "","removed_by_revision"))
