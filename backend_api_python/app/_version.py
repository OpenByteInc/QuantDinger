"""Single source of truth for the backend's app version string.

This constant mirrors the repo-root ``VERSION`` file. Both are kept in sync by
``scripts/bump_version.py`` (and verified by ``scripts/check_version.py`` in
CI). Do **not** edit by hand — run the bump script instead.
"""

APP_VERSION = "3.0.22"
