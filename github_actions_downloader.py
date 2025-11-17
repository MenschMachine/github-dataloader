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
from typing import Dict, List, Optional
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

    def download_repo_data(self, repo: str, run_count: int = 20) -> Dict:
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

        for i, run in enumerate(workflow_runs):
            run_id = run['id']
            print(f"Fetching details for run {i+1}/{len(workflow_runs)}: {run_id}")

            run_data = {
                **run,
                'jobs': self.get_workflow_jobs(repo, run_id),
                'artifacts': self.get_workflow_artifacts(repo, run_id),
            }

            repo_data['workflow_runs'].append(run_data)

            # Rate limiting: GitHub API allows 5000 requests/hour
            time.sleep(0.1)  # Small delay to be respectful

        print(f"Fetched {len(workflow_runs)} workflow runs with full details")
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


def create_current_state_aggregate(all_repo_data: List[Dict], output_dir: Path) -> str:
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

    return str(agg_file)


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
    args = parser.parse_args()

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
    if not github_token:
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
            repo_data = downloader.download_repo_data(repo, args.run_count)
            all_repo_data.append(repo_data)
        except Exception as e:
            print(f"Error processing repository {repo}: {e}")
            continue

    if not all_repo_data:
        print("\nError: No data was downloaded from any repository")
        sys.exit(1)

    # Create current state aggregate
    aggregate_file = create_current_state_aggregate(all_repo_data, output_dir)

    # Upload aggregate file to R2
    if uploader:
        print(f"\nUploading current state aggregate to R2...")
        try:
            uploader.upload_file(aggregate_file, 'current-state.json')
            print(f"✓ Successfully uploaded current-state.json to R2")
        except Exception as e:
            print(f"✗ Failed to upload: {e}")
            sys.exit(1)

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
