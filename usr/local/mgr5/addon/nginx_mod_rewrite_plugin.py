#!/usr/bin/env python3

import os
import xml.etree.ElementTree as etree
from xml.dom import minidom
from sys import stdin
import subprocess
import datetime

def dump_all_to_log(root):
    log_path = '/usr/local/mgr5/var/nginx-mod-rewrite-plugin.log'
    try:
        with open(log_path, 'a') as log_file:
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

def CheckModuleExists():
    module_path = '/usr/share/nginx/modules/ngx_http_apache_rewrite_module.conf'
    if os.path.isfile(module_path):
        return 'mod_rewrite already installed'
    return None

def BuildModule():
    """
    Build and install the nginx mod_rewrite module by calling the utility script.

    Returns:
        str: Error message if the utility failed, None on success
    """
    utils_dir = '/usr/local/ispmanager-mod_rewrite/utils'
    build_script = os.path.join(utils_dir, 'build_mod_rewrite.py')
    log_path = '/usr/local/mgr5/var/nginx-mod-rewrite-plugin.log'

    try:
        with open(log_path, 'a') as log_file:
            log_file.write(f'=== Starting module build via utility: {datetime.datetime.now()} ===\n')
            log_file.write(f'Executing: python3 {build_script}\n')

            result = subprocess.run(
                ['python3', build_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            # Log the result
            log_file.write(f'Build script exit code: {result.returncode}\n')
            if result.stdout:
                log_file.write(f'Stdout:\n{result.stdout}')

        if result.returncode != 0:
            # Extract error message from stderr (already captured in stdout due to redirect)
            return f'Build utility failed with exit code {result.returncode}'

        installed_cfg_path = '/usr/local/ispmanager-mod_rewrite/settings/installed.cfg'
        with open(installed_cfg_path, 'w') as cfg_file:
            cfg_file.write('')
        os.chmod(installed_cfg_path, 0o600)
    except Exception as exc:
        log_path_error = os.path.join(os.path.dirname(log_path), 'error.log')
        with open(log_path_error, 'a') as error_file:
            error_file.write(f'Unexpected error calling build utility: {exc}\n')
        return f'Unexpected error: {exc}'

    return None

def InstallModule():
    result = BuildModule()
    if result is not None:
       return result
    return None

# Обработка нажатия на кнопку "Сохранить"
def Handle(root):
    result = True
    result_error = CheckModuleExists()
    if result_error is None:
        for parent in root.iter():
            for child in list(parent):
                if child.tag == 'textdata' and child.get('name') == 'nginx_mod_rewrite_plugin_msg_info':
                    parent.remove(child)
        result_error = InstallModule()
        result = result_error is None

        if not result:
            # Формирование сообщения об ошибке согласно структуре в metadata
            error = etree.SubElement(root, "error")
            error.set('code', '1')
            error.set('type', 'nginx_mod_rewrite_plugin')
            error.set('object', 'wrong_value')
            param = etree.SubElement(error, 'param')
            param.text = 'wrong_value'
            param.set('name', 'object')
            param.set('type', 'msg')
            param = etree.SubElement(error, 'param')
            param.set('name', 'value')
            param.text = result_error
        else:
            # Add <ok/> element to root
            etree.SubElement(root, 'ok')
    else:
        # Remove textdata element with name="nginx_mod_rewrite_plugin_msg"
        for parent in root.iter():
            for child in list(parent):
                if child.tag == 'textdata' and child.get('name') == 'nginx_mod_rewrite_plugin_msg':
                    parent.remove(child)
        for buttons in root.findall(".//buttons"):
            for btn in list(buttons):
                if btn.tag == 'button' and btn.get('name') == 'ok':
                    buttons.remove(btn)


if __name__ == "__main__":
    func = os.getenv("PARAM_func")
    correct_function = False

    if func == "nginx_mod_rewrite_plugin.settings":
        correct_function = True
    elif func == "plugin.settings":
        if os.getenv("PARAM_plugin_settings") == "nginx_mod_rewrite_plugin":
            correct_function = True
    root = etree.parse(stdin).getroot()

    if correct_function:
        if os.getenv("PARAM_clicked_button") == "ok":
            Handle(root)
        else:
            result_error = CheckModuleExists()
            if result_error is None:
                for parent in root.iter():
                    for child in list(parent):
                        if child.tag == 'textdata' and child.get('name') == 'nginx_mod_rewrite_plugin_msg_info':
                            parent.remove(child)
            else:
                # Remove textdata element with name="nginx_mod_rewrite_plugin_msg"
                for parent in root.iter():
                    for child in list(parent):
                        if child.tag == 'textdata' and child.get('name') == 'nginx_mod_rewrite_plugin_msg':
                            parent.remove(child)

                for buttons in root.findall(".//buttons"):
                    for btn in list(buttons):
                        if btn.tag == 'button' and btn.get('name') == 'ok':
                            buttons.remove(btn)

    etree.dump(root)
    quit(0)
