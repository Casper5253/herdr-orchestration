# Felix Rieseberg

Felix Rieseberg is an engineering manager at Anthropic, where he has led engineering for Claude.ai, Cowork and Claude Code Desktop, and a co-maintainer of Electron. ([post footer](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/), [GitHub](https://github.com/felixrieseberg))

## Ambition as four types: Speed, Altitude, Depth and Range

**What.** Rieseberg argues that the limiting factor in using models is not prompt cleverness but the ambition of the request. He splits ambition into four types: Speed (the same work faster), Altitude (operate at a higher level of abstraction and aim at the outcome behind the task), Depth (work the way a field-leading expert would), and Range (borrow methods and people from other fields).

**How exactly.**
- Speed is the baseline: "If you need some code written, I'll do it faster with Claude." He skips it because it is well established and requires little creativity. ([post](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- Altitude starts from the observation that "when you give the model a task, it's executing your best guess at how to get the outcome you want." Ask "what do you want to happen next? What's the goal beyond the goal?" His example: "At the beginning of last year, I still asked Claude to fix individual crashes for me. Eventually I asked Claude to simply keep our apps crash-free." ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- To make altitude real he changed the workflow and the model's access: Claude can fetch crash reports, map an app version to a git commit, and use Windows, macOS and Linux computers to reproduce and verify fixes. ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- He gives a QBR ladder: first ask "Tell me what changed this quarter, why, and what we should do about it"; then "Work out which three decisions are most urgent ... and start working on potential directions with for/against arguments"; then "The review exists so leadership catches problems early ... Design a better way to do that than a quarterly deck, show from our own history that it would have caught things sooner, and run it beside the current one for a quarter." ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- Depth has five moves: insist on the instruments of the best practitioners (databases and statistical models, not Markdown tables); pick unreasonable goals and tell the model they are reasonable (5s to 200ms); challenge what you know by giving the model raw data and asking it to reproduce or falsify the assumption; ask it to produce new knowledge (simulate customers, find the earliest churn signal, test it on unseen data); and use an outside judge. "Do not ask the model to judge itself." ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- He warns: "When prompted to act like a professional, models often switch vocabulary without actually switching methods." The point is to use the tools and methods, not the persona. ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- Range has two moves: replace nouns with verbs ("a quarterly review isn't 'a deck'. It's 'getting busy people to believe a story about cause and effect, and to decide under uncertainty, in 30 minutes'"), and assemble a crew of fields (analyst, journalist, editor, motion/audio designer, red team, short seller, forensic accountant). "Don't ask the model to write like a short seller, ask it to do what a short seller does to research their position." ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- All of this assumes the model has a computer, can install software, and can access the relevant data through MCP connectors or other means. ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))
- The theory: self-efficacy and specific hard goals change what people attempt. He cites Locke and Latham's 35 years of goal-setting research: people given specific hard goals perform better than people told to try their best. He also cites the four-minute mile, the two-hour marathon, Dantzig's unsolved "homework", and Linux's "just a hobby" announcement as examples of belief and permission-to-fail expanding what gets attempted. ([post](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/); [Locke and Latham paper](https://med.stanford.edu/content/dam/sm/s-spire/documents/PD.locke-and-latham-retrospective_Paper.pdf))

**Sources.** [How to be more ambitious: A tutorial for humans with models](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/), [Locke and Latham retrospective](https://med.stanford.edu/content/dam/sm/s-spire/documents/PD.locke-and-latham-retrospective_Paper.pdf).

## The depth prompt: make the request deeper, not bigger

**What.** A reusable prompt that asks the model to rewrite a task at expert depth before doing it.

**How exactly.** Rieseberg gives the prompt verbatim:

> I want a deeper version of this task, not a bigger one. Before rewriting it: (1) say what a top professional in this field would do here that I'm not asking for; (2) name the rawest material they would start from instead of what has already been digested for me; (3) name the specific methods and tools they would use; (4) say what, other than you or me, would show the result is right. Then rewrite my prompt to ask for exactly that.

([post](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))

The four answers become the new prompt's context: the professional's method, the raw inputs, the tools, and the external verification. The last point matters because it forces an outside judge rather than self-grading. ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))

The theory: the model cannot aim higher than the request, and a vague request collapses to the most obvious method. The rewrite makes the expert method explicit before any work starts. ([same](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/))

**Sources.** [How to be more ambitious](https://felixrieseberg.com/how-to-be-more-ambitious-a-tutorial-for-humans-with-models/).

## Hunger beats tenure: business impact over backlog

**What.** Advice for engineers without social capital, status or power: pick business impact, pick a radical idea, build a working prototype over a weekend, focus on one idea, and lean into the fresh perspective.

**How exactly.**
- He retired the traditional coding interview at Anthropic because "there's no more point in giving you a little React challenge than there is in quizzing people about assembly; Claude is now better at both." ([Hunger beats tenure](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))
- Fresh eyes are an advantage: at Stripe, Patrick Collison asked newcomers to notice things that seem odd because old-timers stop seeing them. He cites James March on organizations needing "an influx of the naive and ignorant" and Orson Welles on the confidence of ignorance. ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))
- The five steps:
  1. Pick business impact over backlog. Understand what the company, group or department is trying to accomplish; "understanding what kind of goals your manager's manager has is often a useful proxy." He quotes Patrick McKenzie: "engineers are hired to create business value, not to program things." ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/); [McKenzie](https://www.kalzumeus.com/2011/10/28/dont-call-yourself-a-programmer/))
  2. Pick a radical idea that would dramatically improve the chances of those goals.
  3. Build it over a weekend. A working prototype can bypass the PRD, buy-in and lobbying cycle.
  4. Focus on one good idea. "If your prototype involves the creation of a new programming language, a backend, and also somehow mobile apps, you're not going to be successful."
  5. Lean into the fresh perspective: user empathy if user-facing, or never having learned why something is "impossible" if infrastructure/tooling.
- The worked example: Anthropic engineers were used to Claude Code as a CLI; two relatively new engineers, Anthony Morris and Alice Zhao, built a working graphical prototype over one weekend. The first public version shipped roughly eight days later. ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))
- The theory: execution is no longer the expensive part, so the scarce contribution is choosing the right business outcome and communicating a working idea. A weekend prototype is a communication device that shows both the opportunity and the person who can run with it. ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))

