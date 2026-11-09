"""HopIn of the joint drive on hopin.drive.localhost, with signed-in people seeded: it writes their cookies and its URL
to the scratch directory, then serves until it is stopped.

    python -m tests.e2e.support.drive_hopin <scratch directory> <skat origin>
"""

import json
import signal
import sys
from pathlib import Path

from webauth.admission import AppRole
from webauth.ports import AccountKind

from hopin.agent.feature import GameFeature
from hopin.skat.feature import SkatFeature
from tests.e2e.support.people import playwright_cookie
from tests.e2e.support.server import loopback_sign_in, running_hopin
from tests.game.support.fake_agent_backend import FakeAgentBackend
from tests.support.signed_in import seed_member

HOST = "hopin.drive.localhost"
PEOPLE = ("ada", "ben", "cleo", "dora", "emil", "fina")


def serve(directory: Path, skat_origin: str) -> None:
    sign_in_directory = directory / "hopin-sign-in"
    sign_in_directory.mkdir(exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with running_hopin(
        loopback_sign_in(sign_in_directory, HOST),
        directory,
        GameFeature(FakeAgentBackend()),
        participation=True,
        certificate=(directory / "drive-key.pem", directory / "drive-ca.pem"),
        more_call_features=(SkatFeature(skat_origin),),
    ) as server:
        cookies = {}
        for name in PEOPLE:
            account = server.stores.accounts.create_for_subject(
                f"subject-{name}", name, role=AppRole.USER, kind=AccountKind.PERSON
            )
            member = seed_member(server.stores, server.config, account=account)
            cookies[name] = [playwright_cookie(header, server.config, HOST) for header in member.set_cookie_headers]
        (directory / "hopin-people.json").write_text(json.dumps({"baseUrl": server.base_url, "people": cookies}))
        signal.pause()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2])
