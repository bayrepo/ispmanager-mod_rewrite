#!/usr/bin/env python3

import os
import xml.etree.ElementTree as etree
from sys import stdin
import re
import datetime
from xml.dom import minidom
import shutil
import subprocess
import fcntl

CONFIG_PATH = "/usr/share/nginx/modules/ngx_http_apache_rewrite_module.conf"
MODULE_LINE = 'load_module "/usr/lib64/nginx/modules/ngx_http_apache_rewrite_module.so";'
MODULE_PATTERN = re.compile(r'^\s*load_module\s+"\/usr\/lib64\/nginx\/modules\/ngx_http_apache_rewrite_module.so";\s*$')
LOG_PATH = "/usr/local/mgr5/var/nginx-mod-rewrite-plugin-addon.log"

def _log(msg: str) -> None:
    debug = read_global_config({'param_name': 'debug'})
    if debug not in ("1", "on"):
        return
    try:
        with open(LOG_PATH, 'a') as log_file:
            log_file.write(f"{datetime.datetime.now().isoformat()} - {msg}\n")
    except Exception:
        pass

def nginx_restart():
    _log("nginx_restart called")
    try:
        result = os.system("systemctl restart nginx")
        if result != 0:
            _log(f"systemctl restart nginx returned non-zero exit status {result}")
        else:
            _log("Nginx restarted successfully")
    except Exception as e:
        _log(f"Error restarting nginx: {e}")

def out_error_xml(message):
    import sys
    root = etree.Element("error", code="access")
    msg = etree.SubElement(root, "msg")
    msg.text = message
    xml_str = etree.tostring(root, encoding='unicode', xml_declaration=True)
    print(xml_str)
    sys.exit(1)

def dump_all_to_log(root):
    try:
        with open(LOG_PATH, 'a') as log_file:
            timestamp = datetime.datetime.now().isoformat()
            xml_str = etree.tostring(root, encoding='unicode')
            # Pretty‑print the XML
            pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ")
            log_file.write(f"{timestamp} - Dumping XML to log:\n{pretty_xml}\n")
            log_file.write(f"{timestamp} - Environment variables:\n")
            for key, value in sorted(os.environ.items()):
                log_file.write(f"{key}={value}\n")
    except Exception:
        pass

def unlock_plugin():
    """
    Release the lock on the plugin lock file.

    Returns:
        bool: True if lock was successfully released, False otherwise (e.g., no lock held)
    """
    lock_path = '/usr/local/ispmanager-mod_rewrite/.lock'

    try:
        if not os.path.exists(lock_path):
            return False

        with open(lock_path, 'r+') as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.truncate(0)
            lock_file.seek(0)
        return True
    except Exception:
        return False


def lock_plugin_with_to():
    """
    Acquire an exclusive (write) lock on the plugin lock file.

    Returns:
        bool: True if lock was successfully acquired, False otherwise
    """
    lock_path = '/usr/local/ispmanager-mod_rewrite/.lock'

    try:
        if not os.path.exists(lock_path):
            return False

        with open(lock_path, 'r+') as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            # Add timestamp and PID to indicate this process holds the lock
            lock_file.write(f'Locked by PID {os.getpid()} at {datetime.datetime.now()}\n')
            lock_file.truncate(0)
        return True
    except Exception:
        return False


def lock_plugin_with_to_read():
    """
    Acquire a shared (read) lock on the plugin lock file.
    Allows multiple readers but prevents writers from acquiring exclusive lock.

    Returns:
        bool: True if lock was successfully acquired, False otherwise
    """
    lock_path = '/usr/local/ispmanager-mod_rewrite/.lock'

    try:
        if not os.path.exists(lock_path):
            return False

        with open(lock_path, 'r+') as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_SH)
            # Add timestamp to indicate this reader holds a shared lock
            lock_file.write(f'Read lock by PID {os.getpid()} at {datetime.datetime.now()}\n')
            lock_file.truncate(0)
        return True
    except Exception:
        return False

