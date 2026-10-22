#!/bin/bash
# Record one RTK corrections test run on the vehicle (rtk_test/).
#
#   bash rtk_test/record_run.sh start --label <name> [--mode xrce|mavros]
#   bash rtk_test/record_run.sh corrections off|on   # gate the caster (sudo)
#   bash rtk_test/record_run.sh mark "agent restart 3"   # note an event
#   bash rtk_test/record_run.sh stop
#   bash rtk_test/record_run.sh status
#
# Run from the repo root on the Jetson, with sudo if docker needs it. The mode
# defaults to the one .env selects (LAUNCH_XRCE_AGENT / LAUNCH_MAVROS).
#
# The bag is recorded by a separate container from the same image
# ("rtk_recorder"), so it keeps recording when the main container restarts
# (agent restart tests) and never shares a process with the stack under test.
# It uses the discovery-server super client profile: plain clients only learn
# about topics they already use, so a recorder would never subscribe.
#
# Each run is a directory under logs/rtk_runs/:
#   bag/          rosbag2 (sqlite3); never open it while recording
#   meta/         run.json, git state, redacted .env and compose config,
#                 image and px4_msgs provenance, container journal
#   (add later)   the PX4 .ulg, px4_console.py snapshots, caster_tap.py output
#
# "corrections off" blocks the Jetson's traffic to the NTRIP caster with an
# iptables chain (RTK_TEST) of its own; "on" removes it. Identical for both
# bridges: use it to start corrections at a known time (time to RTK fixed)
# and to simulate a caster outage. "stop" does not touch it.
#
# Local mode (no Docker, e.g. SITL on a PC): add --local --namespace <ns>
# --out <dir>; ros2 must be sourced, and the bag is recorded directly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RECORDER_NAME="rtk_recorder"
CHAIN="RTK_TEST"

cmd="${1:-}"
shift || true

label="run"
mode=""
local_mode=false
namespace=""
out_dir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --label) label="$2"; shift 2 ;;
        --mode) mode="$2"; shift 2 ;;
        --local) local_mode=true; shift ;;
        --namespace) namespace="$2"; shift 2 ;;
        --out) out_dir="$2"; shift 2 ;;
        on|off) gate="$1"; shift ;;
        *) [[ "${cmd}" == "mark" ]] && { note="$*"; break; }
           echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

env_get() {
    # Value of KEY in .env, without quotes; empty if absent
    local key="$1"
    [[ -r "${REPO_DIR}/.env" ]] || return 0
    sed -n "s/^${key}=//p" "${REPO_DIR}/.env" | tail -1 | sed 's/^["'\'']//; s/["'\'']$//'
}

out_dir="${out_dir:-${REPO_DIR}/logs/rtk_runs}"
current_file="${out_dir}/.current_run"

utc_now() { date -u +%Y-%m-%dT%H:%M:%S.%NZ; }

topics_for() {
    local m="$1" ns="$2" tgt="$3"
    if [[ "$m" == "xrce" ]]; then
        # Versioned PX4 messages get a _v<N> suffix on DDS (vehicle_status_v4
        # on the PR branch); the number changes between PX4 versions
        echo "/${ns}/rtcm_bridge/rtcm /${ns}/rtcm_bridge/fix /${ns}/fmu/in/rtcm_corrections" \
             "/${ns}/fmu/out/vehicle_gnss /${ns}/fmu/out/timesync_status" \
             "/${ns}/fmu/out/vehicle_status(_v[0-9]+)? /rosout"
    else
        # /uas<N>/mavlink_sink: what MAVROS sends the FCU, incl. GPS_RTCM_DATA
        echo "/${ns}/mavros/gps_rtk/send_rtcm /${ns}/mavros/global_position/raw/fix" \
             "/${ns}/mavros/gpsstatus/gps1/raw /${ns}/mavros/gpsstatus/gps2/raw" \
             "/${ns}/mavros/timesync_status /${ns}/mavros/state /uas${tgt}/mavlink_sink /rosout"
    fi
}

