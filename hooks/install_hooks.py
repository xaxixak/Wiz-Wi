"""
Workspace Intelligence Layer - Git Hook Installer
===================================================

Story 3.4: Install/uninstall git hooks for automatic incremental updates.

Usage:
  python hooks/install_hooks.py install   - Install the post-commit hook
  python hooks/install_hooks.py uninstall - Remove the post-commit hook
  python hooks/install_hooks.py status    - Check if hooks are installed

The post-commit hook calls `python cli.py update` with a 5-second debounce
to avoid redundant updates on rapid sequential commits.
"""

import os
import sys
import stat
import subprocess
import argparse
from pathlib import Path

# Resolve paths relative to the workspace-intelligence project root
WI_ROOT = Path(__file__).resolve().parent.parent
CLI_PATH = WI_ROOT / "cli.py"
DEBOUNCE_FILE = ".wi-last-update"
DEBOUNCE_SECONDS = 5

# Marker used to identify hooks managed by this installer
HOOK_MARKER = "# --- workspace-intelligence post-commit hook ---"


def _find_git_root(start_path: Path = None) -> Path:
    """
    Find the nearest git repository root by walking up from start_path.

    Tries `git rev-parse --show-toplevel` first, falls back to directory walk.
    """
    start = start_path or Path.cwd()

    # Try git command first
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(start),
            timeout=10,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Fallback: walk up looking for .git directory
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent

    print("ERROR: Not inside a git repository.")
    print(f"  Searched from: {start}")
    sys.exit(1)


def _get_hooks_dir(git_root: Path) -> Path:
    """Get the git hooks directory, respecting core.hooksPath config."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            cwd=str(git_root),
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            custom_path = Path(result.stdout.strip())
            if custom_path.is_absolute():
                return custom_path
            return git_root / custom_path
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return git_root / ".git" / "hooks"


def _generate_hook_script(cli_path: Path, workspace_path: Path) -> str:
    """
    Generate the post-commit hook shell script content.

    The script includes:
    - Debounce logic using a timestamp file
    - Calls cli.py update in the background
    - Works on both bash (Linux/macOS) and Git Bash (Windows)
    """
    # Use forward slashes for git bash compatibility on Windows
    cli_posix = str(cli_path).replace("\\", "/")
    ws_posix = str(workspace_path).replace("\\", "/")

    script = f"""#!/bin/sh
{HOOK_MARKER}
# Auto-installed by workspace-intelligence hooks/install_hooks.py
# Runs incremental graph update after each commit with debounce.
#
# To uninstall: python hooks/install_hooks.py uninstall
# Or just delete this file.

DEBOUNCE_FILE="{ws_posix}/{DEBOUNCE_FILE}"
DEBOUNCE_SECONDS={DEBOUNCE_SECONDS}
CLI_PATH="{cli_posix}"
WORKSPACE="{ws_posix}"

# --- Debounce check ---
if [ -f "$DEBOUNCE_FILE" ]; then
    LAST_UPDATE=$(cat "$DEBOUNCE_FILE" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST_UPDATE))
    if [ "$ELAPSED" -lt "$DEBOUNCE_SECONDS" ]; then
        # Skip: too soon since last update
        exit 0
    fi
fi

# --- Record timestamp ---
date +%s > "$DEBOUNCE_FILE"

# --- Run incremental update in background ---
# Using python directly; adjust if you use a venv
python "$CLI_PATH" update "$WORKSPACE" --ref HEAD~1 > /dev/null 2>&1 &

