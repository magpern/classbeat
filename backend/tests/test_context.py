import copy
import dataclasses
import pickle
import uuid

import pytest

from classbeat.context import _ISSUER, UserContext, issuer_token  # pyright: ignore[reportPrivateUsage]
from ctx import make_context

USER = uuid.uuid4()
CREDENTIAL = uuid.uuid4()


def build() -> UserContext:
    return make_context(USER, "user", entitlements={"pro"}, connections={("mowl", CREDENTIAL)})


def test_a_context_is_immutable_and_hashable() -> None:
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
    ctx = make_context(USER, "admin")
    assert ctx.entitlements == frozenset()
    assert ctx.connections == frozenset()
    assert isinstance(hash(ctx), int)


def test_the_issuer_token_is_the_module_private_one() -> None:
    assert issuer_token() is _ISSUER


FORGERIES = {
    "direct call": lambda ctx: UserContext(user_id=uuid.uuid4(), role="admin"),
    "type(ctx)(...)": lambda ctx: type(ctx)(user_id=uuid.uuid4(), role="admin"),
    "ctx.__class__(...)": lambda ctx: ctx.__class__(uuid.uuid4(), "admin"),
    "dataclasses.replace": lambda ctx: dataclasses.replace(ctx, user_id=uuid.uuid4()),
    "wrong token": lambda ctx: UserContext(uuid.uuid4(), "admin", issuer=object()),
    "token None": lambda ctx: UserContext(uuid.uuid4(), "admin", issuer=None),
}


@pytest.mark.parametrize("forge", FORGERIES.values(), ids=FORGERIES.keys())
def test_a_context_cannot_be_built_or_copied_without_the_issuer_token(forge) -> None:
    with pytest.raises(PermissionError, match="issued by"):
        forge(build())


def test_the_token_is_not_a_field_and_not_stored() -> None:
    ctx = build()
    assert "issuer" not in {f.name for f in dataclasses.fields(ctx)}
    assert getattr(ctx, "issuer", None) is None  # InitVar: the class default, never the token
    assert copy.copy(ctx) == ctx  # an unchanged copy is harmless
    assert pickle.loads(pickle.dumps(ctx)) == ctx  # noqa: S301 - our own test object


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
        UserContext(issuer=_ISSUER, **kwargs)  # pyright: ignore[reportArgumentType]