def site_state(param):
    """
    Get or set the state of a site in sites_settings.cfg.
    :param param: either a string key or a dict {key: 'on'/'off'}.
    :return: For string key, returns value string or False if not found.
    """
    config_path = "/usr/local/ispmanager-mod_rewrite/settings/sites_settings.cfg"

    if isinstance(param, str):
        lock_plugin_with_to_read()
        param_name = param
        if not os.path.exists(config_path):
            unlock_plugin()
            return "off"
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue
                    pattern = rf'^\s*{re.escape(param_name)}\s*=\s*(.*)$'
                    m = re.match(pattern, line)
                    if m:
                        unlock_plugin()
                        return m.group(1).strip()
        except Exception:
            unlock_plugin()
            return "off"
        unlock_plugin()
        return "off"

    elif isinstance(param, dict):
        # Write mode
        lock_plugin_with_to()
        dir_path = os.path.dirname(config_path)
        try:
            os.makedirs(dir_path, exist_ok=True)
        except Exception:
            unlock_plugin()
            return None
        lines = []
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception:
                unlock_plugin()
                return None
        for key, val in param.items():
            actual_value = 'on' if val == 'on' else 'off'
            found = False
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                pattern = rf'^\s*{re.escape(key)}\s*=\s*.*$'
                if re.match(pattern, line):
                    lines[idx] = f"{key}={actual_value}\n"
                    found = True
                    break
            if not found:
                lines.append(f"{key}={actual_value}\n")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        except Exception:
            unlock_plugin()
            return None
        unlock_plugin()
        return None
    else:
        return None

def read_global_config(params):
    """
    Reads or writes a parameter in the global settings configuration file.
    :param params: dict with keys 'param_name' and 'param_value'.
    :return: param_value as string if reading; None otherwise.
    """
    param_name = params.get('param_name')
    if not param_name:
        return None
    param_value = params.get('param_value')
    config_path = "/usr/local/ispmanager-mod_rewrite/settings/global_settings.cfg"


    if param_value is None:
        lock_plugin_with_to_read()
        # Read mode
        if not os.path.exists(config_path):
            unlock_plugin()
            return None
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith('#'):
                        continue
                    pattern = rf'^\s*{re.escape(param_name)}\s*=\s*(.*)$'
                    m = re.match(pattern, line)
                    if m:
                        unlock_plugin()
                        return m.group(1).strip()
        except Exception:
            unlock_plugin()
            return None
        unlock_plugin()
        return None
    else:
        lock_plugin_with_to()
        # Write mode
        dir_path = os.path.dirname(config_path)
        try:
            os.makedirs(dir_path, exist_ok=True)
        except Exception:
            unlock_plugin()
            return None
        lines = []
        found = False
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception:
                unlock_plugin()
                return None
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                pattern = rf'^\s*{re.escape(param_name)}\s*=\s*.*$'
                if re.match(pattern, line):
                    lines[idx] = f"{param_name}={param_value}\n"
                    found = True
                    break
        if not found:
            lines.append(f"{param_name}={param_value}\n")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        except Exception:
            unlock_plugin()
            return None
        unlock_plugin()
        return None

def check_nginx_rewrite_module():
    _log("check_nginx_rewrite_module called")
    if not os.path.exists(CONFIG_PATH):
        _log(f"Config file {CONFIG_PATH} does not exist")
        return False
    lock_plugin_with_to_read()
    try:
        with open(CONFIG_PATH, 'r') as f:
            for line in f:
                stripped = line.lstrip()
                if stripped.startswith('#'):
                    continue
                if MODULE_PATTERN.match(line):
                    _log("Module found and enabled in config")
                    unlock_plugin()
                    return True
        _log("Module not found in config")
        unlock_plugin()
        return False
    except Exception as e:
        _log(f"Error reading config file {CONFIG_PATH}: {e}")
        unlock_plugin()
        return False

