"""Asking HopIn for a person's export the way an admin's browser does, and reading what it answers."""

import io
import json
import zipfile

from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import handshake_headers


def export_path(subject):
    return f"/api/admin/people/{subject}/export"


def withheld_counts(export):
    return {entry["kind"]: entry["count"] for entry in export["withheld"]}


def read_archive(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist(), json.loads(archive.read("export.json"))


def ask(client, subject, member=None, *, method="GET", headers=None):
    return client.request(
        method, export_path(subject), headers={**handshake_headers(member, origin=None), **(headers or {})}
    )


def erase(client, subject, member=None, *, dry_run=False, origin=TEST_PUBLIC_ORIGIN, headers=None):
    """Asks for a person's erasure the way an admin's page would: a state-changing request from the page's origin."""
    return client.delete(
        f"/api/admin/people/{subject}",
        params={"dry_run": "true"} if dry_run else None,
        headers={**handshake_headers(member, origin=origin), **(headers or {})},
    )
