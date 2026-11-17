from pathlib import Path
import pytest
from lostproperty.domain import found_import,custody_import,claim_import,bag_reconcile,claim_adjudicate
from lostproperty.report import report
from lostproperty.__main__ import demo
F=Path(__file__).parents[1]/"fixtures"

def test_held_property_crosses_custody_and_claim(tmp_path):
    db=tmp_path/"db.sqlite"
    found_import(db,F/"found-import_normal.json")
    custody_import(db,F/"custody-import_normal.json")
    result=claim_import(db,F/"claim-import_normal.json")
    assert result["returned"]==1
    assert report(db)["custody_bag"][0]["custodian"]=="depot"
    assert report(db)["found_item"][0]["status"]=="returned"

def test_found_identity_conflict_is_atomic(tmp_path):
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json")
    with pytest.raises(ValueError,match="found_item_identity_conflict"):
        found_import(db,F/"found-import_conflict.json")
    assert len(report(db)["found_item"])==1

def test_broken_seal_changes_claim_outcome(tmp_path):
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json")
    custody_import(db,F/"custody-import_normal.json")
    assert custody_import(db,F/"custody-import_conflict.json")["broken"]==1
    assert claim_import(db,F/"claim-import_normal.json")["held"]==1
    assert report(db)["claim_case"][0]["reason"]=="custody_broken"

def test_competing_claimants_block_physical_return(tmp_path):
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json");custody_import(db,F/"custody-import_normal.json")
    assert claim_import(db,F/"claim-import_conflict.json")["contested"]==2
    assert report(db)["found_item"][0]["status"]=="ready"

def test_demo_and_retry(tmp_path):
    assert demo(tmp_path)["results"]["claim-import"]["returned"]==1
    db=tmp_path/"data.db"
    assert claim_import(db,F/"claim-import_normal.json")["returned"]==1
    assert len(report(db)["claim_case"])==1

def test_two_witness_reconciliation_recovers_held_claim(tmp_path):
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json")
    custody_import(db,F/"custody-import_normal.json")
    custody_import(db,F/"custody-import_conflict.json")
    claim_import(db,F/"claim-import_normal.json")
    assert report(db)["claim_case"][0]["status"]=="held"
    with pytest.raises(ValueError,match="reconciliation_evidence_insufficient"):
        bag_reconcile(db,F/"bag-reconcile_conflict.json")
    assert report(db)["source_failure"][-1]["error"]=="reconciliation_evidence_insufficient"
    assert bag_reconcile(db,F/"bag-reconcile_normal.json")["claims_recovered"]==1
    assert report(db)["claim_case"][0]["status"]=="returned"
    assert report(db)["return_event"][0]["bag_id"]=="B1"

def test_competing_claim_resolution_requires_verified_boarding_token(tmp_path):
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json")
    custody_import(db,F/"custody-import_normal.json")
    claim_import(db,F/"claim-import_conflict.json")
    assert claim_adjudicate(db,F/"claim-adjudicate_normal.json")["returned"]==1
    status={r["id"]:r["status"] for r in report(db)["claim_case"]}
    assert status=={"C2":"returned","C3":"rejected"}
    assert len(report(db)["return_event"])==1

def test_invalid_competitor_cannot_win_claim(tmp_path):
    import json
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json")
    custody_import(db,F/"custody-import_normal.json");claim_import(db,F/"claim-import_conflict.json")
    bad=tmp_path/"bad.json"
    bad.write_text(json.dumps({"decisions":[{"id":"D2","item":"I1","winner":"C3","reason":"unsupported claim"}]}),encoding="utf-8")
    with pytest.raises(ValueError,match="claim_adjudication_unproven"):
        claim_adjudicate(db,bad)
    assert report(db)["found_item"][0]["status"]=="ready"

def test_out_of_window_found_item_cannot_enter_custody(tmp_path):
    import json
    db=tmp_path/"db.sqlite"
    data=json.loads((F/"found-import_normal.json").read_text())
    data["items"][0]["found_at"]="2026-11-02T00:30:00+08:00"
    file=tmp_path/"late.json";file.write_text(json.dumps(data),encoding="utf-8")
    assert found_import(db,file)["held"]==1
    with pytest.raises(ValueError,match="item_not_ready"):
        custody_import(db,F/"custody-import_normal.json")
    assert report(db)["source_failure"][-1]["error"]=="item_not_ready"

def test_wrong_handoff_sequence_and_missing_claim_features(tmp_path):
    import json
    db=tmp_path/"db.sqlite";found_import(db,F/"found-import_normal.json")
    custody_import(db,F/"custody-import_normal.json")
    data={"bags":[],"handoffs":[{"id":"H3","bag":"B1","sequence":3,"from":"depot",
                                  "to":"lost-office","seal":"S1"}]}
    hand=tmp_path/"hand.json";hand.write_text(json.dumps(data),encoding="utf-8")
    assert custody_import(db,hand)["broken"]==1
    assert report(db)["custody_bag"][0]["custodian"]=="depot"
    bag_reconcile(db,F/"bag-reconcile_normal.json")
    claim={"claims":[{"id":"C4","item":"I1","claimant":"Rider-A","trip":"R1","ticket":"TK1",
                      "features":["red case"]}]}
    file=tmp_path/"claim.json";file.write_text(json.dumps(claim),encoding="utf-8")
    assert claim_import(db,file)["held"]==1
    assert report(db)["claim_case"][0]["reason"]=="evidence_mismatch"