def set_nginx_rewrite_module(enable: bool):
    _log(f"set_nginx_rewrite_module called with enable={enable}")
    lock_plugin_with_to()
    if enable:
        if not os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'w') as f:
                    f.write(MODULE_LINE + "\n")
                _log(f"Created config file {CONFIG_PATH} and added module line")
            except Exception as e:
                _log(f"Failed to create config file {CONFIG_PATH}: {e}")
        else:
            try:
                with open(CONFIG_PATH, 'r') as f:
                    lines = f.readlines()
                found_uncommented = False
                found_commented = False
                idx_commented = -1
                for idx, line in enumerate(lines):
                    stripped = line.lstrip()
                    if stripped.startswith('#'):
                        stripped_no_hash = stripped.lstrip('#').lstrip()
                        if MODULE_PATTERN.match(stripped_no_hash):
                            found_commented = True
                            idx_commented = idx
                    else:
                        if MODULE_PATTERN.match(stripped):
                            found_uncommented = True
                if not found_uncommented:
                    if found_commented:
                        lines[idx_commented] = MODULE_LINE + "\n"
                        _log(f"Uncommented module line at line {idx_commented+1}")
                    else:
                        lines.append(MODULE_LINE + "\n")
                        _log("Appended module line to config")
                    with open(CONFIG_PATH, 'w') as f:
                        f.writelines(lines)
                    _log(f"Wrote changes to config file {CONFIG_PATH}")
            except Exception as e:
                _log(f"Error processing config file {CONFIG_PATH}: {e}")
    else:
        if not os.path.exists(CONFIG_PATH):
            _log(f"Config file {CONFIG_PATH} does not exist; nothing to disable")
            return
        try:
            with open(CONFIG_PATH, 'r') as f:
                lines = f.readlines()
            changed = False
            for idx, line in enumerate(lines):
                stripped = line.lstrip()
                if stripped.startswith('#'):
                    continue
                if MODULE_PATTERN.match(stripped):
                    lines[idx] = '#' + line
                    changed = True
                    _log(f"Commented out module line at line {idx+1}")
            if changed:
                with open(CONFIG_PATH, 'w') as f:
                    f.writelines(lines)
                _log(f"Wrote changes to config file {CONFIG_PATH} after disabling module")
            else:
                _log("No uncommented module line found to disable")
        except Exception as e:
            _log(f"Error disabling module in config file {CONFIG_PATH}: {e}")
    unlock_plugin()

