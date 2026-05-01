from __future__ import annotations

import ctypes
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any

from src.capture.screen import get_arena_window_bounds


@dataclass
class OverlayMarker:
    label: str
    position: tuple[int, int]
    color: str = "#00c8ff"
    radius: int = 12


@dataclass
class OverlayData:
    status: str = ""
    action: str = ""
    input_hint: str = ""
    phase: str = "UNKNOWN"
    turn: int = 0
    has_priority: bool = False
    detail: str = ""
    frame_bgr: Any | None = None
    markers: list[OverlayMarker] = field(default_factory=list)
    window_bounds: dict[str, int] | None = None


class Overlay:
    """Transparent, click-through overlay drawn directly above the Arena window."""

    _TRANSPARENT = "#010203"
    _REFRESH_MS = 50
    _STALE_AFTER_SECONDS = 8.0

    def __init__(self) -> None:
        self._q: queue.Queue[OverlayData | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="mtga-integrated-overlay",
        )
        self._thread.start()

    def update(self, data: OverlayData) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._q.put(data)

    def stop(self) -> None:
        self._q.put(None)

    def _run(self) -> None:
        root = tk.Tk()
        root.title("MTG Bot Overlay")
        root.overrideredirect(True)
        root.configure(bg=self._TRANSPARENT)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", self._TRANSPARENT)

        canvas = tk.Canvas(
            root,
            bg=self._TRANSPARENT,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)

        root.update_idletasks()
        self._make_click_through(root.winfo_id())
        root.withdraw()

        latest: OverlayData | None = None
        current_bounds: dict[str, int] | None = None
        latest_at = 0.0

        def refresh() -> None:
            nonlocal latest, current_bounds, latest_at

            try:
                while True:
                    item = self._q.get_nowait()
                    if item is None:
                        root.destroy()
                        return
                    latest = item
                    latest_at = time.monotonic()
            except queue.Empty:
                pass

            if latest is None or time.monotonic() - latest_at > self._STALE_AFTER_SECONDS:
                canvas.delete("all")
                root.withdraw()
                current_bounds = None
                root.after(self._REFRESH_MS, refresh)
                return

            bounds = self._resolve_bounds(latest)
            if bounds is None:
                root.withdraw()
                current_bounds = None
                root.after(self._REFRESH_MS, refresh)
                return

            if bounds != current_bounds:
                current_bounds = bounds
                root.geometry(
                    f"{bounds['width']}x{bounds['height']}+{bounds['left']}+{bounds['top']}"
                )
                canvas.configure(width=bounds["width"], height=bounds["height"])
                root.deiconify()
                root.lift()

            canvas.delete("all")
            if latest is not None:
                self._draw_status(canvas, latest)
                self._draw_markers(canvas, latest, bounds)

            root.after(self._REFRESH_MS, refresh)

        root.after(self._REFRESH_MS, refresh)
        root.mainloop()

    def _resolve_bounds(self, data: OverlayData | None) -> dict[str, int] | None:
        if data is not None and data.window_bounds is not None:
            return data.window_bounds
        return get_arena_window_bounds()

    def _draw_status(self, canvas: tk.Canvas, data: OverlayData) -> None:
        title = f"{data.status}: {data.action}" if data.action else data.status
        subtitle = (
            f"phase={data.phase} turn={data.turn} "
            f"priority={data.has_priority} input={data.input_hint}"
        )
        detail = data.detail

        lines = [line for line in (title, subtitle, detail) if line]
        if not lines:
            return

        width = min(760, max(320, max(len(line) for line in lines) * 8 + 24))
        height = 22 + 18 * len(lines)
        canvas.create_rectangle(
            10,
            10,
            width,
            height,
            fill="#111111",
            outline="#ffcc00",
            width=1,
        )

        y = 24
        canvas.create_text(
            20,
            y,
            text=title,
            anchor="w",
            fill="#ffcc00",
            font=("Consolas", 12, "bold"),
        )
        y += 18

        for line in lines[1:]:
            canvas.create_text(
                20,
                y,
                text=line,
                anchor="w",
                fill="#f0f0f0",
                font=("Consolas", 10),
            )
            y += 18

    def _draw_markers(
        self,
        canvas: tk.Canvas,
        data: OverlayData,
        bounds: dict[str, int],
    ) -> None:
        for marker in data.markers:
            pos = self._to_overlay_pos(marker.position, bounds)
            if pos is None:
                continue

            x, y = pos
            r = marker.radius
            color = marker.color
            canvas.create_oval(x - r, y - r, x + r, y + r, outline=color, width=2)
            canvas.create_line(x - r - 6, y, x + r + 6, y, fill=color, width=2)
            canvas.create_line(x, y - r - 6, x, y + r + 6, fill=color, width=2)
            canvas.create_text(
                x + r + 8,
                y - r - 4,
                text=marker.label,
                anchor="w",
                fill=color,
                font=("Consolas", 11, "bold"),
            )

    @staticmethod
    def _to_overlay_pos(
        position: tuple[int, int],
        bounds: dict[str, int],
    ) -> tuple[int, int] | None:
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        width = int(bounds.get("width", 0))
        height = int(bounds.get("height", 0))
        x, y = position

        if left <= x <= left + width and top <= y <= top + height:
            return x - left, y - top
        if 0 <= x <= width and 0 <= y <= height:
            return x, y
        return None

    @staticmethod
    def _make_click_through(hwnd: int) -> None:
        user32 = ctypes.windll.user32
        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        ws_ex_toolwindow = 0x00000080
        ws_ex_noactivate = 0x08000000
        hwnd_topmost = -1
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010

        get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

        style = get_window_long(hwnd, gwl_exstyle)
        style |= ws_ex_layered | ws_ex_transparent | ws_ex_toolwindow | ws_ex_noactivate
        set_window_long(hwnd, gwl_exstyle, style)
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate,
        )
