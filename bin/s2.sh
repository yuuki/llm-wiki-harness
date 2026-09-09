#!/usr/bin/env bash
# s2.sh — Semantic Scholar Graph API client for the wiki-gap skill.
#
# Wraps the two calls wiki-gap Step 4 needs (paper search, paper lookup) with:
#   1. API-key injection read from 1Password via `op read` (falls back to the
#      keyless public pool if the key can't be fetched — the API works either
#      way, a key only raises the shared rate limit).
#   2. A cumulative 1-request-per-second throttle enforced ACROSS all endpoints
#      and across concurrent invocations, per Semantic Scholar's documented
#      limit ("1 request per second, cumulative across all endpoints").
#   3. Exponential backoff on 429 / 5xx.
#
# Usage:
#   bin/s2.sh search "<query or title>"      [--fields F] [--limit N]
#   bin/s2.sh paper  "<paperId|DOI:..|ARXIV:..>" [--fields F]
#   bin/s2.sh raw    "<path?query>"          # e.g. "paper/search?query=foo&limit=1"
#
# On success prints the JSON response to stdout and exits 0.
#
# Environment overrides:
#   S2_API_KEY   pre-fetched key; if set, `op read` is skipped entirely.
#   S2_OP_REF    1Password secret reference for the key. Default:
#                op://Private/Semantic Scholar API Key/s2-api-key
#                (change the vault segment if your personal vault isn't "Private").
#   S2_NO_KEY=1  force keyless mode (skip op read; use the public rate pool).
set -euo pipefail

readonly API_BASE="https://api.semanticscholar.org/graph/v1"
readonly OP_REF="${S2_OP_REF:-op://Private/Semantic Scholar API Key/s2-api-key}"
# Cumulative-rate state, shared across processes. Kept out of git via .vault-meta.
readonly STATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.vault-meta/s2"
readonly STAMP_FILE="$STATE_DIR/last-request"
readonly LOCK_DIR="$STATE_DIR/lock"
readonly MIN_INTERVAL=1.0   # seconds; the documented cumulative limit
readonly MAX_ATTEMPTS=5

log() { printf '%s\n' "s2: $*" >&2; }
die() { log "$*"; rm -rf "$LOCK_DIR" 2>/dev/null || true; exit 1; }

# --- high-resolution clock (BSD `date` on macOS lacks %N) -------------------
now() { perl -MTime::HiRes=time -e 'printf "%.3f", time'; }

# --- API key ---------------------------------------------------------------
# Resolved once per invocation and cached in API_KEY (may be empty = keyless).
API_KEY=""
resolve_key() {
  if [[ -n "${S2_NO_KEY:-}" ]]; then
    log "keyless mode (S2_NO_KEY set); using the public rate pool"
    return
  fi
  if [[ -n "${S2_API_KEY:-}" ]]; then
    API_KEY="$S2_API_KEY"
    return
  fi
  if ! command -v op >/dev/null 2>&1; then
    log "warning: 'op' not found; continuing keyless. Install 1Password CLI or set S2_API_KEY."
    return
  fi
  # op read talks to the 1Password desktop app; if that flow fails (locked app,
  # sandbox, missing item) we degrade to keyless rather than aborting.
  if API_KEY="$(op read "$OP_REF" 2>/dev/null)" && [[ -n "$API_KEY" ]]; then
    return
  fi
  API_KEY=""
  log "warning: could not read key from '$OP_REF'; continuing keyless."
  log "         (unlock 1Password, verify the item/field, or set S2_OP_REF / S2_API_KEY.)"
}

# --- cumulative 1 req/sec throttle -----------------------------------------
# Serialize with an atomic mkdir lock so concurrent invocations can't both slip
# a request into the same second. Lock is held only around the throttle+request.
acquire_lock() {
  mkdir -p "$STATE_DIR"
  local waited=0
  until mkdir "$LOCK_DIR" 2>/dev/null; do
    sleep 0.05
    waited=$((waited + 1))
    if (( waited > 1200 )); then   # ~60s stale-lock guard
      log "warning: clearing stale lock $LOCK_DIR"
      rm -rf "$LOCK_DIR"
    fi
  done
}
release_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }

