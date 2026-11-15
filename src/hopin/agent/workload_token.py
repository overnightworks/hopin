import asyncio
import logging
from typing import TYPE_CHECKING, Final

from logline import StepTimer
from webauth.oidc import OidcRefusal

from hopin.agent.issuer_error import refusal_reason

if TYPE_CHECKING:
    from pydantic import SecretStr
    from webauth.oidc import OidcRelyingParty

_STEPS: Final = logging.getLogger(__name__)


async def workload_token(relying_party: OidcRelyingParty, scope: str) -> SecretStr | OidcRefusal:
    """HopIn's own token for a service's scope, asked of the issuer on a worker thread; a refusal ends the step
    `refused` with the issuer's closed error code."""
    async with StepTimer("workload_token.ended", _STEPS) as step:
        granted = await asyncio.to_thread(relying_party.workload_access_token, scopes=(scope,))
        if isinstance(granted, OidcRefusal):
            step.refuse(refusal_reason(granted))
    return granted
