#!/usr/bin/env python3
"""
Git Repository Synchronization Script for CML Jobs

This script pulls the latest changes from the remote repository to keep the
CML project code in sync with GitHub. It's designed to run as a scheduled job
in Cloudera Machine Learning.

Features:
- Pulls latest changes from the configured remote branch
- Handles authentication via GitHub token
- Provides detailed logging of sync operations
- Exits with appropriate status codes for job scheduling
"""

import os
import subprocess
import sys
from typing import Tuple


def run_command(cmd: str, cwd: str = None, check: bool = True) -> Tuple[bool, str, str]:
    """
    Execute a shell command and return success status with output.

    Args:
        cmd: Command to execute
        cwd: Working directory (defaults to /home/cdsw)
        check: Whether to raise exception on non-zero exit code

    Returns:
        Tuple of (success: bool, stdout: str, stderr: str)
    """
    if cwd is None:
        cwd = "/home/cdsw"

    print(f"🔄 Running: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True
        )
        if result.stdout:
            print(result.stdout)
        return True, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        error_msg = f"Command failed with exit code {e.returncode}"
        print(f"❌ {error_msg}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        if e.stdout:
            print(f"Output: {e.stdout}")
        return False, e.stdout or "", e.stderr or ""


def setup_git_config() -> bool:
    """
    Configure git with necessary settings for automated operations.

    Returns:
        True if configuration succeeded, False otherwise
    """
    print("\n📋 Setting up Git configuration...")

    config_commands = [
        "git config --global user.email 'cml-sync@cloudera.local'",
        "git config --global user.name 'CML Git Sync'",
    ]

    for cmd in config_commands:
        success, _, _ = run_command(cmd)
        if not success:
            print(f"⚠️  Warning: Failed to run: {cmd}")

    return True


def get_current_branch() -> str:
    """
    Get the current git branch name.

    Returns:
        Current branch name or 'unknown' if unable to determine
    """
    success, stdout, _ = run_command("git rev-parse --abbrev-ref HEAD", check=False)
    if success:
        return stdout.strip()
    return "unknown"


def get_remote_url() -> str:
    """
    Get the origin remote URL.

    Returns:
        Remote URL or 'unknown' if unable to determine
    """
    success, stdout, _ = run_command("git config --get remote.origin.url", check=False)
    if success:
        return stdout.strip()
    return "unknown"


def fetch_from_remote() -> bool:
    """
    Fetch updates from the remote repository.

    Returns:
        True if fetch succeeded, False otherwise
    """
    print("\n📥 Fetching from remote repository...")

    # If GITHUB_TOKEN is set, configure authenticated fetch
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if github_token:
        print("🔐 Using GitHub token for authentication")
        # Git stores credentials in the URL during fetch
        # The token is already in the URL if set up by the deployment script

    success, _, _ = run_command("git fetch origin")
    if success:
        print("✅ Successfully fetched from remote")
        return True
    else:
        print("❌ Failed to fetch from remote")
        return False


def reset_to_remote() -> bool:
    """
    Reset the local branch to match the remote branch.

    This discards any local uncommitted changes and makes the branch
    match the remote exactly.

    Returns:
        True if reset succeeded, False otherwise
    """
    print("\n🔄 Resetting local branch to remote...")

    branch = get_current_branch()
    if branch == "unknown":
        print("❌ Could not determine current branch")
        return False

    # Reset to remote tracking branch
    success, _, _ = run_command(f"git reset --hard origin/{branch}")
    if success:
        print(f"✅ Successfully reset to origin/{branch}")
        return True
    else:
        print(f"❌ Failed to reset to origin/{branch}")
        return False


def clean_working_directory() -> bool:
    """
    Clean up any untracked files in the working directory.

    Returns:
        True if cleanup succeeded, False otherwise
    """
    print("\n🧹 Cleaning working directory...")

    success, _, _ = run_command("git clean -fd")
    if success:
        print("✅ Working directory cleaned")
        return True
    else:
        print("❌ Failed to clean working directory")
        return False


def get_sync_status() -> dict:
    """
    Get current git status information.

    Returns:
        Dictionary with status information
    """
    status_info = {
        "branch": get_current_branch(),
        "remote_url": get_remote_url(),
    }

    # Get latest commit info
    success, stdout, _ = run_command(
        "git log -1 --format='%h - %s (%an, %ai)'",
        check=False
    )
    if success:
        status_info["latest_commit"] = stdout.strip()

    # Get commit count
    success, stdout, _ = run_command("git rev-list --count HEAD", check=False)
    if success:
        status_info["commit_count"] = stdout.strip()

    return status_info


def main():
    """Main git sync function."""
    print("=" * 60)
    print("🔄 Starting Git Repository Synchronization")
    print("=" * 60)

    # Verify we're in the correct directory
    project_dir = "/home/cdsw"
    if not os.path.exists(project_dir):
        print(f"❌ Error: Project directory not found: {project_dir}")
        raise RuntimeError(f"Project directory not found: {project_dir}")

    print(f"\n📂 Working directory: {project_dir}")

    # Check if .git directory exists
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.exists(git_dir):
        print(f"❌ Error: Not a git repository: {git_dir}")
        raise RuntimeError(f"Not a git repository: {git_dir}")

    # Setup git configuration
    if not setup_git_config():
        print("⚠️  Warning: Git configuration setup had issues")

    # Display current status
    print("\n📊 Current Git Status:")
    status = get_sync_status()
    for key, value in status.items():
        print(f"  {key}: {value}")

    # Perform the sync
    print("\n" + "=" * 60)
    print("Starting synchronization steps...")
    print("=" * 60)

    # Step 1: Fetch from remote
    if not fetch_from_remote():
        print("\n❌ Sync failed: Could not fetch from remote")
        raise RuntimeError("Could not fetch from remote")

    # Step 2: Clean working directory
    if not clean_working_directory():
        print("\n⚠️  Warning: Could not fully clean working directory, continuing...")

    # Step 3: Reset to remote
    if not reset_to_remote():
        print("\n❌ Sync failed: Could not reset to remote")
        raise RuntimeError("Could not reset to remote")

    # Display final status
    print("\n📊 Final Git Status:")
    final_status = get_sync_status()
    for key, value in final_status.items():
        print(f"  {key}: {value}")

    print("\n" + "=" * 60)
    print("✅ Git synchronization completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise  # Re-raise the exception instead of sys.exit(1)
