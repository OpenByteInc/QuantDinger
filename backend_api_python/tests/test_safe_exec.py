"""Sandbox static validation and known escape regression tests."""

from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation, validate_code_safety

# Classic subclass-chain escape (CVE-class pattern for restricted exec).
_SUBCLASS_ESCAPE = """
import operator
ag = operator.attrgetter
cls = ag('__' + 'class__')('')
bases = ag('__' + 'bases__')(cls)
subs = ag('__' + 'subclasses__')(bases[0])()
os_mod = None
for s in subs:
    try:
        init = ag('__' + 'init__')(s)
        globs = ag('__' + 'globals__')(init)
        if globs and 'os' in globs:
            os_mod = globs['os']
            break
    except Exception:
        pass
output = {'plots': [], 'signals': [], 'marker': os_mod.getcwd() if os_mod else None}
"""

_LEGIT_INDICATOR = """
my_indicator_name = "T"
df = df.copy()
output = {'plots': [], 'signals': []}
"""


def test_subclass_escape_rejected_by_validator():
    ok, err = validate_code_safety(_SUBCLASS_ESCAPE)
    assert ok is False
    assert err


def test_subclass_escape_rejected_at_exec():
    env = {
        '__builtins__': build_safe_builtins(),
        'output': None,
        'df': None,
        'params': {},
    }
    result = safe_exec_with_validation(_SUBCLASS_ESCAPE, env, env, timeout=5, pre_import='')
    assert result['success'] is False


def test_legit_indicator_passes_validator():
    ok, err = validate_code_safety(_LEGIT_INDICATOR)
    assert ok is True
    assert err is None


def test_operator_import_rejected():
    ok, _ = validate_code_safety("import operator\noutput = {}")
    assert ok is False
