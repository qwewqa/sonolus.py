# cython: language_level=3
"""Optimizer driver: level dispatch and the from-Func pipeline.

Keeps the toolchain-proof helpers (``compiled`` / ``nogil_sum``) that
``tests/backend/test_opt_toolchain.py`` exercises, and wires the optimization
pipelines.

Levels:

    minimal  (-O0): cfg_cleanup -> bump allocation  (mid-end bypassed)
    fast     (-O1): cfg_cleanup -> build_ssa -> midend_round(allow_repeat=False)
                    -> lower_from_ssa -> try-bump allocation
    standard (-O2): cfg_cleanup -> build_ssa -> midend_standard -> if_convert
                    -> lower_from_ssa -> interference (packing) allocation

``midend_round`` runs SCCP -> simplify/GVN -> DCE over the SSA arena (repeating
once when ``allow_repeat`` and something changed). ``midend_standard`` runs that
core plus LICM and rewrite_switch, repeating the core once on change.
``if_convert`` (standard only) folds diamonds/triangles/{VALUE C, NONE} blocks
into ``If`` expression selects over SSA while phis still exist, relying on
must-fold flags that ``lower_from_ssa`` honors. ``lower_from_ssa`` is the real
out-of-SSA + treeify + coalesce + phi-free cleanup + normalize_switch that
supersedes the naive ``out_of_ssa``, producing a legal non-SSA arena ready for
``allocate_func`` + emission.

The thin Python shim ``sonolus/backend/optimize/__init__.py`` (``run_passes`` /
``optimize_and_finalize`` / ``cfg_to_engine_node``) calls the ``*_cfg`` entry
points below with a level *name*; ``compile_mode`` uses ``optimize_and_finalize``.
``allocate=False`` (visualize_cfg) stops after ``lower_from_ssa`` (or after
``cfg_cleanup`` at minimal), leaving temp places unallocated.

This module also populates the phase registry consulted by ``ir.debug_run``,
registering ``cfg_cleanup``, ``lower``, and the three allocators so
``debug_run(cfg, phases=["cfg_cleanup"])`` runs a single named phase.
"""

from sonolus.backend._opt.ir cimport Func
from sonolus.backend._opt.midend cimport build_ssa, cfg_cleanup, midend_round, midend_standard, out_of_ssa
from sonolus.backend._opt.analysis cimport compute_dominators
from sonolus.backend._opt.lower cimport (
    ALLOC_BUMP,
    ALLOC_PACKING,
    ALLOC_TRY_BUMP,
    allocate_func,
    fuse_rmw,
    if_convert,
    lower_from_ssa,
)
from sonolus.backend._opt.emit cimport emit_func

import os

from sonolus.backend._opt.ir import marshal_in, register_phase, to_basic_blocks
from sonolus.backend.optimize.flow import cfg_to_text
from sonolus.backend.optimize import profiling as _prof

# Debug tracing: when ``SONOLUS_OPT_TRACE=1`` is set in the environment, the
# pipeline exports and prints the CFG after each pass so a contributor can watch
# the IR evolve. Read once at import (like a build flag); the common (untraced)
# path pays only a cheap module-global check per stage.
_TRACE = os.environ.get("SONOLUS_OPT_TRACE") == "1"


def _trace_dump(func, str label):
    """Print ``cfg_to_text`` of ``func`` under a labelled header (GIL held)."""
    print(f"===== SONOLUS_OPT_TRACE: after {label} =====")
    print(cfg_to_text(to_basic_blocks(func)))


# --------------------------------------------------------------------------
# Toolchain-proof helpers (used by test_opt_toolchain.py).
# --------------------------------------------------------------------------

def compiled() -> bool:
    """Return True from compiled code, proving the extension loaded."""
    return True


cdef long _triangular(long n) noexcept nogil:
    """Sum ``0 .. n-1`` in a GIL-free region."""
    cdef long total = 0
    cdef long i
    for i in range(n):
        total += i
    return total


def nogil_sum(long n) -> int:
    """Compute ``sum(range(n))`` inside a ``with nogil:`` block."""
    cdef long result
    with nogil:
        result = _triangular(n)
    return result


# --------------------------------------------------------------------------
# Level dispatch.
# --------------------------------------------------------------------------

cdef enum:
    LEVEL_MINIMAL = 0
    LEVEL_FAST = 1
    LEVEL_STANDARD = 2


cdef int _level_code(object level) except -1:
    if level == "minimal":
        return LEVEL_MINIMAL
    if level == "fast":
        return LEVEL_FAST
    if level == "standard":
        return LEVEL_STANDARD
    raise ValueError(
        f"Unknown optimization level {level!r} (expected 'minimal', 'fast', or 'standard')"
    )


