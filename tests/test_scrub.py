from app.scrub import Finding


def test_pattern_identifiers_are_removed(scrubber):
    text = (
        "Patient Maria Lopez (DOB 1984-03-12, SSN 999-12-3456) of 42 Oak St, Fairfax, Virginia, "
        "phone 555-201-8890, email maria.lopez@example.com, "
        "MRN 11111111-2222-3333-4444-555555555555, continuing lisinopril 10mg."
    )
    scrubbed, findings = scrubber.scrub(text, mask_medications=True)
    for secret in (
        "999-12-3456",
        "555-201-8890",
        "maria.lopez@example.com",
        "11111111-2222-3333-4444-555555555555",
        "lisinopril",
    ):
        assert secret not in scrubbed
    assert findings
    assert all(set(Finding(**{**item.public()}).public()) == set(item.public()) for item in findings)
    assert set(findings[0].public()) == {"entity_type", "start", "end", "score", "recognizer"}


def test_credit_card_is_detected_when_the_gateway_asks(scrubber):
    results = scrubber._analyzer.analyze(
        text="Copay card 4111-1111-1111-1111 was entered on the slip.",
        language="en",
        entities=["CREDIT_CARD"],
        score_threshold=0.4,
    )
    assert any(result.entity_type == "CREDIT_CARD" for result in results)


def test_credit_card_is_outside_the_ingest_allowlist(scrubber):
    text = "Copay card 4111-1111-1111-1111 was entered on the slip."
    scrubbed, findings = scrubber.scrub(text, mask_medications=True)
    assert "4111-1111-1111-1111" in scrubbed
    assert all(item.entity_type != "CREDIT_CARD" for item in findings)


def test_medication_policy(scrubber):
    text = "Plan: continue lisinopril 10mg."
    masked, _on = scrubber.scrub(text, mask_medications=True)
    kept, off = scrubber.scrub(text, mask_medications=False)
    assert "lisinopril" not in masked.lower()
    assert "lisinopril" in kept.lower()
    assert all(item.entity_type != "MEDICATION" for item in off)


def test_phone_formats(scrubber):
    for number in ("555-214-1187", "(555) 214-1187", "555.214.1187"):
        scrubbed, _findings = scrubber.scrub(f"Callback number {number} today.", mask_medications=False)
        assert number not in scrubbed


def test_hctz_is_not_in_the_lexicon(scrubber):
    scrubbed, findings = scrubber.scrub("Plan: continue HCTZ as previously tolerated.", mask_medications=True)
    assert "HCTZ" in scrubbed
    assert all(item.entity_type != "MEDICATION" for item in findings)
