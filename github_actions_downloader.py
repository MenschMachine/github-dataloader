#!/usr/bin/env python3
"""
GitHub Actions Data Downloader
Downloads last 20 workflow runs per repository and creates a current state aggregate
"""

import os
import json
import sys
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import time
import requests
import boto3
from pathlib import Path
from dotenv import load_dotenv
from fnmatch import fnmatch


class GitHubActionsDownloader:
    """Handles downloading GitHub Actions data"""

    def __init__(self, github_token: str):
        self.github_token = github_token
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'token {github_token}',
            'Accept': 'application/vnd.github.v3+json'
        })
        self.api_base = 'https://api.github.com'

    def _make_request(self, url: str, max_retries: int = 4) -> Optional[Dict]:
        """Make API request with retry logic"""
        for attempt in range(max_retries):
            try:
                response = self.session.get(url)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s, 8s
                    print(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}")
                    print(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Request failed after {max_retries} attempts: {e}")
                    raise
        return None

    def get_org_repositories(self, org: str) -> List[str]:
        """Fetch all repositories for an organization"""
        print(f"Fetching repositories for organization: {org}")
        url = f"{self.api_base}/orgs/{org}/repos?per_page=100"

        repos = []
        while url:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            repos.extend([repo['full_name'] for repo in data])

            # Get next page from Link header
            link_header = response.headers.get('Link', '')
            url = None
            for link in link_header.split(','):
                if 'rel="next"' in link:
                    url = link[link.find('<')+1:link.find('>')]
                    break

        print(f"Found {len(repos)} repositories in {org}")
        return repos

    def get_last_workflow_runs(self, repo: str, count: int = 20) -> List[Dict]:
        """Fetch last N workflow runs for a repository"""
        print(f"Fetching last {count} workflow runs for {repo}...")
        url = f"{self.api_base}/repos/{repo}/actions/runs?per_page={count}&page=1"

        try:
            data = self._make_request(url)
            if data and 'workflow_runs' in data:
                runs = data['workflow_runs']
                print(f"Found {len(runs)} workflow runs")
                return runs
            return []
        except Exception as e:
            print(f"Failed to fetch workflow runs for {repo}: {e}")
            return []

    def get_workflow_jobs(self, repo: str, run_id: int) -> List[Dict]:
        """Fetch jobs for a specific workflow run"""
        url = f"{self.api_base}/repos/{repo}/actions/runs/{run_id}/jobs"
        try:
            data = self._make_request(url)
            return data.get('jobs', []) if data else []
        except Exception as e:
            print(f"Failed to fetch jobs for run {run_id}: {e}")
            return []

    def get_workflow_artifacts(self, repo: str, run_id: int) -> List[Dict]:
        """Fetch artifacts for a specific workflow run"""
        url = f"{self.api_base}/repos/{repo}/actions/runs/{run_id}/artifacts"
        try:
            data = self._make_request(url)
            return data.get('artifacts', []) if data else []
        except Exception as e:
            print(f"Failed to fetch artifacts for run {run_id}: {e}")
            return []

    def download_repo_data(self, repo: str, run_count: int = 20, fetch_details: bool = False) -> Dict:
        """Download last N workflow runs for a repository"""
        print(f"\n{'='*60}")
        print(f"Processing repository: {repo}")
        print(f"{'='*60}")

        workflow_runs = self.get_last_workflow_runs(repo, run_count)

        repo_data = {
            'repository': repo,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'workflow_runs': []
        }

        if fetch_details:
            print(f"Fetching detailed info (jobs & artifacts) for {len(workflow_runs)} runs...")
            for i, run in enumerate(workflow_runs):
                run_id = run['id']
                print(f"  [{i+1}/{len(workflow_runs)}] Fetching details for run {run_id}")

                run_data = {
                    **run,
                    'jobs': self.get_workflow_jobs(repo, run_id),
                    'artifacts': self.get_workflow_artifacts(repo, run_id),
                }

                repo_data['workflow_runs'].append(run_data)

                # Rate limiting: GitHub API allows 5000 requests/hour
                time.sleep(0.1)  # Small delay to be respectful

            print(f"Fetched {len(workflow_runs)} workflow runs with full details")
        else:
            # Just use the basic run info (no extra API calls)
            repo_data['workflow_runs'] = workflow_runs
            print(f"Fetched {len(workflow_runs)} workflow runs (basic info only)")

        return repo_data


class CloudflareR2Uploader:
    """Handles uploading to Cloudflare R2"""

    def __init__(self, access_key_id: str, secret_access_key: str,
                 account_id: str, bucket_name: str):
        self.bucket_name = bucket_name

        # Cloudflare R2 endpoint
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

        self.s3_client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name='auto'  # R2 uses 'auto' as region
        )

    def upload_file(self, file_path: str, object_name: Optional[str] = None,
                   max_retries: int = 4) -> bool:
        """Upload file to R2 with retry logic"""
        if object_name is None:
            object_name = os.path.basename(file_path)

        for attempt in range(max_retries):
            try:
                print(f"Uploading {file_path} to R2 bucket {self.bucket_name}...")
                self.s3_client.upload_file(file_path, self.bucket_name, object_name)
                print(f"Successfully uploaded to {object_name}")
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"Upload failed (attempt {attempt + 1}/{max_retries}): {e}")
                    print(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Upload failed after {max_retries} attempts: {e}")
                    raise
        return False

    def object_exists(self, object_name: str) -> bool:
        """Check if an object exists in R2"""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=object_name)
            return True
        except Exception:
            return False

    def download_object(self, object_name: str) -> Optional[bytes]:
        """Download an object from R2 and return its content"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=object_name)
            return response['Body'].read()
        except Exception as e:
            print(f"Failed to download {object_name}: {e}")
            return None

    def list_all_objects(self) -> List[str]:
        """List all objects in the R2 bucket"""
        objects = []
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name)

            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects.append(obj['Key'])

            return objects
        except Exception as e:
            print(f"Failed to list objects: {e}")
            raise

    def delete_all_objects(self) -> int:
        """Delete all objects from the R2 bucket"""
        objects = self.list_all_objects()

        if not objects:
            print("Bucket is already empty")
            return 0

        print(f"Found {len(objects)} object(s) in bucket {self.bucket_name}")
        print("\nDeleting all objects...")

        deleted_count = 0
        failed_count = 0

        for i, obj_key in enumerate(objects, 1):
            try:
                print(f"  [{i}/{len(objects)}] Deleting {obj_key}...")
                self.s3_client.delete_object(Bucket=self.bucket_name, Key=obj_key)
                deleted_count += 1
            except Exception as e:
                print(f"  ✗ Failed to delete {obj_key}: {e}")
                failed_count += 1

        print(f"\nDeleted {deleted_count} object(s)")
        if failed_count > 0:
            print(f"Failed to delete {failed_count} object(s)")

        return deleted_count


def load_config(config_file: str = 'config.json') -> Dict:
    """Load configuration from file"""
    if not os.path.exists(config_file):
        print(f"Config file {config_file} not found!")
        print("Creating example config file...")
        example_config = {
            "repositories": [
                "owner/repo1",
                "owner/repo2"
            ],
            "exclude": [
                "owner/*-test",
                "owner/temp-*"
            ]
        }
        with open(config_file, 'w') as f:
            json.dump(example_config, f, indent=2)
        print(f"Please edit {config_file} with your repositories")
        sys.exit(1)

    with open(config_file, 'r') as f:
        return json.load(f)


def expand_repository_globs(patterns: List[str], downloader: GitHubActionsDownloader,
                           exclude_patterns: Optional[List[str]] = None) -> List[str]:
    """Expand glob patterns in repository list to actual repository names"""
    expanded_repos = []
    orgs_cache = {}  # Cache org repos to avoid multiple API calls

    for pattern in patterns:
        # Check if pattern contains wildcards
        if '*' in pattern or '?' in pattern or '[' in pattern:
            print(f"\nExpanding glob pattern: {pattern}")

            # Extract org from pattern (everything before /)
            if '/' not in pattern:
                print(f"  Warning: Invalid pattern '{pattern}' - must be in format 'org/repo-pattern'")
                continue

            org = pattern.split('/')[0]

            # Fetch org repos if not cached
            if org not in orgs_cache:
                try:
                    orgs_cache[org] = downloader.get_org_repositories(org)
                except Exception as e:
                    print(f"  Error fetching repositories for org '{org}': {e}")
                    continue

            # Match repos against pattern
            matched_repos = [repo for repo in orgs_cache[org] if fnmatch(repo, pattern)]

            if matched_repos:
                print(f"  Matched {len(matched_repos)} repositories:")
                for repo in matched_repos:
                    print(f"    - {repo}")
                expanded_repos.extend(matched_repos)
            else:
                print(f"  Warning: No repositories matched pattern '{pattern}'")
        else:
            # Not a glob pattern, add as-is
            expanded_repos.append(pattern)

    # Apply exclusion patterns if provided
    if exclude_patterns:
        print(f"\nApplying {len(exclude_patterns)} exclusion pattern(s)...")
        initial_count = len(expanded_repos)
        excluded_repos = []

        for exclude_pattern in exclude_patterns:
            for repo in expanded_repos[:]:  # Use slice copy to allow modification during iteration
                # Check if repo matches exclusion pattern (supports globs)
                if fnmatch(repo, exclude_pattern):
                    if repo in expanded_repos:
                        expanded_repos.remove(repo)
                        excluded_repos.append(repo)

        if excluded_repos:
            print(f"  Excluded {len(excluded_repos)} repositories:")
            for repo in excluded_repos:
                print(f"    - {repo}")
        else:
            print(f"  No repositories matched exclusion patterns")

    return expanded_repos


def aggregates_differ(new_aggregate: Dict, old_content: bytes) -> bool:
    """Compare two aggregates, ignoring timestamp fields that always change"""
    try:
        old_aggregate = json.loads(old_content.decode('utf-8'))

        # Create copies without timestamp fields
        new_copy = {k: v for k, v in new_aggregate.items() if k != 'updated_at'}
        old_copy = {k: v for k, v in old_aggregate.items() if k != 'updated_at'}

        # Also remove fetched_at from each repository (changes every run)
        if 'repositories' in new_copy:
            new_repos = []
            for repo in new_copy['repositories']:
                repo_copy = {k: v for k, v in repo.items() if k != 'fetched_at'}
                new_repos.append(repo_copy)
            new_copy['repositories'] = new_repos

        if 'repositories' in old_copy:
            old_repos = []
            for repo in old_copy['repositories']:
                repo_copy = {k: v for k, v in repo.items() if k != 'fetched_at'}
                old_repos.append(repo_copy)
            old_copy['repositories'] = old_repos

        # Compare the content
        return new_copy != old_copy
    except Exception as e:
        print(f"Error comparing aggregates: {e}")
        # If we can't compare, assume they differ (safer to upload)
        return True


class AdaptivePollingState:
    """Manages adaptive polling state for smart backoff"""

    def __init__(self, state_file: str = 'polling_state.json'):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from file"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not load polling state: {e}")
                return self._default_state()
        return self._default_state()

    def _default_state(self) -> Dict:
        """Return default state"""
        return {
            'last_run_time': None,
            'last_change_time': None,
            'consecutive_unchanged': 0
        }

    def should_run(self) -> Tuple[bool, str]:
        """Determine if we should run based on backoff logic"""
        if self.state['last_run_time'] is None:
            return True, "First run"

        last_run = datetime.fromisoformat(self.state['last_run_time'])
        now = datetime.now(timezone.utc)
        elapsed_seconds = (now - last_run).total_seconds()

        # Backoff intervals based on consecutive unchanged runs
        unchanged = self.state['consecutive_unchanged']
        if unchanged == 0:
            interval = 60  # 1 minute
        elif unchanged == 1:
            interval = 120  # 2 minutes
        elif unchanged == 2:
            interval = 300  # 5 minutes
        else:
            interval = 600  # 10 minutes

        if elapsed_seconds < interval:
            wait_time = int(interval - elapsed_seconds)
            return False, f"Backing off: wait {wait_time}s more (unchanged: {unchanged})"

        return True, f"Ready to run (unchanged: {unchanged})"

    def record_run(self, changed: bool):
        """Record that a run completed and whether changes were found"""
        now = datetime.now(timezone.utc).isoformat()
        self.state['last_run_time'] = now

        if changed:
            self.state['last_change_time'] = now
            self.state['consecutive_unchanged'] = 0
        else:
            self.state['consecutive_unchanged'] += 1

        self._save_state()

    def _save_state(self):
        """Save state to file"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save polling state: {e}")


