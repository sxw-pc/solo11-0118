import json
from .store import connect,TABLES

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
        return data
    finally:db.close()

def report_json(path):
    return json.dumps(report(path),ensure_ascii=False,indent=2,default=lambda x:x.hex() if isinstance(x,bytes) else str(x))
