from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fnmatch
import hashlib
import os
import posixpath
import queue
import shlex
import shutil
import stat
import time
from typing import Any, Iterator, Sequence

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore[assignment]

from models import DEFAULT_NOTE_KEYWORD, DEFAULT_SSH_PORT

try:
    import pwd
except ImportError:
    pwd = None  # type: ignore[assignment]

try:
    import grp
except ImportError:
    grp = None  # type: ignore[assignment]

FILE_CHUNK_SIZE = 65536
file_not_found_error = FileNotFoundError


def _parse_colon_id_map(content: str) -> dict[int, str]:
    """
    Parses colon-delimited UNIX files (/etc/passwd or /etc/group) into {id: name}.
    Skips comments and malformed/NIS entries individually without aborting the whole file.
    """
    mapping: dict[int, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(":")
        if len(parts) >= 3 and parts[0]:
            try:
                entry_id = int(parts[2])
            except ValueError:
                continue
            mapping[entry_id] = parts[0]
    return mapping


# Backwards-compatible aliases
_parse_passwd = _parse_colon_id_map
_parse_group = _parse_colon_id_map


def is_local_config(config: dict[str, Any]) -> bool:
    """Returns True if the target configuration points to the local filesystem."""
    host = str(config.get("host", "")).strip().lower()
    return not host or host == "local"


@dataclass(slots=True)
class LocalStatEntry:
    """Lightweight stat attribute container compatible with paramiko.SFTPAttributes fields."""

    filename: str
    st_mode: int
    st_uid: int
    st_gid: int
    st_size: int = 0
    st_mtime: float = 0.0


class LocalFS:
    """
    Wrapper around local filesystem operations mimicking paramiko.SFTPClient.
    """

    def __init__(self) -> None:
        self.getcwd = os.getcwd

    def open(self, path: str, mode: str = "r"):
        if "b" in mode:
            return open(path, mode)
        return open(path, mode, encoding="utf-8", errors="replace")

    def listdir_attr(self, path: str) -> list[LocalStatEntry]:
        attr_list: list[LocalStatEntry] = []
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    # Do not follow symlinks so broken symlinks don't crash directory scans
                    # and directory symlinks cannot trigger infinite loops (matches SFTP readdir).
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                attr_list.append(
                    LocalStatEntry(
                        filename=entry.name,
                        st_mode=st.st_mode,
                        st_uid=st.st_uid,
                        st_gid=st.st_gid,
                        st_size=st.st_size,
                        st_mtime=st.st_mtime,
                    )
                )
        return attr_list

    def stat(self, path: str):
        return os.stat(path)

    def mkdir(self, path: str) -> None:
        os.mkdir(path)

    def rmdir(self, path: str) -> None:
        os.rmdir(path)

    def remove(self, path: str) -> None:
        os.remove(path)

    def chmod(self, path: str, mode: int) -> None:
        os.chmod(path, mode)

    def chown(self, path: str, uid: int, gid: int) -> None:
        if hasattr(os, "chown"):
            os.chown(path, uid, gid)

    def putfo(self, fl, remote_path: str) -> None:
        with open(remote_path, "wb") as f_out:
            shutil.copyfileobj(fl, f_out, length=FILE_CHUNK_SIZE)

    def get(self, remote_path: str, local_path: str) -> None:
        shutil.copy2(remote_path, local_path)

    def close(self) -> None:
        pass


class DummySSH:
    """No-op SSH client placeholder for local connections."""

    def close(self) -> None:
        pass


SFTPLike = Any
SSHLike = Any


def _is_not_found_error(exc: BaseException) -> bool:
    return isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) == errno.ENOENT


def _normalize_root_path(sftp: SFTPLike, raw_path: str, server_name: str) -> str:
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        raise ValueError(f"({server_name}) Path cannot be empty.")
    if isinstance(sftp, LocalFS):
        return os.path.abspath(os.path.expanduser(cleaned))
    return posixpath.normpath(cleaned)


def _join_target_path(sftp: SFTPLike, root_path: str, relative_path: str) -> str:
    rel = relative_path.strip("/")
    if not rel:
        return root_path
    if isinstance(sftp, LocalFS):
        return os.path.normpath(os.path.join(root_path, *rel.split("/")))
    return posixpath.join(root_path, rel)


def _parent_dir(sftp: SFTPLike, full_path: str) -> str:
    return os.path.dirname(full_path) if isinstance(sftp, LocalFS) else posixpath.dirname(full_path)


