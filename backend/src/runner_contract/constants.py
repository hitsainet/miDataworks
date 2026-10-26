"""Every fixed value of ``midataworks.runner/v1`` (FTDD 018 §4.3; FR-018.1 – FR-018.7).

Plain data, emitted under named keys of ``docs/schemas/midataworks-runner-v1.json`` by
``document.render_contract_file()``. Values set by the S0 measurement
(``0xcc/reviews/018_S0_measurement_2026-10-24.md``) are marked; ``render_contract_file()`` refuses
while any of them is ``None`` (S1 never publishes a formula). Changing a value here changes the
published contract: within v1 only additively (new vectors, reasons, modules, RAISED floors);
a new interpreter or bundle is v2 (``VERSIONING``).

Standard library only: the runner image imports this module.
"""

from __future__ import annotations

from typing import Any

from .prelude import prelude_sha256, shim_sha256
from .reasons import REASONS

CONTRACT_FORMAT = "midataworks.runner/v1"
CONTRACT_MAJOR = 1
CONTRACT_VERSION = "1.0"
CASES_FORMAT = "midataworks.runner-cases/v1"

#: The runtime whose fuel accounting defines the floor (018-T16). The Python binding is
#: ``wasmtime`` 50.0.1 on PyPI (a ctypes binding over libwasmtime 50); the CLI that pre-initialises
#: the snapshot is ``wasmtime`` 50.0.0 (there is no CLI release 50.0.1).
RUNTIME: dict[str, Any] = {
    "family": "wasmtime",
    "major": 50,
    "fuel": (
        "Wasmtime fuel with consume_fuel enabled: the count of WebAssembly operators executed, as "
        "Wasmtime 50 meters it. It does not depend on the CPU or the host language, only on the "
        "module bytes (the published snapshot), the request bytes and the runtime major."
    ),
    "config": {
        "consume_fuel": True,
        "epoch_interruption": True,
        "max_wasm_stack": "limits_floor.wasm_stack_bytes",
        "epoch_tick_ms": 10,
    },
}

_RELEASES = "https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.15.1/"

#: The guest bundle (ENH-006 D41, S0 correction): a custom link of libpython with SHIM_SOURCE into a
#: reactor, pre-initialised by ``wasmtime wizer`` with PRELUDE_SOURCE. Snapshot bytes are not
#: reproducible across builds and fuel depends on them, so the snapshot is built ONCE here and every
#: host fetches the same bytes by digest. The other parts record how it was built.
GUEST_BUNDLE_PARTS: dict[str, Any] = {
    "interpreter": {
        "name": "CPython",
        "version": "3.15.1",
        "target": "wasm32-wasip1",
        "url": _RELEASES + "python-3.15.1-wasi_sdk-33.zip",
        "sha256": "252e2e17fab9f51a51fbfc73d51e18805cb633e9f9200c9753b4b801c70ad32c",
        "library_root": "lib/python3.15",
    },
    "libpython": {
        "url": _RELEASES + "_build-python-3.15.1-wasi_sdk-33.zip",
        "sha256": "1b4a2ecd484d5ed9c0c9ed2d91ad45e7c1a1b06c6d9ba286ffef96f2aa229fd6",
        "archives": [
            "libpython3.15.a",
            "Modules/_hacl/libHacl_HMAC.a",
            "Modules/_hacl/libHacl_Hash_BLAKE2.a",
            "Modules/_hacl/libHacl_Hash_MD5.a",
            "Modules/_hacl/libHacl_Hash_SHA1.a",
            "Modules/_hacl/libHacl_Hash_SHA2.a",
            "Modules/_hacl/libHacl_Hash_SHA3.a",
            "Modules/expat/libexpat.a",
        ],
    },
    "headers": {
        "url": "https://github.com/python/cpython/archive/refs/tags/v3.15.1.tar.gz",
        "sha256": "b0ca8592506635d0b2dbf73a198931300d542afa2b62f63213ae0d401ff855af",
    },
    "wasi_sdk": {
        "version": "33.0",
        "url": (
            "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-33/"
            "wasi-sdk-33.0-x86_64-linux.tar.gz"
        ),
        "sha256": "0ba8b5bfaeb2adf3f29bab5841d76cf5318ab8e1642ea195f88baba1abd47bce",
        "link_flags": [
            "-mexec-model=reactor",
            "-z",
            "stack-size=16777216",
            "-Wl,--stack-first",
            "-Wl,--initial-memory=41943040",
            "-ldl",
            "-lwasi-emulated-signal",
            "-lwasi-emulated-getpid",
            "-lwasi-emulated-process-clocks",
            "-lpthread",
            "-lm",
        ],
    },
    "preinit": {
        "tool": "wasmtime wizer",
        "version": "50.0.0",
        "url": (
            "https://github.com/bytecodealliance/wasmtime/releases/download/v50.0.0/"
            "wasmtime-v50.0.0-x86_64-linux.tar.xz"
        ),
        "sha256": "77c19ee0437991f9ac7837b9c269d9c7076744f85dad263fea5214cc2723c7ff",
        "init_func": "wizer.initialize",
        "normalisation": (
            "fixed clock linked into the guest (SHIM_SOURCE), random.seed(0) at the end of the "
            "prelude, library and prelude file times set to 1000000000 (2001-09-09T01:46:40Z)"
        ),
    },
    "shim": {"sha256": shim_sha256()},
    "prelude_sha256": prelude_sha256(),
    "entry": "run",
}

