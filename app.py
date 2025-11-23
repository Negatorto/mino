import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
import threading
import queue
import json
import os
import webbrowser
import difflib

import sftp_logic

class App(ctk.CTk):
    """
    Main Application Window Class
    """
    def __init__(self):
        super().__init__()

        self.title("MINO: Mirroring Integrity Network Operations")
        self.geometry("900x700")

        # --- Data ---
        self.result_queue = queue.Queue()
        self.comparison_results = None
        self.server1_vars = {}
        self.server2_vars = {}

        # Clone Options (Moved to Menu)
        self.clone_options = {
            "host": ctk.BooleanVar(value=True),
            "user": ctk.BooleanVar(value=True),
            "pass": ctk.BooleanVar(value=True),
            "path": ctk.BooleanVar(value=True),
            "port": ctk.BooleanVar(value=True)
        }

        # --- Menu Bar (Custom) ---
        self.create_custom_menubar()

        # --- Main Layout ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1) # Results row (shifted to 3)

        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5) # Shifted to 1
        input_frame.grid_columnconfigure((0, 1), weight=1)

        self.server1_vars = self.create_server_widgets(input_frame, "TEST Server", 0)
        self.server2_vars = self.create_server_widgets(input_frame, "PRODUCTION Server", 1)

        # Trace "clone" variables
        self.server1_vars["host"].trace_add("write", lambda *args: self.on_clone_input("host"))
        self.server1_vars["user"].trace_add("write", lambda *args: self.on_clone_input("user"))
        self.server1_vars["pass"].trace_add("write", lambda *args: self.on_clone_input("pass"))
        self.server1_vars["path"].trace_add("write", lambda *args: self.on_clone_input("path"))
        self.server1_vars["port"].trace_add("write", lambda *args: self.on_clone_input("port"))

        control_frame = ctk.CTkFrame(self)
        control_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5) # Shifted to 2
        control_frame.grid_columnconfigure(0, weight=1)

        # Button sub-frame
        button_subframe = ctk.CTkFrame(control_frame, fg_color="transparent")
        button_subframe.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.compare_button = ctk.CTkButton(button_subframe, text="Compare Folders", command=self.start_comparison)
        self.compare_button.pack(side="left", padx=(0, 10))

        self.sync_button = ctk.CTkButton(button_subframe, text="Sync TEST -> PROD", command=self.open_sync_window, state="disabled")
        self.sync_button.pack(side="left", padx=(0, 10))

        self.diff_button = ctk.CTkButton(button_subframe, text="Compare Selected File", command=self.open_diff_window, state="disabled")
        self.diff_button.pack(side="left")

        # Clone Checkbox (Moved to Menu)
        self.clone_var = ctk.BooleanVar(value=False)
        
        # Theme switcher (Moved to Menu)


        result_frame = ctk.CTkFrame(self)
        result_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10)) # Shifted to 3
        result_frame.grid_rowconfigure(0, weight=1)
        result_frame.grid_columnconfigure(0, weight=1)

        self.create_treeview(result_frame)

        status_frame = ctk.CTkFrame(self, height=30)
        status_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10)) # Shifted to 4
        
        self.status_var = ctk.StringVar(value="Ready (Using SFTP).")
        self.status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", fill="x", padx=10, expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=150)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)
        self.progress_bar.configure(mode="indeterminate")

    def create_custom_menubar(self):
        """Creates a custom menu bar using CTkFrame and Buttons."""
        menubar_frame = ctk.CTkFrame(self, height=30, corner_radius=0, fg_color=("gray85", "gray17"))
        menubar_frame.grid(row=0, column=0, sticky="ew")
        
        # File Menu
        self.file_btn = ctk.CTkButton(menubar_frame, text="File", width=60, fg_color="transparent", text_color=("black", "white"), hover_color=("gray70", "gray25"), command=self.show_file_menu)
        self.file_btn.pack(side="left", padx=2)
        
        # Settings Menu
        self.settings_btn = ctk.CTkButton(menubar_frame, text="Settings", width=80, fg_color="transparent", text_color=("black", "white"), hover_color=("gray70", "gray25"), command=self.show_settings_menu)
        self.settings_btn.pack(side="left", padx=2)
        
        # About Menu
        self.about_btn = ctk.CTkButton(menubar_frame, text="About", width=60, fg_color="transparent", text_color=("black", "white"), hover_color=("gray70", "gray25"), command=lambda: webbrowser.open("https://github.com/Negatorto/mino"))
        self.about_btn.pack(side="left", padx=2)

    def show_file_menu(self):
        from tkinter import Menu
        menu = Menu(self, tearoff=0)
        menu.add_command(label="Save Workspace (Safe)", command=self.save_workspace_safe)
        menu.add_command(label="Save Sensitive Workspace (With Passwords)", command=self.save_workspace_sensitive)
        menu.add_command(label="Load Workspace", command=self.load_workspace)
        menu.add_separator()
        menu.add_command(label="Exit", command=self.quit)
        
        # Position menu below the button
        x = self.file_btn.winfo_rootx()
        y = self.file_btn.winfo_rooty() + self.file_btn.winfo_height()
        menu.tk_popup(x, y)

    def show_settings_menu(self):
        from tkinter import Menu
        menu = Menu(self, tearoff=0)
        menu.add_command(label="Clone Options...", command=self.open_clone_settings)
        
        # Appearance Mode (Light/Dark)
        theme_menu = Menu(menu, tearoff=0)
        menu.add_cascade(label="Themes", menu=theme_menu)
        theme_menu.add_command(label="Light", command=lambda: self.change_appearance_mode("Light"))
        theme_menu.add_command(label="Dark", command=lambda: self.change_appearance_mode("Dark"))
        theme_menu.add_command(label="System", command=lambda: self.change_appearance_mode("System"))
        
        x = self.settings_btn.winfo_rootx()
        y = self.settings_btn.winfo_rooty() + self.settings_btn.winfo_height()
        menu.tk_popup(x, y)

    def save_workspace_safe(self):
        """Saves configuration excluding passwords."""
        self._save_workspace(include_passwords=False)

    def save_workspace_sensitive(self):
        """Saves configuration including passwords."""
        self._save_workspace(include_passwords=True)

    def _save_workspace(self, include_passwords=False):
        """Internal method to save workspace."""
        title = "Save Sensitive Workspace" if include_passwords else "Save Workspace"
        file_path = filedialog.asksaveasfilename(
            initialdir="workspaces",
            title=title,
            defaultextension=".json",
            filetypes=(("JSON Files", "*.json"), ("All Files", "*.*"))
        )
        if not file_path:
            return

        s1_data = {k: v.get() for k, v in self.server1_vars.items()}
        s2_data = {k: v.get() for k, v in self.server2_vars.items()}

        if not include_passwords:
            s1_data["pass"] = ""
            s2_data["pass"] = ""

        data = {
            "server1": s1_data,
            "server2": s2_data,
            "clone_options": {k: v.get() for k, v in self.clone_options.items()}
        }

        try:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=4)
            self.update_status(f"Workspace saved to {os.path.basename(file_path)}")
        except Exception as e:
            self.show_error("Save Error", f"Could not save workspace:\n{e}")

    def load_workspace(self):
        """Loads server configurations from a JSON file."""
        file_path = filedialog.askopenfilename(
            initialdir="workspaces",
            title="Load Workspace",
            filetypes=(("JSON Files", "*.json"), ("All Files", "*.*"))
        )
        if not file_path:
            return

        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            s1_data = data.get("server1", {})
            s2_data = data.get("server2", {})
            
            for k, v in s1_data.items():
                if k in self.server1_vars:
                    self.server1_vars[k].set(v)
            
            for k, v in s2_data.items():
                if k in self.server2_vars:
                    self.server2_vars[k].set(v)

            if "clone_options" in data:
                for k, v in data["clone_options"].items():
                    if k in self.clone_options:
                        self.clone_options[k].set(v)
            elif "clone_enabled" in data: # Backwards compatibility
                 val = data["clone_enabled"]
                 for v in self.clone_options.values(): v.set(val)

            self.update_status(f"Workspace loaded from {os.path.basename(file_path)}")
        except Exception as e:
            self.show_error("Load Error", f"Could not load workspace:\n{e}")
    def create_server_widgets(self, parent, title, col):
        """Creates the input widgets for a server."""
        frame = ctk.CTkFrame(parent, border_width=1)
        frame.grid(row=0, column=col, sticky="nsew", padx=5, pady=5)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 10))

        vars = {
            "host": ctk.StringVar(),
            "port": ctk.StringVar(value="22"),
            "user": ctk.StringVar(),
            "pass": ctk.StringVar(),
            "path": ctk.StringVar()
        }

        ctk.CTkLabel(frame, text="Host:").grid(row=1, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=vars["host"]).grid(row=1, column=1, sticky="ew", padx=10, pady=3)
        
        ctk.CTkLabel(frame, text="Port:").grid(row=2, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=vars["port"], width=60).grid(row=2, column=1, sticky="w", padx=10, pady=3)

        ctk.CTkLabel(frame, text="Username:").grid(row=3, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=vars["user"]).grid(row=3, column=1, sticky="ew", padx=10, pady=3)
        
        ctk.CTkLabel(frame, text="Password:").grid(row=4, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=vars["pass"], show="*").grid(row=4, column=1, sticky="ew", padx=10, pady=3)
        
        ctk.CTkLabel(frame, text="Remote Path:").grid(row=5, column=0, sticky="w", padx=10, pady=(3, 10))
        ctk.CTkEntry(frame, textvariable=vars["path"]).grid(row=5, column=1, sticky="ew", padx=10, pady=(3, 10))
        
        return vars

    def create_treeview(self, parent):
        """Creates and styles the results Treeview."""
        self.style = ttk.Style(self)
        self.tree = ttk.Treeview(parent, columns=("Status", "Path", "Owner", "Perms"), show="headings")
        
        self.tree.heading("Status", text="Status")
        self.tree.heading("Path", text="File Path")
        self.tree.heading("Owner", text="Owner:Group")
        self.tree.heading("Perms", text="Permissions")
        
        self.tree.column("Status", width=150, anchor='w')
        self.tree.column("Path", width=450, anchor='w')
        self.tree.column("Owner", width=120, anchor='w')
        self.tree.column("Perms", width=120, anchor='w')

        scrollbar = ctk.CTkScrollbar(parent, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.grid(row=0, column=0, sticky="nsew")

        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<Button-3>', self.show_tree_context_menu)
        
        self.update_treeview_style(ctk.get_appearance_mode())
        self.update_treeview_tag_colors(ctk.get_appearance_mode())

    def show_tree_context_menu(self, event):
        """Shows a context menu on right-click."""
        try:
            # Get all selected items
            selected_items = self.tree.selection()
            
            # If right-click was on an unselected item, select it
            item_id_under_mouse = self.tree.identify_row(event.y)
            if item_id_under_mouse and item_id_under_mouse not in selected_items:
                self.tree.selection_set(item_id_under_mouse)
                selected_items = self.tree.selection() # Update selected_items

            if not selected_items:
                return

            from tkinter import Menu
            context_menu = Menu(self.tree, tearoff=0)

            # Determine if "Compare Selected File" should be enabled
            can_diff = False
            if len(selected_items) == 1:
                item_data = self.tree.item(selected_items[0])
                status = item_data['values'][0] if item_data['values'] else ""
                if status in ["DIFFERENT", "IDENTICAL"]:
                    can_diff = True
            
            if can_diff:
                context_menu.add_command(label="Compare Selected File", command=self.open_diff_window)
            else:
                context_menu.add_command(label="Compare Selected File", state="disabled")

            # "Sync File" option
            if len(selected_items) == 1:
                item_data = self.tree.item(selected_items[0])
                status = item_data['values'][0] if item_data['values'] else ""
                # Allow sync if file is different or only on test
                if status in ["DIFFERENT", "ONLY ON TEST"]:
                     context_menu.add_command(label="Sync File (TEST -> PROD)", command=lambda: self.sync_single_file(selected_items[0]))
                else:
                     context_menu.add_command(label="Sync File (TEST -> PROD)", state="disabled")

            # "Change Attributes..." is always available for selected items
            context_menu.add_command(label="Change Attributes...", command=lambda: self.open_attributes_window(selected_items))
            context_menu.tk_popup(event.x_root, event.y_root)

        except Exception as e:
            print(f"Error showing context menu: {e}")

    def sync_single_file(self, item_id):
        """Starts the single file sync process."""
        item_data = self.tree.item(item_id)
        values = item_data['values']
        relative_path = values[1]
        
        if not messagebox.askyesno("Confirm Sync", f"Are you sure you want to overwrite/copy '{relative_path}' to PRODUCTION?", parent=self):
            return

        self.update_status(f"Syncing {relative_path}...")
        self.progress_bar.start()
        
        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}
        
        threading.Thread(target=sftp_logic.sync_single_file_task, args=(s1_config, s2_config, relative_path, self.result_queue), daemon=True).start()
        self.after(100, self.check_single_sync_queue)

    def check_single_sync_queue(self):
        """Checks the queue for single file sync results."""
        try:
            result = self.result_queue.get_nowait()
            
            if isinstance(result, Exception):
                self.show_error("Sync Error", f"An error occurred:\n{result}")
                self.stop_loading()
            elif isinstance(result, str):
                 self.update_status(result)
                 self.after(100, self.check_single_sync_queue)
            elif isinstance(result, dict) and result.get('status') == 'single_sync_complete':
                self.update_status(f"Synced {result.get('file')} successfully.")
                self.stop_loading()
                # Ideally, we should update the tree item status here to "IDENTICAL"
                # For now, let's just show a message, or maybe trigger a refresh if user wants
                if messagebox.askyesno("Sync Complete", "File synced. Refresh comparison?", parent=self):
                    self.start_comparison()
        except queue.Empty:
            self.after(100, self.check_single_sync_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error updating UI: {e}")
            self.stop_loading()

    def update_treeview_style(self, mode):
        """Updates the ttk.Treeview style to match the CTk theme."""
        if mode == "Dark":
            bg, fg, heading_bg = "#2B2B2B", "#E0E0E0", "#343638"
        else: # "Light"
            bg, fg, heading_bg = "#FFFFFF", "#101010", "#EAEAEA"

        self.style.theme_use("clam")
        self.style.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg)
        self.style.configure("Treeview.Heading", background=heading_bg, foreground=fg, font=('Helvetica', 10, 'bold'))
        self.style.map('Treeview.Heading', background=[('active', heading_bg)])

    def update_treeview_tag_colors(self, mode):
        """Updates the Treeview tag colors for light/dark mode."""
        if mode == "Dark":
            self.tree.tag_configure('different', background='#5D0000', foreground="#FFD0D0")
            self.tree.tag_configure('solo_test', background='#003D5D', foreground="#C0E8FF")
            self.tree.tag_configure('solo_prod', background='#004D00', foreground="#D0FFD0")
            self.tree.tag_configure('identical', foreground='#707070')
        else: 
            self.tree.tag_configure('different', background='#FFC0C0', foreground="#8B0000")
            self.tree.tag_configure('solo_test', background='#C0E8FF', foreground="#003A59")
            self.tree.tag_configure('solo_prod', background='#C0FFC0', foreground="#004D00")
            self.tree.tag_configure('identical', foreground='#888888')

    def change_appearance_mode(self, new_mode_str):
        """Callback for the theme switcher."""
        ctk.set_appearance_mode(new_mode_str)
        self.update_treeview_style(new_mode_str)
        self.update_treeview_tag_colors(new_mode_str)

    def on_clone_input(self, field_key):
        """Clones input from TEST to PROD if the specific option is active."""
        if self.clone_options.get(field_key) and self.clone_options[field_key].get():
            self.server2_vars[field_key].set(self.server1_vars[field_key].get())

    def on_tree_select(self, event):
        """Enables Diff button only if a 'DIFFERENT' item is selected."""
        selected_items = self.tree.selection()
        if not selected_items:
            self.diff_button.configure(state="disabled")
            return
        
        item = self.tree.item(selected_items[0])
        status = item['values'][0] if item['values'] else ""
        
        if status in ["DIFFERENT", "IDENTICAL"]:
            self.diff_button.configure(state="normal")
        else:
            self.diff_button.configure(state="disabled")

    def open_diff_window(self):
        """Opens the DiffWindow for the selected file."""
        try:
            selected_item = self.tree.selection()[0]
            item_values = self.tree.item(selected_item)['values']
        except IndexError:
            self.show_error("Error", "No valid file selected.")
            return

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        diff_top_level = ctk.CTkToplevel(self)
        diff_top_level.title(f"Diff: {item_values[1]}")
        diff_top_level.geometry("1100x700")
        DiffWindow(diff_top_level, s1_config, s2_config, item_values)

    def open_attributes_window(self, selected_item_ids):
        """Opens the AttributesWindow for the selected file(s)."""
        if not selected_item_ids:
            self.show_error("Error", "No file(s) selected.")
            return

        # Extract item_values for all selected items
        selected_items_data = [self.tree.item(item_id)['values'] for item_id in selected_item_ids]

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        attr_top_level = ctk.CTkToplevel(self)
        attr_top_level.title("Change Attributes")
        attr_top_level.geometry("500x400")
        attr_top_level.transient(self)
        AttributesWindow(attr_top_level, s1_config, s2_config, selected_items_data, self.update_status, self.start_comparison)
        
    def open_sync_window(self):
        """Opens the SyncWindow for the current comparison."""
        if not self.comparison_results:
            self.show_error("Sync Error", "Please run a comparison first.")
            return

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        sync_top_level = ctk.CTkToplevel(self)
        sync_top_level.title("Synchronize TEST to PRODUCTION")
        sync_top_level.geometry("600x550")
        sync_top_level.transient(self)
        SyncWindow(sync_top_level, s1_config, s2_config, self.comparison_results, self.start_comparison)

    def open_clone_settings(self):
        """Opens the Clone Settings popup window."""
        clone_top_level = ctk.CTkToplevel(self)
        clone_top_level.title("Clone Options")
        clone_top_level.geometry("300x250")
        clone_top_level.transient(self)
        CloneSettingsWindow(clone_top_level, self.clone_options, self.on_clone_input)

    def update_status(self, message):
        """Thread-safe method to update the status label."""
        self.status_var.set(message)

    def start_comparison(self):
        """Starts the folder comparison in a background thread."""
        self.compare_button.configure(state="disabled")
        self.diff_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")
        self.progress_bar.start()
        self.update_status("Starting comparison...")
        
        self.comparison_results = None
        for i in self.tree.get_children():
            self.tree.delete(i)

        s1 = {k: v.get() for k, v in self.server1_vars.items()}
        s2 = {k: v.get() for k, v in self.server2_vars.items()}

        threading.Thread(target=sftp_logic.compare_folders_task, args=(s1, s2, self.result_queue), daemon=True).start()
        self.after(100, self.check_queue)

    def check_queue(self):
        """Checks the result queue for updates from the thread."""
        try:
            result = self.result_queue.get_nowait()
            
            if isinstance(result, Exception):
                self.show_error("Error", f"An error occurred:\n{result}")
                self.stop_loading()
            elif isinstance(result, str):
                 self.update_status(result)
                 self.after(100, self.check_queue)
            elif isinstance(result, dict):
                self.populate_results(result)
                self.stop_loading()
                self.update_status("Comparison complete.")
        except queue.Empty:
            self.after(100, self.check_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error updating UI: {e}")
            self.stop_loading()

    def stop_loading(self):
        """Stops progress bar and re-enables button."""
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.compare_button.configure(state="normal")

    def populate_results(self, results):
        """Fills the Treeview with comparison results."""
        self.update_status("Populating results...")
        self.comparison_results = results
        
        files_s1_meta = results.get('files_s1', {})
        files_s2_meta = results.get('files_s2', {})
        
        for f in sorted(results['different']):
            meta1, meta2 = files_s1_meta.get(f, {}), files_s2_meta.get(f, {})
            owner1_str = f"{meta1.get('owner', '?')}:{meta1.get('group', '?')}"
            perms1_sym = meta1.get('mode', '?')
            perms1_oct = meta1.get('octal_mode', '?')
            owner2_str = f"{meta2.get('owner', '?')}:{meta2.get('group', '?')}"
            perms2_sym = meta2.get('mode', '?')
            perms2_oct = meta2.get('octal_mode', '?')

            owner_str = owner1_str if owner1_str == owner2_str else f"{owner1_str} -> {owner2_str}"
            perms_sym_str = perms1_sym if perms1_sym == perms2_sym else f"{perms1_sym} -> {perms2_sym}"
            perms_oct_str = perms1_oct if perms1_oct == perms2_oct else f"{perms1_oct} -> {perms2_oct}"
            
            self.tree.insert("", "end", values=("DIFFERENT", f, owner_str, perms_sym_str, perms_oct_str), tags=('different',))
        
        for f in sorted(results['only_on_1']):
            meta = files_s1_meta.get(f, {})
            owner = f"{meta.get('owner', '?')}:{meta.get('group', '?')}"
            perms_sym = meta.get('mode', '?')
            perms_oct = meta.get('octal_mode', '?')
            self.tree.insert("", "end", values=("ONLY ON TEST", f, owner, perms_sym, perms_oct), tags=('solo_test',))

        for f in sorted(results['only_on_2']):
            meta = files_s2_meta.get(f, {})
            owner = f"{meta.get('owner', '?')}:{meta.get('group', '?')}"
            perms_sym = meta.get('mode', '?')
            perms_oct = meta.get('octal_mode', '?')
            self.tree.insert("", "end", values=("ONLY ON PROD", f, owner, perms_sym, perms_oct), tags=('solo_prod',))
        
        for f in sorted(results['identical']):
            meta1 = files_s1_meta.get(f, {})
            owner1_str = f"{meta1.get('owner', '?')}:{meta1.get('group', '?')}"
            perms1_sym = meta1.get('mode', '?')
            perms1_oct = meta1.get('octal_mode', '?')
            
            meta2 = files_s2_meta.get(f, {})
            owner2_str = f"{meta2.get('owner', '?')}:{meta2.get('group', '?')}"
            perms2_sym = meta2.get('mode', '?')
            perms2_oct = meta2.get('octal_mode', '?')
            
            owner_str = owner1_str if owner1_str == owner2_str else f"{owner1_str} -> {owner2_str}"
            perms_sym_str = perms1_sym if perms1_sym == perms2_sym else f"{perms1_sym} -> {perms2_sym}"
            perms_oct_str = perms1_oct if perms1_oct == perms2_oct else f"{perms1_oct} -> {perms2_oct}"
            
            self.tree.insert("", "end", values=("IDENTICAL", f, owner_str, perms_sym_str, perms_oct_str), tags=('identical',))

        self.diff_button.configure(state="disabled")
        
        has_changes = results['different'] or results['only_on_1'] or results['only_on_2']
        if has_changes:
            self.sync_button.configure(state="normal")
        
        self.update_status(f"Done. Found {len(results['different'])} different files, "
                           f"{len(results['only_on_1'])} only on TEST, {len(results['only_on_2'])} only on PROD.")

    def show_error(self, title, message):
        """Shows a cross-platform error message."""
        messagebox.showerror(title, message, parent=self)


class DiffWindow(ctk.CTkFrame):
    """
    A new Toplevel window for side-by-side file comparison.
    """
    def __init__(self, parent_toplevel, s1_config, s2_config, item_values):
        super().__init__(parent_toplevel, fg_color="transparent")
        self.pack(fill="both", expand=True)

        self.s1_config, self.s2_config = s1_config, s2_config
        self.status, self.relative_path, self.owner_group, self.symbolic_perms, self.octal_perms = item_values
        
        self.diff_queue, self.file_contents, self.diff_result = queue.Queue(), {"TEST": None, "PROD": None}, []

        # Parse metadata for each server
        owner1, owner2 = (self.owner_group, self.owner_group) if " -> " not in self.owner_group else self.owner_group.split(" -> ")
        perms1, perms2 = (self.symbolic_perms, self.symbolic_perms) if " -> " not in self.symbolic_perms else self.symbolic_perms.split(" -> ")

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        control_frame = ctk.CTkFrame(self, fg_color="transparent")
        control_frame.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=5)
        
        self.highlight_inline_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(control_frame, text="Highlight inline differences", variable=self.highlight_inline_var, command=self.populate_diff).pack(side="left")

        self.line_number_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(control_frame, text="Show line numbers", variable=self.line_number_var, command=self.populate_diff).pack(side="left", padx=10)

        self.show_metadata_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(control_frame, text="Show Metadata", variable=self.show_metadata_var, command=self.toggle_metadata_visibility).pack(side="left", padx=10)

        self.edit_test_btn = ctk.CTkButton(control_frame, text="Edit TEST", width=80, command=lambda: self.open_editor("TEST"))
        self.edit_test_btn.pack(side="left", padx=10)
        
        self.edit_prod_btn = ctk.CTkButton(control_frame, text="Edit PROD", width=80, command=lambda: self.open_editor("PROD"))
        self.edit_prod_btn.pack(side="left", padx=10)

        self.status_var = ctk.StringVar(value=f"Loading file: {self.relative_path}...")
        ctk.CTkLabel(control_frame, textvariable=self.status_var, anchor="e").pack(side="right", fill="x", expand=True)

        self.text1_frame, self.text1, self.lines1, self.meta_label1 = self.create_text_pane(self, f"TEST Server ({self.s1_config['host']})", owner1, perms1)
        self.text1_frame.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=10)

        self.text2_frame, self.text2, self.lines2, self.meta_label2 = self.create_text_pane(self, f"PRODUCTION Server ({self.s2_config['host']})", owner2, perms2)
        self.text2_frame.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=10)
        
        self.main_scrollbar = ctk.CTkScrollbar(self, command=self.on_main_scroll)
        self.main_scrollbar.grid(row=2, column=2, sticky="ns", pady=10)
        
        self.text1.configure(yscrollcommand=self.on_text_scroll_1)
        self.text2.configure(yscrollcommand=self.on_text_scroll_2)

        self.lines1 = self.configure_line_number_tags(self.lines1)
        self.lines2 = self.configure_line_number_tags(self.lines2)

        self.bind("<Configure>", self.update_tags_for_theme, add="+")
        self.update_tags_for_theme()

        self.toggle_metadata_visibility() # Set initial state

        threading.Thread(target=sftp_logic.download_file_task, args=(self.s1_config, self.relative_path, self.diff_queue, "TEST"), daemon=True).start()
        threading.Thread(target=sftp_logic.download_file_task, args=(self.s2_config, self.relative_path, self.diff_queue, "PROD"), daemon=True).start()
        self.after(100, self.check_diff_queue)

    def open_editor(self, server_name):
        content = self.file_contents.get(server_name)
        if content is None:
            messagebox.showerror("Error", f"File content for {server_name} is not loaded yet.", parent=self)
            return
            
        config = self.s1_config if server_name == "TEST" else self.s2_config
        
        editor_top = ctk.CTkToplevel(self)
        editor_top.title(f"Edit {server_name}: {self.relative_path}")
        editor_top.geometry("800x600")
        editor_top.transient(self)
        
        EditorWindow(editor_top, config, self.relative_path, content, server_name, self.refresh_diff)

    def refresh_diff(self):
        """Reloads the files and refreshes the diff."""
        self.status_var.set("Reloading files...")
        self.file_contents = {"TEST": None, "PROD": None}
        self.diff_result = []
        
        # Clear text areas
        self.text1.configure(state="normal"); self.text1.delete("1.0", "end"); self.text1.configure(state="disabled")
        self.text2.configure(state="normal"); self.text2.delete("1.0", "end"); self.text2.configure(state="disabled")
        
        threading.Thread(target=sftp_logic.download_file_task, args=(self.s1_config, self.relative_path, self.diff_queue, "TEST"), daemon=True).start()
        threading.Thread(target=sftp_logic.download_file_task, args=(self.s2_config, self.relative_path, self.diff_queue, "PROD"), daemon=True).start()
        self.after(100, self.check_diff_queue)

    def create_text_pane(self, parent, title, owner_info, perms_info):
        frame = ctk.CTkFrame(parent, border_width=1)
        frame.grid_rowconfigure(2, weight=1) # Textbox row
        frame.grid_columnconfigure(1, weight=1) 
        
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5,0))
        
        meta_text = f"Owner: {owner_info}  |  Perms: {perms_info}"
        meta_label = ctk.CTkLabel(frame, text=meta_text, font=ctk.CTkFont(size=11), text_color="gray")
        meta_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0,5))

        line_numbers = ctk.CTkTextbox(frame, width=45, wrap="none", font=("monospace", 10), state="disabled", border_width=0, corner_radius=0)
        line_numbers.grid(row=2, column=0, sticky="ns")
        line_numbers.grid_forget() # Hidden by default
        
        text_widget = ctk.CTkTextbox(frame, wrap="none", font=("monospace", 10), state="disabled", border_width=0, corner_radius=0)
        text_widget.grid(row=2, column=1, sticky="nsew")

        return frame, text_widget, line_numbers, meta_label

    def toggle_metadata_visibility(self):
        """Shows or hides the metadata labels based on the checkbox."""
        if self.show_metadata_var.get():
            self.meta_label1.grid()
            self.meta_label2.grid()
        else:
            self.meta_label1.grid_remove()
            self.meta_label2.grid_remove()

    def configure_line_number_tags(self, line_widget):
        mode = ctk.get_appearance_mode()
        if mode == "Dark":
            line_widget.configure(fg_color="#202020")
            line_widget.tag_config('line', foreground="#909090")
            line_widget.tag_config('blank', foreground="#202020")
        else: # Light
            line_widget.configure(fg_color="#EAEAEA")
            line_widget.tag_config('line', foreground="#707070")
            line_widget.tag_config('blank', foreground="#EAEAEA")
        return line_widget

    def update_tags_for_theme(self, event=None):
        mode = ctk.get_appearance_mode()
        if mode == "Dark":
            self.text1.tag_config('removed', background='#5D0000', foreground="#FFD0D0")
            self.text1.tag_config('blank', background='#252525')
            self.text1.tag_config('highlight_inline', background='#7A7A00', foreground="#FFFFE0")
            self.text2.tag_config('added', background='#004D00', foreground="#D0FFD0")
            self.text2.tag_config('blank', background='#252525')
            self.text2.tag_config('highlight_inline', background='#7A7A00', foreground="#FFFFE0")
        else: # Light
            self.text1.tag_config('removed', background='#FFC0C0', foreground="#8B0000")
            self.text1.tag_config('blank', background='#FAFAFA')
            self.text1.tag_config('highlight_inline', background='#FFFF99', foreground="#4D4D00")
            self.text2.tag_config('added', background='#C0FFC0', foreground="#004D00")
            self.text2.tag_config('blank', background='#FAFAFA')
            self.text2.tag_config('highlight_inline', background='#FFFF99', foreground="#4D4D00")
        self.lines1 = self.configure_line_number_tags(self.lines1)
        self.lines2 = self.configure_line_number_tags(self.lines2)

    def on_main_scroll(self, *args):
        self.text1.yview(*args); self.text2.yview(*args); self.lines1.yview(*args); self.lines2.yview(*args)

    def on_text_scroll_1(self, first, last):
        self.main_scrollbar.set(first, last); self.text2.yview_moveto(first); self.lines1.yview_moveto(first); self.lines2.yview_moveto(first)

    def on_text_scroll_2(self, first, last):
        self.main_scrollbar.set(first, last); self.text1.yview_moveto(first); self.lines1.yview_moveto(first); self.lines2.yview_moveto(first)

    def check_diff_queue(self):
        try:
            result = self.diff_queue.get_nowait()
            if isinstance(result, Exception):
                messagebox.showerror("Download Error", f"Failed to download file:\n{result}", parent=self)
                self.status_var.set("Download error.")
            elif isinstance(result, str):
                 self.status_var.set(result)
            elif isinstance(result, dict):
                self.file_contents[result['server']] = result['content']
                self.status_var.set(f"Downloaded {result['server']} file.")

            if self.file_contents["TEST"] is not None and self.file_contents["PROD"] is not None:
                self.status_var.set("Files downloaded. Calculating diff...")
                s1_lines, s2_lines = self.file_contents["TEST"].splitlines(), self.file_contents["PROD"].splitlines()
                self.diff_result = list(difflib.ndiff(s1_lines, s2_lines))
                self.populate_diff()
                self.status_var.set("Diff complete.")
                return
            self.after(100, self.check_diff_queue)
        except queue.Empty:
            self.after(100, self.check_diff_queue)
        except Exception as e:
            messagebox.showerror("GUI Error", f"Error in diff queue: {e}", parent=self)
            self.status_var.set("GUI Error.")

    def populate_diff(self):
        if not self.diff_result: return
        highlight_on, lines_on = self.highlight_inline_var.get(), self.line_number_var.get()

        if lines_on: 
            self.lines1.grid(row=2, column=0, sticky="ns")
            self.lines2.grid(row=2, column=0, sticky="ns")
        else: 
            self.lines1.grid_forget()
            self.lines2.grid_forget()

        for widget in [self.text1, self.text2, self.lines1, self.lines2]:
            widget.configure(state="normal")
            widget.delete('1.0', "end")

        i, line_num_1, line_num_2 = 0, 1, 1
        while i < len(self.diff_result):
            line = self.diff_result[i]
            if line.startswith(' '):
                self.text1.insert("end", line[2:] + '\n'); self.text2.insert("end", line[2:] + '\n')
                self.lines1.insert("end", f"{line_num_1}\n", 'line'); self.lines2.insert("end", f"{line_num_2}\n", 'line')
                line_num_1 += 1; line_num_2 += 1; i += 1
            elif (highlight_on and line.startswith('- ') and (i + 1 < len(self.diff_result)) and self.diff_result[i+1].startswith('+ ')):
                line1_text, line2_text = line[2:], self.diff_result[i+1][2:]
                self.text1.insert("end", line1_text + '\n', 'removed'); self.text2.insert("end", line2_text + '\n', 'added')
                self.lines1.insert("end", f"{line_num_1}\n", 'line'); self.lines2.insert("end", f"{line_num_2}\n", 'line')
                self.apply_inline_tags(line1_text, line2_text)
                line_num_1 += 1; line_num_2 += 1; i += 2
            elif line.startswith('- '):
                self.text1.insert("end", line[2:] + '\n', 'removed'); self.text2.insert("end", '\n', 'blank')
                self.lines1.insert("end", f"{line_num_1}\n", 'line'); self.lines2.insert("end", '\n', 'blank')
                line_num_1 += 1; i += 1
            elif line.startswith('+ '):
                self.text1.insert("end", '\n', 'blank'); self.text2.insert("end", line[2:] + '\n', 'added')
                self.lines1.insert("end", '\n', 'blank'); self.lines2.insert("end", f"{line_num_2}\n", 'line')
                line_num_2 += 1; i += 1
            else: i += 1 
        for widget in [self.text1, self.text2, self.lines1, self.lines2]: widget.configure(state="disabled")

    def apply_inline_tags(self, line1, line2):
        s = difflib.SequenceMatcher(None, line1, line2, autojunk=False)
        line1_start, line2_start = self.text1.index(f"end - {len(line1) + 1}c"), self.text2.index(f"end - {len(line2) + 1}c")
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            if tag == 'replace':
                self.text1.tag_add('highlight_inline', f"{line1_start} + {i1}c", f"{line1_start} + {i2}c")
                self.text2.tag_add('highlight_inline', f"{line2_start} + {j1}c", f"{line2_start} + {j2}c")
            elif tag == 'delete':
                self.text1.tag_add('highlight_inline', f"{line1_start} + {i1}c", f"{line1_start} + {i2}c")
            elif tag == 'insert':
                self.text2.tag_add('highlight_inline', f"{line2_start} + {j1}c", f"{line2_start} + {j2}c")


