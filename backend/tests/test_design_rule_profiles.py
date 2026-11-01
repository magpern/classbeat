"""Rule profiles (ADR 0017 P1): schema, loader, digests, no free text and ``select_profile``."""

import ast
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from classbeat.design.rules import RATIONALES, ProfileError, load_registry, select_profile
from classbeat.design.rules import enums as E
from classbeat.design.rules import profile as P

PROFILES = P.PROFILES_DIR
RULES_PKG = Path(P.__file__).resolve().parent


def _profile_dict(**over: Any) -> dict[str, Any]:
    d = json.loads((PROFILES / "cycling" / "interval_45.json").read_text())
    d.update(over)
    return d


def _parse(d: dict[str, Any]) -> P.Profile:
    return P.parse_profile(json.dumps(d).encode())


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "profiles"
    shutil.copytree(PROFILES, dst)
    return dst


def test_shipped_profiles_load() -> None:
    reg = load_registry()
    assert sorted(reg.profiles) == [
        f"cycling.{t}.{n}" for t in ("interval", "plain") for n in (30, 45, 60)
    ]
    assert reg.rules_version == 1
    for p in reg.profiles.values():
        assert p.evidence == "strong"
        assert p.n >= E.MIN_N_PERCENTILE
        assert p.source == E.SOURCE
        assert 0 < len(p.rules) <= P.MAX_RULES
    # plain classes stay in zones 1 to 4; interval classes have top-zone rules
    assert "zone_ceiling_exceeded" in {r.rule for r in reg.profiles["cycling.plain.45"].rules}
    assert "top_zone_share_high" in {r.rule for r in reg.profiles["cycling.interval.45"].rules}


def test_only_strong_classes_ship() -> None:
    reg = load_registry()
    assert not [p for p in reg.profiles.values() if p.class_type not in ("plain", "interval")]
    assert not [p for p in reg.profiles.values() if p.label_minutes not in (30, 45, 60)]


def test_digests_are_stable_and_match_the_index() -> None:
    index = json.loads((PROFILES / "index.json").read_text())
    for pid, entry in index["profiles"].items():
        raw = (PROFILES / entry["file"]).read_bytes()
        assert P.digest_of(raw) == entry["digest"], f"{pid}: bump version and refresh the index"
        # a checkout with other line endings does not change the digest
        assert P.digest_of(raw.replace(b"\n", b"\r\n")) == entry["digest"]
    assert index["rules_version"] == max(e["version"] for e in index["profiles"].values())


