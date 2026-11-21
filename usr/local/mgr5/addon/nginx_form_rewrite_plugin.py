#!/usr/bin/env python3

import os
import sys
import xml.etree.ElementTree as etree
from sys import stdin
import re
import datetime
from xml.dom import minidom
import shutil
import subprocess
import fcntl
import tempfile
import configparser
import io

CONFIG_PATH = "/usr/share/nginx/modules/ngx_http_apache_rewrite_module.conf"
MODULE_SO_PATH = "/usr/lib64/nginx/modules/ngx_http_apache_rewrite_module.so"
MODULE_LINE = 'load_module "/usr/lib64/nginx/modules/ngx_http_apache_rewrite_module.so";'
MODULE_PATTERN = re.compile(r'^\s*load_module\s+"\/usr\/lib64\/nginx\/modules\/ngx_http_apache_rewrite_module.so";\s*$')
LOG_PATH = "/usr/local/mgr5/var/nginx-mod-rewrite-plugin-addon.log"
LOCK_PATH = '/usr/local/ispmanager-mod_rewrite/.lock'
LOCK_TIMEOUT = 10

SECTION = 'settings'

_debug_cache = None

def _read_ini(path):
    cp = configparser.RawConfigParser()
    cp.optionxform = str
    if not os.path.exists(path):
        return cp
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        if not content.strip().startswith('['):
            content = f'[{SECTION}]\n{content}'
        cp.read_string(content)
    except (configparser.Error, Exception):
        pass
    return cp

def _write_ini(cp, path):
    buf = io.StringIO()
    cp.write(buf)
    raw = buf.getvalue()
    lines = raw.splitlines(True)
    body = ''.join(l for l in lines if not l.startswith(f'[{SECTION}]'))
    atomic_write(path, body)

def _get_val(cp, key):
    if cp.has_section(SECTION):
        return cp.get(SECTION, key, fallback=None)
    return None

def _set_val(cp, key, value):
    if not cp.has_section(SECTION):
        cp.add_section(SECTION)
    cp.set(SECTION, key, value)

def _is_debug():
    global _debug_cache
    if _debug_cache is not None:
        return _debug_cache
    config_path = "/usr/local/ispmanager-mod_rewrite/settings/global_settings.cfg"
    _debug_cache = False
    if not os.path.exists(config_path):
        return False
    try:
        cp = _read_ini(config_path)
        val = _get_val(cp, 'debug')
        if val is not None:
            _debug_cache = val.strip() in ("1", "on")
    except Exception:
        pass
    return _debug_cache

def _log(msg: str) -> None:
    if not _is_debug():
        return
    try:
        with open(LOG_PATH, 'a') as log_file:
            log_file.write(f"{datetime.datetime.now().isoformat()} - {msg}\n")
    except Exception:
        pass

def _reset_debug_cache():
    global _debug_cache
    _debug_cache = None

