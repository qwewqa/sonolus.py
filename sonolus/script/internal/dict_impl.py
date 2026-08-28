from typing import Any

from sonolus.script.array import Array
from sonolus.script.debug import static_error
from sonolus.script.internal.context import Context, ctx, set_ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.maybe import Nothing, Some
from sonolus.script.num import Num, _is_num
from sonolus.script.record import Record


def _truth_test_body(value):
    return bool(value)


@meta_fn
def _truth_test(value):
    from sonolus.script.internal.visitor import compile_and_call

    value = validate_value(value)
    if ctx():
        return compile_and_call(_truth_test_body, value)
    return bool(value._as_py_())


def _keys_less(lhs, rhs):
    return bool(lhs < rhs)


def _same_key_identity_shape(stored, probe):
    from sonolus.script.internal.tuple_impl import TupleImpl

    if stored is probe:
        return True
    if isinstance(stored, TupleImpl) and isinstance(probe, TupleImpl) and len(stored.value) == len(probe.value):
        return all(map(_same_key_identity_shape, stored.value, probe.value))
    return False


def _tuple_keys_equal(stored, probe):
    for left, right in zip(stored, probe):  # ruff: ignore[zip-without-explicit-strict, reimplemented-builtin]
        if not _keys_equal(left, right):
            return False
    return True


def _host_call(fn, *args, memo=None):
    from sonolus.script.internal.tuple_impl import TupleImpl

    def freeze(value):
        value = validate_value(value)
        token = id(value)
        if issubclass(type(value), Num):
            return token, type(value), value._as_py_()
        if isinstance(value, TupleImpl):
            return token, tuple, tuple(freeze(item) for item in value.value)
        if isinstance(value, Record):
            return token, type(value), tuple(freeze(value._value_[field.name]) for field in value._fields_)
        return token, None, value._as_py_()

    def thaw(frozen):
        token, type_, value = frozen
        if token in memo:
            return memo[token]
        if type_ is None:
            result = value
        elif type_ is tuple:
            result = tuple(thaw(item) for item in value)
        elif issubclass(type_, Num):
            result = Num(value)
            result.__class__ = type_
        else:
            result = type_._raw(**{field.name: thaw(item) for field, item in zip(type_._fields_, value, strict=True)})
        memo[token] = result
        return result

    active_ctx = ctx()
    memo = {} if memo is None else memo
    frozen_args = tuple(freeze(arg) for arg in args)
    set_ctx(None)
    try:
        return fn(*(thaw(arg) for arg in frozen_args))
    finally:
        set_ctx(active_ctx)


@meta_fn
def _keys_equal(stored, probe):
    from sonolus.script.internal.visitor import _is_strict_subclass, compile_and_call

    stored = validate_value(stored)
    probe = validate_value(probe)
    if _same_key_identity_shape(stored, probe):
        return True
    from sonolus.script.internal.tuple_impl import TupleImpl

    if isinstance(stored, TupleImpl) and isinstance(probe, TupleImpl):
        if len(stored.value) != len(probe.value):
            return False
        return compile_and_call(_tuple_keys_equal, stored, probe)
    probe_has_priority = _is_strict_subclass(stored, probe)
    if probe_has_priority:
        result = validate_value(compile_and_call(probe.__eq__, stored))
        if ctx() and not ctx().live:
            return None
        if not (result._is_py_() and result._as_py_() is NotImplemented):
            return compile_and_call(_truth_test, result)
    result = validate_value(compile_and_call(stored.__eq__, probe))
    if ctx() and not ctx().live:
        return None
    if not (result._is_py_() and result._as_py_() is NotImplemented):
        return compile_and_call(_truth_test, result)
    if probe_has_priority:
        return False
    result = validate_value(compile_and_call(probe.__eq__, stored))
    if ctx() and not ctx().live:
        return None
    if result._is_py_() and result._as_py_() is NotImplemented:
        return False
    return compile_and_call(_truth_test, result)


