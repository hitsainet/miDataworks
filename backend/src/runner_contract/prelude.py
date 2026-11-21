"""The guest's own code, as contract text (FTDD 018 §4.4; guest.prelude_source, guest.shim_source).

Both strings are published in ``midataworks-runner-v1.json`` and their SHA-256s are part of the
bundle digest. The backend never executes either (FR-018.18): ``PRELUDE_SOURCE`` runs only inside
the WebAssembly guest, once, at pre-initialisation (``wasmtime wizer``), and ``SHIM_SOURCE`` is the C
entry module linked with ``libpython`` into the reactor the snapshot is taken from
(ENH-006 D41, S0 correction: the stock ``python.wasm`` cannot be pre-initialised).

What a call sees (each call is a fresh instance of the snapshot): the prelude reads one request
from standard input, writes the ``load`` phase marker to file descriptor 3, compiles the source and
refuses statically named capabilities, executes the module under restricted builtins, writes the
``call`` marker, calls the function with ``(text, params)`` and writes ONE reply line to standard
output. The Python-level checks name exact reasons; they are NOT the barrier (the WebAssembly
boundary is: no preopened directory, an empty environment, a fixed clock and random stream).

Standard library only: the runner image imports this module.
"""

from __future__ import annotations

import hashlib

PRELUDE_SOURCE = '''\
# midataworks.runner/v1 guest prelude. Runs once at pre-initialisation; every call is a fresh
# instance of the resulting snapshot and enters _runner_main.
import sys

sys.setrecursionlimit(1000)

import builtins
import json
import os

import bisect
import collections
import decimal
import fractions
import functools
import heapq
import itertools
import math
import operator
import re
import statistics
import string
import unicodedata

_ALLOWED = frozenset((
    "bisect", "collections", "decimal", "fractions", "functools", "heapq", "itertools", "json",
    "math", "operator", "re", "statistics", "string", "unicodedata",
))
_REMOVED_BUILTINS = frozenset((
    "__import__", "breakpoint", "compile", "copyright", "credits", "eval", "exec", "exit",
    "globals", "help", "input", "license", "locals", "open", "quit", "vars",
))
# The introspection and frame attributes that reach past the restricted builtins (the escape path).
# Plain builtins (open, eval, exec, compile, globals, ...) are simply absent from _SAFE, so naming
# one is a NameError (rule_error); refusing them statically too would catch legitimate attribute
# names such as re.compile. This set must equal constants.STATIC_REFUSALS (a contract test pins it).
_REFUSED_NAMES = frozenset((
    "__base__", "__bases__", "__builtins__", "__class__", "__closure__", "__code__", "__dict__",
    "__getattribute__", "__globals__", "__import__", "__loader__", "__mro__", "__reduce__",
    "__reduce_ex__", "__spec__", "__subclasses__", "ag_frame", "cr_frame", "f_back", "f_builtins",
    "f_globals", "f_locals", "gi_code", "gi_frame", "tb_frame", "tb_next",
))

_write = os.write
_read = os.read
_dumps = json.dumps
_loads = json.loads
_compile = compile
_exec = exec
_real_import = builtins.__import__
_code_type = type(_compile("0", "<p>", "eval"))
_function_type = type(lambda: 0)
_int = int
_str = str
_type = type
_sorted = sorted
_isinstance = isinstance
_frozenset = frozenset
_len = len
_RULE = "<rule>"
_state = {"refused": None, "imports": set()}


class _Discard:
    """print() and the standard streams write nowhere: only the reply reaches fd 1."""

    def write(self, s):
        return _len(s)

    def flush(self):
        return None


sys.stdout = _Discard()
sys.stderr = _Discard()


def _checked_import(name, globals=None, locals=None, fromlist=(), level=0):
    top = name.partition(".")[0]
    if level != 0 or top not in _ALLOWED:
        if _state["refused"] is None:
            _state["refused"] = name
        raise ImportError("import of %r refused" % (name,))
    _state["imports"].add(top)
    return _real_import(name, globals, locals, fromlist, level)


_SAFE = {k: getattr(builtins, k) for k in dir(builtins) if k not in _REMOVED_BUILTINS}
_SAFE["__import__"] = _checked_import


def _static_refusal(code):
    stack = [code]
    while stack:
        c = stack.pop(0)
        for name in c.co_names:
            if name in _REFUSED_NAMES:
                return name
        for const in c.co_consts:
            if _type(const) is _code_type:
                stack.append(const)
    return None


def _diag(exc, wanted):
    if not wanted:
        return None
    line = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == _RULE:
            line = tb.tb_lineno
        tb = tb.tb_next
    if line is None and _isinstance(exc, SyntaxError) and exc.filename == _RULE:
        line = exc.lineno
    try:
        message = _str(exc)[:200]
    except BaseException:
        message = ""
    return {"exception_type": _type(exc).__name__[:100], "line": line, "message": message}


def _reply(obj):
    _write(1, _dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode("ascii") + b"\\n")


def _fail(phase, kind, exc=None, wanted=False, refused=None):
    result = {"fail": kind}
    if refused is not None:
        result["refused"] = refused
    d = _diag(exc, wanted) if exc is not None else None
    if d is not None:
        result["diag"] = d
    _reply({"phase": phase, "result": result})


def _failure_kind(exc):
    if _isinstance(exc, MemoryError):
        return "memory"
    if _isinstance(exc, RecursionError):
        return "recursion"
    return "error"


def _runner_main():
    chunks = []
    while True:
        chunk = _read(0, 1 << 20)
        if not chunk:
            break
        chunks.append(chunk)
    request = _loads(b"".join(chunks))
    mode = request["mode"]
    wanted = request["diagnostics"] is True
    _write(3, b"load")
    try:
        code = _compile(request["source"], _RULE, "exec")
    except BaseException as exc:
        return _fail("load", _failure_kind(exc), exc, wanted)
    refused = _static_refusal(code)
    if refused is not None:
        return _fail("load", "load_refused", refused=refused)
    module = {"__builtins__": _SAFE, "__name__": "rule"}
    try:
        _exec(code, module)
    except BaseException as exc:
        if _state["refused"] is not None:
            return _fail("load", "load_refused", refused=_state["refused"])
        return _fail("load", _failure_kind(exc), exc, wanted)
    if _state["refused"] is not None:
        return _fail("load", "load_refused", refused=_state["refused"])
    if mode == "inspect":
        functions = _sorted(
            k for k, v in module.items()
            if _type(v) is _function_type and v.__code__.co_filename == _RULE
        )
        return _reply({"phase": "load", "inspect": {
            "functions": functions, "imports": _sorted(_state["imports"])}})
    function = module.get(request["function"])
    if _type(function) is not _function_type or function.__code__.co_filename != _RULE:
        return _fail("load", "error", NameError("function %r is not exported" % (request["function"],)), wanted)
    _write(3, b"call")
    try:
        value = function(request["text"], request["params"])
    except BaseException as exc:
        if _state["refused"] is not None:
            return _fail("call", "load_refused", refused=_state["refused"])
        return _fail("call", _failure_kind(exc), exc, wanted)
    if _state["refused"] is not None:
        return _fail("call", "load_refused", refused=_state["refused"])
    kind = _type(value)
    if kind is _int:
        if value < 0:
            result = {"t": "int", "v": "negative"}
        elif value >= (1 << 63):
            result = {"t": "int", "v": "overflow"}
        else:
            result = {"t": "int", "v": _str(value)}
    elif kind is bool:
        result = {"t": "bool"}
    elif kind is float:
        result = {"t": "float"}
    elif kind is _str:
        result = {"t": "str"}
    elif kind is bytes:
        result = {"t": "bytes"}
    elif value is None:
        result = {"t": "none"}
    else:
        result = {"t": "other"}
    _reply({"phase": "call", "result": result})


import random as _random

_random.seed(0)
del _random
'''

