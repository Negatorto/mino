import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
import threading
import queue
import json
import os
import webbrowser
import difflib
import subprocess
import platform

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Warning: PIL.ImageTk not available. Icon will not be loaded.")
    Image = None
    ImageTk = None

import sftp_logic
from ui_editor import DiffWindow, EditorWindow
from ui_sync import SyncWindow
from ui_attributes import AttributesWindow
from ui_settings import CloneSettingsWindow, NoteKeywordWindow, IgnorePatternsWindow

class App(ctk.CTk):
    """
    Main Application Window Class
    """
    def __init__(self):
        super().__init__()

        self.title("MINO: Mirroring Integrity Network Operations")
        self.geometry("900x700")

        # --- Icon Setup ---
        # Absolute path to assets directory
        assets_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets")

        # 1. For Windows (.ico)
        try:
            icon_path_ico = os.path.join(assets_path, "mino.ico")
            if os.path.exists(icon_path_ico):
                self.iconbitmap(icon_path_ico)
        except Exception:
            pass  # Ignore errors on non-Windows systems or if file issues

        # 2. For Linux/macOS (.png)
        if Image and ImageTk:
            try:
                icon_path_png = os.path.join(assets_path, "mino_icon.png")
                if os.path.exists(icon_path_png):
                    icon_img = Image.open(icon_path_png)
                    self.iconphoto(False, ImageTk.PhotoImage(icon_img))
            except Exception as e:
                print(f"Error loading icon: {e}")
        else:
            print("Skipping icon load: PIL.ImageTk not available.")

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
        self.grid_rowconfigure(3, weight=1)  # Results row (shifted to 3)

        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)  # Shifted to 1
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
        control_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)  # Shifted to 2
        control_frame.grid_columnconfigure(0, weight=1)

        # Button sub-frame
        button_subframe = ctk.CTkFrame(control_frame, fg_color="transparent")
        button_subframe.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.compare_button = ctk.CTkButton(button_subframe, text="Compare Folders", command=self.start_comparison)
        self.compare_button.pack(side="left", padx=(0, 10))

        self.diff_button = ctk.CTkButton(button_subframe, text="Compare Selected File", command=self.open_diff_window, state="disabled")
        self.diff_button.pack(side="left", padx=(0, 10))

        self.sync_button = ctk.CTkButton(button_subframe, text="Sync TEST -> PROD", command=lambda: self.open_sync_window(reverse=False), state="disabled")
        self.sync_button.pack(side="left", padx=(0, 10))

        self.sync_reverse_button = ctk.CTkButton(button_subframe, text="Sync PROD -> TEST", command=lambda: self.open_sync_window(reverse=True), state="disabled")
        self.sync_reverse_button.pack(side="left", padx=(0, 10))

        # Clone Checkbox (Moved to Menu)
        self.clone_var = ctk.BooleanVar(value=False)
        self.scan_notes_var = ctk.BooleanVar(value=False)

        self.scan_notes_switch = ctk.CTkSwitch(button_subframe, text="Scan Notes", variable=self.scan_notes_var)
        self.scan_notes_switch.pack(side="left", padx=20)
        self.note_keyword_var = ctk.StringVar(value="MINO")
        self.ignore_patterns_var = ctk.StringVar(value="")  # Comma-separated patterns

        # Theme switcher (Moved to Menu)

        result_frame = ctk.CTkFrame(self)
        result_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))  # Shifted to 3
        result_frame.grid_rowconfigure(0, weight=1)
        result_frame.grid_columnconfigure(0, weight=1)

        self.create_treeview(result_frame)

        status_frame = ctk.CTkFrame(self, height=30)
        status_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))  # Shifted to 4

        self.status_var = ctk.StringVar(value="Ready (Using SFTP).")
        self.status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", fill="x", padx=10, expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=150)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)
        self.progress_bar.configure(mode="indeterminate")

        # Note display label
        self.note_display_var = ctk.StringVar(value="")
        # Red for Light mode, Orange for Dark mode
        self.note_display_label = ctk.CTkLabel(status_frame, textvariable=self.note_display_var, text_color=("red", "#FFCC00"), font=("Helvetica", 12, "bold"))
        self.note_display_label.pack(side="right", padx=10)

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
        self.about_btn = ctk.CTkButton(menubar_frame, text="About", width=60, fg_color="transparent", text_color=("black", "white"), hover_color=("gray70", "gray25"), command=self.open_about_window)
        self.about_btn.pack(side="left", padx=2)

    def show_file_menu(self):
        from tkinter import Menu  # Lazy import
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
        menu.add_command(label="Set Note Keyword...", command=self.open_note_keyword_settings)
        menu.add_command(label="Set Ignore Patterns...", command=self.open_ignore_patterns_settings)

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
            "clone_options": {k: v.get() for k, v in self.clone_options.items()},
            "scan_notes": self.scan_notes_var.get(),
            "note_keyword": self.note_keyword_var.get(),
            "ignore_patterns": self.ignore_patterns_var.get()
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
            elif "clone_enabled" in data:  # Backwards compatibility
                val = data["clone_enabled"]
                for v in self.clone_options.values():
                    v.set(val)

            if "scan_notes" in data:
                self.scan_notes_var.set(data["scan_notes"])

            if "note_keyword" in data:
                self.note_keyword_var.set(data["note_keyword"])

            if "ignore_patterns" in data:
                self.ignore_patterns_var.set(data["ignore_patterns"])

            self.update_status(f"Workspace loaded from {os.path.basename(file_path)}")
        except Exception as e:
            self.show_error("Load Error", f"Could not load workspace:\n{e}")

    def create_server_widgets(self, parent, title, col):
        """Creates the input widgets for a server."""
        frame = ctk.CTkFrame(parent, border_width=1)
        frame.grid(row=0, column=col, sticky="nsew", padx=5, pady=5)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 10))

        server_vars = {
            "host": ctk.StringVar(),
            "port": ctk.StringVar(value="22"),
            "user": ctk.StringVar(),
            "pass": ctk.StringVar(),
            "path": ctk.StringVar()
        }

        ctk.CTkLabel(frame, text="Host:").grid(row=1, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["host"], placeholder_text="Leave empty or 'local'", placeholder_text_color=["gray10", "gray90"]).grid(row=1, column=1, sticky="ew", padx=10, pady=3)

        ctk.CTkLabel(frame, text="Port:").grid(row=2, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["port"], width=60).grid(row=2, column=1, sticky="w", padx=10, pady=3)

        ctk.CTkLabel(frame, text="Username:").grid(row=3, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["user"]).grid(row=3, column=1, sticky="ew", padx=10, pady=3)

        ctk.CTkLabel(frame, text="Password:").grid(row=4, column=0, sticky="w", padx=10, pady=3)
        ctk.CTkEntry(frame, textvariable=server_vars["pass"], show="*").grid(row=4, column=1, sticky="ew", padx=10, pady=3)

        ctk.CTkLabel(frame, text="Remote Path:").grid(row=5, column=0, sticky="w", padx=10, pady=(3, 10))
        ctk.CTkEntry(frame, textvariable=server_vars["path"]).grid(row=5, column=1, sticky="ew", padx=10, pady=(3, 10))

        return server_vars

    def create_treeview(self, parent):
        """Creates and styles the results Treeview."""
        self.style = ttk.Style(self)
        self.tree = ttk.Treeview(parent, columns=("Status", "Path", "Note", "Owner", "Perms"), show="headings")

        self.tree.heading("Status", text="Status")
        self.tree.heading("Path", text="File Path")
        self.tree.heading("Note", text="Note")
        self.tree.heading("Owner", text="Owner:Group")
        self.tree.heading("Perms", text="Permissions")

        self.tree.column("Status", width=150, anchor='w')
        self.tree.column("Path", width=410, anchor='w')
        self.tree.column("Note", width=60, anchor='center')
        self.tree.column("Owner", width=120, anchor='w')
        self.tree.column("Perms", width=120, anchor='w')

        scrollbar = ctk.CTkScrollbar(parent, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.grid(row=0, column=0, sticky="nsew")

        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<Double-1>', self.on_tree_double_click)
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
                selected_items = self.tree.selection()  # Update selected_items

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

            # "Sync File" option (TEST -> PROD)
            syncable_items_test_prod = []
            syncable_items_prod_test = []
            for item_id in selected_items:
                item_data = self.tree.item(item_id)
                status = item_data['values'][0] if item_data['values'] else ""
                if status in ["DIFFERENT", "ONLY ON TEST"]:
                    syncable_items_test_prod.append(item_id)
                if status in ["DIFFERENT", "ONLY ON PROD"]:
                    syncable_items_prod_test.append(item_id)

            if len(syncable_items_test_prod) == 1:
                context_menu.add_command(label="Sync File (TEST -> PROD)", command=lambda: self.sync_single_file(syncable_items_test_prod[0]))
            elif len(syncable_items_test_prod) > 1:
                context_menu.add_command(label=f"Sync {len(syncable_items_test_prod)} Files (TEST -> PROD)", command=lambda: self.sync_selected_files(syncable_items_test_prod))
            else:
                context_menu.add_command(label="Sync File (TEST -> PROD)", state="disabled")

            if len(syncable_items_prod_test) == 1:
                context_menu.add_command(label="Sync File (PROD -> TEST)", command=lambda: self.sync_single_file(syncable_items_prod_test[0], reverse=True))
            elif len(syncable_items_prod_test) > 1:
                context_menu.add_command(label=f"Sync {len(syncable_items_prod_test)} Files (PROD -> TEST)", command=lambda: self.sync_selected_files(syncable_items_prod_test, reverse=True))
            else:
                context_menu.add_command(label="Sync File (PROD -> TEST)", state="disabled")

            # "Change Attributes..." is always available for selected items
            context_menu.add_command(label="Change Attributes...", command=lambda: self.open_attributes_window(selected_items))
            context_menu.tk_popup(event.x_root, event.y_root)

        except Exception as e:
            print(f"Error showing context menu: {e}")

    def sync_single_file(self, item_id, reverse=False):
        """Starts the single file sync process."""
        item_data = self.tree.item(item_id)
        values = item_data['values']
        relative_path = values[1]

        direction = "PROD to TEST" if reverse else "TEST to PROD"
        if not messagebox.askyesno("Confirm Sync", f"Are you sure you want to overwrite/copy '{relative_path}' ({direction})?", parent=self):
            return

        self.update_status(f"Syncing {relative_path}...")
        self.progress_bar.start()

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        if reverse:
            threading.Thread(target=sftp_logic.sync_single_file_task, args=(s2_config, s1_config, relative_path, self.result_queue), daemon=True).start()
        else:
            threading.Thread(target=sftp_logic.sync_single_file_task, args=(s1_config, s2_config, relative_path, self.result_queue), daemon=True).start()
        self.after(100, self.check_single_sync_queue)

    def sync_selected_files(self, item_ids, reverse=False):
        """Starts the batch sync process for selected files."""
        files_to_sync = []
        for item_id in item_ids:
            values = self.tree.item(item_id)['values']
            files_to_sync.append(values[1])  # relative_path

        direction = "PROD to TEST" if reverse else "TEST to PROD"
        if not messagebox.askyesno("Confirm Batch Sync", f"Are you sure you want to sync {len(files_to_sync)} files ({direction})?", parent=self):
            return

        self.update_status(f"Starting batch sync for {len(files_to_sync)} files ({direction})...")
        self.progress_bar.start()

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        if reverse:
            threading.Thread(target=sftp_logic.sync_multiple_files_task, args=(s2_config, s1_config, files_to_sync, self.result_queue), daemon=True).start()
        else:
            threading.Thread(target=sftp_logic.sync_multiple_files_task, args=(s1_config, s2_config, files_to_sync, self.result_queue), daemon=True).start()
        self.after(100, self.check_batch_sync_queue)

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
                if messagebox.askyesno("Sync Complete", "File synced. Refresh comparison?", parent=self):
                    self.start_comparison()
        except queue.Empty:
            self.after(100, self.check_single_sync_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error updating UI: {e}")
            self.stop_loading()

    def check_batch_sync_queue(self):
        """Checks the queue for batch sync results."""
        try:
            result = self.result_queue.get_nowait()

            if isinstance(result, Exception):
                self.show_error("Sync Error", f"An error occurred:\n{result}")
                self.stop_loading()
            elif isinstance(result, str):
                self.update_status(result)
                self.after(100, self.check_batch_sync_queue)
            elif isinstance(result, dict):
                if result.get('status') == 'single_sync_complete':
                    self.update_status(f"Synced {result.get('file')} successfully.")
                    self.after(100, self.check_batch_sync_queue)
                elif result.get('status') == 'batch_sync_complete':
                    self.stop_loading()
                    if messagebox.askyesno("Batch Sync Complete", "All selected files synced. Refresh comparison?", parent=self):
                        self.start_comparison()
        except queue.Empty:
            self.after(100, self.check_batch_sync_queue)
        except Exception as e:
            self.show_error("GUI Error", f"Error updating UI: {e}")
            self.stop_loading()

    def update_treeview_style(self, mode):
        """Updates the ttk.Treeview style to match the CTk theme."""
        if mode == "Dark":
            bg, fg, heading_bg = "#2B2B2B", "#E0E0E0", "#343638"
        else:  # "Light"
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
            self.note_display_var.set("")  # Clear note display
            return

        item = self.tree.item(selected_items[0])
        status = item['values'][0] if item['values'] else ""

        # Check for note content
        note_content = ""
        relative_path = item['values'][1]

        if self.comparison_results:
            # Check metadata from both sources
            # Check files_s1
            s1_meta = self.comparison_results.get('files_s1', {}).get(relative_path)
            if s1_meta and s1_meta.get('has_note') and isinstance(s1_meta['has_note'], str):
                note_content = s1_meta['has_note']

            # If not found in s1, check s2
            if not note_content:
                s2_meta = self.comparison_results.get('files_s2', {}).get(relative_path)
                if s2_meta and s2_meta.get('has_note') and isinstance(s2_meta['has_note'], str):
                    note_content = s2_meta['has_note']

        if note_content:
            self.note_display_var.set(f"{note_content}")
        else:
            self.note_display_var.set("")

        if status in ["DIFFERENT", "IDENTICAL"]:
            self.diff_button.configure(state="normal")
        else:
            self.diff_button.configure(state="disabled")

    def on_tree_double_click(self, event):
        """Opens Diff Window on double-click if valid."""
        selected_items = self.tree.selection()
        if not selected_items:
            return

        item = self.tree.item(selected_items[0])
        status = item['values'][0] if item['values'] else ""

        if status in ["DIFFERENT", "IDENTICAL"]:
            self.open_diff_window()

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
        
    def open_sync_window(self, reverse=False):
        """Opens the SyncWindow for the current comparison."""
        if not self.comparison_results:
            self.show_error("Sync Error", "Please run a comparison first.")
            return

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        source_name = "PRODUCTION" if reverse else "TEST"
        dest_name = "TEST" if reverse else "PRODUCTION"

        sync_top_level = ctk.CTkToplevel(self)
        sync_top_level.title(f"Synchronize {source_name} to {dest_name}")
        sync_top_level.geometry("600x550")
        sync_top_level.transient(self)
        
        # Center
        sync_top_level.update_idletasks()
        width = sync_top_level.winfo_width()
        height = sync_top_level.winfo_height()
        x = (sync_top_level.winfo_screenwidth() // 2) - (width // 2)
        y = (sync_top_level.winfo_screenheight() // 2) - (height // 2)
        sync_top_level.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        SyncWindow(sync_top_level, s1_config, s2_config, self.comparison_results, self.start_comparison, reverse=reverse)

    def open_clone_settings(self):
        """Opens the Clone Settings popup window."""
        clone_top_level = ctk.CTkToplevel(self)
        clone_top_level.title("Clone Options")
        clone_top_level.geometry("320x290")
        clone_top_level.transient(self)
        CloneSettingsWindow(clone_top_level, self.clone_options, self.on_clone_input)

    def open_note_keyword_settings(self):
        """Opens a custom dialog to set the note keyword."""
        keyword_top_level = ctk.CTkToplevel(self)
        keyword_top_level.title("Set Note Keyword")
        keyword_top_level.geometry("300x150")
        keyword_top_level.transient(self)
        
        # Center the window
        keyword_top_level.update_idletasks()
        width = keyword_top_level.winfo_width()
        height = keyword_top_level.winfo_height()
        x = (keyword_top_level.winfo_screenwidth() // 2) - (width // 2)
        y = (keyword_top_level.winfo_screenheight() // 2) - (height // 2)
        keyword_top_level.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        NoteKeywordWindow(keyword_top_level, self.note_keyword_var, lambda k: print(f"Note keyword set to: {k}"))

    def open_ignore_patterns_settings(self):
        """Opens a custom dialog to set the ignore patterns."""
        ignore_top_level = ctk.CTkToplevel(self)
        ignore_top_level.title("Set Ignore Patterns")
        ignore_top_level.geometry("400x330")
        ignore_top_level.transient(self)
        
        # Center the window
        ignore_top_level.update_idletasks()
        width = ignore_top_level.winfo_width()
        height = ignore_top_level.winfo_height()
        x = (ignore_top_level.winfo_screenwidth() // 2) - (width // 2)
        y = (ignore_top_level.winfo_screenheight() // 2) - (height // 2)
        ignore_top_level.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        IgnorePatternsWindow(ignore_top_level, self.ignore_patterns_var)

    def update_status(self, message):
        """Thread-safe method to update the status label."""
        self.status_var.set(message)

    def start_comparison(self):
        """Starts the folder comparison in a background thread."""
        self.compare_button.configure(state="disabled")
        self.diff_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")
        self.sync_reverse_button.configure(state="disabled") # Added this line
        self.progress_bar.start()
        self.update_status("Starting comparison...")
        
        self.comparison_results = None
        for i in self.tree.get_children():
            self.tree.delete(i)

        s1 = {k: v.get() for k, v in self.server1_vars.items()}
        s2 = {k: v.get() for k, v in self.server2_vars.items()}

        threading.Thread(target=sftp_logic.compare_folders_task, args=(s1, s2, self.result_queue, self.note_keyword_var.get() if self.scan_notes_var.get() else False, self.ignore_patterns_var.get()), daemon=True).start()
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
            
            owner_str = owner1_str if owner1_str == owner2_str else f"{owner1_str} -> {owner2_str}"
            perms_sym_str = perms1_sym if perms1_sym == perms2_sym else f"{perms1_sym} -> {perms2_sym}"
            perms_oct_str = perms1_oct if perms1_oct == perms2_oct else f"{perms1_oct} -> {perms2_oct}"
            
            note_str = ""
            if self.scan_notes_var.get():
                has_note1 = meta1.get('has_note', False)
                has_note2 = meta2.get('has_note', False)
                if has_note1 or has_note2:
                    note_str = "⚠" # Orange warning
                else:
                    note_str = "✓"

            self.tree.insert("", "end", values=("DIFFERENT", f, note_str, owner_str, perms_sym_str, perms_oct_str), tags=('different',))
        
        for f in sorted(results['only_on_1']):
            meta = files_s1_meta.get(f, {})
            owner = f"{meta.get('owner', '?')}:{meta.get('group', '?')}"
            perms_sym = meta.get('mode', '?')
            perms_oct = meta.get('octal_mode', '?')
            perms_oct = meta.get('octal_mode', '?')

            note_str = ""
            if self.scan_notes_var.get():
                 if meta.get('has_note', False):
                     note_str = "⚠"
                 else:
                     note_str = "✓"

            self.tree.insert("", "end", values=("ONLY ON TEST", f, note_str, owner, perms_sym, perms_oct), tags=('solo_test',))

        for f in sorted(results['only_on_2']):
            meta = files_s2_meta.get(f, {})
            owner = f"{meta.get('owner', '?')}:{meta.get('group', '?')}"
            perms_sym = meta.get('mode', '?')
            perms_oct = meta.get('octal_mode', '?')
            perms_oct = meta.get('octal_mode', '?')

            note_str = ""
            if self.scan_notes_var.get():
                if meta.get('has_note', False):
                    note_str = "⚠"
                else:
                    note_str = "✓"

            self.tree.insert("", "end", values=("ONLY ON PROD", f, note_str, owner, perms_sym, perms_oct), tags=('solo_prod',))
        
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
            
            owner_str = owner1_str if owner1_str == owner2_str else f"{owner1_str} -> {owner2_str}"
            perms_sym_str = perms1_sym if perms1_sym == perms2_sym else f"{perms1_sym} -> {perms2_sym}"
            perms_oct_str = perms1_oct if perms1_oct == perms2_oct else f"{perms1_oct} -> {perms2_oct}"
            
            note_str = ""
            if self.scan_notes_var.get():
                has_note1 = meta1.get('has_note', False)
                has_note2 = meta2.get('has_note', False)
                if has_note1 or has_note2:
                    note_str = "⚠"
                else:
                    note_str = "✓"

            self.tree.insert("", "end", values=("IDENTICAL", f, note_str, owner_str, perms_sym_str, perms_oct_str), tags=('identical',))

        self.diff_button.configure(state="disabled")
        
        has_changes = results['different'] or results['only_on_1'] or results['only_on_2']
        if has_changes:
            self.sync_button.configure(state="normal")
        
        self.update_status(f"Done. Found {len(results['different'])} different files, "
                           f"{len(results['only_on_1'])} only on TEST, {len(results['only_on_2'])} only on PROD.")

    def show_error(self, title, message):
        """Shows a cross-platform error message."""
        messagebox.showerror(title, message, parent=self)

    def open_url(self, url):
        """Opens a URL, suppressing stderr on Linux to avoid Wayland warnings."""
        if platform.system() == "Linux":
            try:
                # Use xdg-open directly and suppress stderr
                subprocess.Popen(['xdg-open', url], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            except Exception:
                webbrowser.open(url) # Fallback
        else:
            webbrowser.open(url)

    def open_about_window(self):
        """Opens the About pop-up."""
        about_toplevel = ctk.CTkToplevel(self)
        about_toplevel.title("About MINO")
        about_toplevel.geometry("340x200")
        about_toplevel.resizable(False, False)
        about_toplevel.transient(self) # Make it modal-like on top of main window
        
        # Center the window
        about_toplevel.update_idletasks()
        width = about_toplevel.winfo_width()
        height = about_toplevel.winfo_height()
        x = (about_toplevel.winfo_screenwidth() // 2) - (width // 2)
        y = (about_toplevel.winfo_screenheight() // 2) - (height // 2)
        about_toplevel.geometry('{}x{}+{}+{}'.format(width, height, x, y))

        # Content
        # Title (Bold)
        ctk.CTkLabel(about_toplevel, text="MINO", font=("Helvetica", 24, "bold")).pack(pady=(20, 2))
        
        # Subtitle (Regular font)
        ctk.CTkLabel(about_toplevel, text="Mirroring Integrity Network Operations", font=("Helvetica", 13)).pack(pady=(0, 10))
        
        # Author (Same Regular font)
        ctk.CTkLabel(about_toplevel, text="Created by Alessio Saponaro", font=("Helvetica", 13)).pack(pady=(0, 15))
        
        # Button
        ctk.CTkButton(about_toplevel, text="View on GitHub", height=30, command=lambda: self.open_url("https://github.com/Negatorto/mino")).pack(pady=5)


if __name__ == "__main__":
    ctk.set_appearance_mode("System")  # Modes: "System" (standard), "Dark", "Light"
    ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

    app = App()
    app.mainloop()