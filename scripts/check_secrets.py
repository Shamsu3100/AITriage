"""Run this BEFORE every git push.  python scripts/check_secrets.py

Scans tracked files for things that look like credentials. Not clever, not
complete, but it catches the mistake that actually happens: a key pasted
into source, or a .env that slipped past .gitignore.
"""
import re
import subprocess
import sys

PATTERNS = {
    "Anthropic key":  r"sk-ant-[A-Za-z0-9_\-]{20,}",
    "OpenAI key":     r"sk-[A-Za-z0-9]{32,}",
    "Google key":     r"AIza[A-Za-z0-9_\-]{35}",
    "AWS key id":     r"AKIA[A-Z0-9]{16}",
    "GitHub token":   r"gh[pousr]_[A-Za-z0-9]{36,}",
    "Slack token":    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    "Private key":    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "Hardcoded key=": r"""(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*["'][^"'\s]{12,}["']""",
}
SKIP_SUFFIX = (".lock", ".png", ".jpg", ".gif", ".pdf", ".zip", ".gguf", ".db")


def tracked_files():
    """Only files git would actually upload."""
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    if out.returncode != 0:
        print("Not a git repository. Nothing to check.")
        sys.exit(0)
    return [f for f in out.stdout.splitlines() if f and not f.endswith(SKIP_SUFFIX)]


def main():
    findings = []

    for path in tracked_files():
        # A tracked .env is already a failure, whatever is inside it.
        if path == ".env" or path.endswith("/.env"):
            findings.append((path, 0, ".env is TRACKED BY GIT", ""))
            continue
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            # Placeholders and deliberately-documented examples are not leaks.
            # Real scanners all have an escape hatch; the fix for a false
            # positive is to mark that ONE line, never to disable the tool.
            if ("your-key-here" in line or "PASTE_YOUR" in line
                    or "REDACTED" in line or "allowlist secret" in line):
                continue
            for label, pat in PATTERNS.items():
                m = re.search(pat, line)
                if m:
                    shown = m.group(0)[:14] + "..."
                    findings.append((path, lineno, label, shown))

    if not findings:
        print("OK - no credentials found in tracked files.")
        return 0

    print(f"\n  {len(findings)} PROBLEM(S) FOUND - DO NOT PUSH\n")
    for path, lineno, label, shown in findings:
        where = f"{path}:{lineno}" if lineno else path
        print(f"  {where:<40} {label}  {shown}")
    print("\n  If any of these is a REAL key, it is already compromised the")
    print("  moment you push. Rotate it. Deleting the line is not enough.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
