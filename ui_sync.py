import customtkinter as ctk
from tkinter import messagebox, filedialog
import threading
import queue
import sftp_logic

class SyncWindow(ctk.CTkFrame):
    """
    A Toplevel window for managing the synchronization.
    Supports both TEST -> PROD and PROD -> TEST (reverse).
    """
    def __init__(self, parent_toplevel, s1_config, s2_config, results, refresh_callback, reverse=False):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True)

        self.parent_toplevel = parent_toplevel
        self.refresh_callback = refresh_callback
        self.sync_queue = queue.Queue()
        self.reverse = reverse

        # Determine Source and Destination based on direction
        if not reverse:
            self.source_config = s1_config
            self.dest_config = s2_config
            self.source_name = "TEST"
            self.dest_name = "PROD"
            # Standard mapping
            self.files_to_copy_list = results.get('only_on_1', [])
            self.files_to_overwrite_list = results.get('different', [])
            self.files_to_delete_list = results.get('only_on_2', [])
            self.dirs_to_create_list = results.get('only_on_1_dirs', [])
            self.dirs_to_delete_list = results.get('only_on_2_dirs', [])
        else:
            self.source_config = s2_config
            self.dest_config = s1_config
            self.source_name = "PROD"
            self.dest_name = "TEST"
            # Reverse mapping
            # "only_on_2" are files on PROD (source) missing on TEST (dest) -> Copy
            self.files_to_copy_list = results.get('only_on_2', [])
            self.files_to_overwrite_list = results.get('different', [])
            self.files_to_delete_list = results.get('only_on_1', [])
            self.dirs_to_create_list = results.get('only_on_2_dirs', [])
            self.dirs_to_delete_list = results.get('only_on_1_dirs', [])

        self.effective_results = {
            'only_on_1': self.files_to_copy_list,
            'different': self.files_to_overwrite_list,
            'only_on_1_dirs': self.dirs_to_create_list,
            'only_on_2': self.files_to_delete_list,
            'only_on_2_dirs': self.dirs_to_delete_list
        }

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1 Summary Frame 
        summary_frame = ctk.CTkFrame(self, border_width=1)
        summary_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        summary_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(summary_frame, text=f"Sync Summary ({self.source_name} -> {self.dest_name})", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=5, sticky="w")

        self.to_copy = len(self.files_to_copy_list)
        self.to_overwrite = len(self.files_to_overwrite_list)
        self.to_delete = len(self.files_to_delete_list)

        ctk.CTkLabel(summary_frame, text=f"Files to copy to {self.dest_name}:").grid(row=1, column=0, sticky="w", padx=10, pady=2)
        ctk.CTkLabel(summary_frame, text=str(self.to_copy)).grid(row=1, column=1, sticky="w", padx=10, pady=2)

        ctk.CTkLabel(summary_frame, text=f"Files to overwrite on {self.dest_name}:").grid(row=2, column=0, sticky="w", padx=10, pady=2)
        ctk.CTkLabel(summary_frame, text=str(self.to_overwrite)).grid(row=2, column=1, sticky="w", padx=10, pady=2)

        ctk.CTkLabel(summary_frame, text=f"Files to delete from {self.dest_name}:").grid(row=3, column=0, sticky="w", padx=10, pady=(2, 10))
        ctk.CTkLabel(summary_frame, text=str(self.to_delete), text_color="orange").grid(row=3, column=1, sticky="w", padx=10, pady=(2, 10))

        # 2 Options Frame
        options_frame = ctk.CTkFrame(self, border_width=1)
        options_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=0)
        options_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(options_frame, text=f"Backup Options for {self.dest_name}", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")

        self.backup_var = ctk.StringVar(value="none")

        radio1 = ctk.CTkRadioButton(options_frame, text=f"No Backup. Make {self.dest_name} identical to {self.source_name}.", variable=self.backup_var, value="none")
        radio1.grid(row=1, column=0, sticky="w", padx=10, pady=5)

        radio2 = ctk.CTkRadioButton(options_frame, text=f"Remote Backup: Copy {self.dest_name} folder on the server before sync.", variable=self.backup_var, value="remote")
        radio2.grid(row=2, column=0, sticky="w", padx=10, pady=5)

        radio3 = ctk.CTkRadioButton(options_frame, text=f"Local Backup: Download {self.dest_name} folder to this PC before sync.", variable=self.backup_var, value="local")
        radio3.grid(row=3, column=0, sticky="w", padx=10, pady=(5, 10))

        # 3 Action Frame
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        action_frame.grid_columnconfigure(0, weight=1)

        self.action_button = ctk.CTkButton(action_frame, text="Start Synchronization", command=self.start_sync_process)
        self.action_button.pack(side="left", padx=(0, 10))

        self.cancel_button = ctk.CTkButton(action_frame, text="Cancel", command=self.parent_toplevel.destroy, fg_color="gray")
        self.cancel_button.pack(side="left")

        # 4 Status Bar
        status_frame = ctk.CTkFrame(self, height=30)
        status_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))

        self.status_var = ctk.StringVar(value="Ready to synchronize.")
        self.status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", fill="x", padx=10, expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=150)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)

    def start_sync_process(self):
        """Initiates the backup and/or sync process based on user selection."""
        self.action_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.progress_bar.start()

        backup_choice = self.backup_var.get()
        delete_on_dest = True  # We make Destination a mirror of Source

        if backup_choice == "none":
            if not messagebox.askyesno(f"Confirm Sync ({self.source_name} -> {self.dest_name})", f"This will modify the {self.dest_name} server to match the {self.source_name} server, including deleting files, without a backup.\n\nAre you absolutely sure?", parent=self.parent_toplevel):
                self.stop_loading()
                return
            self.run_sync_task(delete_on_dest)

        elif backup_choice == "remote":
            self.update_status(f"Starting remote backup of {self.dest_name}...")
            # We use self.dest_config for the backup target
            threading.Thread(target=sftp_logic.backup_folder_remote_task, args=(self.dest_config, self.sync_queue, self.dest_name), daemon=True).start()
            self.after(100, lambda: self.check_queue(delete_on_dest))

        elif backup_choice == "local":
            local_path = filedialog.askdirectory(title=f"Select Local Backup Folder for {self.dest_name}", parent=self.parent_toplevel)
            if not local_path:
                self.stop_loading()
                return
            self.update_status(f"Starting local backup of {self.dest_name}...")
            # We use self.dest_config for the backup target
            threading.Thread(target=sftp_logic.backup_folder_local_task, args=(self.dest_config, local_path, self.sync_queue, self.dest_name), daemon=True).start()
            self.after(100, lambda: self.check_queue(delete_on_dest))

    def run_sync_task(self, delete_on_dest):
        """Starts the main synchronization task in a thread."""
        self.update_status(f"Starting synchronization ({self.source_name} -> {self.dest_name})...")
        self.progress_bar.start()
        # sftp_logic.sync_folders_task expects (s1, s2, results...).
        # We pass source_config as s1, dest_config as s2, and effective_results.
        threading.Thread(target=sftp_logic.sync_folders_task, args=(self.source_config, self.dest_config, self.effective_results, delete_on_dest, self.sync_queue), daemon=True).start()
        self.after(100, lambda: self.check_queue(delete_on_dest))

    def check_queue(self, delete_on_dest):
        """Checks the queue for updates from the running tasks."""
        try:
            result = self.sync_queue.get_nowait()

            if isinstance(result, Exception):
                self.show_error("Task Error", f"An error occurred:\n{result}")
                self.stop_loading()
            elif isinstance(result, str):
                self.update_status(result)
                self.after(100, lambda: self.check_queue(delete_on_dest))
            elif isinstance(result, dict):
                status = result.get('status')
                if status == 'backup_complete' and result.get('success'):
                    self.update_status(f"Backup complete. Path: {result.get('path')}")
                    self.run_sync_task(delete_on_dest)  # Start sync after backup
                elif status == 'sync_complete' and result.get('success'):
                    self.update_status("Synchronization finished successfully!")
                    self.stop_loading(finished=True)
                else:
                    self.after(100, lambda: self.check_queue(delete_on_dest))
        except queue.Empty:
            self.after(100, lambda: self.check_queue(delete_on_dest))
        except Exception as e:
            self.show_error("GUI Error", f"An error occurred in the UI: {e}")
            self.stop_loading()

    def stop_loading(self, finished=False):
        """Stops the progress bar and re-enables controls."""
        self.progress_bar.stop()
        self.progress_bar.set(1 if finished else 0)
        self.cancel_button.configure(state="normal")
        if finished:
            self.action_button.configure(text="Close & Refresh", command=self.close_and_refresh, state="normal")
            self.cancel_button.configure(state="disabled")
        else:
            self.action_button.configure(state="normal")

    def close_and_refresh(self):
        """Calls the main app's refresh callback and closes the window."""
        if self.refresh_callback:
            self.refresh_callback()
        self.parent_toplevel.destroy()

    def update_status(self, message):
        self.status_var.set(message)

    def show_error(self, title, message):
        messagebox.showerror(title, message, parent=self.parent_toplevel)
