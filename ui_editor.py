from __future__ import annotations

import difflib
import queue
import threading
from tkinter import messagebox
from typing import Any, Callable, Sequence

import customtkinter as ctk

from models import ComparisonRow
import sftp_logic
from ui_helpers import (
    clear_queue,
    create_centered_toplevel,
    get_theme_palette,
    poll_queue,
    resolve_theme_mode,
)


class DiffWindow(ctk.CTkFrame):
    """Side-by-side file comparison window with inline character diff and editor launcher."""

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        s1_config: dict[str, Any],
        s2_config: dict[str, Any],
        item_values: ComparisonRow | Sequence[Any],
    ) -> None:
        super().__init__(parent_toplevel, fg_color="transparent")
        self.pack(fill="both", expand=True)

        self.parent_toplevel = parent_toplevel
        self.s1_config = s1_config
        self.s2_config = s2_config

        row = item_values if isinstance(item_values, ComparisonRow) else ComparisonRow.from_tree_values(item_values)
        self.status = row.status
        self.relative_path = row.relative_path
        self.note = row.note
        self.owner_group = row.owner_group
        self.symbolic_perms = row.symbolic_perms
        self.octal_perms = row.octal_perms

        self.diff_queue: queue.Queue[Any] = queue.Queue()
        self.file_contents: dict[str, str | None] = {"TEST": None, "PROD": None}
        self.diff_result: list[str] = []
        self._downloads_pending = 0
        self._files_ready = False
        self._syncing_scroll = False
        self._applied_theme_mode: str | None = None

        owner1, owner2 = row.split_owners()
        perms1, perms2 = row.split_symbolic_perms()

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure((0, 1), weight=1)

        control_frame = ctk.CTkFrame(self, fg_color="transparent")
        control_frame.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=5)

        self.highlight_inline_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            control_frame,
            text="Highlight inline differences",
            variable=self.highlight_inline_var,
            command=self.populate_diff,
        ).pack(side="left")

        self.line_number_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            control_frame,
            text="Show line numbers",
            variable=self.line_number_var,
            command=self.populate_diff,
        ).pack(side="left", padx=10)

        self.show_metadata_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            control_frame,
            text="Show Metadata",
            variable=self.show_metadata_var,
            command=self.toggle_metadata_visibility,
        ).pack(side="left", padx=10)

        self.edit_test_btn = ctk.CTkButton(
            control_frame, text="Edit TEST", width=80, command=lambda: self.open_editor("TEST")
        )
        self.edit_test_btn.pack(side="left", padx=10)

        self.edit_prod_btn = ctk.CTkButton(
            control_frame, text="Edit PROD", width=80, command=lambda: self.open_editor("PROD")
        )
        self.edit_prod_btn.pack(side="left", padx=10)

        self.status_var = ctk.StringVar(value=f"Loading file: {self.relative_path}...")
        ctk.CTkLabel(control_frame, textvariable=self.status_var, anchor="e").pack(
            side="right", fill="x", expand=True
        )

        host1_display = str(self.s1_config.get("host", "")).strip() or "local"
        host2_display = str(self.s2_config.get("host", "")).strip() or "local"

        self.text1_frame, self.text1, self.lines1, self.meta_label1 = self._create_text_pane(
            self, f"TEST Server ({host1_display})", owner1, perms1
        )
        self.text1_frame.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=10)

        self.text2_frame, self.text2, self.lines2, self.meta_label2 = self._create_text_pane(
            self, f"PRODUCTION Server ({host2_display})", owner2, perms2
        )
        self.text2_frame.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=10)

        self.main_scrollbar = ctk.CTkScrollbar(self, command=self.on_main_scroll)
        self.main_scrollbar.grid(row=2, column=2, sticky="ns", pady=10)

        self.text1.configure(yscrollcommand=lambda first, last: self._on_pane_scroll(self.text1, first, last))
        self.text2.configure(yscrollcommand=lambda first, last: self._on_pane_scroll(self.text2, first, last))

        self.bind("<Configure>", self.update_tags_for_theme, add="+")
        self.update_tags_for_theme(force=True)
        self.toggle_metadata_visibility()

        self._start_downloads()

    def _start_downloads(self) -> None:
        clear_queue(self.diff_queue)
        self.file_contents = {"TEST": None, "PROD": None}
        self.diff_result = []
        self._files_ready = False
        self._downloads_pending = 2

        for server_name, cfg in (("TEST", self.s1_config), ("PROD", self.s2_config)):
            threading.Thread(
                target=sftp_logic.download_file_task,
                args=(cfg, self.relative_path, self.diff_queue, server_name),
                daemon=True,
            ).start()

        poll_queue(
            self,
            self.diff_queue,
            self._handle_diff_queue_item,
            on_ui_error=self._handle_diff_ui_error,
        )

    def open_editor(self, server_name: str) -> None:
        content = self.file_contents.get(server_name)
        if content is None:
            messagebox.showerror(
                "Error",
                f"File content for {server_name} is not loaded yet.",
                parent=self.parent_toplevel,
            )
            return

        config = self.s1_config if server_name == "TEST" else self.s2_config
        editor_top = create_centered_toplevel(
            self,
            f"Edit {server_name}: {self.relative_path}",
            800,
            600,
        )
        EditorWindow(editor_top, config, self.relative_path, content, server_name, self.refresh_diff)

    def refresh_diff(self) -> None:
        """Reloads both files and refreshes the side-by-side diff."""
        self.status_var.set("Reloading files...")
        self._clear_all_panes()
        self._start_downloads()

    def _clear_all_panes(self) -> None:
        for widget in (self.text1, self.text2, self.lines1, self.lines2):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")

    def _create_text_pane(
        self,
        parent: ctk.CTkBaseClass,
        title: str,
        owner_info: str,
        perms_info: str,
    ) -> tuple[ctk.CTkFrame, ctk.CTkTextbox, ctk.CTkTextbox, ctk.CTkLabel]:
        frame = ctk.CTkFrame(parent, border_width=1)
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 0)
        )

        meta_text = f"Owner: {owner_info}  |  Perms: {perms_info}"
        meta_label = ctk.CTkLabel(frame, text=meta_text, font=ctk.CTkFont(size=11), text_color="gray")
        meta_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 5))

        line_numbers = ctk.CTkTextbox(
            frame,
            width=45,
            wrap="none",
            font=("monospace", 10),
            state="disabled",
            border_width=0,
            corner_radius=0,
            activate_scrollbars=False,
        )
        line_numbers.grid(row=2, column=0, sticky="ns")
        line_numbers.grid_forget()

        text_widget = ctk.CTkTextbox(
            frame,
            wrap="none",
            font=("monospace", 10),
            state="disabled",
            border_width=0,
            corner_radius=0,
        )
        text_widget.grid(row=2, column=1, sticky="nsew")

        return frame, text_widget, line_numbers, meta_label

    def toggle_metadata_visibility(self) -> None:
        """Shows or hides the metadata labels based on the checkbox."""
        if self.show_metadata_var.get():
            self.meta_label1.grid()
            self.meta_label2.grid()
        else:
            self.meta_label1.grid_remove()
            self.meta_label2.grid_remove()

    def _configure_line_number_tags(self, line_widget: ctk.CTkTextbox, tags_palette: dict[str, str]) -> None:
        gutter_bg = tags_palette["gutter_bg"]
        gutter_fg = tags_palette["gutter_fg"]
        line_widget.configure(fg_color=gutter_bg)
        line_widget.tag_config("line", foreground=gutter_fg)
        line_widget.tag_config("blank", foreground=gutter_bg)

    def update_tags_for_theme(self, event: Any = None, *, force: bool = False) -> None:
        current_mode = resolve_theme_mode()
        if not force and current_mode == self._applied_theme_mode:
            return
        self._applied_theme_mode = current_mode

        tags = get_theme_palette(current_mode)["tags"]
        self.text1.tag_config("removed", background=tags["different_bg"], foreground=tags["different_fg"])
        self.text2.tag_config("added", background=tags["solo_prod_bg"], foreground=tags["solo_prod_fg"])

        for text_widget in (self.text1, self.text2):
            text_widget.tag_config("blank", background=tags["blank_bg"])
            text_widget.tag_config(
                "highlight_inline",
                background=tags["inline_bg"],
                foreground=tags["inline_fg"],
            )

        self._configure_line_number_tags(self.lines1, tags)
        self._configure_line_number_tags(self.lines2, tags)

    def on_main_scroll(self, *args: Any) -> None:
        self._syncing_scroll = True
        try:
            for widget in (self.text1, self.text2, self.lines1, self.lines2):
                widget.yview(*args)
            first, last = self.text1.yview()
            self.main_scrollbar.set(first, last)
        finally:
            self._syncing_scroll = False

    def _on_pane_scroll(self, source: ctk.CTkTextbox, first: str | float, last: str | float) -> None:
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            self.main_scrollbar.set(first, last)
            other = self.text2 if source is self.text1 else self.text1
            other.yview_moveto(first)
            self.lines1.yview_moveto(first)
            self.lines2.yview_moveto(first)
        finally:
            self._syncing_scroll = False

    def _handle_diff_ui_error(self, exc: Exception) -> None:
        messagebox.showerror("GUI Error", f"Error in diff queue: {exc}", parent=self.parent_toplevel)
        self.status_var.set("GUI Error.")

    def _handle_diff_queue_item(self, result: Any) -> bool:
        if isinstance(result, Exception):
            self._downloads_pending -= 1
            messagebox.showerror(
                "Download Error",
                f"Failed to download file:\n{result}",
                parent=self.parent_toplevel,
            )
            self.status_var.set("Download error.")
            return self._downloads_pending > 0

        if isinstance(result, str):
            self.status_var.set(result)
            return True

        if isinstance(result, dict) and "server" in result:
            self._downloads_pending -= 1
            server = result["server"]
            self.file_contents[server] = result["content"]
            self.status_var.set(f"Downloaded {server} file.")

            if self.file_contents["TEST"] is not None and self.file_contents["PROD"] is not None:
                self.status_var.set("Files downloaded. Calculating diff...")
                s1_lines = self.file_contents["TEST"].splitlines()
                s2_lines = self.file_contents["PROD"].splitlines()
                # Filter out '? ' hint lines so modified '- ' and '+ ' lines stay adjacent
                # and side-by-side inline character highlighting works on replacements/deletions.
                self.diff_result = [
                    line for line in difflib.ndiff(s1_lines, s2_lines) if not line.startswith("? ")
                ]
                self._files_ready = True
                self.populate_diff()
                self.status_var.set("Diff complete.")
                return False

            return self._downloads_pending > 0

        return self._downloads_pending > 0

    def populate_diff(self) -> None:
        if not self._files_ready:
            return

        highlight_on = self.highlight_inline_var.get()
        lines_on = self.line_number_var.get()

        if lines_on:
            self.lines1.grid(row=2, column=0, sticky="ns")
            self.lines2.grid(row=2, column=0, sticky="ns")
        else:
            self.lines1.grid_forget()
            self.lines2.grid_forget()

        for widget in (self.text1, self.text2, self.lines1, self.lines2):
            widget.configure(state="normal")
            widget.delete("1.0", "end")

        i, line_num_1, line_num_2 = 0, 1, 1
        total_lines = len(self.diff_result)

        while i < total_lines:
            line = self.diff_result[i]
            if line.startswith("  "):
                text = line[2:]
                self.text1.insert("end", f"{text}\n")
                self.text2.insert("end", f"{text}\n")
                self.lines1.insert("end", f"{line_num_1}\n", "line")
                self.lines2.insert("end", f"{line_num_2}\n", "line")
                line_num_1 += 1
                line_num_2 += 1
                i += 1
            elif (
                highlight_on
                and line.startswith("- ")
                and (i + 1 < total_lines)
                and self.diff_result[i + 1].startswith("+ ")
            ):
                line1_text = line[2:]
                line2_text = self.diff_result[i + 1][2:]
                self.text1.insert("end", f"{line1_text}\n", "removed")
                self.text2.insert("end", f"{line2_text}\n", "added")
                self.lines1.insert("end", f"{line_num_1}\n", "line")
                self.lines2.insert("end", f"{line_num_2}\n", "line")
                self.apply_inline_tags(line1_text, line2_text)
                line_num_1 += 1
                line_num_2 += 1
                i += 2
            elif line.startswith("- "):
                self.text1.insert("end", f"{line[2:]}\n", "removed")
                self.text2.insert("end", "\n", "blank")
                self.lines1.insert("end", f"{line_num_1}\n", "line")
                self.lines2.insert("end", "\n", "blank")
                line_num_1 += 1
                i += 1
            elif line.startswith("+ "):
                self.text1.insert("end", "\n", "blank")
                self.text2.insert("end", f"{line[2:]}\n", "added")
                self.lines1.insert("end", "\n", "blank")
                self.lines2.insert("end", f"{line_num_2}\n", "line")
                line_num_2 += 1
                i += 1
            else:
                i += 1

        for widget in (self.text1, self.text2, self.lines1, self.lines2):
            widget.configure(state="disabled")

    def apply_inline_tags(self, line1: str, line2: str) -> None:
        matcher = difflib.SequenceMatcher(None, line1, line2, autojunk=False)
        line1_start = self.text1.index(f"end - {len(line1) + 1}c")
        line2_start = self.text2.index(f"end - {len(line2) + 1}c")
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "delete"):
                self.text1.tag_add("highlight_inline", f"{line1_start} + {i1}c", f"{line1_start} + {i2}c")
            if tag in ("replace", "insert"):
                self.text2.tag_add("highlight_inline", f"{line2_start} + {j1}c", f"{line2_start} + {j2}c")


