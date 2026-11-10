# shellcheck shell=bash
# lib/progress.sh -- the deploy's live status panel (sourced by deploy-soc-lab.sh).
#
# On a terminal it redraws a panel in place: a total bar, the current stage,
# one bar per VM (step k/n and what it is doing) and one for the deploy's own
# tasks. When stdout is not a terminal (a log file, a pipe) it prints a plain
# timestamped line whenever something changes instead.
#
# Every message the deploy prints goes through `say`, so it lands above the
# panel instead of tearing it. VMs report their step through /run/lab-progress
# (written by lab_step in each provision.sh); progress_poll reads it.
# NO_PROGRESS=1 forces the plain mode on a terminal too.

declare -A PG_STATE=()   # queued | launching | provisioning | done | failed
declare -A PG_STEP=()    # current step number on the VM
declare -A PG_STEPS=()   # total steps (counted from the template at render time)
declare -A PG_DESC=()    # what the VM is doing now
declare -A PG_LAST=()    # last line printed per VM (plain mode only)
PG_VMS=()                # display order
PG_STAGE=""
PG_TASK=0 PG_TASKS=0 PG_TASK_DESC=""
PG_LINES=0               # height of the panel currently on screen
PG_START=${SECONDS}
PG_TTY=false
if [ -t 1 ] && [ "${TERM:-dumb}" != "dumb" ] && [ -z "${NO_PROGRESS:-}" ]; then PG_TTY=true; fi

