import customtkinter as ctk
from tkinter import messagebox
import threading
import queue
import difflib
import sftp_logic

class DiffWindow(ctk.CTkFrame):
    """
    A new Toplevel window for side-by-side file comparison.
    """
    def __init__(self, parent_toplevel, s1_config, s2_config, item_values):
        super().__init__(parent_toplevel, fg_color="transparent")
        self.pack(fill="both", expand=True)

        self.s1_config, self.s2_config = s1_config, s2_config

        # Unpack based on length (Retroactive fix for Note column)
        if len(item_values) == 6:
            self.status, self.relative_path, self.note, self.owner_group, self.symbolic_perms, self.octal_perms = item_values
        else:
            self.status, self.relative_path, self.owner_group, self.symbolic_perms, self.octal_perms = item_values
            self.note = ""

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

        self.toggle_metadata_visibility()  # Set initial state

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
        self.text1.configure(state="normal")
        self.text1.delete("1.0", "end")
        self.text1.configure(state="disabled")

        self.text2.configure(state="normal")
        self.text2.delete("1.0", "end")
        self.text2.configure(state="disabled")

        threading.Thread(target=sftp_logic.download_file_task, args=(self.s1_config, self.relative_path, self.diff_queue, "TEST"), daemon=True).start()
        threading.Thread(target=sftp_logic.download_file_task, args=(self.s2_config, self.relative_path, self.diff_queue, "PROD"), daemon=True).start()
        self.after(100, self.check_diff_queue)

    def create_text_pane(self, parent, title, owner_info, perms_info):
        frame = ctk.CTkFrame(parent, border_width=1)
        frame.grid_rowconfigure(2, weight=1)  # Textbox row
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 0))

        meta_text = f"Owner: {owner_info}  |  Perms: {perms_info}"
        meta_label = ctk.CTkLabel(frame, text=meta_text, font=ctk.CTkFont(size=11), text_color="gray")
        meta_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 5))

        line_numbers = ctk.CTkTextbox(frame, width=45, wrap="none", font=("monospace", 10), state="disabled", border_width=0, corner_radius=0)
        line_numbers.grid(row=2, column=0, sticky="ns")
        line_numbers.grid_forget()  # Hidden by default

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
        else:  # Light
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
        else:  # Light
            self.text1.tag_config('removed', background='#FFC0C0', foreground="#8B0000")
            self.text1.tag_config('blank', background='#FAFAFA')
            self.text1.tag_config('highlight_inline', background='#FFFF99', foreground="#4D4D00")
            self.text2.tag_config('added', background='#C0FFC0', foreground="#004D00")
            self.text2.tag_config('blank', background='#FAFAFA')
            self.text2.tag_config('highlight_inline', background='#FFFF99', foreground="#4D4D00")
        self.lines1 = self.configure_line_number_tags(self.lines1)
        self.lines2 = self.configure_line_number_tags(self.lines2)

    def on_main_scroll(self, *args):
        self.text1.yview(*args)
        self.text2.yview(*args)
        self.lines1.yview(*args)
        self.lines2.yview(*args)

    def on_text_scroll_1(self, first, last):
        self.main_scrollbar.set(first, last)
        self.text2.yview_moveto(first)
        self.lines1.yview_moveto(first)
        self.lines2.yview_moveto(first)

    def on_text_scroll_2(self, first, last):
        self.main_scrollbar.set(first, last)
        self.text1.yview_moveto(first)
        self.lines1.yview_moveto(first)
        self.lines2.yview_moveto(first)

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
        if not self.diff_result:
            return
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
                self.text1.insert("end", line[2:] + '\n')
                self.text2.insert("end", line[2:] + '\n')
                self.lines1.insert("end", f"{line_num_1}\n", 'line')
                self.lines2.insert("end", f"{line_num_2}\n", 'line')
                line_num_1 += 1
                line_num_2 += 1
                i += 1
            elif (highlight_on and line.startswith('- ') and (i + 1 < len(self.diff_result)) and self.diff_result[i + 1].startswith('+ ')):
                line1_text, line2_text = line[2:], self.diff_result[i + 1][2:]
                self.text1.insert("end", line1_text + '\n', 'removed')
                self.text2.insert("end", line2_text + '\n', 'added')
                self.lines1.insert("end", f"{line_num_1}\n", 'line')
                self.lines2.insert("end", f"{line_num_2}\n", 'line')
                self.apply_inline_tags(line1_text, line2_text)
                line_num_1 += 1
                line_num_2 += 1
                i += 2
            elif line.startswith('- '):
                self.text1.insert("end", line[2:] + '\n', 'removed')
                self.text2.insert("end", '\n', 'blank')
                self.lines1.insert("end", f"{line_num_1}\n", 'line')
                self.lines2.insert("end", '\n', 'blank')
                line_num_1 += 1
                i += 1
            elif line.startswith('+ '):
                self.text1.insert("end", '\n', 'blank')
                self.text2.insert("end", line[2:] + '\n', 'added')
                self.lines1.insert("end", '\n', 'blank')
                self.lines2.insert("end", f"{line_num_2}\n", 'line')
                line_num_2 += 1
                i += 1
            else:
                i += 1
        for widget in [self.text1, self.text2, self.lines1, self.lines2]:
            widget.configure(state="disabled")

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


