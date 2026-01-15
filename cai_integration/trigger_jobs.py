#!/usr/bin/env python3
"""
Trigger and monitor CML jobs for Ray cluster deployment.

This script:
1. Gets job IDs from configuration
2. Checks if jobs already succeeded (skip if not force rebuild)
3. Triggers jobs in sequence with proper dependencies
4. Waits for each job to complete with status updates

Run this in GitHub Actions after job creation completes.
"""

import argparse
import json
import os
import sys
import time
import requests
from typing import Dict, Optional


class JobTrigger:
    """Handle CML job triggering and monitoring."""

    def __init__(self):
        """Initialize CML REST API client."""
        self.cml_host = os.environ.get("CML_HOST")
        self.api_key = os.environ.get("CML_API_KEY")
        self.force_rebuild = os.environ.get("FORCE_REBUILD", "").lower() == "true"

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

    def get_job_ids_by_name(self, project_id: str, job_names: list) -> Dict[str, str]:
        """Get job IDs by job names from CML API."""
        print(f"🔍 Looking up job IDs from CML...")

        result = self.make_request("GET", f"projects/{project_id}/jobs")
        if not result:
            print("❌ Failed to list jobs")
            return {}

        jobs = result.get("jobs", [])
        job_id_map = {}

        for job in jobs:
            job_name = job.get("name", "")
            job_id = job.get("id", "")
            if job_name and job_id:
                job_id_map[job_name] = job_id

        # Map from job config keys to job IDs
        job_ids = {}
        for key, name in job_names.items():
            if name in job_id_map:
                job_ids[key] = job_id_map[name]
                print(f"   ✅ Found {key}: {job_id_map[name]}")
            else:
                print(f"   ⚠️  Not found: {name}")

        return job_ids

    def job_succeeded_recently(self, project_id: str, job_id: str) -> bool:
        """Check if job has completed successfully recently."""
        result = self.make_request(
            "GET", f"projects/{project_id}/jobs/{job_id}/runs", params={"page_size": 5}
        )

        if result:
            runs = result.get("runs", [])
            if runs:
                status = runs[0].get("status", "").lower()
                if status in ["succeeded", "success"]:
                    return True

        return False

    def trigger_job(self, project_id: str, job_id: str) -> Optional[str]:
        """Trigger a job execution."""
        result = self.make_request("POST", f"projects/{project_id}/jobs/{job_id}/runs")

        if result:
            run_id = result.get("id")
            return run_id

        return None

    def wait_for_job_completion(
        self, project_id: str, job_id: str, run_id: str, timeout: int = 1800
    ) -> bool:
        """Wait for job run to complete with status updates."""
        print(f"   ⏳ Waiting for job to complete...")
        print(f"      (timeout: {timeout}s)\n")

        start_time = time.time()
        last_status = None

        while time.time() - start_time < timeout:
            result = self.make_request(
                "GET", f"projects/{project_id}/jobs/{job_id}/runs/{run_id}"
            )

            if result:
                status = result.get("status", "unknown").lower()

                # Only print on status change
                if status != last_status:
                    elapsed = int(time.time() - start_time)
                    print(f"      [{elapsed}s] Status: {status}")
                    last_status = status

                # Success statuses
                if status in ["succeeded", "success", "engine_succeeded"]:
                    print(f"   ✅ Job completed successfully\n")
                    return True

                # Failure statuses (any failure state should stop immediately)
                elif status in ["failed", "error", "engine_failed", "killed", "stopped", "timedout"]:
                    print(f"   ❌ Job failed with status: {status}\n")
                    return False

            time.sleep(10)

        elapsed = int(time.time() - start_time)
        print(f"   ❌ Job timeout ({elapsed}s / {timeout}s)\n")
        return False

    def run(
        self, project_id: str, job_ids: Dict[str, str], job_configs: Dict
    ) -> bool:
        """Execute job triggering and monitoring."""
        print("=" * 70)
        print("🚀 Trigger Ray Cluster Deployment Jobs")
        print("=" * 70)

        if self.force_rebuild:
            print(f"   Force rebuild: ✅ ENABLED (will rerun all jobs)\n")
        else:
            print(f"   Force rebuild: ❌ DISABLED (skip already-successful jobs)\n")

        # Job execution order (respecting dependencies)
        job_sequence = ["git_sync", "setup_environment", "launch_ray_cluster"]

        for job_key in job_sequence:
            if job_key not in job_ids:
                print(f"⚠️  Job not found: {job_key}")
                continue

            job_id = job_ids[job_key]
            job_config = job_configs.get("jobs", {}).get(job_key, {})
            job_name = job_config.get("name", job_key)

            print(f"🔷 Running: {job_name}")

            # Check if already succeeded
            if (
                not self.force_rebuild
                and self.job_succeeded_recently(project_id, job_id)
            ):
                print(f"   ✅ Already succeeded, skipping\n")
                continue

            # Trigger job
            run_id = self.trigger_job(project_id, job_id)
            if not run_id:
                print(f"   ❌ Failed to trigger job\n")
                return False

            print(f"   ✅ Job triggered: {run_id}")

            # Wait for completion
            timeout = job_config.get("timeout", 1800)
            if not self.wait_for_job_completion(project_id, job_id, run_id, timeout):
                print(f"❌ Job failed: {job_name}")
                return False

        print("=" * 70)
        print("✅ All Deployment Jobs Complete!")
        print("=" * 70)

        return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Trigger and monitor CML jobs")
    parser.add_argument("--project-id", required=True, help="CML project ID")
    parser.add_argument(
        "--jobs-config",
        default="cai_integration/jobs_config.yaml",
        help="Path to jobs config YAML",
    )

    args = parser.parse_args()

    try:
        import yaml

        # Load job configuration
        with open(args.jobs_config) as f:
            job_configs = yaml.safe_load(f)

        # Create trigger instance
        trigger = JobTrigger()

        # Get job IDs by querying CML API with job names from config
        job_names = {
            key: config["name"]
            for key, config in job_configs.get("jobs", {}).items()
        }
        job_ids = trigger.get_job_ids_by_name(args.project_id, job_names)

        if not job_ids:
            print("❌ No jobs found in project")
            sys.exit(1)

        # Trigger jobs
        success = trigger.run(args.project_id, job_ids, job_configs)
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n⚠️  Job execution cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