cdef Func _pipeline(Func func, int level, bint allocate):
    """Run the level-``level`` pipeline over ``func``, returning a fresh arena.

    ``minimal`` bypasses the mid-end (cfg_cleanup -> bump). ``fast`` and
    ``standard`` share the cfg_cleanup -> build_ssa front, then diverge:

        fast     (-O1): midend_round(allow_repeat=False) -> lower_from_ssa
                        -> try-bump allocation. No LICM / rewrite_switch /
                        if-conversion (dev default; iteration speed first).
        standard (-O2): midend_standard (core -> LICM -> rewrite_switch, core
                        repeated once on change) -> if_convert (SSA in / SSA out,
                        while phis exist) -> lower_from_ssa -> packing allocation.

    Each stage returns a fresh ``Func``; the allocator (when requested) rewrites
    the final arena in place.

    ``allocate=False`` returns the pre-allocation form: post-cfg_cleanup for
    minimal, post-lower_from_ssa (temps unallocated) for fast/standard.
    """
    cdef Func cleaned
    cdef Func ssa
    cdef Func opt
    cdef Func conv
    cdef Func lowered
    # Opt-in per-pass timing (SONOLUS_OPT_PROFILE / CLI --profile); read the flag
    # once so an untraced build pays only a bint test per pass. Same idiom as _TRACE.
    cdef bint prof = _prof.enabled
    cdef long long t0 = 0

    if level == LEVEL_MINIMAL:
        if prof: t0 = _prof.now_ns()
        cleaned = cfg_cleanup(func, False)
        if prof: _prof.record("cfg_cleanup", _prof.now_ns() - t0)
        if _TRACE:
            _trace_dump(cleaned, "cfg_cleanup")
        if allocate:
            if prof: t0 = _prof.now_ns()
            allocate_func(cleaned, ALLOC_BUMP)
            if prof: _prof.record("allocate", _prof.now_ns() - t0)
            if _TRACE:
                _trace_dump(cleaned, "allocate(bump)")
        return cleaned

    if prof: t0 = _prof.now_ns()
    cleaned = cfg_cleanup(func, False)
    if prof: _prof.record("cfg_cleanup", _prof.now_ns() - t0)
    if _TRACE:
        _trace_dump(cleaned, "cfg_cleanup")
    if prof: t0 = _prof.now_ns()
    ssa = build_ssa(cleaned)
    if prof: _prof.record("build_ssa", _prof.now_ns() - t0)
    if _TRACE:
        _trace_dump(ssa, "build_ssa")

    if level == LEVEL_FAST:
        # fast (-O1): one mid-end round, cheap allocation, no LICM/switch/if-conv.
        if prof: t0 = _prof.now_ns()
        opt = midend_round(ssa, False)
        if prof: _prof.record("midend", _prof.now_ns() - t0)
        if _TRACE:
            _trace_dump(opt, "midend_round")
        if prof: t0 = _prof.now_ns()
        lowered = lower_from_ssa(opt)
        if prof: _prof.record("lower", _prof.now_ns() - t0)
        if _TRACE:
            _trace_dump(lowered, "lower_from_ssa")
        if allocate:
            if prof: t0 = _prof.now_ns()
            allocate_func(lowered, ALLOC_TRY_BUMP)
            fuse_rmw(lowered)  # place-based RMW fusion (post-allocation)
            if prof: _prof.record("allocate", _prof.now_ns() - t0)
            if _TRACE:
                _trace_dump(lowered, "allocate(try_bump)+fuse_rmw")
        return lowered

    # standard (-O2): full mid-end (LICM + rewrite_switch) then if-conversion
    # over SSA (while phis still exist), then lowering + packing allocation.
    if prof: t0 = _prof.now_ns()
    opt = midend_standard(ssa)
    if prof: _prof.record("midend", _prof.now_ns() - t0)
    if _TRACE:
        _trace_dump(opt, "midend_standard")
    if prof: t0 = _prof.now_ns()
    conv = if_convert(opt)
    if prof: _prof.record("if_convert", _prof.now_ns() - t0)
    if _TRACE:
        _trace_dump(conv, "if_convert")
    if prof: t0 = _prof.now_ns()
    lowered = lower_from_ssa(conv)
    if prof: _prof.record("lower", _prof.now_ns() - t0)
    if _TRACE:
        _trace_dump(lowered, "lower_from_ssa")
    if allocate:
        if prof: t0 = _prof.now_ns()
        allocate_func(lowered, ALLOC_PACKING)
        fuse_rmw(lowered)  # place-based RMW fusion (post-allocation)
        if prof: _prof.record("allocate", _prof.now_ns() - t0)
        if _TRACE:
            _trace_dump(lowered, "allocate(packing)+fuse_rmw")
    return lowered


