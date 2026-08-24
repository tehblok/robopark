# Robopark Phase 5 Tracker Core + Actions

## Implemented contracts

Phase 5 introduces a shared Tracker API under `/tracker/*` for admin, operator,
and mechanic roles with backend-enforced scope checks:

- queue/park access is derived from user role and assigned parks;
- every read/write by ticket key performs issue scope enforcement;
- upstream Tracker failures are normalized to `502` with stable detail codes.

## Read endpoints

- `GET /tracker/issues`
- `GET /tracker/issues/{key}`
- `GET /tracker/issues/{key}/comments`
- `GET /tracker/transitions/{key}`

## Action endpoints

- `POST /tracker/issues/{key}/comment`
- `POST /tracker/issues/{key}/assign`
- `POST /tracker/issues/{key}/unassign`
- `POST /tracker/issues/{key}/transition`
- `POST /tracker/issues/{key}/close`

## Policy flags

Stored in `platform_settings`:

- `tracker_operator_untagged`
- `tracker_operator_raw`
- `tracker_operator_firmware_profile`
- `tracker_mechanic_write`

Administrative API:

- `GET /admin/settings/tracker-policy`
- `PUT /admin/settings/tracker-policy`