#: The published snapshot (S0, 2.3): ``url`` is where the operator uploads the exact bytes built by
#: ``runner/scripts/build_bundle.py --from-parts``; ``sha256`` and ``size`` pin them.
GUEST_SNAPSHOT: dict[str, Any] = {
    "url": (
        "https://github.com/hitsainet/miDataworks/releases/download/runner-bundle-v1/"
        "midataworks-runner-snapshot-v1.wasm"
    ),
    # Built once by ``runner/scripts/build_bundle.py --from-parts`` (S0, 2026-10-24); every host
    # fetches these exact bytes. PROVISIONAL until the operator uploads them to ``url`` and the node
    # Jobs (FTASKS 2.7) confirm the figures on the GPU node.
    "sha256": "a113fe691fed77e63aebf0239d2b7dd84bc0d0a11255b43a17c7547a63dbb6fb",
    "size": 40_760_800,
}

LANGUAGE: dict[str, Any] = {
    "implementation": "CPython",
    "version": "3.15.1",
    "flags": ["isolated", "no_site", "dont_write_bytecode", "utf8_mode"],
    "hash_seed": 0,
    "recursion_limit": 1000,
}

#: Modules a code rule may import (018-T24). Each is imported into the snapshot by the prelude.
STDLIB_ALLOWED: tuple[str, ...] = (
    "bisect",
    "collections",
    "decimal",
    "fractions",
    "functools",
    "heapq",
    "itertools",
    "json",
    "math",
    "operator",
    "re",
    "statistics",
    "string",
    "unicodedata",
)

#: Names the prelude refuses statically (``rule_load_refused`` naming the first found in a
#: breadth-first walk of the compiled module's code objects' ``co_names``). The Python layer
#: names reasons; it is not the barrier (FTDD §4.4 "Two layers, one barrier").
STATIC_REFUSALS: tuple[str, ...] = (
    "__base__",
    "__bases__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__import__",
    "__loader__",
    "__mro__",
    "__reduce__",
    "__reduce_ex__",
    "__spec__",
    "__subclasses__",
    "ag_frame",
    "cr_frame",
    "f_back",
    "f_builtins",
    "f_globals",
    "f_locals",
    "gi_code",
    "gi_frame",
    "tb_frame",
    "tb_next",
)

