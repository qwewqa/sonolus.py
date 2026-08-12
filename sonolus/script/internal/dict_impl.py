from typing import Any

from sonolus.script.array import Array
from sonolus.script.debug import static_error
from sonolus.script.internal.context import Context, ctx, set_ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.maybe import Nothing, Some
from sonolus.script.num import Num, _is_num
from sonolus.script.record import Record


@meta_fn
def _keys_equal(stored, probe):
    from sonolus.script.internal.visitor import _is_strict_subclass, compile_and_call

    stored = validate_value(stored)
    probe = validate_value(probe)
    probe_has_priority = _is_strict_subclass(stored, probe)
    if probe_has_priority:
        result = validate_value(compile_and_call(probe.__eq__, stored))
        if not (result._is_py_() and result._as_py_() is NotImplemented):
            return result
    result = validate_value(compile_and_call(stored.__eq__, probe))
    if not (result._is_py_() and result._as_py_() is NotImplemented):
        return result
    if probe_has_priority:
        return False
    result = validate_value(compile_and_call(probe.__eq__, stored))
    if result._is_py_() and result._as_py_() is NotImplemented:
        return False
    return result


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

    __hash__ = None

    @meta_fn
    def __or__(self, other):
        if not isinstance(other, DictImpl):
            raise TypeError("Unsupported type for '|' operator")
        return self.from_dict({**self._as_dict_with_py_keys(), **other._as_dict_with_py_keys()})

    @staticmethod
    def from_dict(d):
        keys = tuple(validate_value(k) for k in d)
        values = tuple(validate_value(v) for v in d.values())
        if not all(k._is_py_() for k in keys):
            raise TypeError("Dict keys must be a compile-time constant")
        if len(keys) >= 2:
            py_keys = [k._as_py_() for k in keys]
            py_key_types = {type(k) for k in py_keys}
            is_comparable = (
                len(py_key_types) == 1
                and py_keys[0].__lt__(py_keys[1]) is not NotImplemented  # noqa: PLC2801
                and type(keys[0]).__lt__ is not object.__lt__
            )
            if is_comparable:
                ordered_keys = tuple((k, i) for i, k in sorted(enumerate(py_keys), key=lambda x: x[1]))
            else:
                ordered_keys = None
        else:
            ordered_keys = None
        if all(v._is_py_() for v in values) and len({type(v) for v in values}) == 1:
            values = Array[type(values[0]), len(values)]._with_value([*values])
        return DictImpl(keys, ordered_keys, values)

    def _as_dict_with_py_keys(self):
        return {
            self._keys[i]._as_py_(): self._values._value[i]
            if isinstance(self._values, Array) and isinstance(self._values._value, list)
            else self._values[i]
            for i in range(self._size)
        }

    @meta_fn
    def _try_constsearch(self, item):
        from sonolus.script.internal.visitor import compile_and_call

        orig_ctx = ctx()
        begin_ctx = orig_ctx.branch(None)
        set_ctx(begin_ctx)

        for i, k in enumerate(self._keys):
            eq = validate_value(compile_and_call(_keys_equal, k, item))
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
            eq_test = compile_and_call(_keys_equal, lo_value, item).ir()
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
        eq_test = compile_and_call(_keys_equal, mid_value, item).ir()
        ctx_init = ctx()
        ctx_init.test = eq_test
        eq_ctx = ctx_init.branch(None)
        neq_ctx = ctx_init.branch(0)

        set_ctx(eq_ctx)
        res._set_(orig_index)
        after_eq_ctx = ctx()

        set_ctx(neq_ctx)

        neq_test = compile_and_call(item.__lt__, mid_value).ir()
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
