import base64
import io
import re
import struct
import wave
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect

from hopin.config.constants import FILE_LIMITS, LINE_LIMITS
from hopin.words import text
from tests.e2e.support.calls import join, open_call, open_host, say_in_chat
from tests.e2e.support.media import RECORD_PEER_CONNECTIONS, WITHHOLD_RECEIPTS, wait_for_peer_count
from tests.e2e.support.page_assertions import LIST_IS_AT_THE_BOTTOM, MESSAGES_IN_A_LONG_HISTORY
from tests.e2e.support.people import DESKTOP, FORM_FACTORS
from tests.e2e.support.signaling import RECORD_SOCKET_FRAMES, SOCKET_FRAMES

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

SMILEY = "\N{GRINNING FACE}"
SENT_LINE = "#chatMessages .chat-msg.self"
HEARD_LINE = "#chatMessages .chat-msg.other"
LIST_FILLS_PAST_ITS_BOX = "(list) => list.scrollHeight > list.clientHeight"
LIST_IS_AT_THE_TOP = "(list) => list.scrollTop === 0"
SENT_FILE = "#chatMessages .chat-file.self"
HEARD_FILE = "#chatMessages .chat-file.other"
SHOWN_PICTURE = "#chatMessages .chat-picture"
PLAYER = "#chatMessages audio.chat-audio"
VIDEO_PLAYER = "#chatMessages video.chat-video"
LOAD_BUTTON = ".chat-file-load"
DOWNLOAD_BUTTON = ".chat-file-download"
BYTES_PER_MEGABYTE = 1024 * 1024
# The page's revoked blob URLs, so a test can prove that leaving releases what the chat held.
RECORD_REVOKED_URLS = """
window.revokedUrls = [];
const revokeObjectUrl = URL.revokeObjectURL.bind(URL);
URL.revokeObjectURL = (url) => { window.revokedUrls.push(url); revokeObjectUrl(url); };
"""
REVOKED_URLS = "() => window.revokedUrls"
IMAGE_IS_DECODED = "(image) => image.complete && image.naturalWidth > 0"
PLAYER_NEVER_STARTS = "(player) => !player.autoplay && player.paused && player.currentTime === 0"
# The type of every blob the page made a URL for, by URL: the content-security policy admits a blob as a media source,
# not as a fetch.
RECORD_BLOB_TYPES = """
window.blobTypes = {};
const createObjectUrl = URL.createObjectURL.bind(URL);
URL.createObjectURL = (blob) => { const url = createObjectUrl(blob); window.blobTypes[url] = blob.type; return url; };
"""
BLOB_TYPE_OF_PLAYER = "(player) => window.blobTypes[player.src]"
# A short WebM the page records from a canvas, as base64 text: the browser's own encoder makes a clip it can decode.
RECORD_A_SHORT_VIDEO = """
async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 48;
    const context = canvas.getContext("2d");
    const recorder = new MediaRecorder(canvas.captureStream(), { mimeType: "video/webm" });
    const parts = [];
    recorder.ondataavailable = (event) => parts.push(event.data);
    const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
    recorder.start();
    for (let step = 0; step < 6; step += 1) {
        context.fillStyle = `hsl(${step * 60}, 90%, 50%)`;
        context.fillRect(0, 0, 64, 48);
        await new Promise((resolve) => requestAnimationFrame(resolve));
    }
    recorder.stop();
    await stopped;
    const bytes = new Uint8Array(await new Blob(parts).arrayBuffer());
    return btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
}
"""


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_chat_with_a_long_history_scrolls_inside_the_panel_and_keeps_the_composer_in_view(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    """CALL-44: Chat with a long history scrolls inside the panel and keeps the composer in view."""
    _, guest, _ = open_call(open_person, base_url, page_errors, form_factor)
    guest.click("#chatBtn")
    for number in range(MESSAGES_IN_A_LONG_HISTORY):
        guest.fill("#chatInput", f"line {number}")
        guest.click("#chatSend")
    messages = guest.locator("#chatMessages")
    composer = guest.locator("#chatInput")
    viewport_height = guest.viewport_size["height"] if guest.viewport_size else 0

    expect(messages.locator(".chat-msg")).to_have_count(MESSAGES_IN_A_LONG_HISTORY)
    assert messages.evaluate(LIST_FILLS_PAST_ITS_BOX)
    assert messages.evaluate(LIST_IS_AT_THE_BOTTOM)
    assert composer.bounding_box()["y"] + composer.bounding_box()["height"] <= viewport_height

    messages.evaluate("(list) => { list.scrollTop = 0; }")

    assert messages.evaluate(LIST_IS_AT_THE_TOP)
    expect(messages.locator(".chat-msg").first).to_be_in_viewport()
    expect(composer).to_be_in_viewport()


def test_chat_sends_the_typed_message_when_enter_is_pressed(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")

    guest.fill("#chatInput", "sent with enter")
    guest.press("#chatInput", "Enter")

    expect(guest.locator("#chatMessages .chat-msg.self")).to_have_text("sent with enter")
    expect(guest.locator("#chatInput")).to_have_value("")


def test_chat_panel_closes_from_its_close_button_found_by_name(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")
    expect(guest.locator("#chatPanel")).to_have_class("chat-panel visible")

    guest.get_by_role("button", name=text("de", "chat.closeButton")).click()

    expect(guest.locator("#chatPanel")).not_to_have_class("chat-panel visible")


def _three_people(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> tuple[Page, Page, Page, str]:
    host, link = open_host(open_person, base_url, page_errors, DESKTOP, RECORD_SOCKET_FRAMES)
    scripts = (RECORD_PEER_CONNECTIONS, RECORD_SOCKET_FRAMES)
    guest = join(open_person, link, DESKTOP, scripts, page_errors)
    third = join(open_person, link, DESKTOP, scripts, page_errors)
    for person in (host, guest, third):
        wait_for_peer_count(person, 2)
    return host, guest, third, link


def test_a_line_reaches_both_other_people_under_the_senders_name_and_no_socket_carries_its_text(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-71: A line reaches every other person over the call's own connections, so no signaling socket carries it."""
    host, guest, third, _ = _three_people(open_person, base_url, page_errors)
    line = "only between the browsers"

    say_in_chat(guest, line)

    for reader in (host, third):
        reader.click("#chatBtn")
        expect(reader.locator(HEARD_LINE)).to_contain_text(line)
        expect(reader.locator(f"{HEARD_LINE} .chat-sender")).not_to_be_empty()
    expect(guest.locator(SENT_LINE)).to_have_text(line)
    expect(guest.locator(f"{SENT_LINE}.pending")).to_have_count(0)
    for person in (host, guest, third):
        assert line not in person.evaluate(SOCKET_FRAMES)


def test_a_person_who_joins_later_reads_none_of_the_earlier_lines(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-71: A line reaches the people the sender's page shows when it is sent, and a later joiner reads none."""
    host, guest, link = open_call(open_person, base_url, page_errors, DESKTOP)
    say_in_chat(host, "before the third")
    expect(guest.locator(HEARD_LINE)).to_have_count(1)

    late = join(open_person, link, DESKTOP, (RECORD_PEER_CONNECTIONS,), page_errors)
    wait_for_peer_count(late, 2)
    say_in_chat(guest, "after the third")

    late.click("#chatBtn")
    expect(late.locator(HEARD_LINE)).to_have_text([re.compile("after the third$")])


def test_the_composer_sends_a_line_at_the_limit_and_says_why_one_over_it_is_not_sent(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-71: A line holds 1 to 600 characters; the composer says why a longer draft is not sent."""
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")
    at_the_limit = SMILEY * LINE_LIMITS.max_characters
    over_the_limit = SMILEY * (LINE_LIMITS.max_characters + 1)

    guest.fill("#chatInput", at_the_limit)
    expect(guest.locator("#chatDraftProblem")).to_have_count(0)
    guest.click("#chatSend")
    expect(guest.locator(SENT_LINE)).to_have_text(at_the_limit)

    guest.fill("#chatInput", over_the_limit)
    guest.click("#chatSend")

    expect(guest.locator("#chatDraftProblem")).to_have_text(
        text("de", "chat.lineTooLong", max=str(LINE_LIMITS.max_characters))
    )
    expect(guest.locator("#chatInput")).to_have_value(over_the_limit)
    expect(guest.locator(SENT_LINE)).to_have_count(1)


EMOJI_LINE = " ".join(
    (
        "\N{GRINNING FACE}",
        "\N{FAMILY}\N{ZERO WIDTH JOINER}\N{GIRL}",
        "\N{THUMBS UP SIGN}\N{EMOJI MODIFIER FITZPATRICK TYPE-4}",
        "\N{REGIONAL INDICATOR SYMBOL LETTER D}\N{REGIONAL INDICATOR SYMBOL LETTER E}",
        "1\ufe0f\u20e3",
    )
)
# A family: man, woman and child joined by two zero width joiners, one glyph of five code points.
FAMILY = "\U0001f468\u200d\U0001f469\u200d\U0001f467"


def _insert_as_a_keyboard_does(person: Page, text: str) -> None:
    person.focus("#chatInput")
    person.keyboard.insert_text(text)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_line_of_emoji_inserted_as_a_system_keyboard_does_arrives_unchanged_and_the_send_button_stays_whole(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], form_factor: dict[str, Any]
) -> None:
    """CALL-75: Smileys come from the device's emoji keyboard: text it inserts into the composer arrives unchanged."""
    host, guest, _ = open_call(open_person, base_url, page_errors, form_factor)
    guest.click("#chatBtn")

    _insert_as_a_keyboard_does(guest, EMOJI_LINE)
    guest.click("#chatSend")

    host.click("#chatBtn")
    expect(host.locator(HEARD_LINE)).to_have_text(re.compile(f"{re.escape(EMOJI_LINE)}$"))
    expect(guest.locator(SENT_LINE)).to_have_text(EMOJI_LINE)
    panel = guest.locator("#chatPanel").bounding_box()
    send = guest.locator("#chatSend").bounding_box()
    assert panel["x"] <= send["x"]
    assert send["x"] + send["width"] <= panel["x"] + panel["width"]
    expect(guest.locator("#chatSend")).to_be_in_viewport(ratio=1)


def test_the_composer_counts_an_emoji_line_by_code_points_and_refuses_one_over_the_limit_without_cutting_it(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-75: A line of joined emoji at the limit is sent whole; one code point over is refused, never cut."""
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")
    at_the_limit = FAMILY * (LINE_LIMITS.max_characters // len(FAMILY))
    over_the_limit = at_the_limit + SMILEY

    _insert_as_a_keyboard_does(guest, at_the_limit)
    guest.click("#chatSend")
    expect(guest.locator(SENT_LINE)).to_have_text(at_the_limit)

    _insert_as_a_keyboard_does(guest, over_the_limit)
    guest.click("#chatSend")

    expect(guest.locator("#chatDraftProblem")).to_have_text(
        text("de", "chat.lineTooLong", max=str(LINE_LIMITS.max_characters))
    )
    expect(guest.locator("#chatInput")).to_have_value(over_the_limit)


def _png() -> bytes:
    """A small picture the browser decodes."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    side = 8
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * side for _ in range(side))
    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _wav() -> bytes:
    """A short song the browser decodes."""
    sound = io.BytesIO()
    with wave.open(sound, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00" * 1600)
    return sound.getvalue()


def _choose(person: Page, files: Any) -> None:
    """Pick the files the way a person does: the attach button opens the picker."""
    if not person.locator("#chatPanel.visible").count():
        person.click("#chatBtn")
    with person.expect_file_chooser() as picker:
        person.click("#chatAttach")
    picker.value.set_files(files)


def _post(person: Page, name: str, content: bytes, mime_type: str) -> None:
    _choose(person, {"name": name, "mimeType": mime_type, "buffer": content})


def test_a_posted_picture_shows_in_both_other_pages_once_its_bytes_pass_the_raster_check_and_no_socket_carries_it(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-72: A picture reaches every other person between the browsers and shows after the raster check."""
    host, guest, third, _ = _three_people(open_person, base_url, page_errors)
    name = "only-between-the-browsers.png"

    _post(guest, name, _png(), "image/png")

    for reader in (host, third):
        reader.click("#chatBtn")
        picture = reader.locator(SHOWN_PICTURE)
        expect(picture).to_be_visible()
        assert picture.evaluate(IMAGE_IS_DECODED)
        expect(reader.locator(f"{HEARD_FILE} .chat-file-name")).to_contain_text(name)
    expect(guest.locator(SHOWN_PICTURE)).to_be_visible()
    expect(guest.locator(f"{SENT_FILE}.pending")).to_have_count(0)
    for person in (host, guest, third):
        assert name not in person.evaluate(SOCKET_FRAMES)


def test_a_file_that_is_no_picture_waits_for_a_tap_and_downloads_byte_for_byte_under_its_name(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-72: Any other file is fetched on a tap and offered only as a download under its name."""
    host, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    content = bytes(range(256)) * 800
    host.click("#chatBtn")

    _post(guest, "report.pdf", content, "application/pdf")

    expect(host.locator(f"{HEARD_FILE} {LOAD_BUTTON}")).to_be_visible()
    expect(host.locator(SHOWN_PICTURE)).to_have_count(0)
    host.click(f"{HEARD_FILE} {LOAD_BUTTON}")
    with host.expect_download() as download:
        host.click(f"{HEARD_FILE} {DOWNLOAD_BUTTON}")
    assert download.value.suggested_filename == "report.pdf"
    assert Path(download.value.path()).read_bytes() == content
    expect(guest.locator(f"{SENT_FILE}.pending")).to_have_count(0)


def test_a_file_called_a_picture_whose_bytes_are_not_one_is_never_shown_inline(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-72: A picture shows only when its first bytes are a raster picture, never an SVG."""
    host, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    host.click("#chatBtn")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>document.title = "ran"</script></svg>'

    _post(guest, "logo.png", svg, "image/png")

    expect(host.locator(f"{HEARD_FILE} {DOWNLOAD_BUTTON}")).to_be_visible()
    expect(host.locator(SHOWN_PICTURE)).to_have_count(0)
    expect(host).not_to_have_title("ran")


@pytest.mark.parametrize(
    ("name", "clip_of", "player", "blob_type"),
    [
        ("song.wav", lambda _guest: _wav(), PLAYER, "audio/wav"),
        ("clip.webm", lambda guest: base64.b64decode(guest.evaluate(RECORD_A_SHORT_VIDEO)), VIDEO_PLAYER, "video/webm"),
    ],
    ids=["song", "video"],
)
def test_a_posted_song_or_video_waits_for_a_tap_and_then_plays_from_memory_without_starting_by_itself(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    name: str,
    clip_of: Callable[[Page], bytes],
    player: str,
    blob_type: str,
) -> None:
    """CALL-74: A song or video is fetched on a tap, plays with controls from a typed blob, never autoplays."""
    host, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP, RECORD_BLOB_TYPES)
    host.click("#chatBtn")

    _post(guest, name, clip_of(guest), "application/octet-stream")

    expect(host.locator(player)).to_have_count(0)
    host.click(f"{HEARD_FILE} {LOAD_BUTTON}")
    expect(host.locator(player)).to_be_visible()
    expect(host.locator(player)).to_have_attribute("controls", "")
    assert host.locator(player).evaluate(BLOB_TYPE_OF_PLAYER) == blob_type
    assert host.locator(player).evaluate(PLAYER_NEVER_STARTS)
    expect(guest.locator(player)).to_be_visible()


@pytest.mark.parametrize(
    ("name", "mime_type", "container_head", "player"),
    [
        ("broken.wav", "audio/wav", b"RIFF\x00\x00\x00\x00WAVE", PLAYER),
        ("broken.webm", "video/webm", b"\x1a\x45\xdf\xa3\x00\x00\x00\x00", VIDEO_PLAYER),
    ],
    ids=["song", "video"],
)
def test_a_song_or_video_the_browser_cannot_decode_is_offered_as_a_download_like_any_file(
    open_person: Callable[..., Page],
    base_url: str,
    page_errors: list[str],
    name: str,
    mime_type: str,
    container_head: bytes,
    player: str,
) -> None:
    """CALL-74: A file that passes the container check and fails to decode is offered as a download."""
    host, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    host.click("#chatBtn")
    broken = container_head + bytes(range(200))

    _post(guest, name, broken, mime_type)

    host.click(f"{HEARD_FILE} {LOAD_BUTTON}")
    expect(host.locator(f"{HEARD_FILE} {DOWNLOAD_BUTTON}")).to_be_visible()
    expect(host.locator(player)).to_have_count(0)
    with host.expect_download() as download:
        host.click(f"{HEARD_FILE} {DOWNLOAD_BUTTON}")
    assert Path(download.value.path()).read_bytes() == broken


def test_a_file_called_a_song_whose_bytes_are_not_one_is_never_played(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-74: A song plays only when its first bytes pass the container check, whatever its sender calls it."""
    host, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    host.click("#chatBtn")

    _post(guest, "song.mp3", b"<html><script>document.title = 'ran'</script></html>", "audio/mpeg")

    host.click(f"{HEARD_FILE} {LOAD_BUTTON}")
    expect(host.locator(f"{HEARD_FILE} {DOWNLOAD_BUTTON}")).to_be_visible()
    expect(host.locator(PLAYER)).to_have_count(0)
    expect(host).not_to_have_title("ran")


def test_a_picture_one_person_never_answered_turns_undelivered_for_them_when_they_leave(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-72: A sender's file shows pending until every receiver has answered it; one who leaves is named."""
    host, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.evaluate(WITHHOLD_RECEIPTS)

    _post(host, "unanswered.png", _png(), "image/png")

    expect(guest.locator(SHOWN_PICTURE)).to_have_count(1)
    expect(host.locator(f"{SENT_FILE}.pending")).to_have_count(1)
    guest.click("#leaveBtn")
    expect(host.locator(f"{SENT_FILE}.pending")).to_have_count(0)
    expect(host.locator(f"{SENT_FILE} .chat-delivery:not(.chat-file-note)")).to_have_count(1)


def test_the_composer_names_a_file_over_the_per_file_cap_and_posts_none(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str], tmp_path: Path
) -> None:
    """CALL-72: The composer refuses a file over the per-file cap, naming it and the cap."""
    _, guest, _ = open_call(open_person, base_url, page_errors, DESKTOP)
    guest.click("#chatBtn")
    too_large = tmp_path / "recording.mov"
    with too_large.open("wb") as handle:
        handle.truncate(FILE_LIMITS.max_bytes + 1)

    _choose(guest, too_large)

    expect(guest.locator("#chatFileProblem")).to_have_text(
        text("de", "chat.fileTooLarge", name="recording.mov", max=str(FILE_LIMITS.max_bytes // BYTES_PER_MEGABYTE))
    )
    expect(guest.locator("#chatMessages .chat-file")).to_have_count(0)


def test_leaving_the_call_revokes_the_blob_url_of_a_shown_picture_and_a_played_song(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    """CALL-72: Every blob URL is released at leave."""
    host, link = open_host(open_person, base_url, page_errors, DESKTOP, RECORD_REVOKED_URLS)
    guest = join(open_person, link, DESKTOP, (RECORD_PEER_CONNECTIONS, RECORD_REVOKED_URLS), page_errors)
    wait_for_peer_count(host, 1)
    wait_for_peer_count(guest, 1)
    host.click("#chatBtn")
    _post(guest, "kept-for-the-call.png", _png(), "image/png")
    shown_url = host.locator(SHOWN_PICTURE).get_attribute("src")
    expect(guest.locator(f"{SENT_FILE}.pending")).to_have_count(0)
    _post(guest, "kept-too.wav", _wav(), "audio/wav")
    host.click(f"{HEARD_FILE}:has-text('kept-too.wav') {LOAD_BUTTON}")
    played_url = host.locator(PLAYER).get_attribute("src")

    host.click("#leaveBtn")
    host.click("#leaveYes")

    host.locator("[data-screen=end]").wait_for()
    assert shown_url is not None
    assert played_url is not None
    assert {shown_url, played_url} <= set(host.evaluate(REVOKED_URLS))
