# Vendored spotdown contract (SD-P4, #1301)

Byte-for-byte copies of spotdown's versioned schemas and their examples, used by
`tests/test_spotdown_contract.py` to check classbeat's client and the fake spotdown
(`tests/spotdownfake.py`) against the contract. Do not edit them here.

- Source: [magpern/spotdown](https://github.com/magpern/spotdown), `main` at commit
  `b2c36d845f8d285d2edf13b68c44291d6cbc2c02` (the schemas themselves last changed in
  `c72a47836db80ddaf043c272e7c9ccc325d9e166`, spotdown#15).
- Files: `schema/analysis-v1.json`, `schema/match-v1.json`, `schema/job-v1.json`,
  `schema/lookup-v1.json` and `schema/examples/*.json`.
- Contract text: `docs/contract-v2.md` at the same commit.

To update: copy the files from a newer spotdown commit, change the commit above, and run the
contract test. A new schema version (`*-v2`) is a new file beside the old one, never an edit.
