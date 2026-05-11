#!/usr/bin/env bash
# Run the full quality gate (fail-fast), capture each step's output to a log,
# and print a detailed breakdown at the end — on success AND failure.
#
# Steps mirror Makefile `total-test` order: cheapest first.
#   1. ruff check
#   2. ruff format --check
#   3. mypy --strict src
#   4. grace lint --failOn errors
#   5. inv-lint   (scripts/lint_invariants.py)
#   6. test-cov   (pytest unit + coverage ≥80%)
#
# Integration suite (tests/integration) is intentionally NOT part of this
# pre-merge gate. It hits the real Claude CLI subprocess (paid quota,
# subscription auth, sandbox deps like socat/bubblewrap) and is therefore
# environment-sensitive and non-deterministic. Run separately via
# `make test-integration` per docs/runbook/operations.md §Integration testing
# (cadence: manual nightly + pre-cutover).

set -u
set -o pipefail

LOG_DIR=".total-test-logs"
mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR"/*.log

STEPS=()
STATUS=()
DURATION=()
SUMMARY=()

run_step() {
  local name="$1"; shift
  local log="$LOG_DIR/${name}.log"
  STEPS+=("$name")
  local t0 t1
  t0=$(date +%s)
  printf '\n▶ %s\n' "$name"
  if "$@" >"$log" 2>&1; then
    STATUS+=("PASS")
  else
    STATUS+=("FAIL")
  fi
  t1=$(date +%s)
  DURATION+=("$((t1 - t0))s")
  # tail to console so user sees something live
  tail -n 3 "$log" || true
}

step_ruff_check()        { uv run ruff check .; }
step_ruff_format_check() { uv run ruff format --check .; }
step_mypy()              { uv run mypy src; }
step_grace_lint()        { grace lint --failOn errors; }
step_inv_lint()          { uv run python scripts/lint_invariants.py; }
step_test_cov()          { uv run pytest tests/unit --cov=src/ai_steward_wiki --cov-report=term-missing --cov-fail-under=80; }

# --- key-metric extractors ---------------------------------------------------

# Extract a numeric token following a keyword on the pytest summary line,
# e.g. "397 passed, 2 skipped" → pytest_num "passed" → 397
pytest_num() {
  local log="$1" kw="$2"
  grep -Eo "[0-9]+ ${kw}" "$log" | tail -n1 | grep -Eo '[0-9]+' || echo 0
}

summary_ruff_check() {
  local log="$1" errs=0
  if grep -q "All checks passed" "$log"; then
    errs=0
  else
    errs=$(grep -Eo 'Found [0-9]+ error' "$log" | tail -n1 | grep -Eo '[0-9]+' || echo 0)
    [ -z "$errs" ] && errs=0
  fi
  echo "errors=${errs}"
}
summary_ruff_format() {
  local log="$1"
  local formatted reformat
  formatted=$(grep -Eo '[0-9]+ files? already formatted' "$log" | tail -n1 | grep -Eo '[0-9]+' || echo 0)
  reformat=$(grep -Eo '[0-9]+ files? would be reformatted' "$log" | tail -n1 | grep -Eo '[0-9]+' || echo 0)
  [ -z "$formatted" ] && formatted=0
  [ -z "$reformat" ] && reformat=0
  echo "formatted=${formatted} reformat=${reformat}"
}
summary_mypy() {
  local log="$1" files errs
  if grep -q "^Success" "$log"; then
    files=$(grep -Eo 'no issues found in [0-9]+ source files?' "$log" | grep -Eo '[0-9]+' | tail -n1)
    [ -z "$files" ] && files=0
    echo "files=${files} errors=0"
  else
    errs=$(grep -Eo 'Found [0-9]+ error' "$log" | tail -n1 | grep -Eo '[0-9]+')
    files=$(grep -Eo 'in [0-9]+ files? \(checked' "$log" | grep -Eo '[0-9]+' | tail -n1)
    [ -z "$errs" ] && errs=0
    [ -z "$files" ] && files=0
    echo "files=${files} errors=${errs}"
  fi
}
summary_grace() {
  local log="$1" governed xml errs warns
  governed=$(grep -Eo 'Governed files checked: [0-9]+' "$log" | grep -Eo '[0-9]+' | tail -n1)
  xml=$(grep -Eo 'XML files checked: [0-9]+' "$log" | grep -Eo '[0-9]+' | tail -n1)
  errs=$(grep -Eo 'errors: [0-9]+' "$log" | grep -Eo '[0-9]+' | tail -n1)
  warns=$(grep -Eo 'warnings: [0-9]+' "$log" | grep -Eo '[0-9]+' | tail -n1)
  [ -z "$governed" ] && governed=0
  [ -z "$xml" ] && xml=0
  [ -z "$errs" ] && errs=0
  [ -z "$warns" ] && warns=0
  echo "governed=${governed} xml=${xml} errors=${errs} warnings=${warns}"
}
summary_inv_lint() {
  local log="$1" total passed
  total=$(grep -Eo 'All [0-9]+ invariant checks passed' "$log" | grep -Eo '[0-9]+' | tail -n1)
  if [ -n "$total" ]; then
    echo "checks=${total} passed=${total} failed=0"
  else
    passed=$(grep -cE ':[[:space:]]*ok' "$log" || echo 0)
    local failed
    failed=$(grep -cE ':[[:space:]]*fail|violation' "$log" || echo 0)
    echo "passed=${passed} failed=${failed}"
  fi
}
summary_pytest() {
  local log="$1"
  local passed failed errors skipped total cov result
  passed=$(pytest_num "$log" passed)
  failed=$(pytest_num "$log" failed)
  errors=$(pytest_num "$log" error)
  skipped=$(pytest_num "$log" skipped)
  total=$((passed + failed + errors + skipped))
  result="passed=${passed} failed=${failed} skipped=${skipped} total=${total}"
  cov=$(grep -Eo 'Total coverage: [0-9.]+%' "$log" | grep -Eo '[0-9.]+%' | tail -n1)
  [ -z "$cov" ] && cov=$(grep -E '^TOTAL' "$log" | tail -n1 | grep -Eo '[0-9.]+%' | tail -n1)
  [ -n "$cov" ] && result="${result} | coverage=${cov}"
  echo "$result"
}
# --- run pipeline (fail-fast) -----------------------------------------------

