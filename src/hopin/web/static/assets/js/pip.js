// The stylesheet owns this breakpoint; script cannot read a media query out of it.
const PHONE_LAYOUT_QUERY = "(max-width: 600px)";
const PICTURE_IN_PICTURE_COUNT = "2";
const PRIMARY_BUTTON = 0;

const stage = document.getElementById("stage");
const ownTile = document.getElementById("localContainer");

let drag = null;

export function nearestCorner(centre, stageRect) {
    const vertical = centre.y < stageRect.top + stageRect.height / 2 ? "t" : "b";
    const horizontal = centre.x < stageRect.left + stageRect.width / 2 ? "l" : "r";
    return vertical + horizontal;
}

function isPictureInPicture() {
    return stage.dataset.count === PICTURE_IN_PICTURE_COUNT && matchMedia(PHONE_LAYOUT_QUERY).matches;
}

function startDrag(event) {
    if (event.button !== PRIMARY_BUTTON || !isPictureInPicture()) return;
    ownTile.setPointerCapture(event.pointerId);
    ownTile.classList.add("dragging");
    drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY };
}

function moveDrag(event) {
    if (drag?.pointerId !== event.pointerId) return;
    ownTile.style.translate = `${event.clientX - drag.startX}px ${event.clientY - drag.startY}px`;
}

function endDrag(event) {
    if (drag?.pointerId !== event.pointerId) return;
    const box = ownTile.getBoundingClientRect();
    const centre = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    ownTile.dataset.corner = nearestCorner(centre, stage.getBoundingClientRect());
    ownTile.style.translate = "";
    ownTile.classList.remove("dragging");
    drag = null;
}

export function initPip() {
    ownTile.addEventListener("pointerdown", startDrag);
    ownTile.addEventListener("pointermove", moveDrag);
    ownTile.addEventListener("pointerup", endDrag);
    ownTile.addEventListener("pointercancel", endDrag);
}
