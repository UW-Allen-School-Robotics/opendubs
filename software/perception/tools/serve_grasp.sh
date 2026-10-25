#!/usr/bin/env bash
# Inside the server container (tools/grasp_stack.sh): the perception server and the tasks node,
# both under /perception on the container's ROS domain. One process group: if either exits, the
# other is stopped and the container exits, so whoever supervises it (Docker or systemd) restarts
# both. The logs go to stdout and to /data/results/{server,tasks}.log.
set -uo pipefail
NS=(--ros-args -r __ns:=/perception)
CONFIG=/work/software/perception/config/perception_manip.yaml
TASKS_CONFIG=/work/software/perception/config/perception_tasks.yaml

python3 -u -m perception_server.server --config "${CONFIG}" "${NS[@]}" 2>&1 | tee /data/results/server.log &
python3 -u -m perception_tasks.node --config "${TASKS_CONFIG}" "${NS[@]}" 2>&1 | tee /data/results/tasks.log &
# docker stop sends TERM to this script (PID 1): pass it to the two pipelines, once.
trap 'trap - EXIT TERM INT; pkill -TERM -P $$ 2>/dev/null' EXIT TERM INT
wait -n
echo "serve_grasp: a process exited; stopping the other" >&2
exit 1