class EditorWindow(ctk.CTkFrame):
    """Toplevel window for editing and saving a local or SFTP file."""

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        config: dict[str, Any],
        relative_path: str,
        initial_content: str,
        server_name: str,
        on_save_callback: Callable[[], None] | None,
    ) -> None:
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True)

        self.config = config
        self.relative_path = relative_path
        self.server_name = server_name
        self.on_save_callback = on_save_callback
        self.parent_toplevel = parent_toplevel
        self.queue: queue.Queue[Any] = queue.Queue()

        self.text_area = ctk.CTkTextbox(self, wrap="none", font=("monospace", 12), undo=True)
        self.text_area.pack(fill="both", expand=True, padx=10, pady=10)
        self.text_area.insert("1.0", initial_content)

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=10, pady=10)

        self.save_btn = ctk.CTkButton(button_frame, text=f"Save to {server_name}", command=self.save_file)
        self.save_btn.pack(side="right", padx=5)

        self.cancel_btn = ctk.CTkButton(
            button_frame, text="Cancel", fg_color="gray", command=parent_toplevel.destroy
        )
        self.cancel_btn.pack(side="right", padx=5)

        self.status_lbl = ctk.CTkLabel(button_frame, text="", text_color="gray")
        self.status_lbl.pack(side="left", padx=5)

    def save_file(self) -> None:
        content = self.text_area.get("1.0", "end-1c")
        if not messagebox.askyesno(
            "Confirm Save",
            f"Overwrite '{self.relative_path}' on {self.server_name}?",
            parent=self.parent_toplevel,
        ):
            return

        self.save_btn.configure(state="disabled")
        self.status_lbl.configure(text="Uploading...")
        clear_queue(self.queue)

        threading.Thread(
            target=sftp_logic.upload_file_task,
            args=(self.config, self.relative_path, content, self.queue, self.server_name),
            daemon=True,
        ).start()

        poll_queue(
            self,
            self.queue,
            self._handle_save_queue_item,
            on_ui_error=self._handle_save_ui_error,
        )

    def _handle_save_ui_error(self, exc: Exception) -> None:
        messagebox.showerror("GUI Error", f"Error saving file: {exc}", parent=self.parent_toplevel)
        self.save_btn.configure(state="normal")
        self.status_lbl.configure(text="Error.")

    def _handle_save_queue_item(self, result: Any) -> bool:
        if isinstance(result, Exception):
            messagebox.showerror(
                "Upload Error",
                f"Failed to upload:\n{result}",
                parent=self.parent_toplevel,
            )
            self.save_btn.configure(state="normal")
            self.status_lbl.configure(text="Error.")
            return False

        if isinstance(result, str):
            self.status_lbl.configure(text=result)
            return True

        if isinstance(result, dict) and result.get("status") == "upload_complete":
            messagebox.showinfo("Success", "File saved successfully.", parent=self.parent_toplevel)
            self.save_btn.configure(state="normal")
            self.status_lbl.configure(text="Saved.")
            if self.on_save_callback:
                self.on_save_callback()
            self.parent_toplevel.destroy()
            return False

        return True
