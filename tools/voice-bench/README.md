# Voice bench

Benchmarks and prototypes for the voice study,
[`notes/design/voice-llm-research.md`](../../notes/design/voice-llm-research.md). **None of them has
run on a Thor or on the tablet yet.** Each script's docstring says what it has been checked against.

| file | what | where it runs |
|---|---|---|
| `llm_bench.py` | time to first token, **time to first sentence** and decode rate against any OpenAI-compatible server (vLLM, llama.cpp, SGLang, Ollama). Standard library only | anywhere; first real use on the non-robot Thor |
| `thor_llm_bench.sh` | serves each candidate model in a container, one at a time, with a fixed memory share, and runs `llm_bench.py` with `tegrastats` running alongside. Refuses to run on a robot | **the non-robot Thor only** |
| `claude_bench.py` | the same voice turn against the Claude API (Haiku, Sonnet and Opus 5.5): latency and cost per turn | from the robot's uplink, with a key from the environment |
| `audio_lab.py` + `audio_lab.html` | the tablet ↔ Thor audio round trip: uplink frames, streamed downlink, and how much echo the tablet's canceller removes | the Thor (or a laptop) and the tablet |
| `safe_tools.py` | the allowlist: the only things a model may ask the robot to do (the face, a read-only status, and, if the operator enables it, a gated routine request) | imported by the benches; the future voice service |
| `test_voice_bench.py` | checks the above without a Thor, a model or a network | anywhere, `python3 tools/voice-bench/test_voice_bench.py` |

Results, recordings and model downloads go **outside the repository** (CLAUDE.md). Never run these
against a process or device another session started; each script refuses a busy port.
