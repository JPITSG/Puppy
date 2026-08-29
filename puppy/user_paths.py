"""Linux account paths shared by the runtime and engine drivers."""
from __future__ import annotations

import os
import pwd


def service_home() -> str:
    """Return the configured service home, then the account database home.

    Service managers commonly provide HOME explicitly so engine credentials
    stay attached to the intended Unix account. A missing or relative value
    must not silently redirect an arbitrary service user into root's home.
    """
    configured = str(os.environ.get("HOME") or "")
    if configured and os.path.isabs(configured):
        return os.path.normpath(configured)
    try:
        account_home = str(pwd.getpwuid(os.geteuid()).pw_dir or "")
    except (KeyError, OSError):
        account_home = ""
    if account_home and os.path.isabs(account_home):
        return os.path.normpath(account_home)
    return "/"
