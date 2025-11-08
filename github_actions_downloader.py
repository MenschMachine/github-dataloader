#!/usr/bin/env python3
"""
GitHub Actions Data Downloader
Downloads GitHub Actions workflow data and uploads to Cloudflare R2
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

    def _get_all_pages(self, url: str) -> List[Dict]:
        """Fetch all pages from paginated API endpoint"""
        results = []
        while url:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict) and 'workflow_runs' in data:
                results.extend(data['workflow_runs'])
            elif isinstance(data, dict) and 'jobs' in data:
                results.extend(data['jobs'])
            else:
                results.append(data)

            # Get next page from Link header
            link_header = response.headers.get('Link', '')
            url = None
            for link in link_header.split(','):
                if 'rel="next"' in link:
                    url = link[link.find('<')+1:link.find('>')]
                    break

        return results

    def get_org_repositories(self, org: str) -> List[str]:
        """Fetch all repositories for an organization"""
        print(f"Fetching repositories for organization: {org}")
        url = f"{self.api_base}/orgs/{org}/repos"
        params = {'per_page': 100, 'type': 'all'}

        param_str = '&'.join([f"{k}={v}" for k, v in params.items()])
        url = f"{url}?{param_str}"

        repos = self._get_all_pages(url)
        repo_names = [repo['full_name'] for repo in repos]
        print(f"Found {len(repo_names)} repositories in {org}")
        return repo_names

    def get_workflow_runs(self, repo: str, since: Optional[str] = None) -> List[Dict]:
        """Fetch workflow runs for a repository"""
        print(f"Fetching workflow runs for {repo}...")
        url = f"{self.api_base}/repos/{repo}/actions/runs"
        params = {'per_page': 100}

        if since:
            params['created'] = f'>={since}'

        # Add params to URL
        param_str = '&'.join([f"{k}={v}" for k, v in params.items()])
        url = f"{url}?{param_str}"

        runs = self._get_all_pages(url)
        print(f"Found {len(runs)} workflow runs")
        return runs

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

    def download_repo_data(self, repo: str, since: Optional[str] = None) -> Dict:
        """Download all actions data for a repository"""
        print(f"\n{'='*60}")
        print(f"Processing repository: {repo}")
        print(f"{'='*60}")

        workflow_runs = self.get_workflow_runs(repo, since)

        repo_data = {
            'repository': repo,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'workflow_runs': []
        }

        for i, run in enumerate(workflow_runs):
            print(f"Processing run {i+1}/{len(workflow_runs)}: {run['id']}")

            run_data = {
                **run,
                'jobs': self.get_workflow_jobs(repo, run['id']),
                'artifacts': self.get_workflow_artifacts(repo, run['id'])
            }
            repo_data['workflow_runs'].append(run_data)

            # Rate limiting: GitHub API allows 5000 requests/hour
            time.sleep(0.1)  # Small delay to be respectful

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


class StateManager:
    """Manages state for incremental fetching"""

    def __init__(self, state_file: str = 'last_fetch_state.json'):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from file"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load state file: {e}")
                return {}
        return {}

    def get_last_fetch_time(self, repo: str) -> Optional[str]:
        """Get last fetch time for a repository"""
        return self.state.get(repo, {}).get('last_fetch')

    def update_fetch_time(self, repo: str, timestamp: Optional[str] = None):
        """Update last fetch time for a repository"""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()

        if repo not in self.state:
            self.state[repo] = {}

        self.state[repo]['last_fetch'] = timestamp

    def save_state(self):
        """Save state to file"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
            print(f"State saved to {self.state_file}")
        except Exception as e:
            print(f"Failed to save state: {e}")


def load_config(config_file: str = 'config.json') -> Dict:
    """Load configuration from file"""
    if not os.path.exists(config_file):
        print(f"Config file {config_file} not found!")
        print("Creating example config file...")
        example_config = {
            "repositories": [
                "owner/repo1",
                "owner/repo2"
            ]
        }
        with open(config_file, 'w') as f:
            json.dump(example_config, f, indent=2)
        print(f"Please edit {config_file} with your repositories")
        sys.exit(1)

    with open(config_file, 'r') as f:
        return json.load(f)


