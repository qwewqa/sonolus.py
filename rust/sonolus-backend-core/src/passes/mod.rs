//! Pass framework and optimization-level configuration (PORT.md T2.2).
//!
//! This module hosts the plain ordered pass pipeline the optimizer runs on
//! mid-level [`Mir`]. Per ARCHITECTURE §3 the pass manager is **not** a
//! requires/preserves constraint solver: it is a deterministic, ordered list of
//! passes, run front to back, each fully responsible for invalidating the
//! [`Analyses`] cache after any mutation it makes (the discipline documented in
//! [`crate::analysis`]).
//!
//! # The `Pass` trait
//!
//! A pass is a stateless transform with a stable [`name`](Pass::name) and a
//! [`run`](Pass::run) method returning a **changed flag**: `true` iff it
//! mutated the MIR. The flag drives nothing in a plain prefix pipeline today
//! (passes run exactly once each), but it is the contract a future
//! fixpoint/until-stable driver would consume, and the per-pass debug log
//! surfaces it so a "claims-no-change-but-mutated" bug is visible.
//!
//! # Invalidation responsibility (read before writing a pass)
//!
//! The [`Analyses`] cache cannot see MIR mutation. **Every pass that returns
//! `true` must have invalidated** the cache appropriately before returning
//! (`invalidate_cfg`/`invalidate_values`/`invalidate_all` — see the analysis
//! module docs). The [`Pipeline`] leans on T2.1's debug fingerprinting to catch
//! violations: in debug builds it re-fingerprints the MIR after each pass and,
//! if a pass reported "no change" but the MIR actually changed, panics loudly
//! (a pass that mutates *must* report `true`; one that reports `true` but forgot
//! to invalidate trips the analysis cache's own fingerprint guard on the next
//! analysis request). Release builds skip both checks.
//!
//! # Levels are pipeline prefixes (decisions D5/D9)
//!
//! There is exactly one ordered optimization pipeline — the [`registry`]. Each
//! entry carries a [`Stage`] tag (which wave it belongs to). A [`Level`] selects
//! a **prefix** of that list:
//!
//! - [`Level::Minimal`] — empty prefix: no optimization passes (the baseline).
//! - [`Level::Fast`] — through wave [`Stage::W1`].
//! - [`Level::Standard`] — the whole list.
//!
//! [`passes_for_level`] computes the prefix. Wave tasks append their pass
//! constructors to the registry with the right stage tag; nothing else in this
//! module changes. W1 is registered: T3.1 SCCP → T3.2 GVN+rules → T3.3 DCE,
//! in pipeline order.
//!
//! # Determinism
//!
//! The pipeline is a `Vec` walked front to back; passes are constructed in
//! registry order. No ordering escapes to output beyond the registry's own
//! order (invariant §3.5).

pub mod dce;
pub mod gvn;
pub mod rules;
pub mod sccp;

use std::time::Duration;

use crate::analysis::Analyses;
use crate::mir::Mir;
use crate::pipeline::Level;

/// One optimization pass: a stateless MIR-to-MIR transform.
///
/// Passes are constructed by the [`registry`] and owned by a [`Pipeline`]. A
/// pass holds no per-run state; everything it needs comes through `run`.
pub trait Pass {
    /// A stable, human-readable name (used in debug logs and stats; should be
    /// unique within the registry).
    fn name(&self) -> &'static str;

    /// Runs the pass over `mir`, using and (after mutation) invalidating
    /// `analyses`. Returns `true` iff the MIR was mutated.
    ///
    /// Contract (enforced in debug builds by [`Pipeline`]): the return value
    /// must be `true` whenever the MIR content actually changed, and the pass
    /// must have called the appropriate `analyses.invalidate_*` before any
    /// later analysis request observes the change.
    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool;
}

/// The wave/stage a registry pass belongs to. Levels select a prefix up to and
/// including a stage (see [`passes_for_level`]). Variants are ordered by the
/// pipeline position of their wave so `<=` comparison is the prefix test.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Stage {
    /// Wave W1 (SCCP, GVN + rewrite rules, ADCE/branch simplification). The
    /// `fast` level stops here.
    W1,
    /// Wave W2 (Mem2Reg/SROA, copy coalescing, allocation quality).
    W2,
    /// Wave W3 (switch formation, LICM, micro-unroll).
    W3,
    /// Wave W4 (expression-level if-conversion, block shaping).
    W4,
}

impl Stage {
    /// The highest stage included at `level`, or `None` for `minimal` (no
    /// optimization passes at all). A registry pass is selected iff its stage
    /// is `<=` this value.
    fn cutoff(level: Level) -> Option<Self> {
        match level {
            Level::Minimal => None,
            Level::Fast => Some(Self::W1),
            Level::Standard => Some(Self::W4),
        }
    }
}

