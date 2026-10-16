My script is crashing. Please figure out what's wrong and fix it.

The command I ran:

    python pipeline.py sample.json

The output I got:

    Traceback (most recent call last):
      File ".../pipeline.py", line 40, in <module>
        raise SystemExit(main(sys.argv))
      File ".../pipeline.py", line 34, in main
        for line in build_report(rows):
      File ".../pipeline.py", line 24, in build_report
        out.append(_label_for(r))
      File ".../pipeline.py", line 16, in _label_for
        return f"#{row['id']:>3}  {row['name']:<20}  score={row['score']}"
    TypeError: unsupported format string passed to NoneType.__format__

The second row in `sample.json` has no `id` field on purpose — that data is broken and the loader should reject it loudly, not let it through. After your fix, the script should exit with a clear `ValueError` pointing at the missing field, and `python pipeline.py sample.json` should exit non-zero without producing any of the report lines.
