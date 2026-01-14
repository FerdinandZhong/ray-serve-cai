#!/usr/bin/env python3
"""
Setup CML project for Ray cluster deployment.

This script:
1. Searches for existing project or creates new one with git
2. Waits for git clone to complete
3. Returns project ID for next steps

Run this in GitHub Actions as the first deployment step.
"""

import json
import os
import sys
import time
import requests
from typing import Optional


class ProjectSetup:
    """Handle CML project creation and git clone waiting."""

    def __init__(self):
        """Initialize CML REST API client."""
        self.cml_host = os.environ.get("CML_HOST")
        self.api_key = os.environ.get("CML_API_KEY")
        self.github_repo = os.environ.get("GITHUB_REPOSITORY")
        self.gh_pat = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
        self.project_name = "ray-cluster"

        if not all([self.cml_host, self.api_key]):
            print("❌ Error: Missing required environment variables")
            print("   Required: CML_HOST, CML_API_KEY")
            sys.exit(1)

        self.api_url = f"{self.cml_host.rstrip('/')}/api/v2"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key.strip()}",
        }

    def make_request(
        self, method: str, endpoint: str, data: dict = None, params: dict = None
    ) -> Optional[dict]:
        """Make an API request to CML."""
        url = f"{self.api_url}/{endpoint.lstrip('/')}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                json=data,
                params=params,
                timeout=30,
            )

            if 200 <= response.status_code < 300:
                if response.text:
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        return {}
                return {}
            else:
                print(f"❌ API Error ({response.status_code}): {response.text[:200]}")
                return None

        except Exception as e:
            print(f"❌ Request error: {e}")
            return None

    def search_projects(self, project_name: str) -> Optional[str]:
        """Search for a project by name."""
        print(f"🔍 Searching for project: {project_name}")
        search_filter = f'{{"name":"{project_name}"}}'

        result = self.make_request(
            "GET", "projects", params={"search_filter": search_filter, "page_size": 50}
        )

        if result:
            projects = result.get("projects", [])
            if projects:
                project_id = projects[0].get("id")
                print(f"✅ Found existing project: {project_id}")
                return project_id

        print(f"   No existing project found")
        return None

    def create_project_with_git(
        self, project_name: str, git_url: str
    ) -> Optional[str]:
        """Create a project with git template."""
        print(f"📝 Creating project: {project_name}")

        project_data = {
            "name": project_name,
            "description": "Ray cluster deployment project",
            "template": "git",
            "project_visibility": "private",
        }

        # Add git configuration (without token in URL for security)
        if git_url:
            project_data["git_url"] = git_url

        result = self.make_request("POST", "projects", data=project_data)

        if result:
            project_id = result.get("id")
            print(f"✅ Project created: {project_id}")
            return project_id

        return None

    def get_or_create_project(self) -> Optional[str]:
        """Get existing project or create new one with git."""
        print("\n" + "=" * 70)
        print("Step 1: Get or Create CML Project")
        print("=" * 70)

        # Try to find existing project
        project_id = self.search_projects(self.project_name)
        if project_id:
            return project_id

        # Create new project with git
        if not self.github_repo:
            print("⚠️  No existing project and no GitHub repo provided")
            print("   Set GITHUB_REPOSITORY to create project with git")
            return None

        git_url = f"https://github.com/{self.github_repo}"
        return self.create_project_with_git(self.project_name, git_url)

    def wait_for_git_clone(self, project_id: str, timeout: int = 900) -> bool:
        """Wait for git repository to be cloned."""
        print(f"\n⏳ Waiting for git clone to complete...")
        print(f"   (timeout: {timeout}s)\n")

        start_time = time.time()
        last_status = None
        poll_count = 0

        while time.time() - start_time < timeout:
            result = self.make_request("GET", f"projects/{project_id}")
            poll_count += 1

            if result:
                status = result.get("status", "unknown").lower()

                # Only print on status change
                if status != last_status:
                    elapsed = int(time.time() - start_time)
                    print(f"   [{elapsed}s] Status: {status}")
                    last_status = status

                if status in ["success", "ready"]:
                    print(f"✅ Git clone complete")
                    print(f"   Waiting 30 seconds for files to be written...\n")
                    time.sleep(30)
                    return True
                elif status in ["error", "failed"]:
                    print(f"\n❌ Git clone failed: {status}")
                    error_msg = result.get("error_message", "No error message")
                    print(f"   Error: {error_msg}\n")
                    return False

            time.sleep(10)

        elapsed = int(time.time() - start_time)
        print(f"❌ Timeout waiting for git clone ({elapsed}s / {timeout}s)")
        print(f"   Polled {poll_count} times\n")
        return False

    def run(self) -> bool:
        """Execute project setup."""
        print("=" * 70)
        print("🚀 CML Project Setup")
        print("=" * 70)

        project_id = self.get_or_create_project()
        if not project_id:
            print("❌ Failed to get/create project")
            return False

        # Only wait for git clone if we created a new project with git
        if self.github_repo:
            if not self.wait_for_git_clone(project_id):
                print("❌ Git clone failed")
                return False

        print("=" * 70)
        print("✅ Project Setup Complete!")
        print("=" * 70)
        print(f"\nProject ID: {project_id}")

        # Output for GitHub Actions
        print(f"\n::set-output name=project_id::{project_id}")
        # Also write to file for shell access
        with open("/tmp/project_id.txt", "w") as f:
            f.write(project_id)

        return True


def main():
    """Main entry point."""
    try:
        setup = ProjectSetup()
        success = setup.run()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠️  Setup cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
