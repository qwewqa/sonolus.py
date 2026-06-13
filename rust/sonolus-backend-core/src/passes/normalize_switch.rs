//! W5 switch normalization (PORT.md T3.11) — the successor of the legacy
//! `NormalizeSwitch` pass (`sonolus/backend/optimize/simplify.py`), redesigned
//! for MIR per decisions D2/D13. It **manufactures dense 0-based integer case
//! sets**: a multi-way branch whose case conds form an affine integer
//! progression `base, base+s, base+2s, ...` gets its scrutinee rebased to
//! `(x - base) / s` and its conds relabeled `0..n-1`, so the emitter's
//! dense-form *selection* (T1.2 / legacy `finalize.py`, `emit::is_dense`)
//! picks the O(1) `SwitchIntegerWithDefault` dispatcher instead of the linear
//! `SwitchWithDefault` scan. Headline metric: **`eval_count`** (the scan's
//! per-dispatch case-constant evaluations disappear), with `static_nodes`
//! riding along (n case constants replaced by 1-2 rebase ops); the dispatch
//! count is unchanged (same block, one dispatcher either way).
//!
//! The selection half of the legacy pair needs no work here: the emitter has
//! selected the dense form for already-dense 0-based sets since T1.2. This
//! pass only manufactures density where it provably preserves semantics and
//! measurably pays.
//!
//! # Placement (decision owned by T3.11, licensed by D13)
//!
//! Legacy ran late, post-`FromSSA`/pre-`Allocate`, on the flat CFG; D4
//! originally sketched the Rust port inside lowering. This port runs it as
//! the **last MIR registry pass** (its own [`Stage::W5`]) instead: the rebase
//! is two ordinary pure binary instructions (invariant §3.3 holds — no
//! variadic forms, no special lowering), so out-of-SSA, allocation, and
//! lowering handle it with zero special cases, and the pass gets the standard
//! per-transform verification surface (the `compile_cfg_with_pipeline`
//! differential injection point plus per-pass fuzz) that emission-time code
//! does not. Nothing runs after it in the registry, so the manufactured form
//! reaches the emitter intact; T3.9's exit shaping (which guards *existing*
//! dense sets against degradation) runs earlier and is never fought — this
//! pass only ever adds dense sets.
//!
//! [`Stage::W5`]: crate::passes::Stage::W5
//!
//! # The transform
//!
//! ```text
//! Branch { test: x, cases: [(c_0, t_0), ..., (c_{n-1}, t_{n-1})], default }
//!   where c_i = base + i*s exactly (sorted ascending, the terminator contract)
//! ⇒
//! sub = Subtract(x, base)            (appended to the block schedule)
//! v   = Divide(sub, s)               (omitted when s == 1)
//! Branch { test: v, cases: [(0, t_0), ..., (n-1, t_{n-1})], default }
//! ```
//!
//! Targets keep their order (the relabeling is monotone: `s > 0`), the
//! default edge is untouched (present or absent), and predecessor sets do not
//! change, so no phi re-keying is ever needed. The rebase constants are
//! int-tagged (`Inst::ConstInt`, the legacy `IRConst(int)` path). The new
//! values are single-use and scheduled at the end of the dispatch block, so
//! lowering splices them into the test expression exactly like any other
//! last-in-block value.
//!
//! # Guards (every one is load-bearing; refusal = no transform)
//!
//! 1. **Integer cases only**: every cond's numeric value (int- or float-tagged)
//!    must be an exact integer with `|c| <= 2^51`.
//! 2. **Affine**: `c_i == base + i*s` exactly in `i64` arithmetic, `s >= 1`
//!    (conds are sorted ascending and distinct by the `Terminator::Branch`
//!    contract; anything else is refused, not assumed).
//! 3. **`base >= 1`** — the exactness proof's precondition (below). This
//!    deliberately refuses `base == 0 && s >= 2` (e.g. `{0, 2, 4}`) and all
//!    negative-base sets (e.g. `{-3, -2, -1}`): both have *real* f64
//!    counterexamples, not just unproven cases (see "why base >= 1").
//! 4. **Cost thresholds**: `n >= 3` for `s == 1` (pure rebase), `n >= 7` for
//!    `s >= 2` (rebase + divide) — see the cost model.
//! 5. Already-dense sets (`base == 0 && s == 1`) are skipped: the emitter
//!    selects the O(1) form for them as-is.
//!
//! # Exactness proof
//!
//! Both matchers agree with plain f64 `==` against their case sets: the linear
//! forms (`SwitchWithDefault` scan, `If(Equal(...))`) compare `x == c_i`, and
//! the dense integer form takes case `i` iff the scrutinee is integral and
//! in-range — i.e. iff it `==` some `i` in `0..n` (`-0.0` indexes case 0 and
//! `-0.0 == 0`; NaN/inf fail the range check and match nothing, same as `==`).
//! So the transform is exact iff, with `v = fl(fl(x - base) / s)` (`fl` =
//! IEEE-754 round-to-nearest-even, the interpreter's `Subtract` and `py_div`):
//!
//! > for every f64 `x` and every `i` in `0..n`:  `v == i  ⇔  x == c_i`.
//!
//! **(⇒ for cases).** `base`, `c_i`, and `R_i := c_i - base = i*s` are
//! integers of magnitude `<= 2^52`, hence exact f64s, and the real difference
//! `c_i - base` is representable, so `fl(c_i - base) = R_i` exactly. The real
//! quotient `R_i / s` is exactly the f64 `i`, and division is correctly
//! rounded, so `fl(R_i / s) = i` exactly.
//!
//! **(⇐, nothing else maps).** Write `y := fl(x - base)`.
//!
//! *Division preimage, `i >= 1`*: `fl(y/s) = i` forces the real quotient into
//! `i`'s rounding window, i.e. `y ∈ [Y - s*δ⁻, Y + s*δ⁺]` with `Y := s*i`
//! (an exact f64 `<= 2^52`) and `δ⁻/δ⁺` the half-gaps from `i` to its f64
//! neighbours (`δ⁺ = 2^(b-53)`, `δ⁻ = 2^(b-54)` if `i = 2^b` else `2^(b-53)`,
//! where `2^b <= i < 2^(b+1)`). Let `2^m <= Y < 2^(m+1)`; the gap from `Y` to
//! the next f64 up is `2^(m-52)`, down is `2^(m-53)` if `Y = 2^m` else
//! `2^(m-52)`. Then the window is strictly inside both gaps:
//! - up: `s*δ⁺ = s*2^(b-53) <= (Y/2^b)*2^(b-53) = Y*2^-53 < 2^(m-52)`;
//! - down, `Y > 2^m`: same bound `< 2^(m-52)`;
//! - down, `Y = 2^m`, `i = 2^b`: `s = 2^(m-b)`, so `s*δ⁻ = 2^(m-54) < 2^(m-53)`;
//! - down, `Y = 2^m`, `i > 2^b`: `s*δ⁻ = (2^m/i)*2^(b-53) < 2^(m-53)`.
//!
//! All bounds are strict, so the only f64 in the window is `Y` itself and the
//! window's endpoints are not f64s (no round-half-to-even tie can produce
//! `i`). Hence `fl(y/s) = i ⇔ y = s*i` exactly.
//!
//! *Division preimage, `i = 0`*: `fl(y/s) = ±0` iff `|y| <= s*2^-1075`
//! (underflow incl. the half-way tie, which rounds to the even significand 0).
//! Subtraction below yields `y = 0` only for `x == base`, and every other
//! reachable `y` has `|y| >= 2^-53` (for `x` within `[base/2, 2*base]` the
//! subtraction is exact by Sterbenz and distinct f64s near `base >= 1` differ
//! by at least `2^-53`; outside that range `|x - base| >= base/2 >= 1/2`).
//! Since `s <= 2^52`, `s*2^-1075 < 2^-53`, so `fl(y/s) = ±0 ⇔ x == base = c_0`
//! (and `x == base` gives exactly `+0`, which the dense form maps to case 0).
//!
//! *Subtraction preimage*: fix `R := s*i >= 1` (exact, and `R < c_i` because
//! `base >= 1`). `fl(x - base) = R` forces the real `x - base` into `R`'s
//! rounding window, i.e. `x ∈ [c_i - ulp⁻(R)/2, c_i + ulp(R)/2]` (subtraction
//! is correctly rounded). With `2^B <= c_i < 2^(B+1)` and `2^b' <= R <
//! 2^(b'+1)`, `R < c_i` gives `b' <= B`, and the same three-case comparison as
//! above (half-widths `<= 2^(B-53)`, against `c_i`'s neighbour gaps
//! `2^(B-52)`/`2^(B-53)`, with `b' <= B-1` whenever `c_i = 2^B`) shows the
//! window contains no f64 but `c_i` and no representable tie point. Hence
//! `fl(x - base) = s*i ⇔ x = c_i`.
//!
//! *Non-finite `x`*: `y` and `v` stay NaN (resp. ±inf, since `base` and `s`
//! are finite and nonzero) and match nothing — the default outcome, same as
//! the original scan. `x = -0.0` cannot reach any case (`c_i >= base >= 1`).
//!
//! # Why `base >= 1` (the refusals are real miscompiles, not caution)
//!
//! - `base <= -1` rebases *away* from zero and absorbs: cases `{-3,-2,-1,0}`,
//!   `x = 1e-17` → `fl(x + 3) = 3.0` exactly (`1e-17 < ulp(3)/2`), so the
//!   rebased form takes the case that belongs to `x == 0` while the original
//!   takes the default.
//! - `base == 0, s >= 2` divides raw subnormals: cases `{0,2,4}`,
//!   `x = ±2^-1074` → `fl(x/2) = ±2^-1075` ties to `±0`, so the rebased form
//!   takes case 0 while the original takes the default.
//!
//! Both are pinned by `exactness` tests below (as raw f64 mapping
//! divergences, demonstrating the guard is load-bearing). Negative-base sets
//! *could* be supported under a `|c_i - base| < |c_i|` side condition, but
//! they do not occur in the corpus/pydori and are refused wholesale.
//!
//! # Cost model
//!
//! Interpreter evals per dispatch (each node evaluation counts 1, constants
//! included; `S` = scrutinee subtree evals, identical on both sides):
//!
//! - linear `SwitchWithDefault`: `1 + S + m + 1`, where `m` = case constants
//!   scanned (`k+1` for a hit on case `k`; `n` for the default/exit outcome);
//! - dense rebased `SwitchIntegerWithDefault`: `1 + S + e + 1`, where
//!   `e = 2` for `s == 1` (`Subtract` + const) or `e = 4` for `s >= 2`
//!   (+ `Divide` + const) — O(1), position-independent.
//!
//! With no profile information, outcomes are weighted uniformly (each case
//! hit once + one default outcome): `E[m] = n/2 + n/(n+1)`. Manufacture pays
//! iff `E[m] > e`: `n >= 3` for `s == 1` (2.25 > 2), `n >= 7` for `s >= 2`
//! (4.375 > 4). Static nodes move by `e - n` per dispatch block (n case
//! constants dropped, e rebase nodes added) — also strictly negative at both
//! thresholds. The thresholds are re-tunable at G3.5 with metric evidence
//! (D13); the model deliberately leans conservative so a hit-on-early-cases
//! distribution cannot regress `eval_count`.
//!
//! # What legacy did that this port deliberately does not
//!
//! Legacy normalized every integral affine progression with >= 2 cases —
//! any stride, base 0, negative bases — with no exactness analysis and no
//! cost model. The absorption/subnormal corners above were latent
//! miscompiles (unreachable in practice only because traced frontend
//! scrutinee/case sets were small non-negative integers). This port refuses
//! everything outside the proof (guards 1-3) and everything the cost model
//! scores as a loss (guard 4). Legacy also rewrote `block.test` in place on
//! the flat CFG; the MIR form wraps the test *value* instead — the scrutinee's
//! defining computation is never duplicated or moved, so an effectful or
//! expensive scrutinee is evaluated exactly once, exactly as before.
//!
//! # Pass discipline
//!
//! Deterministic: one ascending block scan, `Vec`s only. Iterative: no
//! recursion (invariant §3.4). Effects: the two new ops are pure and total
//! (`Subtract` never traps; `Divide` traps only on a zero divisor and `s >= 1`
//! is a nonzero constant), evaluated at the end of the dispatch block where
//! the branch was already about to dispatch — no new traps, no RNG, no
//! reordering. Mid-level IR stays binary (§3.3). Idempotent: manufactured
//! sets are dense 0-based and skipped on re-runs. Invalidation: the
//! terminator changes, so a changed run calls `invalidate_all` (the
//! conservative choice the other terminator-touching passes use).

