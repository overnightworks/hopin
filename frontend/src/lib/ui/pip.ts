// The stylesheet owns this breakpoint; script cannot read a media query out of it.
const PHONE_LAYOUT_QUERY = "(max-width: 600px)";
const PICTURE_IN_PICTURE_COUNT = "2";
const PRIMARY_BUTTON = 0;

interface Point {
    x: number;
    y: number;
}

interface Drag {
    pointerId: number;
    startX: number;
    startY: number;
}

function nearestCorner(centre: Point, stageRect: DOMRect): string {
    const vertical = centre.y < stageRect.top + stageRect.height / 2 ? "t" : "b";
    const horizontal = centre.x < stageRect.left + stageRect.width / 2 ? "l" : "r";
    return vertical + horizontal;
}

function stageOf(ownTile: HTMLElement): HTMLElement {
    const stage = ownTile.closest<HTMLElement>("#stage");
    if (!stage) throw new Error("The own tile is not inside the stage.");
    return stage;
}

export function pictureInPicture(ownTile: HTMLElement): () => void {
    const stage = stageOf(ownTile);

    let drag: Drag | null = null;

    function isPictureInPicture(): boolean {
        return stage.dataset.count === PICTURE_IN_PICTURE_COUNT && matchMedia(PHONE_LAYOUT_QUERY).matches;
    }

    function startDrag(event: PointerEvent): void {
        if (event.button !== PRIMARY_BUTTON || !isPictureInPicture()) return;
        ownTile.setPointerCapture(event.pointerId);
        ownTile.classList.add("dragging");
        drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY };
    }

    function releaseTile(): void {
        ownTile.style.translate = "";
        ownTile.classList.remove("dragging");
        drag = null;
    }

    function moveDrag(event: PointerEvent): void {
        if (drag?.pointerId !== event.pointerId) return;
        if (!isPictureInPicture()) {
            releaseTile();
            return;
        }
        ownTile.style.translate = `${String(event.clientX - drag.startX)}px ${String(event.clientY - drag.startY)}px`;
    }

    function endDrag(event: PointerEvent): void {
        if (drag?.pointerId !== event.pointerId) return;
        if (!isPictureInPicture()) {
            releaseTile();
            return;
        }
        const box = ownTile.getBoundingClientRect();
        const centre = { x: box.left + box.width / 2, y: box.top + box.height / 2 };
        ownTile.dataset.corner = nearestCorner(centre, stage.getBoundingClientRect());
        releaseTile();
    }

    ownTile.addEventListener("pointerdown", startDrag);
    ownTile.addEventListener("pointermove", moveDrag);
    ownTile.addEventListener("pointerup", endDrag);
    ownTile.addEventListener("pointercancel", endDrag);
    return () => {
        ownTile.removeEventListener("pointerdown", startDrag);
        ownTile.removeEventListener("pointermove", moveDrag);
        ownTile.removeEventListener("pointerup", endDrag);
        ownTile.removeEventListener("pointercancel", endDrag);
    };
}