# pg_count_steps <rendered template>: how many lab_step lines will run. A step inside an
# `if [ "${FLAG}" = "1" ]` or `if [ -n "${FLAG}" ]` block is skipped when the template sets that flag
# (FLAG="value", filled in at render time) so the block can't run; `else` flips it. Steps behind any
# other condition, or whose flag is still a __PLACEHOLDER__, are counted.
pg_count_steps() {
    awk '
        function active(   i) { for (i = 1; i <= depth; i++) if (on[i] == 0) return 0; return 1 }
        { line = $0; sub(/^[ \t]+/, "", line); indent = length($0) - length(line) }
        line ~ /^[A-Z_][A-Z0-9_]*="[^"$]*"[ \t]*(#.*)?$/ {
            name = line; sub(/=.*/, "", name)
            val = line; sub(/^[^"]*"/, "", val); sub(/".*/, "", val)
            if (val !~ /^__[A-Z0-9_]+__$/) { flag[name] = val; known[name] = 1 }   # __X__: not rendered yet
        }
        line ~ /^if .*; then$/ {
            depth++; ind[depth] = indent
            if (match(line, /^if \[ "\$\{[A-Z_][A-Z0-9_]*\}" = "1" \]; then$/)) {
                n = line; sub(/^if \[ "\$\{/, "", n); sub(/\}.*/, "", n)
                on[depth] = (n in known) ? (flag[n] == "1") : 2
            } else if (match(line, /^if \[ -n "\$\{[A-Z_][A-Z0-9_]*\}" \]; then$/)) {
                n = line; sub(/^if \[ -n "\$\{/, "", n); sub(/\}.*/, "", n)
                on[depth] = (n in known) ? (flag[n] != "") : 2
            } else on[depth] = 2          # unknown condition or flag (2): counted either way
            next
        }
        depth > 0 && indent == ind[depth] && line == "else" { if (on[depth] != 2) on[depth] = !on[depth]; next }
        depth > 0 && indent == ind[depth] && line ~ /^fi($|[ ;])/ { depth--; next }
        line ~ /^lab_step / && active() { steps++ }
        END { print steps + 0 }
    ' "$1"
}

progress_add_vm() {      # progress_add_vm <name> <template>: register a VM (queued)
    local name="$1" steps
    steps=$(pg_count_steps "$2")
    PG_VMS+=("${name}")
    PG_STATE[${name}]=queued
    PG_STEP[${name}]=0
    PG_STEPS[${name}]=$((steps > 0 ? steps : 1))
    PG_DESC[${name}]=""
}

progress_recount() {     # progress_recount <name> <rendered file>: the step total with the real flags
    local steps
    [ -n "${PG_STATE[$1]+x}" ] || return 0
    steps=$(pg_count_steps "$2")
    PG_STEPS[$1]=$((steps > 0 ? steps : 1))
}

progress_vm() {          # progress_vm <name> <state> [step] [description]
    local name="$1"
    if [ -z "${PG_STATE[${name}]+x}" ]; then progress_add_vm "${name}" /dev/null; fi
    PG_STATE[${name}]="$2"
    if [ -n "${3:-}" ]; then              # never past the template's own count
        PG_STEP[${name}]=$(($3 < PG_STEPS[${name}] ? $3 : PG_STEPS[${name}]))
    fi
    if [ "$#" -ge 4 ]; then PG_DESC[${name}]="$4"; fi   # "" clears it
    if [ "$2" = "done" ]; then PG_STEP[${name}]=${PG_STEPS[${name}]}; fi
    progress_draw
}

progress_stage() { PG_STAGE="$1"; progress_draw; }
progress_tasks()      { PG_TASKS="$1"; }                  # how many deploy-side tasks to expect
progress_task_start() { PG_TASK_DESC="$1"; progress_draw; }
progress_task_done() {
    PG_TASK=$((PG_TASK + 1))
    PG_TASK_DESC=""
    if [ "${PG_TASK}" -ge "${PG_TASKS}" ]; then PG_TASK_DESC="done"; fi
    progress_draw
}

progress_finish() {      # final frame; later output prints below it as normal text
    PG_STAGE="$1"
    progress_draw
    if ! ${PG_TTY}; then printf '%s %s\n' "$(date +%H:%M:%S)" "${PG_STAGE}"; fi   # plain mode shows no stages
    PG_LINES=0
    PG_TTY=false
    PG_VMS=()            # plain mode has nothing left to report
    PG_TASK=0
}

progress_poll() {        # progress_poll <name>: read the VM's step file (one multipass call)
    local name="$1" line
    line=$(run_with_timeout 30 multipass exec "${name}" -- cat /run/lab-progress 2>&1) || return 0
    if [[ "${line}" =~ ^([0-9]+)\ (.*)$ ]]; then
        if [ "${BASH_REMATCH[1]}" != "${PG_STEP[${name}]}" ]; then
            progress_vm "${name}" provisioning "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
        fi
    fi
}

progress_launch_detail() {   # what `multipass launch` is doing, from its log (no multipass call)
    local log="$1" last
    [ -f "${log}" ] || return 0
    last=$(tr '\r' '\n' < "${log}" | grep -v '^[[:space:]]*$' | tail -n 1 | sed 's/^[[:space:]]*[|/\\-][[:space:]]*//')
    printf '%s' "${last:0:40}"
}

# --- drawing ---------------------------------------------------------------
pg_bar() {               # pg_bar <done> <total> <width>
    local done="$1" total="$2" width="$3" fill i out=""
    [ "${total}" -gt 0 ] || total=1
    fill=$((done * width / total))
    for ((i = 0; i < width; i++)); do
        if [ "${i}" -lt "${fill}" ]; then out+="█"; else out+="░"; fi
    done
    printf '%s' "${out}"
}

pg_vm_text() {           # one VM's status, without the bar
    local name="$1"
    case "${PG_STATE[${name}]}" in
        queued)       printf 'queued' ;;
        launching)    printf 'launching... %s' "${PG_DESC[${name}]}" ;;
        provisioning) printf '%s/%s %s' "${PG_STEP[${name}]}" "${PG_STEPS[${name}]}" "${PG_DESC[${name}]:-booting, updating packages}" ;;
        "done")       printf 'done' ;;
        failed)       printf 'FAILED %s' "${PG_DESC[${name}]}" ;;
    esac
}

