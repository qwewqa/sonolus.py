"""The two expression-depth caps in the optimizer core stay in the order that keeps emit's budget out of the way.

`lower.pyx`'s `_MAX_FOLD_DEPTH` force-materializes any deeper expression tree into a temp, so a place index that
survives lowering is bounded near that cap. `emit.pyx` then walks that index looking for a runtime-constant
subtree and gives up after `_RTC_DEPTH_LIMIT` levels, classifying what it did not finish walking as not
runtime-constant, which keeps the `*Shifted` rewrite the runtime-constant decline exists to avoid. Invert the two
caps and that give-up path starts firing on indices lowering deliberately left intact.

Near that cap, not at it: if-conversion is exempt from the fold cap and can exceed it by its own arm budget,
which is what the `~(_MAX_FOLD_DEPTH + IFCONV_ARM_BUDGET)` notes in `lower.pyx` describe. The two caps are equal
today, so that window sits above what the assertion below pins, and it is left unpinned deliberately: an index
tree deep enough to reach into it is not reachable from source, and landing in it costs a missed decline rather
than a miscompile.

The caps are `cdef` module-level ints, which are not visible from Python, so this reads the `.pyx` sources: it
pins the relationship between the two constants in the source, not the behavior of the loaded extension.
"""

from __future__ import annotations

import re
from pathlib import Path

_OPT_DIR = Path(__file__).resolve().parents[2] / "sonolus" / "backend" / "_opt"


def _read_cdef_int(file_name: str, name: str) -> int:
    path = _OPT_DIR / file_name
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^cdef int32_t {re.escape(name)} = (\d+)$", text, re.MULTILINE)
    assert match is not None, f"{name} is no longer a module-level `cdef int32_t` in {file_name}"
    return int(match.group(1))


def test_rtc_depth_limit_is_not_below_the_fold_depth_cap():
    rtc_depth_limit = _read_cdef_int("emit.pyx", "_RTC_DEPTH_LIMIT")
    max_fold_depth = _read_cdef_int("lower.pyx", "_MAX_FOLD_DEPTH")
    assert rtc_depth_limit >= max_fold_depth
