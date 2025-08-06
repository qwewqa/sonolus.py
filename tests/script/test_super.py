from sonolus.script.debug import debug_log
from tests.script.conftest import run_and_validate


class A:
    def m(self):
        debug_log(1)

    @classmethod
    def m_cls(cls):
        debug_log(1)


class B(A):
    def m(self):
        debug_log(2)
        super().m()
        debug_log(3)

    @classmethod
    def m_cls(cls):
        debug_log(2)
        super().m_cls()
        debug_log(3)


class C(A):
    def m(self):
        debug_log(4)
        super().m()
        debug_log(5)

    @classmethod
    def m_cls(cls):
        debug_log(4)
        super().m_cls()
        debug_log(5)


class D(B, C):
    def m(self):
        debug_log(6)
        super().m()
        debug_log(7)

    @classmethod
    def m_cls(cls):
        debug_log(6)
        super().m_cls()
        debug_log(7)


def test_super_simple():
    b = B()
    b._is_comptime_value_ = True  # type: ignore

    def fn():
        b.m()

    run_and_validate(fn)


def test_super_diamond():
    d = D()
    d._is_comptime_value_ = True  # type: ignore

    def fn():
        d.m()

    run_and_validate(fn)


def test_super_classmethod():
    b = B()
    b._is_comptime_value_ = True  # type: ignore

    def fn():
        b.m_cls()

    run_and_validate(fn)


def test_super_classmethod_diamond():
    d = D()
    d._is_comptime_value_ = True  # type: ignore

    def fn():
        d.m_cls()

    run_and_validate(fn)
