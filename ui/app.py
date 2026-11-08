from __future__ import annotations

import json
import os
import platform
import queue
import subprocess
import threading
from tkinter import Menu, filedialog, messagebox, ttk
from typing import Any, Sequence
import webbrowser

import customtkinter as ctk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

from core.models import (
    DEFAULT_NOTE_KEYWORD,
    DEFAULT_SSH_PORT,
    ComparisonRow,
    FileStatus,
    format_transition,
)
from core import sftp_logic
from ui.attributes import AttributesWindow
from ui.editor import DiffWindow
from ui.helpers import (
    clear_queue,
    create_centered_toplevel,
    get_theme_palette,
    poll_queue,
)
from ui.settings import CloneSettingsWindow, IgnorePatternsWindow, NoteKeywordWindow
from ui.sync import SyncWindow

BASE_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
WORKSPACES_DIR = os.path.join(BASE_DIR, "workspaces")


class App(ctk.CTk):
    """Main Application Window for MINO."""

    SERVER_FIELDS = ("host", "port", "user", "pass", "path")

    def __init__(self) -> None:
        super().__init__()

        self.title("MINO: Mirroring Integrity Network Operations")
        self.geometry("900x700")

        self._icon_photo: Any = None
        self._setup_icons()

        self.result_queue: queue.Queue[Any] = queue.Queue()
        self.comparison_results: dict[str, Any] | None = None
        self._loading_workspace = False
        self._sort_reverse: dict[str, bool] = {}

        self.clone_options: dict[str, ctk.BooleanVar] = {
            field: ctk.BooleanVar(value=True) for field in self.SERVER_FIELDS
        }

        self.create_custom_menubar()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        input_frame.grid_columnconfigure((0, 1), weight=1)

        self.server1_vars = self.create_server_widgets(input_frame, "TEST Server", 0)
        self.server2_vars = self.create_server_widgets(input_frame, "PRODUCTION Server", 1)

        for field in self.SERVER_FIELDS:
            self.server1_vars[field].trace_add("write", lambda *_args, f=field: self.on_clone_input(f))

        control_frame = ctk.CTkFrame(self)
        control_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        control_frame.grid_columnconfigure(0, weight=1)

        button_subframe = ctk.CTkFrame(control_frame, fg_color="transparent")
        button_subframe.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.compare_button = ctk.CTkButton(
            button_subframe, text="Compare Folders", command=self.start_comparison
        )
        self.compare_button.pack(side="left", padx=(0, 10))

        self.diff_button = ctk.CTkButton(
            button_subframe,
            text="Compare Selected File",
            command=self.open_diff_window,
            state="disabled",
        )
        self.diff_button.pack(side="left", padx=(0, 10))

        self.sync_button = ctk.CTkButton(
            button_subframe,
            text="Sync TEST -> PROD",
            command=lambda: self.open_sync_window(reverse=False),
            state="disabled",
        )
        self.sync_button.pack(side="left", padx=(0, 10))

        self.sync_reverse_button = ctk.CTkButton(
            button_subframe,
            text="Sync PROD -> TEST",
            command=lambda: self.open_sync_window(reverse=True),
            state="disabled",
        )
        self.sync_reverse_button.pack(side="left", padx=(0, 10))

        self.scan_notes_var = ctk.BooleanVar(value=False)
        self.scan_notes_switch = ctk.CTkSwitch(
            button_subframe, text="Scan Notes", variable=self.scan_notes_var
        )
        self.scan_notes_switch.pack(side="left", padx=20)

        self.note_keyword_var = ctk.StringVar(value=DEFAULT_NOTE_KEYWORD)
        self.ignore_patterns_var = ctk.StringVar(value="")

        result_frame = ctk.CTkFrame(self)
        result_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        result_frame.grid_rowconfigure(0, weight=1)
        result_frame.grid_columnconfigure(0, weight=1)

        self.create_treeview(result_frame)

        status_frame = ctk.CTkFrame(self, height=30)
        status_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))

        self.status_var = ctk.StringVar(value="Ready (Using SFTP / Local).")
        self.status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", fill="x", padx=10, expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=150)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)
        self.progress_bar.configure(mode="indeterminate")

        self.note_display_var = ctk.StringVar(value="")
        self.note_display_label = ctk.CTkLabel(
            status_frame,
            textvariable=self.note_display_var,
            text_color=("red", "#FFCC00"),
            font=("Helvetica", 12, "bold"),
        )
        self.note_display_label.pack(side="right", padx=10)

    def _setup_icons(self) -> None:
        try:
            icon_path_ico = os.path.join(ASSETS_DIR, "mino.ico")
            if os.path.exists(icon_path_ico):
                self.iconbitmap(icon_path_ico)
        except Exception:
            pass

        if Image and ImageTk:
            try:
                icon_path_png = os.path.join(ASSETS_DIR, "mino_icon.png")
                if os.path.exists(icon_path_png):
                    icon_img = Image.open(icon_path_png)
                    # Keep strong reference so Tk PhotoImage is not garbage collected
                    self._icon_photo = ImageTk.PhotoImage(icon_img)
                    self.iconphoto(False, self._icon_photo)
            except Exception as exc:
                print(f"Error loading icon: {exc}")

    def _get_server_configs(self) -> tuple[dict[str, str], dict[str, str]]:
        s1 = {k: v.get() for k, v in self.server1_vars.items()}
        s2 = {k: v.get() for k, v in self.server2_vars.items()}
        return s1, s2

    def _get_row_from_item(self, item_id: str) -> ComparisonRow | None:
        values = self.tree.item(item_id).get("values")
        if not values:
            return None
        return ComparisonRow.from_tree_values(values)

    def create_custom_menubar(self) -> None:
        """Creates the top custom menu bar."""
        menubar_frame = ctk.CTkFrame(self, height=30, corner_radius=0, fg_color=("gray85", "gray17"))
        menubar_frame.grid(row=0, column=0, sticky="ew")

        btn_kwargs = {
            "fg_color": "transparent",
            "text_color": ("black", "white"),
            "hover_color": ("gray70", "gray25"),
        }
        self.file_btn = ctk.CTkButton(
            menubar_frame, text="File", width=60, command=self.show_file_menu, **btn_kwargs
        )
        self.file_btn.pack(side="left", padx=2)

        self.settings_btn = ctk.CTkButton(
            menubar_frame, text="Settings", width=80, command=self.show_settings_menu, **btn_kwargs
        )
        self.settings_btn.pack(side="left", padx=2)

        self.about_btn = ctk.CTkButton(
            menubar_frame, text="About", width=60, command=self.open_about_window, **btn_kwargs
        )
        self.about_btn.pack(side="left", padx=2)

    @staticmethod
    def _popup_below_button(menu: Menu, button: ctk.CTkButton) -> None:
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height()
        menu.tk_popup(x, y)

    def show_file_menu(self) -> None:
        menu = Menu(self, tearoff=0)
        menu.add_command(label="Save Workspace (Safe)", command=self.save_workspace_safe)
        menu.add_command(
            label="Save Sensitive Workspace (With Passwords)",
            command=self.save_workspace_sensitive,
        )
        menu.add_command(label="Load Workspace", command=self.load_workspace)
        menu.add_separator()
        menu.add_command(label="Exit", command=self.quit)
        self._popup_below_button(menu, self.file_btn)

    def show_settings_menu(self) -> None:
        menu = Menu(self, tearoff=0)
        menu.add_command(label="Clone Options...", command=self.open_clone_settings)
        menu.add_command(label="Set Note Keyword...", command=self.open_note_keyword_settings)
        menu.add_command(label="Set Ignore Patterns...", command=self.open_ignore_patterns_settings)

        theme_menu = Menu(menu, tearoff=0)
        menu.add_cascade(label="Themes", menu=theme_menu)
        for theme_name in ("Light", "Dark", "System"):
            theme_menu.add_command(
                label=theme_name,
                command=lambda mode=theme_name: self.change_appearance_mode(mode),
            )

        self._popup_below_button(menu, self.settings_btn)

    def save_workspace_safe(self) -> None:
        """Saves configuration excluding passwords."""
        self._save_workspace(include_passwords=False)

    def save_workspace_sensitive(self) -> None:
        """Saves configuration including passwords."""
        self._save_workspace(include_passwords=True)

    def _save_workspace(self, include_passwords: bool = False) -> None:
        os.makedirs(WORKSPACES_DIR, exist_ok=True)
        title = "Save Sensitive Workspace" if include_passwords else "Save Workspace"
        file_path = filedialog.asksaveasfilename(
            initialdir=WORKSPACES_DIR,
            title=title,
            defaultextension=".json",
            filetypes=(("JSON Files", "*.json"), ("All Files", "*.*")),
            parent=self,
        )
        if not file_path:
            return

        s1_data, s2_data = self._get_server_configs()
        if not include_passwords:
            s1_data["pass"] = ""
            s2_data["pass"] = ""

        data = {
            "server1": s1_data,
            "server2": s2_data,
            "clone_options": {k: v.get() for k, v in self.clone_options.items()},
            "scan_notes": self.scan_notes_var.get(),
            "note_keyword": self.note_keyword_var.get(),
            "ignore_patterns": self.ignore_patterns_var.get(),
        }

        try:
            with open(file_path, "w", encoding="utf-8") as f_out:
                json.dump(data, f_out, indent=4)
            self.update_status(f"Workspace saved to {os.path.basename(file_path)}")
        except Exception as exc:
            self.show_error("Save Error", f"Could not save workspace:\n{exc}")

    def load_workspace(self) -> None:
        """Loads server configurations and settings from a JSON file."""
        os.makedirs(WORKSPACES_DIR, exist_ok=True)
        file_path = filedialog.askopenfilename(
            initialdir=WORKSPACES_DIR,
            title="Load Workspace",
            filetypes=(("JSON Files", "*.json"), ("All Files", "*.*")),
            parent=self,
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f_in:
                data = json.load(f_in)

            self._loading_workspace = True
            try:
                if "clone_options" in data and isinstance(data["clone_options"], dict):
                    for k, v in data["clone_options"].items():
                        if k in self.clone_options:
                            self.clone_options[k].set(bool(v))
                elif "clone_enabled" in data:
                    val = bool(data["clone_enabled"])
                    for v in self.clone_options.values():
                        v.set(val)

                for k, v in data.get("server1", {}).items():
                    if k in self.server1_vars:
                        self.server1_vars[k].set(str(v))

                for k, v in data.get("server2", {}).items():
                    if k in self.server2_vars:
                        self.server2_vars[k].set(str(v))
            finally:
                self._loading_workspace = False

            if "scan_notes" in data:
                self.scan_notes_var.set(bool(data["scan_notes"]))
            if "note_keyword" in data and str(data["note_keyword"]).strip():
                self.note_keyword_var.set(str(data["note_keyword"]).strip())
            if "ignore_patterns" in data:
                self.ignore_patterns_var.set(str(data["ignore_patterns"]))

            self.update_status(f"Workspace loaded from {os.path.basename(file_path)}")
        except Exception as exc:
            self.show_error("Load Error", f"Could not load workspace:\n{exc}")

    def create_server_widgets(
        self, parent: ctk.CTkBaseClass, title: str, col: int
    ) -> dict[str, ctk.StringVar]:
        """Creates the connection input panel for a target environment."""
        frame = ctk.CTkFrame(parent, border_width=1)
        frame.grid(row=0, column=col, sticky="nsew", padx=5, pady=5)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 10)
        )

        server_vars = {
            "host": ctk.StringVar(),
            "port": ctk.StringVar(value=str(DEFAULT_SSH_PORT)),
            "user": ctk.StringVar(),
            "pass": ctk.StringVar(),
            "path": ctk.StringVar(),
        }

        ctk.CTkLabel(frame, text="Host:").grid(row=1, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(
            frame,
            textvariable=server_vars["host"],
            placeholder_text="Leave empty or 'local'",
            placeholder_text_color=["gray10", "gray90"],
        ).grid(row=1, column=1, sticky="ew", padx=10, pady=3)

        ctk.CTkLabel(frame, text="Port:").grid(row=2, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["port"], width=60).grid(
            row=2, column=1, sticky="w", padx=10, pady=3
        )

        ctk.CTkLabel(frame, text="Username:").grid(row=3, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["user"]).grid(
            row=3, column=1, sticky="ew", padx=10, pady=3
        )

        ctk.CTkLabel(frame, text="Password:").grid(row=4, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["pass"], show="*").grid(
            row=4, column=1, sticky="ew", padx=10, pady=3
        )

        ctk.CTkLabel(frame, text="Remote Path:").grid(row=5, column=0, sticky="w", padx=10, pady=(3, 10))
        ctk.CTkEntry(frame, textvariable=server_vars["path"]).grid(
            row=5, column=1, sticky="ew", padx=10, pady=(3, 10)
        )

        return server_vars

    def create_treeview(self, parent: ctk.CTkBaseClass) -> None:
        """Creates and styles the comparison results Treeview."""
        self.style = ttk.Style(self)
        visible_cols = ("Status", "Path", "Note", "Owner", "Perms")
        all_cols = (*visible_cols, "OctalPerms")
        self.tree = ttk.Treeview(
            parent,
            columns=all_cols,
            displaycolumns=visible_cols,
            show="headings",
        )

        headings = {
            "Status": ("Status", 150, "w"),
            "Path": ("File Path", 410, "w"),
            "Note": ("Note", 60, "center"),
            "Owner": ("Owner:Group", 120, "w"),
            "Perms": ("Permissions", 120, "w"),
        }
        for col_id, (label, width, anchor) in headings.items():
            self.tree.heading(
                col_id,
                text=label,
                command=lambda c=col_id: self._sort_treeview_by_column(c),
            )
            self.tree.column(col_id, width=width, anchor=anchor)

        scrollbar = ctk.CTkScrollbar(parent, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.grid(row=0, column=0, sticky="nsew")

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.show_tree_context_menu)
        if platform.system() == "Darwin":
            self.tree.bind("<Button-2>", self.show_tree_context_menu)

        self.update_treeview_style()
        self.update_treeview_tag_colors()

    def _sort_treeview_by_column(self, col_id: str) -> None:
        reverse = self._sort_reverse.get(col_id, False)
        items = [(self.tree.set(item_id, col_id).lower(), item_id) for item_id in self.tree.get_children("")]
        items.sort(key=lambda pair: pair[0], reverse=reverse)
        for idx, (_val, item_id) in enumerate(items):
            self.tree.move(item_id, "", idx)
        self._sort_reverse[col_id] = not reverse

    def show_tree_context_menu(self, event: Any) -> None:
        """Shows a context menu on right-click."""
        try:
            selected_items = list(self.tree.selection())
            item_under_mouse = self.tree.identify_row(event.y)
            if item_under_mouse and item_under_mouse not in selected_items:
                self.tree.selection_set(item_under_mouse)
                selected_items = list(self.tree.selection())

            if not selected_items:
                return

            rows_by_id = {
                item_id: row
                for item_id in selected_items
                if (row := self._get_row_from_item(item_id)) is not None
            }
            if not rows_by_id:
                return

            context_menu = Menu(self.tree, tearoff=0)

            can_diff = len(rows_by_id) == 1 and next(iter(rows_by_id.values())).can_diff
            context_menu.add_command(
                label="Compare Selected File",
                command=self.open_diff_window,
                state="normal" if can_diff else "disabled",
            )

            syncable_to_prod = [iid for iid, row in rows_by_id.items() if row.can_sync_to_prod]
            syncable_to_test = [iid for iid, row in rows_by_id.items() if row.can_sync_to_test]

            self._add_context_sync_item(context_menu, syncable_to_prod, "TEST -> PROD", reverse=False)
            self._add_context_sync_item(context_menu, syncable_to_test, "PROD -> TEST", reverse=True)

            items_snapshot = tuple(rows_by_id.keys())
            context_menu.add_command(
                label="Change Attributes...",
                command=lambda ids=items_snapshot: self.open_attributes_window(ids),
            )
            context_menu.tk_popup(event.x_root, event.y_root)
        except Exception as exc:
            print(f"Error showing context menu: {exc}")

    def _add_context_sync_item(
        self,
        menu: Menu,
        item_ids: list[str],
        direction_label: str,
        *,
        reverse: bool,
    ) -> None:
        if len(item_ids) == 1:
            first_id = item_ids[0]
            menu.add_command(
                label=f"Sync File ({direction_label})",
                command=lambda iid=first_id, rev=reverse: self.sync_single_file(iid, reverse=rev),
            )
        elif len(item_ids) > 1:
            ids_tuple = tuple(item_ids)
            menu.add_command(
                label=f"Sync {len(item_ids)} Files ({direction_label})",
                command=lambda ids=ids_tuple, rev=reverse: self.sync_selected_files(ids, reverse=rev),
            )
        else:
            menu.add_command(label=f"Sync File ({direction_label})", state="disabled")

    def sync_single_file(self, item_id: str, reverse: bool = False) -> None:
        """Synchronizes a single selected file."""
        self._start_selected_sync([item_id], reverse=reverse)

    def sync_selected_files(self, item_ids: Sequence[str], reverse: bool = False) -> None:
        """Synchronizes multiple selected files in one session."""
        self._start_selected_sync(item_ids, reverse=reverse)

    def _start_selected_sync(self, item_ids: Sequence[str], *, reverse: bool = False) -> None:
        files_to_sync = [
            row.relative_path
            for item_id in item_ids
            if (row := self._get_row_from_item(item_id)) is not None
        ]
        if not files_to_sync:
            return

        source_name = "PROD" if reverse else "TEST"
        dest_name = "TEST" if reverse else "PROD"
        direction = f"{source_name} to {dest_name}"
        is_single = len(files_to_sync) == 1

        prompt_title = "Confirm Sync" if is_single else "Confirm Batch Sync"
        prompt_msg = (
            f"Are you sure you want to overwrite/copy '{files_to_sync[0]}' ({direction})?"
            if is_single
            else f"Are you sure you want to sync {len(files_to_sync)} files ({direction})?"
        )
        if not messagebox.askyesno(prompt_title, prompt_msg, parent=self):
            return

        status_msg = (
            f"Syncing {files_to_sync[0]} ({direction})..."
            if is_single
            else f"Starting batch sync for {len(files_to_sync)} files ({direction})..."
        )
        self.update_status(status_msg)
        self.progress_bar.start()
        clear_queue(self.result_queue)

        s1_config, s2_config = self._get_server_configs()
        src_cfg, dst_cfg = (s2_config, s1_config) if reverse else (s1_config, s2_config)

        if is_single:
            threading.Thread(
                target=sftp_logic.sync_single_file_task,
                args=(src_cfg, dst_cfg, files_to_sync[0], self.result_queue, source_name, dest_name),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=sftp_logic.sync_multiple_files_task,
                args=(src_cfg, dst_cfg, files_to_sync, self.result_queue, source_name, dest_name),
                daemon=True,
            ).start()

        poll_queue(
            self,
            self.result_queue,
            lambda item: self._handle_selected_sync_item(item, is_single=is_single),
            on_ui_error=self._handle_queue_ui_error,
        )

    def _handle_queue_ui_error(self, exc: Exception) -> None:
        self.show_error("GUI Error", f"Error updating UI: {exc}")
        self.stop_loading()

    def _handle_selected_sync_item(self, result: Any, *, is_single: bool) -> bool:
        if isinstance(result, Exception):
            self.show_error("Sync Error", f"An error occurred:\n{result}")
            self.stop_loading()
            return False

        if isinstance(result, str):
            self.update_status(result)
            return True

        if isinstance(result, dict):
            status = result.get("status")
            if status == "single_sync_complete":
                self.update_status(f"Synced {result.get('file')} successfully.")
                if is_single:
                    self.stop_loading()
                    if messagebox.askyesno("Sync Complete", "File synced. Refresh comparison?", parent=self):
                        self.start_comparison()
                    return False
                return True

            if status == "batch_sync_complete":
                self.stop_loading()
                if messagebox.askyesno(
                    "Batch Sync Complete",
                    "All selected files synced. Refresh comparison?",
                    parent=self,
                ):
                    self.start_comparison()
                return False

        return True

    def update_treeview_style(self, mode: str | None = None) -> None:
        """Updates the ttk.Treeview style to match the active CustomTkinter theme."""
        tv = get_theme_palette(mode)["treeview"]
        self.style.theme_use("clam")
        self.style.configure(
            "Treeview",
            background=tv["bg"],
            fieldbackground=tv["bg"],
            foreground=tv["fg"],
        )
        self.style.map(
            "Treeview",
            background=[("selected", tv["selected_bg"])],
            foreground=[("selected", "#FFFFFF")],
        )
        self.style.configure(
            "Treeview.Heading",
            background=tv["heading_bg"],
            foreground=tv["fg"],
            font=("Helvetica", 10, "bold"),
        )
        self.style.map("Treeview.Heading", background=[("active", tv["heading_bg"])])

    def update_treeview_tag_colors(self, mode: str | None = None) -> None:
        """Updates the Treeview row tag colors for Light/Dark mode."""
        tags = get_theme_palette(mode)["tags"]
        self.tree.tag_configure("different", background=tags["different_bg"], foreground=tags["different_fg"])
        self.tree.tag_configure("solo_test", background=tags["solo_test_bg"], foreground=tags["solo_test_fg"])
        self.tree.tag_configure("solo_prod", background=tags["solo_prod_bg"], foreground=tags["solo_prod_fg"])
        self.tree.tag_configure("identical", foreground=tags["identical_fg"])

    def change_appearance_mode(self, new_mode_str: str) -> None:
        """Switches the application appearance mode ('Light', 'Dark', 'System')."""
        ctk.set_appearance_mode(new_mode_str)
        # Resolve after set_appearance_mode so 'System' maps to the actual OS theme
        self.update_treeview_style(None)
        self.update_treeview_tag_colors(None)

    def on_clone_input(self, field_key: str) -> None:
        """Clones input from TEST to PROD if enabled and not mid-workspace-load."""
        if self._loading_workspace:
            return
        opt = self.clone_options.get(field_key)
        if opt is not None and opt.get():
            self.server2_vars[field_key].set(self.server1_vars[field_key].get())

    def on_tree_select(self, _event: Any = None) -> None:
        """Updates Diff button state and note display banner on row selection."""
        selected_items = self.tree.selection()
        if not selected_items:
            self.diff_button.configure(state="disabled")
            self.note_display_var.set("")
            return

        row = self._get_row_from_item(selected_items[0])
        if row is None:
            self.diff_button.configure(state="disabled")
            self.note_display_var.set("")
            return

        note_content = ""
        if self.comparison_results:
            for source_key in ("files_s1", "files_s2"):
                meta = self.comparison_results.get(source_key, {}).get(row.relative_path)
                if meta and meta.get("has_note"):
                    raw_note = meta["has_note"]
                    note_content = raw_note if isinstance(raw_note, str) else self.note_keyword_var.get()
                    break

        self.note_display_var.set(note_content)
        self.diff_button.configure(state="normal" if row.can_diff else "disabled")

    def on_tree_double_click(self, _event: Any = None) -> None:
        """Opens DiffWindow on double-clicking a comparable file."""
        selected_items = self.tree.selection()
        if not selected_items:
            return
        row = self._get_row_from_item(selected_items[0])
        if row is not None and row.can_diff:
            self.open_diff_window()

    def open_diff_window(self) -> None:
        """Opens the side-by-side DiffWindow for the currently selected file."""
        selected = self.tree.selection()
        if not selected or (row := self._get_row_from_item(selected[0])) is None:
            self.show_error("Error", "No valid file selected.")
            return

        s1_config, s2_config = self._get_server_configs()
        diff_top = create_centered_toplevel(
            self,
            f"Diff: {row.relative_path}",
            1100,
            700,
            modal_transient=False,
        )
        DiffWindow(diff_top, s1_config, s2_config, row)

    def open_attributes_window(self, selected_item_ids: Sequence[str]) -> None:
        """Opens AttributesWindow for the selected Treeview row(s)."""
        rows = [
            row
            for item_id in selected_item_ids
            if (row := self._get_row_from_item(item_id)) is not None
        ]
        if not rows:
            self.show_error("Error", "No file(s) selected.")
            return

        s1_config, s2_config = self._get_server_configs()
        attr_top = create_centered_toplevel(self, "Change Attributes", 500, 400)
        AttributesWindow(attr_top, s1_config, s2_config, rows, self.update_status, self.start_comparison)

    def open_sync_window(self, reverse: bool = False) -> None:
        """Opens SyncWindow for TEST -> PROD or PROD -> TEST."""
        if not self.comparison_results:
            self.show_error("Sync Error", "Please run a comparison first.")
            return

        s1_config, s2_config = self._get_server_configs()
        source_name = "PRODUCTION" if reverse else "TEST"
        dest_name = "TEST" if reverse else "PRODUCTION"

        sync_top = create_centered_toplevel(
            self, f"Synchronize {source_name} to {dest_name}", 600, 560
        )
        SyncWindow(
            sync_top,
            s1_config,
            s2_config,
            self.comparison_results,
            self.start_comparison,
            reverse=reverse,
        )

    def open_clone_settings(self) -> None:
        """Opens the Clone Settings dialog."""
        clone_top = create_centered_toplevel(self, "Clone Options", 320, 290, resizable=False)
        CloneSettingsWindow(clone_top, self.clone_options, self.on_clone_input)

    def open_note_keyword_settings(self) -> None:
        """Opens the Note Keyword configuration dialog."""
        keyword_top = create_centered_toplevel(self, "Set Note Keyword", 320, 160, resizable=False)
        NoteKeywordWindow(
            keyword_top,
            self.note_keyword_var,
            lambda k: self.update_status(f"Note keyword set to: {k}"),
        )

    def open_ignore_patterns_settings(self) -> None:
        """Opens the Ignore Patterns configuration dialog."""
        ignore_top = create_centered_toplevel(self, "Set Ignore Patterns", 400, 330)
        IgnorePatternsWindow(ignore_top, self.ignore_patterns_var)

    def update_status(self, message: str) -> None:
        """Updates the status bar message."""
        self.status_var.set(message)

    def start_comparison(self) -> None:
        """Starts the background folder comparison."""
        self.compare_button.configure(state="disabled")
        self.diff_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")
        self.sync_reverse_button.configure(state="disabled")
        self.note_display_var.set("")
        self.progress_bar.start()
        self.update_status("Starting comparison...")

        self.comparison_results = None
        self.tree.delete(*self.tree.get_children())
        clear_queue(self.result_queue)

        s1, s2 = self._get_server_configs()
        scan_arg: str | bool = self.note_keyword_var.get() if self.scan_notes_var.get() else False

        threading.Thread(
            target=sftp_logic.compare_folders_task,
            args=(s1, s2, self.result_queue, scan_arg, self.ignore_patterns_var.get()),
            daemon=True,
        ).start()

        poll_queue(
            self,
            self.result_queue,
            self._handle_comparison_queue_item,
            on_ui_error=self._handle_queue_ui_error,
        )

    def _handle_comparison_queue_item(self, result: Any) -> bool:
        if isinstance(result, Exception):
            self.show_error("Error", f"An error occurred:\n{result}")
            self.stop_loading()
            self.update_status("Comparison failed.")
            return False

        if isinstance(result, str):
            self.update_status(result)
            return True

        if isinstance(result, dict):
            self.populate_results(result)
            self.stop_loading()
            return False

        return True

    def stop_loading(self) -> None:
        """Stops the progress bar and re-enables the Compare button."""
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.compare_button.configure(state="normal")

    def _build_note_indicator(self, *metas: dict[str, Any]) -> str:
        if not self.scan_notes_var.get():
            return ""
        return "⚠" if any(m.get("has_note", False) for m in metas if m) else "✓"

    def _insert_single_side_rows(
        self,
        paths: Sequence[str],
        meta_map: dict[str, dict[str, Any]],
        status: FileStatus,
        tag: str,
    ) -> None:
        for rel_path in sorted(paths):
            meta = meta_map.get(rel_path, {})
            owner_group = f"{meta.get('owner', '?')}:{meta.get('group', '?')}"
            row = ComparisonRow(
                status=status.value,
                relative_path=rel_path,
                note=self._build_note_indicator(meta),
                owner_group=owner_group,
                symbolic_perms=meta.get("mode", "?"),
                octal_perms=meta.get("octal_mode", "?"),
            )
            self.tree.insert("", "end", values=row.to_tree_values(), tags=(tag,))

    def _insert_dual_side_rows(
        self,
        paths: Sequence[str],
        meta_s1: dict[str, dict[str, Any]],
        meta_s2: dict[str, dict[str, Any]],
        status: FileStatus,
        tag: str,
    ) -> None:
        for rel_path in sorted(paths):
            m1 = meta_s1.get(rel_path, {})
            m2 = meta_s2.get(rel_path, {})
            owner1 = f"{m1.get('owner', '?')}:{m1.get('group', '?')}"
            owner2 = f"{m2.get('owner', '?')}:{m2.get('group', '?')}"

            row = ComparisonRow(
                status=status.value,
                relative_path=rel_path,
                note=self._build_note_indicator(m1, m2),
                owner_group=format_transition(owner1, owner2),
                symbolic_perms=format_transition(m1.get("mode", "?"), m2.get("mode", "?")),
                octal_perms=format_transition(m1.get("octal_mode", "?"), m2.get("octal_mode", "?")),
            )
            self.tree.insert("", "end", values=row.to_tree_values(), tags=(tag,))

    def populate_results(self, results: dict[str, Any]) -> None:
        """Populates the Treeview with comparison results and updates action buttons."""
        self.update_status("Populating results...")
        self.comparison_results = results

        files_s1_meta = results.get("files_s1", {})
        files_s2_meta = results.get("files_s2", {})

        self._insert_dual_side_rows(
            results.get("different", []), files_s1_meta, files_s2_meta, FileStatus.DIFFERENT, "different"
        )
        self._insert_single_side_rows(
            results.get("only_on_1", []), files_s1_meta, FileStatus.ONLY_ON_TEST, "solo_test"
        )
        self._insert_single_side_rows(
            results.get("only_on_2", []), files_s2_meta, FileStatus.ONLY_ON_PROD, "solo_prod"
        )
        self._insert_dual_side_rows(
            results.get("identical", []), files_s1_meta, files_s2_meta, FileStatus.IDENTICAL, "identical"
        )

        self.diff_button.configure(state="disabled")

        has_changes = bool(
            results.get("different")
            or results.get("only_on_1")
            or results.get("only_on_2")
            or results.get("only_on_1_dirs")
            or results.get("only_on_2_dirs")
        )
        sync_state = "normal" if has_changes else "disabled"
        self.sync_button.configure(state=sync_state)
        self.sync_reverse_button.configure(state=sync_state)

        self.update_status(
            f"Done. Found {len(results.get('different', []))} different files, "
            f"{len(results.get('only_on_1', []))} only on TEST, "
            f"{len(results.get('only_on_2', []))} only on PROD."
        )

    def show_error(self, title: str, message: str) -> None:
        """Displays a modal error dialog."""
        messagebox.showerror(title, message, parent=self)

    def open_url(self, url: str) -> None:
        """Opens a URL, suppressing noisy Wayland/XDG stderr messages on Linux."""
        if platform.system() == "Linux":
            try:
                subprocess.Popen(
                    ["xdg-open", url],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass
        webbrowser.open(url)

    def open_about_window(self) -> None:
        """Opens the About MINO dialog."""
        about_toplevel = create_centered_toplevel(self, "About MINO", 340, 200, resizable=False)

        ctk.CTkLabel(about_toplevel, text="MINO", font=("Helvetica", 24, "bold")).pack(pady=(20, 2))
        ctk.CTkLabel(
            about_toplevel,
            text="Mirroring Integrity Network Operations",
            font=("Helvetica", 13),
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            about_toplevel,
            text="Created by Alessio Saponaro",
            font=("Helvetica", 13),
        ).pack(pady=(0, 15))
        ctk.CTkButton(
            about_toplevel,
            text="View on GitHub",
            height=30,
            command=lambda: self.open_url("https://github.com/Negatorto/mino"),
        ).pack(pady=5)


def main() -> None:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
