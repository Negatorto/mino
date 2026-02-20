import paramiko
import stat
import hashlib
import io
import difflib
import time
import os
import shutil
import fnmatch


# Helper to check for file not found across different paramiko versions/servers
try:
    file_not_found_error = FileNotFoundError
except NameError:
    file_not_found_error = IOError


def _parse_passwd(content):
    """Converts the content of /etc/passwd into a {uid: username} dictionary."""
    mapping = {}
    try:
        for line in content.splitlines():
            if not line or line.startswith('#'):
                continue
            parts = line.split(':')
            if len(parts) >= 3:
                username = parts[0]
                uid = int(parts[2])
                mapping[uid] = username
    except Exception as e:
        print(f"Error parsing passwd: {e}")
    return mapping


def _parse_group(content):
    """Converts the content of /etc/group into a {gid: groupname} dictionary."""
    mapping = {}
    try:
        for line in content.splitlines():
            if not line or line.startswith('#'):
                continue
            parts = line.split(':')
            if len(parts) >= 3:
                groupname = parts[0]
                gid = int(parts[2])
                mapping[gid] = groupname
    except Exception as e:
        print(f"Error parsing group: {e}")
    return mapping


class LocalFS:
    """
    A wrapper around local file system operations to mimic paramiko.SFTPClient.
    """
    def __init__(self):
        self.getcwd = os.getcwd

    def open(self, path, mode='r'):
        return open(path, mode)

    def listdir_attr(self, path):
        attr_list = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    attr = paramiko.SFTPAttributes.from_stat(entry.stat())
                    attr.filename = entry.name
                    attr_list.append(attr)
        except OSError as e:
            raise e
        return attr_list

    def stat(self, path):
        return os.stat(path)

    def mkdir(self, path):
        os.mkdir(path)

    def rmdir(self, path):
        os.rmdir(path)

    def remove(self, path):
        os.remove(path)

    def chmod(self, path, mode):
        os.chmod(path, mode)

    def chown(self, path, uid, gid):
        try:
            os.chown(path, uid, gid)
        except AttributeError:
            pass  # Not available on Windows
        except PermissionError:
            pass  # Might not have permission

    def putfo(self, fl, remote_path):
        with open(remote_path, 'wb') as f:
            shutil.copyfileobj(fl, f)

    def close(self):
        pass


class DummySSH:
    """Dummy SSH client for local connections."""
    def close(self):
        pass


def _get_name_to_uid_map(sftp):
    """
    Returns a dictionary mapping {username: uid} from the SFTP server.
    """
    try:
        if isinstance(sftp, LocalFS):
            return {}

        with sftp.open('/etc/passwd', 'r') as f:
            content = f.read().decode('utf-8', errors='ignore')
            uid_to_name = _parse_passwd(content)
            return {v: k for k, v in uid_to_name.items()}
    except Exception as e:
        print(f"Warning getting uid map: {e}")
        return {}


def _get_name_to_gid_map(sftp):
    """
    Returns a dictionary mapping {groupname: gid} from the SFTP server.
    """
    try:
        if isinstance(sftp, LocalFS):
            return {}

        with sftp.open('/etc/group', 'r') as f:
            content = f.read().decode('utf-8', errors='ignore')
            gid_to_name = _parse_group(content)
            return {v: k for k, v in gid_to_name.items()}
    except Exception as e:
        print(f"Warning getting gid map: {e}")
        return {}