def _makedirs(sftp: SFTPLike, target_dir: str, known_dirs: set[str] | None = None) -> None:
    """Recursively ensures `target_dir` exists on both LocalFS and remote SFTP."""
    if not target_dir:
        return
    if known_dirs is not None and target_dir in known_dirs:
        return

    if isinstance(sftp, LocalFS):
        os.makedirs(target_dir, exist_ok=True)
        if known_dirs is not None:
            known_dirs.add(target_dir)
        return

    norm = posixpath.normpath(target_dir)
    if known_dirs is not None and norm in known_dirs:
        return

    try:
        st = sftp.stat(norm)
        if stat.S_ISDIR(st.st_mode):
            if known_dirs is not None:
                known_dirs.add(norm)
            return
    except OSError as exc:
        if not _is_not_found_error(exc):
            raise

    is_abs = norm.startswith("/")
    parts = [p for p in norm.split("/") if p]
    current = "/" if is_abs else ""

    for part in parts:
        current = posixpath.join(current, part) if current else part
        if known_dirs is not None and current in known_dirs:
            continue
        try:
            st = sftp.stat(current)
            if not stat.S_ISDIR(st.st_mode):
                raise NotADirectoryError(f"Path exists and is not a directory: '{current}'")
        except OSError as exc:
            if _is_not_found_error(exc):
                try:
                    sftp.mkdir(current)
                except OSError:
                    # Re-check in case of race or server reporting generic IOError when existing
                    st = sftp.stat(current)
                    if not stat.S_ISDIR(st.st_mode):
                        raise
            else:
                raise
        if known_dirs is not None:
            known_dirs.add(current)


def _read_remote_text(sftp: SFTPLike, path: str) -> str:
    with sftp.open(path, "rb") as f_in:
        raw = f_in.read()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


def _get_uid_to_name_map(sftp: SFTPLike) -> dict[int, str]:
    """Returns {uid: username} for either local POSIX system or remote SFTP server."""
    if isinstance(sftp, LocalFS):
        if pwd is not None:
            try:
                return {entry.pw_uid: entry.pw_name for entry in pwd.getpwall()}
            except Exception:
                pass
        if os.path.exists("/etc/passwd"):
            try:
                return _parse_colon_id_map(_read_remote_text(sftp, "/etc/passwd"))
            except Exception:
                pass
        return {}

    return _parse_colon_id_map(_read_remote_text(sftp, "/etc/passwd"))


def _get_gid_to_name_map(sftp: SFTPLike) -> dict[int, str]:
    """Returns {gid: groupname} for either local POSIX system or remote SFTP server."""
    if isinstance(sftp, LocalFS):
        if grp is not None:
            try:
                return {entry.gr_gid: entry.gr_name for entry in grp.getgrall()}
            except Exception:
                pass
        if os.path.exists("/etc/group"):
            try:
                return _parse_colon_id_map(_read_remote_text(sftp, "/etc/group"))
            except Exception:
                pass
        return {}

    return _parse_colon_id_map(_read_remote_text(sftp, "/etc/group"))


def _get_name_to_uid_map(sftp: SFTPLike) -> dict[str, int]:
    """Returns {username: uid} safely without raising on missing /etc/passwd."""
    try:
        uid_map = _get_uid_to_name_map(sftp)
        return {name: uid for uid, name in uid_map.items()}
    except Exception as exc:
        print(f"Warning getting uid map: {exc}")
        return {}


def _get_name_to_gid_map(sftp: SFTPLike) -> dict[str, int]:
    """Returns {groupname: gid} safely without raising on missing /etc/group."""
    try:
        gid_map = _get_gid_to_name_map(sftp)
        return {name: gid for gid, name in gid_map.items()}
    except Exception as exc:
        print(f"Warning getting gid map: {exc}")
        return {}


def _parse_port(config: dict[str, Any], server_name: str) -> int:
    raw = str(config.get("port", "")).strip()
    if not raw:
        return DEFAULT_SSH_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"({server_name}) Invalid SSH port '{raw}'. Must be an integer.") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"({server_name}) SSH port {port} out of range (1-65535).")
    return port


def _connect_ssh_client(config: dict[str, Any], server_name: str, timeout: int = 10) -> Any:
    if paramiko is None:
        raise RuntimeError(
            "The 'paramiko' library is required for SFTP connections. "
            "Install dependencies via: pip install -r requirements.txt"
        )
    host = str(config.get("host", "")).strip()
    port = _parse_port(config, server_name)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        host,
        port=port,
        username=config.get("user", ""),
        password=config.get("pass", ""),
        timeout=timeout,
    )
    return ssh


def connect_to_target(
    config: dict[str, Any],
    q_out: queue.Queue[Any],
    server_name: str,
) -> tuple[SSHLike, SFTPLike]:
    """
    Connects to the target based on `config`.
    Returns `(DummySSH(), LocalFS())` when host is empty or 'local',
    otherwise `(paramiko.SSHClient, paramiko.SFTPClient)`.
    """
    if is_local_config(config):
        q_out.put(f"({server_name}) Accessing local file system...")
        return DummySSH(), LocalFS()

    host = str(config.get("host", "")).strip()
    q_out.put(f"({server_name}) Connecting to {host}...")
    ssh = _connect_ssh_client(config, server_name, timeout=10)
    try:
        sftp = ssh.open_sftp()
    except Exception:
        ssh.close()
        raise
    return ssh, sftp


