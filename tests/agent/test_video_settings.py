import secrets

import pytest
from pydantic import ValidationError

from hopin.agent.video_settings import VideoSettings
from tests.support.settings_refusal import assert_refused_without_echo

pytestmark = pytest.mark.integration

VIDEO_ENVIRONMENT = {
    "HOPIN_VIDEO__ORIGIN": "http://video.test:8100",
    "HOPIN_VIDEO__AUDIENCE_SCOPE": "scenario-video-audience",
}
SECRET_SHAPED = f"not-a-value-{secrets.token_hex(8)}"
MALFORMED_SETTINGS = [
    pytest.param("HOPIN_VIDEO__ORIGIN", f"http://video.test:8100/{SECRET_SHAPED}", id="an-origin-with-a-path"),
    pytest.param("HOPIN_VIDEO__ORIGIN", f"https://{SECRET_SHAPED}.test", id="an-origin-over-https"),
    pytest.param("HOPIN_VIDEO__ORIGIN", f"http://video.test:{SECRET_SHAPED}", id="an-origin-with-a-bad-port"),
    pytest.param("HOPIN_VIDEO__AUDIENCE_SCOPE", f"two {SECRET_SHAPED}", id="a-scope-with-a-space"),
    pytest.param("HOPIN_VIDEO__AUDIENCE_SCOPE", "", id="an-empty-scope"),
]


@pytest.fixture
def video_environment(monkeypatch):
    for name, value in VIDEO_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_the_origin_and_the_audience_scope_load_from_their_environment_into_the_configured_service(
    video_environment,
):
    video = VideoSettings()

    assert (video.service.origin, video.service.audience_scope) == (
        VIDEO_ENVIRONMENT["HOPIN_VIDEO__ORIGIN"],
        VIDEO_ENVIRONMENT["HOPIN_VIDEO__AUDIENCE_SCOPE"],
    )


def test_without_an_origin_the_video_is_off(video_environment):
    for name in VIDEO_ENVIRONMENT:
        video_environment.delenv(name)

    video = VideoSettings()

    assert (video.origin, video.audience_scope, video.service) == (None, None, None)


def test_an_origin_without_an_audience_scope_fails_when_the_settings_load_and_names_the_setting(video_environment):
    video_environment.delenv("HOPIN_VIDEO__AUDIENCE_SCOPE")

    with pytest.raises(ValidationError) as refusal:
        VideoSettings()

    assert "HOPIN_VIDEO__AUDIENCE_SCOPE" in str(refusal.value)


@pytest.mark.parametrize(("name", "value"), MALFORMED_SETTINGS)
def test_a_malformed_video_setting_fails_when_the_settings_load_and_echoes_no_input(name, value, video_environment):
    video_environment.setenv(name, value)

    assert_refused_without_echo(VideoSettings, SECRET_SHAPED)
