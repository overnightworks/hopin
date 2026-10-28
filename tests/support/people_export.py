"""Asking HopIn for a person's export the way an admin's browser does, and reading what it answers."""

import io
import json
import zipfile

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
