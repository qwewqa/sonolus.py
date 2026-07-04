"""Shared helpers for arena-IR marshal round-trip tests."""

from __future__ import annotations

import re

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.ir import IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock


def _collect_temp_names(cfg: BasicBlock) -> list[str]:
    """Distinct temp names in a stable first-appearance walk order."""
    names: list[str] = []
    seen: set[str] = set()

    def visit_val(n):
        if isinstance(n, (IRPureInstr, IRInstr)):
            for a in n.args:
                visit_val(a)
        elif isinstance(n, IRGet):
            visit_place(n.place)
        elif isinstance(n, BlockPlace):
            visit_place(n)

    def visit_place(p):
        if isinstance(p, BlockPlace):
            b = p.block
            if isinstance(b, TempBlock):
                if b.name not in seen:
                    seen.add(b.name)
                    names.append(b.name)
            else:
                visit_val(b)
            visit_val(p.index)

    for blk in traverse_cfg_reverse_postorder(cfg):
        for st in blk.statements:
            if isinstance(st, IRSet):
                visit_place(st.place)
                visit_val(st.value)
            else:
                visit_val(st)
        visit_val(blk.test)
    return names


def canon_text(cfg: BasicBlock) -> str:
    """Canonicalize temp names in ``cfg_to_text`` output to t0/t1/...

    Two structurally-identical CFGs that differ only in temp naming then compare
    equal. Real block names (``EntityInfo`` etc.) are never in the temp-name set,
    so they are left untouched; because both sides are canonicalized identically,
    any residual naming divergence is normalized away.
    """
    names = _collect_temp_names(cfg)
    index = {nm: i for i, nm in enumerate(names)}
    text = cfg_to_text(cfg)
    # Two-phase (sentinel then final) so replacements never chain; longest
    # names first so e.g. ``v1`` cannot clobber part of ``v10``.
    for nm in sorted(names, key=len, reverse=True):
        text = re.sub(r"\b" + re.escape(nm) + r"\b", f"\x01{index[nm]}\x01", text)
    for i in index.values():
        text = text.replace(f"\x01{i}\x01", f"t{i}")
    return text


def roundtrip(cfg: BasicBlock, mode=None, callback=None) -> BasicBlock:
    """Marshal a CFG into the arena and export it straight back."""
    func = ir.marshal_in(cfg, mode, callback)
    func.verify()
    return ir.to_basic_blocks(func)


def assert_idempotent(cfg: BasicBlock, mode=None, callback=None) -> BasicBlock:
    """export(import(x)) is byte-for-byte stable under a second round-trip."""
    rt1 = roundtrip(cfg, mode, callback)
    rt2 = roundtrip(rt1, mode, callback)
    assert cfg_to_text(rt1) == cfg_to_text(rt2), "export/import not idempotent"
    return rt1


def assert_faithful(cfg: BasicBlock, mode=None, callback=None) -> BasicBlock:
    """Round-trip preserves the CFG modulo deterministic temp renumbering.

    Only valid for CFGs already in binary associative form (marshal-in binarizes
    n-ary Add/Multiply/Mod/Rem, so an n-ary input is not text-faithful -- compare
    against an unflattened original instead).
    """
    rt = roundtrip(cfg, mode, callback)
    assert canon_text(rt) == canon_text(cfg)
    return rt
