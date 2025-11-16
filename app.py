import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
import threading
import queue
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

        # --- Main Layout ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1) # Results row

        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        input_frame.grid_columnconfigure((0, 1), weight=1)

        self.server1_vars = self.create_server_widgets(input_frame, "TEST Server", 0)
        self.server2_vars = self.create_server_widgets(input_frame, "PRODUCTION Server", 1)

        # Trace "clone" variables
        self.server1_vars["user"].trace_add("write", self.on_clone_input)
        self.server1_vars["pass"].trace_add("write", self.on_clone_input)
        self.server1_vars["path"].trace_add("write", self.on_clone_input)

        control_frame = ctk.CTkFrame(self)
        control_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
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

        # Clone Checkbox
        self.clone_var = ctk.BooleanVar(value=False)
        self.clone_check = ctk.CTkCheckBox(control_frame, text="Clone TEST input (User, Pass, Path) to PRODUCTION", variable=self.clone_var)
        self.clone_check.grid(row=1, column=0, sticky="w", padx=10, pady=5)

        # Theme switcher
        self.theme_menu = ctk.CTkOptionMenu(
            control_frame,
            values=["Light", "Dark", "System"],
            command=self.change_appearance_mode
        )
        self.theme_menu.grid(row=0, column=1, sticky="e", padx=10, pady=5)
        self.theme_menu.set("System")

        result_frame = ctk.CTkFrame(self)
        result_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        result_frame.grid_rowconfigure(0, weight=1)
        result_frame.grid_columnconfigure(0, weight=1)

        self.create_treeview(result_frame)

        status_frame = ctk.CTkFrame(self, height=30)
        status_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(5, 10))
        
        self.status_var = ctk.StringVar(value="Ready (Using SFTP).")
        self.status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", fill="x", padx=10, expand=True)

        self.progress_bar = ctk.CTkProgressBar(status_frame, width=150)
        self.progress_bar.pack(side="right", padx=10)
        self.progress_bar.set(0)
        self.progress_bar.configure(mode="indeterminate")


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
            item_id = self.tree.identify_row(event.y)
            if not item_id:
                return

            self.tree.selection_set(item_id)
            item_data = self.tree.item(item_id)
            status = item_data['values'][0] if item_data['values'] else ""

            from tkinter import Menu
            context_menu = Menu(self.tree, tearoff=0)

            if status == "DIFFERENT":
                context_menu.add_command(label="Compare Selected File", command=self.open_diff_window)
            else:
                context_menu.add_command(label="Compare Selected File", state="disabled")

            context_menu.add_command(label="Change Attributes...", command=self.open_attributes_window)
            context_menu.tk_popup(event.x_root, event.y_root)

        except Exception as e:
            print(f"Error showing context menu: {e}")

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

    def on_clone_input(self, *args):
        """Clones input from TEST to PROD if checkbox is active."""
        if self.clone_var.get():
            self.server2_vars["user"].set(self.server1_vars["user"].get())
            self.server2_vars["pass"].set(self.server1_vars["pass"].get())
            self.server2_vars["path"].set(self.server1_vars["path"].get())

    def on_tree_select(self, event):
        """Enables Diff button only if a 'DIFFERENT' item is selected."""
        selected_items = self.tree.selection()
        if not selected_items:
            self.diff_button.configure(state="disabled")
            return
        
        item = self.tree.item(selected_items[0])
        status = item['values'][0] if item['values'] else ""
        
        if status == "DIFFERENT":
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

    def open_attributes_window(self):
        """Opens the AttributesWindow for the selected file."""
        try:
            selected_item = self.tree.selection()[0]
            item_values = self.tree.item(selected_item)['values']
        except IndexError:
            self.show_error("Error", "No valid file selected.")
            return

        s1_config = {k: v.get() for k, v in self.server1_vars.items()}
        s2_config = {k: v.get() for k, v in self.server2_vars.items()}

        attr_top_level = ctk.CTkToplevel(self)
        attr_top_level.title("Change Attributes")
        attr_top_level.geometry("500x400")
        attr_top_level.transient(self)
        AttributesWindow(attr_top_level, s1_config, s2_config, item_values, self.update_status, self.start_comparison)
        
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