use crate::analysis::Analyses;
use crate::mir::{CaseCond, Inst, Mir, Terminator};
use crate::ops::Op;
use crate::passes::Pass;

/// Cost-model threshold for a pure rebase (`s == 1`, 2 extra evals).
const MIN_CASES_REBASE: usize = 3;
/// Cost-model threshold for a strided rebase (`s >= 2`, 4 extra evals).
const MIN_CASES_STRIDED: usize = 7;
/// Magnitude bound on case values (exactness proof headroom: every derived
/// quantity stays well under 2^53).
const MAX_CASE_MAGNITUDE: i64 = 1 << 51;

/// The T3.11 pass. See the module docs.
#[derive(Debug, Default)]
pub struct NormalizeSwitch;

impl Pass for NormalizeSwitch {
    fn name(&self) -> &'static str {
        "normalize-switch"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        let mut changed = false;
        for b in 0..mir.blocks.len() {
            let Terminator::Branch { cases, .. } = &mir.blocks[b].terminator else {
                continue;
            };
            let Some(plan) = rebase_plan(cases) else {
                continue;
            };
            apply(mir, b, plan);
            changed = true;
        }
        if changed {
            analyses.invalidate_all();
        }
        changed
    }
}

/// An accepted rebase: scrutinee becomes `(x - base) / stride`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct RebasePlan {
    base: i64,
    stride: i64,
}

