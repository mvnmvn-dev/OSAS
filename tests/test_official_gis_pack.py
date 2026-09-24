from services.safety_service import CAMPUS_CHECKPOINTS, OFFICIAL_PATROL_ROUTES, OFFICIAL_CHECKPOINT_RISK


def test_official_checkpoint_pack_contains_all_operational_ids():
    ids = {row[0] for row in CAMPUS_CHECKPOINTS}
    assert len(ids) == 22
    assert {"G1", "ST1", "ST2", "LAB1", "AD4", "PR2"}.issubset(ids)
    assert CAMPUS_CHECKPOINTS[0][3:] == (14.726050, 121.035880, 10)


def test_official_routes_reference_known_checkpoints():
    ids = {row[0] for row in CAMPUS_CHECKPOINTS}
    assert set(OFFICIAL_PATROL_ROUTES) == {"Alpha", "Bravo", "Charlie", "Delta"}
    assert all(set(route).issubset(ids) for route in OFFICIAL_PATROL_ROUTES.values())
    assert OFFICIAL_CHECKPOINT_RISK["G1"] == ("Critical", 10)
    assert OFFICIAL_CHECKPOINT_RISK["AD4"] == ("Low", 2)
