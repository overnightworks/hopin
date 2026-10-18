from dataclasses import dataclass, field

import pytest

from hopin.rooms.models import Account
from hopin.signaling.identity import QueryIdIdentityResolver


@dataclass
class FakeHandshake:
    query_params: dict[str, str] = field(default_factory=dict)


class TestQueryIdIdentityResolver:
    @pytest.mark.asyncio
    async def test_resolve_with_id_answers_account_named_by_the_value(self):
        account = await QueryIdIdentityResolver().resolve(FakeHandshake({"id": "alice"}))
        assert account == Account(id="alice", name="alice")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query_params", [{}, {"id": ""}], ids=["missing", "empty"])
    async def test_resolve_without_usable_id_refuses(self, query_params):
        assert await QueryIdIdentityResolver().resolve(FakeHandshake(query_params)) is None
