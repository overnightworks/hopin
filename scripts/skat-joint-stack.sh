#!/usr/bin/env bash
# Start the joint drive of HopIn and Skat on one machine, behind HTTPS, and keep it up until it is stopped:
#
#   scripts/skat-joint-stack.sh [--smoke] <skat checkout> <scratch directory>
#
# Three hosts resolve to loopback and share the site drive.localhost: hopin.drive.localhost (this checkout's
# harness, signed-in people seeded), skat.drive.localhost (the checkout's real `skat serve` behind a TLS terminator,
# on its own throwaway Postgres) and auth.drive.localhost (the drive's own issuer). No real realm or live stack is
# touched. The URLs, the people's cookies (`hopin-people.json`) and the pids are written to the scratch directory;
# every secret is generated there and never printed. With --smoke the stack checks that the three hosts answer over
# HTTPS and that a HopIn page loads, then stops.
#
# The script runs itself under the probe-stack lock and the repository-wide cap: the 1800 s cap and a 30 s grace,
# which the teardown below (processes by pid, the container) stays within.
set -euo pipefail

CAP_SECONDS=1800
GRACE_SECONDS=30
READY_WAIT_SECONDS=120

if [ -z "${HOPIN_JOINT_CAPPED:-}" ]; then
    HOPIN_JOINT_CAPPED=1 exec flock -o -w 3600 /tmp/probe-stack.lock \
        setpriv --pdeathsig TERM timeout -k "${GRACE_SECONDS}" "${CAP_SECONDS}" "$0" "$@"
fi

smoke=0
if [ "${1:-}" = "--smoke" ]; then
    smoke=1
    shift
fi
if [ "$#" -ne 2 ]; then
    echo "usage: $0 [--smoke] <skat checkout> <scratch directory>" >&2
    exit 2
fi
skat=$(realpath "$1")
scratch=$(realpath -m "$2")
hopin=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "${scratch}"
chmod 700 "${scratch}"
pids_file="${scratch}/pids"
: >"${pids_file}"
container="hopin-joint-postgres-$$"

teardown() {
    trap - EXIT TERM
    local pid
    while read -r pid; do
        kill "${pid}" 2>/dev/null || true
    done <"${pids_file}"
    docker rm --force "${container}" >/dev/null 2>&1 || true
    local waited=0
    while [ "${waited}" -lt "${GRACE_SECONDS}" ] && [ -s "${pids_file}" ]; do
        local alive=0
        while read -r pid; do kill -0 "${pid}" 2>/dev/null && alive=1; done <"${pids_file}"
        [ "${alive}" -eq 0 ] && break
        sleep 1
        waited=$((waited + 1))
    done
    while read -r pid; do kill -9 "${pid}" 2>/dev/null || true; done <"${pids_file}"
}
trap teardown EXIT TERM

start() { # start <name> <command...>: a child in the background, its pid kept for the teardown
    local name=$1
    shift
    "$@" >"${scratch}/${name}.log" 2>&1 &
    echo "$!" >>"${pids_file}"
}

