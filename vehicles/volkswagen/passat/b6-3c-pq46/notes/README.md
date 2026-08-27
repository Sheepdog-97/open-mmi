# Reverse-engineering notes

Discovery notes may use provisional terminology and raw CAN observations. Before
a mapping becomes maintained, translate the confirmed concept into the shared
canonical event/status vocabulary and deterministic fixtures.

Do not store VINs, registration numbers, or unredacted private captures here.

For a related-vehicle mapping that is plausible but not yet authoritative on this
vehicle, use `candidate_mappings.v1.json`. Structured candidates must keep
`runtime_authority` false and include the exact status rule, source profiles, local
evidence, confidence and verification steps. `canbusd` ignores this file; promotion
means moving/merging the rule into `config.json`, adding replay coverage and removing
the candidate record.
