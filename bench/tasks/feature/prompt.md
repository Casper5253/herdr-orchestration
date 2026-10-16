I'd like to add a "tag" filter to my notes CLI. Right now `Note` already has a `tags` list and `add` accepts tags, but nothing in the UI lets the user filter by tag. Please add:

1. A `--tags` option on `add` that accepts a comma-separated list of tags, stored on the note.
2. A new `tag` subcommand on the CLI that takes a single tag and prints the matching notes (just `#<id> <title>`, one per line, same format as `search`).
3. A `by_tag(tag)` method on `NoteStore` that returns notes whose tag list contains the given tag (case-insensitive, exact match per tag).

The package is just `models.py`, `store.py`, and `cli.py`. Keep the change minimal and don't break the existing `add`, `list`, or `search` behaviour.
