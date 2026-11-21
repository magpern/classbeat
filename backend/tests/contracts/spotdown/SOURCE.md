# Vendored spotdown contract (SD-P4, #1301)

Byte-for-byte copies of spotdown's versioned schemas and their examples, used by
`tests/test_spotdown_contract.py` to check classbeat's client and the fake spotdown
(`tests/spotdownfake.py`) against the contract. Do not edit them here.

- Source: [magpern/spotdown](https://github.com/magpern/spotdown), `main` at commit
  `23ab5a00467bdedef611461eb1a85e360a8aae24` (spotdown#39, which last changed the schemas: the
  optional `content.explicit` group of `analysis-v1`, document version 6.4, and the caller's
  optional `explicit` flag on each `/v2/match` item and on `POST /v2/analyses`; the schema files
  `match-v1`, `job-v1` and `lookup-v1` are unchanged since spotdown#23). Before it spotdown#23
  added the alignment consistency rules and the `url` ref digest, spotdown#21 the alignment
  hypotheses' `name` and `chosen`, and spotdown#18 the optional
  `provenance.source.decoded_duration_check`.
- Files: `schema/analysis-v1.json`, `schema/match-v1.json`, `schema/job-v1.json`,
  `schema/lookup-v1.json` and `schema/examples/*.json`.
- Contract text: `docs/contract-v2.md` at the same commit.

To update: copy the files from a newer spotdown commit, change the commit above, and run the
contract test. A new schema version (`*-v2`) is a new file beside the old one, never an edit.
