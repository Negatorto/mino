from __future__ import annotations

import queue
from typing import Any, Callable

import customtkinter as ctk

from models import THEME_PALETTES


def resolve_theme_mode(mode: str | None = None) -> str:
    """
    Resolves CustomTkinter appearance mode ('System', 'Dark', 'Light') to 'Dark' or 'Light'.
    Fixes 'System' mode falling back to Light even on Dark OS themes.
    """
    effective = ctk.get_appearance_mode() if mode in (None, "System") else mode
    return "Dark" if effective == "Dark" else "Light"


def get_theme_palette(mode: str | None = None) -> dict[str, dict[str, str]]:
    """Returns the color palette dictionary for the active appearance mode."""
    return THEME_PALETTES[resolve_theme_mode(mode)]


def clear_queue(q: queue.Queue[Any]) -> None:
    """Drains any stale items left in a thread queue before starting a new task."""
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


def poll_queue(
    widget: ctk.CTkBaseClass | ctk.CTk | ctk.CTkToplevel,
    q: queue.Queue[Any],
    on_item: Callable[[Any], bool],
    on_ui_error: Callable[[Exception], None] | None = None,
    interval_ms: int = 50,
) -> None:
    """
    Drains all currently available items from `q` on each UI tick and invokes `on_item(item)`.
    - If `on_item` returns False, polling stops immediately.
    - If `on_item` returns True, continues draining and reschedules after `interval_ms`.
    - Safely aborts if `widget` has been destroyed.
    """

    def _step() -> None:
        if not widget.winfo_exists():
            return
        try:
            while True:
                item = q.get_nowait()
                should_continue = on_item(item)
                if not should_continue or not widget.winfo_exists():
                    return
        except queue.Empty:
            if widget.winfo_exists():
                widget.after(interval_ms, _step)
        except Exception as exc:
            if on_ui_error and widget.winfo_exists():
                on_ui_error(exc)

    _step()


def create_centered_toplevel(
    parent: ctk.CTk | ctk.CTkBaseClass,
    title: str,
    width: int,
    height: int,
    *,
    resizable: bool = True,
    modal_transient: bool = True,
) -> ctk.CTkToplevel:
    """Creates a CTkToplevel centered on screen with consistent geometry across platforms."""
    top = ctk.CTkToplevel(parent)
    top.title(title)
    top.resizable(resizable, resizable)
    if modal_transient:
        top.transient(parent.winfo_toplevel())

    screen_w = top.winfo_screenwidth()
    screen_h = top.winfo_screenheight()
    pos_x = max(0, (screen_w - width) // 2)
    pos_y = max(0, (screen_h - height) // 2)
    top.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
    return top