@contextmanager
def target_session(
    config: dict[str, Any],
    q_out: queue.Queue[Any],
    server_name: str,
) -> Iterator[tuple[SSHLike, SFTPLike]]:
    """Context manager guaranteeing SFTP and SSH connections are always closed cleanly."""
    ssh, sftp = connect_to_target(config, q_out, server_name)
    try:
        yield ssh, sftp
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass


def _parse_ignore_patterns(ignore_patterns: str | Sequence[str] | None) -> list[str]:
    if not ignore_patterns:
        return []
    if isinstance(ignore_patterns, str):
        normalized = ignore_patterns.replace("\n", ",")
        return [p.strip() for p in normalized.split(",") if p.strip()]
    return [str(p).strip() for p in ignore_patterns if str(p).strip()]


def _is_ignored(filename: str, relative_path: str, patterns: Sequence[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(relative_path, pattern):
            return True
    return False


def _resolve_scan_keyword(scan_notes: bool | str) -> str | None:
    if not scan_notes:
        return None
    if isinstance(scan_notes, str) and scan_notes.strip():
        return scan_notes.strip()
    return DEFAULT_NOTE_KEYWORD


def _hash_and_scan_stream(file_obj, keyword: str | None) -> tuple[str, str | bool]:
    """
    Computes MD5 hash and optionally scans for `keyword` in a single streaming pass
    using constant memory (~64 KB), handling matches that span chunk boundaries.
    """
    hasher = hashlib.md5()
    keyword_bytes = keyword.encode("utf-8", errors="ignore") if keyword else b""
    check_keyword = bool(keyword_bytes)
    overlap = max(0, len(keyword_bytes) - 1)
    tail = b""
    found_keyword = False

    while True:
        chunk = file_obj.read(FILE_CHUNK_SIZE)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="ignore")
        hasher.update(chunk)

        if check_keyword and not found_keyword:
            window = tail + chunk
            if keyword_bytes in window:
                found_keyword = True
            elif overlap > 0:
                tail = window[-overlap:]

    has_note: str | bool = keyword if (found_keyword and keyword is not None) else False
    return hasher.hexdigest(), has_note


def scan_sftp_directory(
    sftp: SFTPLike,
    start_path: str,
    q_out: queue.Queue[Any],
    server_name: str,
    scan_notes: bool | str = False,
    ignore_patterns: str | Sequence[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """
    Recursively scans a local or SFTP directory, returning `(file_metadata, dir_paths)`.
    Raises an exception if `start_path` itself does not exist or is not a directory.
    """
    root_path = _normalize_root_path(sftp, start_path, server_name)

    # Validate root directory upfront so typos never produce a dangerous false-empty scan
    try:
        root_stat = sftp.stat(root_path)
    except OSError as exc:
        if _is_not_found_error(exc):
            raise FileNotFoundError(f"({server_name}) Directory not found: '{root_path}'") from exc
        raise
    if not stat.S_ISDIR(root_stat.st_mode):
        raise NotADirectoryError(f"({server_name}) Path is not a directory: '{root_path}'")

    uid_map: dict[int, str] = {}
    gid_map: dict[int, str] = {}
    try:
        q_out.put(f"({server_name}) Reading user/group maps...")
        uid_map = _get_uid_to_name_map(sftp)
        gid_map = _get_gid_to_name_map(sftp)
    except Exception as exc:
        q_out.put(f"({server_name}) Warning: Could not read user/group maps: {exc}. Using UID/GID numbers.")

    patterns_list = _parse_ignore_patterns(ignore_patterns)
    keyword = _resolve_scan_keyword(scan_notes)

    file_metadata: dict[str, dict[str, Any]] = {}
    dir_paths: set[str] = set()
    path_stack: list[tuple[str, str]] = [(root_path, "")]

    while path_stack:
        current_full_path, current_rel_path = path_stack.pop()
        display_rel = current_rel_path[:50]
        q_out.put(f"({server_name}) Scanning: .../{display_rel}")

        try:
            entries = sftp.listdir_attr(current_full_path)
        except Exception as exc:
            if not current_rel_path:
                # Root directory must be readable
                raise
            q_out.put(f"({server_name}) Directory Error {current_full_path}: {exc}")
            continue

        for item in entries:
            filename = item.filename
            if filename in (".", ".."):
                continue

            relative_path = f"{current_rel_path}/{filename}" if current_rel_path else filename
            if patterns_list and _is_ignored(filename, relative_path, patterns_list):
                continue

            full_path = _join_target_path(sftp, root_path, relative_path)
            mode = item.st_mode or 0

            if stat.S_ISDIR(mode):
                path_stack.append((full_path, relative_path))
                dir_paths.add(relative_path)
            elif stat.S_ISREG(mode):
                try:
                    with sftp.open(full_path, "rb") as f_in:
                        file_hash, has_note = _hash_and_scan_stream(f_in, keyword)

                    owner_name = uid_map.get(item.st_uid, str(item.st_uid))
                    group_name = gid_map.get(item.st_gid, str(item.st_gid))

                    file_metadata[relative_path] = {
                        "hash": file_hash,
                        "owner": owner_name,
                        "group": group_name,
                        "mode": stat.filemode(mode),
                        "octal_mode": f"{stat.S_IMODE(mode) & 0o777:03o}",
                        "has_note": has_note,
                    }
                except Exception as exc:
                    q_out.put(f"({server_name}) File Error {full_path}: {exc}")

    q_out.put(f"({server_name}) Scan complete. Found {len(file_metadata)} files and {len(dir_paths)} directories.")
    return file_metadata, sorted(dir_paths)


def compare_folders_task(
    s1_config: dict[str, Any],
    s2_config: dict[str, Any],
    q_out: queue.Queue[Any],
    scan_notes: bool | str = False,
    ignore_patterns: str | Sequence[str] | None = None,
) -> None:
    """Main background task to compare TEST (s1) and PROD (s2) folders."""
    try:
        if not str(s1_config.get("path", "")).strip() or not str(s2_config.get("path", "")).strip():
            raise ValueError("Both TEST and PRODUCTION paths must be specified.")

        with target_session(s1_config, q_out, "TEST") as (_, sftp1):
            files_s1, dirs_s1 = scan_sftp_directory(
                sftp1, s1_config["path"], q_out, "TEST", scan_notes, ignore_patterns
            )

        with target_session(s2_config, q_out, "PROD") as (_, sftp2):
            prod_root = _normalize_root_path(sftp2, s2_config["path"], "PROD")
            try:
                sftp2.stat(prod_root)
            except OSError as exc:
                if _is_not_found_error(exc):
                    q_out.put(f"(PROD) Destination path '{prod_root}' not found. Creating it...")
                    _makedirs(sftp2, prod_root)
                    q_out.put("(PROD) Created destination path.")
                else:
                    raise

            files_s2, dirs_s2 = scan_sftp_directory(
                sftp2, prod_root, q_out, "PROD", scan_notes, ignore_patterns
            )

        q_out.put("Comparing file and directory lists...")
        set_files_s1, set_files_s2 = set(files_s1.keys()), set(files_s2.keys())
        common_files = set_files_s1 & set_files_s2
        set_dirs_s1, set_dirs_s2 = set(dirs_s1), set(dirs_s2)

        results = {
            "files_s1": files_s1,
            "files_s2": files_s2,
            "only_on_1": sorted(set_files_s1 - set_files_s2),
            "only_on_2": sorted(set_files_s2 - set_files_s1),
            "different": sorted(f for f in common_files if files_s1[f]["hash"] != files_s2[f]["hash"]),
            "identical": sorted(f for f in common_files if files_s1[f]["hash"] == files_s2[f]["hash"]),
            "only_on_1_dirs": sorted(set_dirs_s1 - set_dirs_s2),
            "only_on_2_dirs": sorted(set_dirs_s2 - set_dirs_s1),
            "common_dirs": sorted(set_dirs_s1 & set_dirs_s2),
        }
        q_out.put(results)
    except Exception as exc:
        q_out.put(exc)


def download_file_task(
    config: dict[str, Any],
    relative_path: str,
    q_out: queue.Queue[Any],
    server_name: str,
) -> None:
    """Downloads a single text file from a local or SFTP target."""
    try:
        with target_session(config, q_out, server_name) as (_, sftp):
            root_path = _normalize_root_path(sftp, config["path"], server_name)
            full_path = _join_target_path(sftp, root_path, relative_path)
            q_out.put(f"({server_name}) Downloading: {relative_path}")

            with sftp.open(full_path, "rb") as f_in:
                raw_bytes = f_in.read()

            if isinstance(raw_bytes, bytes):
                try:
                    content_str = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    content_str = raw_bytes.decode("latin-1")
            else:
                content_str = str(raw_bytes)

        q_out.put({"server": server_name, "content": content_str})
    except Exception as exc:
        q_out.put(exc)


def upload_file_task(
    config: dict[str, Any],
    relative_path: str,
    content: str,
    q_out: queue.Queue[Any],
    server_name: str,
) -> None:
    """Uploads UTF-8 text content to a file on a local or SFTP target."""
    try:
        with target_session(config, q_out, server_name) as (_, sftp):
            root_path = _normalize_root_path(sftp, config["path"], server_name)
            full_path = _join_target_path(sftp, root_path, relative_path)
            q_out.put(f"({server_name}) Uploading to: {relative_path}")

            data = content.encode("utf-8")
            with sftp.open(full_path, "wb") as f_out:
                f_out.write(data)

            q_out.put(f"({server_name}) Upload successful.")

        q_out.put({"status": "upload_complete", "server": server_name, "success": True})
    except Exception as exc:
        q_out.put(exc)


def get_all_users_task(config: dict[str, Any], q_out: queue.Queue[Any], server_name: str) -> None:
    """Connects to a target and fetches all usernames."""
    try:
        with target_session(config, q_out, server_name) as (_, sftp):
            q_out.put(f"({server_name}) Fetching user list...")
            users = list(_get_uid_to_name_map(sftp).values())
        q_out.put({"server": server_name, "users": users})
    except Exception as exc:
        q_out.put(exc)


def get_all_groups_task(config: dict[str, Any], q_out: queue.Queue[Any], server_name: str) -> None:
    """Connects to a target and fetches all group names."""
    try:
        with target_session(config, q_out, server_name) as (_, sftp):
            q_out.put(f"({server_name}) Fetching group list...")
            groups = list(_get_gid_to_name_map(sftp).values())
        q_out.put({"server": server_name, "groups": groups})
    except Exception as exc:
        q_out.put(exc)


def get_users_and_groups_task(config: dict[str, Any], q_out: queue.Queue[Any], server_name: str) -> None:
    """Fetches both users and groups over a single connection."""
    try:
        with target_session(config, q_out, server_name) as (_, sftp):
            q_out.put(f"({server_name}) Fetching users and groups...")
            users = list(_get_uid_to_name_map(sftp).values())
            groups = list(_get_gid_to_name_map(sftp).values())
        q_out.put({"server": server_name, "users": users, "groups": groups})
    except Exception as exc:
        q_out.put(exc)


def _resolve_id_or_number(
    name_or_id: str,
    name_to_id: dict[str, int],
    kind: str,
    server_name: str,
) -> int:
    if name_or_id in name_to_id:
        return name_to_id[name_or_id]
    if name_or_id.isdigit():
        return int(name_or_id)
    raise ValueError(f"{kind} '{name_or_id}' not found on {server_name}.")


def _apply_attributes_to_file(
    sftp: SFTPLike,
    full_path: str,
    relative_path: str,
    owner: str,
    group: str,
    mode_int: int | None,
    name_to_uid: dict[str, int],
    name_to_gid: dict[str, int],
    q_out: queue.Queue[Any],
    label: str,
) -> None:
    q_out.put(f"({label}) Changing attributes for: {relative_path}")

    if owner or group:
        current_stat = sftp.stat(full_path)
        uid, gid = current_stat.st_uid, current_stat.st_gid
        if owner:
            uid = _resolve_id_or_number(owner, name_to_uid, "Owner", label)
        if group:
            gid = _resolve_id_or_number(group, name_to_gid, "Group", label)

        # Avoid unnecessary chown syscall if file already has the requested UID and GID
        if (uid, gid) != (current_stat.st_uid, current_stat.st_gid):
            sftp.chown(full_path, uid, gid)

    if mode_int is not None:
        sftp.chmod(full_path, mode_int)


def _parse_octal_permissions(perms_str: str) -> int | None:
    cleaned = str(perms_str or "").strip()
    if not cleaned:
        return None
    try:
        mode = int(cleaned, 8)
    except ValueError as exc:
        raise ValueError(
            f"Invalid permissions format: '{cleaned}'. Use an octal string (e.g., '755' or '0644')."
        ) from exc
    if not (0 <= mode <= 0o7777):
        raise ValueError(f"Permissions out of range: '{cleaned}'. Expected 0000 to 7777.")
    return mode


def change_attributes_batch_task(
    config: dict[str, Any],
    relative_paths: Sequence[str],
    owner: str,
    group: str,
    perms_str: str,
    q_out: queue.Queue[Any],
    server_name: str,
) -> None:
    """
    Applies owner, group, and/or octal permission changes to multiple files on a target
    using a single connection and a single user/group lookup.
    """
    try:
        mode_int = _parse_octal_permissions(perms_str)
        with target_session(config, q_out, server_name) as (_, sftp):
            root_path = _normalize_root_path(sftp, config["path"], server_name)
            name_to_uid = _get_name_to_uid_map(sftp) if owner else {}
            name_to_gid = _get_name_to_gid_map(sftp) if group else {}

            for rel_path in relative_paths:
                file_label = f"{server_name}:{rel_path}"
                try:
                    full_path = _join_target_path(sftp, root_path, rel_path)
                    _apply_attributes_to_file(
                        sftp,
                        full_path,
                        rel_path,
                        owner,
                        group,
                        mode_int,
                        name_to_uid,
                        name_to_gid,
                        q_out,
                        server_name,
                    )
                    q_out.put(f"{file_label}:Success")
                except Exception as exc:
                    q_out.put(f"Error:{file_label}:{exc}")
    except Exception as exc:
        for rel_path in relative_paths:
            q_out.put(f"Error:{server_name}:{rel_path}:{exc}")


def change_attributes_task(
    config: dict[str, Any],
    relative_path: str,
    owner: str,
    group: str,
    perms_str: str,
    q_out: queue.Queue[Any],
    server_name: str,
) -> None:
    """Changes owner and/or permissions of a single file (compatible wrapper)."""
    try:
        mode_int = _parse_octal_permissions(perms_str)
        with target_session(config, q_out, server_name) as (_, sftp):
            root_path = _normalize_root_path(sftp, config["path"], server_name)
            full_path = _join_target_path(sftp, root_path, relative_path)
            name_to_uid = _get_name_to_uid_map(sftp) if owner else {}
            name_to_gid = _get_name_to_gid_map(sftp) if group else {}
            _apply_attributes_to_file(
                sftp,
                full_path,
                relative_path,
                owner,
                group,
                mode_int,
                name_to_uid,
                name_to_gid,
                q_out,
                server_name,
            )
        q_out.put(f"{server_name}:Success")
    except Exception as exc:
        q_out.put(f"Error:{server_name}:{exc}")


def backup_folder_remote_task(
    config: dict[str, Any],
    q_out: queue.Queue[Any],
    server_name: str,
) -> None:
    """
    Creates a sibling backup of a directory on the target itself
    (e.g., `/path/to/folder` -> `/path/to/folder-backup-YYYYMMDD-HHMMSS`).
    """
    ssh: Any = None
    try:
        timestamp = time.strftime("%Y%m%d-%H%M%S")

        if is_local_config(config):
            source_path = os.path.abspath(os.path.expanduser(str(config.get("path", "")).strip()))
            if not source_path or source_path == os.path.sep:
                raise ValueError(f"({server_name}) Cannot create sibling backup of root path '{source_path}'.")
            backup_path = f"{source_path.rstrip(os.path.sep)}-backup-{timestamp}"
            q_out.put(f"({server_name}) Local backup: '{source_path}' -> '{backup_path}'")
            shutil.copytree(source_path, backup_path)
            q_out.put(f"({server_name}) Local backup completed successfully to {backup_path}")
            q_out.put({"status": "backup_complete", "success": True, "path": backup_path})
            return

        raw_remote = posixpath.normpath(str(config.get("path", "")).strip())
        if not raw_remote or raw_remote == "/":
            raise ValueError(f"({server_name}) Cannot create sibling backup of root path '/'.")
        source_path = raw_remote.rstrip("/")
        backup_path = f"{source_path}-backup-{timestamp}"

        host = str(config.get("host", "")).strip()
        q_out.put(f"({server_name}) Connecting to {host} for remote backup...")
        ssh = _connect_ssh_client(config, server_name, timeout=20)

        cmd = f"cp -rp {shlex.quote(source_path)} {shlex.quote(backup_path)}"
        q_out.put(f"({server_name}) Starting remote backup: {cmd}")
        _, stdout, stderr = ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            error_message = stderr.read().decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"Remote backup failed with exit status {exit_status}: {error_message}")

        q_out.put(f"({server_name}) Remote backup completed successfully to {backup_path}")
        q_out.put({"status": "backup_complete", "success": True, "path": backup_path})
    except Exception as exc:
        q_out.put(exc)
    finally:
        if ssh is not None:
            try:
                ssh.close()
            except Exception:
                pass


def backup_folder_local_task(
    config: dict[str, Any],
    local_base_path: str,
    q_out: queue.Queue[Any],
    server_name: str,
) -> None:
    """Recursively downloads a local or SFTP directory to `local_base_path`."""
    try:
        with target_session(config, q_out, server_name) as (_, sftp):
            remote_root = _normalize_root_path(sftp, config["path"], server_name)
            if isinstance(sftp, LocalFS):
                folder_name = os.path.basename(remote_root.rstrip(os.path.sep)) or "root"
            else:
                folder_name = posixpath.basename(remote_root.rstrip("/")) or "root"

            timestamp = time.strftime("%Y%m%d-%H%M%S")
            local_dest_path = os.path.abspath(
                os.path.join(os.path.expanduser(local_base_path), f"{folder_name}-backup-{timestamp}")
            )
            os.makedirs(local_dest_path, exist_ok=True)
            q_out.put(f"({server_name}) Starting local backup to {local_dest_path}...")

            path_stack: list[tuple[str, str]] = [(remote_root, "")]
            while path_stack:
                current_remote_path, current_rel_path = path_stack.pop(0)
                for item in sftp.listdir_attr(current_remote_path):
                    filename = item.filename
                    if filename in (".", ".."):
                        continue

                    relative_path = f"{current_rel_path}/{filename}" if current_rel_path else filename
                    remote_full_path = _join_target_path(sftp, remote_root, relative_path)

                    # Guard against self-recursion if backing up a local folder inside itself
                    if isinstance(sftp, LocalFS) and os.path.abspath(remote_full_path) == local_dest_path:
                        continue

                    local_full_path = os.path.join(local_dest_path, *relative_path.split("/"))
                    mode = item.st_mode or 0

                    if stat.S_ISDIR(mode):
                        os.makedirs(local_full_path, exist_ok=True)
                        path_stack.append((remote_full_path, relative_path))
                    elif stat.S_ISREG(mode):
                        q_out.put(f"({server_name}) Downloading: {relative_path[:60]}...")
                        sftp.get(remote_full_path, local_full_path)

        q_out.put(f"({server_name}) Local backup completed successfully to {local_dest_path}")
        q_out.put({"status": "backup_complete", "success": True, "path": local_dest_path})
    except Exception as exc:
        q_out.put(exc)


def _copy_file_with_metadata(
    src_sftp: SFTPLike,
    dst_sftp: SFTPLike,
    src_root: str,
    dst_root: str,
    relative_path: str,
    *,
    src_meta: dict[str, Any] | None = None,
    dst_uid_map: dict[str, int] | None = None,
    dst_gid_map: dict[str, int] | None = None,
    known_dst_dirs: set[str] | None = None,
    q_out: queue.Queue[Any] | None = None,
) -> None:
    """Copies a single file from source to destination, creating parent dirs and syncing metadata."""
    src_full_path = _join_target_path(src_sftp, src_root, relative_path)
    dst_full_path = _join_target_path(dst_sftp, dst_root, relative_path)

    parent_dir = _parent_dir(dst_sftp, dst_full_path)
    _makedirs(dst_sftp, parent_dir, known_dirs=known_dst_dirs)

    with src_sftp.open(src_full_path, "rb") as f_src:
        dst_sftp.putfo(f_src, dst_full_path)

    # 1. Sync ownership when user/group names exist on destination (ignore PermissionError safely)
    if src_meta and dst_uid_map is not None and dst_gid_map is not None:
        owner = src_meta.get("owner")
        group = src_meta.get("group")
        uid = dst_uid_map.get(owner) if owner else None
        gid = dst_gid_map.get(group) if group else None
        if uid is not None and gid is not None:
            try:
                dst_sftp.chown(dst_full_path, uid, gid)
            except OSError:
                pass

    # 2. Sync permissions independently so unprivileged chown never blocks chmod
    try:
        if src_meta and src_meta.get("octal_mode"):
            mode = int(str(src_meta["octal_mode"]), 8)
        else:
            src_stat = src_sftp.stat(src_full_path)
            mode = stat.S_IMODE(src_stat.st_mode)
        dst_sftp.chmod(dst_full_path, mode)
    except Exception as exc:
        if q_out is not None:
            q_out.put(f"Warning: Could not sync permissions for {relative_path}: {exc}")


def sync_folders_task(
    s1_config: dict[str, Any],
    s2_config: dict[str, Any],
    comparison_results: dict[str, Any],
    delete_on_prod: bool,
    q_out: queue.Queue[Any],
    source_name: str = "TEST",
    dest_name: str = "PROD",
) -> None:
    """
    Synchronizes files from source (`s1_config`) to destination (`s2_config`).
    Supports both TEST -> PROD and PROD -> TEST directions cleanly.
    """
    try:
        q_out.put(f"Connecting to servers for synchronization ({source_name} -> {dest_name})...")
        with (
            target_session(s1_config, q_out, source_name) as (_, sftp_src),
            target_session(s2_config, q_out, dest_name) as (_, sftp_dst),
        ):
            src_root = _normalize_root_path(sftp_src, s1_config["path"], source_name)
            dst_root = _normalize_root_path(sftp_dst, s2_config["path"], dest_name)

            # 1. Handle Deletions on Destination
            if delete_on_prod:
                files_to_delete = sorted(comparison_results.get("only_on_2", []), key=len, reverse=True)
                dirs_to_delete = sorted(comparison_results.get("only_on_2_dirs", []), key=len, reverse=True)

                if files_to_delete:
                    q_out.put(f"Deleting {len(files_to_delete)} files from {dest_name}...")
                    for idx, relative_path in enumerate(files_to_delete, start=1):
                        dst_full_path = _join_target_path(sftp_dst, dst_root, relative_path)
                        try:
                            q_out.put(f"({idx}/{len(files_to_delete)}) Deleting file: {relative_path}")
                            sftp_dst.remove(dst_full_path)
                        except Exception as exc:
                            q_out.put(f"Warning: Could not delete file {relative_path} from {dest_name}: {exc}")

                if dirs_to_delete:
                    q_out.put(f"Deleting {len(dirs_to_delete)} directories from {dest_name}...")
                    for idx, relative_path in enumerate(dirs_to_delete, start=1):
                        dst_full_path = _join_target_path(sftp_dst, dst_root, relative_path)
                        try:
                            q_out.put(f"({idx}/{len(dirs_to_delete)}) Deleting dir: {relative_path}")
                            sftp_dst.rmdir(dst_full_path)
                        except Exception as exc:
                            q_out.put(f"Warning: Could not delete dir {relative_path} from {dest_name}: {exc}")

            # 2. Ensure Destination Root and Directories Exist
            known_dst_dirs: set[str] = set()
            _makedirs(sftp_dst, dst_root, known_dirs=known_dst_dirs)

            dirs_to_create = sorted(comparison_results.get("only_on_1_dirs", []), key=len)
            if dirs_to_create:
                q_out.put(f"Creating {len(dirs_to_create)} directories on {dest_name}...")
                for idx, relative_path in enumerate(dirs_to_create, start=1):
                    dst_full_path = _join_target_path(sftp_dst, dst_root, relative_path)
                    try:
                        q_out.put(f"({idx}/{len(dirs_to_create)}) Creating dir: {relative_path}")
                        _makedirs(sftp_dst, dst_full_path, known_dirs=known_dst_dirs)
                    except Exception as exc:
                        q_out.put(f"Warning: Could not create dir {dst_full_path}: {exc}")

            # 3. Copy / Overwrite Files
            files_to_copy = list(comparison_results.get("only_on_1", [])) + list(
                comparison_results.get("different", [])
            )
            q_out.put(f"Copying/overwriting {len(files_to_copy)} files ({source_name} -> {dest_name})...")
            dst_uid_map = _get_name_to_uid_map(sftp_dst)
            dst_gid_map = _get_name_to_gid_map(sftp_dst)
            src_files_meta = comparison_results.get("files_s1", {})

            for idx, relative_path in enumerate(files_to_copy, start=1):
                try:
                    q_out.put(f"({idx}/{len(files_to_copy)}) Syncing: {relative_path[:60]}")
                    _copy_file_with_metadata(
                        sftp_src,
                        sftp_dst,
                        src_root,
                        dst_root,
                        relative_path,
                        src_meta=src_files_meta.get(relative_path),
                        dst_uid_map=dst_uid_map,
                        dst_gid_map=dst_gid_map,
                        known_dst_dirs=known_dst_dirs,
                        q_out=q_out,
                    )
                except Exception as exc:
                    q_out.put(f"Warning: Could not sync {relative_path}: {exc}")

        q_out.put({"status": "sync_complete", "success": True})
    except Exception as exc:
        q_out.put(exc)


def sync_multiple_files_task(
    s1_config: dict[str, Any],
    s2_config: dict[str, Any],
    relative_paths_list: Sequence[str],
    q_out: queue.Queue[Any],
    source_name: str = "TEST",
    dest_name: str = "PROD",
) -> None:
    """Synchronizes one or more files from source (`s1_config`) to destination (`s2_config`)."""
    try:
        total = len(relative_paths_list)
        q_out.put(f"Connecting to servers to sync {total} file(s) ({source_name} -> {dest_name})...")

        with (
            target_session(s1_config, q_out, source_name) as (_, sftp_src),
            target_session(s2_config, q_out, dest_name) as (_, sftp_dst),
        ):
            src_root = _normalize_root_path(sftp_src, s1_config["path"], source_name)
            dst_root = _normalize_root_path(sftp_dst, s2_config["path"], dest_name)
            known_dst_dirs: set[str] = set()

            for idx, relative_path in enumerate(relative_paths_list, start=1):
                try:
                    q_out.put(f"({idx}/{total}) Syncing: {relative_path}")
                    _copy_file_with_metadata(
                        sftp_src,
                        sftp_dst,
                        src_root,
                        dst_root,
                        relative_path,
                        known_dst_dirs=known_dst_dirs,
                        q_out=q_out,
                    )
                    q_out.put({"status": "single_sync_complete", "success": True, "file": relative_path})
                except Exception as exc:
                    q_out.put(f"Error syncing {relative_path}: {exc}")

        q_out.put("Batch sync complete.")
        q_out.put({"status": "batch_sync_complete", "success": True})
    except Exception as exc:
        q_out.put(exc)


def sync_single_file_task(
    s1_config: dict[str, Any],
    s2_config: dict[str, Any],
    relative_path: str,
    q_out: queue.Queue[Any],
    source_name: str = "TEST",
    dest_name: str = "PROD",
) -> None:
    """Synchronizes a single file from source (`s1_config`) to destination (`s2_config`)."""
    try:
        q_out.put(f"Connecting to servers to sync {relative_path} ({source_name} -> {dest_name})...")
        with (
            target_session(s1_config, q_out, source_name) as (_, sftp_src),
            target_session(s2_config, q_out, dest_name) as (_, sftp_dst),
        ):
            src_root = _normalize_root_path(sftp_src, s1_config["path"], source_name)
            dst_root = _normalize_root_path(sftp_dst, s2_config["path"], dest_name)
            q_out.put(f"Syncing file: {relative_path}...")
            _copy_file_with_metadata(
                sftp_src,
                sftp_dst,
                src_root,
                dst_root,
                relative_path,
                q_out=q_out,
            )

        q_out.put(f"Successfully synced {relative_path} to {dest_name}.")
        q_out.put({"status": "single_sync_complete", "success": True, "file": relative_path})
    except Exception as exc:
        q_out.put(exc)
