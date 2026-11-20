# Vendored spotdown contract (SD-P4, #1301)

Byte-for-byte copies of spotdown's versioned schemas and their examples, used by
`tests/test_spotdown_contract.py` to check classbeat's client and the fake spotdown
(`tests/spotdownfake.py`) against the contract. Do not edit them here.

- Source: [magpern/spotdown](https://github.com/magpern/spotdown), `main` at commit
  `b22048e766ee19d5c6baf863fc1b0b3d705684ea` (spotdown#23, which last changed the schemas: the
  alignment consistency rules and the `url` ref digest). Before it spotdown#21 added the alignment
  hypotheses' `name` and `chosen`, and spotdown#18 the optional
  `provenance.source.decoded_duration_check`.
- Files: `schema/analysis-v1.json`, `schema/match-v1.json`, `schema/job-v1.json`,
  `schema/lookup-v1.json` and `schema/examples/*.json`.
- Contract text: `docs/contract-v2.md` at the same commit.

To update: copy the files from a newer spotdown commit, change the commit above, and run the
contract test. A new schema version (`*-v2`) is a new file beside the old one, never an edit.