/// The exact integer value of a case cond, or `None` when the cond is
/// non-integral or out of the proof's magnitude bound. Float-tagged integral
/// conds (e.g. `3.0`) are accepted by numeric value — case matching is
/// numeric end to end (Python `==` / f64 `==`), so the tag is not load-bearing
/// for the rebased form.
#[allow(
    clippy::float_cmp,
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation
)]
fn case_int_value(cond: CaseCond) -> Option<i64> {
    match cond {
        CaseCond::Int(v) => (v.abs() <= MAX_CASE_MAGNITUDE).then_some(v),
        CaseCond::Float(v) => {
            // |v| <= 2^51 makes the cast exact; trunc()==v makes it integral.
            // (-0.0 passes as 0, then fails the base >= 1 guard downstream.)
            (v.trunc() == v && v.abs() <= MAX_CASE_MAGNITUDE as f64).then_some(v as i64)
        }
    }
}

/// Decides whether (and how) to rebase a case list. `None` = refuse. All
/// guards from the module docs live here.
fn rebase_plan(cases: &[(CaseCond, crate::mir::BlockId)]) -> Option<RebasePlan> {
    let n = cases.len();
    if n < MIN_CASES_REBASE {
        return None;
    }
    let mut values = Vec::with_capacity(n);
    for &(cond, _) in cases {
        values.push(case_int_value(cond)?);
    }
    let base = values[0];
    // The terminator contract sorts cases ascending with distinct conds, so
    // stride >= 1 for affine sets; hand-built MIR violating that is refused
    // (checked, not assumed).
    let stride = values[1].checked_sub(base)?;
    if stride < 1 {
        return None;
    }
    let mut expected = base;
    for &v in &values[1..] {
        expected = expected.checked_add(stride)?;
        if v != expected {
            return None;
        }
    }
    if base == 0 && stride == 1 {
        // Already dense 0-based: the emitter selects the O(1) form as-is.
        return None;
    }
    if base < 1 {
        // Exactness precondition (module docs: absorption / subnormal
        // underflow counterexamples).
        return None;
    }
    let min_cases = if stride == 1 {
        MIN_CASES_REBASE
    } else {
        MIN_CASES_STRIDED
    };
    if n < min_cases {
        return None;
    }
    Some(RebasePlan { base, stride })
}

