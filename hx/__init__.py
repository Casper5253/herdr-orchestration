"""hx: routing, usage and context control for the Herdr team."""

__version__ = "0.1.0"

# Bumped when the on-disk state under <repo>/.hx/ or the shared state dirs
# changes shape in a way an older build cannot read. `hx activate` refuses to
# switch across a change unless the operator passes --allow-state-change.
STATE_FORMAT = 1
