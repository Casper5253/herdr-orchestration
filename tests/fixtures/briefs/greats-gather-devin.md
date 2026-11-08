# Brief: phase 1 gather, blank slate (Q-199)

Use poteto-mode, Investigation playbook. Open your report with the goal, the playbook and the principles you applied.

Read C:\Users\caspe\Documents\Projects\herdr-orchestration\docs\greats\PROGRAM.md first: it holds the creator's direction, verbatim. You do phase 1 only, for the people named in the message that sent you here.

## Rules

- Write only `research/greats/<person-slug>.md` in C:\Users\caspe\Documents\Projects\herdr-orchestration. No git writes, no deletes, no installs. Do not run `gh` or `git clone`: read repos through the web.
- Every claim gets a primary-source URL. Never invent a source, quote, repo or file. If something is not public, say so.

## For each person

1. List every solution, skill, practice or tool they present for building software with AI or as an engineer: their repos, blog posts, talks (and their transcripts), and threads.
2. For each one, write down exactly how it works: the mechanism, the files and prompts, the config, the commands, and the code when it is public. Quote short key lines from their code or prompts, with a link. Depth over breadth: "they use evals" is not enough; how their evals are built, run and judged is.
3. Note the theory behind it in one or two sentences (why they say it works).
4. Do not judge our setup yet. No "we already do this".

## File format

`# <Person>` and a one-line bio with a source, then one `## <solution>` section per item with **What**, **How exactly** and **Sources**, then a closing `## Public code to reuse` list of repos and files.

## Report

Via `hx say claude` if you can (if denied, end your final message with it), under 6 lines: the files written and the item count per person.

## Your people

Lauren Tan (poteto, including the full pstack repo and its upstream), Matt Pocock, Theo (t3.gg).
