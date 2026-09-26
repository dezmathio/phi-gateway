from pathlib import Path

from app.notes import generate_from_csv, write_sample_csvs


def test_committed_fixtures_match_the_generator():
    from app.notes import read_jsonl

    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    fresh_notes, fresh_truths, _lexicon = generate_from_csv(root / "csv", sample=True)
    assert [note["text"] for note in fresh_notes] == [note["text"] for note in read_jsonl(root / "notes.jsonl")]
    assert fresh_truths == read_jsonl(root / "notes_truth.jsonl")


def test_sample_offsets_and_split(tmp_path: Path):
    csv_dir = tmp_path / "csv"
    write_sample_csvs(csv_dir)
    notes, truths, lexicon = generate_from_csv(csv_dir, sample=True)
    assert len(notes) == 20
    assert sum(note["tenant_id"] == "clinic-a" for note in notes) == 10
    assert sum(note["tenant_id"] == "clinic-b" for note in notes) == 10
    assert "lisinopril" in lexicon
    assert "hctz" not in lexicon
    by_id = {row["note_id"]: row for row in truths}
    for note in notes:
        truth = by_id[note["note_id"]]
        assert "MARKER-CLINIC" in note["text"]
        for entity in truth["entities"]:
            assert note["text"][entity["start"] : entity["end"]] == entity["value"]
    hard = [note for note in notes if note["template"] == "hard"]
    assert len(hard) == 2
    assert any("HCTZ" in note["text"] for note in hard)
    assert any("March twelfth, nineteen eighty-four" in note["text"] for note in hard)
