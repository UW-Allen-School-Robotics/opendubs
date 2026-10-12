#!/usr/bin/env bash
# The on-device LLM benchmark for the NON-ROBOT Thor: each candidate model, served one at a time in
# a container, timed by llm_bench.py, with tegrastats running alongside.
#
#   VLLM_IMAGE=<image@sha256:...> ./tools/voice-bench/thor_llm_bench.sh vllm
#   LLAMACPP_IMAGE=<image@sha256:...> GGUF_DIR=/data/gguf ./tools/voice-bench/thor_llm_bench.sh llamacpp
#
# NOT YET RUN ON A THOR. Written 2026-10-12 for the voice study (notes/design/voice-llm-research.md,
# Q1, "Benchmark plan"); syntax-checked only. Expect to fix things on first contact, and record what
# you fixed in the study.
#
# Rules it keeps:
#   * Never on a robot. opendubs2 is never the test bed (notes/workplan-thor-stack.md); this refuses
#     to run where /etc/opendubs/robot.env exists.
#   * Images are named by the caller, pinned. Nothing defaults to :latest (software/stack/versions.env
#     has the same rule for builds). A tag without a digest is accepted with a warning, and the digest
#     it resolved to is recorded.
#   * A busy port is refused, never reused: it may be another session's server (CLAUDE.md, lane rule 6).
#   * Results and model downloads go OUTSIDE the repository (CLAUDE.md, "Downloads never land in the
#     working tree").
#   * The LLM gets a fixed slice of the unified memory (VOICE_LLM_MEM_FRACTION, default 0.20 of
#     ~122 GiB, about 24 GiB), not vLLM's default 0.9: on Thor the GPU's memory IS the system's
#     memory, and the rest of the stack has to fit beside it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
MODE="${1:-}"
PORT="${PORT:-8790}"
MEMFRAC="${VOICE_LLM_MEM_FRACTION:-0.20}"
RUNS="${RUNS:-20}"
OUT="${OUT:-$HOME/voice-bench-results/$(date +%Y%m%d-%H%M%S)}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
NAME="voicebench-$$"

die() { echo "thor_llm_bench: $*" >&2; exit 2; }

[ -n "$MODE" ] || die "usage: $0 vllm|llamacpp"
if [ -f /etc/opendubs/robot.env ] && [ "${NOT_A_ROBOT:-}" != "yes" ]; then
  die "/etc/opendubs/robot.env exists: this looks like a robot. Benchmarks run on the non-robot Thor only."