throttle() {
  [[ -f "$STAMP_FILE" ]] || return 0
  local last; last="$(cat "$STAMP_FILE" 2>/dev/null || echo 0)"
  local wait
  wait="$(perl -e 'my ($last,$min,$now)=@ARGV; my $w=$min-($now-$last); print $w>0 ? sprintf("%.3f",$w) : "0";' \
          "$last" "$MIN_INTERVAL" "$(now)")"
  if [[ "$wait" != "0" ]]; then
    sleep "$wait"
  fi
}

# One rate-limited GET. Args: full URL (already built). Sets HTTP_CODE, BODY.
HTTP_CODE=""
BODY=""
rate_limited_get() {
  local url="$1" out
  # Build the auth header as an array so the "key: value" pair stays a single
  # argv entry. A ${VAR:+-H "..."} expansion collapses into ONE arg and breaks
  # the header (403), so don't reintroduce that.
  local hdr=()
  [[ -n "$API_KEY" ]] && hdr=(-H "x-api-key: $API_KEY")
  acquire_lock
  # shellcheck disable=SC2064
  trap 'release_lock' RETURN
  throttle
  out="$(curl -sS -m 30 -w $'\n%{http_code}' \
              "${hdr[@]}" \
              "$url")" || { now > "$STAMP_FILE"; die "curl failed for $url"; }
  now > "$STAMP_FILE"     # stamp AFTER the request completes
  HTTP_CODE="${out##*$'\n'}"
  BODY="${out%$'\n'*}"
}

# GET with backoff on 429/5xx. Every attempt goes through the throttle.
get_with_backoff() {
  local url="$1" attempt=1 backoff=2
  while :; do
    rate_limited_get "$url"
    case "$HTTP_CODE" in
      2??) printf '%s' "$BODY"; return 0 ;;
      429|5??)
        if (( attempt >= MAX_ATTEMPTS )); then
          die "HTTP $HTTP_CODE after $attempt attempts: $url"
        fi
        log "HTTP $HTTP_CODE; retrying in ${backoff}s (attempt $attempt/$MAX_ATTEMPTS)"
        sleep "$backoff"
        backoff=$((backoff * 2))
        attempt=$((attempt + 1))
        ;;
      *) die "HTTP $HTTP_CODE: $BODY" ;;
    esac
  done
}

# Percent-encode one string (RFC3986 unreserved kept).
urlencode() {
  perl -MURI::Escape -e 'print uri_escape($ARGV[0], "^A-Za-z0-9\-._~")' "$1" 2>/dev/null \
    || perl -e 'my $s=$ARGV[0]; $s=~s/([^A-Za-z0-9\-._~])/sprintf("%%%02X",ord($1))/ge; print $s' "$1"
}

cmd_search() {
  local query="" fields="title,year,externalIds,citationCount,url,abstract" limit="5"
  [[ $# -gt 0 ]] || die "search: missing query"
  query="$1"; shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --fields) fields="$2"; shift 2 ;;
      --limit)  limit="$2";  shift 2 ;;
      *) die "search: unknown arg $1" ;;
    esac
  done
  local url="$API_BASE/paper/search?query=$(urlencode "$query")&fields=$(urlencode "$fields")&limit=$(urlencode "$limit")"
  get_with_backoff "$url"
  echo
}

cmd_paper() {
  local id="" fields="title,year,externalIds,citationCount,url"
  [[ $# -gt 0 ]] || die "paper: missing id"
  id="$1"; shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --fields) fields="$2"; shift 2 ;;
      *) die "paper: unknown arg $1" ;;
    esac
  done
  # id may be paperId, DOI:10.x/..., ARXIV:1712.01208 — encode to keep slashes safe.
  local url="$API_BASE/paper/$(urlencode "$id")?fields=$(urlencode "$fields")"
  get_with_backoff "$url"
  echo
}

cmd_raw() {
  [[ $# -gt 0 ]] || die "raw: missing path"
  get_with_backoff "$API_BASE/$1"
  echo
}

main() {
  [[ $# -gt 0 ]] || die "usage: s2.sh {search|paper|raw} ARGS   (see header)"
  local sub="$1"; shift
  resolve_key
  case "$sub" in
    search) cmd_search "$@" ;;
    paper)  cmd_paper "$@" ;;
    raw)    cmd_raw "$@" ;;
    -h|--help|help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
    *) die "unknown subcommand '$sub' (want search|paper|raw)" ;;
  esac
}

main "$@"
