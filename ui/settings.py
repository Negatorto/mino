from __future__ import annotations

from tkinter import messagebox
from typing import Callable

import customtkinter as ctk


def _create_dialog_buttons(
    parent: ctk.CTkBaseClass,
    on_save: Callable[[], None],
    on_cancel: Callable[[], None],
    pady: tuple[int, int] = (20, 0),
) -> ctk.CTkFrame:
    """Shared Save / Cancel button row for settings dialogs."""
    button_frame = ctk.CTkFrame(parent, fg_color="transparent")
    button_frame.pack(pady=pady)
    ctk.CTkButton(button_frame, text="Save", command=on_save).pack(side="left", padx=10)
    ctk.CTkButton(button_frame, text="Cancel", command=on_cancel, fg_color="gray").pack(side="left", padx=10)
    return button_frame


class CloneSettingsWindow(ctk.CTkFrame):
    """Toplevel frame for managing TEST -> PROD field cloning options."""

    FIELD_ORDER = ("host", "port", "user", "pass", "path")
    FIELD_LABELS = {
        "host": "Host / URL",
        "port": "Port",
        "user": "Username",
        "pass": "Password",
        "path": "Remote Path",
    }

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        clone_options: dict[str, ctk.BooleanVar],
        on_change_callback: Callable[[str], None],
    ) -> None:
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.clone_options = clone_options
        self.on_change_callback = on_change_callback

        ctk.CTkLabel(
            self,
            text="Select fields to clone from TEST to PROD:",
            font=ctk.CTkFont(weight="bold"),
        ).pack(pady=(0, 10))

        for key in self.FIELD_ORDER:
            if key in self.clone_options:
                ctk.CTkCheckBox(
                    self,
                    text=self.FIELD_LABELS.get(key, key),
                    variable=self.clone_options[key],
                    command=lambda k=key: self.on_change_callback(k),
                ).pack(anchor="w", pady=5, padx=20)

        ctk.CTkButton(self, text="Close", command=parent_toplevel.destroy).pack(pady=(20, 0))
        parent_toplevel.bind("<Escape>", lambda _e: parent_toplevel.destroy())


class NoteKeywordWindow(ctk.CTkFrame):
    """Toplevel frame for configuring the scan note keyword."""

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        current_keyword_var: ctk.StringVar,
        on_save_callback: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.current_keyword_var = current_keyword_var
        self.on_save_callback = on_save_callback
        self.parent_toplevel = parent_toplevel

        ctk.CTkLabel(self, text="Enter keyword to scan for:", font=ctk.CTkFont(weight="bold")).pack(
            pady=(0, 10)
        )

        self.entry_var = ctk.StringVar(value=current_keyword_var.get())
        self.entry = ctk.CTkEntry(self, textvariable=self.entry_var, width=200)
        self.entry.pack(pady=5)
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda _e: self.save())
        parent_toplevel.bind("<Escape>", lambda _e: parent_toplevel.destroy())

        _create_dialog_buttons(self, self.save, parent_toplevel.destroy)

    def save(self) -> None:
        new_keyword = self.entry_var.get().strip()
        if not new_keyword:
            messagebox.showwarning(
                "Invalid Keyword",
                "Keyword cannot be empty.",
                parent=self.parent_toplevel,
            )
            return
        self.current_keyword_var.set(new_keyword)
        if self.on_save_callback:
            self.on_save_callback(new_keyword)
        self.parent_toplevel.destroy()


class IgnorePatternsWindow(ctk.CTkFrame):
    """Toplevel frame for configuring file/folder glob ignore patterns."""

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        current_patterns_var: ctk.StringVar,
    ) -> None:
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.current_patterns_var = current_patterns_var
        self.parent_toplevel = parent_toplevel

        ctk.CTkLabel(
            self,
            text="Enter patterns to ignore (one per line or comma-separated):",
            font=ctk.CTkFont(weight="bold"),
            wraplength=380,
        ).pack(pady=(10, 10))
        ctk.CTkLabel(
            self,
            text="Examples: *.pyc, .git, node_modules, .env",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        ).pack(pady=(0, 10))

        self.text_area = ctk.CTkTextbox(self, width=270, height=135)
        self.text_area.pack(pady=5, padx=20, fill="both", expand=True)

        current_val = current_patterns_var.get()
        formatted_val = "\n".join(p.strip() for p in current_val.split(",") if p.strip())
        if formatted_val:
            self.text_area.insert("1.0", formatted_val)

        self.text_area.focus_set()
        parent_toplevel.bind("<Escape>", lambda _e: parent_toplevel.destroy())

        _create_dialog_buttons(self, self.save, parent_toplevel.destroy, pady=(20, 20))

    def save(self) -> None:
        raw_text = self.text_area.get("1.0", "end-1c")
        normalized = raw_text.replace("\n", ",")
        items = [x.strip() for x in normalized.split(",") if x.strip()]
        self.current_patterns_var.set(",".join(items))
        self.parent_toplevel.destroy()
