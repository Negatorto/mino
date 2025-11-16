import customtkinter as ctk
from app import App

if __name__ == "__main__":
    # Set global appearance and theme
    ctk.set_appearance_mode("System")  # Options: "System", "Dark", "Light"
    ctk.set_default_color_theme("blue") # Options: "blue", "green", "dark-blue"

    # Create and run the app
    app = App()
    app.mainloop()