#: Table W (FTDD §4.4; S0 corrections folded in, FTID §16 X13 – X15): the WebAssembly System
#: Interface preview-1 surface every host links ITSELF, rather than the runtime's own WASI context.
WASI_TABLE: tuple[dict[str, Any], ...] = (
    {
        "imports": ["fd_read"],
        "behaviour": (
            "descriptor 0: serves the request bytes (canonical JSON, see guest_interface.request), "
            "then end of file; any other descriptor: EBADF (8)"
        ),
    },
    {
        "imports": ["fd_write"],
        "behaviour": (
            "descriptor 1: appended to the reply buffer; a write that takes the buffer past the "
            "output limit in force ends the instance with rule_output_invalid. Descriptor 2: "
            "discarded (bytes counted, never stored). Descriptor 3: phase markers, the exact bytes "
            "'load' then 'call', EACH ACCEPTED ONCE AND IN ORDER (prelude -> load -> call; inspect "
            "requests stop at load); a marker sets the store's remaining fuel to the next phase's "
            "budget. A repeated marker, an out-of-order marker or any other bytes on descriptor 3 "
            "end the instance with rule_error (a rule must not refill its own fuel; hostile 17, "
            "18). Any other descriptor: EBADF (8)"
        ),
    },
    {
        "imports": ["clock_time_get", "clock_res_get"],
        "behaviour": "time 0 (the Unix epoch) for every clock; resolution 1",
    },
    {
        "imports": ["random_get"],
        "behaviour": (
            "the contract's fixed stream (random.algorithm), restarted at counter 0 in every "
            "instance and continued across calls within it"
        ),
    },
    {
        "imports": ["environ_sizes_get", "environ_get"],
        "behaviour": "an empty environment (count 0, size 0)",
    },
    {
        "imports": ["args_sizes_get", "args_get"],
        "behaviour": 'arguments ["python"]',
    },
    {
        "imports": ["fd_fdstat_get"],
        "behaviour": (
            "descriptors 0, 1, 2: a character device (filetype 2), no flags; any other: EBADF (8)"
        ),
    },
    {
        "imports": ["fd_prestat_get", "fd_prestat_dir_name"],
        "behaviour": "EBADF (8) for every descriptor: there are no preopened directories",
    },
    {
        "imports": [
            "path_create_directory",
            "path_filestat_get",
            "path_filestat_set_times",
            "path_link",
            "path_open",
            "path_readlink",
            "path_remove_directory",
            "path_rename",
            "path_symlink",
            "path_unlink_file",
        ],
        "behaviour": "ENOTCAPABLE (76)",
    },
    {
        "imports": [
            "fd_advise",
            "fd_allocate",
            "fd_close",
            "fd_datasync",
            "fd_fdstat_set_flags",
            "fd_fdstat_set_rights",
            "fd_filestat_get",
            "fd_filestat_set_size",
            "fd_filestat_set_times",
            "fd_pread",
            "fd_pwrite",
            "fd_readdir",
            "fd_renumber",
            "fd_seek",
            "fd_sync",
            "fd_tell",
        ],
        "behaviour": "EBADF (8) for every descriptor",
    },
    {
        "imports": ["sock_accept", "sock_recv", "sock_send", "sock_shutdown", "proc_raise"],
        "behaviour": (
            "linked as functions that end the instance with rule_error when called (the snapshot "
            "imports them, so an unlinked import would refuse instantiation)"
        ),
    },
    {
        "imports": ["proc_exit"],
        "behaviour": "ends the instance; an exit before a complete reply is rule_output_invalid",
    },
    {
        "imports": ["sched_yield", "poll_oneoff"],
        "behaviour": "return at once with success (0); poll_oneoff reports 0 events",
    },
)

RANDOM: dict[str, Any] = {
    "seed": "midataworks.runner/v1 random stream",
    "algorithm": (
        "block i (i = 0, 1, 2, ...) = SHA-256(UTF-8(seed) || i as 8 bytes little-endian); the "
        "stream is the blocks concatenated; random_get(n) returns the next n bytes of the stream"
    ),
}

#: The floor a deployment may raise and never lower (D32, D34). ``None`` = set by S0 measurement.
# PROVISIONAL floors from the S0 measurement on the workstation (2026-10-24); the node Jobs
# (FTASKS 2.7) and CI fuel identity (2.9) confirm or correct them before release. See
# ``0xcc/reviews/018_S0_measurement_2026-10-24.md``.
LIMITS_FLOOR: dict[str, int | None] = {
    "input_code_points": 65_536,
    "memory_bytes": 67_108_864,
    "output_bytes": 4_096,
    "call_fuel": 2_147_483_648,  # 2**31; 10 x fixture 2's worst call fuel (1.43e8) rounded up
    # 2**25: 10 x the largest conformance document's load fuel, fixture 1 (Luhn) at 2,294,645;
    # was 2**24 from fixture 2 alone (~1.4 M), below its own basis (Review R1, 2026-10-24).
    "load_fuel": 33_554_432,
    "prelude_fuel": 67_108_864,  # 2**26; 2 x the prelude fuel of a maximal 4-byte request (2.1e7)
    "wasm_stack_bytes": 2_097_152,  # 2 MiB
}

#: How each floor value was derived (published beside the numbers).
LIMITS_FLOOR_BASIS: dict[str, str] = {
    "input_code_points": "D32 starting value",
    "memory_bytes": "64 MiB (X-13); the snapshot's linear memory starts at 40 MiB",
    "output_bytes": "D32 starting value",
    "call_fuel": (
        "10 x fixture 2's call fuel on its worst 65,536-code-point vector text, rounded up to a "
        "power of two"
    ),
    "load_fuel": (
        "10 x the largest conformance document's load fuel, rounded up to a power of two"
    ),
    "prelude_fuel": (
        "2 x the prelude fuel to read a maximal request (65,536 four-byte code points), rounded up "
        "to a power of two"
    ),
    "wasm_stack_bytes": (
        "2 MiB; Python recursion at recursion_limit raises RecursionError well inside it; C-level "
        "recursion past it traps (both rule_resource_limit); edge vectors stay far from it"
    ),
}

