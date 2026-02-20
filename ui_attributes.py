import customtkinter as ctk
from tkinter import messagebox
import threading
import queue
import sftp_logic

class AttributesWindow(ctk.CTkFrame):
    """
    A Toplevel window for changing file owner and permissions.
    """
    def __init__(self, parent_toplevel, s1_config, s2_config, selected_items_data, status_callback, refresh_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.s1_config, self.s2_config, self.selected_items_data = s1_config, s2_config, selected_items_data
        self.status_callback, self.parent_toplevel = status_callback, parent_toplevel
        self.refresh_callback = refresh_callback

        # Extract data from the first selected item for initial display/defaults
        # The actual changes will be applied to all selected items
        first_item = selected_items_data[0]

        # Retroactive fix for Note column
        if len(first_item) == 6:
            status, relative_path, note, owner_group, symbolic_perms, raw_octal_perms = first_item
        else:
            status, relative_path, owner_group, symbolic_perms, raw_octal_perms = first_item

        octal_perms = str(raw_octal_perms)  # Ensure it's a string
        current_owner, current_group = owner_group.split(':', 1) if ':' in owner_group else (owner_group, "")

        self.owner_var = ctk.StringVar(value=current_owner.split(' -> ')[0])

        self.group_var = ctk.StringVar(value=current_group.split(' -> ')[0])
        self.new_perms = ctk.StringVar(value=octal_perms.split(' -> ')[0])  # Initialize with octal

        self.change_queue, self.user_queue, self.group_queue = queue.Queue(), queue.Queue(), queue.Queue()
        self.tasks_running, self.user_fetch_tasks, self.group_fetch_tasks = 0, 0, 0
        self.all_users, self.all_groups = set(), set()

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text="File(s):", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=5, pady=5)

        if len(self.selected_items_data) > 1:
            file_display_text = f"{len(self.selected_items_data)} files selected"
        else:
            file_display_text = relative_path
        ctk.CTkLabel(self, text=file_display_text, wraplength=450, justify="left").grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ctk.CTkLabel(self, text="Owner:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.owner_menu = ctk.CTkOptionMenu(self, variable=self.owner_var, values=["Loading..."], state="disabled")
        self.owner_menu.grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ctk.CTkLabel(self, text="Group:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.group_menu = ctk.CTkOptionMenu(self, variable=self.group_var, values=["Loading..."], state="disabled")
        self.group_menu.grid(row=2, column=1, sticky="ew", padx=5, pady=5)

        # Display current owner/group/perms for the first item, or indicate mixed if applicable
        if len(self.selected_items_data) == 1:
            ctk.CTkLabel(self, text=f"(Current Owner:Group: {owner_group})", font=ctk.CTkFont(size=10)).grid(row=3, column=1, sticky="w", padx=5)
        else:
            idx = 3 if len(selected_items_data[0]) == 6 else 2
            all_same_owner_group = all(item[idx] == owner_group for item in self.selected_items_data)
            if all_same_owner_group:
                ctk.CTkLabel(self, text=f"(Current Owner:Group: {owner_group})", font=ctk.CTkFont(size=10)).grid(row=3, column=1, sticky="w", padx=5)
            else:
                ctk.CTkLabel(self, text="(Current Owner:Group: Mixed)", font=ctk.CTkFont(size=10)).grid(row=3, column=1, sticky="w", padx=5)

        ctk.CTkLabel(self, text="Perms (octal)").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        ctk.CTkEntry(self, textvariable=self.new_perms).grid(row=4, column=1, sticky="ew", padx=5, pady=5)

        if len(self.selected_items_data) == 1:
            ctk.CTkLabel(self, text=f"(Current: {symbolic_perms} / {octal_perms})", font=ctk.CTkFont(size=10)).grid(row=5, column=1, sticky="w", padx=5)
        else:
            idx = 5 if len(selected_items_data[0]) == 6 else 4
            all_same_perms = all(str(item[idx]) == octal_perms for item in self.selected_items_data)
            if all_same_perms:
                ctk.CTkLabel(self, text=f"(Current: {symbolic_perms} / {octal_perms})", font=ctk.CTkFont(size=10)).grid(row=5, column=1, sticky="w", padx=5)
            else:
                ctk.CTkLabel(self, text="(Current Permissions: Mixed)", font=ctk.CTkFont(size=10)).grid(row=5, column=1, sticky="w", padx=5)

        self.target_test, self.target_prod = ctk.BooleanVar(value=False), ctk.BooleanVar(value=False)

        # Determine initial state of TEST/PROD checkboxes based on all selected items
        has_test_files = any(item[0] in ["DIFFERENT", "IDENTICAL", "ONLY ON TEST"] for item in self.selected_items_data)
        has_prod_files = any(item[0] in ["DIFFERENT", "IDENTICAL", "ONLY ON PROD"] for item in self.selected_items_data)

        self.target_test.set(has_test_files)
        self.target_prod.set(has_prod_files)

        ctk.CTkLabel(self, text="Apply to:").grid(row=6, column=0, sticky="w", padx=5, pady=10)
        server_frame = ctk.CTkFrame(self, fg_color="transparent")
        server_frame.grid(row=6, column=1, sticky="ew", padx=5, pady=10)

        self.check_test = ctk.CTkCheckBox(server_frame, text="TEST Server", variable=self.target_test)
        self.check_test.pack(side="left", padx=(0, 20))
        self.check_prod = ctk.CTkCheckBox(server_frame, text="PROD Server", variable=self.target_prod)
        self.check_prod.pack(side="left")

        # Disable checkboxes if no files are present on that server among the selection
        if not has_test_files:
            self.check_test.configure(state="disabled")
        if not has_prod_files:
            self.check_prod.configure(state="disabled")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=7, column=0, columnspan=2, pady=10)

        self.apply_button = ctk.CTkButton(button_frame, text="Apply Changes", command=self.start_change_task)
        self.apply_button.pack(side="left", padx=10)
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self.parent_toplevel.destroy, fg_color="gray")
        self.cancel_button.pack(side="left", padx=10)

        self.fetch_remote_lists()

    def fetch_remote_lists(self):
        """Starts background tasks to fetch user and group lists from servers."""
        if self.target_test.get():
            self.user_fetch_tasks += 1
            self.group_fetch_tasks += 1
            threading.Thread(target=sftp_logic.get_all_users_task, args=(self.s1_config, self.user_queue, "TEST"), daemon=True).start()
            threading.Thread(target=sftp_logic.get_all_groups_task, args=(self.s1_config, self.group_queue, "TEST"), daemon=True).start()

        if self.target_prod.get():
            self.user_fetch_tasks += 1
            self.group_fetch_tasks += 1
            threading.Thread(target=sftp_logic.get_all_users_task, args=(self.s2_config, self.user_queue, "PROD"), daemon=True).start()
            threading.Thread(target=sftp_logic.get_all_groups_task, args=(self.s2_config, self.group_queue, "PROD"), daemon=True).start()

        if self.user_fetch_tasks > 0:
            self.after(100, self.check_user_queue)
        else:
            self.owner_menu.configure(state="normal", values=[self.owner_var.get()])

        if self.group_fetch_tasks > 0:
            self.after(100, self.check_group_queue)
        else:
            self.group_menu.configure(state="normal", values=[self.group_var.get()])

    def check_user_queue(self):
        """Checks the queue for user lists from the threads."""
        try:
            result = self.user_queue.get_nowait()
            if isinstance(result, Exception):
                self.show_error("Fetch Users Error", f"Failed to get user list:\n{result}")
                self.user_fetch_tasks -= 1
            elif isinstance(result, dict) and 'users' in result:
                self.all_users.update(result['users'])
                self.user_fetch_tasks -= 1

            if self.user_fetch_tasks <= 0:
                if self.all_users:
                    sorted_users = sorted(list(self.all_users))
                    self.owner_menu.configure(state="normal", values=sorted_users)
                    current = self.owner_var.get()
                    self.owner_menu.set(current if current in sorted_users else sorted_users[0])
                else:
                    self.owner_menu.configure(state="normal", values=[self.owner_var.get()])
                return
            self.after(100, self.check_user_queue)
        except queue.Empty:
            self.after(100, self.check_user_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error processing user list: {e}")
            self.owner_menu.configure(state="normal", values=[self.owner_var.get()])

    def check_group_queue(self):
        """Checks the queue for group lists from the threads."""
        try:
            result = self.group_queue.get_nowait()
            if isinstance(result, Exception):
                self.show_error("Fetch Groups Error", f"Failed to get group list:\n{result}")
                self.group_fetch_tasks -= 1
            elif isinstance(result, dict) and 'groups' in result:
                self.all_groups.update(result['groups'])
                self.group_fetch_tasks -= 1

            if self.group_fetch_tasks <= 0:
                if self.all_groups:
                    sorted_groups = sorted(list(self.all_groups))
                    self.group_menu.configure(state="normal", values=sorted_groups)
                    current = self.group_var.get()
                    self.group_menu.set(current if current in sorted_groups else sorted_groups[0])
                else:
                    self.group_menu.configure(state="normal", values=[self.group_var.get()])
                return
            self.after(100, self.check_group_queue)
        except queue.Empty:
            self.after(100, self.check_group_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error processing group list: {e}")
            self.group_menu.configure(state="normal", values=[self.group_var.get()])

    def start_change_task(self):
        """Starts the background task to change attributes for all selected files."""
        self.apply_button.configure(state="disabled")
        self.status_callback("Applying changes...")
        self.tasks_running = 0
        owner, group, perms_str = self.owner_var.get(), self.group_var.get(), self.new_perms.get()

        for item_data in self.selected_items_data:
            status, relative_path, _, _, _ = item_data  # Unpack relevant data

            # Apply to TEST server if selected and file is on TEST
            if self.target_test.get() and status in ["DIFFERENT", "IDENTICAL", "ONLY ON TEST"]:
                self.tasks_running += 1
                threading.Thread(target=sftp_logic.change_attributes_task, args=(self.s1_config, relative_path, owner, group, perms_str, self.change_queue, f"TEST:{relative_path}"), daemon=True).start()

            # Apply to PROD server if selected and file is on PROD
            if self.target_prod.get() and status in ["DIFFERENT", "IDENTICAL", "ONLY ON PROD"]:
                self.tasks_running += 1
                threading.Thread(target=sftp_logic.change_attributes_task, args=(self.s2_config, relative_path, owner, group, perms_str, self.change_queue, f"PROD:{relative_path}"), daemon=True).start()

        if self.tasks_running > 0:
            self.after(100, self.check_change_queue)
        else:
            self.status_callback("No servers selected or no relevant files on selected servers.")
            self.stop_loading()

    def check_change_queue(self):
        """Checks the queue for updates from the attribute change thread."""
        try:
            result = self.change_queue.get_nowait()

            if isinstance(result, str):
                if result.endswith(":Success"):
                    self.tasks_running -= 1
                    self.status_callback(f"Applied: {result.replace(':Success', '')}")
                elif result.startswith("Error:"):
                    self.tasks_running -= 1
                    # Extract server_name and error message
                    parts = result.split(':', 2)  # Split at most twice
                    server_info = parts[1] if len(parts) > 1 else "Unknown"
                    error_msg = parts[2] if len(parts) > 2 else result
                    self.show_error(f"Error on {server_info}", error_msg)
                else:
                    # General status update from sftp_logic (e.g., "Connecting...")
                    self.status_callback(result)
            elif isinstance(result, Exception):
                self.tasks_running -= 1
                self.show_error("Error", f"An unexpected error occurred:\n{result}")

            if self.tasks_running <= 0:
                self.status_callback("All changes processed. Refreshing...")
                self.stop_loading()
                if self.refresh_callback:
                    self.refresh_callback()
                self.parent_toplevel.after(1000, self.parent_toplevel.destroy)  # Close after 1s
            else:
                self.after(100, self.check_change_queue)

        except queue.Empty:
            self.after(100, self.check_change_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error updating UI: {e}")
            self.stop_loading()

    def stop_loading(self):
        self.apply_button.configure(state="normal")

    def show_error(self, title, message):
        messagebox.showerror(title, message, parent=self.parent_toplevel)
