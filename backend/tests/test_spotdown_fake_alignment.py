"""The in-process fake spotdown's cache hit with a reference (contract v2, "aligned analysis"):
a hit that has no alignment yet is aligned to the reference and returned as itself (the same
id, no new analysis); one already aligned to another reference gets a new analysis."""

from spotdowncore import FakeSpotdown

SOURCE = "sha256:" + "ab" * 32
ONE = {"kind": "spotify", "id": "one", "duration_s": 200.0}
OTHER = {"kind": "spotify", "id": "two", "duration_s": 240.0}


def hit(fake: FakeSpotdown, held: str, reference: dict[str, object]) -> str:
    return fake._aligned_hit(held, SOURCE, dict(reference))  # pyright: ignore[reportPrivateUsage]


def test_an_unaligned_hit_returns_the_hit_itself() -> None:
    fake = FakeSpotdown()
    held = fake.add_analysis(None, SOURCE)
    assert held not in fake.aligned
    assert hit(fake, held, ONE) == held
    assert fake.aligned[held] == ONE
    assert list(fake.analyses) == [held]  # no new analysis


def test_a_hit_aligned_to_another_reference_gets_a_new_analysis_and_the_old_one_stays() -> None:
    fake = FakeSpotdown()
    held = fake.add_analysis(None, SOURCE)
    hit(fake, held, ONE)
    made = hit(fake, held, OTHER)
    assert made != held
    assert (fake.aligned[held], fake.aligned[made]) == (ONE, OTHER)
    assert hit(fake, held, OTHER) == made  # asked again: the one already aligned to it
    assert hit(fake, held, ONE) == held
