//! The broken-transform canary (PORT.md T2.3 DoD): a deliberately
//! miscompiling pass used to prove the differential harness and the fuzzer
//! actually catch miscompiles.
//!
//! The bug: a rewrite rule that turns `Add(x, c)` into plain `x` for any
//! nonzero constant `c` — a realistic "identity-element rewrite with a wrong
//! guard" miscompile (the correct rule needs `c == 0`; this one fires on
//! everything *except* 0) that changes observable arithmetic everywhere
//! constant addends appear, loop counter increments included.
//!
//! **Test-only by construction**: this module lives under `tests/` and is
//! compiled only into test binaries that `#[path]`-include it. It is not part
//! of `sonolus-backend-core`, is never registered in the pass
//! [`registry`](sonolus_backend_core::passes::registry), and is unreachable
//! from any production code path.

use sonolus_backend_core::analysis::Analyses;
use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::mir::{Inst, Mir, Terminator, Value};
use sonolus_backend_core::nodes::EngineNodes;
use sonolus_backend_core::ops::Op;
use sonolus_backend_core::passes::{Pass, Pipeline};
use sonolus_backend_core::pipeline::{CompileError, compile_cfg_with_pipeline};
use sonolus_backend_core::rewrite::{Rewrite, RewriteCtx, RewriteDriver, RewriteRule};

/// The deliberately broken rule: `Add(x, c)` -> `x` for any nonzero const `c`.
#[derive(Debug)]
pub struct CanaryAddConstRule;

impl RewriteRule for CanaryAddConstRule {
    fn name(&self) -> &'static str {
        "canary-add-const-is-x"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let Inst::Op {
            op: Op::Add, args, ..
        } = ctx.inst(v)
        else {
            return None;
        };
        if args.len() != 2 {
            return None;
        }
        // WRONG on purpose: the identity-element guard should be `c == 0`,
        // not `c != 0`. (0 itself is excluded so every fire is a real bug —
        // `Add(x, 0) -> x` would be a near-correct rewrite.)
        #[allow(clippy::float_cmp)] // exact zero test, like the real rules
        let fires = match ctx.inst(args[1]) {
            Inst::ConstInt(c) => *c != 0,
            Inst::ConstFloat(c) => *c != 0.0,
            _ => false,
        };
        fires.then_some(Rewrite::Existing(args[0]))
    }
}

/// A pass driving the broken rule to fixpoint (the shape a real W1 rewrite
/// pass will have, minus the bug).
#[derive(Debug)]
pub struct CanaryPass;

impl Pass for CanaryPass {
    fn name(&self) -> &'static str {
        "canary-miscompile"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        let rules: Vec<Box<dyn RewriteRule>> = vec![Box::new(CanaryAddConstRule)];
        let report = RewriteDriver::new(&rules).run(mir);
        if report.rewrites == 0 {
            return false;
        }
        // The rewrite driver leaves replaced defining instructions in the
        // schedule (dead; DCE is a separate pass by contract). Lowering
        // requires single consumption of pending values, so sweep the dead
        // `Add`s out of the schedule — exactly what a real rewrite pass will
        // pair with a DCE pass to do. A replaced value has zero references
        // anywhere (every use was redirected), so "unreferenced Add" is a
        // precise description of the rewrite's leftovers.
        let mut use_counts = vec![0u32; mir.insts.len()];
        for inst in &mir.insts {
            Mir::for_each_operand(inst, |o| {
                use_counts[o as usize] += 1;
            });
        }
        for block in &mir.blocks {
            if let Terminator::Branch { test, .. } = &block.terminator {
                use_counts[*test as usize] += 1;
            }
        }
        let insts = &mir.insts;
        for block in &mut mir.blocks {
            block.insts.retain(|&v| {
                use_counts[v as usize] > 0
                    || !matches!(insts[v as usize], Inst::Op { op: Op::Add, .. })
            });
        }
        analyses.invalidate_all();
        true
    }
}

/// Compiles a frontend CFG with the canary injected as the only optimization
/// pass — the "test side" for canary differential cases.
pub fn compile_with_canary(cfg: &Cfg) -> Result<EngineNodes, CompileError> {
    compile_cfg_with_pipeline(cfg, &Pipeline::new(vec![Box::new(CanaryPass)]))
}
