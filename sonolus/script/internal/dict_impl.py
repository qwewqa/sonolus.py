from typing import Any

from sonolus.script.array import Array
from sonolus.script.debug import error
from sonolus.script.internal.context import Context, ctx, set_ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.num import Num
from sonolus.script.record import Record


class DictImpl[Keys, OrderedKeys, Values](Record):
    _keys: Keys  # tuple[K, ...]
    _ordered_keys: OrderedKeys  # tuple[K, ...] | None
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
        if self._has_ordered_keys:
            return self._binsearch(item) >= 0
        return self._linsearch(item) >= 0

    def __getitem__(self, item):
        constsearch_res = self._try_constsearch(item)
        if constsearch_res is not None:
            assert constsearch_res >= 0, "Key not found"
            return self._values[constsearch_res]
        if not self._has_array_values:
            error(
                "Dict must be accessed via a compile time constant unless "
                "all value are compile time constants of a uniform type."
            )
        if self._has_ordered_keys:
            index = self._binsearch(item)
        else:
            index = self._linsearch(item)
        assert index >= 0, "Key not found"
        return self._values[index]

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
                ordered_keys = tuple(validate_value(k) for k in sorted(py_keys))
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
        from sonolus.backend.visitor import compile_and_call

        for i, k in enumerate(self._keys):
            eq = validate_value(compile_and_call(k.__eq__, item))
            if not eq._is_py_():
                return None
            if eq._as_py_():
                return i
        return -1

    @meta_fn
    def _binsearch(self, item):
        return self._binsearch_internal(item, 0, self._size, Num._alloc_())

    @meta_fn
    def _binsearch_internal(self, item, lo, hi, res):
        from sonolus.backend.visitor import compile_and_call

        if lo >= hi:
            res._set_(-1)
            return res
        mid = (lo + hi) // 2
        mid_value = self._keys[mid]
        ctx_init = ctx()
        ctx_init.test = compile_and_call(mid_value.__eq__, item).ir()
        eq_ctx = ctx_init.branch(None)
        neq_ctx = ctx_init.branch(0)

        set_ctx(eq_ctx)
        res._set_(mid)
        after_eq_ctx = ctx()

        set_ctx(neq_ctx)

        if lo == mid:
            res._set_(-1)
            set_ctx(Context.meet([after_eq_ctx, ctx()]))
            return res

        neq_ctx.test = compile_and_call(mid_value.__lt__, item).ir()
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