def expand_repository_globs(patterns: List[str], downloader: GitHubActionsDownloader) -> List[str]:
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

    return expanded_repos


def create_aggregations(metadata: Dict, output_dir: Path) -> List[str]:
    """Create aggregation files grouped by day, week, month, and year"""
    from collections import defaultdict

    print("\nCreating aggregation files...")

    aggregations = {
        'daily': defaultdict(list),
        'weekly': defaultdict(list),
        'monthly': defaultdict(list),
        'yearly': defaultdict(list)
    }

    # Collect all workflow runs from metadata
    for repo in metadata['repositories']:
        for run_info in repo['workflow_runs']:
            if not run_info.get('created_at'):
                continue

            # Parse the created_at timestamp
            created_at = run_info['created_at']
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except Exception:
                continue

            # Create aggregation entry
            agg_entry = {
                'repository': repo['repository'],
                'run_id': run_info['run_id'],
                'filename': run_info['filename'],
                'name': run_info['name'],
                'status': run_info['status'],
                'conclusion': run_info['conclusion'],
                'created_at': run_info['created_at'],
                'updated_at': run_info['updated_at']
            }

            # Daily: YYYY-MM-DD
            day_key = dt.strftime('%Y-%m-%d')
            aggregations['daily'][day_key].append(agg_entry)

            # Weekly: YYYY-Www (ISO week)
            week_key = dt.strftime('%Y-W%W')
            aggregations['weekly'][week_key].append(agg_entry)

            # Monthly: YYYY-MM
            month_key = dt.strftime('%Y-%m')
            aggregations['monthly'][month_key].append(agg_entry)

            # Yearly: YYYY
            year_key = dt.strftime('%Y')
            aggregations['yearly'][year_key].append(agg_entry)

    # Save aggregation files
    saved_files = []

    for period_type, periods in aggregations.items():
        for period_key, runs in periods.items():
            agg_file = output_dir / f'agg-{period_type}-{period_key}.json'

            agg_data = {
                'period_type': period_type,
                'period': period_key,
                'run_count': len(runs),
                'workflow_runs': runs
            }

            with open(agg_file, 'w') as f:
                json.dump(agg_data, f, indent=2)

            saved_files.append(str(agg_file))

    print(f"Created {len(saved_files)} aggregation files:")
    print(f"  - Daily: {len(aggregations['daily'])} files")
    print(f"  - Weekly: {len(aggregations['weekly'])} files")
    print(f"  - Monthly: {len(aggregations['monthly'])} files")
    print(f"  - Yearly: {len(aggregations['yearly'])} files")

    return saved_files


