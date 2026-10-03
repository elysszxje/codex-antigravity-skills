# Correction Feedback for Task

1. The `retry` decorator did not properly preserve the wrapped function's docstring and signature. Please use `functools.wraps`.
2. Add a test case verifying that non-transient exceptions (such as `ValueError`) are immediately raised without retry.
3. Re-run `python -m unittest discover -s tests -v` and include the test evidence in your report.
