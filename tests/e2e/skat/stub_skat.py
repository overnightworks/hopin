"""A stand-in for Skat's table page on its own https loopback port: it answers `/t/<id>` with a page that reports what
the sandboxed frame is allowed to do, greets the call with `ready` and lists the `hello` messages it receives, and
records the requests it receives. It needs no sign-in."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from tests.e2e.support.server import LOOPBACK_HOST, reserve_free_port, serving, write_self_signed_certificate

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from starlette.requests import Request

TABLE_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Stub table</title></head><body>
<p id="table">Table __ID__</p>
<button id="capture" type="button">Capture</button>
<form id="form" action="/submitted" method="get">
<input name="marker" value="sent"><button id="submit" type="submit">Send</button></form>
<button id="again" type="button">Ready again</button>
<button id="wrong-table" type="button">Ready for another table</button>
<button id="stranger" type="button">Ready from a nested page</button>
<button id="sandboxed-stranger" type="button">Ready from a nested page of no origin</button>
<button id="reload" type="button">Reload</button>
<pre id="report">{}</pre>
<pre id="hellos">[]</pre>
<script>
const report = {};
const publish = () => { document.getElementById("report").textContent = JSON.stringify(report); };
const nameOf = (error) => (error && error.name) || String(error);
report.referrer = document.referrer;
try { top.location.href = "https://top-navigation.invalid/"; report.topNavigation = "allowed"; }
catch (error) { report.topNavigation = nameOf(error); }
navigator.mediaDevices.getUserMedia({ video: true, audio: true })
  .then(() => { report.getUserMedia = "granted"; }, (error) => { report.getUserMedia = nameOf(error); })
  .finally(publish);
alert("stub alert");
report.alert = "returned";
document.getElementById("capture").addEventListener("click", () => {
  navigator.mediaDevices.getDisplayMedia({ video: true })
    .then(() => { report.getDisplayMedia = "granted"; }, (error) => { report.getDisplayMedia = nameOf(error); })
    .finally(publish);
});
publish();
const callOrigin = location.ancestorOrigins[0];
const ready = (table) => ({ v: 1, type: "ready", table });
const hellos = [];
window.addEventListener("message", (event) => {
  if (event.source === parent && event.origin === callOrigin) {
    hellos.push(event.data);
    document.getElementById("hellos").textContent = JSON.stringify(hellos);
  } else if (event.data === "posted") {
    document.body.dataset.posted = "yes";
  } else if (event.data === "heard") {
    hellos.push(event.data);
    document.getElementById("hellos").textContent = JSON.stringify(hellos);
  }
});
document.getElementById("again").addEventListener("click", () => parent.postMessage(ready("__ID__"), callOrigin));
document.getElementById("wrong-table").addEventListener("click", () => {
  parent.postMessage(ready("f".repeat(32)), callOrigin);
});
const intrude = (sandbox) => {
  const nested = document.createElement("iframe");
  if (sandbox) nested.setAttribute("sandbox", "allow-scripts");
  const greeting = `parent.parent.postMessage(${JSON.stringify(ready("__ID__"))}, ${JSON.stringify(callOrigin)});`;
  const forward = `addEventListener("message", () => parent.postMessage("heard", "*"));`;
  nested.srcdoc = `<script>${forward} ${greeting} parent.postMessage("posted", "*");<\\/script>`;
  document.body.append(nested);
};
document.getElementById("stranger").addEventListener("click", () => intrude(false));
document.getElementById("sandboxed-stranger").addEventListener("click", () => intrude(true));
document.getElementById("reload").addEventListener("click", () => location.reload());
parent.postMessage(ready("__ID__"), callOrigin);
</script></body></html>"""


@dataclass
class StubSkat:
    """The stub's origin, and the paths and `Referer` headers of the requests it has answered."""

    origin: str
    paths: list[str] = field(default_factory=list)
    referers: list[str | None] = field(default_factory=list)


def _app(stub: StubSkat) -> Starlette:
    async def table(request: Request) -> Response:
        stub.paths.append(request.url.path + "?" + request.url.query)
        stub.referers.append(request.headers.get("referer"))
        return HTMLResponse(TABLE_PAGE.replace("__ID__", request.path_params["table_id"]))

    async def submitted(request: Request) -> Response:
        stub.paths.append(request.url.path)
        return JSONResponse({"submitted": True})

    return Starlette(routes=[Route("/t/{table_id}", table), Route("/submitted", submitted)])


@contextmanager
def running_stub_skat(tls_directory: Path) -> Iterator[StubSkat]:
    """Serve the stub over https on a free loopback port until the block ends."""
    port = reserve_free_port()
    key_file, certificate_file = write_self_signed_certificate(tls_directory)
    stub = StubSkat(f"https://{LOOPBACK_HOST}:{port}")
    server = uvicorn.Server(
        uvicorn.Config(
            _app(stub),
            host=LOOPBACK_HOST,
            port=port,
            ssl_keyfile=str(key_file),
            ssl_certfile=str(certificate_file),
            log_level="warning",
        )
    )
    with serving(server):
        yield stub
