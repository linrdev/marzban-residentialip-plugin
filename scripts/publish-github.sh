#!/usr/bin/env bash
# Publishes the local, committed add-on and forks original Marzban under linrdev.
# Requires: bash, git, GitHub CLI (gh), internet, GitHub auth with repo creation rights.
set -Eeuo pipefail
owner="linrdev"
repo="$owner/marzban-residentialip-plugin"
upstream="Gozargah/Marzban"
fork="$owner/Marzban"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for bin in gh git; do
  command -v "$bin" >/dev/null || { echo "Missing $bin" >&2; exit 1; }
done
gh auth status >/dev/null
active_login="$(gh api user --jq .login)"
[[ "$active_login" == "$owner" ]] || { echo "Authenticated as $active_login, not $owner" >&2; exit 1; }
[[ -d .git ]] || { echo "First initialize/clone the committed Git repository." >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Uncommitted changes: commit/review before publishing" >&2; exit 1; }
[[ "$(git branch --show-current)" == "main" ]] || { echo "Expected main branch" >&2; exit 1; }

if gh repo view "$repo" >/dev/null 2>&1; then
  visibility="$(gh api "repos/$repo" --jq .visibility)"
  [[ "$visibility" == "public" ]] || { echo "Repo $repo exists but is $visibility, refusing to change visibility" >&2; exit 1; }
  existing_head="$(git ls-remote "https://github.com/$repo.git" HEAD | cut -f1)"
  [[ -z "$existing_head" ]] || { echo "Repo $repo already has commits. Refusing to overwrite or merge." >&2; exit 1; }
  git remote add origin "https://github.com/$repo.git" 2>/dev/null || true
  git push -u origin main
else
  gh repo create "$repo" --public --description "Exclusive residential IP egress add-on for Marzban and Xray" --source=. --remote=origin --push
fi

if gh repo view "$fork" >/dev/null 2>&1; then
  is_fork="$(gh api "repos/$fork" --jq '.fork')"
  parent="$(gh api "repos/$fork" --jq '.parent.full_name // empty')"
  if [[ "$is_fork" != "true" || "${parent,,}" != "${upstream,,}" ]]; then
    echo "$fork exists but is not a fork of $upstream. Refusing to overwrite." >&2
    exit 1
  fi
  echo "Upstream fork already exists: https://github.com/$fork"
else
  gh repo fork "$upstream" --clone=false --remote=false
fi
printf '\nPlugin: https://github.com/%s\nFork:   https://github.com/%s\n' "$repo" "$fork"
