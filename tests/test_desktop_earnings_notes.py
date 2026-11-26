from desktop.earnings_notes import load
from desktop.vietnam import field


def test_vhm_eps_note_needs_publication_matching_period_and_reconciled_profit():
    quarters = [
        {"yearReport": 2025, "lengthReport": quarter, "profit": value}
        for quarter, value in enumerate((10, 20, 30, 40), 1)
    ] + [
        {"yearReport": 2026, "lengthReport": quarter, "profit": value}
        for quarter, value in enumerate((25_551_946_000_000, 22_683_056_000_000, 23_146_135_000_000), 1)
    ]
    book = {"income": {"fields": {"profit": "Attributable to parent company"},
                       "quarters": quarters, "years": [{"yearReport": 2025, "profit": 100}]}}
    note = load("VHM", "2026-11-03", book, field)
    assert note["basic_eps_vnd"] == 8395 and note["diluted_eps_vnd"] == 8395
    assert note["earnings_for_eps_vnd"] == 68_964_838_000_000
    assert load("VHM", "2026-11-02", book, field) == {}
    assert load("VIC", "2026-11-03", book, field) == {}
    quarters[-1]["profit"] -= 10_000_000
    assert load("VHM", "2026-11-03", book, field) == {}