progress_percent() {     # overall progress, 0-100: every step counts the same
    local name done=${PG_TASK} total=${PG_TASKS}
    for name in "${PG_VMS[@]}"; do
        total=$((total + PG_STEPS[${name}]))
        case "${PG_STATE[${name}]}" in
            done | failed) done=$((done + PG_STEPS[${name}])) ;;   # a failed VM is finished too
            *)             done=$((done + PG_STEP[${name}])) ;;
        esac
    done
    echo $((total > 0 ? done * 100 / total : 0))
}

pg_elapsed() { local s=$((SECONDS - PG_START)); printf '%dm%02ds' $((s / 60)) $((s % 60)); }

progress_clear() {       # erase the panel (terminal mode)
    if ${PG_TTY} && [ "${PG_LINES}" -gt 0 ]; then
        printf '\e[%dA\e[J' "${PG_LINES}"
        PG_LINES=0
    fi
}

progress_draw() {
    local pct name text
    pct=$(progress_percent)
    if ! ${PG_TTY}; then             # plain mode: one line per change
        for name in "${PG_VMS[@]}"; do
            text=$(pg_vm_text "${name}")
            # a launch's download percentage changes every second: log it once
            if [ "${PG_STATE[${name}]}" = launching ]; then text="launching"; fi
            if [ "${text}" != "${PG_LAST[${name}]:-}" ] && [ "${PG_STATE[${name}]}" != queued ]; then
                printf '%s [%3d%%] %-14s %s\n' "$(date +%H:%M:%S)" "${pct}" "${name}" "${text}"
                PG_LAST[${name}]="${text}"
            fi
        done
        if [ -n "${PG_TASK_DESC}" ] && [ "${PG_TASK_DESC}" != "${PG_LAST[__tasks]:-}" ]; then   # once per task
            printf '%s [%3d%%] %-14s %s/%s %s\n' "$(date +%H:%M:%S)" "${pct}" "deploy tasks" \
                "${PG_TASK}" "${PG_TASKS}" "${PG_TASK_DESC}"
            PG_LAST[__tasks]="${PG_TASK_DESC}"
        fi
        return 0
    fi
    progress_clear
    local cols=${COLUMNS:-$(tput cols 2>/dev/null || echo 100)} lines=0 vbar
    {
        printf '\e[1mSOC-lab deploy\e[0m  [%s] %3d%%  %s\n' "$(pg_bar "${pct}" 100 30)" "${pct}" "$(pg_elapsed)"
        printf '%s\n\n' "${PG_STAGE}"
        lines=3
        for name in "${PG_VMS[@]}"; do
            vbar=$(pg_bar "${PG_STEP[${name}]}" "${PG_STEPS[${name}]}" 12)
            text=$(pg_vm_text "${name}")
            case "${PG_STATE[${name}]}" in
                "done") printf '  %-13s \e[32m[%s]\e[0m %s\n' "${name}" "${vbar}" "${text}" ;;
                failed) printf '  %-13s \e[31m[%s]\e[0m %s\n' "${name}" "${vbar}" "${text:0:$((cols - 32))}" ;;
                *)      printf '  %-13s [%s] %s\n' "${name}" "${vbar}" "${text:0:$((cols - 32))}" ;;
            esac
            lines=$((lines + 1))
        done
        if [ "${PG_TASKS}" -gt 0 ]; then
            printf '  %-13s [%s] %s/%s %s\n' "deploy tasks" "$(pg_bar "${PG_TASK}" "${PG_TASKS}" 12)" \
                "${PG_TASK}" "${PG_TASKS}" "${PG_TASK_DESC:-waiting}"
            lines=$((lines + 1))
        fi
    }
    PG_LINES=${lines}
}

say() {                  # print a message above the panel
    progress_clear
    printf '%s\n' "$*"
    if ${PG_TTY}; then progress_draw; fi
}
