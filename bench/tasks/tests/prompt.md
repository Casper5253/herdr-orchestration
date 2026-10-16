Please write a pytest test module for `stats.py` in this directory. Cover at least:

- Empty input raises `ValueError`.
- `min`, `max`, `mean`, `median` for a small list (something like `[1, 2, 3, 4, 5]`).
- `median` is the average of the two middle values for even-length input.
- `stdev` is zero when every value is identical.
- `outliers` correctly flags a clear outlier (e.g. `[1, 1, 1, 1, 100]`) and does **not** flag values within 2 stdev of the mean (test the strict `>` boundary, not `>=`).
- `zscores` returns a list of the right length and the outlier's z-score has absolute value > 2.

Save the tests as `test_stats.py` in this directory. Do not modify `stats.py`. Running `pytest -q` from this directory should be green.
