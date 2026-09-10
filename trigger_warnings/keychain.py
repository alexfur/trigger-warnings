"""Optional credential storage in the operating system's own keychain.

This is the one concession to convenience in a design that otherwise reads
credentials from the environment and nowhere else, and it is shaped so that the
property that mattered survives it.

What that property actually defended against was an *ambient* credential file:
some other application's key, in a location this tool does not own, read without
anyone asking for it. A keychain entry under this tool's own service name is not
that. The user put it there deliberately, the operating system holds it
encrypted under their login, and nothing outside this namespace is ever read.

So the rule becomes: the environment wins, this keychain is the fallback, and
there is no third source. Hydration happens once, at startup, by putting a
stored value into this process's own environment. Every module downstream still
reads the environment and nothing else, which keeps that statement literally
true rather than merely nearly true.

No credential is passed as a command line argument, because argv is visible to
other processes owned by the same user. Values are written over a pipe instead.
"""

import os
import platform
import shutil
import subprocess


#: One namespace this tool owns. The account is the variable name, so the
#: mapping needs no table and cannot drift out of step with the environment.
SERVICE = "trigger-warnings"

#: Every credential the tool understands, in the order a report should show
#: them. Kept here so the wizard, the hydrator and --save cannot disagree.
VARIABLES = (
    "DDD_API_KEY",
    "OPENSUBTITLES_API_KEY",
    "OPENSUBTITLES_USERNAME",
    "OPENSUBTITLES_PASSWORD",
    "GEMINI_API_KEY",
)

MACOS = "macos"
LIBSECRET = "libsecret"

_TIMEOUT_S = 20


def backend(which=None, system=None):
    """Which store is usable here, or None.

    Windows is deliberately unsupported rather than half supported: it has no
    dependency-free command line equivalent, and a fallback that wrote a file
    would give back exactly the risk this module was careful to avoid.
    """
    which = which or shutil.which
    system = system or platform.system
    if system() == "Darwin" and which("security"):
        return MACOS
    if which("secret-tool"):
        return LIBSECRET
    return None


def _run(command, stdin_text=None):
    try:
        completed = subprocess.run(
            command,
            input=stdin_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed


def get(variable, store=None):
    """Return a stored value, or None when there is not one.

    A missing entry, an unusable backend and a user who refused the operating
    system's access prompt are all "no value here": each one means the caller
    must look elsewhere, and none of them is worth failing a run over.
    """
    store = store or backend()
    if store == MACOS:
        completed = _run(["security", "find-generic-password",
                          "-a", variable, "-s", SERVICE, "-w"])
    elif store == LIBSECRET:
        completed = _run(["secret-tool", "lookup",
                          "service", SERVICE, "account", variable])
    else:
        return None
    if completed is None or completed.returncode != 0:
        return None
    value = (completed.stdout or "").rstrip("\n")
    return value or None


def store_value(variable, value, store=None):
    """Write one credential, replacing any existing entry.

    The value goes over stdin, never in the argument list. macOS security asks
    for the password twice when it reads from a pipe, so it is sent twice.
    """
    store = store or backend()
    if not value:
        return False
    if store == MACOS:
        completed = _run(
            ["security", "add-generic-password",
             "-a", variable, "-s", SERVICE, "-U",
             "-l", "{} ({})".format(SERVICE, variable), "-w"],
            stdin_text="{0}\n{0}\n".format(value),
        )
    elif store == LIBSECRET:
        completed = _run(
            ["secret-tool", "store", "--label",
             "{} ({})".format(SERVICE, variable),
             "service", SERVICE, "account", variable],
            stdin_text=value + "\n",
        )
    else:
        return False
    return completed is not None and completed.returncode == 0


def forget(variable, store=None):
    """Remove one credential. Absent already counts as removed."""
    store = store or backend()
    if store == MACOS:
        completed = _run(["security", "delete-generic-password",
                          "-a", variable, "-s", SERVICE])
    elif store == LIBSECRET:
        completed = _run(["secret-tool", "clear",
                          "service", SERVICE, "account", variable])
    else:
        return False
    return completed is not None and completed.returncode == 0


ENVIRONMENT = "environment"
KEYCHAIN = "keychain"


def hydrate(environ=None, store=None, variables=VARIABLES):
    """Fill blank credentials from the keychain, and say where each came from.

    Returns a mapping of variable name to source for the ones that ended up
    set. A variable already present in the environment is never overwritten:
    an explicit export is the caller saying which credential to use for this
    run, and a stored value must not silently win against it.
    """
    environ = os.environ if environ is None else environ
    store = backend() if store is None else store
    sources = {}
    for variable in variables:
        if (environ.get(variable) or "").strip():
            sources[variable] = ENVIRONMENT
            continue
        if not store:
            continue
        value = get(variable, store=store)
        if value:
            environ[variable] = value
            sources[variable] = KEYCHAIN
    return sources


def describe(store=None):
    """A short phrase naming the backend, for a human-readable report."""
    store = backend() if store is None else store
    if store == MACOS:
        return "the macOS keychain"
    if store == LIBSECRET:
        return "the libsecret keyring"
    return None
