import paramiko
import stat
import hashlib
import io
import difflib
import time
import os

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

def _get_name_to_uid_map(sftp):
    """Fetches /etc/passwd and returns a {username: uid} map."""
    name_map = {}
    with sftp.open('/etc/passwd', 'r') as f:
        content = f.read().decode('utf-8', errors='ignore')
        for line in content.splitlines():
            if not line or line.startswith('#'): continue
            parts = line.split(':')
            if len(parts) >= 3:
                name_map[parts[0]] = int(parts[2])
    return name_map

def _get_name_to_gid_map(sftp):
    """Fetches /etc/group and returns a {groupname: gid} map."""
    name_map = {}
    with sftp.open('/etc/group', 'r') as f:
        content = f.read().decode('utf-8', errors='ignore')
        for line in content.splitlines():
            if not line or line.startswith('#'): continue
            parts = line.split(':')
            if len(parts) >= 3:
                name_map[parts[0]] = int(parts[2])
    return name_map

def scan_sftp_directory(sftp, start_path, q_out, server_name):
    """
    Recursively scans an SFTP path, returning metadata for files and a list of directories.
    """
    uid_map, gid_map = {}, {}
    try:
        q_out.put(f"({server_name}) Reading user/group maps...")
        with sftp.open('/etc/passwd', 'r') as f:
            uid_map = _parse_passwd(f.read().decode('utf-8', errors='ignore'))
        with sftp.open('/etc/group', 'r') as f:
            gid_map = _parse_group(f.read().decode('utf-8', errors='ignore'))
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
        
        try:
            for item in sftp.listdir_attr(current_path):
                full_path = f"{current_path.rstrip('/')}/{item.filename.lstrip('/')}"
                relative_path = full_path[len(start_path):].lstrip('/')
                
                if stat.S_ISDIR(item.st_mode):
                    # Ignore '.' and '..' directories
                    if item.filename in ['.', '..']:
                        continue
                    path_stack.append(full_path)
                    if relative_path: # Don't add the root path itself
                        dir_paths.add(relative_path)
                elif stat.S_ISREG(item.st_mode):
                    try:
                        mem_file = io.BytesIO()
                        with sftp.open(full_path, 'rb') as f:
                            mem_file.write(f.read())
                        mem_file.seek(0)
                        
                        file_hash = hashlib.md5(mem_file.read()).hexdigest()
                        
                        owner_name = uid_map.get(item.st_uid, str(item.st_uid))
                        group_name = gid_map.get(item.st_gid, str(item.st_gid))

                        file_metadata[relative_path] = {
                            'hash': file_hash, 'owner': owner_name, 'group': group_name,
                            'mode': stat.filemode(item.st_mode),
                            'octal_mode': oct(item.st_mode & 0o777)[2:]
                        }
                    except Exception as e:
                        q_out.put(f"({server_name}) File Error {full_path}: {e}")
        except Exception as e:
            q_out.put(f"({server_name}) Directory Error {current_path}: {e}")
            
    q_out.put(f"({server_name}) Scan complete. Found {len(file_metadata)} files and {len(dir_paths)} directories.")
    return file_metadata, list(dir_paths)

