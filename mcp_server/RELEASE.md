# Release quantdinger-mcp to PyPI

## This release: 0.2.0

```powershell
cd mcp_server

# 1. Install build tools (once)
pip install -e ".[dev]"

# 2. Tests
python -m pytest tests/ -q

# 3. Clean old artifacts (optional)
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force src\quantdinger_mcp.egg-info -ErrorAction SilentlyContinue

# 4. Build
python -m build

# 5. Upload (you run this — needs your PyPI token)
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-Ag..."   # your API token
python -m twine upload dist/quantdinger_mcp-0.2.0*

# 6. Verify
pip install --upgrade "quantdinger-mcp==0.2.0"
quantdinger-mcp
```

Linux / macOS upload:

```bash
cd mcp_server
pip install -e ".[dev]"
python -m pytest tests/ -q
rm -rf dist build src/*.egg-info
python -m build
TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... python -m twine upload dist/quantdinger_mcp-0.2.0*
```

## Notes

- Upload **only** the `0.2.0` files from `dist/` — do not upload older versions again.
- PyPI token: Account settings → API tokens → scope `quantdinger-mcp` or entire account.
- After publish: restart Cursor MCP or `pip install --upgrade quantdinger-mcp`.
