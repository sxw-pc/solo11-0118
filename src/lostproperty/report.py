import json
from .store import connect,TABLES

def _review(db,item_id):
    review=db.execute("SELECT * FROM source_review WHERE item_id=? ORDER BY revision DESC LIMIT 1",(item_id,)).fetchone()
    if not review:return None
    candidates=[dict(r) for r in db.execute("SELECT * FROM source_review_candidate WHERE review_id=? ORDER BY trip_id",
                                            (review["id"],))]
    evidence=[dict(r) for r in db.execute("SELECT * FROM source_review_evidence WHERE review_id=? ORDER BY evidence_kind,evidence_id",
                                          (review["id"],))]
    contacts=[dict(r) for r in db.execute("""
        SELECT b.trip_id AS trip,b.token,b.holder_hint AS holder,
               (SELECT COUNT(*) FROM contact_event e WHERE e.item_id=? AND e.token=b.token) AS contact_count
        FROM boarding_record b JOIN source_review_candidate c
          ON c.trip_id=b.trip_id AND c.review_id=?
        WHERE b.seat=(SELECT seat FROM found_item WHERE id=?) AND c.status='possible'
        ORDER BY b.trip_id,b.token
    """,(item_id,review["id"],item_id))]
    corrections=[dict(r) for r in db.execute("SELECT * FROM contact_correction WHERE item_id=? AND review_id=? ORDER BY token",
                                             (item_id,review["id"]))]
    return {"revision":review["revision"],"procedure":review["procedure_id"],
            "candidates":candidates,"evidence":evidence,"contacts":contacts,"corrections":corrections}

def report(path):
    db=connect(path)
    try:
        data={name:[dict(row) for row in db.execute("SELECT * FROM "+name)] for name in TABLES}
        data["item_chains"]=[dict(row) for row in db.execute("""
            SELECT i.id AS item,i.trip_id,i.seat,i.status AS item_status,t.route,t.vehicle,
                   b.id AS bag,b.seal,b.custodian,b.status AS custody_status,
                   c.id AS claim,c.claimant,c.status AS claim_status,c.reason AS claim_reason,
                   s.sha AS found_source_sha
            FROM found_item i JOIN service_trip t ON t.id=i.trip_id
            JOIN source_blob s ON s.id=i.source_id
            LEFT JOIN bag_item bi ON bi.item_id=i.id LEFT JOIN custody_bag b ON b.id=bi.bag_id
            LEFT JOIN claim_case c ON c.item_id=i.id ORDER BY i.id,c.id
        """)]
        # One row per item: the current source review, recalculated against
        # boarding_record so it always matches the stored evidence rather than
        # a stale note on the original found_item row.
        data["source_reviews"]=[]
        for row in db.execute("SELECT id FROM found_item ORDER BY id"):
            review=_review(db,row["id"])
            if not review:continue
            item=db.execute("SELECT * FROM found_item WHERE id=?",(row["id"],)).fetchone()
            trip=db.execute("SELECT * FROM service_trip WHERE id=?",(item["trip_id"],)).fetchone()
            data["source_reviews"].append({"item":row["id"],"vehicle":trip["vehicle"] if trip else None,"seat":item["seat"],
                                           "claimed_trip":item["trip_id"],**review})
        return data
    finally:db.close()

def report_json(path):
    return json.dumps(report(path),ensure_ascii=False,indent=2,default=lambda x:x.hex() if isinstance(x,bytes) else str(x))
