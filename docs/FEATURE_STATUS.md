# OSAS Feature Status

This inventory keeps the defense and deployment description aligned with the current source code.

## Implemented

- Role-based access control for dashboard, safety, patrol, visitor, reports, and administration routes.
- Failed-login throttling and account lockout.
- Incident creation, audit logging, and server-side state transitions.
- Resolution notes required before an incident reaches `Resolved`.
- Administrator/security-head authorization required to close incidents.
- Patrol checkpoint token/code validation.
- Server-side duplicate checkpoint-scan rejection within a 60-second window for the same guard and checkpoint.
- Weighted heuristic risk score with exponential recency decay and explainable contributions.
- OSRM route integration with a labeled heuristic fallback.
- Visitor pass lifecycle: registered → checked in → exited, with expiry and audit events.
- Safety inspections, emergency drills, evacuation-plan records, reports, and in-app notifications.
- Protected API for checkpoint risk-score explanations.

## Partial or dependent on configuration

- Parent notifications are queued; actual email/SMS delivery requires provider configuration.
- Incident attachments are validated and stored on the application filesystem; managed hosting may use ephemeral disks.
- QR/token scanning is implemented through code/token input. NFC and GPS are stored as method labels only; no hardware integration is present.
- OSRM route quality depends on a reachable campus routing service and suitable map data.
- Integration tests require a configured MySQL database; unit tests and mocked routing tests can run without live external services.
- Dashboard analytics reflect available database records and should not be treated as evidence of improved campus safety without a separate evaluation.

## Future or research work

- Historical holdout evaluation and fixed-route baseline comparison for the risk model.
- Requirements traceability matrix and representative usability study.
- Checkpoint assignment validation against active route/shift membership.
- Durable object storage and private authorization for sensitive attachments.
- Backup, restoration, and disaster-recovery drills.
- Production security review, including secret rotation, HTTPS verification, and upload access testing.