OVERALL=0
run_one() {
  local name="$1"; shift
  local fn="$1"; shift
  local sum_fn="$1"; shift
  run_step "$name" "$fn"
  local idx=$((${#STEPS[@]} - 1))
  SUMMARY+=("$($sum_fn "$LOG_DIR/${name}.log" | tr -d '\r')")
  if [ "${STATUS[$idx]}" = "FAIL" ]; then OVERALL=1; fi
}

run_one ruff-check        step_ruff_check        summary_ruff_check        && [ $OVERALL -eq 0 ] || true
[ $OVERALL -eq 0 ] && run_one ruff-format-check step_ruff_format_check summary_ruff_format
[ $OVERALL -eq 0 ] && run_one mypy              step_mypy              summary_mypy
[ $OVERALL -eq 0 ] && run_one grace-lint        step_grace_lint        summary_grace
[ $OVERALL -eq 0 ] && run_one inv-lint          step_inv_lint          summary_inv_lint
[ $OVERALL -eq 0 ] && run_one test-cov          step_test_cov          summary_pytest

# --- final report -----------------------------------------------------------

printf '\n'
printf '════════════════════════════════════════════════════════════════════════\n'
printf '  TOTAL-TEST REPORT\n'
printf '════════════════════════════════════════════════════════════════════════\n'
printf '  %-20s %-6s %-8s %s\n' "STEP" "STATUS" "TIME" "KEY METRIC"
printf '  %-20s %-6s %-8s %s\n' "--------------------" "------" "--------" "----------------------------------------"
for i in "${!STEPS[@]}"; do
  printf '  %-20s %-6s %-8s %s\n' "${STEPS[$i]}" "${STATUS[$i]}" "${DURATION[$i]}" "${SUMMARY[$i]}"
done
printf '════════════════════════════════════════════════════════════════════════\n'
if [ $OVERALL -eq 0 ]; then
  printf '  ✓ ALL STEPS PASSED\n'
else
  # find first failure
  for i in "${!STEPS[@]}"; do
    if [ "${STATUS[$i]}" = "FAIL" ]; then
      printf '  ✗ FAILED at: %s   (full log: %s/%s.log)\n' "${STEPS[$i]}" "$LOG_DIR" "${STEPS[$i]}"
      printf '\n  --- last 20 lines of %s.log ---\n' "${STEPS[$i]}"
      tail -n 20 "$LOG_DIR/${STEPS[$i]}.log" | sed 's/^/  /'
      break
    fi
  done
fi
printf '════════════════════════════════════════════════════════════════════════\n'

exit $OVERALL