exit 0
"""
    return script


def cmd_install(args: argparse.Namespace) -> None:
    """Install the post-commit hook."""
    git_root = _find_git_root(Path(args.repo) if args.repo else None)
    hooks_dir = _get_hooks_dir(git_root)
    hook_path = hooks_dir / "post-commit"

    print(f"Git root:   {git_root}")
    print(f"Hooks dir:  {hooks_dir}")
    print(f"CLI path:   {CLI_PATH}")

    # Create hooks directory if needed
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing hook
    if hook_path.exists():
        content = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER in content:
            print(f"\nWorkspace Intelligence hook already installed at:")
            print(f"  {hook_path}")
            if args.force:
                print("  --force: Overwriting existing hook.")
            else:
                print("  Use --force to overwrite.")
                return
        else:
            # Existing hook from something else
            print(f"\nWARNING: A post-commit hook already exists at:")
            print(f"  {hook_path}")
            print(f"  It was NOT installed by workspace-intelligence.")
            if args.force:
                # Back up the existing hook
                backup_path = hook_path.with_suffix(".backup")
                print(f"  Backing up existing hook to: {backup_path}")
                hook_path.rename(backup_path)
            else:
                print(f"  Use --force to overwrite (existing hook will be backed up).")
                return

    # Generate and write the hook
    workspace_path = git_root  # Use git root as the workspace
    script = _generate_hook_script(CLI_PATH, workspace_path)

    hook_path.write_text(script, encoding="utf-8")

    # Make executable (on Unix-like systems)
    try:
        current_mode = hook_path.stat().st_mode
        hook_path.chmod(current_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass  # Windows does not need chmod

    print(f"\nPost-commit hook installed successfully:")
    print(f"  {hook_path}")
    print(f"\nThe hook will run 'python cli.py update' after each commit")
    print(f"with a {DEBOUNCE_SECONDS}-second debounce.")


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Uninstall the post-commit hook."""
    git_root = _find_git_root(Path(args.repo) if args.repo else None)
    hooks_dir = _get_hooks_dir(git_root)
    hook_path = hooks_dir / "post-commit"

    if not hook_path.exists():
        print("No post-commit hook found. Nothing to uninstall.")
        return

    content = hook_path.read_text(encoding="utf-8", errors="replace")
    if HOOK_MARKER not in content:
        print(f"Post-commit hook exists but was NOT installed by workspace-intelligence.")
        print(f"  {hook_path}")
        print(f"  Refusing to remove a hook we did not create.")
        return

    hook_path.unlink()
    print(f"Post-commit hook removed: {hook_path}")

    # Also remove debounce file if present
    debounce_path = git_root / DEBOUNCE_FILE
    if debounce_path.exists():
        debounce_path.unlink()
        print(f"Debounce file removed: {debounce_path}")

    # Check for backup to restore
    backup_path = hook_path.with_suffix(".backup")
    if backup_path.exists():
        print(f"\nA backup of a previous hook exists at: {backup_path}")
        print(f"  To restore it: rename it to {hook_path.name}")

    print("\nHook uninstalled successfully.")


def cmd_status(args: argparse.Namespace) -> None:
    """Check if hooks are installed."""
    git_root = _find_git_root(Path(args.repo) if args.repo else None)
    hooks_dir = _get_hooks_dir(git_root)
    hook_path = hooks_dir / "post-commit"

    print(f"Git root:  {git_root}")
    print(f"Hooks dir: {hooks_dir}")

    if not hook_path.exists():
        print(f"\npost-commit hook: NOT INSTALLED")
        return

    content = hook_path.read_text(encoding="utf-8", errors="replace")
    if HOOK_MARKER in content:
        print(f"\npost-commit hook: INSTALLED (workspace-intelligence)")
        print(f"  Path: {hook_path}")

        # Check debounce state
        debounce_path = git_root / DEBOUNCE_FILE
        if debounce_path.exists():
            try:
                import time
                ts = int(debounce_path.read_text(encoding="utf-8").strip())
                elapsed = int(time.time()) - ts
                print(f"  Last update: {elapsed}s ago")
            except (ValueError, OSError):
                print(f"  Last update: unknown (corrupt debounce file)")
        else:
            print(f"  Last update: never (no debounce file)")
    else:
        print(f"\npost-commit hook: EXISTS (not from workspace-intelligence)")
        print(f"  Path: {hook_path}")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="install_hooks",
        description="Install/uninstall workspace-intelligence git hooks",
    )
    subparsers = parser.add_subparsers(dest="action", help="Available actions")

    # -- install --------------------------------------------------------------
    p_install = subparsers.add_parser(
        "install",
        help="Install the post-commit hook",
    )
    p_install.add_argument(
        "--repo",
        default=None,
        help="Path to git repository (default: auto-detect)",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing hook (backs up if not ours)",
    )
    p_install.set_defaults(func=cmd_install)

    # -- uninstall ------------------------------------------------------------
    p_uninstall = subparsers.add_parser(
        "uninstall",
        help="Remove the post-commit hook",
    )
    p_uninstall.add_argument(
        "--repo",
        default=None,
        help="Path to git repository (default: auto-detect)",
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    # -- status ---------------------------------------------------------------
    p_status = subparsers.add_parser(
        "status",
        help="Check if hooks are installed",
    )
    p_status.add_argument(
        "--repo",
        default=None,
        help="Path to git repository (default: auto-detect)",
    )
    p_status.set_defaults(func=cmd_status)

    return parser


def main() -> None:
    """Entry point for the hook installer."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