def create_current_state_aggregate(all_repo_data: List[Dict], output_dir: Path) -> Tuple[str, Dict]:
    """Create a single aggregate file with current state of all repositories"""
    print("\nCreating current state aggregate...")

    aggregate = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'repository_count': len(all_repo_data),
        'total_runs': sum(len(repo['workflow_runs']) for repo in all_repo_data),
        'repositories': []
    }

    for repo_data in all_repo_data:
        repo_info = {
            'name': repo_data['repository'],
            'fetched_at': repo_data['fetched_at'],
            'run_count': len(repo_data['workflow_runs']),
            'workflow_runs': []
        }

        for run in repo_data['workflow_runs']:
            # Extract only the essential info for the aggregate
            run_summary = {
                'run_id': run['id'],
                'name': run.get('name', 'Unknown'),
                'status': run.get('status'),
                'conclusion': run.get('conclusion'),
                'created_at': run.get('created_at'),
                'updated_at': run.get('updated_at'),
                'head_branch': run.get('head_branch'),
                'head_sha': run.get('head_sha', '')[:7],  # Short SHA
                'event': run.get('event'),
                'run_number': run.get('run_number'),
                'html_url': run.get('html_url'),
                'jobs_count': len(run.get('jobs', [])),
                'artifacts_count': len(run.get('artifacts', []))
            }
            repo_info['workflow_runs'].append(run_summary)

        aggregate['repositories'].append(repo_info)

    # Save aggregate file
    agg_file = output_dir / 'current-state.json'
    with open(agg_file, 'w') as f:
        json.dump(aggregate, f, indent=2)

    print(f"Created current state aggregate:")
    print(f"  - {aggregate['repository_count']} repositories")
    print(f"  - {aggregate['total_runs']} total workflow runs")
    print(f"  - File: {agg_file.name}")

    return str(agg_file), aggregate


