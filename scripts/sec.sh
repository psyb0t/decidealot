#!/bin/bash
set -euo pipefail

readonly log_file="${LOG_FILE:-/tmp/decidealot-sec.log}"
readonly sarif_out="${SARIF_OUT:-sec.sarif}"
readonly empty_sarif="{\"version\":\"2.1.0\",\"\$schema\":\"https://json.schemastore.org/sarif-2.1.0.json\",\"runs\":[]}"
readonly source_dir="src"
readonly report_uri="uv.lock"
work_dir="$(mktemp -d)"
readonly work_dir

log() {
	local level="$1"
	shift
	jq -nc \
		--arg time "$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')" \
		--arg level "$level" \
		--arg file "${BASH_SOURCE[1]##*/}" \
		--argjson line "${BASH_LINENO[0]}" \
		--arg func "${FUNCNAME[1]:-main}" \
		--arg msg "$*" \
		'{time:$time,level:$level,file:$file,line:$line,func:$func,msg:$msg}' >&2
}

cleanup() {
	rm -rf "$work_dir"
}

on_error() {
	local status=$?

	log ERROR "command failed exit=$status"
	exit "$status"
}

run_bandit() {
	local status

	set +e
	python -m bandit -q -r "$source_dir" -f json -o "$work_dir/bandit.json" >"$work_dir/bandit.log" 2>&1
	status=$?
	set -e

	if [[ "$status" -ne 0 && ! -s "$work_dir/bandit.json" ]]; then
		cat "$work_dir/bandit.log" >&2
		log ERROR "bandit did not produce a JSON report"
		return "$status"
	fi
}

run_pip_audit() {
	local status

	set +e
	python -m pip_audit --format json --output "$work_dir/pip-audit.json" >"$work_dir/pip-audit.log" 2>&1
	status=$?
	set -e

	if [[ "$status" -ne 0 && ! -s "$work_dir/pip-audit.json" ]]; then
		cat "$work_dir/pip-audit.log" >&2
		log ERROR "pip-audit did not produce a JSON report"
		return "$status"
	fi
}

trap cleanup EXIT
trap on_error ERR
exec > >(tee -a "$log_file") 2>&1

if [[ -n "${DEBUG:-}" ]]; then
	log DEBUG "running Python security scanners"
fi

run_bandit
run_pip_audit

if [[ -s "$work_dir/bandit.json" ]]; then
	jq '{
    version: "2.1.0",
    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    runs: [{
        tool: {driver: {name: "Bandit", rules: []}},
        results: [(.results // [])[]? | {
            ruleId: .test_id,
            level: ({HIGH: "error", MEDIUM: "warning", LOW: "note"}[.issue_severity] // "warning"),
            message: {text: .issue_text},
            locations: [{physicalLocation: {
                artifactLocation: {uri: .filename},
                region: {startLine: .line_number}
            }}]
        }]
    }]
}' "$work_dir/bandit.json" >"$work_dir/bandit.sarif"
fi

if [[ -s "$work_dir/pip-audit.json" ]]; then
	jq --arg report_uri "$report_uri" '{
    version: "2.1.0",
    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    runs: [{
        tool: {driver: {name: "pip-audit", rules: []}},
        results: [(.dependencies // [])[]? | .name as $name | .version as $version
            | (.vulns // [])[]? | {
                ruleId: .id,
                level: "warning",
                message: {text: ($name + " " + $version + ": " + (.description // .id))},
                locations: [{physicalLocation: {
                    artifactLocation: {uri: $report_uri},
                    region: {startLine: 1}
                }}]
            }]
    }]
}' "$work_dir/pip-audit.json" >"$work_dir/pip-audit.sarif"
fi

for scanner in bandit pip-audit; do
	[[ -s "$work_dir/$scanner.sarif" ]] || printf '%s' "$empty_sarif" >"$work_dir/$scanner.sarif"
done

jq -s '{
    version: "2.1.0",
    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    runs: (map(.runs // []) | add)
}' "$work_dir/bandit.sarif" "$work_dir/pip-audit.sarif" >"$sarif_out"

finding_count="$(jq '[.runs[].results[]?] | length' "$sarif_out")"
if [[ "$finding_count" -gt 0 ]]; then
	log WARN "security scan wrote findings=$finding_count path=$sarif_out"

	exit 0
fi

log INFO "security scan wrote findings=0 path=$sarif_out"