write_meta() {
    local run="$1" m="$2" ns="$3" tgt="$4" container="$5"
    local meta="${run}/meta"
    mkdir -p "${meta}"
    {
        git -C "${REPO_DIR}" rev-parse HEAD
        git -C "${REPO_DIR}" status --short
        git -C "${REPO_DIR}" diff
    } > "${meta}/git.txt" 2>&1 || true
    if [[ -r "${REPO_DIR}/.env" ]]; then
        sed -E 's/^(NTRIP_PASSWORD|NTRIP_USERNAME)=.*/\1=<redacted>/' "${REPO_DIR}/.env" > "${meta}/env.txt"
    fi
    if [[ "${local_mode}" == false ]]; then
        (cd "${REPO_DIR}" && docker compose config 2>&1) \
            | sed -E 's/(NTRIP_PASSWORD|ntrip_password|NTRIP_USERNAME|ntrip_username)(:=|: |=)[^ "]*/\1\2<redacted>/g' \
            > "${meta}/compose_config.txt" || true
        docker inspect "${container}" > "${meta}/container_inspect.json" 2>&1 || true
        docker image inspect "$(docker inspect -f '{{.Image}}' "${container}" 2>/dev/null)" \
            > "${meta}/image_inspect.json" 2>&1 || true
        docker exec "${container}" bash -c 'cat /opt/ros/${ROS_DISTRO}/share/px4_msgs/build_info 2>&1' \
            > "${meta}/px4_msgs_build_info.txt" 2>&1 || true
    fi
    (chronyc tracking 2>&1 || timedatectl 2>&1) > "${meta}/clock.txt" || true
    cat > "${meta}/run.json" <<JSON
{
  "label": "${label}",
  "mode": "${m}",
  "namespace": "${ns}",
  "tgt_system": "${tgt}",
  "local": ${local_mode},
  "container": "${container}",
  "hostname": "$(hostname)",
  "start_utc": "$(utc_now)",
  "start_epoch_ns": $(date +%s%N),
  "topics": "$(topics_for "${m}" "${ns}" "${tgt}")"
}
JSON
}

