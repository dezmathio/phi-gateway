from app.metrics import overlaps, score_corpus


def test_overlap_coefficient():
    assert overlaps({"start": 0, "end": 10}, {"start": 0, "end": 10})
    assert overlaps({"start": 0, "end": 10}, {"start": 2, "end": 12})
    assert not overlaps({"start": 0, "end": 10}, {"start": 8, "end": 30})


def test_score_counts_leaks_and_precision():
    notes = [{"note_id": "n", "scrubbed_text": "seen <PERSON> about VA"}]
    truths = [
        {
            "note_id": "n",
            "entities": [
                {"type": "PERSON", "value": "Maria Lopez", "start": 0, "end": 11},
                {"type": "LOCATION", "value": "VA", "start": 20, "end": 22},
            ],
        }
    ]
    predicted = {
        "n": [
            {"entity_type": "PERSON", "start": 0, "end": 11},
            {"entity_type": "PERSON", "start": 40, "end": 48},
        ]
    }
    report = score_corpus(notes, truths, predicted)
    by_type = {row["type"]: row for row in report["per_type"]}
    assert by_type["PERSON"]["recall"] == 1
    assert by_type["PERSON"]["precision"] == 0.5
    assert by_type["LOCATION"]["recall"] == 0
    assert by_type["LOCATION"]["leaks"] == 1
    assert by_type["PERSON"]["leaks"] == 0