def connect_to_target(config, q_out, server_name):
    """
    Connects to the target based on config.
    If host is 'local', returns (DummySSH(), LocalFS()).
    Else returns (paramiko.SSHClient, paramiko.SFTPClient).
    """
    if config['host'].lower() == 'local' or not config['host'].strip():
        q_out.put(f"({server_name}) Accessing local file system...")
        return DummySSH(), LocalFS()

    q_out.put(f"({server_name}) Connecting to {config['host']}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
    sftp = ssh.open_sftp()
    return ssh, sftp


def scan_sftp_directory(sftp, start_path, q_out, server_name, scan_notes=False, ignore_patterns=None):
    """
    Recursively scans an SFTP path, returning metadata for files and a list of directories.
    """
    try:
        if not isinstance(sftp, LocalFS):
            q_out.put(f"({server_name}) Reading user/group maps...")
            with sftp.open('/etc/passwd', 'r') as f:
                uid_map = _parse_passwd(f.read().decode('utf-8', errors='ignore'))
            with sftp.open('/etc/group', 'r') as f:
                gid_map = _parse_group(f.read().decode('utf-8', errors='ignore'))
        else:
            q_out.put(f"({server_name}) Using local users/groups.")
            uid_map = {}
            gid_map = {}
    except Exception as e:
        q_out.put(f"({server_name}) Warning: Could not read user/group maps: {e}. Using UID/GID numbers.")


    file_metadata = {}
    dir_paths = set()
    path_stack = [start_path]

    while path_stack:
        current_path = path_stack.pop()
        if not current_path.startswith(start_path):
            q_out.put(f"({server_name}) Error: Path {current_path} is outside of {start_path}")
            continue

        rel_path_display = current_path[len(start_path):][:50]
        q_out.put(f"({server_name}) Scanning: .../{rel_path_display}")

        # Prepare ignore patterns
        patterns_list = []
        if ignore_patterns:
            # If it's a string, split it. If list, use as is.
            if isinstance(ignore_patterns, str):
                patterns_list = [p.strip() for p in ignore_patterns.split(',') if p.strip()]
            elif isinstance(ignore_patterns, list):
                patterns_list = ignore_patterns

        try:
            for item in sftp.listdir_attr(current_path):
                # Check ignores
                ignored = False
                for pattern in patterns_list:
                    if fnmatch.fnmatch(item.filename, pattern):
                        ignored = True
                        break
                if ignored:
                    # q_out.put(f"({server_name}) Ignoring {item.filename}")
                    continue

                full_path = f"{current_path.rstrip('/')}/{item.filename.lstrip('/')}"
                relative_path = full_path[len(start_path):].lstrip('/')

                if stat.S_ISDIR(item.st_mode):
                    # Ignore '.' and '..' directories
                    if item.filename in ['.', '..']:
                        continue
                    path_stack.append(full_path)
                    if relative_path:  # Don't add the root path itself
                        dir_paths.add(relative_path)
                elif stat.S_ISREG(item.st_mode):
                    try:
                        mem_file = io.BytesIO()
                        with sftp.open(full_path, 'rb') as f:
                            mem_file.write(f.read())
                        mem_file.seek(0)

                        file_hash = hashlib.md5(mem_file.read()).hexdigest()

                        # --- Note Scanning ---
                        has_note = False
                        if scan_notes:
                            # scan_notes is now the keyword string if truthy, or just True/False
                            keyword = "MINO"
                            if isinstance(scan_notes, str) and scan_notes.strip():
                                keyword = scan_notes.strip()

                            mem_file.seek(0)
                            # Simple optimization: read first chunk or read all?
                            # For now read all like before to be safe
                            content = mem_file.read()

                            keyword_bytes = keyword.encode('utf-8', errors='ignore')
                            if keyword_bytes in content:
                                # Find the line containing the keyword
                                try:
                                    content_str = content.decode('utf-8', errors='ignore')
                                    for line in content_str.splitlines():
                                        if keyword in line:
                                            has_note = keyword  # Store only the keyword
                                            break
                                    if not has_note:  # Fallback if something weird happens
                                        has_note = True
                                except Exception:
                                    has_note = True  # Fallback for binary/encoding issues

                        owner_name = uid_map.get(item.st_uid, str(item.st_uid))
                        group_name = gid_map.get(item.st_gid, str(item.st_gid))

                        file_metadata[relative_path] = {
                            'hash': file_hash, 'owner': owner_name, 'group': group_name,
                            'mode': stat.filemode(item.st_mode),
                            'octal_mode': oct(item.st_mode & 0o777)[2:],
                            'has_note': has_note
                        }
                    except Exception as e:
                        q_out.put(f"({server_name}) File Error {full_path}: {e}")
        except Exception as e:
            q_out.put(f"({server_name}) Directory Error {current_path}: {e}")
            # If the initial start_path is not found, this will be caught here.
            # The path_stack will eventually empty, and empty results will be returned.

    q_out.put(f"({server_name}) Scan complete. Found {len(file_metadata)} files and {len(dir_paths)} directories.")
    return file_metadata, list(dir_paths)


def compare_folders_task(s1_config, s2_config, q_out, scan_notes=False, ignore_patterns=None):
    """
    Main background task to compare two SFTP folders.
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        ssh1, sftp1 = connect_to_target(s1_config, q_out, "TEST")
        files_s1, dirs_s1 = scan_sftp_directory(sftp1, s1_config['path'], q_out, "TEST", scan_notes, ignore_patterns)

        ssh2, sftp2 = connect_to_target(s2_config, q_out, "PROD")

        # --- Auto-Create Destination Folder ---
        prod_path = s2_config['path']
        try:
            sftp2.stat(prod_path)
        except file_not_found_error:  # FileNotFoundError or similar
            q_out.put(f"(PROD) Destination path '{prod_path}' not found. Creating it...")
            try:
                if isinstance(sftp2, LocalFS):
                    os.makedirs(prod_path, exist_ok=True)
                else:
                    # Paramiko's mkdir doesn't have exist_ok, so we catch if it already exists
                    # Also, it doesn't create parent directories, so we need to handle that.
                    # For now, assume the parent of prod_path exists or handle simple case.
                    # A more robust solution would be to create parent directories recursively.
                    try:
                        sftp2.mkdir(prod_path)
                    except IOError as e:
                        if "File exists" not in str(e):  # If it exists, it's fine
                            raise  # Re-raise if it's another error
                q_out.put(f"(PROD) Created destination path.")
            except Exception as e:
                q_out.put(f"(PROD) Error creating destination: {e}")

        files_s2, dirs_s2 = scan_sftp_directory(sftp2, s2_config['path'], q_out, "PROD", scan_notes, ignore_patterns)

        q_out.put("Comparing file and directory lists...")
        set_files_s1, set_files_s2 = set(files_s1.keys()), set(files_s2.keys())
        common_files = set_files_s1.intersection(set_files_s2)

        set_dirs_s1, set_dirs_s2 = set(dirs_s1), set(dirs_s2)

        results = {
            'files_s1': files_s1, 'files_s2': files_s2,
            'only_on_1': sorted(list(set_files_s1 - set_files_s2)),
            'only_on_2': sorted(list(set_files_s2 - set_files_s1)),
            'different': [f for f in common_files if files_s1[f]['hash'] != files_s2[f]['hash']],
            'identical': [f for f in common_files if files_s1[f]['hash'] == files_s2[f]['hash']],
            'only_on_1_dirs': sorted(list(set_dirs_s1 - set_dirs_s2)),
            'only_on_2_dirs': sorted(list(set_dirs_s2 - set_dirs_s1)),
            'common_dirs': sorted(list(set_dirs_s1.intersection(set_dirs_s2)))
        }
        q_out.put(results)

    except Exception as e:
        q_out.put(e)
    finally:
        for sftp, ssh, name in [(sftp1, ssh1, "TEST"), (sftp2, ssh2, "PROD")]:
            try:
                if sftp:
                    sftp.close()
                if ssh:
                    ssh.close()
                q_out.put(f"{name} Server connection closed.")
            except Exception:
                pass


def download_file_task(config, relative_path, q_out, server_name):
    """
    Downloads a single file from an SFTP server.
    """
    ssh, sftp = None, None
    try:
        ssh, sftp = connect_to_target(config, q_out, server_name)

        full_path = f"{config['path'].rstrip('/')}/{relative_path}"
        q_out.put(f"({server_name}) Downloading: {relative_path}")

        with sftp.open(full_path, 'rb') as f:
            mem_file = f.read()

        try:
            content_str = mem_file.decode('utf-8')
        except UnicodeDecodeError:
            content_str = mem_file.decode('latin-1')

        q_out.put({'server': server_name, 'content': content_str})

    except Exception as e:
        q_out.put(e)
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


def get_all_users_task(config, q_out, server_name):
    """
    Connects to a server and fetches a list of all usernames from /etc/passwd.
    """
    ssh, sftp = None, None
    try:
        ssh, sftp = connect_to_target(config, q_out, server_name)
        q_out.put(f"({server_name}) Fetching user list...")
        # Assuming _get_name_to_uid_map is a helper function that reads /etc/passwd
        # and returns a {username: uid} map. This function is not in the provided code.
        # For now, we'll use _parse_passwd and reverse it.
        with sftp.open('/etc/passwd', 'r') as f:
            uid_map = _parse_passwd(f.read().decode('utf-8', errors='ignore'))
        users = list(uid_map.values())  # This would give UIDs, not usernames.
        # If the intent is usernames, _parse_passwd needs to be reversed or a new helper.
        q_out.put({'server': server_name, 'users': users})
    except Exception as e:
        q_out.put(e)
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


def get_all_groups_task(config, q_out, server_name):
    """
    Connects to a server and fetches a list of all group names from /etc/group.
    """
    ssh, sftp = None, None
    try:
        ssh, sftp = connect_to_target(config, q_out, server_name)
        q_out.put(f"({server_name}) Fetching group list...")
        # Similar to users, assuming _get_name_to_gid_map exists or reversing _parse_group
        with sftp.open('/etc/group', 'r') as f:
            gid_map = _parse_group(f.read().decode('utf-8', errors='ignore'))
        groups = list(gid_map.values())  # This would give GIDs, not groupnames.
        q_out.put({'server': server_name, 'groups': groups})
    except Exception as e:
        q_out.put(e)
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


def change_attributes_task(config, relative_path, owner, group, perms_str, q_out, server_name):
    """
    Changes the owner and/or permissions of a single file on an SFTP server.
    """
    ssh, sftp = None, None
    try:
        ssh, sftp = connect_to_target(config, q_out, server_name)

        full_path = f"{config['path'].rstrip('/')}/{relative_path}"
        q_out.put(f"({server_name}) Changing attributes for: {relative_path}")

        if owner or group:
            q_out.put(f"({server_name}) Setting owner to '{owner}:{group}'...")
            current_stat = sftp.stat(full_path)
            uid, gid = current_stat.st_uid, current_stat.st_gid

            with sftp.open('/etc/passwd', 'r') as f:
                name_to_uid_map = {v: k for k, v in _parse_passwd(f.read().decode('utf-8', errors='ignore')).items()}
            with sftp.open('/etc/group', 'r') as f:
                name_to_gid_map = {v: k for k, v in _parse_group(f.read().decode('utf-8', errors='ignore')).items()}

            if owner:
                if owner in name_to_uid_map:
                    uid = name_to_uid_map[owner]
                else:
                    raise ValueError(f"Owner '{owner}' not found on {server_name}.")
            if group:
                if group in name_to_gid_map:
                    gid = name_to_gid_map[group]
                else:
                    raise ValueError(f"Group '{group}' not found on {server_name}.")

            sftp.chown(full_path, uid, gid)
            q_out.put(f"({server_name}) Owner/Group changed.")

        if perms_str:
            q_out.put(f"({server_name}) Setting permissions to '{perms_str}'...")
            try:
                mode = int(perms_str, 8)
                sftp.chmod(full_path, mode)
                q_out.put(f"({server_name}) Permissions changed.")
            except ValueError:
                raise ValueError(f"Invalid permissions format: '{perms_str}'. Use an octal string (e.g., '755').")

        q_out.put(f"{server_name}:Success")
    except Exception as e:
        q_out.put(f"Error:{server_name}:{e}")
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


def backup_folder_remote_task(config, q_out, server_name):
    """
    Creates a backup of a directory on the remote server itself.
    e.g., copies /path/to/folder to /path/to/folder-backup-TIMESTAMP
    """
    ssh = None
    try:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        source_path = config['path'].rstrip('/')
        backup_path = f"{source_path}-backup-{timestamp}"

        if config['host'].lower() == 'local' or not config['host'].strip():
            q_out.put(f"({server_name}) Local backup: cp -r '{source_path}' '{backup_path}'")
            try:
                shutil.copytree(source_path, backup_path)
                q_out.put(f"({server_name}) Local backup completed successfully to {backup_path}")
                q_out.put({'status': 'backup_complete', 'success': True, 'path': backup_path})
                return
            except Exception as e:
                raise Exception(f"Local backup failed: {e}")

        q_out.put(f"({server_name}) Connecting to {config['host']} for remote backup...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=20)

        q_out.put(f"({server_name}) Starting remote backup: cp -r '{source_path}' '{backup_path}'")
        stdin, stdout, stderr = ssh.exec_command(f"cp -r '{source_path}' '{backup_path}'")

        exit_status = stdout.channel.recv_exit_status()  # Wait for command to complete

        if exit_status == 0:
            q_out.put(f"({server_name}) Remote backup completed successfully to {backup_path}")
            q_out.put({'status': 'backup_complete', 'success': True, 'path': backup_path})
        else:
            error_message = stderr.read().decode('utf-8', errors='ignore').strip()
            raise Exception(f"Remote backup failed with exit status {exit_status}: {error_message}")

    except Exception as e:
        q_out.put(e)
    finally:
        if ssh:
            ssh.close()


def backup_folder_local_task(config, local_base_path, q_out, server_name):
    """
    Recursively downloads a remote directory to a local path.
    """
    ssh, sftp = None, None
    try:
        remote_start_path = config['path'].rstrip('/')
        folder_name = os.path.basename(remote_start_path) if remote_start_path else 'root'
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        local_dest_path = os.path.join(local_base_path, f"{folder_name}-backup-{timestamp}")

        os.makedirs(local_dest_path, exist_ok=True)

        ssh, sftp = connect_to_target(config, q_out, server_name)

        q_out.put(f"({server_name}) Starting local backup to {local_dest_path}...")

        # Use a stack for iterative directory traversal
        path_stack = [remote_start_path]
        while path_stack:
            current_remote_path = path_stack.pop(0)

            for item in sftp.listdir_attr(current_remote_path):
                remote_full_path = f"{current_remote_path.rstrip('/')}/{item.filename}"
                relative_path = remote_full_path[len(remote_start_path):].lstrip('/')
                local_full_path = os.path.join(local_dest_path, relative_path)

                if stat.S_ISDIR(item.st_mode):
                    path_stack.append(remote_full_path)
                    os.makedirs(local_full_path, exist_ok=True)
                elif stat.S_ISREG(item.st_mode):
                    q_out.put(f"({server_name}) Downloading: {relative_path[:60]}...")
                    sftp.get(remote_full_path, local_full_path)

        q_out.put(f"({server_name}) Local backup completed successfully to {local_dest_path}")
        q_out.put({'status': 'backup_complete', 'success': True, 'path': local_dest_path})

    except Exception as e:
        q_out.put(e)
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


def sync_folders_task(s1_config, s2_config, comparison_results, delete_on_prod, q_out):
    """
    Synchronizes files from TEST (s1) to PROD (s2).
    - Copies files from 'only_on_1' and 'different'.
    - Deletes files from 'only_on_2' if delete_on_prod is True.
    - Sets permissions and ownership.
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        q_out.put("Connecting to servers for synchronization...")
        ssh1, sftp1 = connect_to_target(s1_config, q_out, "TEST")
        ssh2, sftp2 = connect_to_target(s2_config, q_out, "PROD")

        # --- 1. Handle Deletions on PROD ---
        if delete_on_prod:
            files_to_delete = sorted(comparison_results.get('only_on_2', []), key=len, reverse=True)
            dirs_to_delete = sorted([d for d in comparison_results.get('only_on_2_dirs', [])], key=len, reverse=True)

            q_out.put(f"Deleting {len(files_to_delete)} files from PROD server...")
            for i, relative_path in enumerate(files_to_delete):
                prod_full_path = f"{s2_config['path'].rstrip('/')}/{relative_path}"
                try:
                    q_out.put(f"({i+1}/{len(files_to_delete)}) Deleting file: {relative_path}")
                    sftp2.remove(prod_full_path)
                except Exception as e:
                    q_out.put(f"Warning: Could not delete file {relative_path} from PROD: {e}")

            q_out.put(f"Deleting {len(dirs_to_delete)} directories from PROD server...")
            for i, relative_path in enumerate(dirs_to_delete):
                prod_full_path = f"{s2_config['path'].rstrip('/')}/{relative_path}"
                try:
                    q_out.put(f"({i+1}/{len(dirs_to_delete)}) Deleting dir: {relative_path}")
                    sftp2.rmdir(prod_full_path)
                except Exception as e:
                    q_out.put(f"Warning: Could not delete dir {relative_path} from PROD: {e}")

        # --- 2. Handle Copy/Overwrite ---
        dirs_to_create = comparison_results.get('only_on_1_dirs', [])
        files_to_copy = comparison_results.get('only_on_1', []) + comparison_results.get('different', [])

        # Ensure destination root exists
        dest_root = s2_config['path'].rstrip('/')
        try:
            sftp2.stat(dest_root)
        except file_not_found_error:
            q_out.put(f"Destination root {dest_root} does not exist. Creating it...")
            try:
                sftp2.mkdir(dest_root)
            except Exception as e:
                q_out.put(f"Error creating destination root: {e}")
                # We attempt to proceed, maybe it's a permission issue or it does exist
        except Exception:
            pass

        q_out.put(f"Creating {len(dirs_to_create)} directories on destination...")
        for i, relative_path in enumerate(sorted(dirs_to_create, key=len)):
            prod_full_path = f"{s2_config['path'].rstrip('/')}/{relative_path}"
            try:
                q_out.put(f"({i+1}/{len(dirs_to_create)}) Creating dir: {relative_path}")
                sftp2.mkdir(prod_full_path)
            except Exception as e:
                q_out.put(f"Warning: Could not create dir {prod_full_path}: {e}")

        q_out.put(f"Copying/overwriting {len(files_to_copy)} files from TEST to PROD...")
        prod_uid_map, prod_gid_map = _get_name_to_uid_map(sftp2), _get_name_to_gid_map(sftp2)

        for i, relative_path in enumerate(files_to_copy):
            test_full_path = f"{s1_config['path'].rstrip('/')}/{relative_path}"
            prod_full_path = f"{s2_config['path'].rstrip('/')}/{relative_path}"

            try:
                q_out.put(f"({i+1}/{len(files_to_copy)}) Syncing: {relative_path[:60]}")

                with sftp1.open(test_full_path, 'rb') as f_test:
                    sftp2.putfo(f_test, prod_full_path)

                test_meta = comparison_results['files_s1'][relative_path]
                owner, group = test_meta.get('owner'), test_meta.get('group')
                uid, gid = prod_uid_map.get(owner), prod_gid_map.get(group)
                if uid is not None and gid is not None:
                    sftp2.chown(prod_full_path, uid, gid)

                mode_str = test_meta.get('octal_mode')
                if mode_str:
                    sftp2.chmod(prod_full_path, int(mode_str, 8))

            except Exception as e:
                q_out.put(f"Warning: Could not sync {relative_path}: {e}")

        q_out.put({'status': 'sync_complete', 'success': True})

    except Exception as e:
        q_out.put(e)
    finally:
        for sftp, ssh in [(sftp1, ssh1), (sftp2, ssh2)]:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


def upload_file_task(config, relative_path, content, q_out, server_name):
    """
    Uploads content to a file on an SFTP server.
    """
    ssh, sftp = None, None
    try:
        ssh, sftp = connect_to_target(config, q_out, server_name)

        full_path = f"{config['path'].rstrip('/')}/{relative_path}"
        q_out.put(f"({server_name}) Uploading to: {relative_path}")

        with sftp.open(full_path, 'w') as f:
            f.write(content)

        q_out.put({'status': 'upload_complete', 'server': server_name, 'success': True})
        q_out.put(f"({server_name}) Upload successful.")

    except Exception as e:
        q_out.put(e)
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


def sync_single_file_task(s1_config, s2_config, relative_path, q_out):
    """
    Synchronizes a single file from TEST (s1) to PROD (s2).
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        q_out.put(f"Connecting to servers to sync {relative_path}...")

        ssh1, sftp1 = connect_to_target(s1_config, q_out, "TEST")
        ssh2, sftp2 = connect_to_target(s2_config, q_out, "PROD")

        test_full_path = f"{s1_config['path'].rstrip('/')}/{relative_path}"
        prod_full_path = f"{s2_config['path'].rstrip('/')}/{relative_path}"

        q_out.put(f"Syncing file: {relative_path}...")

        # Ensure parent directory exists on PROD
        parent_dir = os.path.dirname(prod_full_path)
        try:
            sftp2.stat(parent_dir)
        except FileNotFoundError:
            # Simple recursive mkdir (might fail if multiple levels missing, but good enough for now)
            q_out.put(f"Creating parent directory on PROD: {parent_dir}")
            try:
                sftp2.mkdir(parent_dir)
            except Exception:
                pass

        with sftp1.open(test_full_path, 'rb') as f_test:
            sftp2.putfo(f_test, prod_full_path)

        # Try to sync permissions if possible
        try:
            test_stat = sftp1.stat(test_full_path)
            sftp2.chmod(prod_full_path, test_stat.st_mode)
        except Exception as e:
            q_out.put(f"Warning: Could not sync attributes: {e}")

        q_out.put({'status': 'single_sync_complete', 'success': True, 'file': relative_path})
        q_out.put(f"Successfully synced {relative_path} to PROD.")

    except Exception as e:
        q_out.put(e)
    finally:
        for sftp, ssh in [(sftp1, ssh1), (sftp2, ssh2)]:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()


def sync_multiple_files_task(s1_config, s2_config, relative_paths_list, q_out):
    """
    Synchronizes multiple files from TEST (s1) to PROD (s2) using a single connection.
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        q_out.put(f"Connecting to servers to sync {len(relative_paths_list)} files...")

        ssh1, sftp1 = connect_to_target(s1_config, q_out, "TEST")
        ssh2, sftp2 = connect_to_target(s2_config, q_out, "PROD")

        total = len(relative_paths_list)
        for i, relative_path in enumerate(relative_paths_list):
            try:
                test_full_path = f"{s1_config['path'].rstrip('/')}/{relative_path}"
                prod_full_path = f"{s2_config['path'].rstrip('/')}/{relative_path}"

                q_out.put(f"({i+1}/{total}) Syncing: {relative_path}")

                # Ensure parent directory exists on PROD
                parent_dir = os.path.dirname(prod_full_path)
                try:
                    sftp2.stat(parent_dir)
                except FileNotFoundError:
                    try:
                        sftp2.mkdir(parent_dir)
                    except Exception:
                        pass

                with sftp1.open(test_full_path, 'rb') as f_test:
                    sftp2.putfo(f_test, prod_full_path)

                # Try to sync permissions if possible
                try:
                    test_stat = sftp1.stat(test_full_path)
                    sftp2.chmod(prod_full_path, test_stat.st_mode)
                except Exception as e:
                    q_out.put(f"Warning: Could not sync attributes for {relative_path}: {e}")

                q_out.put({'status': 'single_sync_complete', 'success': True, 'file': relative_path})

            except Exception as e:
                q_out.put(f"Error syncing {relative_path}: {e}")

        q_out.put({'status': 'batch_sync_complete', 'success': True})
        q_out.put("Batch sync complete.")

    except Exception as e:
        q_out.put(e)
    finally:
        for sftp, ssh in [(sftp1, ssh1), (sftp2, ssh2)]:
            if sftp:
                sftp.close()
            if ssh:
                ssh.close()
