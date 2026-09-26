"""Build free-text encounter notes from Synthea-style CSVs and record planted identifiers.

Synthea's patient CSV has names, DOB, SSN, and address, but not phone or email.
When those columns are missing, this module plants a deterministic fictional
phone (555 prefix) and an example.com address, and records both in the truth file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Extra names so the medication recognizer is a list you can extend, not a hard-coded regex.
EXTRA_MEDICATIONS = ["amlodipine", "losartan", "gabapentin"]

# (first, last, gender, birth, street, city, state, zip, condition, med_description, encounter_class, reason, template)
POPULATION: list[tuple] = [
    ("Maria", "Lopez", "F", "1984-03-12", "42 Oak St", "Fairfax", "Virginia", "22030", "hypertension", "Lisinopril 10 MG Oral Tablet", "ambulatory", "hypertension follow-up", "intake"),
    ("James", "Okonkwo", "M", "1976-11-02", "18 Cedar Ave", "Arlington", "Virginia", "22201", "type 2 diabetes mellitus", "Metformin 500 MG Oral Tablet", "ambulatory", "diabetes follow-up", "narrative"),
    ("Priya", "Shah", "F", "1991-07-19", "904 Maple Dr", "Bethesda", "Maryland", "20814", "asthma", "Albuterol 90 MCG/ACTUAT Inhaler", "emergency", "asthma exacerbation", "chart"),
    ("Elena", "Vasquez", "F", "1988-01-30", "7 Pine St", "Alexandria", "Virginia", "22314", "acute bronchitis", "Amoxicillin 500 MG Oral Capsule", "ambulatory", "bronchitis visit", "contact"),
    ("Noah", "Kim", "M", "2002-05-09", "55 Birch Rd", "Rockville", "Maryland", "20850", "allergic rhinitis", "Cetirizine 10 MG Oral Tablet", "ambulatory", "rhinitis visit", "followup"),
    ("Hannah", "Berger", "F", "1995-12-01", "12 Elm St", "Silver Spring", "Maryland", "20910", "generalized anxiety disorder", "Sertraline 50 MG Oral Tablet", "virtual", "anxiety follow-up", "intake"),
    ("Omar", "Farouk", "M", "1971-04-22", "300 King St", "Alexandria", "Virginia", "22314", "hypothyroidism", "Levothyroxine 50 MCG Oral Tablet", "ambulatory", "thyroid follow-up", "narrative"),
    ("Grace", "Chen", "F", "2016-08-14", "88 Willow Ln", "Fairfax", "Virginia", "22033", "otitis media", "Ibuprofen 200 MG Oral Tablet", "wellness", "sick visit", "chart"),
    ("Luis", "Ortega", "M", "1984-03-12", "19 Spruce Ct", "Reston", "VA", "20190", "hypertension", "Hydrochlorothiazide 25 MG Oral Tablet", "ambulatory", "hypertension follow-up", "hard"),
    ("Aisha", "Rahman", "F", "1983-06-25", "640 Broad St", "Richmond", "Virginia", "23220", "major depressive disorder", "Fluoxetine 20 MG Oral Capsule", "ambulatory", "depression follow-up", "contact"),
    ("Peter", "Novak", "M", "1968-02-17", "2 Harbor Way", "Norfolk", "Virginia", "23510", "obesity", "Atorvastatin 20 MG Oral Tablet", "ambulatory", "weight and lipids visit", "followup"),
    ("Chloe", "Martin", "F", "1999-09-08", "77 Grove Ave", "Charlottesville", "Virginia", "22903", "eczema", "Hydrocortisone 10 MG/ML Topical Cream", "virtual", "dermatology check", "intake"),
    ("Samuel", "Adler", "M", "1957-10-03", "15 Academy Rd", "Richmond", "Virginia", "23221", "migraine", "Sumatriptan 50 MG Oral Tablet", "emergency", "migraine visit", "narrative"),
    ("Nina", "Kowalski", "F", "1979-03-28", "501 Main St", "Roanoke", "Virginia", "24011", "iron deficiency anemia", "Ferrous Sulfate 325 MG Oral Tablet", "ambulatory", "anemia follow-up", "chart"),
    ("Marcus", "Hale", "M", "1990-12-12", "9 Dock St", "Baltimore", "Maryland", "21230", "gastroesophageal reflux", "Omeprazole 20 MG Delayed Release Oral Capsule", "ambulatory", "reflux follow-up", "contact"),
    ("Ivy", "Tran", "F", "2008-04-04", "23 Lantern Ln", "Annapolis", "Maryland", "21401", "well child check", "", "wellness", "well child visit", "followup"),
    ("Diego", "Alvarez", "M", "1982-07-07", "44 Market St", "Frederick", "Maryland", "21701", "low back pain", "Naproxen 500 MG Oral Tablet", "ambulatory", "back pain visit", "intake"),
    ("Ruth", "Feldman", "F", "1984-03-12", "6 Chapel St", "Winchester", "VA", "22601", "osteoarthritis", "Hydrochlorothiazide 25 MG Oral Tablet", "ambulatory", "joint pain visit", "hard"),
    ("Caleb", "Brooks", "M", "1964-05-21", "810 Ridge Rd", "Staunton", "Virginia", "24401", "acute sinusitis", "Fluticasone 50 MCG/ACTUAT Nasal Spray", "ambulatory", "sinusitis visit", "narrative"),
    ("Sofia", "Rossi", "F", "1974-01-15", "3 Plaza Dr", "Manassas", "Virginia", "20110", "community acquired pneumonia", "Azithromycin 250 MG Oral Tablet", "emergency", "pneumonia visit", "chart"),
]

_DOSE_WORDS = {
    "oral",
    "tablet",
    "capsule",
    "inhaler",
    "topical",
    "cream",
    "nasal",
    "spray",
    "delayed",
    "release",
    "mg",
    "mcg",
}


@dataclass
class NoteBuilder:
    chunks: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    length: int = 0

    def text(self, value: str) -> None:
        self.chunks.append(value)
        self.length += len(value)

    def ent(self, entity_type: str, value: str) -> None:
        if not value:
            return
        start = self.length
        self.chunks.append(value)
        self.length += len(value)
        self.entities.append(
            {"type": entity_type, "value": value, "start": start, "end": start + len(value)}
        )

    def render(self) -> str:
        return "".join(self.chunks)


def med_name(description: str) -> str:
    """Keep the drug name from a Synthea medication description, drop the dose."""
    parts: list[str] = []
    for raw in description.split():
        token = raw.strip(",").lower()
        if not token:
            continue
        if any(char.isdigit() for char in token) or token in _DOSE_WORDS:
            break
        parts.append(token)
        if len(parts) == 3:
            break
    return " ".join(parts)


def derive_phone(patient_id: str) -> str:
    number = int(hashlib.sha256(patient_id.encode()).hexdigest()[:6], 16)
    return f"555-{(number % 800) + 200:03d}-{(number // 800) % 10000:04d}"


def derive_email(first: str, last: str, patient_id: str) -> str:
    suffix = int(hashlib.sha256(patient_id.encode()).hexdigest()[:4], 16) % 1000
    local = f"{first}.{last}.{suffix}".lower().replace(" ", "")
    return f"{local}@example.com"


def _us_date(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{month}/{day}/{year}"


def _phone_for_index(index: int) -> str:
    exchange = f"{200 + index:03d}"
    line = f"{1000 + index * 17:04d}"
    style = index % 3
    if style == 0:
        return f"555-{exchange}-{line}"
    if style == 1:
        return f"(555) {exchange}-{line}"
    return f"555.{exchange}.{line}"


def _subject(gender: str) -> str:
    return {"F": "She", "M": "He"}.get(gender, "They")


def _add_meds(builder: NoteBuilder, meds: list[str], *, empty: str = "no medication started") -> None:
    if not meds:
        builder.text(empty)
        return
    for index, med in enumerate(meds):
        if index:
            builder.text(" and ")
        builder.ent("MEDICATION", med)


def _prose_dob(iso: str) -> str:
    if iso == "1984-03-12":
        return "March twelfth, nineteen eighty-four"
    return iso


def render_note(template: str, ctx: dict) -> tuple[str, list[dict]]:
    builder = NoteBuilder()
    full = f"{ctx['first']} {ctx['last']}"
    meds = ctx["meds"]
    if template == "intake":
        builder.text("Patient ")
        builder.ent("PERSON", full)
        builder.text(" (DOB ")
        builder.ent("DATE_TIME", ctx["birth"])
        builder.text(", SSN ")
        builder.ent("US_SSN", ctx["ssn"])
        builder.text(") of ")
        builder.ent("US_STREET_ADDRESS", ctx["street"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["city"])
        builder.text(" ")
        builder.ent("LOCATION", ctx["state"])
        builder.text(", phone ")
        builder.ent("PHONE_NUMBER", ctx["phone"])
        builder.text(f", seen for {ctx['condition']}; continuing ")
        _add_meds(builder, meds, empty="no standing medication")
        builder.text(".")
    elif template == "narrative":
        builder.text("On ")
        builder.ent("DATE_TIME", ctx["encounter_date"])
        builder.text(", ")
        builder.ent("PERSON", full)
        builder.text(" (MRN ")
        builder.ent("MRN", ctx["mrn"])
        builder.text(f") presented for {ctx['condition']}. {_subject(ctx['gender'])} lives at ")
        builder.ent("US_STREET_ADDRESS", ctx["street"])
        builder.text(" in ")
        builder.ent("LOCATION", ctx["city"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["state"])
        builder.text(", and uses ")
        builder.ent("EMAIL_ADDRESS", ctx["email"])
        builder.text(". Call ")
        builder.ent("PHONE_NUMBER", ctx["phone"])
        builder.text(". The plan keeps ")
        _add_meds(builder, meds, empty="no standing medication")
        builder.text(".")
    elif template == "chart":
        builder.text("Chart note. Name on the header is ")
        builder.ent("PERSON", f"{ctx['last']}, {ctx['first']}")
        builder.text(". Date of birth ")
        builder.ent("DATE_TIME", ctx["birth_us"])
        builder.text(". Social security number ")
        builder.ent("US_SSN", ctx["ssn"])
        builder.text(". Reached at ")
        builder.ent("PHONE_NUMBER", ctx["phone"])
        builder.text(" and ")
        builder.ent("EMAIL_ADDRESS", ctx["email"])
        builder.text(f". Problem list includes {ctx['condition']}. Current therapy: ")
        _add_meds(builder, meds, empty="none recorded")
        builder.text(". Visit date ")
        builder.ent("DATE_TIME", ctx["encounter_date"])
        builder.text(".")
    elif template == "contact":
        builder.ent("PERSON", full)
        builder.text(f" was in clinic on ")
        builder.ent("DATE_TIME", ctx["encounter_us"])
        builder.text(f" for {ctx['reason']}. Home: ")
        builder.ent("US_STREET_ADDRESS", ctx["street"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["city"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["state"])
        builder.text(". Email ")
        builder.ent("EMAIL_ADDRESS", ctx["email"])
        builder.text(". Mobile ")
        builder.ent("PHONE_NUMBER", ctx["phone"])
        builder.text(". DOB ")
        builder.ent("DATE_TIME", ctx["birth"])
        builder.text(". SSN ")
        builder.ent("US_SSN", ctx["ssn"])
        builder.text(". Medication list: ")
        _add_meds(builder, meds, empty="none recorded")
        builder.text(". Record id ")
        builder.ent("MRN", ctx["mrn"])
        builder.text(".")
    elif template == "followup":
        builder.text(f"Follow-up for {ctx['condition']}. ")
        builder.ent("PERSON", full)
        builder.text(" (born ")
        builder.ent("DATE_TIME", ctx["birth"])
        builder.text(") confirmed the number ")
        builder.ent("PHONE_NUMBER", ctx["phone"])
        builder.text(" and the mailbox ")
        builder.ent("EMAIL_ADDRESS", ctx["email"])
        builder.text(". They still live on ")
        builder.ent("US_STREET_ADDRESS", ctx["street"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["city"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["state"])
        builder.text(". Identifier ")
        builder.ent("US_SSN", ctx["ssn"])
        builder.text(" was checked at intake. MRN ")
        builder.ent("MRN", ctx["mrn"])
        builder.text(". Continuing ")
        _add_meds(builder, meds, empty="without a new medication")
        builder.text(f" after the ")
        builder.ent("DATE_TIME", ctx["encounter_date"])
        builder.text(f" {ctx['encounter_class']} visit.")
    elif template == "hard":
        # Abbreviation, prose date, last name alone. These are the misses we expect Presidio to drop.
        builder.text("Follow-up documented for ")
        builder.ent("PERSON", ctx["last"])
        builder.text(". Born ")
        builder.ent("DATE_TIME", _prose_dob(ctx["birth"]))
        builder.text(". The file lists social security number ")
        builder.ent("US_SSN", ctx["ssn"])
        builder.text(" and email ")
        builder.ent("EMAIL_ADDRESS", ctx["email"])
        builder.text(". Lives in ")
        builder.ent("LOCATION", ctx["city"])
        builder.text(", ")
        builder.ent("LOCATION", ctx["state"])
        builder.text(". Plan: continue ")
        builder.ent("MEDICATION", "HCTZ")
        builder.text(" as previously tolerated. Callback number ")
        builder.ent("PHONE_NUMBER", ctx["phone"])
        builder.text(".")
    else:
        raise ValueError(f"unknown template {template}")

    marker = "MARKER-CLINICA" if ctx["tenant_id"] == "clinic-a" else "MARKER-CLINICB"
    builder.text(f" Chart marker {marker}.")
    text = builder.render()
    for entity in builder.entities:
        if text[entity["start"] : entity["end"]] != entity["value"]:
            raise RuntimeError(f"offset mismatch for {entity}")
    return text, builder.entities


def write_sample_csvs(csv_dir: Path) -> None:
    csv_dir.mkdir(parents=True, exist_ok=True)
    patients: list[dict] = []
    encounters: list[dict] = []
    conditions: list[dict] = []
    medications: list[dict] = []
    for index, row in enumerate(POPULATION):
        first, last, gender, birth, street, city, state, zip_code, condition, med_description, enc_class, reason, _template = row
        patient_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"phi-gateway-patient-{index}"))
        encounter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"phi-gateway-encounter-{index}"))
        month = (index % 9) + 1
        day = (index % 27) + 1
        encounter_date = f"2024-{month:02d}-{day:02d}"
        patients.append(
            {
                "Id": patient_id,
                "BIRTHDATE": birth,
                "SSN": f"999-{index + 11:02d}-{4000 + index * 53:04d}",
                "FIRST": first,
                "LAST": last,
                "GENDER": gender,
                "ADDRESS": street,
                "CITY": city,
                "STATE": state,
                "ZIP": zip_code,
                "PHONE": _phone_for_index(index),
                "EMAIL": f"{first}.{last}@example.com".lower(),
            }
        )
        encounters.append(
            {
                "Id": encounter_id,
                "START": encounter_date,
                "PATIENT": patient_id,
                "ENCOUNTERCLASS": enc_class,
                "DESCRIPTION": reason,
            }
        )
        conditions.append(
            {
                "START": encounter_date,
                "PATIENT": patient_id,
                "ENCOUNTER": encounter_id,
                "CODE": f"SNOMED-{100000 + index}",
                "DESCRIPTION": condition,
            }
        )
        if med_description:
            medications.append(
                {
                    "START": encounter_date,
                    "PATIENT": patient_id,
                    "ENCOUNTER": encounter_id,
                    "CODE": f"RXNORM-{200000 + index}",
                    "DESCRIPTION": med_description,
                }
            )
    _write_csv(csv_dir / "patients.csv", patients)
    _write_csv(csv_dir / "encounters.csv", encounters)
    _write_csv(csv_dir / "conditions.csv", conditions)
    _write_csv(csv_dir / "medications.csv", medications)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _col(row: dict, *names: str) -> str:
    for name in names:
        if name in row and row[name]:
            return row[name]
        lower = {key.lower(): value for key, value in row.items()}
        if name.lower() in lower and lower[name.lower()]:
            return lower[name.lower()]
    return ""


def load_joined(csv_dir: Path) -> list[dict]:
    patients = { _col(row, "Id"): row for row in _read_csv(csv_dir / "patients.csv") }
    encounters = _read_csv(csv_dir / "encounters.csv")
    conditions: dict[str, list[str]] = {}
    for row in _read_csv(csv_dir / "conditions.csv"):
        conditions.setdefault(_col(row, "ENCOUNTER"), []).append(_col(row, "DESCRIPTION"))
    meds: dict[str, list[str]] = {}
    for row in _read_csv(csv_dir / "medications.csv"):
        name = med_name(_col(row, "DESCRIPTION"))
        if name:
            meds.setdefault(_col(row, "ENCOUNTER"), []).append(name)
    joined: list[dict] = []
    for index, encounter in enumerate(encounters):
        patient = patients[_col(encounter, "PATIENT")]
        patient_id = _col(patient, "Id")
        encounter_id = _col(encounter, "Id")
        first = _col(patient, "FIRST")
        last = _col(patient, "LAST")
        template = ["intake", "narrative", "chart", "contact", "followup"][index % 5]
        joined.append(
            {
                "index": index,
                "template": template,
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "first": first,
                "last": last,
                "gender": _col(patient, "GENDER") or "U",
                "birth": _col(patient, "BIRTHDATE"),
                "ssn": _col(patient, "SSN"),
                "street": _col(patient, "ADDRESS"),
                "city": _col(patient, "CITY"),
                "state": _col(patient, "STATE"),
                "zip": _col(patient, "ZIP"),
                "phone": _col(patient, "PHONE") or derive_phone(patient_id),
                "email": _col(patient, "EMAIL") or derive_email(first, last, patient_id),
                "condition": (conditions.get(encounter_id) or [_col(encounter, "DESCRIPTION") or "unspecified concern"])[0],
                "reason": _col(encounter, "DESCRIPTION") or "visit",
                "encounter_class": _col(encounter, "ENCOUNTERCLASS") or "ambulatory",
                "encounter_date": (_col(encounter, "START") or "")[:10],
                "meds": meds.get(encounter_id, []),
            }
        )
    return joined


def _is_sample(csv_dir: Path) -> bool:
    return "fixtures" in csv_dir.parts


def sample_template_for_index(index: int) -> str:
    if index < len(POPULATION):
        return POPULATION[index][12]
    return ["intake", "narrative", "chart", "contact", "followup"][index % 5]


def generate_from_csv(csv_dir: Path, *, sample: bool | None = None) -> tuple[list[dict], list[dict], list[str]]:
    rows = load_joined(csv_dir)
    use_sample = sample if sample is not None else (_is_sample(csv_dir) and len(rows) == len(POPULATION))
    notes: list[dict] = []
    truths: list[dict] = []
    lexicon: set[str] = set(EXTRA_MEDICATIONS)
    for row in rows:
        if use_sample:
            row["template"] = sample_template_for_index(row["index"])
        tenant_id = "clinic-a" if row["index"] % 2 == 0 else "clinic-b"
        ctx = {
            **row,
            "tenant_id": tenant_id,
            "mrn": row["patient_id"],
            "birth_us": _us_date(row["birth"]) if row["birth"] else "",
            "encounter_us": _us_date(row["encounter_date"]) if row["encounter_date"] else "",
        }
        text, entities = render_note(ctx["template"], ctx)
        for med in row["meds"]:
            lexicon.add(med)
        note_id = f"note-{row['index']:02d}"
        notes.append(
            {
                "note_id": note_id,
                "tenant_id": tenant_id,
                "patient_id": row["patient_id"],
                "encounter_id": row["encounter_id"],
                "encounter_class": row["encounter_class"],
                "condition": row["condition"],
                "template": ctx["template"],
                "demo_clear": ctx["template"] != "hard",
                "text": text,
            }
        )
        truths.append({"note_id": note_id, "tenant_id": tenant_id, "entities": entities})
    lexicon_list = sorted(lexicon, key=lambda item: (-len(item), item))
    return notes, truths, lexicon_list


def write_outputs(
    csv_dir: Path,
    notes_path: Path,
    truth_path: Path,
    lexicon_path: Path | None = None,
    *,
    sample: bool | None = None,
) -> tuple[list[dict], list[dict]]:
    notes, truths, lexicon = generate_from_csv(csv_dir, sample=sample)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text("".join(json.dumps(note) + "\n" for note in notes))
    truth_path.write_text("".join(json.dumps(truth) + "\n" for truth in truths))
    if lexicon_path is not None:
        lexicon_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# One medication name per line. The scrubber compiles these into a Presidio recognizer.\n"
            "# Names come from Synthea medication descriptions, plus a few additions.\n"
            "# HCTZ is omitted on purpose so the abbreviation shows up as a measured miss.\n"
        )
        lexicon_path.write_text(header + "\n".join(lexicon) + "\n")
    return notes, truths


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
