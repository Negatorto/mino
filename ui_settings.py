import customtkinter as ctk

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


class NoteKeywordWindow(ctk.CTkFrame):
    """
    A Toplevel window for setting the note keyword.
    """
    def __init__(self, parent_toplevel, current_keyword_var, on_save_callback):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.current_keyword_var = current_keyword_var
        self.on_save_callback = on_save_callback
        self.parent_toplevel = parent_toplevel

        ctk.CTkLabel(self, text="Enter keyword to scan for:", font=ctk.CTkFont(weight="bold")).pack(pady=(0, 10))

        self.entry_var = ctk.StringVar(value=current_keyword_var.get())
        self.entry = ctk.CTkEntry(self, textvariable=self.entry_var, width=200)
        self.entry.pack(pady=5)

        # Focus the entry
        self.entry.focus_set()

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=(20, 0))

        ctk.CTkButton(button_frame, text="Save", command=self.save).pack(side="left", padx=10)
        ctk.CTkButton(button_frame, text="Cancel", command=parent_toplevel.destroy, fg_color="gray").pack(side="left", padx=10)

    def save(self):
        new_keyword = self.entry_var.get()
        if new_keyword:
            self.current_keyword_var.set(new_keyword)
            if self.on_save_callback:
                self.on_save_callback(new_keyword)
            self.parent_toplevel.destroy()


class IgnorePatternsWindow(ctk.CTkFrame):
    """
    A Toplevel window for setting the ignore patterns.
    """
    def __init__(self, parent_toplevel, current_patterns_var):
        super().__init__(parent_toplevel)
        self.pack(fill="both", expand=True, padx=10, pady=10)

        self.current_patterns_var = current_patterns_var
        self.parent_toplevel = parent_toplevel

        ctk.CTkLabel(self, text="Enter patterns to ignore (one per line or comma-separated):", font=ctk.CTkFont(weight="bold"), wraplength=380).pack(pady=(10, 10))
        ctk.CTkLabel(self, text="Examples: *.pyc, .git, node_modules, .env", font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(0, 10))

        self.text_area = ctk.CTkTextbox(self, width=270, height=135)
        self.text_area.pack(pady=5, padx=20, fill="both", expand=True)

        # Load current values
        current_val = current_patterns_var.get()
        # Display as one per line for editing
        formatted_val = current_val.replace(',', '\n')
        self.text_area.insert("1.0", formatted_val)

        self.text_area.focus_set()

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=(20, 20))

        ctk.CTkButton(button_frame, text="Save", command=self.save).pack(side="left", padx=10)
        ctk.CTkButton(button_frame, text="Cancel", command=parent_toplevel.destroy, fg_color="gray").pack(side="left", padx=10)

    def save(self):
        # Get text, split by newlines or commas, filter empty
        raw_text = self.text_area.get("1.0", "end-1c")
        # Normalize: replace newlines with commas
        normalized = raw_text.replace('\n', ',')
        # Split, strip, filtering empty
        items = [x.strip() for x in normalized.split(',') if x.strip()]
        # Join back with commas
        final_val = ",".join(items)

        self.current_patterns_var.set(final_val)
        self.parent_toplevel.destroy()
