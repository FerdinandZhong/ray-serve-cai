#!/usr/bin/env python3
"""
Create/update CML jobs from configuration.

This script:
1. Loads jobs_config.yaml
2. Creates or updates all jobs (git_sync, setup_environment, launch_ray_cluster)
3. Sets up parent job dependencies
4. Returns job IDs for next step

Run this in GitHub Actions after project setup completes.
"""

import argparse
import json
import os
import sys
import yaml
import requests
from pathlib import Path
from typing import Dict, Optional, Any


class JobManager:
    """Handle CML job creation and updates."""

    def __init__(self):
        """Initialize CML REST API client."""
        self.cml_host = os.environ.get("CML_HOST")
        self.api_key = os.environ.get("CML_API_KEY")

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

    def list_jobs(self, project_id: str) -> Dict[str, str]:
        """List all jobs in a project."""
        print("📋 Listing existing jobs...")
        result = self.make_request("GET", f"projects/{project_id}/jobs")

        if result:
            jobs = {}
            for job in result.get("jobs", []):
                jobs[job.get("name", "")] = job.get("id", "")
            print(f"   Found {len(jobs)} existing jobs")
            return jobs
        print("   No existing jobs found")
        return {}

    def create_job(
        self,
        project_id: str,
        job_config: Dict[str, Any],
        parent_job_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create a new job in the CML project."""
        print(f"   📝 Creating job: {job_config['name']}")

        job_data = {
            "name": job_config["name"],
            "script": job_config["script"],
            "cpu": job_config.get("cpu", 4),
            "memory": job_config.get("memory", 16),
            "timeout": job_config.get("timeout", 600),
        }

        if parent_job_id:
            job_data["parent_job_id"] = parent_job_id

        result = self.make_request("POST", f"projects/{project_id}/jobs", data=job_data)

        if result:
            job_id = result.get("id")
            print(f"      ✅ Created: {job_id}")
            return job_id
        else:
            print(f"      ❌ Failed to create job")
            return None

    def update_job(
        self, project_id: str, job_id: str, job_config: Dict[str, Any]
    ) -> bool:
        """Update an existing job in the CML project."""
        print(f"   🔄 Updating job: {job_config['name']}")

        job_data = {
            "name": job_config["name"],
            "script": job_config["script"],
            "cpu": job_config.get("cpu", 4),
            "memory": job_config.get("memory", 16),
            "timeout": job_config.get("timeout", 600),
        }

        result = self.make_request(
            "PATCH", f"projects/{project_id}/jobs/{job_id}", data=job_data
        )

        if result is not None:
            print(f"      ✅ Updated: {job_id}")
            return True
        else:
            print(f"      ❌ Failed to update job")
            return False

    def create_or_update_jobs(
        self, project_id: str, jobs_config: Dict
    ) -> Dict[str, str]:
        """Create or update all jobs from configuration."""
        print("\n📋 Creating/Updating Jobs")
        print("-" * 70)

        job_ids = {}
        existing_jobs = self.list_jobs(project_id)

        for job_key, job_config in jobs_config.get("jobs", {}).items():
            job_name = job_config["name"]

            # Find parent job ID if specified
            parent_job_id = None
            parent_key = job_config.get("parent_job_key")
            if parent_key and parent_key in job_ids:
                parent_job_id = job_ids[parent_key]

            # Create or update job
            if job_name in existing_jobs:
                job_id = existing_jobs[job_name]
                if self.update_job(project_id, job_id, job_config):
                    job_ids[job_key] = job_id
            else:
                job_id = self.create_job(project_id, job_config, parent_job_id)
                if job_id:
                    job_ids[job_key] = job_id

        print()
        return job_ids

    def run(self, project_id: str) -> bool:
        """Execute job creation."""
        print("=" * 70)
        print("🚀 Create CML Jobs")
        print("=" * 70)

        # Load configuration
        jobs_config = self.load_jobs_config()
        if not jobs_config:
            print("❌ Failed to load jobs configuration")
            return False

        # Create or update jobs
        job_ids = self.create_or_update_jobs(project_id, jobs_config)
        if not job_ids:
            print("❌ Failed to create jobs")
            return False

        print("=" * 70)
        print("✅ Job Creation Complete!")
        print("=" * 70)
        print(f"\nProject ID: {project_id}")
        print(f"Jobs created/updated: {len(job_ids)}")
        for job_key, job_id in job_ids.items():
            print(f"   {job_key}: {job_id}")

        # Save job IDs for next step
        job_ids_json = json.dumps(job_ids)
        print(f"\n::set-output name=job_ids::{job_ids_json}")

        with open("/tmp/job_ids.json", "w") as f:
            json.dump(job_ids, f)

        return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Create or update CML jobs from configuration"
    )
    parser.add_argument(
        "--project-id", required=True, help="CML project ID"
    )

    args = parser.parse_args()

    try:
        manager = JobManager()
        success = manager.run(args.project_id)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠️  Job creation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