**Sources.** [Hunger beats tenure: Advice for junior engineers](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/), [Patrick McKenzie, "Don't Call Yourself a Programmer"](https://www.kalzumeus.com/2011/10/28/dont-call-yourself-a-programmer/).

## Hiring by portfolio, not coding puzzles

**What.** Replace small coding puzzles with evidence that a candidate can build with agents: a substantial project, deployed, secured, and attacked by adversarial agents.

**How exactly.**
- Rieseberg says he stopped asking candidates to do traditional coding interviews. "Our most impactful builders are successful regardless of how good they are at these tests." ([Hunger beats tenure](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))
- He is "scanning resumes and portfolios for little prototypes that exhibit taste, empathy, and principled thinking." ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))
- The signal is the prototype itself: a small, working artifact that shows what the person noticed, what they chose to build, and how they communicated the opportunity. ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))
- The theory: if execution is cheap, the signal is no longer whether someone can write a small algorithm; it is whether they can choose, build and communicate a real product with the tools available. ([same](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/))

**Sources.** [Hunger beats tenure](https://felixrieseberg.com/hunger-beats-tenure-advice-for-junior-engineers/), [interview discussion in the Sequoia summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/).

## Public projects as worked examples

**What.** Rieseberg's public repositories show the kind of desktop, emulator, agent and educational software he builds; they are the code side of the engineering taste in the two posts.

**How exactly.**
- [electron/electron](https://github.com/electron/electron): cross-platform desktop apps with web technologies; Rieseberg is a co-maintainer. [electron/fiddle](https://github.com/electron/fiddle): the easiest way to get started with Electron.
- [felixrieseberg/windows95](https://github.com/felixrieseberg/windows95): Windows 95 in an Electron app, running on macOS, Linux and Windows.
- [felixrieseberg/macintosh.js](https://github.com/felixrieseberg/macintosh.js): a virtual Apple Macintosh with System 8, running in Electron.
- [felixrieseberg/clippy](https://github.com/felixrieseberg/clippy): Clippy with a local LLM, an example of a small AI desktop toy.
- [felixrieseberg/claude-coach](https://github.com/felixrieseberg/claude-coach): Claude as an endurance coach for marathons, triathlons and Ironman events.
- [felixrieseberg/relic](https://github.com/felixrieseberg/relic): a minimal coding agent for ancient operating systems and devices, in C.
- [felixrieseberg/language-model-builder](https://github.com/felixrieseberg/language-model-builder): an educational macOS app that builds and chats with a tiny language model trained on the Mac.
- The pattern across them is small, complete, polished artifacts: an emulator you can run, a toy you can touch, a coach you can talk to, or a minimal agent small enough to understand. ([GitHub repositories](https://github.com/felixrieseberg?tab=repositories))

**Sources.** [GitHub repositories](https://github.com/felixrieseberg?tab=repositories), [Electron](https://github.com/electron/electron), [Electron Fiddle](https://github.com/electron/fiddle), [windows95](https://github.com/felixrieseberg/windows95), [macintosh.js](https://github.com/felixrieseberg/macintosh.js), [clippy](https://github.com/felixrieseberg/clippy), [claude-coach](https://github.com/felixrieseberg/claude-coach), [relic](https://github.com/felixrieseberg/relic), [language-model-builder](https://github.com/felixrieseberg/language-model-builder).

## Public code to reuse

- [electron/electron](https://github.com/electron/electron): the cross-platform desktop runtime Rieseberg co-maintains.
- [electron/fiddle](https://github.com/electron/fiddle): a small Electron playground, useful as a reference for a minimal desktop app.
- [felixrieseberg/windows95](https://github.com/felixrieseberg/windows95): an Electron app that packages a full emulator.
- [felixrieseberg/macintosh.js](https://github.com/felixrieseberg/macintosh.js): another Electron emulator, with the packaging and rendering details exposed.
- [felixrieseberg/clippy](https://github.com/felixrieseberg/clippy): a small local-LLM desktop toy.
- [felixrieseberg/claude-coach](https://github.com/felixrieseberg/claude-coach): a Claude-powered coaching app.
- [felixrieseberg/relic](https://github.com/felixrieseberg/relic): a minimal coding agent in C for old systems.
- [felixrieseberg/language-model-builder](https://github.com/felixrieseberg/language-model-builder): an educational tiny-LLM trainer and chat app.
