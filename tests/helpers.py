"""Shared helpers for the shell tests: finds a bash 4+ (macOS ships 3.2)."""

import shutil
import subprocess


def find_bash():
    for cand in ("/opt/homebrew/bin/bash", shutil.which("bash")):
        if not cand:
            continue
        out = subprocess.run([cand, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip().isdigit() and int(out.stdout) >= 4:
            return cand
    return None


BASH = find_bash()
