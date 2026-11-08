from __future__ import annotations

import queue
import threading
from tkinter import filedialog, messagebox
from typing import Any, Callable

import customtkinter as ctk

import sftp_logic
from ui_helpers import clear_queue, poll_queue


class SyncWindow(ctk.CTkFrame):
    """
    Toplevel frame for managing folder synchronization.
    Supports both TEST -> PROD and PROD -> TEST (reverse) directions.
    """

    def __init__(
        self,
        parent_toplevel: ctk.CTkToplevel,
        s1_config: dict[str, Any],
        s2_config: dict[str, Any],
        results: dict[str, Any],
        refresh_callback: Callable[[], None] | None,
        reverse: bool = False,
    ) -> None:
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True)

        self.parent_toplevel = parent_toplevel
        self.refresh_callback = refresh_callback
        self.sync_queue: queue.Queue[Any] = queue.Queue()
        self.reverse = reverse

        if not reverse:
            self.source_config = s1_config
            self.dest_config = s2_config
            self.source_name = "TEST"
            self.dest_name = "PROD"
            self.files_to_copy_list = results.get("only_on_1", [])
            self.files_to_overwrite_list = results.get("different", [])
            self.files_to_delete_list = results.get("only_on_2", [])
            self.dirs_to_create_list = results.get("only_on_1_dirs", [])
            self.dirs_to_delete_list = results.get("only_on_2_dirs", [])
            src_files_meta = results.get("files_s1", {})
            dst_files_meta = results.get("files_s2", {})
        else:
            self.source_config = s2_config
            self.dest_config = s1_config
            self.source_name = "PROD"
            self.dest_name = "TEST"
            self.files_to_copy_list = results.get("only_on_2", [])
            self.files_to_overwrite_list = results.get("different", [])
            self.files_to_delete_list = results.get("only_on_1", [])
            self.dirs_to_create_list = results.get("only_on_2_dirs", [])
            self.dirs_to_delete_list = results.get("only_on_1_dirs", [])
            src_files_meta = results.get("files_s2", {})
            dst_files_meta = results.get("files_s1", {})

        self.effective_results: dict[str, Any] = {
            "files_s1": src_files_meta,
            "files_s2": dst_files_meta,
            "only_on_1": self.files_to_copy_list,
            "different": self.files_to_overwrite_list,
            "only_on_1_dirs": self.dirs_to_create_list,
            "only_on_2": self.files_to_delete_list,
            "only_on_2_dirs": self.dirs_to_delete_list,
        }

        self.to_copy = len(self.files_to_copy_list)
        self.to_overwrite = len(self.files_to_overwrite_list)
        self.to_delete = len(self.files_to_delete_list)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. Summary Frame
        summary_frame = ctk.CTkFrame(self, border_width=1)
        summary_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        summary_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            summary_frame,
            text=f"Sync Summary ({self.source_name} -> {self.dest_name})",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=10, pady=5, sticky="w")

        ctk.CTkLabel(summary_frame, text=f"Files to copy to {self.dest_name}:").grid(
            row=1, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkLabel(summary_frame, text=str(self.to_copy)).grid(row=1, column=1, sticky="w", padx=10, pady=2)

        ctk.CTkLabel(summary_frame, text=f"Files to overwrite on {self.dest_name}:").grid(
            row=2, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkLabel(summary_frame, text=str(self.to_overwrite)).grid(
            row=2, column=1, sticky="w", padx=10, pady=2
        )

        ctk.CTkLabel(summary_frame, text=f"Files to delete from {self.dest_name}:").grid(
            row=3, column=0, sticky="w", padx=10, pady=(2, 10)
        )
        self.delete_count_label = ctk.CTkLabel(summary_frame, text=str(self.to_delete), text_color="orange")
        self.delete_count_label.grid(row=3, column=1, sticky="w", padx=10, pady=(2, 10))

        # 2. Options Frame
        options_frame = ctk.CTkFrame(self, border_width=1)
        options_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=0)
        options_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            options_frame,
            text=f"Synchronization & Backup Options for {self.dest_name}",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=10, pady=5, sticky="w")

        self.delete_var = ctk.BooleanVar(value=True)
        self.delete_checkbox = ctk.CTkCheckBox(
            options_frame,
            text=f"Delete extra files/folders only on {self.dest_name} (strict mirror)",
            variable=self.delete_var,
            command=self._on_delete_toggle,
        )
        self.delete_checkbox.grid(row=1, column=0, sticky="w", padx=10, pady=(4, 8))

        self.backup_var = ctk.StringVar(value="none")

        ctk.CTkRadioButton(
            options_frame,
            text=f"No Backup: Synchronize {self.dest_name} directly.",
            variable=self.backup_var,
            value="none",
        ).grid(row=2, column=0, sticky="w", padx=10, pady=4)

        ctk.CTkRadioButton(
            options_frame,
            text=f"Remote Backup: Copy {self.dest_name} folder on target before sync.",
            variable=self.backup_var,
            value="remote",
        ).grid(row=3, column=0, sticky="w", padx=10, pady=4)

        ctk.CTkRadioButton(
            options_frame,
            text=f"Local Backup: Download {self.dest_name} folder to this PC before sync.",
            variable=self.backup_var,
            value="local",
        ).grid(row=4, column=0, sticky="w", padx=10, pady=(4, 10))

        # 3. Action Frame
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        action_frame.grid_columnconfigure(0, weight=1)

        self.action_button = ctk.CTkButton(
            action_frame, text="Start Synchronization", command=self.start_sync_process
        )
        self.action_button.pack(side="left", padx=(0, 10))

        self.cancel_button = ctk.CTkButton(
            action_frame, text="Cancel", command=self.parent_toplevel.destroy, fg_color="gray"
        )
        self.cancel_button.pack(side="left")

        # 4. Status Bar
        status_frame = ctk.CTkFrame(self, height=30)
        status_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))

        self.status_var = ctk.StringVar(value="Ready to synchronize.")
        self.status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", fill="x", padx=10, expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=150)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)

    def _on_delete_toggle(self) -> None:
        if self.delete_var.get():
            self.delete_count_label.configure(text=str(self.to_delete), text_color="orange")
        else:
            self.delete_count_label.configure(text="0 (deletion disabled)", text_color="gray")

    def _start_progress(self) -> None:
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

    def start_sync_process(self) -> None:
        """Initiates optional backup followed by synchronization."""
        self.action_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.delete_checkbox.configure(state="disabled")
        clear_queue(self.sync_queue)

        backup_choice = self.backup_var.get()
        delete_on_dest = self.delete_var.get()

        if backup_choice == "none":
            deletion_note = ", including deleting extra files," if delete_on_dest else ""
            if not messagebox.askyesno(
                f"Confirm Sync ({self.source_name} -> {self.dest_name})",
                f"This will modify {self.dest_name} to match {self.source_name}{deletion_note} without a backup.\n\n"
                "Are you absolutely sure?",
                parent=self.parent_toplevel,
            ):
                self.stop_loading()
                return
            self.run_sync_task(delete_on_dest)

        elif backup_choice == "remote":
            self._start_progress()
            self.update_status(f"Starting remote backup of {self.dest_name}...")
            threading.Thread(
                target=sftp_logic.backup_folder_remote_task,
                args=(self.dest_config, self.sync_queue, self.dest_name),
                daemon=True,
            ).start()
            self._poll_sync_queue(delete_on_dest)

        elif backup_choice == "local":
            local_path = filedialog.askdirectory(
                title=f"Select Local Backup Folder for {self.dest_name}",
                parent=self.parent_toplevel,
            )
            if not local_path:
                self.stop_loading()
                return
            self._start_progress()
            self.update_status(f"Starting local backup of {self.dest_name}...")
            threading.Thread(
                target=sftp_logic.backup_folder_local_task,
                args=(self.dest_config, local_path, self.sync_queue, self.dest_name),
                daemon=True,
            ).start()
            self._poll_sync_queue(delete_on_dest)

    def run_sync_task(self, delete_on_dest: bool) -> None:
        """Starts the main folder synchronization background thread."""
        self.update_status(f"Starting synchronization ({self.source_name} -> {self.dest_name})...")
        self._start_progress()
        clear_queue(self.sync_queue)

        threading.Thread(
            target=sftp_logic.sync_folders_task,
            args=(
                self.source_config,
                self.dest_config,
                self.effective_results,
                delete_on_dest,
                self.sync_queue,
                self.source_name,
                self.dest_name,
            ),
            daemon=True,
        ).start()
        self._poll_sync_queue(delete_on_dest)

    def _poll_sync_queue(self, delete_on_dest: bool) -> None:
        poll_queue(
            self,
            self.sync_queue,
            lambda item: self._handle_queue_item(item, delete_on_dest),
            on_ui_error=self._handle_ui_error,
        )

    def _handle_ui_error(self, exc: Exception) -> None:
        self.show_error("GUI Error", f"An error occurred in the UI: {exc}")
        self.stop_loading()

    def _handle_queue_item(self, result: Any, delete_on_dest: bool) -> bool:
        if isinstance(result, Exception):
            self.show_error("Task Error", f"An error occurred:\n{result}")
            self.stop_loading()
            return False

        if isinstance(result, str):
            self.update_status(result)
            return True

        if isinstance(result, dict):
            status = result.get("status")
            if status == "backup_complete" and result.get("success"):
                self.update_status(f"Backup complete. Path: {result.get('path')}")
                self.run_sync_task(delete_on_dest)
                return False
            if status == "sync_complete" and result.get("success"):
                self.update_status("Synchronization finished successfully!")
                self.stop_loading(finished=True)
                return False

        return True

    def stop_loading(self, finished: bool = False) -> None:
        """Stops the progress bar and updates button states."""
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(1.0 if finished else 0.0)

        if finished:
            self.action_button.configure(
                text="Close & Refresh", command=self.close_and_refresh, state="normal"
            )
            self.cancel_button.configure(state="disabled")
        else:
            self.action_button.configure(state="normal")
            self.cancel_button.configure(state="normal")
            self.delete_checkbox.configure(state="normal")

    def close_and_refresh(self) -> None:
        """Refreshes the main window comparison and closes the dialog."""
        if self.refresh_callback:
            self.refresh_callback()
        self.parent_toplevel.destroy()

    def update_status(self, message: str) -> None:
        self.status_var.set(message)

    def show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self.parent_toplevel)
