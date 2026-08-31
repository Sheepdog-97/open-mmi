# Mapping fixtures

`mappings.v1.json` provides deterministic replay coverage for the experimental
Passat mappings. Each case supplies exact CAN bytes and expected canonical
events/status paths. The replay gate must cover every event and non-alias status
output claimed by the profile.
