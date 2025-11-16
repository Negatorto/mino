# MINO: Mirroring Integrity Network Operations

MINO (Mirroring Integrity Network Operations) is a Python-based GUI utility designed to simplify the management and synchronization of files between two remote SFTP servers, typically a testing (TEST) and a production (PROD) environment.

It allows for a clear and interactive way to compare directory structures, view file differences, and synchronize content, ensuring that the production environment accurately mirrors the testing environment.

## Core Features

- **SFTP Connectivity:** Securely connect to two different SFTP servers.
- **Directory Comparison:** Recursively scans and compares directories on both servers, identifying files that are identical, different, or unique to one server. Comparison is based on MD5 hash, permissions, and ownership.
- **Side-by-Side Diff:** Provides a built-in diff viewer to visually inspect content differences between two versions of a file.
- **One-Way Synchronization:** Synchronize the PROD server to match the TEST server. This includes:
  - Copying new and modified files from TEST to PROD.
  - Deleting files from PROD that no longer exist on TEST.
- **Attribute Management:** Change file/directory owner, group, and permissions directly from the UI.
- **Backup Functionality:** Offers options to back up the PROD server (either remotely or locally) before performing a sync operation.
- **Theming:** Supports light, dark, and system-native themes.
