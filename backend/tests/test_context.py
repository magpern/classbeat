import dataclasses
import uuid

import pytest

from classbeat.context import UserContext

USER = uuid.uuid4()
CREDENTIAL = uuid.uuid4()


def test_a_context_is_immutable_and_hashable() -> None:
    def build() -> UserContext:
        return UserContext(
            user_id=USER,
            role="user",
            entitlements=frozenset({"pro"}),
            connections=frozenset({("mowl", CREDENTIAL)}),
        )

    ctx, same = build(), build()
    assert hash(ctx) == hash(same)
    assert ctx == same
    assert len({ctx, same}) == 1
    assert ctx.connection("mowl") == CREDENTIAL
    assert ctx.connection("spotify") is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.role = "admin"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises((AttributeError, TypeError)):  # slots + frozen
        ctx.extra = 1  # pyright: ignore[reportAttributeAccessIssue]


def test_defaults_are_empty() -> None:
    ctx = UserContext(user_id=USER, role="admin")
    assert ctx.entitlements == frozenset()
    assert ctx.connections == frozenset()
    assert isinstance(hash(ctx), int)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"user_id": str(USER), "role": "user"}, TypeError),
        ({"user_id": USER, "role": "root"}, ValueError),
        ({"user_id": USER, "role": "user", "entitlements": {"pro"}}, TypeError),
        ({"user_id": USER, "role": "user", "connections": {"mowl": CREDENTIAL}}, TypeError),
        (
            {
                "user_id": USER,
                "role": "user",
                "connections": frozenset({("mowl", CREDENTIAL), ("mowl", uuid.uuid4())}),
            },
            ValueError,
        ),
    ],
)
def test_invalid_contexts_are_refused(kwargs: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        UserContext(**kwargs)  # pyright: ignore[reportArgumentType]