/// A registry entry: a stage tag plus a constructor for the pass. The
/// constructor is a function pointer so the registry is a plain `const`-shaped
/// table with no per-build allocation until a level actually instantiates it.
#[derive(Debug)]
pub struct RegistryEntry {
    pub stage: Stage,
    pub make: fn() -> Box<dyn Pass>,
}

/// The single ordered optimization pipeline (decisions D5/D9). Levels are
/// prefixes of this list (see [`passes_for_level`]).
///
/// Wave tasks (T3.x) append their pass constructors here in pipeline-execution
/// order, grouped by ascending [`Stage`] — `passes_for_level` relies on the
/// prefix property (every selected stage's entries precede every unselected
/// stage's entries). The W1 order is SCCP → GVN+rules → DCE.
pub fn registry() -> &'static [RegistryEntry] {
    &[
        // ===== Wave W1 — order: SCCP (T3.1), GVN+rules (T3.2), DCE (T3.3) =====
        //
        // T3.1: SCCP (sparse conditional constant propagation; src/passes/sccp.rs).
        RegistryEntry {
            stage: Stage::W1,
            make: || Box::new(sccp::Sccp),
        },
        // T3.2: GVN + rewrite rules (canonical commutative ordering, const
        // folding, algebraic identities, dominator GVN; src/passes/gvn.rs).
        RegistryEntry {
            stage: Stage::W1,
            make: || Box::new(gvn::GvnRewritePass),
        },
        // T3.3: DCE + branch simplification + jump threading (src/passes/dce.rs).
        RegistryEntry {
            stage: Stage::W1,
            make: || Box::new(dce::DcePass),
        },
    ]
}

/// The passes to run at `level`: the [`registry`] prefix whose stage is within
/// the level's cutoff, constructed in registry order. Returns an empty `Vec`
/// for `minimal` (the differential baseline never runs optimization passes).
pub fn passes_for_level(level: Level) -> Vec<Box<dyn Pass>> {
    let Some(cutoff) = Stage::cutoff(level) else {
        return Vec::new();
    };
    registry()
        .iter()
        .take_while(|entry| entry.stage <= cutoff)
        .map(|entry| (entry.make)())
        .collect()
}

/// Per-pass execution record, collected when stats are enabled.
#[derive(Debug, Clone)]
pub struct PassStat {
    /// The pass's [`name`](Pass::name).
    pub name: &'static str,
    /// Whether the pass reported a mutation.
    pub changed: bool,
    /// Wall-clock time spent in [`Pass::run`] (for T2.4 metrics).
    pub elapsed: Duration,
}

/// Optional hooks for observing pipeline execution. The default
/// (`Hooks::default()` / `None` everywhere) keeps the release path lean — no
/// timing, no logging, no allocation per pass.
#[derive(Default)]
pub struct Hooks<'a> {
    /// When set, each pass is timed and a [`PassStat`] is pushed here (for the
    /// T2.4 per-pass metrics). Timing is only taken when this is `Some`.
    pub stats: Option<&'a mut Vec<PassStat>>,
    /// When set, called once per pass after it runs (debug logging hook). The
    /// borrow is mutable so a sink can accumulate.
    pub log: Option<&'a mut dyn FnMut(&PassStat)>,
}

impl std::fmt::Debug for Hooks<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Hooks")
            .field("stats", &self.stats.as_ref().map(|s| s.len()))
            .field("log", &self.log.as_ref().map(|_| "<fn>"))
            .finish()
    }
}

/// A plain ordered pass pipeline (no constraint solving — ARCHITECTURE §3).
///
/// Construct one with [`Pipeline::for_level`] (or [`Pipeline::new`] for tests),
/// then [`run`](Pipeline::run) it over a MIR. Execution is front-to-back,
/// deterministic, each pass owning its own invalidation.
pub struct Pipeline {
    passes: Vec<Box<dyn Pass>>,
}

impl std::fmt::Debug for Pipeline {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Pipeline")
            .field(
                "passes",
                &self.passes.iter().map(|p| p.name()).collect::<Vec<_>>(),
            )
            .finish()
    }
}

impl Pipeline {
    /// The pipeline for an optimization level (the [`registry`] prefix).
    pub fn for_level(level: Level) -> Self {
        Self {
            passes: passes_for_level(level),
        }
    }

    /// A pipeline from an explicit pass list (test/bespoke use).
    pub fn new(passes: Vec<Box<dyn Pass>>) -> Self {
        Self { passes }
    }

    /// The number of passes in this pipeline.
    pub fn len(&self) -> usize {
        self.passes.len()
    }

