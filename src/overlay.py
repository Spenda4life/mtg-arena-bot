from __future__ import annotations
import ctypes
import os
import queue
import signal
import threading
import tkinter as tk
from dataclasses import dataclass


@dataclass
class OverlayData:
    status: str = ""
    phase: str = "?"
    has_priority: bool = False
    our_life: int = 0
    opp_life: int = 0
    hand_count: int = 0
    playable_count: int = 0
    last_action: str = ""
    pending: str = ""


class Overlay:
    """Semi-transparent always-on-top overlay showing bot state.

    Runs a tkinter window in a daemon thread. The main bot loop calls
    update() each tick; the window polls the queue every 150 ms.

    The overlay is NOT click-through so that the Kill button works.
    It sits in the top-left corner which is clear of Arena's in-game UI.
    """

    _W = 360
    _H = 195

    def __init__(self, x: int = 10, y: int = 10):
        self._q: queue.Queue[OverlayData | None] = queue.Queue()
        self._x = x
        self._y = y
        self._thread = threading.Thread(target=self._run, daemon=True, name="overlay")
        self._thread.start()

    def update(self, data: OverlayData) -> None:
        # Drop stale frames — only the latest state matters
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._q.put(data)

    def stop(self) -> None:
        self._q.put(None)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _run(self) -> None:
        root = tk.Tk()
        root.geometry(f"{self._W}x{self._H}+{self._x}+{self._y}")
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-alpha", 0.82)
        root.overrideredirect(True)

        BG = "#0d0d0d"
        root.configure(bg=BG)

        # WS_EX_LAYERED enables alpha transparency. We intentionally omit
        # WS_EX_TRANSPARENT so the Kill button can receive mouse clicks.
        root.update()
        try:
            hwnd = int(root.wm_frame(), 16)
            GWL_EXSTYLE   = -20
            WS_EX_LAYERED = 0x00080000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        except Exception:
            pass

        FONT  = ("Consolas", 9)
        FONTB = ("Consolas", 9, "bold")

        def lbl(text="", fg="#c0c0c0", bold=False) -> tk.Label:
            w = tk.Label(root, text=text, bg=BG, fg=fg,
                         font=FONTB if bold else FONT, anchor="w")
            w.pack(fill="x", padx=8, pady=1)
            return w

        lbl("MTG Bot", fg="#ffcc00", bold=True)
        l_status  = lbl()
        l_phase   = lbl()
        l_life    = lbl()
        l_hand    = lbl()
        l_action  = lbl()
        l_pending = lbl()

        def _kill():
            os.kill(os.getpid(), signal.SIGINT)

        tk.Button(
            root, text="Kill Bot", command=_kill,
            bg="#5c0000", fg="white", activebackground="#8b0000",
            font=FONTB, relief="flat", cursor="hand2", bd=0,
        ).pack(fill="x", padx=8, pady=(4, 6))

        def _trunc(s: str, n: int = 44) -> str:
            return s[:n] + ".." if len(s) > n else s

        def refresh() -> None:
            data: OverlayData | None = None
            while not self._q.empty():
                try:
                    data = self._q.get_nowait()
                except queue.Empty:
                    break

            if data is None and not self._q.empty():
                root.destroy()
                return

            if data is not None:
                pri_color = "#00ff88" if data.has_priority else "#666666"
                pri_text  = "YES" if data.has_priority else "no"

                l_status.config( text=f"  {data.status or 'running'}",                         fg="#aaaaaa")
                l_phase.config(  text=f"  Phase: {data.phase:<10} Priority: {pri_text}",        fg=pri_color)
                l_life.config(   text=f"  Life:  {data.our_life} vs {data.opp_life}",            fg="#ff6666")
                l_hand.config(   text=f"  Hand:  {data.hand_count} cards  ({data.playable_count} playable)", fg="#88ccff")
                l_action.config( text=f"  Act:   {_trunc(data.last_action)}",                   fg="#ffcc00")
                l_pending.config(text=f"  Wait:  {_trunc(data.pending)}" if data.pending else "", fg="#ff8844")

            root.after(150, refresh)

        root.after(150, refresh)
        root.mainloop()
