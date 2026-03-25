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
from typing import Dict, List, Optional


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

    def _topological_order(self, job_configs: Dict) -> List[str]:
        """Return job keys sorted so each parent appears before its children."""
        jobs = job_configs.get("jobs", {})
        order: List[str] = []
        remaining = set(jobs.keys())

        while remaining:
            # Keys whose parent is already placed (or has no parent)
            ready = sorted(
                k for k in remaining
                if jobs[k].get("parent_job_key") is None
                or jobs[k].get("parent_job_key") not in remaining
            )
            if not ready:
                # Circular or unresolvable — append the rest in arbitrary order
                order.extend(sorted(remaining))
                break
            order.extend(ready)
            remaining -= set(ready)

        return order

    def _wait_for_new_run(
        self,
        project_id: str,
        job_id: str,
        job_name: str,
        trigger_epoch: float,
        timeout: int,
    ) -> Optional[str]:
        """
        Poll until a run appears for job_id that was created after trigger_epoch.
        CML auto-triggers child jobs; we just wait for the run to show up.

        Returns the run_id, or None on timeout.
        """
        print(f"   Waiting for CML to auto-trigger: {job_name} ...")
        start = time.time()

        while time.time() - start < timeout:
            result = self.make_request(
                "GET",
                f"projects/{project_id}/jobs/{job_id}/runs",
                params={"page_size": 5},
            )
            if result:
                for run in result.get("runs", []):
                    run_id = run.get("id")
                    created_at = run.get("created_at", "")
                    if not run_id or not created_at:
                        continue

                    # Parse ISO-8601 timestamp and compare with trigger time
                    try:
                        from datetime import datetime, timezone
                        ts = created_at.rstrip("Z")
                        fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in ts else "%Y-%m-%dT%H:%M:%S"
                        dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
                        if dt.timestamp() > trigger_epoch:
                            elapsed = int(time.time() - start)
                            print(f"   [{elapsed}s] New run detected: {run_id}")
                            return run_id
                    except Exception:
                        # If we cannot parse the timestamp, optimistically accept
                        # the most-recent run (first in the list)
                        return run_id

            time.sleep(15)

        print(f"   ❌ Timed out waiting for {job_name} to be auto-triggered ({timeout}s)")
        return None

    def run(
        self, project_id: str, job_ids: Dict[str, str], job_configs: Dict
    ) -> bool:
        """
        Trigger the root job and wait for the entire chain to complete.

        CML auto-triggers child jobs when their parent succeeds; this method
        waits for each step in topological order so GitHub Actions only reports
        success once the cluster is fully deployed.
        """
        print("=" * 70)
        print("🚀 Trigger Ray Cluster Deployment")
        print("=" * 70)

        if self.force_rebuild:
            print(f"   Force rebuild: ✅ ENABLED (will rerun all jobs)\n")
        else:
            print(f"   Force rebuild: ❌ DISABLED (skip already-successful jobs)\n")

        # Determine execution order
        ordered_keys = self._topological_order(job_configs)
        jobs = job_configs.get("jobs", {})

        # Find root job (first in order, has no parent)
        root_job_key = ordered_keys[0] if ordered_keys else None
        if not root_job_key or root_job_key not in job_ids:
            print("❌ Root job not found")
            return False

        root_job_id = job_ids[root_job_key]
        root_job_config = jobs.get(root_job_key, {})
        root_job_name = root_job_config.get("name", root_job_key)

        print(f"🔷 Triggering root job: {root_job_name}")

        # Skip root if it already succeeded (and force_rebuild is off)
        if (
            not self.force_rebuild
            and self.job_succeeded_recently(project_id, root_job_id)
        ):
            print(f"   ✅ Root job already succeeded — skipping trigger\n")
        else:
            run_id = self.trigger_job(project_id, root_job_id)
            if not run_id:
                print(f"   ❌ Failed to trigger root job\n")
                return False

            print(f"   ✅ Root job triggered: {run_id}\n")
            trigger_epoch = time.time()

            timeout = root_job_config.get("timeout", 600)
            if not self.wait_for_job_completion(project_id, root_job_id, run_id, timeout):
                print(f"❌ Root job failed: {root_job_name}")
                return False

            print(f"✅ {root_job_name} complete\n")

        # Wait for every child job in order (CML auto-triggers them)
        for job_key in ordered_keys[1:]:
            job_id = job_ids.get(job_key)
            if not job_id:
                print(f"⚠️  No job_id for '{job_key}' — skipping")
                continue

            job_config = jobs.get(job_key, {})
            job_name = job_config.get("name", job_key)
            timeout = job_config.get("timeout", 1800)

            print(f"🔷 {job_name}")
            # Give CML up to timeout seconds to auto-trigger + complete this job
            new_run_id = self._wait_for_new_run(
                project_id, job_id, job_name, trigger_epoch, timeout
            )
            if not new_run_id:
                return False

            if not self.wait_for_job_completion(project_id, job_id, new_run_id, timeout):
                print(f"❌ {job_name} failed")
                return False

            print(f"✅ {job_name} complete\n")

        print("=" * 70)
        print("✅ Full deployment chain complete!")
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
