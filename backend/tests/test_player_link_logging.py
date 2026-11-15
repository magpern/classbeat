"""A player token or the link callback's query never reaches a log line (ADR 0034 section 3):
``classbeat.log.redact`` removes every ``cbp_`` token and the callback's query string."""

from classbeat.log import redact

SOURCE = "cbp_src_" + "S" * 43
CODE = "cbp_lc_" + "C" * 43
STATE = "planted-state-" + "T" * 20


def test_player_tokens_are_redacted_wherever_they_are() -> None:
    for line in (
        f"stored {SOURCE}",
        f"{{'source_token': '{SOURCE}'}}",
        f'{{"code": "{CODE}"}}',
        f"Authorization: Bearer {SOURCE}",
        f"failed with {CODE}.",
    ):
        out = redact(line)
        assert SOURCE not in out
        assert CODE not in out
        assert "S" * 20 not in out
        assert "C" * 20 not in out


def test_the_callback_query_is_removed() -> None:
    line = f"GET /connections/player/callback?code={CODE}&state={STATE} 302"
    out = redact(line)
    assert out == "GET /connections/player/callback 302"


def test_an_ordinary_word_is_kept() -> None:
    assert redact("cbp_ is the token prefix") == "cbp_ is the token prefix"
