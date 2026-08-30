# Evidence Transformation Instructions

Use this file for a non-identity statistic or output. A transformation turns
ordered locator selections into one exact presentation. It may parse, take a
magnitude, apply an exact scale, round, format, and attach a unit; it may not
aggregate, subtract, divide selected values, fit, classify, or infer a result.

## Inputs And Identity

`sources[0]` supplies input `0`; selected values within it are items `0`, `1`,
and so on. A non-table value refers to one item with:

```json
{"source": {"input": 0, "item": 0}}
```

Every selected item must be consumed exactly once. Narrow an over-broad
locator instead of dropping, duplicating, or collapsing values.

Use `"transformation": null` only when one selected primitive already has the
exact presented type and spelling. Identity preserves strings; renders
integers and decimals canonically; and renders Booleans and null as lowercase
`true`, `false`, and `null`. It does not parse strings, round, scale, add units,
or render binary floats and quantities.

## Value Recipes

Except for `percentage`, each value expression contains `source` and may
contain `parse`, `magnitude`, `scale`, and `render`, applied in that order:

```json
{
  "parse": "decimal",
  "render": {"mode": "fixed", "decimal_places": 2},
  "source": {"input": 0, "item": 0}
}
```

Use `parse:"integer"` or `parse:"decimal"` only for a complete numeric
string. Use `magnitude:true` only for absolute value. `scale` is one nonzero
finite decimal factor and does not establish that a unit conversion is
scientifically valid.

Every numeric value requires `render`, including a native numeric source.
Do not use `render` when passing through a string or null.

Numeric renderers are:

- `{"mode":"integer"}` or `{"mode":"grouped_integer"}` for exact
  integers;
- `{"mode":"fixed","decimal_places":N}`;
- `{"mode":"significant","significant_figures":N}`; and
- `{"mode":"scientific","significant_figures":N}`.

Add `"sign":"always"` only when zero and positive values must show `+`.
Use 0–18 decimal places or 1–18 significant figures. Rounding is decimal
round-half-to-even. The displayed precision, trailing zeroes, sign, and
notation must match exactly.

Supported forms are:

| Form | Values | Exact shape |
| --- | ---: | --- |
| `scalar` | 1 | `value[unit]` |
| `percentage` | 1 proportion | value multiplied by 100, fixed to one decimal place by default, followed by `%` |
| `range` | 2 | `lower–upper[unit]` |
| `plus_minus` | 2 | `value ± uncertainty[unit]` or `value +/- uncertainty[unit]` |
| `interval` | 3 | `value [lower, upper][unit]` |
| `tuple` | 2–8 | `(value, value, …)[unit]` |
| `text` | 1 string | exact selected string |

All forms except `percentage` use `values`. `percentage` instead uses one direct
`source` and optional `decimal_places`. `text` uses one value expression
without numeric operations. `%`, `°`, `°C`, `°F`, and `x` attach directly;
other units follow one ASCII space.

Examples:

```json
{
  "form": "percentage",
  "source": {"input": 0, "item": 0}
}
```

Selected string `0.676` produces exactly `67.6%`.

```json
{
  "form": "plus_minus",
  "unit": "mas",
  "values": [
    {
      "parse": "decimal",
      "render": {"mode": "fixed", "decimal_places": 2},
      "source": {"input": 0, "item": 0}
    },
    {
      "parse": "decimal",
      "render": {"mode": "fixed", "decimal_places": 2},
      "source": {"input": 0, "item": 1}
    }
  ]
}
```

Selected strings `3.417` and `0.084` produce `3.42 ± 0.08 mas` or
`3.42 +/- 0.08 mas`.

If the desired expression requires unsupported arithmetic or punctuation,
have the recorded research script retain the derived value or complete display
string and select that result directly.