class CloneSettingsWindow(ctk.CTkFrame):
    """
    A Toplevel window for managing clone options.
    """
    def __init__(self, parent_toplevel, clone_options, on_change_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.clone_options = clone_options
        self.on_change_callback = on_change_callback

        ctk.CTkLabel(self, text="Select fields to clone from TEST to PROD:", font=ctk.CTkFont(weight="bold")).pack(pady=(0, 10))

        # Create checkboxes for each option
        # We use a specific order for better UX
        order = ["host", "port", "user", "pass", "path"]
        labels = {
            "host": "Host / URL",
            "port": "Port",
            "user": "Username",
            "pass": "Password",
            "path": "Remote Path"
        }

        for key in order:
            if key in self.clone_options:
                cb = ctk.CTkCheckBox(
                    self, 
                    text=labels.get(key, key), 
                    variable=self.clone_options[key],
                    command=lambda k=key: self.on_change_callback(k)
                )
                cb.pack(anchor="w", pady=5, padx=20)
        
        ctk.CTkButton(self, text="Close", command=parent_toplevel.destroy).pack(pady=(20, 0))


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
        status, relative_path, owner_group, symbolic_perms, raw_octal_perms = first_item
        octal_perms = str(raw_octal_perms) # Ensure it's a string
        current_owner, current_group = owner_group.split(':', 1) if ':' in owner_group else (owner_group, "")
        
        self.owner_var = ctk.StringVar(value=current_owner.split(' -> ')[0])


        self.group_var = ctk.StringVar(value=current_group.split(' -> ')[0])
        self.new_perms = ctk.StringVar(value=octal_perms.split(' -> ')[0]) # Initialize with octal
        
        self.change_queue, self.user_queue, self.group_queue = queue.Queue(), queue.Queue(), queue.Queue()
        self.tasks_running, self.user_fetch_tasks, self.group_fetch_tasks = 0, 0, 0
        self.all_users, self.all_groups = set(), set()

        self.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(self, text="File(s):", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        
        if len(self.selected_items_data) > 1:
            file_display_text = f"{len(self.selected_items_data)} files selected"
            # Optionally list first few files
            # file_display_text += ": " + ", ".join([item[1] for item in self.selected_items_data[:3]]) + ("..." if len(self.selected_items_data) > 3 else "")
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
            # Check if all selected items have the same owner/group
            all_same_owner_group = all(item[2] == owner_group for item in self.selected_items_data)
            if all_same_owner_group:
                ctk.CTkLabel(self, text=f"(Current Owner:Group: {owner_group})", font=ctk.CTkFont(size=10)).grid(row=3, column=1, sticky="w", padx=5)
            else:
                ctk.CTkLabel(self, text="(Current Owner:Group: Mixed)", font=ctk.CTkFont(size=10)).grid(row=3, column=1, sticky="w", padx=5)


        ctk.CTkLabel(self, text="Perms (octal)").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        ctk.CTkEntry(self, textvariable=self.new_perms).grid(row=4, column=1, sticky="ew", padx=5, pady=5)
        
        if len(self.selected_items_data) == 1:
            ctk.CTkLabel(self, text=f"(Current: {symbolic_perms} / {octal_perms})", font=ctk.CTkFont(size=10)).grid(row=5, column=1, sticky="w", padx=5)
        else:
            # Check if all selected items have the same permissions
            all_same_perms = all(str(item[4]) == octal_perms for item in self.selected_items_data)
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
        if not has_test_files: self.check_test.configure(state="disabled")
        if not has_prod_files: self.check_prod.configure(state="disabled")

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
            self.user_fetch_tasks += 1; self.group_fetch_tasks += 1
            threading.Thread(target=sftp_logic.get_all_users_task, args=(self.s1_config, self.user_queue, "TEST"), daemon=True).start()
            threading.Thread(target=sftp_logic.get_all_groups_task, args=(self.s1_config, self.group_queue, "TEST"), daemon=True).start()
        
        if self.target_prod.get():
            self.user_fetch_tasks += 1; self.group_fetch_tasks += 1
            threading.Thread(target=sftp_logic.get_all_users_task, args=(self.s2_config, self.user_queue, "PROD"), daemon=True).start()
            threading.Thread(target=sftp_logic.get_all_groups_task, args=(self.s2_config, self.group_queue, "PROD"), daemon=True).start()

        if self.user_fetch_tasks > 0: self.after(100, self.check_user_queue)
        else: self.owner_menu.configure(state="normal", values=[self.owner_var.get()])
        
        if self.group_fetch_tasks > 0: self.after(100, self.check_group_queue)
        else: self.group_menu.configure(state="normal", values=[self.group_var.get()])

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
                else: self.owner_menu.configure(state="normal", values=[self.owner_var.get()])
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
                else: self.group_menu.configure(state="normal", values=[self.group_var.get()])
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
            status, relative_path, _, _, _ = item_data # Unpack relevant data
            
            # Apply to TEST server if selected and file is on TEST
            if self.target_test.get() and status in ["DIFFERENT", "IDENTICAL", "ONLY ON TEST"]:
                self.tasks_running += 1
                threading.Thread(target=sftp_logic.change_attributes_task, args=(self.s1_config, relative_path, owner, group, perms_str, self.change_queue, f"TEST:{relative_path}"), daemon=True).start()

            # Apply to PROD server if selected and file is on PROD
            if self.target_prod.get() and status in ["DIFFERENT", "IDENTICAL", "ONLY ON PROD"]:
                self.tasks_running += 1
                threading.Thread(target=sftp_logic.change_attributes_task, args=(self.s2_config, relative_path, owner, group, perms_str, self.change_queue, f"PROD:{relative_path}"), daemon=True).start()

        if self.tasks_running > 0: self.after(100, self.check_change_queue)
        else: self.status_callback("No servers selected or no relevant files on selected servers."); self.stop_loading()

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
                    parts = result.split(':', 2) # Split at most twice
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
                self.parent_toplevel.after(1000, self.parent_toplevel.destroy) # Close after 1s
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

class SyncWindow(ctk.CTkFrame):
    """
    A Toplevel window for managing the synchronization from TEST to PROD.
    """
    def __init__(self, parent_toplevel, s1_config, s2_config, results, refresh_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True)

        self.parent_toplevel = parent_toplevel
        self.s1_config, self.s2_config = s1_config, s2_config
        self.results = results
        self.refresh_callback = refresh_callback
        self.sync_queue = queue.Queue()

        # --- Layout ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- 1. Summary Frame ---
        summary_frame = ctk.CTkFrame(self, border_width=1)
        summary_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        summary_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(summary_frame, text="Sync Summary", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=5, sticky="w")
        
        self.to_copy = len(self.results.get('only_on_1', []))
        self.to_overwrite = len(self.results.get('different', []))
        self.to_delete = len(self.results.get('only_on_2', []))

        ctk.CTkLabel(summary_frame, text=f"Files to copy to PROD:").grid(row=1, column=0, sticky="w", padx=10, pady=2)
        ctk.CTkLabel(summary_frame, text=str(self.to_copy)).grid(row=1, column=1, sticky="w", padx=10, pady=2)
        
        ctk.CTkLabel(summary_frame, text=f"Files to overwrite on PROD:").grid(row=2, column=0, sticky="w", padx=10, pady=2)
        ctk.CTkLabel(summary_frame, text=str(self.to_overwrite)).grid(row=2, column=1, sticky="w", padx=10, pady=2)

        ctk.CTkLabel(summary_frame, text=f"Files to delete from PROD:").grid(row=3, column=0, sticky="w", padx=10, pady=(2,10))
        ctk.CTkLabel(summary_frame, text=str(self.to_delete), text_color="orange").grid(row=3, column=1, sticky="w", padx=10, pady=(2,10))

        # --- 2. Options Frame ---
        options_frame = ctk.CTkFrame(self, border_width=1)
        options_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=0)
        options_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(options_frame, text="Backup Options for PROD", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        self.backup_var = ctk.StringVar(value="none")
        
        radio1 = ctk.CTkRadioButton(options_frame, text="No Backup. Make PROD identical to TEST.", variable=self.backup_var, value="none")
        radio1.grid(row=1, column=0, sticky="w", padx=10, pady=5)
        
        radio2 = ctk.CTkRadioButton(options_frame, text="Remote Backup: Copy PROD folder on the server before sync.", variable=self.backup_var, value="remote")
        radio2.grid(row=2, column=0, sticky="w", padx=10, pady=5)
        
        radio3 = ctk.CTkRadioButton(options_frame, text="Local Backup: Download PROD folder to this PC before sync.", variable=self.backup_var, value="local")
        radio3.grid(row=3, column=0, sticky="w", padx=10, pady=(5,10))

        # --- 3. Action Frame ---
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=10)
        action_frame.grid_columnconfigure(0, weight=1)

        self.action_button = ctk.CTkButton(action_frame, text="Start Synchronization", command=self.start_sync_process)
        self.action_button.pack(side="left", padx=(0,10))
        
        self.cancel_button = ctk.CTkButton(action_frame, text="Cancel", command=self.parent_toplevel.destroy, fg_color="gray")
        self.cancel_button.pack(side="left")

        # --- 4. Status Bar ---
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
        delete_on_prod = True # By default, we make PROD a mirror of TEST

        if backup_choice == "none":
            if not messagebox.askyesno("Confirm Sync", "This will modify the PRODUCTION server to match the TEST server, including deleting files, without a backup.\n\nAre you absolutely sure?", parent=self.parent_toplevel):
                self.stop_loading()
                return
            self.run_sync_task(delete_on_prod)

        elif backup_choice == "remote":
            self.update_status("Starting remote backup...")
            threading.Thread(target=sftp_logic.backup_folder_remote_task, args=(self.s2_config, self.sync_queue, "PROD"), daemon=True).start()
            self.after(100, lambda: self.check_queue(delete_on_prod))

        elif backup_choice == "local":
            local_path = filedialog.askdirectory(title="Select Local Backup Folder", parent=self.parent_toplevel)
            if not local_path:
                self.stop_loading()
                return
            self.update_status("Starting local backup...")
            threading.Thread(target=sftp_logic.backup_folder_local_task, args=(self.s2_config, local_path, self.sync_queue, "PROD"), daemon=True).start()
            self.after(100, lambda: self.check_queue(delete_on_prod))

    def run_sync_task(self, delete_on_prod):
        """Starts the main synchronization task in a thread."""
        self.update_status("Starting synchronization...")
        self.progress_bar.start()
        threading.Thread(target=sftp_logic.sync_folders_task, args=(self.s1_config, self.s2_config, self.results, delete_on_prod, self.sync_queue), daemon=True).start()
        self.after(100, lambda: self.check_queue(delete_on_prod))

    def check_queue(self, delete_on_prod):
        """Checks the queue for updates from the running tasks."""
        try:
            result = self.sync_queue.get_nowait()

            if isinstance(result, Exception):
                self.show_error("Task Error", f"An error occurred:\n{result}")
                self.stop_loading()
            elif isinstance(result, str):
                self.update_status(result)
                self.after(100, lambda: self.check_queue(delete_on_prod))
            elif isinstance(result, dict):
                status = result.get('status')
                if status == 'backup_complete' and result.get('success'):
                    self.update_status(f"Backup complete. Path: {result.get('path')}")
                    self.run_sync_task(delete_on_prod) # Start sync after backup
                elif status == 'sync_complete' and result.get('success'):
                    self.update_status("Synchronization finished successfully!")
                    self.stop_loading(finished=True)
                else:
                    self.after(100, lambda: self.check_queue(delete_on_prod))
        except queue.Empty:
            self.after(100, lambda: self.check_queue(delete_on_prod))
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

class EditorWindow(ctk.CTkFrame):
    """
    A Toplevel window for editing a single file.
    """
    def __init__(self, parent_toplevel, config, relative_path, content, server_name, on_save_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.config = config
        self.relative_path = relative_path
        self.server_name = server_name
        self.on_save_callback = on_save_callback
        
        self.upload_queue = queue.Queue()

        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(header_frame, text=f"Editing: {relative_path} ({server_name})", font=ctk.CTkFont(weight="bold")).pack(side="left")
        
        self.save_button = ctk.CTkButton(header_frame, text="Save & Upload", command=self.start_upload)
        self.save_button.pack(side="right")

        # Text Editor
        self.text_area = ctk.CTkTextbox(self, wrap="none", font=("monospace", 12))
        self.text_area.pack(fill="both", expand=True)
        self.text_area.insert("1.0", content)

        # Status Bar
        self.status_label = ctk.CTkLabel(self, text="Ready to edit.", anchor="w")
        self.status_label.pack(fill="x", pady=(5, 0))

    def start_upload(self):
        """Starts the upload process."""
        content = self.text_area.get("1.0", "end-1c") # Get all text except the last newline
        self.save_button.configure(state="disabled")
        self.status_label.configure(text="Uploading...")
        
        threading.Thread(target=sftp_logic.upload_file_task, args=(self.config, self.relative_path, content, self.upload_queue, self.server_name), daemon=True).start()
        self.after(100, self.check_upload_queue)

    def check_upload_queue(self):
        """Checks the upload status."""
        try:
            result = self.upload_queue.get_nowait()
            
            if isinstance(result, Exception):
                messagebox.showerror("Upload Error", f"Failed to upload file:\n{result}", parent=self)
                self.status_label.configure(text="Upload failed.")
                self.save_button.configure(state="normal")
            elif isinstance(result, str):
                self.status_label.configure(text=result)
                self.after(100, self.check_upload_queue)
            elif isinstance(result, dict) and result.get('status') == 'upload_complete':
                messagebox.showinfo("Success", "File saved and uploaded successfully.", parent=self)
                self.on_save_callback() # Refresh the diff
                self.winfo_toplevel().destroy() # Close editor
                
        except queue.Empty:
            self.after(100, self.check_upload_queue)