#: Whether ``LIMITS_FLOOR`` is final (ENH-006 D32, D47). Published beside the floor as
#: ``limits_floor_status`` (validated by ``document.LimitsFloorStatus``). PROVISIONAL until the node
#: Jobs (FTASKS 2.7) and the fuel-identity run on GitHub's runners (2.9) confirm or correct it;
#: confirming it is a contract minor (``provisional`` false), and so is raising a floor.
LIMITS_FLOOR_STATUS: dict[str, Any] = {
    "provisional": True,
    "measured": "S0 on a workstation, 2026-10-24 (0xcc/reviews/018_S0_measurement_2026-10-24.md)",
    "pending": [
        "FTASKS 2.7: cold start, per-call p95 and instantiation on the node, per image",
        "FTASKS 2.9: fuel identity on GitHub's runners",
    ],
    "change_rule": (
        "a confirmed value or a RAISED floor is a contract minor (contract_version 1.x); a floor "
        "is never lowered within v1"
    ),
}

#: Limits a request carries (each at or above its floor where one exists).
REQUEST_LIMITS: tuple[str, ...] = (
    "call_fuel",
    "load_fuel",
    "prelude_fuel",
    "memory_bytes",
    "input_code_points",
    "output_bytes",
    "wall_ms",
)

COUNT_CAP = 1_000

DIAGNOSTIC: dict[str, Any] = {
    "max_message_chars": 200,
    "fields": ["exception_type", "line", "message"],
    "modes": ["dry_run"],
}

#: What each reason means, published beside the list.
REASON_MEANINGS: dict[str, str] = {
    "rule_timeout": "the wall-time backstop expired (host fault; never a verdict)",
    "rule_resource_limit": (
        "fuel ran out in the load or call phase, memory growth was refused, Python recursion or "
        "the WebAssembly stack was exhausted"
    ),
    "rule_worker_unavailable": (
        "the runner could not be reached, its bundle did not verify, or its lane stayed full past "
        "the backstop (host fault)"
    ),
    "rule_error": (
        "the code raised, did not compile, did not export the function, or broke the phase-marker "
        "protocol; also a guest that called an unlinked capability"
    ),
    "rule_load_refused": "the code named a refused capability or imported a module not allowed",
    "rule_output_invalid": (
        "the reply was absent, more than one line, over the output limit, outside the grammar, "
        "or the function returned anything but a non-negative int (bool is not an int)"
    ),
    "rule_input_too_large": "the text has more code points than the input limit in force",
    "rule_not_approved": (
        "the code's hash is not approved where the database is (never emitted by the runner)"
    ),
}

assert tuple(REASON_MEANINGS) == REASONS  # noqa: S101 - import-time consistency, not a check

GUEST_INTERFACE: dict[str, Any] = {
    "request": (
        "canonical JSON (keys sorted by code point, ',' and ':' separators, UTF-8, non-ASCII as "
        'itself) of {"mode": "run" | "inspect", "source": str, "function": str | null, "params": '
        'object | null, "text": str | null, "diagnostics": bool}; fuel depends on these exact '
        "bytes"
    ),
    "reply": (
        "exactly one line on descriptor 1: canonical-key compact ASCII JSON followed by one "
        'newline. run: {"phase": "load" | "call", "result": R}; R = {"t": "int", "v": "<decimal '
        '>= 0>" | "overflow" | "negative"} | {"t": "bool" | "float" | "str" | "bytes" | "none" | '
        '"other"} | {"fail": "load_refused", "refused": str} | {"fail": "error" | "recursion" | '
        '"memory", "diag"?: {"exception_type": str, "line": int | null, "message": str}}. inspect: '
        '{"phase": "load", "inspect": {"functions": [str], "imports": [str]}} or a load fail'
    ),
    "host_reduction": (
        'the HOST decides: a count only for t == "int" with a decimal v >= 0 (clamped to count_cap; '
        '"overflow" -> count_cap); "negative", bool and every other t -> rule_output_invalid; '
        "load_refused -> rule_load_refused; error -> rule_error; recursion, memory -> "
        "rule_resource_limit; a reply absent, longer than the output limit, not exactly one line "
        "or outside this grammar -> rule_output_invalid. The grammar is the output barrier: a "
        "guest that forges the line can only report a count. diag is kept only in dry_run"
    ),
    "phases": (
        "prelude_fuel from the first instruction of the entry point 'run'; load_fuel from the "
        "'load' marker; call_fuel from the 'call' marker. Fuel exhausted before 'load' is "
        "rule_error (never expected); in the load or call phase, rule_resource_limit"
    ),
    "traps": (
        "out of fuel -> as phases; epoch deadline -> rule_timeout; stack overflow -> "
        "rule_resource_limit; memory growth refused -> the guest's MemoryError, else a trap -> "
        "rule_resource_limit; any other trap -> rule_error"
    ),
}

