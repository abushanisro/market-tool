# Replay fixtures

Real responses captured on 2026-10-27 from the public endpoints listed in `index.json`
(via a browser on the developer machine, because the build sandbox had no direct egress).
`recorded_url` is the exact URL that was fetched; where it differs from the index key
(Frankfurter time series) the payload is a superset/subset of what the key URL would return.

Used by `EMITHRAN_REPLAY_DIR` for tests and offline demos. Every record produced from a replayed
response is flagged `replayed` in `quality_flags` and is visible as such in the UI.
