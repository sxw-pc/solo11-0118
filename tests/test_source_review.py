"""Source-trip review: which riders to visit after an off-line find.

Nothing here proves ownership or returns an item; the tests assert only which
trips remain possible, which trip the evidence excludes, and who staff should
contact. All material is synthetic and offline.
"""
from pathlib import Path
import json
import pytest
from lostproperty.domain import (found_import,clearing_register,clear_observe_import,
                                 contact_import,review_import)
from lostproperty.report import report
from lostproperty.__main__ import COMMANDS,source_demo,main

F=Path(__file__).parents[1]/"fixtures"/"scenarios"

def review(db,item="I-S"):
    rows=[r for r in report(db)["source_reviews"] if r["item"]==item]
    assert len(rows)==1
    return rows[0]

def status(review):
    return {c["trip_id"]:c["status"] for c in review["candidates"]}

def contacted(review):
    return sorted((c["trip"],c["holder"]) for c in review["contacts"])

def reasons(review):
    return {c["trip_id"]:c["reason"] for c in review["candidates"]}

def setup(tmp_path,procedure="route18_camera_procedure.json",found="reuse_r1_r2_found.json",extra=None):
    db=tmp_path/"db.sqlite"
    clearing_register(db,F/procedure)
    found_import(db,F/found)
    if extra:clear_observe_import(db,F/extra)
    return db

# -- the R1/R2 acceptance check -------------------------------------------

def test_camera_procedure_excludes_r1_and_contacts_only_p2(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="clearance_camera_supervisor.json")
    r=review(db)
    assert status(r)=={"R1":"excluded","R2":"possible"}
    assert contacted(r)==[("R2","P2")]
    excluded=next(c for c in r["candidates"] if c["trip_id"]=="R1")
    assert excluded["exclusion_kind"]=="clearing"
    assert excluded["exclusion_evidence"]=="O-CAM-S"
    # The still-possible trip must say why it could not be excluded.
    assert reasons(r)["R2"]=="no_clearing_or_entry_record"

def test_dual_procedure_camera_is_not_enough_so_both_are_contacted(tmp_path):
    db=setup(tmp_path,"route18_dual_procedure.json",extra="clearance_camera_supervisor.json")
    r=review(db)
    assert status(r)=={"R1":"possible","R2":"possible"}
    assert contacted(r)==[("R1","P1"),("R2","P2")]
    ev=next(e for e in r["evidence"] if e["evidence_id"]=="O-CAM-S")
    assert ev["applicable"]==0 and ev["reason"]=="method_not_in_force"

def test_dual_procedure_with_two_cleaners_excludes_r1(tmp_path):
    db=setup(tmp_path,"route18_dual_procedure.json",extra="clearance_dual_two_cleaners.json")
    r=review(db)
    assert status(r)=={"R1":"excluded","R2":"possible"}
    assert contacted(r)==[("R2","P2")]

def test_dual_procedure_needs_two_distinct_witnesses(tmp_path):
    db=setup(tmp_path,"route18_dual_procedure.json",extra="clearance_dual_one_cleaner.json")
    r=review(db)
    assert status(r)=={"R1":"possible","R2":"possible"}
    ev=next(e for e in r["evidence"] if e["evidence_id"]=="O-DUAL-ONE")
    assert ev["reason"]=="witnesses_insufficient"

def test_neither_review_claims_an_owner(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="clearance_camera_supervisor.json")
    data=report(db)
    # Source review only re-points attention; found/claim/custody state untouched.
    assert data["found_item"][0]["trip_id"]=="R2"
    assert data["found_item"][0]["status"]=="ready"
    assert data["claim_case"]==[] and data["return_event"]==[]

# -- interrupted and contradicting clearing evidence ----------------------

def test_clearing_with_gap_cannot_cut_r1(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="clearance_camera_gap.json")
    r=review(db)
    assert status(r)=={"R1":"possible","R2":"possible"}
    assert contacted(r)==[("R1","P1"),("R2","P2")]
    ev=next(e for e in r["evidence"] if e["evidence_id"]=="O-CAM-GAP")
    assert ev["reason"]=="observation_gap"

def test_occupied_observation_contradicts_empty_and_blocks_narrowing(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="contradiction_occupied.json")
    r=review(db)
    assert status(r)=={"R1":"possible","R2":"possible"}
    assert contacted(r)==[("R1","P1"),("R2","P2")]
    # R1's exclusion is withdrawn and carries the conflict reason.
    assert reasons(r)["R1"]=="conflicting_evidence:occupied_observation"

def test_wrong_vehicle_or_seat_observation_is_inert(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="other_vehicle_clearance.json")
    r=review(db)
    # BUS-9/S observation says nothing about BUS-7.
    assert status(r)=={"R1":"possible","R2":"possible"}

def test_other_vehicle_same_seat_number_is_never_merged(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",found="other_vehicle_same_seat_found.json",
             extra="other_vehicle_clearance.json")
    r=review(db)
    # Q1 is a different vehicle entirely; its seat S and boarding P9 stay out.
    assert "Q1" not in status(r)
    assert contacted(r)==[("R1","P1"),("R2","P2")]

# -- item-entry record and missing riders ---------------------------------

def test_item_entry_record_can_cut_r1_without_clearing(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="item_entry_after_r1.json")
    r=review(db)
    assert status(r)=={"R1":"excluded","R2":"possible"}
    excluded=next(c for c in r["candidates"] if c["trip_id"]=="R1")
    assert excluded["exclusion_kind"]=="item_entry" and excluded["exclusion_evidence"]=="E-S"
    assert contacted(r)==[("R2","P2")]

