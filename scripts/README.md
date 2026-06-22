# Repository Scripts

These scripts are backend and repository maintenance helpers. Frontend build and
i18n tooling belongs in the separate `QuantDinger-Vue` repository.

The end-user installers live at the repository root:

| Script | Purpose |
| --- | --- |
| `../install.sh` | Interactive Linux/macOS one-command installer. |
| `../install.ps1` | Interactive Windows PowerShell one-command installer. |

| Script | Purpose | Status |
| --- | --- | --- |
| `generate-secret-key.ps1` | Generate a secure `SECRET_KEY` and update `backend_api_python/.env` on Windows. | Keep |
| `generate-secret-key.sh` | Generate a secure `SECRET_KEY` and update `backend_api_python/.env` on macOS/Linux. | Keep |
| `bump_version.py` | Update the repo-root `VERSION` and `backend_api_python/VERSION` fallback files. | Keep |
| `check_version.py` | Verify version fallback files stay aligned; used by CI. | Keep |

Removed frontend-only scripts should be recreated or moved in the frontend
repository if that workflow is still needed.
