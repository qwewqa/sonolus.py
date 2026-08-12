"""Tests that declaring an archetype subclass follows Python's own class creation protocol.

`_BaseArchetype.__init_subclass__` is the hook that does the per-mode setup every subclass needs, which puts it
between the declaration and the rest of the protocol: a base or mixin further along the mro only gets its turn if
this hook passes control on. It is also where a declaration the build cannot honour is rejected, since a class
that reaches the build has already lost the source location the declaration had.
"""

import pytest

from sonolus.backend.mode import Mode
from sonolus.build.compile import compile_mode
from sonolus.script.archetype import PlayArchetype, PreviewArchetype, WatchArchetype, callback, imported
from sonolus.script.internal.context import ProjectContextState, RuntimeChecks


def test_a_mixin_hook_runs_when_the_archetype_base_is_listed_first():
    declared = []

    class RegistryMixin:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__(**kwargs)
            declared.append(cls.__name__)

    class Registered(PlayArchetype, RegistryMixin):
        pass

    assert declared == ["Registered"]
    assert Registered.name == "Registered"


@pytest.mark.parametrize("base", [PlayArchetype, WatchArchetype, PreviewArchetype])
def test_an_unexpected_class_keyword_is_rejected(base):
    with pytest.raises(TypeError, match="takes no keyword arguments"):

        class Tagged(base, tag="chart"):
            pass


def plain_archetype() -> type:
    """A play archetype with a field and a callback, fresh per call so field initialization cannot carry over."""

    class Note(PlayArchetype):
        beat: float = imported()

        def update_sequential(self):
            pass

    return Note


def declare_subclass(base: type, name: str, spelling: str) -> type:
    """Declare a subclass of `base` with the class statement or with a direct `type()` call."""
    if spelling == "class_statement":

        class Sub(base):
            pass

        return Sub
    return type(name, (base,), {})


@pytest.mark.parametrize("spelling", ["class_statement", "type_call"])
def test_a_subclass_of_a_derived_archetype_is_rejected(spelling: str):
    derived = plain_archetype().derive("DerivedNote", is_scored=True)

    with pytest.raises(TypeError, match="cannot subclass DerivedNote"):
        declare_subclass(derived, "SubOfDerived", spelling)


def test_a_subclass_of_a_derived_archetype_declaring_its_own_members_is_rejected():
    derived = plain_archetype().derive("DerivedNote", is_scored=True)

    with pytest.raises(TypeError, match="cannot subclass DerivedNote"):

        class SubOfDerived(derived):
            extra: float = imported()

            @callback(order=5)
            def update_sequential(self):
                pass


def test_deriving_from_a_derived_archetype_is_rejected_by_derive_itself():
    derived = plain_archetype().derive("DerivedNote", is_scored=True)

    with pytest.raises(RuntimeError, match="Cannot derive from a derived archetype"):
        derived.derive("DerivedAgain", is_scored=True)


def test_deriving_from_a_plain_archetype_is_accepted():
    derived = plain_archetype().derive("DerivedNote", is_scored=True)

    assert derived.name == "DerivedNote"
    assert derived.schema() == {"name": "DerivedNote", "fields": ["beat"], "exports": []}


def test_a_subclass_of_a_plain_archetype_is_accepted():
    class SubOfPlain(plain_archetype()):
        extra: float = imported()

    assert SubOfPlain.name == "SubOfPlain"
    assert SubOfPlain.schema() == {"name": "SubOfPlain", "fields": ["beat", "extra"], "exports": []}


@pytest.mark.parametrize("order", [0, 3])
def test_a_callback_marker_on_a_method_that_is_not_a_callback_is_rejected(order: int):
    with pytest.raises(TypeError, match="Method 'helper' of MarkedHelper is decorated with @callback"):

        class MarkedHelper(PlayArchetype):
            @callback(order=order)
            def helper(self):
                pass


def test_a_callback_marker_naming_another_modes_callback_is_rejected():
    with pytest.raises(TypeError, match="Method 'touch' of MarkedTouch is decorated with @callback"):

        class MarkedTouch(WatchArchetype):
            @callback(order=2)
            def touch(self):
                pass


def test_a_callback_marker_on_a_callback_of_this_mode_is_accepted():
    class Ordered(PlayArchetype):
        @callback(order=3)
        def update_sequential(self):
            pass

    assert "update_sequential" in Ordered._callbacks_


def test_a_non_zero_order_on_a_callback_that_does_not_support_one_is_rejected_by_the_build():
    # The neighbouring guard, pinned so the two stay apart: the marker is on a callback of this mode, so the
    # declaration is accepted and the build is what rejects the order.
    class OrderedInitialize(PlayArchetype):
        @callback(order=3)
        def initialize(self):
            pass

    with pytest.raises(ValueError, match="Callback 'initialize' does not support a non-zero order"):
        compile_mode(
            mode=Mode.PLAY,
            project_state=ProjectContextState(runtime_checks=RuntimeChecks.NONE),
            archetypes=[OrderedInitialize],
            global_callbacks=None,
            level=None,
            validate_only=True,
        )


def test_a_callback_declared_as_a_classmethod_is_rejected():
    # A callback is traced as a bound method of the entity, so a classmethod would be handed the entity as `cls`.
    with pytest.raises(TypeError, match="Callback 'preprocess' of ClassLevel is declared as a @classmethod"):

        class ClassLevel(PlayArchetype):
            @classmethod
            def preprocess(cls):
                pass


def test_a_callback_declared_as_a_staticmethod_is_rejected():
    with pytest.raises(TypeError, match="Callback 'preprocess' of Detached is declared as a @staticmethod"):

        class Detached(PlayArchetype):
            @staticmethod
            def preprocess():
                pass


