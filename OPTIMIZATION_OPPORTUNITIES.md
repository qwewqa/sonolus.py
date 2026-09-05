# Optimizer opportunities

These are possible follow-up optimizations found while adding `Copy` fusion. They are not part of the implemented
`Copy` pass.

## Static real-block memory forwarding

The current dead-store pass uses liveness only for temporary allocations and otherwise removes self-copies. It does
not track successive stores to static addresses in writable real blocks. A local memory-state analysis could remove
an earlier store when another store definitely overwrites the same address before any possible read. Tracking short
contiguous ranges would also cover adjacent dead stores produced by flattened records and arrays.

The same state could forward a known constant from a store to a later load of the same static address. Existing GVN
only reuses loads from non-writable real blocks; it deliberately does not model stores. Either optimization needs an
alias barrier for dynamic addresses, dynamic blocks, entity block views, calls or operations that can access memory,
and control-flow joins. Removing a dead store must retain evaluation of a side-effecting stored value.

## Emission-aware load cost

`lower._tree_cost` prices a real-block load as a `Get`, a block leaf, and its index expression. It does not include
the extra `Add` and offset leaf emitted for a dynamic index with a nonzero place offset. It also does not reflect the
different operand shape when emission selects `GetShifted`. This can make materialization decisions from a cost that
differs from the emitted tree.

A follow-up could share an emission-shape classifier between costing and emission, or reproduce the emitter's exact
cases in the cost model with tests that compare predicted and emitted effective cost. Runtime-constant address trees
need their existing special treatment because the Sonolus runtime folds them.

## Copy fusion with dynamic addresses

The implemented `Copy` pass accepts only static real-block addresses. Dynamic-address fusion could cover loops or
unrolled code whose source and destination addresses share a stable base expression and advance by consecutive
offsets.

This extension needs proofs that every fused address expression is stable for the full run, source and destination
ranges cannot overlap in a way that changes sequential behavior, and converting address arithmetic to `Copy`'s
arguments preserves 32-bit-float address semantics. In particular, integer adjacency above the exactly representable
float range is not enough to prove runtime adjacency. Entity blocks and their array views also need explicit alias
handling.