/// Applies an accepted plan to block `b`: schedules the rebase ops and
/// relabels the conds `0..n-1` (targets and default untouched).
fn apply(mir: &mut Mir, b: crate::mir::BlockId, plan: RebasePlan) {
    let Terminator::Branch { test, .. } = &mir.blocks[b].terminator else {
        unreachable!("apply is only called for Branch terminators");
    };
    let test = *test;
    let base_c = mir.push_inst(Inst::ConstInt(plan.base));
    let sub = mir.push_inst(Inst::Op {
        op: Op::Subtract,
        pure_node: true,
        args: vec![test, base_c],
    });
    mir.blocks[b].insts.push(sub);
    let new_test = if plan.stride == 1 {
        sub
    } else {
        let stride_c = mir.push_inst(Inst::ConstInt(plan.stride));
        let div = mir.push_inst(Inst::Op {
            op: Op::Divide,
            pure_node: true,
            args: vec![sub, stride_c],
        });
        mir.blocks[b].insts.push(div);
        div
    };
    let Terminator::Branch { test, cases, .. } = &mut mir.blocks[b].terminator else {
        unreachable!("apply is only called for Branch terminators");
    };
    *test = new_test;
    for (i, (cond, _)) in cases.iter_mut().enumerate() {
        *cond = CaseCond::Int(i64::try_from(i).expect("case count fits i64"));
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::float_cmp)] // exact f64 equality is the assertion contract here

    use super::*;
    use crate::analysis::Analyses;
    use crate::mir::{BlockId, BlockRef, IndexRef, Place, Value};

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        NormalizeSwitch.run(mir, &mut analyses)
    }

    fn concrete_place(block: i64, index: i64) -> Place {
        Place {
            block: BlockRef::Concrete(block),
            index: IndexRef::Const(index),
            offset: 0,
        }
    }

    fn sched(mir: &mut Mir, block: BlockId, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn store_to(mir: &mut Mir, block: BlockId, out_index: i64, value: i64) {
        let c = mir.push_inst(Inst::ConstInt(value));
        sched(
            mir,
            block,
            Inst::Store {
                place: concrete_place(20, out_index),
                value: c,
            },
        );
    }

    /// A multi-way branch on `Get(21[0])` with the given int conds, one arm
    /// per cond, plus a default arm. Returns `(mir, scrutinee, arm_ids,
    /// default_id)`.
    fn switch_mir(conds: &[CaseCond]) -> (Mir, Value, Vec<BlockId>, BlockId) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let arms: Vec<BlockId> = (0..conds.len()).map(|_| mir.push_block()).collect();
        let d = mir.push_block();
        for (i, &arm) in arms.iter().enumerate() {
            store_to(&mut mir, arm, 0, i64::try_from(i).unwrap() + 10);
        }
        store_to(&mut mir, d, 0, 99);
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: conds.iter().copied().zip(arms.iter().copied()).collect(),
            default: Some(d),
        };
        (mir, x, arms, d)
    }

    fn int_conds(values: &[i64]) -> Vec<CaseCond> {
        values.iter().map(|&v| CaseCond::Int(v)).collect()
    }

    /// Asserts block 0's terminator is a Branch with dense conds `0..n` over
    /// `arms` and returns its test value.
    fn assert_dense(mir: &Mir, arms: &[BlockId], default: BlockId) -> Value {
        let Terminator::Branch {
            test,
            cases,
            default: d,
        } = &mir.blocks[0].terminator
        else {
            panic!("must stay a branch");
        };
        let expected: Vec<(CaseCond, BlockId)> = arms
            .iter()
            .enumerate()
            .map(|(i, &t)| (CaseCond::Int(i64::try_from(i).unwrap()), t))
            .collect();
        assert_eq!(
            cases, &expected,
            "conds must be dense 0-based, targets kept"
        );
        assert_eq!(*d, Some(default), "default untouched");
        *test
    }

    // ------------------------------------------------------------------
    // Positive manufacture
    // ------------------------------------------------------------------

    #[test]
    fn offset_rebase_subtract_only() {
        // {1, 2, 3}: base 1, stride 1 -> Subtract(x, 1), conds {0, 1, 2}.
        let (mut mir, x, arms, d) = switch_mir(&int_conds(&[1, 2, 3]));
        assert!(run_pass(&mut mir));
        let test = assert_dense(&mir, &arms, d);
        let Inst::Op {
            op: Op::Subtract,
            pure_node: true,
            args,
        } = mir.inst(test)
        else {
            panic!("test must be a Subtract, got {:?}", mir.inst(test));
        };
        assert_eq!(args[0], x);
        assert_eq!(*mir.inst(args[1]), Inst::ConstInt(1));
        // Scheduled at the end of the dispatch block, after the load.
        assert_eq!(mir.blocks[0].insts.as_slice(), &[x, test]);
    }

    #[test]
    fn strided_rebase_subtract_and_divide() {
        // {3, 5, 7, 9, 11, 13, 15}: base 3, stride 2, n = 7 (the strided
        // threshold) -> Divide(Subtract(x, 3), 2), conds {0..6}.
        let (mut mir, x, arms, d) = switch_mir(&int_conds(&[3, 5, 7, 9, 11, 13, 15]));
        assert!(run_pass(&mut mir));
        let test = assert_dense(&mir, &arms, d);
        let Inst::Op {
            op: Op::Divide,
            pure_node: true,
            args,
        } = mir.inst(test)
        else {
            panic!("test must be a Divide, got {:?}", mir.inst(test));
        };
        assert_eq!(*mir.inst(args[1]), Inst::ConstInt(2));
        let Inst::Op {
            op: Op::Subtract,
            args: sub_args,
            ..
        } = mir.inst(args[0])
        else {
            panic!("dividend must be the Subtract");
        };
        assert_eq!(sub_args[0], x);
        assert_eq!(*mir.inst(sub_args[1]), Inst::ConstInt(3));
        assert_eq!(mir.blocks[0].insts.as_slice(), &[x, args[0], test]);
    }

    #[test]
    fn power_of_two_stride_fires() {
        // {4, 8, 12, 16, 20, 24, 28}: base 4, stride 4.
        let (mut mir, _, arms, d) = switch_mir(&int_conds(&[4, 8, 12, 16, 20, 24, 28]));
        assert!(run_pass(&mut mir));
        assert_dense(&mir, &arms, d);
    }

    #[test]
    fn float_tagged_integral_conds_fire() {
        // {1.0, 2.0, 3.0} float-tagged: numeric values are affine integers.
        let conds = vec![
            CaseCond::Float(1.0),
            CaseCond::Float(2.0),
            CaseCond::Float(3.0),
        ];
        let (mut mir, _, arms, d) = switch_mir(&conds);
        assert!(run_pass(&mut mir));
        assert_dense(&mir, &arms, d);
    }

    #[test]
    fn missing_default_is_supported() {
        // Post-shape branches can be default-less (unmatched -> exit).
        let (mut mir, _, _, _) = switch_mir(&int_conds(&[2, 3, 4]));
        let Terminator::Branch { test, cases, .. } = mir.blocks[0].terminator.clone() else {
            panic!();
        };
        mir.blocks[0].terminator = Terminator::Branch {
            test,
            cases,
            default: None,
        };
        assert!(run_pass(&mut mir));
        let Terminator::Branch { cases, default, .. } = &mir.blocks[0].terminator else {
            panic!();
        };
        assert_eq!(*default, None);
        assert_eq!(cases[0].0, CaseCond::Int(0));
        assert_eq!(cases[2].0, CaseCond::Int(2));
    }

    #[test]
    fn idempotent() {
        let (mut mir, _, _, _) = switch_mir(&int_conds(&[1, 2, 3]));
        assert!(run_pass(&mut mir));
        let after_first = mir.clone();
        assert!(!run_pass(&mut mir), "manufactured sets are dense: skipped");
        assert_eq!(mir.blocks, after_first.blocks);
    }

    // ------------------------------------------------------------------
    // Refusals
    // ------------------------------------------------------------------

    fn assert_refused(conds: &[CaseCond], why: &str) {
        let (mut mir, x, _, _) = switch_mir(conds);
        let before = mir.blocks[0].terminator.clone();
        assert!(!run_pass(&mut mir), "{why}");
        assert_eq!(mir.blocks[0].terminator, before, "{why}");
        assert_eq!(mir.blocks[0].insts.as_slice(), &[x], "{why}: no new insts");
    }

    #[test]
    fn already_dense_is_skipped() {
        assert_refused(&int_conds(&[0, 1, 2]), "dense 0-based needs no help");
    }

    #[test]
    fn base_zero_strided_is_refused() {
        // {0, 2, 4, 6, 8, 10, 12}: above the strided threshold but base 0 —
        // the subnormal-underflow counterexample (proven divergent in the
        // exactness tests below).
        assert_refused(
            &int_conds(&[0, 2, 4, 6, 8, 10, 12]),
            "base 0 with stride >= 2 is outside the exactness proof",
        );
    }

    #[test]
    fn negative_base_is_refused() {
        assert_refused(
            &int_conds(&[-3, -2, -1]),
            "negative base rebases away from zero (absorption)",
        );
        assert_refused(
            &int_conds(&[-2, 0, 2, 4, 6, 8, 10]),
            "negative base, strided",
        );
    }

    #[test]
    fn non_affine_is_refused() {
        assert_refused(&int_conds(&[1, 2, 4]), "not an affine progression");
        assert_refused(
            &int_conds(&[3, 5, 7, 10, 11, 13, 15]),
            "one case off-pattern",
        );
    }

    #[test]
    fn non_integral_conds_are_refused() {
        assert_refused(
            &[
                CaseCond::Float(0.5),
                CaseCond::Float(1.5),
                CaseCond::Float(2.5),
            ],
            "non-integral float conds (affine with stride 1, but not integers)",
        );
        assert_refused(
            &[
                CaseCond::Int(1),
                CaseCond::Float(1.5),
                CaseCond::Int(2),
                CaseCond::Float(2.5),
                CaseCond::Int(3),
                CaseCond::Float(3.5),
                CaseCond::Int(4),
            ],
            "mixed int/half conds",
        );
    }

    #[test]
    fn below_threshold_is_refused() {
        assert_refused(&int_conds(&[5, 6]), "n = 2 < 3 for stride 1");
        assert_refused(
            &int_conds(&[3, 5, 7, 9, 11, 13]),
            "n = 6 < 7 for stride 2 (cost model)",
        );
    }

    #[test]
    fn out_of_magnitude_conds_are_refused() {
        let big = MAX_CASE_MAGNITUDE + 1;
        assert_refused(
            &int_conds(&[big, big + 1, big + 2]),
            "case magnitude above 2^51",
        );
    }

    #[test]
    fn single_case_and_two_way_branches_are_untouched() {
        // The {0: else, None: then} two-way form and single-case switches are
        // the emitter's If territory; n < 3 never manufactures.
        let (mut mir, _, _, _) = switch_mir(&int_conds(&[5]));
        assert!(!run_pass(&mut mir));
        let (mut mir, _, _, _) = switch_mir(&int_conds(&[0]));
        assert!(!run_pass(&mut mir));
    }

    // ------------------------------------------------------------------
    // Exactness: brute-force the case-mapping equivalence
    // ------------------------------------------------------------------

    /// The original matcher: index of the first case `x` equals, else None
    /// (`SwitchWithDefault` scan semantics; f64 `==`).
    fn original_outcome(x: f64, base: i64, stride: i64, n: usize) -> Option<usize> {
        #[allow(clippy::cast_precision_loss)]
        (0..n).find(|&i| x == (base + i64::try_from(i).unwrap() * stride) as f64)
    }

    /// The manufactured matcher: `v = (x - base) / stride` through the dense
    /// integer form's range/integrality check (`interpret.rs`
    /// `SwitchIntegerWithDefault`).
    #[allow(
        clippy::cast_precision_loss,
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss
    )]
    fn rebased_outcome(x: f64, base: i64, stride: i64, n: usize) -> Option<usize> {
        let mut v = x - base as f64;
        if stride != 1 {
            v /= stride as f64;
        }
        (v >= 0.0 && v < n as f64 && v.trunc() == v).then_some(v as usize)
    }

    /// Probe values around every case plus global adversaries: neighbours at
    /// several ulp distances, halfway-ish offsets, subnormals, huge values,
    /// non-integral and non-finite values.
    fn probes(base: i64, stride: i64, n: usize) -> Vec<f64> {
        #[allow(clippy::cast_precision_loss)]
        let mut xs: Vec<f64> = vec![
            0.0,
            -0.0,
            f64::MIN_POSITIVE,
            -f64::MIN_POSITIVE,
            f64::from_bits(1), // smallest subnormal
            -f64::from_bits(1),
            f64::from_bits(2),
            -f64::from_bits(2),
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            1e300,
            -1e300,
            9_007_199_254_740_992.0, // 2^53
            -9_007_199_254_740_992.0,
            (base as f64) / 2.0,
            (base as f64) * 2.0,
            (base + i64::try_from(n).unwrap() * stride) as f64, // one past the last case
            (base - stride) as f64,                             // one before the first
        ];
        for i in 0..n {
            #[allow(clippy::cast_precision_loss)]
            let c = (base + i64::try_from(i).unwrap() * stride) as f64;
            xs.push(c);
            let mut up = c;
            let mut down = c;
            for _ in 0..4 {
                up = up.next_up();
                down = down.next_down();
                xs.push(up);
                xs.push(down);
            }
            for d in [0.5, 0.25, 1e-9, 1e-17] {
                xs.push(c + d);
                xs.push(c - d);
                xs.push(c + d * f64::from(i32::try_from(stride).unwrap_or(1)));
            }
        }
        xs
    }

    #[test]
    fn exactness_brute_force_over_accepted_configs() {
        // Every accepted (base, stride, n) family the guards admit, hammered
        // with adversarial probes: the manufactured mapping must equal the
        // original mapping for every probe.
        let configs: &[(i64, i64, usize)] = &[
            (1, 1, 3),
            (2, 1, 4),
            (7, 1, 9),
            (100, 1, 3),
            (65_535, 1, 5),
            (1, 2, 7),
            (3, 2, 7),
            (1, 3, 8),
            (5, 4, 7),
            (9, 7, 9),
            (1, 16, 7),
            ((1 << 40) + 1, 5, 7),
            (MAX_CASE_MAGNITUDE - 100, 1, 8),
            (1, MAX_CASE_MAGNITUDE / 8, 7),
        ];
        for &(base, stride, n) in configs {
            // The config must actually be accepted by the guards.
            let conds: Vec<CaseCond> = (0..n)
                .map(|i| CaseCond::Int(base + i64::try_from(i).unwrap() * stride))
                .collect();
            let pairs: Vec<(CaseCond, BlockId)> = conds.iter().map(|&c| (c, 1usize)).collect();
            assert_eq!(
                rebase_plan(&pairs),
                Some(RebasePlan { base, stride }),
                "config ({base}, {stride}, {n}) must be accepted"
            );
            for x in probes(base, stride, n) {
                let original = original_outcome(x, base, stride, n);
                let rebased = rebased_outcome(x, base, stride, n);
                assert_eq!(
                    original,
                    rebased,
                    "mapping diverges for x={x:?} (bits {:#x}) under (base={base}, stride={stride}, n={n})",
                    x.to_bits()
                );
            }
        }
    }

    #[test]
    fn refused_configs_have_real_divergences() {
        // The guard refusals are load-bearing: the would-be mappings diverge
        // on concrete f64 inputs.
        //
        // base 0, stride 2 ({0, 2, 4, ...}): the smallest subnormal divides
        // to a tie that rounds to +0 -> case 0; the original scan defaults.
        let x = f64::from_bits(1);
        assert_eq!(original_outcome(x, 0, 2, 7), None);
        assert_eq!(rebased_outcome(x, 0, 2, 7), Some(0));
        // base -3 ({-3, -2, -1, 0}): 1e-17 is absorbed by the +3 rebase and
        // lands exactly on rebased case 3 (the original case 0 = 0.0).
        let x = 1e-17;
        assert_eq!(original_outcome(x, -3, 1, 4), None);
        assert_eq!(rebased_outcome(x, -3, 1, 4), Some(3));
    }

    // ------------------------------------------------------------------
    // End-to-end: frontend CFG -> pipeline with/without normalization
    // ------------------------------------------------------------------

    use crate::cfg::{
        BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
        TempBlockDef,
    };
    use crate::diff::{DiffConfig, DiffOutcome, diff_with};
    use crate::interpret::Interpreter;
    use crate::nodes::format_engine_node;
    use crate::passes::Pipeline;
    use crate::passes::dce::DcePass;
    use crate::passes::gvn::GvnRewritePass;
    use crate::passes::if_convert::IfConvert;
    use crate::passes::licm::LicmPass;
    use crate::passes::mem2reg::Mem2Reg;
    use crate::passes::sccp::Sccp;
    use crate::passes::shape::ShapePass;
    use crate::passes::switch_form::SwitchForm;
    use crate::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

    /// The frontend CFG for an if/elif chain with affine non-dense constants:
    ///
    /// ```text
    /// t <- Get(-3[0])
    /// if t == 3 { 20[0] <- 10 } elif t == 4 { 20[0] <- 11 }
    /// elif t == 5 { 20[0] <- 12 } else { 20[0] <- 99 }
    /// ```
    ///
    /// Switch formation (T3.6) merges the chain into one multi-way block with
    /// cases {3, 4, 5}; this pass rebases it to the dense {0, 1, 2}.
    fn affine_chain_cfg() -> Cfg {
        let mut cfg = Cfg::default();
        cfg.strings.push("t".to_owned());
        cfg.temp_blocks.push(TempBlockDef { name: 0, size: 1 });
        let node = |cfg: &mut Cfg, n: Node| {
            cfg.nodes.push(n);
            cfg.nodes.len() - 1
        };
        let place = |cfg: &mut Cfg, block: BlockValue, index: i64| {
            cfg.places.push(CfgPlace {
                block,
                index: IndexValue::Int(index),
                offset: 0,
            });
            cfg.places.len() - 1
        };
        // Blocks: 0..3 = checks (entry = check 0), 3..6 = arms, 6 = else, 7 = exit.
        let arm_base = 3;
        let else_arm = 6;
        let exit = 7;
        for k in 0..3usize {
            let mut stmts = Vec::new();
            if k == 0 {
                let in_p = place(&mut cfg, BlockValue::Int(-3), 0);
                let get_in = node(&mut cfg, Node::Get(in_p));
                let t_p = place(&mut cfg, BlockValue::Temp(0), 0);
                stmts.push(node(
                    &mut cfg,
                    Node::Set {
                        place: t_p,
                        value: get_in,
                    },
                ));
            }
            let t_p = place(&mut cfg, BlockValue::Temp(0), 0);
            let get_t = node(&mut cfg, Node::Get(t_p));
            let c = node(&mut cfg, Node::ConstInt(i64::try_from(k).unwrap() + 3));
            let eq = node(
                &mut cfg,
                Node::PureInstr {
                    op: Op::Equal,
                    args: vec![get_t, c],
                },
            );
            let next = if k + 1 < 3 { k + 1 } else { else_arm };
            cfg.blocks.push(BasicBlock {
                statements: stmts,
                test: eq,
                outgoing: vec![
                    Edge {
                        cond: EdgeCond::Int(0),
                        target: next,
                    },
                    Edge {
                        cond: EdgeCond::None,
                        target: arm_base + k,
                    },
                ],
            });
        }
        for value in [10i64, 11, 12, 99] {
            let out_p = place(&mut cfg, BlockValue::Int(20), 0);
            let v = node(&mut cfg, Node::ConstInt(value));
            let set = node(
                &mut cfg,
                Node::Set {
                    place: out_p,
                    value: v,
                },
            );
            let zt = node(&mut cfg, Node::ConstInt(0));
            cfg.blocks.push(BasicBlock {
                statements: vec![set],
                test: zt,
                outgoing: vec![Edge {
                    cond: EdgeCond::None,
                    target: exit,
                }],
            });
        }
        let zt = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![],
            test: zt,
            outgoing: vec![],
        });
        cfg
    }

    /// The full registry prefix below W5 (W1 + W2 + W3 + W4): the comparison
    /// baseline that isolates this pass's contribution.
    fn w4_pipeline() -> Pipeline {
        Pipeline::new(w4_passes())
    }

    fn w4_passes() -> Vec<Box<dyn crate::passes::Pass>> {
        vec![
            Box::new(Sccp) as Box<dyn crate::passes::Pass>,
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(Mem2Reg),
            Box::new(Sccp),
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(SwitchForm),
            Box::new(LicmPass),
            Box::new(ShapePass),
            Box::new(IfConvert),
        ]
    }

    fn w4_plus_normalize() -> Pipeline {
        let mut passes = w4_passes();
        passes.push(Box::new(NormalizeSwitch));
        Pipeline::new(passes)
    }

    #[test]
    fn affine_chain_becomes_dense_switch_with_eval_reduction() {
        let cfg = affine_chain_cfg();
        let without = compile_cfg_with_pipeline(&cfg, &w4_pipeline()).unwrap();
        let with = compile_cfg_with_pipeline(&cfg, &w4_plus_normalize()).unwrap();
        // Without normalization the merged {3, 4, 5} chain is a linear
        // SwitchWithDefault; with it, the emitter selects the O(1) form.
        let without_dump = format_engine_node(&without.arena, without.root);
        let with_dump = format_engine_node(&with.arena, with.root);
        assert!(
            without_dump.contains("SwitchWithDefault"),
            "baseline must be the linear scan:\n{without_dump}"
        );
        assert!(
            with_dump.contains("SwitchIntegerWithDefault"),
            "manufactured cases must emit an integer switch:\n{with_dump}"
        );
        // Behavior identical on hits, misses, non-integral, NaN; eval count
        // strictly lower for late hits and the default outcome, dispatch
        // unchanged (same block structure).
        for input in [3.0, 4.0, 5.0, 6.0, 0.0, 4.5, f64::NAN] {
            let run = |nodes: &crate::nodes::EngineNodes| {
                let mut interp = Interpreter::new(0);
                interp.set_block(-3, vec![input]);
                interp.run(nodes).unwrap();
                let out = interp.block(20).unwrap()[0];
                (out, interp.dispatch_count(), interp.eval_count())
            };
            let (out_without, dispatch_without, eval_without) = run(&without);
            let (out_with, dispatch_with, eval_with) = run(&with);
            assert_eq!(out_with, out_without, "input {input}");
            assert_eq!(
                dispatch_with, dispatch_without,
                "input {input}: dispatch must be unchanged"
            );
            // Cost model: rebase adds 2 evals, the scan costs m (1 for input
            // 3.0 ... 3 for 5.0/miss). Strictly better past the second case.
            let scanned: u64 = if input == 3.0 {
                1
            } else if input == 4.0 {
                2
            } else {
                3
            };
            assert_eq!(
                eval_with,
                eval_without - scanned + 2,
                "input {input}: eval delta must match the cost model"
            );
        }
        // The standard level (which includes this pass via the registry)
        // produces the same dense form, and the chain merge means a single
        // dispatch block (the T3.6 + T3.11 composition).
        let standard = compile_cfg(&cfg, Level::Standard).unwrap();
        assert!(
            format_engine_node(&standard.arena, standard.root).contains("SwitchIntegerWithDefault")
        );
        // And dispatch drops vs minimal (the chain's round trips are gone).
        let minimal = compile_cfg(&cfg, Level::Minimal).unwrap();
        let run_dispatch = |nodes: &crate::nodes::EngineNodes| {
            let mut interp = Interpreter::new(0);
            interp.set_block(-3, vec![5.0]);
            interp.run(nodes).unwrap();
            interp.dispatch_count()
        };
        assert!(run_dispatch(&standard) < run_dispatch(&minimal));
    }

    #[test]
    fn affine_chain_diffs_clean_against_minimal() {
        let cfg = affine_chain_cfg();
        for seed in [0u64, 1, 42] {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed ^ 0xABCD,
                eval_budget: 100_000,
            };
            for (label, pipeline) in [
                (
                    "normalize only",
                    Pipeline::new(vec![
                        Box::new(NormalizeSwitch) as Box<dyn crate::passes::Pass>
                    ]),
                ),
                ("w4 + normalize", w4_plus_normalize()),
            ] {
                let outcome = diff_with(
                    &cfg,
                    |c| compile_cfg(c, Level::Minimal),
                    |c| compile_cfg_with_pipeline(c, &pipeline),
                    &config,
                );
                assert!(
                    matches!(outcome, DiffOutcome::Match),
                    "{label} seed {seed}: {outcome:?}"
                );
            }
        }
    }

    #[test]
    fn registry_standard_includes_normalize_last() {
        let names: Vec<&'static str> = crate::passes::passes_for_level(Level::Standard)
            .iter()
            .map(|p| p.name())
            .collect();
        assert_eq!(
            names.last(),
            Some(&"normalize-switch"),
            "normalize-switch must be the last standard pass: {names:?}"
        );
        let fast: Vec<&'static str> = crate::passes::passes_for_level(Level::Fast)
            .iter()
            .map(|p| p.name())
            .collect();
        assert!(
            !fast.contains(&"normalize-switch"),
            "fast must not include W5"
        );
    }
}