# --------------------------------------------------------------------------
# Python-callable entry points (used by the optimize/__init__ shim).
# --------------------------------------------------------------------------

def run_pipeline_cfg(entry, level, mode=None, callback=None, allocate=True):
    """marshal_in -> level pipeline -> (allocate) -> to_basic_blocks.

    ``allocate=False`` stops before allocation (for ``visualize_cfg``), leaving
    temp places unallocated (post-lower_from_ssa for fast/standard, post-cleanup
    for minimal).
    """
    cdef int lvl = _level_code(level)
    cdef bint prof = _prof.enabled
    cdef long long t0 = 0
    if prof: t0 = _prof.now_ns()
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    if prof: _prof.record("marshal_in", _prof.now_ns() - t0)
    cdef Func result = _pipeline(func, lvl, allocate)
    if prof: t0 = _prof.now_ns()
    bb = to_basic_blocks(result)
    if prof: _prof.record("marshal_out", _prof.now_ns() - t0)
    return bb


def optimize_and_finalize_cfg(entry, level, mode=None, callback=None):
    """marshal_in -> level pipeline -> allocate -> emit (fused; no export)."""
    cdef int lvl = _level_code(level)
    cdef bint prof = _prof.enabled
    cdef long long t0 = 0
    if prof: t0 = _prof.now_ns()
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    if prof: _prof.record("marshal_in", _prof.now_ns() - t0)
    cdef Func result = _pipeline(func, lvl, True)
    if prof: t0 = _prof.now_ns()
    node = emit_func(result)
    if prof: _prof.record("emit", _prof.now_ns() - t0)
    return node


# --------------------------------------------------------------------------
# compile_mode: per-mode callback compilation driver. Lives here (rather than
# sonolus/build/compile.py, which keeps the public ``compile_mode`` name as a
# thin delegator that engine.py / tests import) so the compile driver lives in
# the compiled package. ``callback_to_cfg`` and the rest of the frontend stay in
# Python and are passed in, keeping the frontend/optimizer boundary explicit and
# avoiding an import cycle (compile.py imports this module).
#
# Compilation is serial: each callback is traced, optimized, and emitted in one
# fixed order, and node indices are assigned by ``OutputNodeGenerator.add`` in
# that same order -- so the serialized output is deterministic by construction.
# Result-dict shapes: archetype callbacks -> {"index", "order"}; global
# callbacks -> bare node index.
# --------------------------------------------------------------------------

# Lazily-populated Python deps (avoid import-time cycles / heavy top-level imports).
_MODE_STATE = None
_OUTPUT_GEN = None
_OPT_CONFIG = None
_OPT_FINALIZE = None
_STANDARD_LEVEL = None
_PLAY_MODE = None
_COMPILATION_ERROR = None


cdef _ensure_compile_deps():
    global _MODE_STATE, _OUTPUT_GEN, _OPT_CONFIG, _OPT_FINALIZE, _STANDARD_LEVEL, _PLAY_MODE
    global _COMPILATION_ERROR
    if _MODE_STATE is None:
        from sonolus.backend.optimize import (
            STANDARD_PASSES as _sp,
            OptimizerConfig as _oc,
            optimize_and_finalize as _of,
        )
        from sonolus.backend.mode import Mode as _mode
        from sonolus.build.node import OutputNodeGenerator as _og
        from sonolus.script.internal.context import ModeContextState as _ms
        from sonolus.script.internal.error import CompilationError as _ce
        _MODE_STATE = _ms
        _OUTPUT_GEN = _og
        _OPT_CONFIG = _oc
        _OPT_FINALIZE = _of
        _STANDARD_LEVEL = _sp
        _PLAY_MODE = _mode.PLAY
        _COMPILATION_ERROR = _ce


