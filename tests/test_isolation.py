from pathlib import Path

from app.auth import AuthError, authorize
from app.ingest import ingest_note
from app.store import reset_stores


KEYS = {
    "clinic-a": {"key": "sk-test-clinic-a-0001", "alias": "clinic-a"},
    "clinic-b": {"key": "sk-test-clinic-b-0001", "alias": "clinic-b"},
}


def test_cross_tenant_is_forbidden():
    try:
        authorize(KEYS["clinic-a"]["key"], "clinic-b", KEYS)
    except AuthError as exc:
        assert exc.status == 403
        assert "clinic-a" in exc.detail
    else:
        raise AssertionError("expected 403")


def test_same_tenant_ok():
    info = authorize(KEYS["clinic-b"]["key"], "clinic-b", KEYS)
    assert info["alias"] == "clinic-b"


def test_sqlite_does_not_keep_identifier_values(tmp_path: Path, scrubber):
    reset_stores()
    text = (
        "Patient Maria Lopez (DOB 1984-03-12, SSN 999-12-3456) of 42 Oak St, Fairfax, Virginia, "
        "phone 555-201-8890, email maria.lopez@example.com, MRN 11111111-2222-3333-4444-555555555555."
    )
    ingest_note(
        "clinic-a",
        text,
        note_id="unit-1",
        scrubber=scrubber,
        data_dir=tmp_path,
        mask_medications=True,
    )
    blob = (tmp_path / "tenants" / "clinic-a.sqlite").read_bytes()
    for secret in (b"999-12-3456", b"555-201-8890", b"maria.lopez@example.com", b"11111111-2222-3333-4444-555555555555"):
        assert secret not in blob
    other = ingest_note(
        "clinic-b",
        "Clinic B only marker MARKER-CLINICB and phone 555-299-4242.",
        note_id="unit-b",
        scrubber=scrubber,
        data_dir=tmp_path,
        mask_medications=False,
    )
    assert "MARKER-CLINICB" in other["scrubbed_text"]
    clinic_a = (tmp_path / "tenants" / "clinic-a.sqlite").read_bytes()
    assert b"MARKER-CLINICB" not in clinic_a
