# OSAS RBAC Role Matrix

OSAS uses least-privilege role permissions enforced by the application before-request guard and route decorators. Direct URL and API access use the same permission map; hiding a navigation item is not the security control.

## Roles and permissions

| Capability | Administrator | Security Head | Security Guard | Safety Officer | Faculty / Staff |
|---|---|---|---|---|---|
| Dashboard and safety overview | manage | view | view | view | view |
| View incidents and emergency records | view | view | view | view | view |
| Create/update incident records | manage | manage | — | manage | — |
| Resolve incidents | manage | manage | manage | manage | — |
| Close incidents | manage | manage | — | — | — |
| Patrol routes and checkpoints | manage | manage | manage | — | — |
| Visitor pass viewing | view | manage | manage | view | view |
| Visitor registration and gate validation | manage | manage | manage | — | — |
| Inspections, drills, and evacuation plans | manage | manage | — | manage | — |
| Reports and exports | view/export | view | — | view/export | — |
| User management and role assignment | manage | — | — | — | — |
| Activity/audit views | view | — | — | — | — |
| Own profile and password | manage own | manage own | manage own | manage own | manage own |

`—` means the capability is not granted by the current role permission map. The incident service also enforces valid state transitions and requires closure by an administrator or security head, regardless of the caller's UI.

## Incident workflow

```text
Reported → Verified → Responding → Resolved → Closed
```

- Backward transitions are rejected.
- `Resolved` requires response/resolution notes.
- `Closed` requires the actor role to be `admin` or `security_head`.
- Every accepted transition is written to `audit_logs` and notifies operational staff.

## Patrol controls

- A checkpoint must be resolved by its active token or code.
- A repeated scan from the same guard at the same checkpoint within 60 seconds is rejected server-side with HTTP 409 through the API.
- Scan methods (`qr`, `nfc`, `gps`, `manual`) are stored as method labels; NFC/GPS hardware capture is not currently implemented.

## Other controls implemented

- Failed-login lockout is configurable and stored per user.
- CSRF protection covers web POST forms.
- Session cookies use HTTP-only and SameSite settings; production enables secure cookies.
- File extension and size validation applies to incident attachments.
- Permission failures are handled through the global endpoint map and a 403 response.
