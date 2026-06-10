//! Sanity checks for the generated `Op` enum (`src/ops.rs`).
//!
//! Full synchronization with `sonolus/backend/ops.py` is enforced byte-for-byte by the
//! Python-side test `tests/backend/test_ops_sync.py`; these tests only spot-check known
//! entries and the id/name lookups.

use sonolus_backend_core::ops::Op;

#[test]
fn entry_count_matches_ops_py() {
    assert_eq!(Op::COUNT, 191);
    assert_eq!(Op::all().count(), usize::from(Op::COUNT));
}

#[test]
fn known_op_flags() {
    // Pure math op.
    assert_eq!(Op::Abs.name(), "Abs");
    assert!(Op::Abs.pure());
    assert!(!Op::Abs.side_effects());
    assert!(!Op::Abs.control_flow());

    // Side-effecting op.
    assert_eq!(Op::DebugLog.name(), "DebugLog");
    assert!(Op::DebugLog.side_effects());
    assert!(!Op::DebugLog.pure());
    assert!(!Op::DebugLog.control_flow());

    // Neither pure nor side-effecting (nondeterministic read).
    assert!(!Op::Random.pure());
    assert!(!Op::Random.side_effects());

    // Pure control flow.
    assert!(Op::JumpLoop.pure());
    assert!(!Op::JumpLoop.side_effects());
    assert!(Op::JumpLoop.control_flow());

    // Side-effecting control flow.
    assert!(Op::Break.side_effects());
    assert!(!Op::Break.pure());
    assert!(Op::Break.control_flow());
}

#[test]
fn ids_are_definition_order() {
    // First and last entries of sonolus/backend/ops.py.
    assert_eq!(Op::Abs.id(), 0);
    assert_eq!(Op::While.id(), Op::COUNT - 1);
}

#[test]
fn lookups_round_trip() {
    for op in Op::all() {
        assert_eq!(
            Op::from_id(op.id()),
            Some(op),
            "from_id({}) mismatch",
            op.id()
        );
        assert_eq!(
            Op::from_name(op.name()),
            Some(op),
            "from_name({:?}) mismatch",
            op.name()
        );
    }
    assert_eq!(Op::from_id(Op::COUNT), None);
    assert_eq!(Op::from_id(u16::MAX), None);
    assert_eq!(Op::from_name("NotARealOp"), None);
    assert_eq!(Op::from_name(""), None);
}
