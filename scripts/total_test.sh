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
#   7. integration (pytest tests/integration, if present)

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
step_integration() {
  if [ -d tests/integration ]; then
    RUN_INTEGRATION=1 uv run pytest tests/integration -v
  else
    echo "tests/integration not present — skipping"
  fi
}

# --- key-metric extractors ---------------------------------------------------

summary_ruff_check() {
  local log="$1"
  local found
  found=$(grep -Eo 'Found [0-9]+ error' "$log" | tail -n1 || true)
  if [ -n "$found" ]; then echo "$found"
  elif grep -q "All checks passed" "$log"; then echo "All checks passed"
  else tail -n1 "$log"; fi
}
summary_ruff_format() {
  local log="$1"
  local n
  n=$(grep -Eo '[0-9]+ files? would be reformatted' "$log" | tail -n1 || true)
  if [ -n "$n" ]; then echo "$n"
  elif grep -q "already formatted" "$log"; then
    grep -Eo '[0-9]+ files? already formatted' "$log" | tail -n1
  else tail -n1 "$log"; fi
}
summary_mypy() {
  local log="$1"
  grep -E "^(Success|Found [0-9]+ error)" "$log" | tail -n1 || tail -n1 "$log"
}
summary_grace() {
  local log="$1"
  # grace lint typically prints counts like "errors=N warnings=M"
  local last
  last=$(grep -iE 'error|warning|pass|ok' "$log" | tail -n1 || true)
  [ -n "$last" ] && echo "$last" || tail -n1 "$log"
}
summary_inv_lint() {
  local log="$1"
  local last
  last=$(grep -iE 'invariant|violation|pass|ok|✓|✗' "$log" | tail -n1 || true)
  [ -n "$last" ] && echo "$last" || tail -n1 "$log"
}
summary_pytest() {
  local log="$1"
  local line cov
  line=$(grep -Eo '[0-9]+ (passed|failed|error|skipped)[^=]*' "$log" | tail -n1 || true)
  cov=$(grep -Eo 'Total coverage: [0-9.]+%' "$log" | tail -n1 || true)
  [ -z "$cov" ] && cov=$(grep -Eo 'TOTAL[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+%' "$log" | tail -n1 || true)
  if [ -n "$line" ] && [ -n "$cov" ]; then echo "$line | $cov"
  elif [ -n "$line" ]; then echo "$line"
  else tail -n1 "$log"; fi
}
summary_integration() {
  local log="$1"
  if grep -q "skipping" "$log"; then echo "skipped (tests/integration absent)"; return; fi
  summary_pytest "$log"
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
[ $OVERALL -eq 0 ] && run_one integration       step_integration       summary_integration

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
