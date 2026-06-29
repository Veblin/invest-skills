#!/usr/bin/env bash
# Configure GitHub branch protection for main via Repository Rulesets API.
# Requires: gh CLI authenticated with repo admin scope.
#
# Usage:
#   bash scripts/protect-main-branch.sh [owner/repo]
#
# If owner/repo is omitted, reads from `git remote get-url origin`.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RULESET_NAME="Protect main branch"

resolve_repo() {
  if [[ -n "${1:-}" ]]; then
    echo "$1"
    return
  fi
  gh repo view --json nameWithOwner -q .nameWithOwner
}

ensure_gh_auth() {
  # Invalid GITHUB_TOKEN overrides gh keyring login and breaks API calls.
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    if ! gh auth status -h github.com >/dev/null 2>&1; then
      echo "⚠️  GITHUB_TOKEN is set but invalid; unset it and use 'gh auth login' instead." >&2
      echo "    export -n GITHUB_TOKEN" >&2
      exit 1
    fi
  fi
  if ! gh auth status -h github.com >/dev/null 2>&1; then
    echo "❌ gh is not authenticated. Run:" >&2
    echo "   gh auth login -h github.com -p https -s repo,admin:repo" >&2
    exit 1
  fi
}

fetch_status_checks() {
  local repo="$1"
  local sha
  sha="$(gh api "repos/${repo}/commits/main" -q .sha 2>/dev/null || true)"
  if [[ -z "$sha" ]]; then
    echo ""
    return
  fi
  gh api "repos/${repo}/commits/${sha}/check-runs" \
    --paginate \
    -q '.check_runs[] | select(.status == "completed" and .conclusion == "success") | .name' \
    2>/dev/null | sort -u | paste -sd, - || true
}

pick_checks() {
  local detected="$1"
  local checks=""
  local preferred="tests Validate / tests secrets-scan Security Scan / secrets-scan"

  for name in $preferred; do
    if [[ ",${detected}," == *",${name},"* ]]; then
      checks="${checks}${checks:+|}${name}"
    fi
  done

  if [[ -z "$checks" && -n "$detected" ]]; then
    checks="$(printf '%s' "$detected" | tr ',' '|')"
  fi

  if [[ -z "$checks" ]]; then
    checks="tests|secrets-scan"
    echo "⚠️  Could not detect CI checks on main; using defaults: tests secrets-scan" >&2
    echo "    Re-run after at least one successful CI run on main if merge is blocked." >&2
  fi

  echo "$checks"
}

find_ruleset_id() {
  local repo="$1"
  gh api "repos/${repo}/rulesets" -q ".[] | select(.name == \"${RULESET_NAME}\") | .id" 2>/dev/null || true
}

build_ruleset_json() {
  local checks_csv="$1"
  local checks_json
  checks_json="$(printf '%s' "$checks_csv" | tr '|' '\n' | jq -R -s -c 'split("\n") | map(select(length > 0)) | map({context: .})')"

  jq -n \
    --arg name "$RULESET_NAME" \
    --argjson checks "$checks_json" \
    -f /dev/stdin <<'JQEOF'
{
  name: $name,
  target: "branch",
  enforcement: "active",
  conditions: {
    ref_name: {
      include: ["refs/heads/main"],
      exclude: []
    }
  },
  rules: [
    { type: "deletion" },
    { type: "non_fast_forward" },
    {
      type: "pull_request",
      parameters: {
        dismiss_stale_reviews_on_push: false,
        require_code_owner_review: false,
        require_last_push_approval: false,
        required_approving_review_count: 0,
        required_review_thread_resolution: false
      }
    },
    {
      type: "required_status_checks",
      parameters: {
        strict_required_status_checks_policy: true,
        required_status_checks: $checks
      }
    }
  ]
}
JQEOF
}

main() {
  ensure_gh_auth
  local repo
  repo="$(resolve_repo "${1:-}")"
  echo "Repository: ${repo}"

  local detected
  detected="$(fetch_status_checks "$repo")"
  if [[ -n "$detected" ]]; then
    echo "Detected CI checks on main: ${detected}"
  fi

  local checks_csv
  checks_csv="$(pick_checks "$detected")"
  echo "Required status checks: ${checks_csv//|/ }"

  local payload ruleset_id
  payload="$(build_ruleset_json "$checks_csv")"
  ruleset_id="$(find_ruleset_id "$repo")"

  if [[ -n "$ruleset_id" ]]; then
    echo "Updating existing ruleset #${ruleset_id} ..."
    gh api --method PUT "repos/${repo}/rulesets/${ruleset_id}" --input - <<< "$payload" >/dev/null
  else
    echo "Creating ruleset ..."
    ruleset_id="$(gh api --method POST "repos/${repo}/rulesets" --input - <<< "$payload" -q .id)"
  fi

  echo ""
  echo "✅ main branch protection active (ruleset #${ruleset_id})"
  echo ""
  echo "Rules applied:"
  echo "  • Block branch deletion"
  echo "  • Block force push"
  echo "  • Require pull request before merge (0 approvals — solo-friendly)"
  echo "  • Require CI status checks: ${checks_csv//|/ }"
  echo ""
  echo "Verify:"
  echo "  gh api repos/${repo}/rules/branches/main"
}

main "$@"
