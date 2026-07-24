#!/usr/bin/env python3
"""
Utility for building and installing nginx mod_rewrite module.
This script handles downloading, extracting, building, and installing the module.
Returns None on success, or error message string on failure.
"""

import os
import sys
import shutil
import subprocess
import zipfile
import datetime
import re


def _get_nginx_version(log_file):
    """
    Detect nginx version from /usr/sbin/nginx -V output.

    Args:
        log_file: File object to write log messages to

    Returns:
        str or None: Version string (e.g., '1.30.2') if found, None otherwise
    """
    nginx_binary = '/usr/sbin/nginx'
    if not os.path.exists(nginx_binary):
        log_file.write('Nginx binary not found at /usr/sbin/nginx, using default version detection.\n')
        return None

    try:
        result = subprocess.run([nginx_binary, '-V'], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log_file.write(f'Failed to run nginx -V with exit code {result.returncode}\n')
            if result.stderr:
                log_file.write(f'nginx stderr: {result.stderr.strip()}\n')
            return None

        log_file.write(f'nginx stdout:\n{result.stderr.strip()}\n')

        # First line contains "nginx version: nginx/X.Y.Z"
        first_line = result.stderr.strip().split('\n')[0]

        # Parse the version from the first line
        # Expected format: "nginx version: nginx/1.30.2 ..."
        match = re.search(r'nginx/(\d+\.\d+\.?\d*)', first_line)
        if match:
            version = match.group(1)
            log_file.write(f'Detected nginx version: {version}\n')
            return version
        else:
            log_file.write(f'Could not parse nginx version from: {first_line}\n')
            return None
    except subprocess.TimeoutExpired:
        log_file.write('nginx -V command timed out.\n')
        return None
    except Exception as e:
        log_file.write(f'Error running nginx -V: {e}\n')
        return None


def build_mod_rewrite():
    """
    Build and install the nginx mod_rewrite module.

    Returns:
        str: Error message on failure, None on success
    """
    tmp_dir = '/tmp/nginx-mod-rewrite'
    log_path = '/usr/local/mgr5/var/nginx-mod-rewrite-plugin.log'

    # Check for --nodeps argument - skip installdeps stage if provided
    nodeps = False
    args = sys.argv[1:]
    if '--nodeps' in args:
        nodeps = True

    try:
        # Ensure log directory exists
        log_dir = os.path.dirname(log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # Setup temporary directory: create or clean
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)

        with open(log_path, 'a') as log_file:
            log_file.write(f'=== Starting module download and install: {datetime.datetime.now()} ===\n')

            # Copy the ZIP file into the temporary directory
            src_zip = '/usr/local/ispmanager-mod_rewrite/mod_rewrite.zip'
            dst_zip = os.path.join(tmp_dir, 'mod_rewrite.zip')
            try:
                shutil.copy(src_zip, dst_zip)
                log_file.write(f'Copied zip to {dst_zip}\n')
            except Exception as e:
                return f'Failed to copy zip: {e}'

            # Extract the ZIP file
            try:
                with zipfile.ZipFile(dst_zip, 'r') as zip_ref:
                    zip_ref.extractall(tmp_dir)
                log_file.write('Extracted zip contents.\n')
            except Exception as e:
                return f'Failed to extract zip: {e}'

            # Locate the extracted root directory
            extracted_root = None
            for entry in os.listdir(tmp_dir):
                full_path = os.path.join(tmp_dir, entry)
                if os.path.isdir(full_path):
                    extracted_root = full_path
                    break
            if not extracted_root:
                return 'Extracted folder not found.'
            log_file.write(f'Using extracted root: {extracted_root}\n')

            # Define the sequence of commands to run
            if nodeps:
                commands = []
            else:
                commands = [['bash', 'package_preparer.sh', 'installdeps']]

            # Detect nginx version and decide on download target
            nginx_version_to_use = '.'  # Default fallback

            detected_version = _get_nginx_version(log_file)
            if detected_version is not None:
                nginx_version_to_use = detected_version
                log_file.write(f'Using detected nginx version: {nginx_version_to_use} for download\n')
            else:
                log_file.write('Nginx version detection failed, using default (.).\n')

            if nginx_version_to_use == '.':
                commands.extend([
                    ['bash', 'package_preparer.sh', 'download', nginx_version_to_use, 'system']
                ])
            else:
                commands.extend([
                    ['bash', 'package_preparer.sh', 'download', nginx_version_to_use]
                ])

            commands.extend([
                ['bash', 'package_preparer.sh', 'build'],
                ['bash', 'package_preparer.sh', 'installmod']
            ])

            for cmd in commands:
                log_file.write(f'Running command: {" ".join(cmd)}\n')
                result = subprocess.run(cmd, cwd=extracted_root, stdout=log_file, stderr=log_file)
                if result.returncode != 0:
                    return f'Command {" ".join(cmd)} failed with exit code {result.returncode}'

            log_file.write('All commands executed successfully.\n')
    except Exception as exc:
        return f'Unexpected error: {exc}'
    finally:
        # Clean up temporary directory
        try:
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir)
        except Exception:
            pass

    return None


if __name__ == '__main__':
    # Run the build process and output result to stdout
    result = build_mod_rewrite()
    if result is not None:
        # On error, write error message to stderr
        print(result, file=sys.stderr)
        sys.exit(1)
    else:
        # On success, exit with 0 (no output)
        sys.exit(0)