def test_a_callback_a_mixin_declares_as_a_staticmethod_is_rejected_naming_the_archetype():
    # The mode base supplies the default under this name, so the mro walk passes it and reaches the mixin. The
    # archetype being declared is what the message must name, since the mixin may be shared with a mode that
    # has no such callback.
    class StaticMixin:
        @staticmethod
        def preprocess():
            pass

    with pytest.raises(TypeError, match="Callback 'preprocess' of FromMixin is declared as a @staticmethod"):

        class FromMixin(PlayArchetype, StaticMixin):
            pass


def test_a_callback_marker_on_a_mixin_method_is_accepted_in_every_mode():
    # A mixin is shared across modes, so the marker is right for the mode that has the callback and inert for the
    # mode that does not. Only the declaring class's own members are checked, which is what leaves this legal.
    class TouchMixin:
        @callback(order=2)
        def touch(self):
            pass

    class PlayWithTouch(PlayArchetype, TouchMixin):
        pass

    class WatchWithTouchMixin(WatchArchetype, TouchMixin):
        pass

    assert "touch" in PlayWithTouch._callbacks_
    assert "touch" not in WatchWithTouchMixin._callbacks_


def compile_archetypes(mode: Mode, archetypes: list[type]) -> dict:
    return compile_mode(
        mode=mode,
        project_state=ProjectContextState(runtime_checks=RuntimeChecks.NONE),
        archetypes=archetypes,
        global_callbacks=None,
        level=None,
        validate_only=True,
    )


def test_a_callback_bound_under_a_second_name_keeps_its_order():
    # The marker is on the function object while the callbacks are resolved by name, so one function bound
    # under both a callback name and another name is still this archetype's callback.
    class Aliased(PlayArchetype):
        @callback(order=3)
        def update_sequential(self):
            pass

        tick = update_sequential

    result = compile_archetypes(Mode.PLAY, [Aliased])

    assert result["archetypes"][0]["updateSequential"]["order"] == 3


def test_a_callback_declared_under_a_private_name_and_bound_to_the_callback_name_is_accepted():
    class Descriptive(PlayArchetype):
        @callback(order=1)
        def _prepare_note(self):
            pass

        preprocess = _prepare_note

    result = compile_archetypes(Mode.PLAY, [Descriptive])

    assert result["archetypes"][0]["preprocess"]["order"] == 1


def test_a_subclass_binding_an_inherited_callback_under_a_second_name_is_accepted():
    # The subclass's own __dict__ holds only the alias, so the exemption has to consult the callbacks the
    # archetype resolved rather than the names it declares.
    class Base(PlayArchetype):
        @callback(order=2)
        def preprocess(self):
            pass

    class Sub(Base):
        alias = Base.__dict__["preprocess"]

    result = compile_archetypes(Mode.PLAY, [Sub])

    assert result["archetypes"][0]["preprocess"]["order"] == 2


def test_a_callback_marker_hidden_under_a_staticmethod_is_rejected():
    # The marker sits on the wrapped function, where a plain attribute lookup on the staticmethod cannot see it.
    with pytest.raises(TypeError, match="Method 'helper' of StaticMarked is decorated with @callback"):

        class StaticMarked(PlayArchetype):
            @staticmethod
            @callback(order=1)
            def helper():
                pass


@pytest.mark.parametrize("wrapper", [staticmethod, classmethod])
def test_a_callback_marker_left_on_a_staticmethod_wrapper_is_rejected(wrapper):
    # The mirror of the case above: applied outermost, @callback marks the descriptor rather than the function
    # it wraps, so the scan has to look at both.
    def helper(*args):
        pass

    with pytest.raises(TypeError, match="Method 'helper' of WrapperMarked is decorated with @callback"):
        type("WrapperMarked", (PlayArchetype,), {"helper": callback(order=1)(wrapper(helper))})


def test_a_callback_marker_borrowed_from_another_archetype_is_rejected():
    # The exemption is per archetype: the borrowed function is a callback of Source, but under this name in
    # Borrower the order is never read.
    class Source(PlayArchetype):
        @callback(order=1)
        def preprocess(self):
            pass

    with pytest.raises(TypeError, match="Method 'helper' of Borrower is decorated with @callback"):

        class Borrower(PlayArchetype):
            helper = Source.__dict__["preprocess"]


@pytest.mark.parametrize(("py_name", "wire_name"), [("spawn_time", "spawnTime"), ("despawn_time", "despawnTime")])
def test_a_watch_spawn_phase_callback_ships_a_non_zero_order(py_name: str, wire_name: str):
    # The runtime walks the spawn queue in these two callbacks' order, the same way play walks it in
    # spawn_order's.
    def spawn_phase(self) -> float:
        return 0.0

    spawn_phase.__name__ = py_name
    ordered = type("OrderedSpawnPhase", (WatchArchetype,), {py_name: callback(order=2)(spawn_phase)})

    result = compile_archetypes(Mode.WATCH, [ordered])

    assert result["archetypes"][0][wire_name]["order"] == 2


def test_a_non_zero_order_on_a_watch_callback_that_does_not_support_one_is_rejected_by_the_build():
    # The two spawn-phase callbacks are the only watch additions to the order-supporting set.
    class OrderedInitialize(WatchArchetype):
        @callback(order=3)
        def initialize(self):
            pass

    with pytest.raises(ValueError, match="Callback 'initialize' does not support a non-zero order"):
        compile_archetypes(Mode.WATCH, [OrderedInitialize])