def compile_mode(
    mode,
    project_state,
    archetypes,
    global_callbacks,
    callback_to_cfg,
    level=None,
    validate_only=False,
):
    _ensure_compile_deps()
    if level is None:
        level = _STANDARD_LEVEL

    mode_state = _MODE_STATE(mode, archetypes)
    nodes = _OUTPUT_GEN()
    results = {}

    def optimize_cfg(cfg, cb_name):
        """optimize + emit for one already-traced CFG -> its EngineNode.

        Operates on a per-callback-local arena and touches no shared state.

        Failures (e.g. the temp-slot cap, marshal-in validation) are wrapped with
        the callback name and mode as a CompilationError so the cli/dev-server
        pretty handlers catch them (matching the frontend error pattern). ``from``
        keeps the original traceback."""
        try:
            return _OPT_FINALIZE(cfg, level, _OPT_CONFIG(mode=mode, callback=cb_name))
        except _COMPILATION_ERROR:
            raise
        except Exception as e:
            raise _COMPILATION_ERROR(
                f"Optimization failed for callback {cb_name!r} in {getattr(mode, 'name', mode)} mode: {e}"
            ) from e

    # DETERMINISM: ``callback_to_cfg`` populates shared, first-touch-ordered maps
    # -- ``project_state`` ROM / const / debug-string indices and ``mode_state``
    # global-memory offsets. Tracing callbacks in one fixed serial order makes
    # those first-touch assignments deterministic, and registering nodes into the
    # shared ``OutputNodeGenerator`` in that same order makes node indices
    # deterministic too.
    #
    # Task entry: (kind, archetype_data|None, cb_name, cb_order, payload) where
    # payload is the EngineNode, or None for validate_only.
    tasks = []
    base_archetype_entries = {}

    if archetypes is not None:
        base_archetypes = []
        seen_base_archetypes = set()
        for a in archetypes:
            base = getattr(a, "_derived_base_", a)
            if base not in seen_base_archetypes:
                seen_base_archetypes.add(base)
                base_archetypes.append(base)

        for archetype in base_archetypes:
            archetype._init_fields()

            imports = []
            for name, import_info in archetype._imported_keys_.items():
                import_entry = {"name": name, "index": import_info.index}
                if import_info.default is not None:
                    import_entry["def"] = import_info.default
                imports.append(import_entry)

            archetype_data = {
                "name": archetype.name,
                "hasInput": archetype.is_scored,
                "imports": imports,
            }
            if mode == _PLAY_MODE:
                archetype_data["exports"] = [*archetype._exported_keys_]

            callback_items = [
                (cb_name, cb_info, archetype._callbacks_[cb_name])
                for cb_name, cb_info in archetype._supported_callbacks_.items()
                if cb_name in archetype._callbacks_
                and archetype._callbacks_[cb_name] not in archetype._default_callbacks_
            ]

            for cb_name, cb_info, cb in callback_items:
                cb_order = getattr(cb, "_callback_order_", 0)
                if not cb_info.supports_order and cb_order != 0:
                    raise ValueError(f"Callback '{cb_name}' does not support a non-zero order")
                # Trace, then optimize+emit -- always traced (validation traces too).
                cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, archetype)
                if validate_only:
                    tasks.append(("archetype", archetype_data, cb_info.name, cb_order, None))
                else:
                    node = optimize_cfg(cfg, cb_info.name)
                    tasks.append(("archetype", archetype_data, cb_info.name, cb_order, node))

            base_archetype_entries[archetype] = archetype_data

    if global_callbacks is not None:
        for cb_info, cb in global_callbacks:
            cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, None)
            if validate_only:
                tasks.append(("global", None, cb_info.name, 0, None))
            else:
                node = optimize_cfg(cfg, cb_info.name)
                tasks.append(("global", None, cb_info.name, 0, node))

    # Register nodes into the shared generator in the fixed trace order.
    for kind, archetype_data, cb_name, cb_order, payload in tasks:
        if validate_only:
            node_index = 0
        else:
            node_index = nodes.add(payload)
        if kind == "archetype":
            archetype_data[cb_name] = {"index": node_index, "order": cb_order}
        else:
            results[cb_name] = node_index

    if archetypes is not None:
        results["archetypes"] = [
            {**base_archetype_entries[getattr(a, "_derived_base_", a)], "name": a.name, "hasInput": a.is_scored}
            for a in archetypes
        ]

    results["nodes"] = nodes.get()
    return results


# --------------------------------------------------------------------------
# Debug phase registry (consulted by ir.debug_run).
# --------------------------------------------------------------------------

def _phase_cfg_cleanup(func):
    return cfg_cleanup(<Func>func, False)


def _phase_ssa(func):
    return build_ssa(<Func>func)


def _phase_unssa(func):
    return out_of_ssa(<Func>func)


def _phase_ifconv(func):
    return if_convert(<Func>func)


def _phase_lower(func):
    return lower_from_ssa(<Func>func)


def _phase_dominators(func):
    compute_dominators(<Func>func)
    return func


def _phase_bump(func):
    allocate_func(<Func>func, ALLOC_BUMP)
    return func


def _phase_packing(func):
    allocate_func(<Func>func, ALLOC_PACKING)
    return func


def _phase_try_bump(func):
    allocate_func(<Func>func, ALLOC_TRY_BUMP)
    return func


register_phase("cfg_cleanup", _phase_cfg_cleanup)
register_phase("ssa", _phase_ssa)
register_phase("unssa", _phase_unssa)
register_phase("ifconv", _phase_ifconv)
register_phase("lower", _phase_lower)
register_phase("dominators", _phase_dominators)
register_phase("bump", _phase_bump)
register_phase("packing", _phase_packing)
register_phase("try_bump", _phase_try_bump)
