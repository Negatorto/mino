from __future__ import annotations

import queue
import threading
from tkinter import messagebox
from typing import Any, Callable, Sequence

import customtkinter as ctk

from core.models import KEEP_CURRENT, ComparisonRow
from core import sftp_logic
from ui.helpers import clear_queue, poll_queue


class AttributesWindow(ctk.CTkFrame):
    """Toplevel frame for viewing and changing file ownership (chown) and permissions (chmod)."""

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        s1_config: dict[str, Any],
        s2_config: dict[str, Any],
        selected_items_data: Sequence[ComparisonRow | Sequence[Any]],
        status_callback: Callable[[str], None],
        refresh_callback: Callable[[], None] | None,
    ) -> None:
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.s1_config = s1_config
        self.s2_config = s2_config
        self.rows: list[ComparisonRow] = [
            item if isinstance(item, ComparisonRow) else ComparisonRow.from_tree_values(item)
            for item in selected_items_data
        ]
        self.status_callback = status_callback
        self.parent_toplevel = parent_toplevel
        self.refresh_callback = refresh_callback

        first_row = self.rows[0]
        first_owner, first_group = first_row.primary_owner_and_group()
        first_octal = first_row.primary_octal_perms()

        all_same_owner_group = all(r.owner_group == first_row.owner_group for r in self.rows)
        all_same_perms = all(r.octal_perms == first_row.octal_perms for r in self.rows)

        initial_owner = first_owner if (all_same_owner_group and first_owner and first_owner != "?") else KEEP_CURRENT
        initial_group = first_group if (all_same_owner_group and first_group and first_group != "?") else KEEP_CURRENT
        initial_perms = first_octal if (all_same_perms and first_octal != "?") else ""

        self.owner_var = ctk.StringVar(value=initial_owner)
        self.group_var = ctk.StringVar(value=initial_group)
        self.new_perms = ctk.StringVar(value=initial_perms)

        self.fetch_queue: queue.Queue[Any] = queue.Queue()
        self.change_queue: queue.Queue[Any] = queue.Queue()
        self.fetch_tasks_pending = 0
        self.tasks_running = 0
        self.all_users: set[str] = set()
        self.all_groups: set[str] = set()

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text="File(s):", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=5, pady=5
        )
        file_display_text = (
            f"{len(self.rows)} files selected" if len(self.rows) > 1 else first_row.relative_path
        )
        ctk.CTkLabel(self, text=file_display_text, wraplength=400, justify="left").grid(
            row=0, column=1, sticky="w", padx=5, pady=5
        )

        ctk.CTkLabel(self, text="Owner:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.owner_menu = ctk.CTkOptionMenu(
            self, variable=self.owner_var, values=["Loading..."], state="disabled"
        )
        self.owner_menu.grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ctk.CTkLabel(self, text="Group:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.group_menu = ctk.CTkOptionMenu(
            self, variable=self.group_var, values=["Loading..."], state="disabled"
        )
        self.group_menu.grid(row=2, column=1, sticky="ew", padx=5, pady=5)

        owner_summary = (
            f"(Current Owner:Group: {first_row.owner_group})"
            if all_same_owner_group
            else "(Current Owner:Group: Mixed)"
        )
        ctk.CTkLabel(self, text=owner_summary, font=ctk.CTkFont(size=10)).grid(
            row=3, column=1, sticky="w", padx=5
        )

        ctk.CTkLabel(self, text="Perms (octal):").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.perms_entry = ctk.CTkEntry(
            self,
            textvariable=self.new_perms,
            placeholder_text="e.g. 755 (leave empty to keep current)",
        )
        self.perms_entry.grid(row=4, column=1, sticky="ew", padx=5, pady=5)

        perms_summary = (
            f"(Current: {first_row.symbolic_perms} / {first_row.octal_perms})"
            if all_same_perms
            else "(Current Permissions: Mixed)"
        )
        ctk.CTkLabel(self, text=perms_summary, font=ctk.CTkFont(size=10)).grid(
            row=5, column=1, sticky="w", padx=5
        )

        has_test_files = any(r.exists_on_test for r in self.rows)
        has_prod_files = any(r.exists_on_prod for r in self.rows)

        self.target_test = ctk.BooleanVar(value=has_test_files)
        self.target_prod = ctk.BooleanVar(value=has_prod_files)

        ctk.CTkLabel(self, text="Apply to:").grid(row=6, column=0, sticky="w", padx=5, pady=10)
        server_frame = ctk.CTkFrame(self, fg_color="transparent")
        server_frame.grid(row=6, column=1, sticky="ew", padx=5, pady=10)

        self.check_test = ctk.CTkCheckBox(server_frame, text="TEST Server", variable=self.target_test)
        self.check_test.pack(side="left", padx=(0, 20))
        self.check_prod = ctk.CTkCheckBox(server_frame, text="PROD Server", variable=self.target_prod)
        self.check_prod.pack(side="left")

        if not has_test_files:
            self.check_test.configure(state="disabled")
        if not has_prod_files:
            self.check_prod.configure(state="disabled")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=7, column=0, columnspan=2, pady=10)

        self.apply_button = ctk.CTkButton(
            button_frame, text="Apply Changes", command=self.start_change_task
        )
        self.apply_button.pack(side="left", padx=10)
        self.cancel_button = ctk.CTkButton(
            button_frame, text="Cancel", command=self.parent_toplevel.destroy, fg_color="gray"
        )
        self.cancel_button.pack(side="left", padx=10)

        self.fetch_remote_lists()

    def _populate_dropdown(
        self,
        menu: ctk.CTkOptionMenu,
        var: ctk.StringVar,
        fetched_items: set[str],
    ) -> None:
        current = var.get()
        options = [KEEP_CURRENT] + sorted(fetched_items)
        if current and current not in options and current not in ("?", "Loading..."):
            options.insert(1, current)
        menu.configure(state="normal", values=options)
        menu.set(current if current in options else KEEP_CURRENT)

    def fetch_remote_lists(self) -> None:
        """Fetches user and group lists from active target servers using one connection per server."""
        clear_queue(self.fetch_queue)
        self.fetch_tasks_pending = 0

        for enabled, cfg, name in (
            (self.target_test.get(), self.s1_config, "TEST"),
            (self.target_prod.get(), self.s2_config, "PROD"),
        ):
            if enabled:
                self.fetch_tasks_pending += 1
                threading.Thread(
                    target=sftp_logic.get_users_and_groups_task,
                    args=(cfg, self.fetch_queue, name),
                    daemon=True,
                ).start()

        if self.fetch_tasks_pending > 0:
            poll_queue(
                self,
                self.fetch_queue,
                self._handle_fetch_queue_item,
                on_ui_error=self._handle_fetch_ui_error,
            )
        else:
            self._populate_dropdown(self.owner_menu, self.owner_var, set())
            self._populate_dropdown(self.group_menu, self.group_var, set())

    def _handle_fetch_ui_error(self, exc: Exception) -> None:
        self.show_error("GUI Error", f"Error processing user/group lists: {exc}")
        self._populate_dropdown(self.owner_menu, self.owner_var, self.all_users)
        self._populate_dropdown(self.group_menu, self.group_var, self.all_groups)

    def _handle_fetch_queue_item(self, result: Any) -> bool:
        if isinstance(result, Exception):
            self.fetch_tasks_pending -= 1
            self.show_error("Fetch Users/Groups Error", f"Failed to get user/group list:\n{result}")
        elif isinstance(result, dict) and ("users" in result or "groups" in result):
            self.fetch_tasks_pending -= 1
            self.all_users.update(result.get("users", []))
            self.all_groups.update(result.get("groups", []))
        else:
            return True

        if self.fetch_tasks_pending <= 0:
            self._populate_dropdown(self.owner_menu, self.owner_var, self.all_users)
            self._populate_dropdown(self.group_menu, self.group_var, self.all_groups)
            return False
        return True

    @staticmethod
    def _clean_choice(value: str) -> str:
        cleaned = value.strip()
        if cleaned in ("", KEEP_CURRENT, "?", "Loading..."):
            return ""
        return cleaned

    def start_change_task(self) -> None:
        """Validates inputs and applies attribute changes in batched connections per server."""
        owner = self._clean_choice(self.owner_var.get())
        group = self._clean_choice(self.group_var.get())
        perms_str = self.new_perms.get().strip()

        if perms_str:
            try:
                mode_val = int(perms_str, 8)
                if not (0 <= mode_val <= 0o7777):
                    raise ValueError
            except ValueError:
                self.show_error(
                    "Invalid Permissions",
                    f"'{perms_str}' is not a valid octal permission (e.g., 644, 755, 0755).",
                )
                return

        if not owner and not group and not perms_str:
            self.show_error("No Changes", "Specify at least an Owner, Group, or Permissions value to apply.")
            return

        test_paths = [r.relative_path for r in self.rows if self.target_test.get() and r.exists_on_test]
        prod_paths = [r.relative_path for r in self.rows if self.target_prod.get() and r.exists_on_prod]

        self.tasks_running = len(test_paths) + len(prod_paths)
        if self.tasks_running == 0:
            self.status_callback("No servers selected or no relevant files on selected servers.")
            return

        self.apply_button.configure(state="disabled")
        self.status_callback("Applying attribute changes...")
        clear_queue(self.change_queue)

        if test_paths:
            threading.Thread(
                target=sftp_logic.change_attributes_batch_task,
                args=(self.s1_config, test_paths, owner, group, perms_str, self.change_queue, "TEST"),
                daemon=True,
            ).start()

        if prod_paths:
            threading.Thread(
                target=sftp_logic.change_attributes_batch_task,
                args=(self.s2_config, prod_paths, owner, group, perms_str, self.change_queue, "PROD"),
                daemon=True,
            ).start()

        poll_queue(
            self,
            self.change_queue,
            self._handle_change_queue_item,
            on_ui_error=self._handle_change_ui_error,
        )

    def _handle_change_ui_error(self, exc: Exception) -> None:
        self.show_error("GUI Error", f"Error updating UI: {exc}")
        self.stop_loading()

    def _handle_change_queue_item(self, result: Any) -> bool:
        if isinstance(result, str):
            if result.endswith(":Success"):
                self.tasks_running -= 1
                self.status_callback(f"Applied: {result.removesuffix(':Success')}")
            elif result.startswith("Error:"):
                self.tasks_running -= 1
                parts = result.split(":", 2)
                server_info = parts[1] if len(parts) > 1 else "Unknown"
                error_msg = parts[2] if len(parts) > 2 else result
                self.show_error(f"Error on {server_info}", error_msg)
            else:
                self.status_callback(result)
        elif isinstance(result, Exception):
            self.tasks_running -= 1
            self.show_error("Error", f"An unexpected error occurred:\n{result}")

        if self.tasks_running <= 0:
            self.status_callback("All changes processed. Refreshing...")
            self.stop_loading()
            if self.refresh_callback:
                self.refresh_callback()
            self.parent_toplevel.after(500, self.parent_toplevel.destroy)
            return False

        return True

    def stop_loading(self) -> None:
        self.apply_button.configure(state="normal")

    def show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self.parent_toplevel)
