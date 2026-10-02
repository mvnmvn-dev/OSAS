from services.safety_service import ACTIVE_CHECKPOINT_CODES, CAMPUS_CHECKPOINTS, OFFICIAL_PATROL_ROUTES, OFFICIAL_CHECKPOINT_RISK


def test_official_checkpoint_pack_contains_all_operational_ids():
    ids = {row[0] for row in CAMPUS_CHECKPOINTS}
    assert ids == {"ENT", "PG", "OSAS", "ITS"}
    assert ids == ACTIVE_CHECKPOINT_CODES
    assert CAMPUS_CHECKPOINTS[0][0:3] == ("ENT", "Entrance", "ZA")
    assert CAMPUS_CHECKPOINTS[0][3:] == (14.726050, 121.035880, 10)


def test_official_routes_reference_known_checkpoints():
    ids = {row[0] for row in CAMPUS_CHECKPOINTS}
    assert set(OFFICIAL_PATROL_ROUTES) == {"Alpha", "Bravo", "Charlie", "Delta"}
    assert all(set(route).issubset(ids) for route in OFFICIAL_PATROL_ROUTES.values())
    assert OFFICIAL_CHECKPOINT_RISK["ENT"] == ("Critical", 10)
    assert OFFICIAL_CHECKPOINT_RISK["OSAS"] == ("Medium", 4)
    assert OFFICIAL_CHECKPOINT_RISK["ITS"] == ("High", 7)
    assert OFFICIAL_CHECKPOINT_RISK["PG"] == ("Medium", 6)
