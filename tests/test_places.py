from smartrefunds.places import normalise_place


def test_spellings_collapse_to_one_slug():
    for raw in ["Northfield", "North field", "northfield", "Northfield sort centre", "Northfield DC",
                "NFD", "NFD hub", "North-field depot"]:
        assert normalise_place(raw) == "northfield", raw


def test_empty_and_unknown():
    assert normalise_place(None) is None
    assert normalise_place("  ") is None
    assert normalise_place("Southgate depot") == "southgate"
