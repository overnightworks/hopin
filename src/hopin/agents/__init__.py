from typing import TYPE_CHECKING, Final

import httpx2

from hopin.agents.game_master import AgentServiceGameMaster, AgentTransport, silence_transport_logs
from hopin.agents.tokens import StarterTokens

if TYPE_CHECKING:
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

# No retries: a run is started once, never sent again behind the person's back.
AGENT_SERVICE_OVER_HTTP: Final[AgentTransport] = httpx2.AsyncHTTPTransport


def agent_service_game_master(
    agent_service_url: str,
    *,
    relying_party: OidcRelyingParty,
    sessions: SessionRecordStore,
    web_auth: WebAuthConfig,
    transport: AgentTransport,
) -> AgentServiceGameMaster:
    """Build the game master; as a side effect, silence the HTTP and sign-in loggers that could write a token."""
    silence_transport_logs()
    return AgentServiceGameMaster(
        agent_service_url=agent_service_url,
        tokens=StarterTokens(relying_party, sessions, web_auth),
        transport=transport,
    )