SHIM_SOURCE = r"""/* midataworks.runner/v1 guest shim: the reactor entry points around libpython (ENH-006 D41).
 * wizer.initialize runs once under `wasmtime wizer` (interpreter start + the prelude); run is
 * the entry point every call enters on a fresh instance of the snapshot. */
#include <Python.h>
#include <time.h>

extern void __wasm_call_ctors(void);
static PyObject *runner_main = NULL;

/* Table W's fixed instant inside the guest: every libc clock reads the Unix epoch. */
int clock_gettime(clockid_t id, struct timespec *ts) {
    (void)id;
    if (ts) { ts->tv_sec = 0; ts->tv_nsec = 0; }
    return 0;
}

__attribute__((export_name("wizer.initialize")))
void runner_initialize(void) {
    __wasm_call_ctors();
    PyPreConfig pre;
    PyPreConfig_InitIsolatedConfig(&pre);
    pre.utf8_mode = 1;
    Py_PreInitialize(&pre);
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.site_import = 0;
    config.write_bytecode = 0;
    config.use_hash_seed = 1;
    config.hash_seed = 0;
    config.install_signal_handlers = 0;
    config.buffered_stdio = 0;
    PyConfig_SetString(&config, &config.home, L"/");
    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) { Py_ExitStatusException(status); }
    FILE *fh = fopen("/prelude/prelude.py", "rb");
    if (!fh) { __builtin_trap(); }
    if (PyRun_SimpleFileExFlags(fh, "<prelude>", 1, NULL) != 0) { __builtin_trap(); }
    PyObject *main_module = PyImport_AddModule("__main__");
    runner_main = PyObject_GetAttrString(main_module, "_runner_main");
    if (!runner_main) { __builtin_trap(); }
}

__attribute__((export_name("run")))
int runner_run(void) {
    if (!runner_main) { __builtin_trap(); }
    PyObject *result = PyObject_CallNoArgs(runner_main);
    if (!result) { return 1; }
    Py_DECREF(result);
    return 0;
}
"""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prelude_sha256() -> str:
    """SHA-256 of the prelude's UTF-8 bytes: ``guest.bundle.prelude_sha256``."""
    return _sha256(PRELUDE_SOURCE)


def shim_sha256() -> str:
    """SHA-256 of the shim's UTF-8 bytes: ``guest.bundle.shim.sha256``."""
    return _sha256(SHIM_SOURCE)
