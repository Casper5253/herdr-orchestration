# Andrej Karpathy

Andrej Karpathy is a co-founder of OpenAI, former director of AI at Tesla, founder of Eureka Labs, and the author of nanoGPT, nanochat, llm.c, micrograd and autoresearch. ([Sequoia Ascent 2026](https://karpathy.bearblog.dev/sequoia-ascent-2026/), [GitHub](https://github.com/karpathy))

## Software 2.0: the dataset and the weights are the program

**What.** Software 2.0 is Karpathy's name for the shift from writing explicit instructions to specifying a dataset, an architecture and an optimizer, then letting training compile that specification into neural-network weights.

**How exactly.**
- In Software 1.0 the source is code. In Software 2.0 the source is the dataset plus the neural-net architecture. Training compiles it into the final network. Karpathy: "In Software 2.0 most often the source code comprises 1) the dataset that defines the desirable behavior and 2) the neural net architecture that gives the rough skeleton of the code, but with many details (the weights) to be filled in. The process of training the neural network compiles the dataset into the binary — the final neural network." ([Software 2.0](https://medium.com/@karpathy/software-2-0-a64152b37c35))
- The workflow splits: "the 2.0 programmers (data labelers) edit and grow the datasets, while a few 1.0 programmers maintain and iterate on the surrounding training code infrastructure, analytics, visualizations and labeling interfaces." ([same](https://medium.com/@karpathy/software-2-0-a64152b37c35))
- He lists the operational properties he says make the stack attractive: it is computationally homogeneous (matrix multiplies and ReLU), constant running time, constant memory, portable, agile (remove half the channels, retrain, get a smaller faster model), and separately trained modules can be backpropagated through as one. The costs are opacity, silent failure and adversarial examples. ([same](https://medium.com/@karpathy/software-2-0-a64152b37c35))
- The theory, in two sentences: for a large class of problems it is easier to collect examples of desired behavior than to write the rules, and an optimizer searching a continuous program space can find code that beats hand-written rules. ([same](https://medium.com/@karpathy/software-2-0-a64152b37c35))

**Sources.** [Software 2.0 essay](https://medium.com/@karpathy/software-2-0-a64152b37c35). The talk version is in [Software Is Changing (Again)](https://www.youtube.com/watch?v=LCEmiRjPEtQ).

## Software 3.0: prompts, context and tools are the program

**What.** Software 3.0 is programming a general LLM through prompts, context, examples, tools, memory and instructions. The context window is the main lever; the model interprets that context and performs computation over digital information.

**How exactly.**
- The earlier one-liner was "The hottest new programming language is English." ([tweet, January 2023](https://x.com/karpathy/status/1617979122625712128))
- In the YC talk Karpathy says the model became "programmable with natural language", "your prompts are now programs that program these new programmable computers", and "the context window is kind of like the RAM. And then the LLM is orchestrating memory and compute for problem-solving using all these capabilities." ([Software Is Changing (Again), transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d), [video](https://www.youtube.com/watch?v=LCEmiRjPEtQ))
- He contrasts a Software 1.0 installer (a shell script with environment branches) with a Software 3.0 installer: a block of text you paste into an agent. The agent reads the local environment, debugs errors, adapts to the machine and completes the setup. "That is a different kind of program. It is less precise, but more adaptive." ([Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The MenuGen example shows the same move at application level. The old app needed OCR, image generation, frontend, API, auth, payments, secrets, domain and Vercel deployment. The Software 3.0 version gives the menu photo to a multimodal model and asks it to render dish images into the menu image. "All of MenuGen is spurious in that framing... In the Software 3.0 paradigm, the neural network does more of the work." ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- He extends the practice to documentation and infrastructure: publish `llms.txt`, serve docs as Markdown, replace instructions like "click here" with an equivalent programmatic action an agent can execute, and expose MCP servers and structured actions. "Any time your docs say 'click here,' that is bad. An LLM will not be able to instrument this action right now." ([YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The theory: the program is not only the prompt text; it is the whole context configuration, including examples, tools, schemas, memory and environment access. The model supplies the interpretation and adaptation that explicit code had to spell out. ([Sequoia summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))

**Sources.** [The hottest new programming language is English](https://x.com/karpathy/status/1617979122625712128), [Software Is Changing (Again) video](https://www.youtube.com/watch?v=LCEmiRjPEtQ), [transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d), [Sequoia Ascent 2026 summary and edited transcript](https://karpathy.bearblog.dev/sequoia-ascent-2026/), [2025 LLM Year in Review](https://karpathy.bearblog.dev/year-in-review-2025/).

## LLM OS: model as kernel, context as RAM, tools as peripherals

**What.** The LLM OS is a mental model: the model is the kernel/CPU, the context window is RAM, files and retrieval are storage, tools are peripherals, and agents are processes.

**How exactly.**
- The seed tweet: LLMs are emerging "not as a chatbot, but the kernel process of a new Operating System." ([tweet, September 2023](https://x.com/karpathy/status/1707437820045062561))
- In the YC talk he draws the same mapping: "the LLM is a new kind of operating system... The context window is kind of like the RAM. And then the LLM is orchestrating memory and compute for problem-solving using all these capabilities." ([transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The model is useful for design decisions. When deciding whether something should be a tool, a subagent, a piece of context or memory, the OS analogy gives a vocabulary: syscalls/tools, RAM/context, disk/retrieval, processes/agents, permissions. Karpathy applies it to agent-native infrastructure: docs as Markdown, CLIs, APIs, MCP servers, structured logs, machine-readable schemas, copy-pasteable instructions, safe permissions and auditable actions. ([Sequoia summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- He also calls the current era "1960s computing": compute is centralized, users are thin clients, and the personal-computing and GUI layers have not been invented yet. ([YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The theory: treating the model as a kernel forces you to design the surrounding operating system, not just the prompt. Context, tools, memory, permissions and feedback loops become one architecture. ([YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))

**Sources.** [LLM OS tweet](https://x.com/karpathy/status/1707437820045062561), [Intro to Large Language Models](https://www.youtube.com/watch?v=zjkBMFhNj_g), [Software Is Changing (Again) transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d), [Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/).

## Partial autonomy and the autonomy slider

**What.** Build products that keep the human in a fast generation-verification loop instead of jumping to full autonomy. The product exposes a slider from autocomplete to a full agent.

**How exactly.**
- The Cursor example is the concrete ladder: Tab completion, then Cmd+K to change a selected chunk, then Cmd+L to work on a file, then Cmd+I to let an agent act across the repo. "You are in charge of the autonomy slider, and depending on the complexity of the task at hand, you can tune the amount of autonomy that you're willing to give up for that task." ([YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The interface should make verification cheap. Karpathy argues for application-specific GUIs because "reading text is effortful, and looking at a diff is just a highway to your brain"; a red/green diff and accept/reject keys let the human audit a fallible system quickly. ([same](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- Keep the AI on a leash: small incremental chunks, single-purpose tools, a fast generation-verification loop, and prompts complete enough that verification does not fail and send the loop spinning. "It's less Iron Man robots, and more Iron Man suits that you want to build." ([same](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The demo-to-product gap explains the caution: "a demo works if anything works. But a product only works if everything works." Software is in the high-reliability category, so the tail matters. ([same](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The theory: autonomy is a product setting, not a binary. The human's verification capacity is the bottleneck, and the UI plus the slider are how you expand it without losing control. ([same](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))

**Sources.** [Software Is Changing (Again) transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d), [video](https://www.youtube.com/watch?v=LCEmiRjPEtQ), [Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/).

## Vibe coding

**What.** Vibe coding is a specific, low-stakes practice: give in to the vibes, accept the model's output, do not read the diffs, and work around bugs by prompting again.

**How exactly.**
- The original tweet: "There's a new kind of coding I call 'vibe coding', where you fully give in to the vibes, embrace exponentials, and forget that the code even exists." He describes talking to Cursor Composer with SuperWhisper, asking for "the dumbest things like 'decrease the padding on the sidebar by half'", accepting all, not reading diffs, pasting errors with no comment, and working around bugs or asking for random changes until they go away. "It's not too bad for throwaway weekend projects, but still quite amusing." ([tweet, February 2025](https://x.com/karpathy/status/1886192184808149383))
- The MenuGen case is the boundary. The vibe-coded app worked on his laptop in a few hours, but making it real took about a week of auth, payments, domain and Vercel work. "The code was actually the easy part of vibe coding MenuGen... All of this was not code. All of this DevOps stuff was me in the browser clicking stuff." ([YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- He says vibe coding is "so great when you want to build something super duper custom that doesn't appear to exist, and you just want to wing it because it's a Saturday". It is also a good way to build intuition for what models can and cannot do. ([same](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- The theory: lowering the cost of code to near zero changes what is worth building. Vibe coding raises the floor and makes software accessible, but it is explicitly for low-stakes and throwaway work, not for a production quality bar. ([2025 LLM Year in Review](https://karpathy.bearblog.dev/year-in-review-2025/))

**Sources.** [Vibe coding tweet](https://x.com/karpathy/status/1886192184808149383), [YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d), [MenuGen post and TLDR](https://x.com/karpathy/status/1917961248031080455), [2025 LLM Year in Review](https://karpathy.bearblog.dev/year-in-review-2025/).

## Agentic engineering

**What.** Agentic engineering is the professional discipline that sits on top of vibe coding: coordinating fallible, stochastic agents while preserving correctness, security, taste and maintainability.

**How exactly.**
- The distinction: "Vibe coding raises the floor. Agentic engineering is about extrapolating the ceiling." The agentic engineer "does not blindly accept generated code. They design specs, supervise plans, inspect diffs, write tests, create evaluation loops, manage permissions, isolate worktrees, and preserve quality." ([Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The MenuGen payment bug is the worked example. The agent tried to match Stripe purchases to Google accounts by email address. "A human needs enough product and engineering judgment to insist on persistent user IDs." The human owns the spec, the system boundaries and the identity model; the agent fills in the code. ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/); [edited transcript](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The verifiability frame: traditional software automates what you can specify; LLMs and RL automate what you can verify. Capability spikes where a task is verifiable, emphasized by labs, covered by data and economically valuable: `capability spike ~= verifiability x training attention x data coverage x economic value`. Coding and math move fast because they have automatic reward signals; other domains may need fine-tuning, evals or their own RL environments. ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/); [2025 review](https://karpathy.bearblog.dev/year-in-review-2025/))
- Jagged intelligence follows: models can "refactor a 100,000-line codebase or find zero-day vulnerabilities" and still tell you to walk to the car wash. "To the extent models remain jagged, it means you need to be in the loop." ([Sequoia summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- Hiring should test the discipline: give a candidate a substantial project, have them build and deploy it with agents, secure it, then send adversarial agents to break it. ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The theory: agents are powerful but fallible and jagged; the human's scarce contribution moves to specs, taste, evals, security, system boundaries and knowing when the model is off the rails. "You can outsource your thinking, but you can't outsource your understanding." ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/))

**Sources.** [Sequoia Ascent 2026 summary and edited transcript](https://karpathy.bearblog.dev/sequoia-ascent-2026/), [video](https://www.youtube.com/watch?v=96jN2OCOfLs), [2025 LLM Year in Review](https://karpathy.bearblog.dev/year-in-review-2025/).

## Autoresearch: `program.md` as the research org

**What.** Autoresearch is a small repository that gives an AI agent a real single-GPU LLM training setup and lets it run experiments autonomously overnight.

**How exactly.**
- The repo has three files that matter: `prepare.py` (fixed constants, one-time data prep, tokenizer, dataloader, evaluation; do not modify), `train.py` (the single file the agent edits: model, optimizer, training loop), and `program.md` (baseline instructions for one agent; the human edits this). "The core idea is that you're not touching any of the Python files like you normally would as a researcher. Instead, you are programming the `program.md` Markdown files that provide context to the AI agents and set up your autonomous research org." ([README](https://github.com/karpathy/autoresearch))
- Every run has a fixed 5-minute wall-clock training budget. The metric is `val_bpb`, validation bits per byte, lower is better and vocab-size independent. The fixed budget makes architectural changes comparable and yields about 12 experiments per hour, roughly 100 while a person sleeps. ([README](https://github.com/karpathy/autoresearch))
- Setup: `uv sync`, `uv run prepare.py`, then `uv run train.py` for one manual run. The agent instruction is: "Hi have a look at program.md and let's kick off a new experiment! let's do the setup first." ([README](https://github.com/karpathy/autoresearch))
- The loop in `program.md`: create branch `autoresearch/<tag>`; read `README.md`, `prepare.py`, `train.py`; verify `~/.cache/autoresearch/` data; initialize `results.tsv`; edit `train.py`; `git commit`; run `uv run train.py > run.log 2>&1`; `grep "^val_bpb:\|^peak_vram_mb:" run.log`; record the result; if `val_bpb` improved, advance the branch and keep the commit; if equal or worse, `git reset` back; repeat. ([program.md](https://github.com/karpathy/autoresearch/blob/master/program.md))
- The TSV columns are `commit`, `val_bpb`, `memory_gb`, `status` (`keep`/`discard`/`crash`) and `description`. The file is deliberately left untracked. Crashes are logged; if a run exceeds 10 minutes it is killed and treated as a failure. The instruction says "NEVER STOP" once the loop begins. ([program.md](https://github.com/karpathy/autoresearch/blob/master/program.md))
- The design constraints: one file to modify, a fixed time budget, one GPU, one metric, no distributed training, no complex configs. `program.md` is "essentially a super lightweight 'skill'." ([README](https://github.com/karpathy/autoresearch))
- The theory: make the research loop legible to an agent. A narrow edit surface, a stable evaluation and a git branch as the ratchet turn research judgment into a repeatable loop. ([README](https://github.com/karpathy/autoresearch))

**Sources.** [autoresearch README](https://github.com/karpathy/autoresearch), [program.md](https://github.com/karpathy/autoresearch/blob/master/program.md), [repository](https://github.com/karpathy/autoresearch).

## nanochat: the minimal full-stack ChatGPT harness

**What.** nanochat is a minimal, hackable, single-GPU LLM harness that covers tokenization, pretraining, finetuning, evaluation, inference and a chat interface.

**How exactly.**
- The README calls it "the simplest experimental harness for training LLMs", designed for a single GPU node, with minimal code covering all major stages. It can train a GPT-2-capability model for about $48 on an 8xH100 node, or about $15 on a spot instance. ([README](https://github.com/karpathy/nanochat))
- One complexity dial controls the model: `--depth`, the number of layers. All other hyperparameters (width, heads, learning-rate adjustments, training horizons, weight decays) are calculated from it so the model comes out compute-optimal. GPT-2 capability is around depth 24-26. ([README](https://github.com/karpathy/nanochat))
- The reference pipeline is `runs/speedrun.sh`; it trains the model, then `python -m scripts.chat_cli` lets you talk to it. `runs/runcpu.sh` shows a CPU/MPS example. Research uses `runs/scaling_laws.sh` and `runs/miniseries.sh`; a quick 12-layer experiment is `torchrun --standalone --nproc_per_node=8 -m scripts.base_train -- --depth=12 --run="d12" ...`. ([README](https://github.com/karpathy/nanochat))
- The repository is deliberately not a framework: "there are no giant configuration objects, model factories, or if-then-else monsters in the code base. It is a single, cohesive, minimal, readable, hackable, maximally-forkable 'strong baseline' codebase designed to run start to end and produce a ChatGPT model you can talk to." ([README](https://github.com/karpathy/nanochat))
- The file layout exposes the whole stack: `nanochat/gpt.py`, `tokenizer.py`, `engine.py` (KV-cache inference), `execution.py` (Python tool execution), `core_eval.py`, `loss_eval.py`, `optim.py` (AdamW + Muon), `dataloader.py`, `dataset.py`; `scripts/base_train.py`, `base_eval.py`, `chat_sft.py`, `chat_rl.py`, `chat_cli.py`, `chat_eval.py`; `tasks/` for ARC, GSM8K, HumanEval, MMLU and SmolTalk; `tests/`. ([README](https://github.com/karpathy/nanochat))
- The theory: a real end-to-end system with one complexity dial is easier to understand, fork and improve than an exhaustively configurable framework. ([README](https://github.com/karpathy/nanochat))

**Sources.** [nanochat README](https://github.com/karpathy/nanochat), [nanochat repository](https://github.com/karpathy/nanochat), [original nanochat discussion](https://github.com/karpathy/nanochat/discussions/1).

## nanoGPT: the small readable GPT training loop

**What.** nanoGPT is a small, readable repository for training and finetuning medium-sized GPTs.

**How exactly.**
- `train.py` is a roughly 300-line training loop; `model.py` is a roughly 300-line GPT definition that can optionally load OpenAI GPT-2 weights. The README says it "prioritizes teeth over education" and is "very easy to hack". ([README](https://github.com/karpathy/nanoGPT))
- The quick start trains a character-level GPT on Shakespeare: `python data/shakespeare_char/prepare.py`, then `python train.py config/train_shakespeare_char.py`, then `python sample.py --out_dir=out-shakespeare-char`. On one A100 the run takes about three minutes and reaches a best validation loss of 1.4697. ([README](https://github.com/karpathy/nanoGPT))
- Reproducing GPT-2 (124M) uses OpenWebText: `python data/openwebtext/prepare.py`, then `torchrun --standalone --nproc_per_node=8 train.py config/train_gpt2.py`. The run takes about four days on an 8xA100 40GB node and reaches a loss of about 2.85. ([README](https://github.com/karpathy/nanoGPT))
- Finetuning is the same loop with `init_from` set to a GPT-2 checkpoint and a smaller learning rate: `python train.py config/finetune_shakespeare.py`; then `python sample.py --out_dir=out-shakespeare`. ([README](https://github.com/karpathy/nanoGPT))
- The README now points to nanochat as the improved cousin and calls nanoGPT "very old and deprecated but I will leave it up for posterity." ([README](https://github.com/karpathy/nanoGPT))
- The theory: a small, plain, hackable training loop is a better learning and research base than a large framework when the goal is to understand or modify the algorithm. ([README](https://github.com/karpathy/nanoGPT))

**Sources.** [nanoGPT README](https://github.com/karpathy/nanoGPT), [nanoGPT repository](https://github.com/karpathy/nanoGPT), [nanochat README](https://github.com/karpathy/nanochat).

## llm.c: GPT training in raw C and CUDA

**What.** llm.c trains GPT-2 and GPT-3 miniseries in simple, pure C/CUDA, with a parallel PyTorch reference implementation.

**How exactly.**
- The CPU reference is `train_gpt2.c`, "a simple reference CPU fp32 implementation in ~1,000 lines of clean code in one file". The mainline GPU code is `train_gpt2.cu`. The repo aims to keep only C and CUDA in the mainline. ([README](https://github.com/karpathy/llm.c))
- CPU quick start: `chmod u+x ./dev/download_starter_pack.sh`, `./dev/download_starter_pack.sh`, `make train_gpt2`, `OMP_NUM_THREADS=8 ./train_gpt2`. It downloads a tokenized TinyShakespeare dataset and GPT-2 124M weights, trains for 40 steps with AdamW and samples text. ([README](https://github.com/karpathy/llm.c))
- The unit test is the correctness anchor: `make test_gpt2` loads `gpt2_124M_debug_state.bin`, runs a forward pass, compares logits and loss with PyTorch, then runs 10 Adam training iterations and checks the losses match. The GPU test is `make test_gpt2cu PRECISION=FP32 && ./test_gpt2cu`; the expected output is `overall okay: 1`. ([README](https://github.com/karpathy/llm.c))
- The repository is explicitly educational and serious at once: `dev/cuda` is a library of hand-written, documented kernels from simple to fast; the root stays simple and readable. Karpathy says he will reject a 2% performance PR that costs 500 lines of complex C and an exotic dependency. ([README](https://github.com/karpathy/llm.c))
- The theory: writing the whole algorithm in one readable file exposes the machinery that frameworks hide, and a PyTorch reference test keeps the C implementation honest. ([README](https://github.com/karpathy/llm.c))

**Sources.** [llm.c README](https://github.com/karpathy/llm.c), [llm.c repository](https://github.com/karpathy/llm.c), [GPT-2 reproduction discussion](https://github.com/karpathy/llm.c/discussions/481).

## micrograd and microGPT: the algorithm in one small file

**What.** micrograd is a tiny autograd engine and neural-network library; microGPT is a complete GPT-2-like transformer training and sampling implementation in one dependency-free Python file.

**How exactly.**
- micrograd implements backpropagation (reverse-mode autodiff) over a dynamically built DAG and a small neural-net library with a PyTorch-like API. The engine is about 100 lines and the nn library about 50 lines. The DAG operates on scalar values, chopping each neuron into individual adds and multiplies, which is enough to train a 2-layer MLP binary classifier on the moon dataset. ([README](https://github.com/karpathy/micrograd))
- The API is `Value`: `a = Value(-4.0)`, `b = Value(2.0)`, `c = a + b`, `d = a * b + b**3`, then `g.backward()` and `a.grad` / `b.grad`. The tests use PyTorch as a reference for the gradients; run `python -m pytest`. ([README](https://github.com/karpathy/micrograd))
- microGPT is linked from the README as a more advanced example: "it trains and samples from a full GPT-2-like transformer in pure, dependency-free Python. It builds on a more efficient and better version of the autograd engine here ... and is the complete algorithm in a single file — everything else is just efficiency." ([README](https://github.com/karpathy/micrograd); [microGPT gist](https://gist.github.com/karpathy/8627fe009c40f57531cb18360106ce95); [explainer](https://karpathy.github.io/2026/02/12/microgpt/))
- The theory: the complete algorithm in a single file is the best educational artifact; frameworks and kernels are efficiency layers around it. ([README](https://github.com/karpathy/micrograd))

**Sources.** [micrograd README](https://github.com/karpathy/micrograd), [micrograd repository](https://github.com/karpathy/micrograd), [microGPT gist](https://gist.github.com/karpathy/8627fe009c40f57531cb18360106ce95), [microGPT explainer](https://karpathy.github.io/2026/02/12/microgpt/).

## A Recipe for Training Neural Networks

**What.** A six-stage process for applying a neural network to a new problem, built from the observation that neural-net training is a leaky abstraction and fails silently.

**How exactly.**
- The two motivating observations: "Neural net training is a leaky abstraction" and "Neural net training fails silently." A syntactically correct run can still be logically wrong, so a "fast and furious" approach "does not work and only leads to suffering." ([A Recipe for Training Neural Networks](https://karpathy.github.io/2019/04/25/recipe/))
- Stage 1, become one with the data: inspect thousands of examples for hours, understand the distribution, look for duplicates, corrupted images/labels, imbalance, bias, label noise, and write code to search/filter/sort/visualize outliers. ([same](https://karpathy.github.io/2019/04/25/recipe/))
- Stage 2, end-to-end skeleton plus dumb baselines: pick a simple model you could not have screwed up, fix the random seed, disable augmentation, add significant digits to eval, verify loss at init, initialize the final layer well, compute a human baseline, train an input-independent baseline, overfit one batch, verify decreasing training loss, visualize exactly what goes into the net, visualize prediction dynamics, use backprop to chart dependencies, and generalize a special case. ([same](https://karpathy.github.io/2019/04/25/recipe/))
- Stage 3, overfit: choose the simplest architecture from the most related paper ("Don't be a hero"), use Adam with learning rate 3e-4, complexify one signal at a time, and do not trust learning-rate decay defaults. ([same](https://karpathy.github.io/2019/04/25/recipe/))
- Stage 4, regularize: more real data first, then augmentation, pretraining, smaller input dimensionality, smaller model, smaller batch, dropout, weight decay, early stopping, and possibly a larger model. Stage 5, tune: random search over grid search. Stage 6, squeeze: ensembles and leave it training longer than feels comfortable. ([same](https://karpathy.github.io/2019/04/25/recipe/))
- The theory: build from simple to complex, make a concrete hypothesis at every step, and validate it before adding more complexity; unverified complexity is the main source of hard-to-find bugs. ([same](https://karpathy.github.io/2019/04/25/recipe/))

**Sources.** [A Recipe for Training Neural Networks](https://karpathy.github.io/2019/04/25/recipe/).

## Agent-native infrastructure: build for the agent, not just the human

**What.** Treat agents as a new class of user and build docs, APIs, CLIs and permissions for them.

**How exactly.**
- Publish `llms.txt` (a Markdown file telling LLMs what a domain is about), serve documentation as Markdown, and replace "click here" instructions with the equivalent programmatic action an agent can execute. Karpathy cites Vercel and Stripe as early examples of LLM-friendly docs. ([YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d))
- Provide repo context in an agent-legible form: `github1s.com` concatenates files into one text file; Cognition's Devin builds a context package for a repo; DeepWiki answers questions about a repo. ([same](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d); [nanochat README](https://github.com/karpathy/nanochat))
- In the Sequoia talk he generalizes this to sensors and actuators: "A sensor turns some state of the world into digital information. An actuator lets an agent change something." The agent-native stack includes Markdown docs, CLIs, APIs, MCP servers, structured logs, machine-readable schemas, copy-pasteable instructions, safe permissions, auditable actions and headless setup flows. ([Sequoia summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The MenuGen deployment is the benchmark: building the app was easy compared with wiring Vercel, auth, payments, DNS, secrets and production settings. In a mature agent-native world, "build MenuGen" should deploy the whole thing without manual clicking. ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The theory: when the primary user is an agent acting for a human, machine-readable information and executable actions beat human-only screens. ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/))

**Sources.** [YC transcript](https://gist.github.com/nate-selzer/842514625486c2ee816ea0edb429629d), [Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/).

## LLM knowledge bases

**What.** Use an agent to compile raw sources into a persistent Markdown wiki instead of retrieving raw documents for every question.

**How exactly.**
- The agent incrementally builds summaries, entity pages, concept pages, contradictions, cross-links, logs and an evolving synthesis. The human asks questions against the wiki. This is not RAG over raw documents each time; the knowledge is compiled once and maintained. ([Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/); [LLM knowledge bases gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f))
- Karpathy's framing: "Instead of using retrieval-augmented generation to answer questions from raw documents each time, an agent incrementally compiles raw sources into a persistent Markdown wiki." The point is a tool for understanding, not just answer retrieval. ([Sequoia summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/))
- The theory: understanding is the bottleneck; a compiled, inspectable knowledge base is a better substrate for human understanding than a fresh retrieval on every question. ([same](https://karpathy.bearblog.dev/sequoia-ascent-2026/))

**Sources.** [Sequoia Ascent 2026 summary](https://karpathy.bearblog.dev/sequoia-ascent-2026/), [LLM knowledge bases gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), [2025 LLM Year in Review](https://karpathy.bearblog.dev/year-in-review-2025/).

## Public code to reuse

- [karpathy/nanochat](https://github.com/karpathy/nanochat): minimal full-stack ChatGPT training and inference. Start with `runs/speedrun.sh`, `scripts/chat_cli.py`, `nanochat/gpt.py`, `nanochat/tokenizer.py`, `nanochat/engine.py`, `nanochat/execution.py`, `scripts/base_train.py`, `scripts/chat_sft.py`, `scripts/chat_rl.py` and `tasks/`.
- [karpathy/autoresearch](https://github.com/karpathy/autoresearch): the autonomous research loop. Read `program.md`, `train.py`, `prepare.py` and the `results.tsv` schema.
- [karpathy/nanoGPT](https://github.com/karpathy/nanoGPT): the small GPT training loop. `train.py`, `model.py`, `config/train_shakespeare_char.py`, `config/train_gpt2.py`, `data/shakespeare_char/prepare.py`, `sample.py`.
- [karpathy/llm.c](https://github.com/karpathy/llm.c): GPT-2/3 training in C/CUDA. `train_gpt2.c`, `train_gpt2.cu`, `test_gpt2.c`, `dev/cuda`, `scripts/README.md`.
- [karpathy/micrograd](https://github.com/karpathy/micrograd): the tiny autograd engine. `micrograd/engine.py`, `micrograd/nn.py`, `demo.ipynb`, and the PyTorch-reference tests.
- [microGPT gist](https://gist.github.com/karpathy/8627fe009c40f57531cb18360106ce95): a complete GPT-2-like transformer in one dependency-free Python file.
- [karpathy/llm-council](https://github.com/karpathy/llm-council), [karpathy/reader3](https://github.com/karpathy/reader3), [karpathy/hn-time-capsule](https://github.com/karpathy/hn-time-capsule): vibe-coded applications Karpathy cites as examples of code that is cheap enough to be ephemeral.
