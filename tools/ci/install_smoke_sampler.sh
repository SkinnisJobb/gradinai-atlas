#!/usr/bin/env bash
# install_smoke_sampler.sh OUT: sample memory and CPU of every container of the compose project
# `atlas` straight from its cgroup (v2), every ~0.5 s, until killed (install smoke, sizing).
#
# One line per container per sample: "<epoch>\t<service>\t<memory bytes>\t<cpu usage_usec>".
# Memory is memory.current minus inactive_file, the figure `docker stats` shows (page cache
# that can be dropped is not counted); CPU is the cumulative usage_usec of cpu.stat, which
# tools/ci/install_smoke_sizing.py turns into percent of one core between samples. Reading the
# cgroup is cheap enough to sample short legs too; `docker stats --no-stream` takes ~2 s a call.

#
# The container's scope is looked up under any slice: system.slice on GitHub's runners, ci.slice
# on the self-hosted one (its docker sets cgroup-parent, see the atlas-ci-runner runbook).

set -u
OUT=${1:?usage: install_smoke_sampler.sh OUT}
declare -A SVC CG
while :; do
    t=$(date +%s.%N)
    for id in $(docker ps -q --no-trunc --filter label=com.docker.compose.project=atlas 2>/dev/null); do
        [ -n "${SVC[$id]:-}" ] || SVC[$id]=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id" 2>/dev/null)
        [ -n "${CG[$id]:-}" ] || CG[$id]=$(ls -d /sys/fs/cgroup/*.slice/docker-$id.scope 2>/dev/null | head -n 1)
        cg=${CG[$id]}
        [ -r "$cg/memory.current" ] || continue
        cur=$(<"$cg/memory.current")
        inactive=$(awk '$1=="inactive_file"{print $2}' "$cg/memory.stat" 2>/dev/null)
        cpu=$(awk '$1=="usage_usec"{print $2}' "$cg/cpu.stat" 2>/dev/null)
        printf '%s\t%s\t%s\t%s\n' "$t" "${SVC[$id]}" "$((cur - ${inactive:-0}))" "${cpu:-0}"
    done
    sleep 0.5
done >> "$OUT"