class AttributesWindow(ctk.CTkFrame):
    """
    A Toplevel window for changing file owner and permissions.
    """
    def __init__(self, parent_toplevel, s1_config, s2_config, item_values, status_callback, refresh_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.s1_config, self.s2_config, self.item_values = s1_config, s2_config, item_values
        self.status_callback, self.parent_toplevel = status_callback, parent_toplevel
        self.refresh_callback = refresh_callback

        self.status, self.relative_path, self.owner_group, self.symbolic_perms, raw_octal_perms = item_values
        self.octal_perms = str(raw_octal_perms) # Ensure it's a string
        current_owner, current_group = self.owner_group.split(':', 1) if ':' in self.owner_group else (self.owner_group, "")
        
        self.owner_var = ctk.StringVar(value=current_owner.split(' -> ')[0])
        self.group_var = ctk.StringVar(value=current_group.split(' -> ')[0])
        self.new_perms = ctk.StringVar(value=self.octal_perms.split(' -> ')[0]) # Initialize with octal
        
        self.change_queue, self.user_queue, self.group_queue = queue.Queue(), queue.Queue(), queue.Queue()
        self.tasks_running, self.user_fetch_tasks, self.group_fetch_tasks = 0, 0, 0
        self.all_users, self.all_groups = set(), set()

        self.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(self, text="File:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ctk.CTkLabel(self, text=self.relative_path, wraplength=450, justify="left").grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ctk.CTkLabel(self, text="Owner:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.owner_menu = ctk.CTkOptionMenu(self, variable=self.owner_var, values=["Loading..."], state="disabled")
        self.owner_menu.grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ctk.CTkLabel(self, text="Group:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.group_menu = ctk.CTkOptionMenu(self, variable=self.group_var, values=["Loading..."], state="disabled")
        self.group_menu.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        ctk.CTkLabel(self, text=f"(Current Owner:Group: {self.owner_group})", font=ctk.CTkFont(size=10)).grid(row=3, column=1, sticky="w", padx=5)

        ctk.CTkLabel(self, text="Perms (octal)").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        ctk.CTkEntry(self, textvariable=self.new_perms).grid(row=4, column=1, sticky="ew", padx=5, pady=5)
        ctk.CTkLabel(self, text=f"(Current: {self.symbolic_perms} / {self.octal_perms})", font=ctk.CTkFont(size=10)).grid(row=5, column=1, sticky="w", padx=5)
        
        self.target_test, self.target_prod = ctk.BooleanVar(value=False), ctk.BooleanVar(value=False)
        if self.status in ["DIFFERENT", "IDENTICAL", "ONLY ON TEST"]: self.target_test.set(True)
        if self.status in ["DIFFERENT", "IDENTICAL", "ONLY ON PROD"]: self.target_prod.set(True)

        ctk.CTkLabel(self, text="Apply to:").grid(row=6, column=0, sticky="w", padx=5, pady=10)
        server_frame = ctk.CTkFrame(self, fg_color="transparent")
        server_frame.grid(row=6, column=1, sticky="ew", padx=5, pady=10)
        
        self.check_test = ctk.CTkCheckBox(server_frame, text="TEST Server", variable=self.target_test)
        self.check_test.pack(side="left", padx=(0, 20))
        self.check_prod = ctk.CTkCheckBox(server_frame, text="PROD Server", variable=self.target_prod)
        self.check_prod.pack(side="left")

        if self.status == "ONLY ON TEST": self.check_prod.configure(state="disabled")
        if self.status == "ONLY ON PROD": self.check_test.configure(state="disabled")

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
        """Starts the background task to change attributes."""
        self.apply_button.configure(state="disabled")
        self.status_callback("Applying changes...")
        self.tasks_running = 0
        owner, group, perms_str = self.owner_var.get(), self.group_var.get(), self.new_perms.get()
        
        if self.target_test.get():
            self.tasks_running += 1
            threading.Thread(target=sftp_logic.change_attributes_task, args=(self.s1_config, self.relative_path, owner, group, perms_str, self.change_queue, "TEST"), daemon=True).start()

        if self.target_prod.get():
            self.tasks_running += 1
            threading.Thread(target=sftp_logic.change_attributes_task, args=(self.s2_config, self.relative_path, owner, group, perms_str, self.change_queue, "PROD"), daemon=True).start()

        if self.tasks_running > 0: self.after(100, self.check_change_queue)
        else: self.status_callback("No servers selected."); self.stop_loading()

    def check_change_queue(self):
        """Checks the queue for updates from the attribute change thread."""
        try:
            result = self.change_queue.get_nowait()
            if isinstance(result, Exception):
                self.show_error("Error", f"An error occurred:\n{result}")
                self.tasks_running -= 1
                if self.tasks_running <= 0: self.stop_loading()
            elif isinstance(result, str):
                 self.status_callback(result)
                 self.after(100, self.check_change_queue)
            elif isinstance(result, bool) and result is True:
                self.tasks_running -= 1
                if self.tasks_running <= 0:
                    self.status_callback("Changes applied. Refreshing...")
                    self.stop_loading()
                    if self.refresh_callback:
                        self.refresh_callback()
                    self.parent_toplevel.after(1000, self.parent_toplevel.destroy) # Close after 1s
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