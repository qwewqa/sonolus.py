//! Python-`json.dumps`-compatible JSON serialization.
//!
//! The frozen `collection.py` writes every site-tree JSON file with
//! `json.dumps(content)` using `CPython`'s **default** settings, which differ
//! from `serde_json` in three ways that reach the output bytes:
//!
//! - separators are `", "` and `": "` (with spaces),
//! - `ensure_ascii=True`: every non-ASCII character is escaped as a lowercase
//!   4-hex-digit backslash-u sequence (astral characters as UTF-16 surrogate
//!   pairs),
//! - floats are formatted with `repr(float)` (scientific notation iff the
//!   decimal exponent is `< -4` or `>= 16`, exponent written with an explicit
//!   sign and at least two digits, positional form always carries a decimal
//!   point).
//!
//! Reproducing that format keeps Rust-written site trees byte-identical to the
//! legacy Python output for JSON files (the T5.3 A/B check), on top of the
//! baseline determinism contract (same input in, same bytes out).
//!
//! Serialization is iterative (explicit work stack, invariant: no recursion
//! over user-sized structures). Note that values parsed by `serde_json` are
//! depth-limited (default 128) anyway.

use std::fmt::Write as _;

use serde_json::Value;

/// One pending unit of serialization work.
enum Frame<'a> {
    /// Serialize a JSON value.
    Value(&'a Value),
    /// Emit literal text (separators and closing brackets).
    Text(&'static str),
    /// Emit an escaped object key followed by `": "`.
    Key(&'a str),
}

/// Serializes a [`Value`] exactly like `CPython`'s `json.dumps(value)` with
/// default arguments.
///
/// # Panics
///
/// Panics if the value contains a non-finite float. `serde_json::Number`
/// cannot represent NaN or infinities (without the `arbitrary_precision`
/// feature), so this is unreachable for parsed or `json!`-built values.
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    let mut stack: Vec<Frame> = vec![Frame::Value(value)];
    while let Some(frame) = stack.pop() {
        match frame {
            Frame::Text(text) => out.push_str(text),
            Frame::Key(key) => {
                write_string(&mut out, key);
                out.push_str(": ");
            }
            Frame::Value(value) => match value {
                Value::Null => out.push_str("null"),
                Value::Bool(true) => out.push_str("true"),
                Value::Bool(false) => out.push_str("false"),
                Value::Number(n) => write_number(&mut out, n),
                Value::String(s) => write_string(&mut out, s),
                Value::Array(items) => {
                    out.push('[');
                    stack.push(Frame::Text("]"));
                    for (i, item) in items.iter().enumerate().rev() {
                        stack.push(Frame::Value(item));
                        if i > 0 {
                            stack.push(Frame::Text(", "));
                        }
                    }
                }
                Value::Object(map) => {
                    out.push('{');
                    stack.push(Frame::Text("}"));
                    for (i, (key, item)) in map.iter().enumerate().rev() {
                        stack.push(Frame::Value(item));
                        stack.push(Frame::Key(key));
                        if i > 0 {
                            stack.push(Frame::Text(", "));
                        }
                    }
                }
            },
        }
    }
    out
}

fn write_number(out: &mut String, n: &serde_json::Number) {
    if let Some(i) = n.as_i64() {
        let _ = write!(out, "{i}");
    } else if let Some(u) = n.as_u64() {
        let _ = write!(out, "{u}");
    } else {
        let x = n.as_f64().expect("a serde_json Number is i64, u64, or f64");
        out.push_str(&py_float_repr(x));
    }
}

/// Formats a finite `f64` exactly like `CPython`'s `repr(float)`.
///
/// Both `CPython` and Rust produce the shortest digit string that
/// round-trips, so only the *presentation* differs: `CPython` uses
/// scientific notation iff the decimal exponent (of the leading digit) is
/// `< -4` or `>= 16`, writes the exponent sign explicitly with at least two
/// digits, and always includes a decimal point in positional form (`2.0`,
/// not `2`).
fn py_float_repr(x: f64) -> String {
    debug_assert!(x.is_finite(), "JSON numbers are always finite");
    // Rust's LowerExp gives the shortest round-trip digits, e.g. "-1.2345e-3".
    let formatted = format!("{x:e}");
    let (mantissa, exp) = formatted
        .split_once('e')
        .expect("LowerExp output always contains an exponent");
    let exp: i32 = exp.parse().expect("LowerExp exponent is a valid integer");
    let (sign, mantissa) = match mantissa.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", mantissa),
    };
    // The digit string with the decimal point removed; no trailing zeros.
    let digits = match mantissa.split_once('.') {
        Some((int_part, frac_part)) => format!("{int_part}{frac_part}"),
        None => mantissa.to_owned(),
    };
    let mut out = String::from(sign);
    if !(-4..16).contains(&exp) {
        // Scientific: d[.ddd]e+XX / d[.ddd]e-XX with at least two exponent
        // digits.
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let exp_abs = exp.unsigned_abs();
        let exp_sign = if exp < 0 { '-' } else { '+' };
        let _ = write!(out, "e{exp_sign}{exp_abs:02}");
    } else if exp < 0 {
        // Positional, below one: 0.000ddd
        out.push_str("0.");
        for _ in 0..(-exp - 1) {
            out.push('0');
        }
        out.push_str(&digits);
    } else {
        // Positional, at least one: dd.dd / dd000.0
        let point = usize::try_from(exp).expect("0 <= exp < 16") + 1;
        if digits.len() > point {
            out.push_str(&digits[..point]);
            out.push('.');
            out.push_str(&digits[point..]);
        } else {
            out.push_str(&digits);
            for _ in 0..(point - digits.len()) {
                out.push('0');
            }
            out.push_str(".0");
        }
    }
    out
}