    /// Whether the pipeline has no passes (the `minimal` level, and every level
    /// until W1 lands).
    pub fn is_empty(&self) -> bool {
        self.passes.is_empty()
    }

    /// Runs every pass once, in order, over `mir`. Returns `true` iff any pass
    /// mutated the MIR.
    pub fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        self.run_with_hooks(mir, analyses, &mut Hooks::default())
    }

    /// [`run`](Self::run) with [`Hooks`] for stats/logging. In debug builds
    /// this also enforces the changed-flag contract: a pass that reports "no
    /// change" but actually mutated the MIR panics (it must return `true`; the
    /// analysis cache's own fingerprint guard separately catches a pass that
    /// returns `true` but forgets to invalidate).
    pub fn run_with_hooks(
        &self,
        mir: &mut Mir,
        analyses: &mut Analyses,
        hooks: &mut Hooks<'_>,
    ) -> bool {
        let want_timing = hooks.stats.is_some() || hooks.log.is_some();
        let mut any_changed = false;
        for pass in &self.passes {
            // Debug-only: snapshot the MIR fingerprint to catch a pass that
            // mutates but reports `false`.
            #[cfg(debug_assertions)]
            let before_fp = mir_fingerprint(mir);

            let start = want_timing.then(std::time::Instant::now);
            let changed = pass.run(mir, analyses);
            let elapsed = start.map_or(Duration::ZERO, |s| s.elapsed());

            #[cfg(debug_assertions)]
            if !changed {
                assert_eq!(
                    before_fp,
                    mir_fingerprint(mir),
                    "pass {:?} mutated the MIR but reported no change: a pass \
                     that changes the MIR must return true (so fixpoint drivers \
                     and stats see the mutation)",
                    pass.name()
                );
            }

            any_changed |= changed;
            if want_timing {
                let record = PassStat {
                    name: pass.name(),
                    changed,
                    elapsed,
                };
                if let Some(log) = hooks.log.as_deref_mut() {
                    log(&record);
                }
                if let Some(stats) = hooks.stats.as_deref_mut() {
                    stats.push(record);
                }
            }
        }
        any_changed
    }
}

