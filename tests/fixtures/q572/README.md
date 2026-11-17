# Q-572 screen fixtures

`opencode-home.txt` is an unmodified read-only `hc read t47 --json` text capture from 17 November 2026. The terminal was on its home screen. Its todo panel was not visible. The sidebar regression adds a synthetic right-hand panel beyond the captured box border. It does not claim to capture the original incident.

`codex-wrapped.txt` is the screen text captured from t45 during the initial typing probe. That probe was stopped by the lead. No further live typing is authorized. The delayed-render regression uses this captured scrolled input, adds the observed busy footer text, and supplies an empty frame before the paste. The timing sequence is synthetic. The captured wrapped input itself is identified as nonempty by the original parser.
