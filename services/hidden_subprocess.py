"""Keep background helpers from opening Windows console windows."""

import os
import subprocess as _subprocess


def _options(kwargs):
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
    return kwargs


def run(*args, **kwargs):
    return _subprocess.run(*args, **_options(kwargs))


def Popen(*args, **kwargs):
    return _subprocess.Popen(*args, **_options(kwargs))


def __getattr__(name):
    return getattr(_subprocess, name)