/// Writes a string literal with `CPython`'s `ensure_ascii=True` escaping: the
/// printable ASCII range (0x20..=0x7E) except `"` and `\` is literal, the
/// usual short escapes apply, and everything else becomes a lowercase
/// 4-hex-digit backslash-u escape (astral characters as surrogate pairs).
fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\u{c}' => out.push_str("\\f"),
            '\r' => out.push_str("\\r"),
            '\u{20}'..='\u{7e}' => out.push(c),
            _ => {
                let code = u32::from(c);
                if code <= 0xFFFF {
                    let _ = write!(out, "\\u{code:04x}");
                } else {
                    let reduced = code - 0x1_0000;
                    let high = 0xD800 + (reduced >> 10);
                    let low = 0xDC00 + (reduced & 0x3FF);
                    let _ = write!(out, "\\u{high:04x}\\u{low:04x}");
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    /// Expected strings are written with `^` standing in for a backslash so
    /// the source stays free of literal escape sequences.
    fn bs(s: &str) -> String {
        s.replace('^', "\\")
    }

    #[test]
    fn matches_python_json_dumps_reference_output() {
        // Expected string captured verbatim from CPython 3.14
        // `json.dumps(...)` on the same value.
        let value = json!({
            "a": 1,
            "b": [1.5, 2.0, true, null],
            "t": "caf\u{e9} \u{1F600}",
            "e": 1e16,
            "f": 1e-5,
            "g": 0.0001,
            "h": 1e15,
            "z": -0.0,
            "i": 5e-324,
            "j": 1.797_693_134_862_315_7e308,
            "ctl": "a\tb\u{1}c\u{7f}d",
        });
        let expected = bs(concat!(
            r#"{"a": 1, "b": [1.5, 2.0, true, null], "#,
            r#""t": "caf^u00e9 ^ud83d^ude00", "#,
            r#""e": 1e+16, "f": 1e-05, "g": 0.0001, "h": 1000000000000000.0, "#,
            r#""z": -0.0, "i": 5e-324, "j": 1.7976931348623157e+308, "#,
            r#""ctl": "a^tb^u0001c^u007fd"}"#,
        ));
        assert_eq!(dumps(&value), expected);
    }

    #[test]
    fn float_repr_matches_python_repr_table() {
        // (value, repr(value)) pairs from CPython.
        let table: &[(f64, &str)] = &[
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (2.0, "2.0"),
            (0.1, "0.1"),
            (123.456, "123.456"),
            (1e15, "1000000000000000.0"),
            (9_999_999_999_999_998.0, "9999999999999998.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (-1.5e-7, "-1.5e-07"),
            (5e-324, "5e-324"),
            (f64::MAX, "1.7976931348623157e+308"),
            (f64::MIN_POSITIVE, "2.2250738585072014e-308"),
            (-12345.6789, "-12345.6789"),
        ];
        for &(value, expected) in table {
            assert_eq!(py_float_repr(value), expected, "for {value:e}");
        }
    }

    #[test]
    fn escapes_match_python_defaults() {
        assert_eq!(dumps(&json!("\"\\")), bs(r#""^"^^""#));
        assert_eq!(dumps(&json!("\u{8}\u{c}")), bs(r#""^b^f""#));
        assert_eq!(dumps(&json!("\u{1F600}")), bs(r#""^ud83d^ude00""#));
        assert_eq!(dumps(&json!("\u{e9}e")), bs(r#""^u00e9e""#));
        // DEL is outside printable ASCII and must be escaped.
        assert_eq!(dumps(&json!("\u{7f}")), bs(r#""^u007f""#));
    }

    #[test]
    fn empty_containers_and_nesting() {
        assert_eq!(
            dumps(&json!({"e": {}, "l": [], "s": ""})),
            r#"{"e": {}, "l": [], "s": ""}"#
        );
        assert_eq!(
            dumps(&json!([[1, [2, [3]]], {"a": {"b": [false]}}])),
            r#"[[1, [2, [3]]], {"a": {"b": [false]}}]"#
        );
    }

    #[test]
    fn object_key_order_is_insertion_order() {
        let mut map = serde_json::Map::new();
        map.insert("z".to_owned(), json!(1));
        map.insert("a".to_owned(), json!(2));
        map.insert("m".to_owned(), json!(3));
        assert_eq!(dumps(&Value::Object(map)), r#"{"z": 1, "a": 2, "m": 3}"#);
    }

    #[test]
    fn deep_nesting_does_not_overflow_the_stack() {
        // Build a 100k-deep array iteratively and serialize it.
        let mut value = json!([]);
        for _ in 0..100_000 {
            value = Value::Array(vec![value]);
        }
        let s = dumps(&value);
        assert_eq!(s.len(), 2 * 100_001);
        // Tear it down iteratively too; a plain drop of a deep Value would
        // recurse in serde_json's Drop.
        let mut current = value;
        while let Value::Array(mut items) = current {
            current = items.pop().unwrap_or(Value::Null);
        }
    }
}
