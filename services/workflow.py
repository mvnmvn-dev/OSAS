"""Server-side workflow rules shared by routes, services, and tests."""

INCIDENT_TRANSITIONS = {
    "reported": frozenset({"reported", "verified"}),
    "verified": frozenset({"verified", "responding"}),
    "responding": frozenset({"responding", "resolved"}),
    "resolved": frozenset({"resolved", "closed"}),
    "closed": frozenset({"closed"}),
}

# Closing an incident is a governance action, not a routine status update.
INCIDENT_CLOSE_ROLES = frozenset({"admin", "security_head"})


def validate_incident_transition(current_status, next_status, actor_role=None, notes=""):
    """Return ``(is_valid, reason)`` for an incident status change.

    The function is deliberately independent of Flask and the database so that
    workflow behavior can be verified in a clean test environment.
    """
    current = (current_status or "").strip().lower()
    next_value = (next_status or "").strip().lower()
    if next_value not in INCIDENT_TRANSITIONS:
        return False, "Unknown incident status."
    if next_value not in INCIDENT_TRANSITIONS.get(current, frozenset()):
        return False, f"Incident cannot move from {current or 'unknown'} to {next_value}."
    if next_value == "closed" and actor_role not in INCIDENT_CLOSE_ROLES:
        return False, "Only an administrator or security head may close an incident."
    if next_value == "resolved" and not (notes or "").strip():
        return False, "Resolution notes are required before an incident can be resolved."
    return True, "OK"


def allowed_incident_transitions(current_status, actor_role=None):
    """Return user-facing status options permitted from the current state."""
    current = (current_status or "").strip().lower()
    options = []
    for status in INCIDENT_TRANSITIONS.get(current, frozenset()):
        valid, _ = validate_incident_transition(current, status, actor_role, "resolution recorded")
        if valid:
            options.append(status)
    return sorted(options)
