#!/usr/bin/env python3
"""
Orchestrate Ray cluster deployment to Cloudera Machine Learning (CML).

This script handles:
1. Project creation/discovery
2. Git repository cloning
3. Job configuration and setup
4. Job execution in sequence
5. Ray cluster deployment

Usage:
    export CML_HOST="https://ml.example.cloudera.site"
    export CML_API_KEY="your-api-key"
    export GITHUB_REPOSITORY="owner/repo"
    export GH_PAT="your-github-token"  # optional, for private repos

    python cai_integration/deploy_to_cml.py
"""

import json
import os
import sys
import time
import requests
import yaml
from typing import Dict, Any, Optional
from pathlib import Path


class CAIDeployer:
    """Handle Ray cluster deployment to CML via REST API."""

    def __init__(self):
        """Initialize CML REST API client."""
        print("🚀 Initializing CAI Deployer...")

        self.cml_host = os.environ.get("CML_HOST")
        self.api_key = os.environ.get("CML_API_KEY")
        self.github_repo = os.environ.get("GITHUB_REPOSITORY")
        self.gh_pat = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
        self.project_name = "ray-cluster"
        self.force_rebuild = os.environ.get("FORCE_REBUILD", "").lower() == "true"

        print(f"   CML Host: {self.cml_host}")
        print(f"   Project Name: {self.project_name}")
        print(f"   GitHub Repo: {self.github_repo}")
        print(f"   Force Rebuild: {self.force_rebuild}")

        if not all([self.cml_host, self.api_key]):
            print("❌ Error: Missing required environment variables")
            print("   Required: CML_HOST, CML_API_KEY")
            sys.exit(1)

        # Setup API
        self.api_url = f"{self.cml_host.rstrip('/')}/api/v2"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key.strip()}",
        }
        print(f"   API URL: {self.api_url}")
        print("✅ Deployer initialized")

    def make_request(
        self,
        method: str,
        endpoint: str,
        data: Dict = None,
        params: Dict = None,
        files: Dict = None
    ) -> Optional[Dict]:
        """Make an API request to CML."""
        url = f"{self.api_url}/{endpoint.lstrip('/')}"

        try:
            headers = self.headers.copy()
            if files:
                headers.pop("Content-Type", None)

            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data if not files else None,
                files=files,
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
            "GET", "projects",
            params={"search_filter": search_filter, "page_size": 50}
        )

        if result:
            projects = result.get("projects", [])
            if projects:
                project_id = projects[0].get("id")
                print(f"✅ Found existing project: {project_id}")
                return project_id

        print(f"   No existing project found")
        return None

    def create_project_with_git(self, project_name: str, git_url: str) -> Optional[str]:
        """Create a project with git template."""
        print(f"📝 Creating project: {project_name}")

        project_data = {
            "name": project_name,
            "description": "Ray cluster deployment project",
            "template": "git",
            "project_visibility": "private",
        }

        # Add git configuration if provided
        if git_url:
            # Inject token into git URL if available
            if self.gh_pat and "github.com" in git_url:
                git_url = git_url.replace(
                    "https://github.com",
                    f"https://{self.gh_pat}@github.com"
                )
            project_data["git_url"] = git_url

        result = self.make_request("POST", "projects", data=project_data)

        if result:
            project_id = result.get("id")
            print(f"✅ Project created: {project_id}")
            return project_id

        return None

    def get_or_create_project(self) -> Optional[str]:
        """Get existing project or create new one with git."""
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
        print(f"⏳ Waiting for git clone to complete...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self.make_request("GET", f"projects/{project_id}")

            if result:
                status = result.get("status", "").lower()
                print(f"   Status: {status}")

                if status in ["success", "ready"]:
                    print("✅ Git clone complete")
                    time.sleep(30)  # Extra time for files to write
                    return True
                elif status in ["error", "failed"]:
                    print(f"❌ Git clone failed: {status}")
                    return False

            time.sleep(10)

        print("❌ Timeout waiting for git clone")
        return False

    def load_jobs_config(self) -> Dict[str, Any]:
        """Load jobs configuration from YAML."""
        config_path = Path(__file__).parent / "jobs_config.yaml"

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            print(f"✅ Loaded jobs config from {config_path}")
            return config
        except Exception as e:
            print(f"❌ Failed to load jobs config: {e}")
            return {}

    def create_or_update_jobs(self, project_id: str, jobs_config: Dict) -> Dict[str, str]:
        """Create or update jobs from configuration."""
        print(f"\n📋 Setting up jobs...")

        job_ids = {}

        for job_key, job_config in jobs_config.get("jobs", {}).items():
            print(f"\n   Job: {job_config['name']}")

            # Search for existing job
            result = self.make_request(
                "GET", f"projects/{project_id}/jobs",
                params={"search_filter": f'{{"name":"{job_config["name"]}"}}'}
            )

            existing_job_id = None
            if result:
                jobs = result.get("jobs", [])
                if jobs:
                    existing_job_id = jobs[0].get("id")

            # Prepare job data
            job_data = {
                "name": job_config["name"],
                "script": job_config["script"],
                "cpu": job_config.get("cpu", 4),
                "memory": job_config.get("memory", 16),
                "timeout": job_config.get("timeout", 600),
            }

            # Add parent job if specified
            parent_key = job_config.get("parent_job_key")
            if parent_key and parent_key in job_ids:
                job_data["parent_job_id"] = job_ids[parent_key]

            # Create or update job
            if existing_job_id:
                print(f"      Updating existing job: {existing_job_id}")
                result = self.make_request(
                    "PATCH",
                    f"projects/{project_id}/jobs/{existing_job_id}",
                    data=job_data
                )
                if result:
                    job_ids[job_key] = existing_job_id
            else:
                print(f"      Creating new job")
                result = self.make_request(
                    "POST",
                    f"projects/{project_id}/jobs",
                    data=job_data
                )
                if result:
                    job_ids[job_key] = result.get("id")

            if job_ids.get(job_key):
                print(f"      ✅ Job ready: {job_ids[job_key]}")
            else:
                print(f"      ❌ Failed to setup job")

        return job_ids

    def job_succeeded_recently(self, project_id: str, job_id: str) -> bool:
        """Check if job has completed successfully recently."""
        result = self.make_request(
            "GET",
            f"projects/{project_id}/jobs/{job_id}/runs",
            params={"page_size": 5}
        )

        if result:
            runs = result.get("runs", [])
            for run in runs:
                status = run.get("status", "").lower()
                if status == "succeeded":
                    print(f"      Job succeeded recently")
                    return True

        return False

    def trigger_job(self, project_id: str, job_id: str) -> Optional[str]:
        """Trigger a job execution."""
        result = self.make_request(
            "POST",
            f"projects/{project_id}/jobs/{job_id}/runs",
            data={}
        )

        if result:
            run_id = result.get("id")
            print(f"      ✅ Job triggered: {run_id}")
            return run_id

        return None

    def wait_for_job_completion(self, project_id: str, job_id: str, run_id: str, timeout: int = 1800) -> bool:
        """Wait for job to complete."""
        print(f"      ⏳ Waiting for job to complete...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self.make_request(
                "GET",
                f"projects/{project_id}/jobs/{job_id}/runs/{run_id}"
            )

            if result:
                status = result.get("status", "").lower()

                if status == "succeeded":
                    print(f"      ✅ Job completed successfully")
                    return True
                elif status in ["failed", "error"]:
                    print(f"      ❌ Job failed: {status}")
                    return False

            time.sleep(10)

        print(f"      ❌ Job timeout")
        return False

    def deploy(self):
        """Execute the full deployment."""
        print("\n" + "=" * 70)
        print("🚀 Ray Cluster Deployment Orchestration")
        print("=" * 70)

        # Step 1: Get or create project
        print("\n📦 Step 1: Project Setup")
        project_id = self.get_or_create_project()
        if not project_id:
            print("❌ Failed to get/create project")
            return False

        # Step 2: Wait for git clone
        print("\n🔄 Step 2: Wait for Git Clone")
        if self.github_repo:
            if not self.wait_for_git_clone(project_id):
                print("❌ Git clone failed")
                return False
        else:
            print("⏭️  Skipping git clone (no repo specified)")

        # Step 3: Load and setup jobs
        print("\n⚙️  Step 3: Setup Jobs")
        jobs_config = self.load_jobs_config()
        if not jobs_config:
            print("❌ Failed to load jobs configuration")
            return False

        job_ids = self.create_or_update_jobs(project_id, jobs_config)
        if not job_ids:
            print("❌ Failed to create jobs")
            return False

        # Step 4: Execute jobs in sequence
        print("\n🚀 Step 4: Execute Jobs")
        for job_key, job_id in job_ids.items():
            job_name = jobs_config["jobs"][job_key]["name"]
            print(f"\n   Running: {job_name}")

            # Skip if already succeeded (unless force rebuild)
            if not self.force_rebuild and self.job_succeeded_recently(project_id, job_id):
                print(f"      ⏭️  Skipping (already succeeded)")
                continue

            # Trigger job
            run_id = self.trigger_job(project_id, job_id)
            if not run_id:
                print(f"      ❌ Failed to trigger job")
                return False

            # Wait for completion
            if not self.wait_for_job_completion(project_id, job_id, run_id):
                print(f"      ❌ Job failed")
                return False

        print("\n" + "=" * 70)
        print("✅ Deployment Complete!")
        print("=" * 70)
        print(f"\nProject ID: {project_id}")
        print(f"All jobs completed successfully")
        print("\nRay cluster information saved to /home/cdsw/ray_cluster_info.json")

        return True


def main():
    """Main entry point."""
    try:
        deployer = CAIDeployer()
        success = deployer.deploy()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠️  Deployment cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
