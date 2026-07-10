# Fix clamp() lower bound

## Problem

In `sample_target/mathutil.py`, the function `clamp(value, low, high)` does not correctly enforce the lower bound.

When `value` is below `low`, the function should return `low`. Currently `test_clamp_below_low` fails:

```
assert clamp(-5, 0, 10) == 0
```

## Expected behavior

`clamp` should return a value within the inclusive range `[low, high]`:

- if value < low → return low
- if value > high → return high
- otherwise → return value

Standard implementation: `return max(low, min(high, value))` (after normalizing low/high if swapped).

## Acceptance

All tests in `tests/test_mathutil.py` pass.
