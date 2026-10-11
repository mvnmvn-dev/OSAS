from services.workflow import allowed_incident_transitions, validate_incident_transition


def test_incident_workflow_requires_sequential_response():
    assert validate_incident_transition("reported", "verified")[0]
    assert validate_incident_transition("verified", "responding")[0]
    assert not validate_incident_transition("verified", "resolved", notes="done")[0]


def test_resolution_requires_notes():
    assert not validate_incident_transition("responding", "resolved")[0]
    assert validate_incident_transition("responding", "resolved", notes="Area secured")[0]


def test_only_authorized_roles_can_close_incidents():
    assert not validate_incident_transition("resolved", "closed", "guard", "Closure recorded")[0]
    assert validate_incident_transition("resolved", "closed", "security_head", "Closure recorded")[0]


def test_allowed_transitions_are_role_aware():
    assert "resolved" in allowed_incident_transitions("responding", "guard")
    assert "closed" not in allowed_incident_transitions("resolved", "guard")
    assert "closed" in allowed_incident_transitions("resolved", "admin")