fi
case "$(cd "$(dirname "$OUT")" 2>/dev/null && pwd || echo "$OUT")" in
  "$REPO"|"$REPO"/*) die "OUT=$OUT is inside the repository; results go outside it" ;;
esac
command -v docker >/dev/null || die "docker not found"
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
  die "port $PORT is in use (another session's server?). Set PORT to a free one."
fi
mkdir -p "$OUT"

pinned() {   # $1 = image reference. Refuse empty; warn on a tag without a digest; record the digest.
  local img="$1"
  [ -n "$img" ] || die "no image given (set VLLM_IMAGE or LLAMACPP_IMAGE, pinned by digest)"
  case "$img" in *@sha256:*) ;; *) echo "WARNING: $img is not pinned by digest; recording what it resolves to" >&2 ;; esac
  docker pull -q "$img" >/dev/null
  docker image inspect --format '{{json .RepoDigests}}' "$img" >> "$OUT/images.txt"
}

record_platform() {
  {
    echo "date: $(date -Is)"; echo "host: $(hostname)"
    head -1 /etc/nv_tegra_release 2>/dev/null || echo "nv_tegra_release: missing"
    (nvpmodel -q 2>/dev/null || sudo -n nvpmodel -q 2>/dev/null || echo "nvpmodel: unreadable") | head -3
    free -g; docker --version
    dpkg -l 2>/dev/null | awk '/nvidia-container-toolkit /{print "container-toolkit:", $3}'
  } > "$OUT/platform.txt"
  cat "$OUT/platform.txt" >&2
}

start_tegrastats() {
  if command -v tegrastats >/dev/null; then
    tegrastats --interval 500 --logfile "$OUT/tegrastats.log" & TEGRA_PID=$!
  else
    echo "tegrastats not found: no memory/power trace" >&2; TEGRA_PID=""
  fi
}

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  [ -n "${TEGRA_PID:-}" ] && kill "$TEGRA_PID" 2>/dev/null || true
}
trap cleanup EXIT

wait_ready() {   # the OpenAI-compatible server answers /v1/models
  local t0=$SECONDS
  until curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; do
    if ! docker ps -q --filter "name=$NAME" | grep -q .; then docker logs --tail 50 "$NAME" >&2 || true; die "server exited"; fi
    [ $((SECONDS - t0)) -lt 1800 ] || die "server not ready after 30 min"
    sleep 5
  done
  echo "ready after $((SECONDS - t0)) s" >&2
  echo "$1 load_s=$((SECONDS - t0))" >> "$OUT/load-times.txt"
}

bench() {   # $1 = served model name, $2 = label, $3 = extra request JSON
  local snap req="${3:-}"; [ -n "$req" ] || req='{}'
  snap="$(free -m | awk '/Mem:/{print $3}')"
  echo "$2 used_mib_after_load=$snap" >> "$OUT/memory.txt"
  for hist in 0 2000; do
    python3 "$HERE/llm_bench.py" --url "http://127.0.0.1:$PORT/v1" --model "$1" --runs "$RUNS" \
      --label "$2 hist=$hist" --history-tokens "$hist" --extra "$req" \
      --out "$OUT/runs.jsonl" | tee -a "$OUT/summary.jsonl"
  done
}

drop_caches() {   # NGC's Thor note: a stopped vLLM can leave page cache that breaks the next start
  sync; sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || echo "(could not drop caches without sudo)" >&2
}

# ---- the candidates ------------------------------------------------------------------------------
# Hugging Face repo ids, served name, extra server args, extra request JSON. CHECK EACH ID EXISTS and
# its licence before running: the study names them, the hub is the source of truth.
VLLM_CASES=(
  "Qwen/Qwen3-4B-Instruct-2507|--max-model-len 8192|{}"
  "Qwen/Qwen3-8B-AWQ|--max-model-len 8192|{\"chat_template_kwargs\": {\"enable_thinking\": false}}"
  "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8|--max-model-len 8192|{}"
  "openai/gpt-oss-20b|--max-model-len 8192|{\"reasoning_effort\": \"low\"}"
)

run_vllm() {
  pinned "${VLLM_IMAGE:-}"
  for c in "${VLLM_CASES[@]}"; do
    IFS='|' read -r model args req <<<"$c"
    echo "=== vLLM: $model ===" >&2
    # shellcheck disable=SC2086
    docker run -d --name "$NAME" --runtime nvidia --network host --ipc=host \
      -v "$HF_CACHE:/root/.cache/huggingface" ${HF_TOKEN:+-e HF_TOKEN} \
      "$VLLM_IMAGE" vllm serve "$model" --port "$PORT" --host 127.0.0.1 \
      --gpu-memory-utilization "$MEMFRAC" $args >/dev/null
    wait_ready "$model"
    bench "$model" "vllm $model mem=$MEMFRAC" "$req"
    docker logs "$NAME" > "$OUT/server-$(echo "$model" | tr '/' '_').log" 2>&1 || true
    docker rm -f "$NAME" >/dev/null
    drop_caches
  done
}

run_llamacpp() {
  pinned "${LLAMACPP_IMAGE:-}"
  [ -d "${GGUF_DIR:-}" ] || die "GGUF_DIR is not a directory of .gguf files"
  for f in "$GGUF_DIR"/*.gguf; do
    local m; m="$(basename "$f" .gguf)"
    echo "=== llama.cpp: $m ===" >&2
    docker run -d --name "$NAME" --runtime nvidia --network host -v "$GGUF_DIR:/models:ro" \
      "$LLAMACPP_IMAGE" llama-server -m "/models/$m.gguf" --host 127.0.0.1 --port "$PORT" \
      -ngl 999 -c 8192 --alias "$m" >/dev/null
    wait_ready "$m"
    bench "$m" "llama.cpp $m" "{}"
    docker logs "$NAME" > "$OUT/server-$m.log" 2>&1 || true
    docker rm -f "$NAME" >/dev/null
  done
}

record_platform
start_tegrastats
case "$MODE" in
  vllm) run_vllm ;;
  llamacpp) run_llamacpp ;;
  *) die "unknown mode $MODE (vllm|llamacpp)" ;;
esac
echo "results in $OUT (summary.jsonl, runs.jsonl, tegrastats.log, platform.txt). Copy the tables into the study." >&2
