from cogs.plaza import Plaza


def test_pick_preferred_listing_prefers_normal_inactive() -> None:
    rows = [
        {"is_active": False, "is_gold": True, "is_rainbow": False},
        {"is_active": False, "is_gold": False, "is_rainbow": False},
    ]

    index = Plaza._pick_preferred_listing_index(rows)

    assert index == 1


def test_pick_preferred_listing_falls_back_to_inactive_variant() -> None:
    rows = [
        {"is_active": True, "is_gold": False, "is_rainbow": False},
        {"is_active": False, "is_gold": True, "is_rainbow": False},
    ]

    index = Plaza._pick_preferred_listing_index(rows)

    assert index == 1