TEXT_RULES = (
    "Texts are sequences of Unicode code points. The CALLER replaces every lone surrogate "
    "(U+D800 - U+DFFF) with U+FFFD before encoding; a host refuses a request whose text is not "
    "valid UTF-8 (text_invalid). A text longer than input_code_points code points is "
    "rule_input_too_large for that text; texts are never truncated or split."
)

HASH_RULE = (
    "code_sha256 = SHA-256 of the canonical JSON of the CodeRuleDocument (every field present; keys "
    "sorted by Unicode code point, NOT UTF-16 code units; ',' and ':' separators; UTF-8 with "
    "non-ASCII characters written as themselves, never \\u escapes; NaN and infinity refused). The "
    "published document file is exactly the bytes hashed. Outside the hash on purpose: the code "
    "rule's name, behaviour key, version number and cases."
)

INSTANCE_RULE = (
    "One fresh instance of the snapshot per (text, function) call: a module with three functions "
    "over one row is three instances. A host may cache the compiled snapshot by its sha256 and "
    "documents by code_sha256, never an instance or any guest state."
)

VERSIONING = (
    "Additive within v1: new vectors, reasons, modules and RAISED floors are a new minor "
    "(contract_version 1.x). A new interpreter, prelude, shim or snapshot is v2. A runtime major "
    "that keeps every fuel-edge and limit vector green is a minor; otherwise a major, or not "
    "taken. The cases file is append-only within v1."
)

PROPERTIES: dict[str, str] = {
    "property_1": (
        "No secret reaches the runner process: its environment holds no credential, token or "
        "database address, it mounts no secret, configuration map or service-account token, and "
        "/health lists its environment variable NAMES (env_names) so a deployment can check it."
    ),
    "property_2": (
        "The runner process has no network egress: from inside its container a plain socket call "
        "outside the guest cannot reach the backend, cluster DNS or an outside address, while a "
        "positive control with the same labels minus the policy can. Only the backend may call it."
    ),
    "conformance_check": (
        "Each host records, after every runner deploy, the positive control, the three refused "
        "connections and the env_names comparison against its secret list; topology is free "
        "(a separate pod or a sidecar) as long as both properties pass (D34)."
    ),
}

#: The hostile classes every host's vectors cover (FR-018.11; 17 – 19 from the S0 measurement).
HOSTILE_CLASSES: tuple[dict[str, Any], ...] = (
    {"number": 1, "attack": "open a network socket"},
    {"number": 2, "attack": "read a file: /etc/passwd, the runner's configuration, another rule"},
    {"number": 3, "attack": "read an environment variable"},
    {"number": 4, "attack": "spawn a process"},
    {"number": 5, "attack": "loop forever"},
    {"number": 6, "attack": "allocate past the memory floor"},
    {"number": 7, "attack": "return or print a megabyte"},
    {"number": 8, "attack": "return the matched text"},
    {"number": 9, "attack": "read the clock or a random source"},
    {"number": 10, "attack": "carry state between calls"},
    {"number": 11, "attack": "recurse until the stack is exhausted"},
    {"number": 12, "attack": "load-time bomb (a huge constant built at import)"},
    {"number": 13, "attack": "malformed return: negative, float, bool, string, structure, nothing"},
    {"number": 14, "attack": "count above the cap"},
    {"number": 15, "attack": "import a module outside the list"},
    {"number": 16, "attack": "pathological backtracking in the guest's re"},
    {"number": 17, "attack": "refill fuel by replaying a phase marker (S0)"},
    {"number": 18, "attack": "an out-of-order or unknown phase marker (S0)"},
    {"number": 19, "attack": "forge an extra reply line on descriptor 1 (S0)"},
)

#: Sentinel kinds a hostile vector may name; the harness supplies fresh values (FTDD §4.5).
SENTINEL_KINDS: dict[str, str] = {
    "env": "a fresh random value in the runner process's own environment, under the given NAME",
    "file": (
        "a fresh random value in a file the runner process can read, at the given relative NAME "
        "in the runner process's working directory"
    ),
    "cached_rule": (
        "a fresh random value in the source of another code rule the runner ran (and cached) "
        "just before the vector"
    ),
}