def main():
    """Main execution function"""
    # Load environment variables from .env file if it exists
    load_dotenv()

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Download GitHub Actions data and optionally upload to Cloudflare R2'
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
        help='Directory to save downloaded JSON files (default: current directory)'
    )
    args = parser.parse_args()

    print("GitHub Actions Data Downloader")
    print("=" * 60)

    if args.local_only:
        print("Mode: Local only (R2 upload disabled)")
    else:
        print("Mode: Download and upload to R2")

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

    if not repository_patterns:
        print("No repositories configured in config.json")
        sys.exit(1)

    print(f"Configured repository patterns: {len(repository_patterns)}")

    # Initialize components
    downloader = GitHubActionsDownloader(github_token)

    # Expand glob patterns in repository list
    repositories = expand_repository_globs(repository_patterns, downloader)

    if not repositories:
        print("\nError: No repositories found after expanding patterns")
        sys.exit(1)

    print(f"\nTotal repositories to process: {len(repositories)}")

    uploader = None
    if not args.local_only:
        uploader = CloudflareR2Uploader(r2_access_key, r2_secret_key,
                                         r2_account_id, r2_bucket_name)
    state_manager = StateManager()

    # Download data for each repository
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    metadata = {
        'downloaded_at': datetime.now(timezone.utc).isoformat(),
        'timestamp': timestamp,
        'repositories': []
    }

    metadata_file = output_dir / f'metadata-{timestamp}.json'
    total_runs_saved = 0
    upload_errors = []

    for repo in repositories:
        try:
            # Get last fetch time for incremental updates
            since = state_manager.get_last_fetch_time(repo)
            if since:
                print(f"Fetching data since {since}")
            else:
                print("First fetch - downloading all data")

            repo_data = downloader.download_repo_data(repo, since)

            # Save individual workflow run files
            repo_slug = repo.replace('/', '-')
            run_files = []

            for run in repo_data['workflow_runs']:
                run_id = run['id']
                run_filename = f'run-{repo_slug}-{run_id}.json'
                run_filepath = output_dir / run_filename

                # Save run file immediately
                with open(run_filepath, 'w') as f:
                    json.dump(run, f, indent=2)

                # Upload immediately if not in local-only mode
                if uploader:
                    try:
                        uploader.upload_file(str(run_filepath), f"{timestamp}/{run_filename}")
                    except Exception as e:
                        error_msg = f"Failed to upload {run_filename}: {e}"
                        print(f"  ✗ {error_msg}")
                        upload_errors.append(error_msg)

                run_files.append({
                    'run_id': run_id,
                    'filename': run_filename,
                    'name': run.get('name', 'Unknown'),
                    'status': run.get('status'),
                    'conclusion': run.get('conclusion'),
                    'created_at': run.get('created_at'),
                    'updated_at': run.get('updated_at')
                })
                total_runs_saved += 1

            print(f"Saved {len(run_files)} workflow run files for {repo}")

            # Add repository metadata
            metadata['repositories'].append({
                'repository': repo,
                'fetched_at': repo_data['fetched_at'],
                'workflow_run_count': len(run_files),
                'workflow_runs': run_files
            })

            # Save metadata file after each repository
            print(f"Updating metadata file...")
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            # Upload metadata file immediately if not in local-only mode
            if uploader:
                try:
                    uploader.upload_file(str(metadata_file), f"{timestamp}/{metadata_file.name}")
                except Exception as e:
                    error_msg = f"Failed to upload {metadata_file.name}: {e}"
                    print(f"  ✗ {error_msg}")
                    upload_errors.append(error_msg)

            # Update state after successful processing
            state_manager.update_fetch_time(repo)
            state_manager.save_state()

        except Exception as e:
            print(f"Error processing repository {repo}: {e}")
            continue

    print(f"\nTotal workflow run files saved: {total_runs_saved}")
    print(f"Metadata file: {metadata_file.name}")

    # Create aggregation files
    aggregation_files = create_aggregations(metadata, output_dir)

    # Upload aggregation files immediately
    if uploader:
        print(f"\nUploading {len(aggregation_files)} aggregation files to R2...")
        for i, agg_file in enumerate(aggregation_files, 1):
            try:
                file_name = Path(agg_file).name
                print(f"  [{i}/{len(aggregation_files)}] Uploading {file_name}...")
                uploader.upload_file(agg_file, f"{timestamp}/{file_name}")
            except Exception as e:
                error_msg = f"Failed to upload {file_name}: {e}"
                print(f"  ✗ {error_msg}")
                upload_errors.append(error_msg)

    # Report results
    if args.local_only:
        print("\nLocal-only mode:")
        print(f"  Data saved to: {output_dir.absolute()}")
        print(f"  - Workflow runs: {total_runs_saved} files")
        print(f"  - Metadata: {metadata_file.name}")
        print(f"  - Aggregations: {len(aggregation_files)} files")
    else:
        if upload_errors:
            print(f"\n✗ Upload completed with {len(upload_errors)} errors:")
            for error in upload_errors:
                print(f"  - {error}")
            sys.exit(1)
        else:
            print(f"\n✓ Successfully uploaded all files to R2")
            print(f"  R2 path: {timestamp}/")
            print(f"  - Workflow runs: {total_runs_saved} files")
            print(f"  - Metadata: 1 file")
            print(f"  - Aggregations: {len(aggregation_files)} files")

    print("\n" + "=" * 60)
    if args.local_only:
        print("Download completed successfully!")
    else:
        print("Download and upload completed successfully!")
    print("=" * 60)


if __name__ == '__main__':
    main()
