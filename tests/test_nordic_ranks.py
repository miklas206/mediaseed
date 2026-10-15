from mediaseed.nordic_ranks import GIB, TIB, rank_progress


def test_successor_and_unknown_true_upload_are_not_credited_upload():
    progress = rank_progress({"automatic": "Explorer"}, {"uploaded": 999*TIB})
    assert progress["target"]["title"] == "Contributor"
    upload, membership = progress["target"]["requirements"]
    assert upload == {"metric": "trueUpload", "goal": TIB, "unit": "bytes", "current": None, "missing": None}
    assert membership["goal"] == 60 and membership["missing"] is None


def test_unknown_and_alternative_ranks_never_guess_a_successor():
    for title in ("Beginner", "Owner", "Archivist", "Legend", ""):
        assert rank_progress({"automatic": title}, {})["target"] is None


def test_screenshot_requirements_and_seeding_size_gap():
    progress = rank_progress({}, {"seeding_size": 4*TIB})
    table = progress["table"]
    assert table[0]["requirements"][0]["goal"] == 500*GIB
    assert [r["title"] for r in table] == ["Explorer", "Contributor", "Pirate", "Veteran", "Guru", "Legend", "Archivist"]
    assert [r["goal"] for r in table[4]["requirements"]] == [50*TIB, 50, 25, 365]
    assert [r["goal"] for r in table[5]["requirements"]] == [100*TIB, 200, 100, 730]
    assert table[-1]["alternative"] is True
    size = table[-1]["requirements"][1]
    assert size["current"] == 4*TIB and size["missing"] == 6*TIB
    assert table[-1]["requirements"][0]["missing"] is None
    assert rank_progress({}, {"seeding_size": 12*TIB})["table"][-1]["requirements"][1]["missing"] == 0