class DictImpl[Keys, OrderedKeys, Values](Record):
    _keys: Keys  # tuple[K, ...]
    _ordered_keys: OrderedKeys  # tuple[tuple[K, int], ...] | None
    _values: Values  # tuple[V, ...] | Array[V, ...]

    @property
    @meta_fn
    def _has_ordered_keys(self) -> bool:
        keys = validate_value(self._ordered_keys)
        return not (keys._is_py_() and keys._as_py_() is None)

    @property
    @meta_fn
    def _has_array_values(self) -> bool:
        values = validate_value(self._values)
        return isinstance(values, Array)

    @property
    @meta_fn
    def _size(self) -> int:
        return validate_value(len(self._keys))._as_py_()

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, item):
        constsearch_res = self._try_constsearch(item)
        if constsearch_res is not None:
            return constsearch_res >= 0
        numsearch_res = self._try_numsearch(item)
        if numsearch_res is not None:
            return numsearch_res >= 0
        if self._has_ordered_keys:
            return self._binsearch(item) >= 0
        return self._linsearch(item) >= 0

    def __getitem__(self, item):
        result = self._maybe_getitem(item)
        return result.get(error_message="KeyError")

    @meta_fn
    def get(self, item, default=None, /):
        from sonolus.script.internal.visitor import compile_and_call

        result = validate_value(compile_and_call(self._maybe_getitem, item))
        if not ctx().live:
            return None
        default = validate_value(default)
        if default._is_py_() and default._as_py_() is None:
            present = validate_value(result.is_some)
            if present._is_py_():
                return compile_and_call(result.get_unsafe) if present._as_py_() else default
            static_error("Dict.get with an omitted or None default requires a compile time constant key")
        return compile_and_call(result.or_default, default)

    def _maybe_getitem(self, item):
        index = self._try_constsearch(item)
        if index is not None:
            if index < 0:
                return Nothing
            elif isinstance(self._values, Array):
                return Some(self._values.get_unchecked(index))
            else:
                return Some(self._values[index])
        # A runtime (non-constant) key into a dict whose values are not a uniform-constant
        # Array can only be resolved by indexing a tuple with a runtime index, which is
        # impossible to compile. numsearch/binsearch/linsearch all produce runtime indices,
        # so reject here (before them) with a clear compile-time diagnostic. constsearch above
        # already handled compile-time-constant keys, which are valid for tuple values.
        if not self._has_array_values:
            static_error(
                "Dict must be accessed via a compile time constant unless "
                "all values are compile time constants of a uniform type."
            )
        index = self._try_numsearch(item)
        if index is not None:
            if index < 0:
                return Nothing
            elif isinstance(self._values, Array):
                return Some(self._values.get_unchecked(index))
            else:
                return Some(self._values[index])
        if self._has_ordered_keys:
            index = self._binsearch(item)
        else:
            index = self._linsearch(item)
        if index < 0:
            return Nothing
        elif isinstance(self._values, Array):
            return Some(self._values.get_unchecked(index))
        else:
            return Some(self._values[index])

    @meta_fn
    def __eq__(self, other: Any):
        raise TypeError("Dict equality comparison is not supported")

    @meta_fn
    def __ne__(self, other: Any):
        raise TypeError("Dict equality comparison is not supported")

    __hash__ = None

    @meta_fn
    def __or__(self, other):
        if not isinstance(other, DictImpl):
            return NotImplemented
        return self.from_items((*self.items(), *other.items()))

    @staticmethod
    def from_dict(d):
        return DictImpl._from_unique_items(tuple(d.items()))

    @staticmethod
    def from_items(items):
        merged = []
        host_memo = {}
        for key, value in items:
            key = validate_value(key)
            value = validate_value(value)
            if not key._is_py_():
                raise TypeError("Dict keys must be a compile-time constant")
            for i, (existing, _) in enumerate(merged):
                if existing is key or _host_call(_keys_equal, existing, key, memo=host_memo):
                    merged[i] = (existing, value)
                    break
            else:
                merged.append((key, value))
        return DictImpl._from_unique_items(merged)

    @staticmethod
    def _from_unique_items(items):
        from sonolus.script.internal.tuple_impl import TupleImpl

        def ordered_signature(key):
            if issubclass(type(key), Num):
                return Num if type(key) is Num else None
            if isinstance(key, TupleImpl):
                members = tuple(ordered_signature(item) for item in key.value)
                return (tuple, members) if all(member is not None for member in members) else None
            py_key = key._as_py_()
            return type(py_key) if isinstance(py_key, str) else None

        items = tuple((validate_value(key), validate_value(value)) for key, value in items)
        keys = tuple(key for key, _ in items)
        values = tuple(value for _, value in items)
        if not all(k._is_py_() for k in keys):
            raise TypeError("Dict keys must be a compile-time constant")
        if len(keys) >= 2:
            py_keys = [k._as_py_() for k in keys]
            py_key_types = {type(k) for k in py_keys}
            ordered_signatures = {ordered_signature(key) for key in keys}
            try:
                is_comparable = (
                    len(py_key_types) == 1
                    and len(ordered_signatures) == 1
                    and None not in ordered_signatures
                    and py_keys[0].__lt__(py_keys[1]) is not NotImplemented  # ruff: ignore[unnecessary-dunder-call]
                    and type(keys[0]).__lt__ is not object.__lt__
                )
            except TypeError:
                is_comparable = False
            if is_comparable:
                try:
                    ordered_keys = tuple((keys[i], i) for i, _ in sorted(enumerate(py_keys), key=lambda item: item[1]))
                except TypeError:
                    ordered_keys = None
            else:
                ordered_keys = None
        else:
            ordered_keys = None
        if all(v._is_py_() for v in values) and len({type(v) for v in values}) == 1:
            values = Array[type(values[0]), len(values)]._with_value([*values])
        return DictImpl(keys, ordered_keys, values)

    def _items_with_py_keys(self):
        return tuple(
            (
                self._keys[i]._as_py_(),
                self._values._value[i]
                if isinstance(self._values, Array) and isinstance(self._values._value, list)
                else self._values[i],
            )
            for i in range(self._size)
        )

    @meta_fn
    def _try_constsearch(self, item):
        from sonolus.script.internal.visitor import compile_and_call

        orig_ctx = ctx()
        begin_ctx = orig_ctx.branch(None)
        set_ctx(begin_ctx)

        for i, k in enumerate(self._keys):
            eq = validate_value(compile_and_call(_keys_equal, k, item))
            if not ctx().live:
                return None
            if not eq._is_py_():
                # _try_constsearch added orig_ctx -> begin_ctx speculatively. Remove the edge before fallback.
                del orig_ctx.outgoing[None]
                set_ctx(orig_ctx)
                return None
            eq_py = eq._as_py_()
            if eq_py is NotImplemented:
                continue
            if eq_py:
                return i
        return -1

    @meta_fn
    def _try_numsearch(self, item):
        item = validate_value(item)
        if not _is_num(item):
            return None
        res = Num._alloc_()
        orig_ctx = ctx()
        begin_ctx = orig_ctx.branch(None)
        set_ctx(begin_ctx)
        begin_ctx.test = item.ir()
        end_ctxs = []
        for i, k in enumerate(self._keys):
            if not _is_num(k):
                # _try_numsearch added orig_ctx -> begin_ctx speculatively. Remove the edge before fallback.
                del orig_ctx.outgoing[None]
                set_ctx(orig_ctx)
                return None
            set_ctx(begin_ctx.branch(k._as_py_()))
            res._set_(i)
            end_ctxs.append(ctx())
        set_ctx(begin_ctx.branch(None))
        res._set_(-1)
        end_ctxs.append(ctx())
        set_ctx(Context.meet(end_ctxs))
        return res

    @meta_fn
    def _binsearch(self, item):
        return self._binsearch_internal(item, 0, self._size, Num._alloc_())

    @meta_fn
    def _binsearch_internal(self, item, lo, hi, res):
        from sonolus.script.internal.visitor import compile_and_call

        if lo >= hi:
            res._set_(-1)
            return res

        if hi - lo <= 3:
            # Linear search
            lo_value, orig_index = self._ordered_keys[lo]
            equal = compile_and_call(_keys_equal, lo_value, item)
            if not ctx().live:
                return res
            eq_test = equal.ir()
            ctx_init = ctx()
            ctx_init.test = eq_test
            eq_ctx = ctx_init.branch(None)
            neq_ctx = ctx_init.branch(0)

            set_ctx(eq_ctx)
            res._set_(orig_index)
            after_eq_ctx = ctx()

            set_ctx(neq_ctx)
            self._binsearch_internal(item, lo + 1, hi, res)

            set_ctx(Context.meet([after_eq_ctx, ctx()]))
            return res

        mid = (lo + hi) // 2
        mid_value, orig_index = self._ordered_keys[mid]
        equal = compile_and_call(_keys_equal, mid_value, item)
        if not ctx().live:
            return res
        eq_test = equal.ir()
        ctx_init = ctx()
        ctx_init.test = eq_test
        eq_ctx = ctx_init.branch(None)
        neq_ctx = ctx_init.branch(0)

        set_ctx(eq_ctx)
        res._set_(orig_index)
        after_eq_ctx = ctx()

        set_ctx(neq_ctx)

        less = compile_and_call(_keys_less, item, mid_value)
        if not ctx().live:
            return res
        neq_test = less.ir()
        neq_ctx = ctx()
        neq_ctx.test = neq_test
        lt_ctx = neq_ctx.branch(None)
        gt_ctx = neq_ctx.branch(0)

        set_ctx(lt_ctx)
        self._binsearch_internal(item, lo, mid, res)
        after_lt_ctx = ctx()
        set_ctx(gt_ctx)
        self._binsearch_internal(item, mid + 1, hi, res)
        after_gt_ctx = ctx()

        set_ctx(Context.meet([after_eq_ctx, after_gt_ctx, after_lt_ctx]))
        return res

    def _linsearch(self, item):
        for i, k in enumerate(self._keys):
            if k == item:
                return i
        return -1

    def _tuple_iter_(self) -> tuple:
        from sonolus.script.internal.tuple_impl import tuple_iter

        return tuple_iter(self._keys)

    @meta_fn
    def keys(self):
        return validate_value(tuple(self._keys[i] for i in range(self._size)))

    @meta_fn
    def values(self):
        return validate_value(
            tuple(
                self._values._value[i]
                if isinstance(self._values, Array) and isinstance(self._values._value, list)
                else self._values[i]
                for i in range(self._size)
            )
        )

    @meta_fn
    def items(self):
        return validate_value(
            tuple(
                (
                    self._keys[i],
                    self._values._value[i]
                    if isinstance(self._values, Array) and isinstance(self._values._value, list)
                    else self._values[i],
                )
                for i in range(self._size)
            )
        )