def compare_folders_task(s1_config, s2_config, q_out):
    """
    Main background task to compare two SFTP folders.
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        q_out.put(f"Connecting to TEST Server ({s1_config['host']})...")
        ssh1 = paramiko.SSHClient()
        ssh1.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh1.connect(s1_config['host'], port=int(s1_config['port']), username=s1_config['user'], password=s1_config['pass'], timeout=10)
        sftp1 = ssh1.open_sftp()
        files_s1, dirs_s1 = scan_sftp_directory(sftp1, s1_config['path'], q_out, "TEST")
        
        q_out.put(f"Connecting to PRODUCTION Server ({s2_config['host']})...")
        ssh2 = paramiko.SSHClient()
        ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh2.connect(s2_config['host'], port=int(s2_config['port']), username=s2_config['user'], password=s2_config['pass'], timeout=10)
        sftp2 = ssh2.open_sftp()
        files_s2, dirs_s2 = scan_sftp_directory(sftp2, s2_config['path'], q_out, "PROD")

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
                if sftp: sftp.close()
                if ssh: ssh.close()
                q_out.put(f"{name} Server connection closed.")
            except: pass

def download_file_task(config, relative_path, q_out, server_name):
    """
    Downloads a single file from an SFTP server.
    """
    ssh, sftp = None, None
    try:
        q_out.put(f"({server_name}) Connecting to {config['host']}...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
        sftp = ssh.open_sftp()
        
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
        if sftp: sftp.close()
        if ssh: ssh.close()

def get_all_users_task(config, q_out, server_name):
    """
    Connects to a server and fetches a list of all usernames from /etc/passwd.
    """
    ssh, sftp = None, None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
        sftp = ssh.open_sftp()
        q_out.put(f"({server_name}) Fetching user list...")
        users = list(_get_name_to_uid_map(sftp).keys())
        q_out.put({'server': server_name, 'users': users})
    except Exception as e:
        q_out.put(e)
    finally:
        if sftp: sftp.close()
        if ssh: ssh.close()

def get_all_groups_task(config, q_out, server_name):
    """
    Connects to a server and fetches a list of all group names from /etc/group.
    """
    ssh, sftp = None, None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
        sftp = ssh.open_sftp()
        q_out.put(f"({server_name}) Fetching group list...")
        groups = list(_get_name_to_gid_map(sftp).keys())
        q_out.put({'server': server_name, 'groups': groups})
    except Exception as e:
        q_out.put(e)
    finally:
        if sftp: sftp.close()
        if ssh: ssh.close()

def change_attributes_task(config, relative_path, owner, group, perms_str, q_out, server_name):
    """
    Changes the owner and/or permissions of a single file on an SFTP server.
    """
    ssh, sftp = None, None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
        sftp = ssh.open_sftp()
        
        full_path = f"{config['path'].rstrip('/')}/{relative_path}"
        q_out.put(f"({server_name}) Changing attributes for: {relative_path}")

        if owner or group:
            q_out.put(f"({server_name}) Setting owner to '{owner}:{group}'...")
            current_stat = sftp.stat(full_path)
            uid, gid = current_stat.st_uid, current_stat.st_gid

            if owner:
                uid_map = _get_name_to_uid_map(sftp)
                if owner in uid_map: uid = uid_map[owner]
                else: raise ValueError(f"Owner '{owner}' not found on {server_name}.")
            if group:
                gid_map = _get_name_to_gid_map(sftp)
                if group in gid_map: gid = gid_map[group]
                else: raise ValueError(f"Group '{group}' not found on {server_name}.")
            
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
        if sftp: sftp.close()
        if ssh: ssh.close()

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

        q_out.put(f"({server_name}) Connecting to {config['host']} for remote backup...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=20)

        q_out.put(f"({server_name}) Starting remote backup: cp -r '{source_path}' '{backup_path}'")
        stdin, stdout, stderr = ssh.exec_command(f"cp -r '{source_path}' '{backup_path}'")
        
        exit_status = stdout.channel.recv_exit_status() # Wait for command to complete
        
        if exit_status == 0:
            q_out.put(f"({server_name}) Remote backup completed successfully to {backup_path}")
            q_out.put({'status': 'backup_complete', 'success': True, 'path': backup_path})
        else:
            error_message = stderr.read().decode('utf-8', errors='ignore').strip()
            raise Exception(f"Remote backup failed with exit status {exit_status}: {error_message}")

    except Exception as e:
        q_out.put(e)
    finally:
        if ssh: ssh.close()

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

        q_out.put(f"({server_name}) Connecting to {config['host']} for local backup...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
        sftp = ssh.open_sftp()

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
        if sftp: sftp.close()
        if ssh: ssh.close()

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
        # Connect to TEST
        ssh1 = paramiko.SSHClient()
        ssh1.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh1.connect(s1_config['host'], port=int(s1_config['port']), username=s1_config['user'], password=s1_config['pass'], timeout=10)
        sftp1 = ssh1.open_sftp()
        
        # Connect to PROD
        ssh2 = paramiko.SSHClient()
        ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh2.connect(s2_config['host'], port=int(s2_config['port']), username=s2_config['user'], password=s2_config['pass'], timeout=10)
        sftp2 = ssh2.open_sftp()
        
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
        
        q_out.put(f"Creating {len(dirs_to_create)} directories on PROD server...")
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
                if uid is not None and gid is not None: sftp2.chown(prod_full_path, uid, gid)
                
                mode_str = test_meta.get('octal_mode')
                if mode_str: sftp2.chmod(prod_full_path, int(mode_str, 8))

            except Exception as e:
                q_out.put(f"Warning: Could not sync {relative_path}: {e}")

        q_out.put({'status': 'sync_complete', 'success': True})

    except Exception as e:
        q_out.put(e)
    finally:
        for sftp, ssh in [(sftp1, ssh1), (sftp2, ssh2)]:
            if sftp: sftp.close()
            if ssh: ssh.close()

def upload_file_task(config, relative_path, content, q_out, server_name):
    """
    Uploads content to a file on an SFTP server.
    """
    ssh, sftp = None, None
    try:
        q_out.put(f"({server_name}) Connecting to {config['host']}...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['host'], port=int(config['port']), username=config['user'], password=config['pass'], timeout=10)
        sftp = ssh.open_sftp()
        
        full_path = f"{config['path'].rstrip('/')}/{relative_path}"
        q_out.put(f"({server_name}) Uploading to: {relative_path}")
        
        with sftp.open(full_path, 'w') as f:
            f.write(content)
            
        q_out.put({'status': 'upload_complete', 'server': server_name, 'success': True})
        q_out.put(f"({server_name}) Upload successful.")
        
    except Exception as e:
        q_out.put(e)
    finally:
        if sftp: sftp.close()
        if ssh: ssh.close()

def sync_single_file_task(s1_config, s2_config, relative_path, q_out):
    """
    Synchronizes a single file from TEST (s1) to PROD (s2).
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        q_out.put(f"Connecting to servers to sync {relative_path}...")
        
        # Connect to TEST
        ssh1 = paramiko.SSHClient()
        ssh1.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh1.connect(s1_config['host'], port=int(s1_config['port']), username=s1_config['user'], password=s1_config['pass'], timeout=10)
        sftp1 = ssh1.open_sftp()
        
        # Connect to PROD
        ssh2 = paramiko.SSHClient()
        ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh2.connect(s2_config['host'], port=int(s2_config['port']), username=s2_config['user'], password=s2_config['pass'], timeout=10)
        sftp2 = ssh2.open_sftp()
        
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
            if sftp: sftp.close()
            if ssh: ssh.close()

def sync_multiple_files_task(s1_config, s2_config, relative_paths_list, q_out):
    """
    Synchronizes multiple files from TEST (s1) to PROD (s2) using a single connection.
    """
    ssh1, sftp1, ssh2, sftp2 = None, None, None, None
    try:
        q_out.put(f"Connecting to servers to sync {len(relative_paths_list)} files...")
        
        # Connect to TEST
        ssh1 = paramiko.SSHClient()
        ssh1.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh1.connect(s1_config['host'], port=int(s1_config['port']), username=s1_config['user'], password=s1_config['pass'], timeout=10)
        sftp1 = ssh1.open_sftp()
        
        # Connect to PROD
        ssh2 = paramiko.SSHClient()
        ssh2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh2.connect(s2_config['host'], port=int(s2_config['port']), username=s2_config['user'], password=s2_config['pass'], timeout=10)
        sftp2 = ssh2.open_sftp()
        
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
            if sftp: sftp.close()
            if ssh: ssh.close()