start_run() {
    mkdir -p "${out_dir}"
    if [[ -f "${current_file}" ]]; then
        echo "A run is already recording: $(cat "${current_file}"). Stop it first." >&2
        exit 1
    fi
    local container ns tgt
    container="$(env_get CONTAINER_NAME)"
    container="${container:-autonomypark}"
    ns="${namespace:-$(env_get MAVROS_NAMESPACE)}"
    tgt="$(env_get MAVROS_TGT_SYSTEM)"
    if [[ -z "${ns}" ]]; then
        echo "No namespace: set MAVROS_NAMESPACE in .env or pass --namespace" >&2
        exit 2
    fi
    if [[ -z "${mode}" ]]; then
        if [[ "$(env_get LAUNCH_XRCE_AGENT)" == "true" ]]; then mode="xrce"; else mode="mavros"; fi
    fi
    if [[ "${mode}" != "xrce" && "${mode}" != "mavros" ]]; then
        echo "--mode must be xrce or mavros, got ${mode}" >&2
        exit 2
    fi
    local run_name run
    run_name="$(date -u +%Y%m%dT%H%M%SZ)_${mode}_${label}"
    run="${out_dir}/${run_name}"
    mkdir -p "${run}"
    write_meta "${run}" "${mode}" "${ns}" "${tgt}" "${container}"
    # Topic names as regular expressions, recorded with one anchored --regex
    # shellcheck disable=SC2207
    local topics=($(topics_for "${mode}" "${ns}" "${tgt}"))
    local regex
    regex="^($(IFS='|'; echo "${topics[*]}"))\$"

    if [[ "${local_mode}" == true ]]; then
        setsid ros2 bag record -s sqlite3 --polling-interval 100 -o "${run}/bag" -e "${regex}" \
            > "${run}/meta/recorder.log" 2>&1 < /dev/null &
        echo $! > "${run}/meta/recorder.pid"
    else
        # The compose service's image, volumes, network and entrypoint (which
        # waits for the clock), with the run directory mounted
        (cd "${REPO_DIR}" && docker compose run -d --rm --no-deps --name "${RECORDER_NAME}" \
            -e FASTRTPS_PROFILE_PATH=/etc/fastdds/super_client_config.xml \
            -v "${out_dir}:/rtk_runs" \
            homebrew_bringup \
            ros2 bag record -s sqlite3 --polling-interval 100 -o "/rtk_runs/${run_name}/bag" -e "${regex}") \
            > "${run}/meta/recorder_start.txt" 2>&1
    fi
    echo "${run}" > "${current_file}"

    # Confirm it subscribed to every topic (the publishers must exist)
    local deadline=$((SECONDS + 60)) missing
    while :; do
        local log
        if [[ "${local_mode}" == true ]]; then log="$(cat "${run}/meta/recorder.log")"
        else log="$(docker logs "${RECORDER_NAME}" 2>&1 || true)"; fi
        missing=()
        for t in "${topics[@]}"; do
            grep -q -E "Subscribed to topic '${t}'" <<< "${log}" || missing+=("${t}")
        done
        [[ ${#missing[@]} -eq 0 ]] && break
        if (( SECONDS > deadline )); then
            echo "WARNING: after 60 s, not subscribed to: ${missing[*]}" | tee -a "${run}/meta/warnings.txt"
            break
        fi
        sleep 2
    done
    echo "Recording ${mode} run: ${run}"
}

stop_run() {
    if [[ ! -f "${current_file}" ]]; then
        echo "No run is recording." >&2
        exit 1
    fi
    local run
    run="$(cat "${current_file}")"
    if [[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["local"])' "${run}/meta/run.json")" == "True" ]]; then
        local pid
        pid="$(cat "${run}/meta/recorder.pid")"
        kill -INT -- "-${pid}" 2>/dev/null || kill -INT "${pid}" 2>/dev/null || true
        for _ in $(seq 60); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.5; done
    else
        docker logs "${RECORDER_NAME}" > "${run}/meta/recorder.log" 2>&1 || true
        docker kill --signal=INT "${RECORDER_NAME}" > /dev/null 2>&1 || true
        timeout 60 docker wait "${RECORDER_NAME}" > /dev/null 2>&1 || true
    fi
    local stop_utc
    stop_utc="$(utc_now)"
    python3 - "${run}/meta/run.json" "${stop_utc}" "$(date +%s%N)" <<'PY'
import json, sys
p, utc, ns = sys.argv[1], sys.argv[2], int(sys.argv[3])
d = json.load(open(p)); d["stop_utc"] = utc; d["stop_epoch_ns"] = ns
json.dump(d, open(p, "w"), indent=2)
PY
    local container start_utc
    container="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container"])' "${run}/meta/run.json")"
    start_utc="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["start_utc"])' "${run}/meta/run.json")"
    # The stack's output for the run (bridge, agent, ntrip_client, MAVROS)
    journalctl CONTAINER_NAME="${container}" --since "${start_utc%.*}" --until "${stop_utc%.*}" \
        -o short-iso-precise > "${run}/meta/container_journal.txt" 2>&1 || true
    iptables -S "${CHAIN}" > "${run}/meta/iptables_at_stop.txt" 2>&1 || true
    rm -f "${current_file}"
    if [[ -f "${run}/bag/metadata.yaml" ]]; then
        echo "Stopped: ${run}"
    else
        echo "WARNING: ${run}/bag/metadata.yaml missing; the recorder did not stop cleanly." \
             "Repair with: ros2 bag reindex -s sqlite3 ${run}/bag" | tee -a "${run}/meta/warnings.txt"
    fi
    echo "Next: copy the PX4 .ulg covering this run into ${run}/ and run rtk_test/analyze_run.py"
}

status_run() {
    if [[ -f "${current_file}" ]]; then
        local run
        run="$(cat "${current_file}")"
        echo "Recording: ${run} ($(du -sh "${run}/bag" 2>/dev/null | cut -f1))"
    else
        echo "Not recording."
    fi
    if iptables -S "${CHAIN}" > /dev/null 2>&1; then
        echo "Corrections: OFF (iptables chain ${CHAIN} present)"
    else
        echo "Corrections: on"
    fi
}

gate_corrections() {
    local host port
    host="$(env_get NTRIP_HOST)"
    port="$(env_get NTRIP_PORT)"
    port="${port:-2101}"
    if [[ -z "${host}" ]]; then
        echo "NTRIP_HOST not set in .env" >&2
        exit 2
    fi
    if [[ "${gate:-}" == "off" ]]; then
        if ! iptables -S "${CHAIN}" > /dev/null 2>&1; then
            iptables -N "${CHAIN}"
            iptables -A "${CHAIN}" -p tcp -d "${host}" --dport "${port}" -j REJECT --reject-with tcp-reset
            iptables -A "${CHAIN}" -p tcp -s "${host}" --sport "${port}" -j DROP
            iptables -I OUTPUT -j "${CHAIN}"
            iptables -I INPUT -j "${CHAIN}"
        fi
        echo "Corrections OFF: ${host}:${port} blocked"
    elif [[ "${gate:-}" == "on" ]]; then
        while iptables -D OUTPUT -j "${CHAIN}" 2> /dev/null; do :; done
        while iptables -D INPUT -j "${CHAIN}" 2> /dev/null; do :; done
        iptables -F "${CHAIN}" 2> /dev/null || true
        iptables -X "${CHAIN}" 2> /dev/null || true
        echo "Corrections ON: ${host}:${port} unblocked"
    else
        echo "Usage: $0 corrections off|on" >&2
        exit 2
    fi
    if [[ -f "${current_file}" ]]; then
        echo "$(utc_now) corrections ${gate}" >> "$(cat "${current_file}")/meta/events.txt"
    fi
}

mark_event() {
    if [[ ! -f "${current_file}" ]]; then
        echo "No run is recording." >&2
        exit 1
    fi
    echo "$(utc_now) mark ${note:-}" >> "$(cat "${current_file}")/meta/events.txt"
    echo "Marked: ${note:-}"
}

case "${cmd}" in
    start) start_run ;;
    mark) mark_event ;;
    stop) stop_run ;;
    status) status_run ;;
    corrections) gate_corrections ;;
    *)
        sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
esac