class EditorWindow(ctk.CTkFrame):
    """
    A Toplevel window for editing a file.
    """
    def __init__(self, parent_toplevel, config, relative_path, initial_content, server_name, on_save_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True)
        self.config = config
        self.relative_path = relative_path
        self.server_name = server_name
        self.on_save_callback = on_save_callback
        self.parent_toplevel = parent_toplevel

        self.queue = queue.Queue()

        self.text_area = ctk.CTkTextbox(self, wrap="none", font=("monospace", 12))
        self.text_area.pack(fill="both", expand=True, padx=10, pady=10)
        self.text_area.insert("1.0", initial_content)

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=10, pady=10)

        self.save_btn = ctk.CTkButton(button_frame, text=f"Save to {server_name}", command=self.save_file)
        self.save_btn.pack(side="right", padx=5)

        self.cancel_btn = ctk.CTkButton(button_frame, text="Cancel", fg_color="gray", command=parent_toplevel.destroy)
        self.cancel_btn.pack(side="right", padx=5)

        self.status_lbl = ctk.CTkLabel(button_frame, text="", text_color="gray")
        self.status_lbl.pack(side="left", padx=5)

    def save_file(self):
        content = self.text_area.get("1.0", "end-1c") # Get all content except trailing newline
        if not messagebox.askyesno("Confirm Save", f"Overwrite {self.relative_path} on {self.server_name}?", parent=self.parent_toplevel):
            return

        self.save_btn.configure(state="disabled")
        self.status_lbl.configure(text="Uploading...")

        threading.Thread(target=sftp_logic.upload_file_task, args=(self.config, self.relative_path, content, self.queue, self.server_name), daemon=True).start()
        self.after(100, self.check_save_queue)

    def check_save_queue(self):
        try:
            result = self.queue.get_nowait()
            if isinstance(result, Exception):
                messagebox.showerror("Upload Error", f"Failed to upload:\n{result}", parent=self.parent_toplevel)
                self.save_btn.configure(state="normal")
                self.status_lbl.configure(text="Error.")
            elif isinstance(result, str) and result == "Upload complete":
                messagebox.showinfo("Success", "File saved successfully.", parent=self.parent_toplevel)
                self.save_btn.configure(state="normal")
                self.status_lbl.configure(text="Saved.")
                if self.on_save_callback:
                    self.on_save_callback() # Refresh diff
                self.parent_toplevel.destroy()
            else:
                self.after(100, self.check_save_queue)
        except queue.Empty:
            self.after(100, self.check_save_queue)
