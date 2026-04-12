#!/usr/bin/env bash
# verify_scrub.sh — zero-match verifier for the release tree.
# Exits 1 if any forbidden pattern is found. Use as a pre-commit gate.
set -uo pipefail

REPO="${1:-.}"
FAIL=0

check() {
    local pattern="$1"
    local description="$2"
    local matches
    matches=$(grep -rlnE "$pattern" "$REPO" \
        --exclude-dir=node_modules \
        --exclude-dir=.git \
        --exclude-dir=dist \
        --exclude-dir=__pycache__ \
        --exclude=verify_scrub.sh \
        2>/dev/null || true)
    if [[ -n "$matches" ]]; then
        echo "FAIL [$description]"
        echo "$matches" | sed 's/^/  /'
        FAIL=1
    else
        echo "PASS [$description]"
    fi
}

echo "=== Secret literals ==="
check 'audit-director-key'                                       "Director password literal"
check 'audit-(trace|safety|policy|hallucination|drift|cost)-key'  "Auditor password literals"
check 'BwozyQ9yjoqvVzz1BhrzV7vnqUtaoSks'                         "project-rpi token"

echo ""
echo "=== Personal paths ==="
check 'C:\\Users\\RA'                                            "Windows backslash RA path"
check 'C:/Users/RA'                                              "Windows forward-slash RA path"
check '/c/Users/RA'                                              "Git-bash RA path"
check 'Documents[/\\]RPi'                                        "Documents/RPi path"
check 'Obsidian Vaults'                                          "Obsidian vault reference"
check 'OneDrive'                                                 "OneDrive path reference"

echo ""
echo "=== Internal codenames ==="
check '\brpi5-vision\b'                                          "Historical codename rpi5-vision"

echo ""
echo "=== Internal dev notes ==="
test -d "$REPO/docs/superpowers" && { echo "FAIL: docs/superpowers/ present"; FAIL=1; } || echo "PASS: docs/superpowers/ absent"

for f in CHECKPOINT-2026-04-05.md TEST-RUN-REPORT-2026-04-06.md TEST-RUN-2-REPORT-2026-04-06.md; do
    test -e "$REPO/$f" && { echo "FAIL: $f present"; FAIL=1; } || echo "PASS: $f absent"
done

echo ""
echo "=== Forbidden files ==="
for f in .env config/redis-acl.conf config/projects.json data/audit.db .claude/settings.local.json adapters/rpi_adapter.py; do
    test -e "$REPO/$f" && { echo "FAIL: $f present"; FAIL=1; } || echo "PASS: $f absent"
done

echo ""
echo "=== Memory-file canary ==="
memory_hits=$(find "$REPO" \( -iname "MEMORY.md" -o -iname "memory_*.md" -o -path "*/memory/*" \) \
              -not -path "*/node_modules/*" -not -path "*/.git/*" 2>/dev/null)
if [[ -n "$memory_hits" ]]; then
    echo "FAIL [memory files present in project tree]"
    echo "$memory_hits" | sed 's/^/  /'
    FAIL=1
else
    echo "PASS: no memory files"
fi

echo ""
echo "=== Required files ==="
for f in README.md LICENSE .gitignore .env.example config/redis-acl.conf.example config/projects.json.example docker-compose.yml; do
    test -f "$REPO/$f" && echo "PASS: $f present" || { echo "FAIL: missing $f"; FAIL=1; }
done

echo ""
echo "=== rpi identifier audit (review manually) ==="
grep -rnE '\brpi\b' "$REPO" \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist \
    --exclude-dir=__pycache__ --exclude=verify_scrub.sh 2>/dev/null || echo "  (none)"

echo ""
if [ $FAIL -eq 0 ]; then
    echo "ALL CHECKS PASSED"
else
    echo "SCRUB INCOMPLETE — fix failures above"
fi

exit $FAIL
