"""
Compatibility wrapper.
The application modules are organized into:
- core/ : Data models and SFTP/LocalFS logic
- ui/   : CustomTkinter interface components
"""
from ui.app import App, main

if __name__ == "__main__":
    main()
