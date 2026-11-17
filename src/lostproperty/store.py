import sqlite3
from pathlib import Path

SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source_blob(id INTEGER PRIMARY KEY,kind TEXT,sha TEXT,raw BLOB,result TEXT,UNIQUE(kind,sha));
CREATE TABLE IF NOT EXISTS source_failure(id INTEGER PRIMARY KEY,kind TEXT,sha TEXT,raw BLOB,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS service_trip(id TEXT PRIMARY KEY,route TEXT,end_at TEXT,vehicle TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS boarding_record(token TEXT PRIMARY KEY,trip_id TEXT REFERENCES service_trip(id),seat TEXT,holder_hint TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS found_item(id TEXT PRIMARY KEY,trip_id TEXT REFERENCES service_trip(id),seat TEXT,found_at TEXT,features TEXT,status TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS custody_bag(id TEXT PRIMARY KEY,seal TEXT,custodian TEXT,status TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS bag_item(bag_id TEXT REFERENCES custody_bag(id),item_id TEXT REFERENCES found_item(id),PRIMARY KEY(bag_id,item_id),UNIQUE(item_id));
CREATE TABLE IF NOT EXISTS handoff_event(id TEXT PRIMARY KEY,bag_id TEXT REFERENCES custody_bag(id),sequence INTEGER,from_holder TEXT,to_holder TEXT,seal TEXT,status TEXT,source_id INTEGER REFERENCES source_blob(id),UNIQUE(bag_id,sequence));
CREATE TABLE IF NOT EXISTS bag_reconciliation(id TEXT PRIMARY KEY,bag_id TEXT REFERENCES custody_bag(id),new_seal TEXT,witness1 TEXT,witness2 TEXT,observed_items TEXT,reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS claim_case(id TEXT PRIMARY KEY,item_id TEXT REFERENCES found_item(id),claimant TEXT,trip_id TEXT,ticket_token TEXT,status TEXT,reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS claim_evidence(claim_id TEXT REFERENCES claim_case(id),feature TEXT,PRIMARY KEY(claim_id,feature));
CREATE TABLE IF NOT EXISTS claim_adjudication(id TEXT PRIMARY KEY,item_id TEXT REFERENCES found_item(id),winner_claim TEXT REFERENCES claim_case(id),reason TEXT,source_id INTEGER REFERENCES source_blob(id));
CREATE TABLE IF NOT EXISTS return_event(id INTEGER PRIMARY KEY,item_id TEXT REFERENCES found_item(id),claim_id TEXT REFERENCES claim_case(id),bag_id TEXT REFERENCES custody_bag(id),source_id INTEGER REFERENCES source_blob(id));
"""
TABLES=["source_blob","service_trip","boarding_record","found_item","custody_bag","bag_item","handoff_event","bag_reconciliation","claim_case","claim_evidence","claim_adjudication","return_event"]

TABLES.insert(1,"source_failure")
def connect(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(p);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