def test_missing_boarding_keeps_trip_but_contacts_nobody(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",found="reuse_missing_boarding_found.json")
    r=review(db)
    assert status(r)=={"R1":"possible","R2":"possible"}
    # Only R2 has a boarding row; R1 stays possible and says it has no record.
    assert contacted(r)==[("R2","P2")]
    assert reasons(r)["R1"].endswith(";no_boarding_record")

def test_trips_outside_intake_window_are_not_candidates(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",found="trip_out_of_window_found.json")
    r=review(db)
    # R0 ended 07:30; the 11:10 find is far outside its two-hour window.
    assert status(r)=={"R1":"possible","R2":"possible"}
    assert contacted(r)==[("R1","P1"),("R2","P2")]

def test_same_seat_number_never_merges_people_or_crosses_seats(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",found="reuse_same_token_two_people.json")
    rows=review(db)["contacts"]
    # Only seat S counts; the seat-T rider on R1 is excluded, and the two
    # different seat-S riders on R2 stay two separate contacts.
    assert sorted((c["trip"],c["holder"]) for c in rows)==[("R1","P1"),("R2","P2"),("R2","P2b")]

# -- ordering, pinning, revisions, corrections, idempotency ---------------

def test_procedure_registered_after_intake_does_not_apply_retroactively(tmp_path):
    db=tmp_path/"db.sqlite"
    found_import(db,F/"reuse_r1_r2_found.json")
    clearing_register(db,F/"route18_camera_procedure.json")   # too late
    clear_observe_import(db,F/"clearance_camera_supervisor.json")
    r=review(db)
    assert status(r)=={"R1":"possible","R2":"possible"}
    assert r["procedure"] is None
    ev=next(e for e in r["evidence"] if e["evidence_id"]=="O-CAM-S")
    assert ev["reason"]=="no_procedure_pinned"

def test_duplicate_and_late_import_create_one_revision_and_a_correction(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json")
    first=review(db)
    assert first["revision"]==1 and status(first)=={"R1":"possible","R2":"possible"}

    # Re-importing identical material must not manufacture a new revision.
    found_import(db,F/"reuse_r1_r2_found.json")
    assert review(db)["revision"]==1
    assert report(db)["contact_correction"]==[]

    # Late clearing material revises the list and records what changed.
    clear_observe_import(db,F/"clearance_camera_supervisor.json")
    later=review(db)
    assert later["revision"]==2 and contacted(later)==[("R2","P2")]
    corrections=[(c["trip_id"],c["holder"],c["note"]) for c in later["corrections"]]
    assert ("R1","P1","removed_by_revision") in corrections
    # Re-importing the same clearing keeps revision 2 and adds no duplicate.
    clear_observe_import(db,F/"clearance_camera_supervisor.json")
    assert review(db)["revision"]==2
    assert len(report(db)["contact_correction"])==1

def test_later_revision_that_adds_a_rider_is_a_correction_not_a_rewrite(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="clearance_camera_supervisor.json")
    assert contacted(review(db))==[("R2","P2")]
    # A contradictory observation withdraws the cut: P1 must be visited again.
    clear_observe_import(db,F/"contradiction_occupied.json")
    r=review(db)
    assert r["revision"]==3 and contacted(r)==[("R1","P1"),("R2","P2")]
    assert ("R1","P1","added_by_revision") in [(c["trip_id"],c["holder"],c["note"]) for c in r["corrections"]]
    # The earlier contact with P2 is preserved as history.
    contact_import(db,F/"contacts_r1_r2.json")
    events=report(db)["contact_event"]
    assert [(e["trip_id"],e["holder"],e["staff"]) for e in events]==[("R2","P2","agent-1")]

def test_review_import_recomputes_on_demand_and_rejects_unknown_item(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json")
    refile=tmp_path/"recompute.json";refile.write_text(json.dumps({"items":["I-S"]}),encoding="utf-8")
    assert review_import(db,refile)=={"items":1}
    assert review(db)["revision"]==1
    bad=tmp_path/"bad.json";bad.write_text(json.dumps({"items":["NOPE"]}),encoding="utf-8")
    with pytest.raises(ValueError,match="unknown_item"):
        review_import(db,bad)

def test_contact_must_match_a_real_boarding_row(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json")
    bad=tmp_path/"bad.json"
    bad.write_text(json.dumps({"contacts":[{"id":"CE9","item":"I-S","trip":"R1","token":"TK-R2-S",
                                           "holder":"P2","contacted_at":"2026-11-01T11:30:00+08:00"}]}),encoding="utf-8")
    with pytest.raises(ValueError,match="contact_boarding_mismatch"):
        contact_import(db,bad)

def test_cli_exposes_source_review_commands(tmp_path):
    for name in ("clearing-register","clear-observe-import","contact-import","review-import"):
        assert name in COMMANDS

def test_source_demo_contrasts_the_two_procedures():
    reviews=source_demo()["reviews"]
    assert reviews["cabin_camera"]["excluded"]==["R1"]
    assert reviews["cabin_camera"]["contacts"]==[["R2","P2"]]
    assert reviews["dual_attested"]["remaining"]==["R1","R2"]
    assert reviews["dual_attested"]["contacts"]==[["R1","P1"],["R2","P2"]]

# -- report shape ---------------------------------------------------------

def test_report_lists_each_item_with_sources_contacts_and_evidence(tmp_path):
    db=setup(tmp_path,"route18_camera_procedure.json",extra="clearance_camera_supervisor.json")
    row=review(db)
    assert row["vehicle"]=="BUS-7" and row["seat"]=="S" and row["claimed_trip"]=="R2"
    assert {c["trip_id"] for c in row["candidates"]}=={"R1","R2"}
    assert all({"status","exclusion_kind","exclusion_evidence","reason"}<=set(c) for c in row["candidates"])
    assert any(e["applicable"]==1 for e in row["evidence"])