def test_changed_file_without_version_and_digest_is_refused(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    f = root / "cycling" / "plain_30.json"
    d = json.loads(f.read_text())
    d["rules"][0]["low"] = d["rules"][0]["low"] + 1
    f.write_text(json.dumps(d, indent=2) + "\n")
    with pytest.raises(ProfileError, match="digest differs"):
        load_registry(root)


def test_changed_version_without_index_is_refused(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    f = root / "cycling" / "plain_30.json"
    d = json.loads(f.read_text())
    d["version"] = 2
    f.write_text(json.dumps(d, indent=2) + "\n")
    with pytest.raises(ProfileError):
        load_registry(root)


def test_unlisted_profile_file_is_refused(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    shutil.copy(root / "cycling" / "plain_30.json", root / "cycling" / "plain_75.json")
    with pytest.raises(ProfileError, match="differ"):
        load_registry(root)


def test_schema_json_is_in_sync_and_closed() -> None:
    assert (PROFILES / "schema.json").read_text() == P.dumps_schema()
    schema = json.loads(P.dumps_schema())

    def closed(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node.get("additionalProperties") is False
            for v in node.values():
                closed(v)
        elif isinstance(node, list):
            for v in node:
                closed(v)

    closed(schema)


def test_unknown_field_is_refused() -> None:
    with pytest.raises(ProfileError):
        _parse(_profile_dict(title="anything"))
    d = _profile_dict()
    d["rules"][0]["note"] = "x"
    with pytest.raises(ProfileError):
        _parse(d)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source",), "somewhere else"),
        (("snapshot",), "last week"),
        (("id",), "cycling.interval.45 and more"),
        (("rules", 0, "rule"), "a free sentence about warming up"),
        (("rules", 0, "metric"), "free text"),
        (("rules", 0, "rationale_id"), "Most classes ride a while first."),
        (("rules", 0, "brief_overrides"), ["any text at all"]),
        (("sport",), "swimming"),
        (("class_type",), "spin party"),
    ],
)
def test_free_strings_are_refused(path: tuple[Any, ...], value: Any) -> None:
    d = _profile_dict()
    node: Any = d
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ProfileError):
        _parse(d)


def test_percentile_basis_needs_25_classes() -> None:
    assert _parse(_profile_dict(n=25)).n == 25
    with pytest.raises(ProfileError, match="n >= 25"):
        _parse(_profile_dict(n=24))


def test_range_basis_forces_info_and_small_n() -> None:
    d = _profile_dict(n=12, evidence="moderate")
    for r in d["rules"]:
        r["basis"] = "range_10"
        r["severity"] = "warn"
    with pytest.raises(ProfileError):
        _parse(d)
    for r in d["rules"]:
        r["severity"] = "info"
    assert _parse(d).n == 12


def test_evidence_limits() -> None:
    with pytest.raises(ProfileError, match="weak"):
        _parse(_profile_dict(evidence="weak"))
    with pytest.raises(ProfileError, match="moderate"):
        _parse(_profile_dict(evidence="moderate"))
    assert _parse(_profile_dict(evidence="weak", rules=[])).rules == []


def test_rule_must_match_its_metric_and_side() -> None:
    d = _profile_dict()
    d["rules"][0]["metric"] = "cooldown_s"
    with pytest.raises(ProfileError):
        _parse(d)
    d = _profile_dict()
    d["rules"][0]["high"] = d["rules"][0]["low"]
    with pytest.raises(ProfileError):
        _parse(d)
    d = _profile_dict()
    d["rules"][0]["basis"] = "p95"
    with pytest.raises(ProfileError):
        _parse(d)


def test_id_must_match_the_fields() -> None:
    with pytest.raises(ProfileError):
        _parse(_profile_dict(label_minutes=60))


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in [k, *_strings(v)]]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def test_profile_files_hold_only_ids_and_numbers() -> None:
    allowed = {
        *E.SPORTS,
        *E.CLASS_TYPES,
        *E.EVIDENCES,
        *E.BASES,
        *E.SEVERITIES,
        *E.METRICS,
        *E.RULE_IDS,
        *E.RATIONALE_IDS,
        *E.BRIEF_OVERRIDES,
        E.SOURCE,
    }
    keys = set(P.Profile.model_fields) | set(P.ProfileRule.model_fields) | {"schema"}
    for f in PROFILES.glob("cycling/*.json"):
        d = json.loads(f.read_text())
        for s in _strings(d):
            if s in allowed or s in keys:
                continue
            assert re.fullmatch(r"cycling\.[a-z_]+\.\d+|\d{4}-\d{2}-\d{2}", s), (f.name, s)
        for r in d["rules"]:
            for v in (r["low"], r["high"]):
                assert v is None or isinstance(v, int | float)


def test_enums_and_rationales_are_consistent() -> None:
    assert set(E.RULE_SPECS) == set(E.RULE_IDS)
    assert {s.metric for s in E.RULE_SPECS.values()} <= set(E.METRICS)
    assert set(RATIONALES) == set(E.RATIONALE_IDS)
    for sentence in RATIONALES.values():
        assert sentence.endswith(".")
        assert not re.search(r"\d", sentence)
        assert len(sentence) <= 100
    used = {r.rationale_id for p in load_registry().profiles.values() for r in p.rules}
    assert used <= set(RATIONALES)


@pytest.mark.parametrize(
    ("class_type", "length", "expected"),
    [
        ("interval", 45, "cycling.interval.45"),
        ("plain", 30, "cycling.plain.30"),
        ("plain", 60, "cycling.plain.60"),
        ("interval", 42, "cycling.interval.45"),  # exactly 3 minutes
        ("interval", 48, "cycling.interval.45"),
        ("interval", 41.9, None),
        ("interval", 48.1, None),
        ("plain", 33, "cycling.plain.30"),
        ("plain", 37, None),
        ("plain", 57, "cycling.plain.60"),
        ("plain", 63, "cycling.plain.60"),
        ("plain", 64, None),
        ("plain", 75, None),  # 75 ships nothing
        ("plain", 72, None),
        ("plain", 20, None),
        ("plain", 0, None),
        ("plain", -5, None),
        ("plain", float("nan"), None),
        ("plain", float("inf"), None),
        ("hiit", 30, None),
        ("journey", 45, None),
        ("ftp_test", 60, None),
        ("unknown", 45, None),
    ],
)
def test_select_profile_within_three_minutes(
    class_type: str, length: float, expected: str | None
) -> None:
    got = select_profile(class_type, length)
    assert (got.id if got else None) == expected


def test_select_profile_other_sport_is_none() -> None:
    assert select_profile("plain", 45, sport="running") is None
    assert select_profile("plain", 45, sport="cycling") is not None


def test_rules_package_imports_neither_agent_nor_database() -> None:
    banned = ("classbeat.agent", "classbeat.db", "sqlalchemy", "psycopg", "classbeat.jobs")
    for f in RULES_PKG.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(banned), (f.name, n)
