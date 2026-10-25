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
| `generate-secret-key.ps1` | Generate a secure `SECRET_KEY` and update the unified `.env` on Windows. | Keep |
| `generate-secret-key.sh` | Generate a secure `SECRET_KEY` and update the unified `.env` on macOS/Linux. | Keep |
| `bootstrap_env.py` | Create or safely upgrade `.env`, generate missing credentials, and preserve existing database access. | Keep |
| `sync_env.py` | Add missing keys from `.env.example` without overwriting existing values. | Keep |
| `bump_version.py` | Update the repo-root `VERSION` and `backend_api_python/VERSION` fallback files. | Keep |
| `check_docs.py` | Validate documentation structure, local links, code fences, and referenced assets. | Keep |
| `check_mojibake.py` | Detect common text-encoding corruption in tracked files. | Keep |
| `check_version.py` | Verify version fallback files stay aligned; used by CI. | Keep |

Removed frontend-only scripts should be recreated or moved in the frontend
repository if that workflow is still needed.
