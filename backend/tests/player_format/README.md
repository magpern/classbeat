# Vendored player class format (ADR 0034 section 4)

Copied **unchanged** from `magpern/classbeat-player`, pinned to commit

    5dc9380635a913de50139f650854f005e3f659f6

(`docs(adr): 0010 pins the classbeat event names and who refuses a wrong link (#485)`).
Format 1.1 is what `classbeat.player_format_model` writes; format 1.2 (the Spotify playlist id) is
a later pin update, in its own PR, which replaces every file below from the new commit.

| Here | In the player repository |
|---|---|
| `schema/class.schema.json` | `schema/class-format/v1/class.schema.json` (strict) |
| `examples/valid/`, `examples/warnings.json` | `schema/class-format/v1/examples/` (valid vectors only) |
| `fixtures/` | `schema/converter/fixtures/` |
| `reference_converter.py` | `schema/converter/reference_converter.py` (writes format 1.0) |
| `classformat/` | `schema/python/src/classformat/` (validator and hash, pure) |

Never edit these files by hand. `jsonschema` (dev group) runs the schema; `rfc8785` is the hash's
canonical form, as in the player. The tests read them and never touch the network.