free_port() {
    python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

wait_for() { # wait_for <description> <command...>
    local description=$1 waited=0
    shift
    until "$@" >/dev/null 2>&1; do
        waited=$((waited + 1))
        if [ "${waited}" -ge "${READY_WAIT_SECONDS}" ]; then
            echo "${description} did not come up; see ${scratch}/*.log" >&2
            exit 1
        fi
        sleep 1
    done
}

skat_port=$(free_port)
skat_tls_port=$(free_port)
issuer_port=$(free_port)
skat_origin="https://skat.drive.localhost:${skat_tls_port}"
issuer_url="https://auth.drive.localhost:${issuer_port}/realms/drive"
hopin_python="${hopin}/.venv/bin/python"

# One self-signed certificate for the three hosts; Skat trusts it for its issuer calls through SSL_CERT_FILE.
PYTHONPATH="${hopin}" "${hopin_python}" - "${scratch}" <<'PY'
import sys
from pathlib import Path

from tests.e2e.support.server import write_self_signed_certificate

directory = Path(sys.argv[1])
key, certificate = write_self_signed_certificate(
    directory, ("hopin.drive.localhost", "skat.drive.localhost", "auth.drive.localhost")
)
key.rename(directory / "drive-key.pem")
certificate.rename(directory / "drive-ca.pem")
PY
drive_key="${scratch}/drive-key.pem"
drive_ca="${scratch}/drive-ca.pem"

umask 077
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' >"${scratch}/skat-client-secret"
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' >"${scratch}/skat-session-secret"
python3 -c 'import secrets; print(secrets.token_urlsafe(24))' >"${scratch}/skat-database-password"

# Skat's own tooling, installed and built inside its checkout only.
(cd "${skat}" && uv sync --all-extras && make frontend) >"${scratch}/skat-install.log" 2>&1

image=$(sed -n "s/^IMAGE='\(.*\)'$/\1/p" "${skat}/scripts/test_postgres.sh")
docker run --quiet --detach --name "${container}" \
    --env POSTGRES_HOST_AUTH_METHOD=trust \
    --publish 127.0.0.1::5432 \
    --tmpfs /var/lib/postgresql \
    "${image}" >/dev/null
wait_for "Postgres" docker exec "${container}" pg_isready --username postgres
postgres_port=$(docker port "${container}" 5432/tcp | head -n 1 | sed 's/.*://')

cat >"${scratch}/run_hopin.py" <<'PY'
"""HopIn on hopin.drive.localhost with signed-in people seeded; writes their cookies and its URL, then serves."""

import json
import signal
import sys
from pathlib import Path

from webauth.admission import AppRole
from webauth.ports import AccountKind

from hopin.agent.feature import GameFeature
from hopin.skat.feature import SkatFeature
from tests.e2e.support.people import _playwright_cookie
from tests.e2e.support.server import loopback_sign_in, running_hopin
from tests.game.support.fake_agent_backend import FakeAgentBackend
from tests.support.signed_in import seed_member

HOST = "hopin.drive.localhost"
PEOPLE = ("ada", "ben", "cleo", "dora", "emil", "fina")
directory, skat_origin = Path(sys.argv[1]), sys.argv[2]
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
        cookies[name] = [_playwright_cookie(header, server.config, HOST) for header in member.set_cookie_headers]
    (directory / "hopin-people.json").write_text(json.dumps({"baseUrl": server.base_url, "people": cookies}))
    signal.pause()
PY
rm -f "${scratch}/hopin-people.json"
start hopin env PYTHONPATH="${hopin}" "${hopin_python}" "${scratch}/run_hopin.py" "${scratch}" "${skat_origin}"
wait_for "HopIn" test -s "${scratch}/hopin-people.json"
hopin_origin=$("${hopin_python}" -c 'import json, sys; print(json.load(open(sys.argv[1]))["baseUrl"])' "${scratch}/hopin-people.json")

start issuer env PYTHONPATH="${hopin}" "${hopin_python}" -m tests.e2e.support.drive_issuer \
    --port "${issuer_port}" --certificate "${drive_ca}" --key "${drive_key}" --issuer "${issuer_url}" \
    --client-id skat --client-secret-file "${scratch}/skat-client-secret"

skat_environment=(
    SKAT_DATABASE_URL="postgresql://postgres@127.0.0.1:${postgres_port}/postgres"
    SKAT_DATABASE_PASSWORD_FILE="${scratch}/skat-database-password"
    SKAT_OIDC_ISSUER="${issuer_url}"
    SKAT_OIDC_CLIENT_SECRET_FILE="${scratch}/skat-client-secret"
    SKAT_PUBLIC_ORIGIN="${skat_origin}"
    SKAT_CALL_ORIGIN="${hopin_origin}"
    SKAT_SESSION_SECRET_FILE="${scratch}/skat-session-secret"
    SKAT_HOST=127.0.0.1
    SKAT_PORT="${skat_port}"
    SSL_CERT_FILE="${drive_ca}"
)
(cd "${skat}" && env "${skat_environment[@]}" .venv/bin/skat migrate) >"${scratch}/skat-migrate.log" 2>&1
(cd "${skat}" && start skat env "${skat_environment[@]}" .venv/bin/skat serve)
start terminator "${hopin_python}" "${hopin}/tests/e2e/support/tls_terminator.py" \
    --listen-port "${skat_tls_port}" --target-port "${skat_port}" --certificate "${drive_ca}" --key "${drive_key}"

hosts() { echo "hopin.drive.localhost:${hopin_origin##*:} skat.drive.localhost:${skat_tls_port} auth.drive.localhost:${issuer_port}"; }
answers() { # answers <host:port> <path>: the HTTP status over HTTPS, trusting only the drive certificate
    curl --silent --output /dev/null --write-out '%{http_code}' --cacert "${drive_ca}" \
        --resolve "$1:127.0.0.1" "https://$1$2"
}
answers_with() { # answers_with <host:port> <path> <status>: the status is exactly the one given
    [ "$(answers "$1" "$2")" = "$3" ]
}
wait_for "the issuer" answers_with "auth.drive.localhost:${issuer_port}" /realms/drive/protocol/openid-connect/certs 200
wait_for "Skat behind its terminator" answers_with "skat.drive.localhost:${skat_tls_port}" /health 200
session=$("${hopin_python}" -c 'import json, sys; print(json.load(open(sys.argv[1]))["people"]["ada"][0]["value"])' "${scratch}/hopin-people.json")
page_status=$(curl --silent --output "${scratch}/hopin-page.html" --write-out '%{http_code}' --cacert "${drive_ca}" \
    --resolve "hopin.drive.localhost:${hopin_origin##*:}:127.0.0.1" --cookie "__Host-session_id=${session}" "${hopin_origin}/")

approve_page=$(curl --silent --cacert "${drive_ca}" --resolve "auth.drive.localhost:${issuer_port}:127.0.0.1" \
    "https://auth.drive.localhost:${issuer_port}/realms/drive/protocol/openid-connect/auth?client_id=skat")

cat >"${scratch}/ready.json" <<JSON
{"hopin": "${hopin_origin}", "skat": "${skat_origin}", "issuer": "${issuer_url}", "people": "${scratch}/hopin-people.json"}
JSON
echo "hosts: $(hosts)"
echo "issuer certs: $(answers "auth.drive.localhost:${issuer_port}" /realms/drive/protocol/openid-connect/certs)"
echo "skat health: $(answers "skat.drive.localhost:${skat_tls_port}" /health), table page: $(answers "skat.drive.localhost:${skat_tls_port}" /t/00000000000000000000000000000000)"
echo "issuer /auth: $(grep -o '<title>[^<]*' <<<"${approve_page}" | head -n 1) approve buttons: $(grep -o 'id="approve-' <<<"${approve_page}" | wc -l)"
echo "hopin page: ${page_status} $(grep -o '<title>[^<]*' "${scratch}/hopin-page.html" | head -n 1)"
echo "ready: ${scratch}/ready.json"
if [ "${smoke}" -eq 1 ]; then
    [ "${page_status}" = 200 ] && grep -q 'id="approve-member"' <<<"${approve_page}"
    exit 0
fi
sleep infinity # until the cap or a signal stops the stack