def atomic_write(filepath, content, encoding='utf-8'):
    dir_path = os.path.dirname(filepath)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix='.tmp_')
    try:
        with os.fdopen(fd, 'w', encoding=encoding) as f:
            f.write(content)
        os.rename(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

class PluginLock:
    def __init__(self, exclusive=True, timeout=LOCK_TIMEOUT):
        self.exclusive = exclusive
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        if not os.path.exists(LOCK_PATH):
            return self
        self._fd = open(LOCK_PATH, 'r+')
        flag = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
        deadline = datetime.datetime.now() + datetime.timedelta(seconds=self.timeout)
        while True:
            try:
                fcntl.flock(self._fd, flag | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if datetime.datetime.now() >= deadline:
                    self._fd.close()
                    self._fd = None
                    raise TimeoutError(f"Could not acquire {'exclusive' if self.exclusive else 'shared'} lock within {self.timeout}s")
                import time
                time.sleep(0.1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None
        return False

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

def append_error_to_doc(root, error_type, object_name, message_text):
    error_elem = etree.SubElement(root, 'error', {
        'type': error_type,
        'object': object_name,
    })
    msg_elem = etree.SubElement(error_elem, 'msg')
    msg_elem.text = message_text
    return root

def dump_all_to_log(root):
    try:
        with open(LOG_PATH, 'a') as log_file:
            timestamp = datetime.datetime.now().isoformat()
            xml_str = etree.tostring(root, encoding='unicode')
            pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ")
            log_file.write(f"{timestamp} - Dumping XML to log:\n{pretty_xml}\n")
            log_file.write(f"{timestamp} - Environment variables:\n")
            for key, value in sorted(os.environ.items()):
                log_file.write(f"{key}={value}\n")
    except Exception:
        pass

def site_state(param):
    """
    Get or set the state of a site in sites_settings.cfg.
    :param param: either a string key or a dict {key: 'on'/'off'}.
    :return: For string key, returns value string or "off" if not found.
    """
    config_path = "/usr/local/ispmanager-mod_rewrite/settings/sites_settings.cfg"

    if isinstance(param, str):
        try:
            with PluginLock(exclusive=False):
                cp = _read_ini(config_path)
                val = _get_val(cp, param)
                if val is not None:
                    return val.strip()
        except TimeoutError:
            _log("Timeout acquiring read lock for site_state")
        except Exception:
            pass
        return "off"

    elif isinstance(param, dict):
        try:
            with PluginLock(exclusive=True):
                dir_path = os.path.dirname(config_path)
                os.makedirs(dir_path, exist_ok=True)
                cp = _read_ini(config_path)
                for key, val in param.items():
                    actual_value = 'on' if val == 'on' else 'off'
                    _set_val(cp, key, actual_value)
                _write_ini(cp, config_path)
        except TimeoutError:
            _log("Timeout acquiring write lock for site_state")
        except Exception as e:
            _log(f"Error writing site_state: {e}")
        return None
    else:
        return None

def read_global_config(params):
    """
    Reads or writes a parameter in the global settings configuration file.
    :param params: dict with keys 'param_name' and optionally 'param_value'.
    :return: param value as string if reading; None otherwise.
    """
    param_name = params.get('param_name')
    if not param_name:
        return None
    param_value = params.get('param_value')
    config_path = "/usr/local/ispmanager-mod_rewrite/settings/global_settings.cfg"

    if param_value is None:
        try:
            with PluginLock(exclusive=False):
                cp = _read_ini(config_path)
                val = _get_val(cp, param_name)
                if val is not None:
                    return val.strip()
        except TimeoutError:
            _log("Timeout acquiring read lock for global config")
        except Exception:
            pass
        return None
    else:
        try:
            with PluginLock(exclusive=True):
                dir_path = os.path.dirname(config_path)
                os.makedirs(dir_path, exist_ok=True)
                cp = _read_ini(config_path)
                _set_val(cp, param_name, param_value)
                _write_ini(cp, config_path)
                if param_name == 'debug':
                    _reset_debug_cache()
        except TimeoutError:
            _log("Timeout acquiring write lock for global config")
        except Exception as e:
            _log(f"Error writing global config: {e}")
        return None

def check_nginx_rewrite_module():
    _log("check_nginx_rewrite_module called")
    if not os.path.exists(CONFIG_PATH):
        _log(f"Config file {CONFIG_PATH} does not exist")
        return False
    try:
        with PluginLock(exclusive=False):
            with open(CONFIG_PATH, 'r') as f:
                for line in f:
                    stripped = line.lstrip()
                    if stripped.startswith('#'):
                        continue
                    if MODULE_PATTERN.match(line):
                        _log("Module found and enabled in config")
                        return True
        _log("Module not found in config")
        return False
    except TimeoutError:
        _log("Timeout acquiring read lock for nginx module check")
        return False
    except Exception as e:
        _log(f"Error reading config file {CONFIG_PATH}: {e}")
        return False

def set_nginx_rewrite_module(enable: bool):
    _log(f"set_nginx_rewrite_module called with enable={enable}")
    if enable and not os.path.isfile(MODULE_SO_PATH):
        _log(f"Refusing to enable module: {MODULE_SO_PATH} does not exist. Build the module first.")
        return False
    try:
        with PluginLock(exclusive=True):
            if enable:
                if not os.path.exists(CONFIG_PATH):
                    atomic_write(CONFIG_PATH, MODULE_LINE + "\n")
                    _log(f"Created config file {CONFIG_PATH} and added module line")
                else:
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
                        atomic_write(CONFIG_PATH, ''.join(lines))
                        _log(f"Wrote changes to config file {CONFIG_PATH}")
            else:
                if not os.path.exists(CONFIG_PATH):
                    _log(f"Config file {CONFIG_PATH} does not exist; nothing to disable")
                    return
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
                    atomic_write(CONFIG_PATH, ''.join(lines))
                    _log(f"Wrote changes to config file {CONFIG_PATH} after disabling module")
                else:
                    _log("No uncommented module line found to disable")
    except TimeoutError:
        _log("Timeout acquiring write lock for nginx module config")
    except Exception as e:
        _log(f"Error processing config file {CONFIG_PATH}: {e}")

_last_template_error = None

def get_last_template_error():
    return _last_template_error

def make_nginx_custom_template() -> bool:
    global _last_template_error
    _last_template_error = None
    tmpl = "/usr/local/mgr5/etc/templates/nginx-vhosts.template"
    tmpl_ssl = "/usr/local/mgr5/etc/templates/nginx-vhosts-ssl.template"

    if os.path.exists(tmpl) or os.path.exists(tmpl_ssl):
        _log("Error: Custom templates already exist. Aborting make_nginx_custom_template.")
        _last_template_error = "Пользовательские шаблоны уже существуют. Удалите их вручную перед повторной активацией."
        return False

    default_dir = "/usr/local/mgr5/etc/templates/default"
    default_tmpl = os.path.join(default_dir, "nginx-vhosts.template")
    default_ssl_tmpl = os.path.join(default_dir, "nginx-vhosts-ssl.template")
    target_dir = "/usr/local/mgr5/etc/templates"

    def cleanup_templates():
        for f in (tmpl, tmpl_ssl):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass

    try:
        with PluginLock(exclusive=True):
            try:
                if os.path.exists(default_tmpl):
                    shutil.copy2(default_tmpl, target_dir)
                if os.path.exists(default_ssl_tmpl):
                    shutil.copy2(default_ssl_tmpl, target_dir)
            except Exception as e:
                _log(f"Error copying default templates: {e}")
                cleanup_templates()
                _last_template_error = f"Ошибка копирования шаблонов: {e}"
                return False

            if not os.path.exists(tmpl) or not os.path.exists(tmpl_ssl):
                _log("Error: Failed to copy one or both template files.")
                cleanup_templates()
                _last_template_error = "Не удалось скопировать один или оба файла шаблонов из директории default."
                return False

            patches_dir = "/usr/local/ispmanager-mod_rewrite/patches"
            patch_tmpl = os.path.join(patches_dir, "nginx-vhosts.template.patch")
            patch_ssl = os.path.join(patches_dir, "nginx-vhosts-ssl.template.patch")

            patch_errors = []
            for target, patch_file in ((tmpl, patch_tmpl), (tmpl_ssl, patch_ssl)):
                if not os.path.exists(patch_file):
                    msg = f"Файл патча {os.path.basename(patch_file)} не найден."
                    _log(msg)
                    patch_errors.append(msg)
                    continue
                try:
                    result = subprocess.run(
                        ["patch", "-p1", target, patch_file],
                        cwd=target_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    if result.returncode != 0:
                        stderr_text = result.stderr.decode(errors='ignore').strip()
                        msg = f"Патч {os.path.basename(patch_file)} не применился к шаблону ISPmanager. Возможно, версия панели изменилась. Детали: {stderr_text}"
                        _log(f"Patch failed for {target}: {stderr_text}")
                        patch_errors.append(msg)
                except Exception as e:
                    msg = f"Ошибка применения патча {os.path.basename(patch_file)}: {e}"
                    _log(msg)
                    patch_errors.append(msg)

            if patch_errors:
                _log("Error: One or more patches failed. Removing copied template files.")
                cleanup_templates()
                _last_template_error = '; '.join(patch_errors)
                return False

    except TimeoutError:
        _log("Timeout acquiring write lock for template creation")
        cleanup_templates()
        _last_template_error = "Не удалось получить блокировку для создания шаблонов (таймаут)."
        return False
    except Exception as e:
        _log(f"Unexpected error in make_nginx_custom_template: {e}")
        cleanup_templates()
        _last_template_error = f"Неожиданная ошибка при создании шаблонов: {e}"
        return False

    read_global_config({'param_name': 'templates_created', 'param_value': 'on'})
    _log("Success: Custom Nginx templates created and patched successfully.")
    return True

if __name__ == "__main__":
    root = etree.parse(stdin).getroot()
    func = os.getenv('PARAM_func')
    user_level = os.getenv('AUTH_LEVEL', '2')

    _log(f"Function called: {func}, User level: {user_level}")
    if int(user_level) < 16:
        _log("User level below user; returning access error in document")
        append_error_to_doc(root, 'access_denied', 'form', 'Доступ запрещён: требуется уровень пользователя или выше')
        etree.dump(root)
        sys.exit(0)

    if func == 'nginx_mod_rewrite_plugin_menu_settings':
        if int(user_level) < 29:
            _log("User level below admin; returning access error in document")
            append_error_to_doc(root, 'access_denied', 'form', 'Доступ запрещён: требуется уровень администратора')
            etree.dump(root)
            sys.exit(0)

        nginx_mod_rewrite_plugin_menu_module_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_menu_module_enable")
        nginx_mod_rewrite_plugin_menu_debug_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_menu_debug_enable")
        mod_enabled = check_nginx_rewrite_module()
        _log(f"Current module state: {'enabled' if mod_enabled else 'disabled'}")
        mod_rewrite_global = read_global_config({'param_name': 'global_mod_rewrite_enable'})
        debug = read_global_config({'param_name': 'debug'})

        if os.getenv('PARAM_clicked_button') == 'ok' and nginx_mod_rewrite_plugin_menu_module_enable is not None:
            if nginx_mod_rewrite_plugin_menu_module_enable == "on" and not mod_enabled:
                enable_result = set_nginx_rewrite_module(True)
                if enable_result is False:
                    append_error_to_doc(root, 'module_not_built', 'nginx_mod_rewrite_plugin_menu_module_enable',
                        'Модуль не собран. Сначала соберите модуль через раздел «Модули» → «Установка модуля для nginx».')
                    new_element = etree.Element("nginx_mod_rewrite_plugin_menu_module_enable")
                    new_element.text = "off"
                    root.append(new_element)
                else:
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
                    template_err = get_last_template_error()
                    if template_err:
                        append_error_to_doc(root, 'template_error', 'nginx_mod_rewrite_plugin_menu_global_module_enable', template_err)
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
                    read_global_config({'param_name': 'templates_created', 'param_value': 'off'})
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
        sok = os.environ.get('PARAM_sok', '')
        event_type = os.getenv("EVENT_TYPE")
        domain = os.environ.get('PARAM_elid', '')
        _log(f"webdomain.edit handler: sok={sok}, event_type={event_type}, domain={domain}")
        if sok == 'ok' and event_type == 'after':
            path_to_global_config = f'/etc/nginx/vhosts-resources/{domain}'
            nginx_mod_rewrite_plugin_site_edit_enable = os.getenv("PARAM_nginx_mod_rewrite_plugin_site_edit_enable")
            mod_rewrite_global = read_global_config({'param_name': 'global_mod_rewrite_enable'})
            if nginx_mod_rewrite_plugin_site_edit_enable == "on":
                if mod_rewrite_global in ("1", "on"):
                    _log("Template mode enabled, no include file needed")
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
                            atomic_write(mod_file_path, "GlobalLocationRewriteEngine on;\nHtaccessEnable on;\n")
                            _log(f"Created/overwritten mod_rewrite.conf at {mod_file_path}")
                        except Exception as e:
                            _log(f"Failed to write mod_rewrite.conf at {mod_file_path}: {e}")
                    else:
                        _log(f"Path {path_to_global_config} is not a directory or does not exist")
                site_state({domain: "on"})
            else:
                _log("mod_rewrite should be disabled")
                if mod_rewrite_global not in ("1", "on"):
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
                site_state({domain: "off"})
        else:
            if domain != "":
                domain_state = site_state(domain)
                if domain_state == "on":
                    new_element = etree.Element("nginx_mod_rewrite_plugin_site_edit_enable")
                    new_element.text = "on"
                    root.append(new_element)
    elif func == "site.edit":
        sok = os.environ.get('PARAM_sok', '')
        event_type = os.getenv("EVENT_TYPE")
        domain = os.environ.get('PARAM_elid', '')
        _log(f"site.edit handler: sok={sok}, event_type={event_type}, domain={domain}")
        if sok == 'ok' and event_type == 'after':
            nginx_mod_rewrite_plugin_site_edit_enable = os.getenv("PARAM_site_nginx_mod_rewrite_plugin_site_edit_enable")
            _log(f"site.edit save: checkbox={nginx_mod_rewrite_plugin_site_edit_enable}")
            if domain:
                if nginx_mod_rewrite_plugin_site_edit_enable == "on":
                    site_state({domain: "on"})
                else:
                    site_state({domain: "off"})
            else:
                _log("site.edit: PARAM_elid is empty, skipping save")
        else:
            if domain != "":
                domain_state = site_state(domain)
                if domain_state == "on":
                    new_element = etree.Element("site_nginx_mod_rewrite_plugin_site_edit_enable")
                    new_element.text = "on"
                    root.append(new_element)

    etree.dump(root)
    sys.exit(0)