/// Debug-only full-MIR fingerprint for the changed-flag contract check. Reuses
/// the analysis module's value fingerprint (which already covers shape +
/// instructions + temps), the exact thing a pass could mutate.
#[cfg(debug_assertions)]
fn mir_fingerprint(mir: &Mir) -> u64 {
    crate::analysis::debug_mir_fingerprint(mir)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mir::{Inst, Terminator};

    /// A no-op pass that reports the given changed value (honestly).
    struct NopPass {
        name: &'static str,
        reports: bool,
    }

    impl Pass for NopPass {
        fn name(&self) -> &'static str {
            self.name
        }
        fn run(&self, _mir: &mut Mir, _analyses: &mut Analyses) -> bool {
            self.reports
        }
    }

    /// A pass that appends a constant instruction to block 0 and invalidates
    /// values correctly, reporting `true`.
    struct AppendConstPass;
    impl Pass for AppendConstPass {
        fn name(&self) -> &'static str {
            "append-const"
        }
        fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
            let c = mir.push_inst(Inst::ConstInt(1));
            mir.blocks[0].insts.push(c);
            analyses.invalidate_values();
            true
        }
    }

    /// A buggy pass: mutates the MIR but reports `false` (contract violation).
    struct LyingPass;
    impl Pass for LyingPass {
        fn name(&self) -> &'static str {
            "lying"
        }
        fn run(&self, mir: &mut Mir, _analyses: &mut Analyses) -> bool {
            let c = mir.push_inst(Inst::ConstInt(7));
            mir.blocks[0].insts.push(c);
            false // lie
        }
    }

    fn one_block_mir() -> Mir {
        let mut mir = Mir::new();
        let b = mir.push_block();
        mir.blocks[b].terminator = Terminator::Exit;
        mir
    }

    #[test]
    fn empty_pipeline_reports_no_change() {
        let mut mir = one_block_mir();
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![]);
        assert!(pipeline.is_empty());
        assert!(!pipeline.run(&mut mir, &mut analyses));
    }

    #[test]
    fn passes_run_in_order_and_changed_flag_aggregates() {
        let mut mir = one_block_mir();
        let mut analyses = Analyses::new();
        let mut order: Vec<&str> = Vec::new();
        // Two honest nops (false) and one real change.
        let pipeline = Pipeline::new(vec![
            Box::new(NopPass {
                name: "a",
                reports: false,
            }),
            Box::new(AppendConstPass),
            Box::new(NopPass {
                name: "c",
                reports: false,
            }),
        ]);
        let mut log = |s: &PassStat| order.push(s.name);
        let mut hooks = Hooks {
            stats: None,
            log: Some(&mut log),
        };
        let changed = pipeline.run_with_hooks(&mut mir, &mut analyses, &mut hooks);
        assert!(changed, "the middle pass changed the MIR");
        assert_eq!(order, vec!["a", "append-const", "c"]);
    }

    #[test]
    fn stats_collect_per_pass_records() {
        let mut mir = one_block_mir();
        let mut analyses = Analyses::new();
        let mut stats: Vec<PassStat> = Vec::new();
        let pipeline = Pipeline::new(vec![
            Box::new(NopPass {
                name: "a",
                reports: false,
            }),
            Box::new(AppendConstPass),
        ]);
        {
            let mut hooks = Hooks {
                stats: Some(&mut stats),
                log: None,
            };
            pipeline.run_with_hooks(&mut mir, &mut analyses, &mut hooks);
        }
        assert_eq!(stats.len(), 2);
        assert_eq!(stats[0].name, "a");
        assert!(!stats[0].changed);
        assert_eq!(stats[1].name, "append-const");
        assert!(stats[1].changed);
    }

    #[test]
    fn no_op_pass_does_not_trip_the_debug_guard() {
        // An honest false-reporting pass that does nothing must pass cleanly.
        let mut mir = one_block_mir();
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![Box::new(NopPass {
            name: "honest",
            reports: false,
        })]);
        assert!(!pipeline.run(&mut mir, &mut analyses));
    }

    #[cfg(debug_assertions)]
    #[test]
    #[should_panic(expected = "mutated the MIR but reported no change")]
    fn lying_pass_panics_under_debug_fingerprint() {
        let mut mir = one_block_mir();
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![Box::new(LyingPass)]);
        let _ = pipeline.run(&mut mir, &mut analyses);
    }

    #[cfg(debug_assertions)]
    #[test]
    #[should_panic(expected = "without invalidation")]
    fn pass_forgetting_invalidation_trips_the_analysis_guard() {
        // A pass that mutates instructions and reports `true` but forgets to
        // invalidate: the *next* analysis request (here, the next pass reading
        // liveness) trips T2.1's fingerprint guard.
        struct ForgetfulMutator;
        impl Pass for ForgetfulMutator {
            fn name(&self) -> &'static str {
                "forgetful"
            }
            fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
                // Prime the liveness cache.
                let _ = analyses.liveness(mir);
                // Mutate without invalidating.
                let c = mir.push_inst(Inst::ConstInt(42));
                mir.blocks[0].insts.push(c);
                true
            }
        }
        struct ReadsLiveness;
        impl Pass for ReadsLiveness {
            fn name(&self) -> &'static str {
                "reads-liveness"
            }
            fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
                let _ = analyses.liveness(mir); // stale cache -> panic
                false
            }
        }
        let mut mir = one_block_mir();
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![Box::new(ForgetfulMutator), Box::new(ReadsLiveness)]);
        let _ = pipeline.run(&mut mir, &mut analyses);
    }

    #[test]
    fn levels_are_prefixes_of_the_registry() {
        // Minimal never runs optimization passes; fast is a prefix of
        // standard; W1 order is SCCP → GVN → DCE.
        assert!(passes_for_level(Level::Minimal).is_empty());
        assert!(Pipeline::for_level(Level::Minimal).is_empty());
        let names = |level| -> Vec<&'static str> {
            passes_for_level(level).iter().map(|p| p.name()).collect()
        };
        let fast = names(Level::Fast);
        let standard = names(Level::Standard);
        assert!(
            standard.starts_with(&fast),
            "fast {fast:?} must be a prefix of standard {standard:?}"
        );
        assert_eq!(fast.first(), Some(&"sccp"), "SCCP is the first W1 pass");
        assert!(fast.iter().any(|p| *p == "gvn"));
        assert!(fast.iter().any(|p| *p == "dce"));
        // Registry entries are grouped by ascending stage (the prefix property
        // passes_for_level relies on).
        let stages: Vec<Stage> = registry().iter().map(|e| e.stage).collect();
        let mut sorted = stages.clone();
        sorted.sort();
        assert_eq!(stages, sorted, "registry must be grouped by stage");
    }

    #[test]
    fn stage_cutoffs_are_monotone() {
        // minimal selects nothing; fast <= standard in stage coverage.
        assert_eq!(Stage::cutoff(Level::Minimal), None);
        assert_eq!(Stage::cutoff(Level::Fast), Some(Stage::W1));
        assert_eq!(Stage::cutoff(Level::Standard), Some(Stage::W4));
        assert!(Stage::W1 <= Stage::W4);
        assert!(Stage::W1 < Stage::W2);
    }
}