def make_nginx_custom_template() -> bool:
    tmpl = "/usr/local/mgr5/etc/templates/nginx-vhosts.template"
    tmpl_ssl = "/usr/local/mgr5/etc/templates/nginx-vhosts-ssl.template"

    # If templates already exist, abort
    if os.path.exists(tmpl) or os.path.exists(tmpl_ssl):
        _log("Error: Custom templates already exist. Aborting make_nginx_custom_template.")
        return False

    default_dir = "/usr/local/mgr5/etc/templates/default"
    default_tmpl = os.path.join(default_dir, "nginx-vhosts.template")
    default_ssl_tmpl = os.path.join(default_dir, "nginx-vhosts-ssl.template")
    target_dir = "/usr/local/mgr5/etc/templates"

    lock_plugin_with_to()

    # Copy default templates if they exist
    try:
        if os.path.exists(default_tmpl):
            shutil.copy2(default_tmpl, target_dir)
        if os.path.exists(default_ssl_tmpl):
            shutil.copy2(default_ssl_tmpl, target_dir)
    except Exception as e:
        _log(f"Error copying default templates: {e}")
        # Remove any partially copied files
        for f in (tmpl, tmpl_ssl):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        unlock_plugin()
        return False

    # Ensure copied files exist
    if not os.path.exists(tmpl) or not os.path.exists(tmpl_ssl):
        _log("Error: Failed to copy one or both template files.")
        for f in (tmpl, tmpl_ssl):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        unlock_plugin()
        return False

    patches_dir = "/usr/local/ispmanager-mod_rewrite/patches"
    patch_tmpl = os.path.join(patches_dir, "nginx-vhosts.template.patch")
    patch_ssl = os.path.join(patches_dir, "nginx-vhosts-ssl.template.patch")

    def apply_patch(target, patch_file):
        if not os.path.exists(patch_file):
            _log(f"Patch file {patch_file} does not exist.")
            unlock_plugin()
            return False
        try:
            result = subprocess.run(
                ["patch", "-p1", target, patch_file],
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                _log(f"Patch failed for {target}: {result.stderr.decode(errors='ignore')}")
                unlock_plugin()
                return False
            unlock_plugin()
            return True
        except Exception as e:
            _log(f"Exception applying patch to {target}: {e}")
            unlock_plugin()
            return False

    patch_success = True
    if not apply_patch(tmpl, patch_tmpl):
        patch_success = False
    if not apply_patch(tmpl_ssl, patch_ssl):
        patch_success = False

    if not patch_success:
        _log("Error: One or more patches failed. Removing copied template files.")
        for f in (tmpl, tmpl_ssl):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        unlock_plugin()
        return False
    unlock_plugin()
    read_global_config({'param_name': 'templates_created', 'param_value': 'on'})
    _log("Success: Custom Nginx templates created and patched successfully.")
    return True

if __name__ == "__main__":
    root = etree.parse(stdin).getroot()
    func = os.getenv('PARAM_func')
    user_level = os.getenv('AUTH_LEVEL', '2')

    _log(f"Function called: {func}, User level: {user_level}")
    if int(user_level) < 16:
        _log("User level below user; skipping actions at all")
        out_error_xml("Access denied")

    if func == 'nginx_mod_rewrite_plugin_menu_settings':
        # Проверка уровня доступа - действия только для admin и выше (уровень 29+)
        if int(user_level) < 29:
            _log("User level below admin; skipping actions")
            out_error_xml("Access denied")

        nginx_mod_rewrite_plugin_menu_module_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_menu_module_enable")
        nginx_mod_rewrite_plugin_menu_debug_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_menu_debug_enable")
        mod_enabled = check_nginx_rewrite_module()
        _log(f"Current module state: {'enabled' if mod_enabled else 'disabled'}")
        mod_rewrite_global = read_global_config({'param_name': 'global_mod_rewrite_enable'})
        debug = read_global_config({'param_name': 'debug'})

        if os.getenv('PARAM_clicked_button') == 'ok' and nginx_mod_rewrite_plugin_menu_module_enable is not None:
            if nginx_mod_rewrite_plugin_menu_module_enable == "on" and not mod_enabled:
                set_nginx_rewrite_module(True)
                nginx_restart()
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_module_enable")
                new_element.text = "on"
                root.append(new_element)
            elif nginx_mod_rewrite_plugin_menu_module_enable == "off" and mod_enabled:
                set_nginx_rewrite_module(False)
                nginx_restart()
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_module_enable")
                new_element.text = "off"
                root.append(new_element)
            else:
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_module_enable")
                new_element.text = "on" if mod_enabled else "off"
                root.append(new_element)
        else:
            new_element = etree.Element("nginx_mod_rewrite_plugin_menu_module_enable")
            new_element.text = "on" if mod_enabled else "off"
            root.append(new_element)

        nginx_mod_rewrite_plugin_menu_global_module_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_menu_global_module_enable")
        if os.getenv('PARAM_clicked_button') == 'ok' and nginx_mod_rewrite_plugin_menu_global_module_enable is not None:
            if nginx_mod_rewrite_plugin_menu_global_module_enable == "on" and mod_rewrite_global not in ("1", "on"):
                if make_nginx_custom_template():
                    read_global_config({'param_name': 'global_mod_rewrite_enable', 'param_value': 'on'})
                    new_element = etree.Element("nginx_mod_rewrite_plugin_menu_global_module_enable")
                    new_element.text = "on"
                    root.append(new_element)
                else:
                    new_element = etree.Element("nginx_mod_rewrite_plugin_menu_global_module_enable")
                    new_element.text = "off"
                    root.append(new_element)
            elif nginx_mod_rewrite_plugin_menu_global_module_enable == "off" and mod_rewrite_global in ("1", "on"):
                read_global_config({'param_name': 'global_mod_rewrite_enable', 'param_value': 'off'})
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_global_module_enable")
                new_element.text = "off"
                root.append(new_element)
                is_template = read_global_config({'param_name': 'templates_created'})
                if is_template in ("1", "on"):
                    tmpl = "/usr/local/mgr5/etc/templates/nginx-vhosts.template"
                    tmpl_ssl = "/usr/local/mgr5/etc/templates/nginx-vhosts-ssl.template"
                    for f in (tmpl, tmpl_ssl):
                        try:
                            if os.path.exists(f):
                                os.remove(f)
                        except Exception:
                            pass
            else:
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_global_module_enable")
                new_element.text = "on" if mod_rewrite_global in ("1", "on") else "off"
                root.append(new_element)
        else:
            new_element = etree.Element("nginx_mod_rewrite_plugin_menu_global_module_enable")
            new_element.text = "on" if mod_rewrite_global in ("1", "on") else "off"
            root.append(new_element)

        if os.getenv('PARAM_clicked_button') == 'ok' and nginx_mod_rewrite_plugin_menu_debug_enable is not None:
            if nginx_mod_rewrite_plugin_menu_debug_enable == "on" and debug not in ("1", "on"):
                read_global_config({'param_name': 'debug', 'param_value': 'on'})
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_debug_enable")
                new_element.text = "on"
                root.append(new_element)
            elif nginx_mod_rewrite_plugin_menu_debug_enable == "off" and debug in ("1", "on"):
                read_global_config({'param_name': 'debug', 'param_value': 'off'})
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_debug_enable")
                new_element.text = "off"
                root.append(new_element)
            else:
                new_element = etree.Element("nginx_mod_rewrite_plugin_menu_debug_enable")
                new_element.text = "on" if debug in ("1", "on") else "off"
                root.append(new_element)
        else:
            new_element = etree.Element("nginx_mod_rewrite_plugin_menu_debug_enable")
            new_element.text = "on" if debug in ("1", "on") else "off"
            root.append(new_element)

    elif func == "webdomain.edit":
        _log("webdomain.edit handler")
        if int(user_level) < 16:
            _log("User level below user; skipping actions at all")
            out_error_xml("Access denied")
        sok = os.environ.get('PARAM_sok', '')
        type = os.getenv("EVENT_TYPE")
        domain = os.environ.get('PARAM_elid', '')
        if sok == 'ok' and type == 'after':
            path_to_global_config = f'/etc/nginx/vhosts-resources/{domain}'
            nginx_mod_rewrite_plugin_site_edit_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_site_edit_enable")
            mod_rewrite_global = read_global_config({'param_name': 'global_mod_rewrite_enable'})
            if nginx_mod_rewrite_plugin_site_edit_enable == "on":
                if mod_rewrite_global in ("1", "on"):
                    _log("Template state enabled")
                    new_element = etree.Element("nginx_mod_rewrite_plugin_site_edit_enable")
                    new_element.text = "on"
                    root.append(new_element)
                else:
                    new_element = etree.Element("nginx_mod_rewrite_plugin_site_edit_enable")
                    new_element.text = "on"
                    root.append(new_element)
                    if os.path.isdir(path_to_global_config):
                        mod_file_path = os.path.join(path_to_global_config, "mod_rewrite.conf")
                        try:
                            with open(mod_file_path, "w", encoding="utf-8") as f:
                                f.write("GlobalLocationRewriteEngine on;\nHtaccessEnable on;\n")
                            _log(f"Created/overwritten mod_rewrite.conf at {mod_file_path}")
                        except Exception as e:
                            _log(f"Failed to write mod_rewrite.conf at {mod_file_path}: {e}")
                    else:
                        _log(f"Path {path_to_global_config} is not a directory or does not exist")
                site_state({ domain: "on"})
            else:
                _log("mod_rewrite should be disbled")
                if mod_rewrite_global not in ("1", "on"):
                    _log("Global state enabled")
                    if os.path.isdir(path_to_global_config):
                        mod_file_path = os.path.join(path_to_global_config, "mod_rewrite.conf")
                        if os.path.isfile(mod_file_path):
                            try:
                                os.remove(mod_file_path)
                                _log(f"Deleted existing mod_rewrite.conf at {mod_file_path}")
                            except Exception as e:
                                _log(f"Failed to delete mod_rewrite.conf at {mod_file_path}: {e}")
                        else:
                            _log(f"mod_rewrite.conf at {mod_file_path} does not exist; nothing to delete")
                    else:
                        _log(f"Path {path_to_global_config} is not a directory or does not exist")
                new_element = etree.Element("nginx_mod_rewrite_plugin_site_edit_enable")
                new_element.text = "off"
                root.append(new_element)
                site_state({ domain: "off"})
        else:
            if domain != "":
                domain_state = site_state(domain)
                if domain_state == "on":
                    new_element = etree.Element("nginx_mod_rewrite_plugin_site_edit_enable")
                    new_element.text = "on"
                    root.append(new_element)
    elif func == "site.edit":
        _log("site.edit handler")
    etree.dump(root)
    quit(0)
