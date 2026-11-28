import json

import pytest

from tests.stage.support.tables import entry

pytestmark = pytest.mark.usefixtures("game_environment", "logging_restored")

SENTINEL_ORIGIN = "https://sentinel.example"
SENTINEL_PATH = "/sentinel/{context}"
MALFORMED_REGISTRIES = {
    "not json": "sentinel[",
    "not a list": json.dumps({"sentinel": 1}),
    "an entry that is no object": json.dumps(["sentinel"]),
    "a missing field": json.dumps([{"name": "sentinel", "title": "T", "origin": SENTINEL_ORIGIN}]),
    "an unknown field": json.dumps([entry(origin=SENTINEL_ORIGIN, sentinel="x")]),
    "a name with a capital": json.dumps([entry(name="Sentinel", origin=SENTINEL_ORIGIN)]),
    "a name with a digit first": json.dumps([entry(name="1sentinel", origin=SENTINEL_ORIGIN)]),
    "a name of 33 characters": json.dumps([entry(name="s" * 33, origin=SENTINEL_ORIGIN)]),
    "an empty title": json.dumps([entry(title="", origin=SENTINEL_ORIGIN)]),
    "a repeated name": json.dumps([entry(name="sentinel"), entry(name="sentinel", origin=SENTINEL_ORIGIN)]),
    "a repeated origin": json.dumps([entry(origin=SENTINEL_ORIGIN), entry(name="chess", origin=SENTINEL_ORIGIN)]),
    "a second entry that is malformed": json.dumps([entry(), entry(name="chess", origin="http://sentinel.example")]),
}
MALFORMED_ORIGINS = [
    "http://sentinel.example",
    "https://sentinel.example/",
    "https://sentinel.example/t",
    "https://sentinel.example:443",
    "https://SENTINEL.example",
    "https://sentinel.example.",
    "https://user@sentinel.example",
    "https://sentinel.example?x=1",
    "https://sentinel.example#x",
    "https://sentinel.exämple",
    "https://sentinel..example",
    "https://sent'inel.example",
    "https://",
    "https://:8443",
    "sentinel.example",
    "",
]
MALFORMED_PATHS = [
    "sentinel/{context}",
    "/sentinel",
    "/sentinel/{context}/{context}",
    "/sentinel/{context}?x=1",
    "/sentinel/{context}#x",
    "/sentinel/../{context}",
    "/sentinel/./{context}",
    "/sentinel/{context}/..",
    "",
]
REFUSED = "HOPIN_STAGE_APPS is malformed"


@pytest.mark.parametrize("value", MALFORMED_REGISTRIES.values(), ids=MALFORMED_REGISTRIES.keys())
def test_refuses_a_malformed_registry_in_one_line_naming_the_setting_and_never_its_value(serve_refusal, value):
    """ST-13."""
    serve_refusal("HOPIN_STAGE_APPS", value, REFUSED)


@pytest.mark.parametrize("origin", MALFORMED_ORIGINS)
def test_refuses_an_entry_with_a_malformed_origin_in_one_line_never_echoing_it(serve_refusal, origin):
    """ST-13."""
    serve_refusal("HOPIN_STAGE_APPS", json.dumps([entry(origin=origin, title="sentinel")]), REFUSED)


@pytest.mark.parametrize("path", MALFORMED_PATHS)
def test_refuses_an_entry_with_a_malformed_frame_path_in_one_line_never_echoing_it(serve_refusal, path):
    """ST-13."""
    serve_refusal("HOPIN_STAGE_APPS", json.dumps([entry(frame_path=path, title="sentinel")]), REFUSED)
