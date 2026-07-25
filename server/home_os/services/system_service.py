import os
import subprocess


def privileged_command(command):
    command = list(command)
    if os.geteuid() != 0:
        raise PermissionError("Home OS backend must run as root for system administration")
    return command


def run_privileged(command, **kwargs):
    return subprocess.run(privileged_command(command), **kwargs)