def main():
    """Main execution function"""
    # Load environment variables from .env file if it exists
    load_dotenv()

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Download last 20 workflow runs per repository and create current state aggregate'
    )
    parser.add_argument(
        '--local-only',
        action='store_true',
        help='Save data locally only, skip uploading to R2'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='.',
        help='Directory to save the aggregate file (default: current directory)'
    )
    parser.add_argument(
        '--run-count',
        type=int,
        default=20,
        help='Number of recent workflow runs to fetch per repository (default: 20)'
    )
    parser.add_argument(
        '--clear',
        action='store_true',
        help='Delete all files from R2 bucket and exit'
    )
    parser.add_argument(
        '--fetch-details',
        action='store_true',
        help='Fetch detailed job and artifact info for each run (slower, more API calls)'
    )
    parser.add_argument(
        '--no-adaptive-polling',
        action='store_true',
        help='Disable adaptive polling (always run, ignore backoff)'
    )
    args = parser.parse_args()

    # Check adaptive polling state (unless disabled or in clear mode)
    if not args.no_adaptive_polling and not args.clear:
        polling_state = AdaptivePollingState()
        should_run, reason = polling_state.should_run()
        if not should_run:
            print(f"⏸️  Skipping run: {reason}")
            sys.exit(0)
        print(f"▶️  {reason}")

    print("GitHub Actions Current State Downloader")
    print("=" * 60)

    if args.local_only:
        print("Mode: Local only (R2 upload disabled)")
    else:
        print("Mode: Download and upload to R2")

    print(f"Fetching last {args.run_count} runs per repository")

    # Create output directory if it doesn't exist
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir.absolute()}")
    print()

    # Load environment variables
    github_token = os.getenv('GITHUB_TOKEN')
    r2_access_key = os.getenv('R2_ACCESS_KEY_ID')
    r2_secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    r2_account_id = os.getenv('R2_ACCOUNT_ID')
    r2_bucket_name = os.getenv('R2_BUCKET_NAME')

    # Validate environment variables
    missing_vars = []

    # GitHub token not required for --clear mode
    if not args.clear and not github_token:
        missing_vars.append('GITHUB_TOKEN')

    # R2 credentials only required if not in local-only mode
    if not args.local_only:
        if not r2_access_key:
            missing_vars.append('R2_ACCESS_KEY_ID')
        if not r2_secret_key:
            missing_vars.append('R2_SECRET_ACCESS_KEY')
        if not r2_account_id:
            missing_vars.append('R2_ACCOUNT_ID')
        if not r2_bucket_name:
            missing_vars.append('R2_BUCKET_NAME')

    if missing_vars:
        print("Error: Missing required environment variables:")
        for var in missing_vars:
            print(f"  - {var}")
        sys.exit(1)

    # Handle --clear mode: delete all files from R2 and exit
    if args.clear:
        print("\n" + "!" * 60)
        print("WARNING: This will delete ALL files from the R2 bucket!")
        print(f"Bucket: {r2_bucket_name}")
        print("!" * 60)

        response = input("\nType 'yes' to confirm deletion: ")
        if response.lower() != 'yes':
            print("Deletion cancelled.")
            sys.exit(0)

        print()
        uploader = CloudflareR2Uploader(r2_access_key, r2_secret_key,
                                         r2_account_id, r2_bucket_name)
        deleted_count = uploader.delete_all_objects()

        print("\n" + "=" * 60)
        print(f"✓ Deleted {deleted_count} file(s) from R2 bucket")
        print("=" * 60)
        sys.exit(0)

    # Load configuration
    config = load_config()
    repository_patterns = config.get('repositories', [])
    exclude_patterns = config.get('exclude', [])

    if not repository_patterns:
        print("No repositories configured in config.json")
        sys.exit(1)

    print(f"Configured repository patterns: {len(repository_patterns)}")
    if exclude_patterns:
        print(f"Configured exclusion patterns: {len(exclude_patterns)}")

    # Initialize components
    downloader = GitHubActionsDownloader(github_token)

    # Expand glob patterns in repository list
    repositories = expand_repository_globs(repository_patterns, downloader, exclude_patterns)

    if not repositories:
        print("\nError: No repositories found after expanding patterns")
        sys.exit(1)

    print(f"\nTotal repositories to process: {len(repositories)}")

    uploader = None
    if not args.local_only:
        uploader = CloudflareR2Uploader(r2_access_key, r2_secret_key,
                                         r2_account_id, r2_bucket_name)

    # Download data for each repository
    all_repo_data = []

    for repo in repositories:
        try:
            repo_data = downloader.download_repo_data(repo, args.run_count, args.fetch_details)
            all_repo_data.append(repo_data)
        except Exception as e:
            print(f"Error processing repository {repo}: {e}")
            continue

    if not all_repo_data:
        print("\nError: No data was downloaded from any repository")
        sys.exit(1)

    # Create current state aggregate
    aggregate_file, aggregate_data = create_current_state_aggregate(all_repo_data, output_dir)

    # Upload aggregate file to R2
    content_changed = False
    if uploader:
        print(f"\nChecking if upload is needed...")

        # Check if file exists in R2 and compare content
        should_upload = True
        if uploader.object_exists('current-state.json'):
            print("Existing current-state.json found in R2, comparing...")
            old_content = uploader.download_object('current-state.json')

            if old_content:
                if aggregates_differ(aggregate_data, old_content):
                    print("Content has changed, upload needed")
                    should_upload = True
                    content_changed = True
                else:
                    print("Content unchanged, skipping upload")
                    should_upload = False
                    content_changed = False
        else:
            print("No existing file in R2, upload needed")
            content_changed = True

        if should_upload:
            print(f"\nUploading current state aggregate to R2...")
            try:
                uploader.upload_file(aggregate_file, 'current-state.json')
                print(f"✓ Successfully uploaded current-state.json to R2")
            except Exception as e:
                print(f"✗ Failed to upload: {e}")
                sys.exit(1)
        else:
            print(f"✓ No upload needed - current-state.json is up to date")
    else:
        # In local-only mode, always consider content as changed
        content_changed = True

    # Record this run in adaptive polling state
    if not args.no_adaptive_polling and not args.clear:
        polling_state.record_run(content_changed)
        if content_changed:
            print("📊 Adaptive polling: changes detected, reset backoff")
        else:
            print(f"📊 Adaptive polling: no changes, backoff increased")

    # Report results
    print("\n" + "=" * 60)
    if args.local_only:
        print("Download completed successfully!")
        print(f"  Current state: {aggregate_file}")
    else:
        print("Download and upload completed successfully!")
        print(f"  Current state uploaded to R2: current-state.json")
    print("=" * 60)


if __name__ == '__main__':
    main()
