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


def test_credit_card_is_caught_at_ingest(scrubber):
    text = "Copay card 4111-1111-1111-1111 was entered on the slip."
    scrubbed, findings = scrubber.scrub(text, mask_medications=True)
    assert "4111-1111-1111-1111" not in scrubbed
    assert any(item.entity_type == "CREDIT_CARD" for item in findings)


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


def test_us_street_address_recognizer(scrubber):
    for address in (
        "18 Cedar Ave",
        "7 Pine St",
        "12 Elm St",
        "300 King St",
        "640 Broad St",
        "77 Grove Ave",
        "15 Academy Rd",
        "9 Dock St",
        "23 Lantern Ln",
        "810 Ridge Rd",
    ):
        scrubbed, findings = scrubber.scrub(f"Lives at {address} in Virginia.", mask_medications=False)
        assert address not in scrubbed, f"{address} should be masked"
        types = {item.entity_type for item in findings}
        assert "US_STREET_ADDRESS" in types or "LOCATION" in types, f"{address} should trigger address recognition"


def test_street_address_common_patterns(scrubber):
    for address, city_state in [
        ("123 Main Street", "Springfield, IL"),
        ("456 Oak Avenue", "Portland, OR"),
        ("789 First Blvd", "Austin, TX"),
        ("1 Park Place", "Miami, FL"),
        ("50 Harbor Way", "Seattle, WA"),
    ]:
        text = f"Patient lives at {address}, {city_state}."
        scrubbed, _findings = scrubber.scrub(text, mask_medications=False)
        assert address not in scrubbed, f"{address} should be masked"
