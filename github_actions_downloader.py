#!/usr/bin/env python3
"""
GitHub Actions Data Downloader
Downloads GitHub Actions workflow data and uploads to Cloudflare R2
"""

import os
import json
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional
import time
import requests
import boto3
from pathlib import Path


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


def main():
    """Main execution function"""
    print("GitHub Actions Data Downloader")
    print("=" * 60)

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
    repositories = config.get('repositories', [])

    if not repositories:
        print("No repositories configured in config.json")
        sys.exit(1)

    print(f"Configured repositories: {len(repositories)}")

    # Initialize components
    downloader = GitHubActionsDownloader(github_token)
    uploader = CloudflareR2Uploader(r2_access_key, r2_secret_key,
                                     r2_account_id, r2_bucket_name)
    state_manager = StateManager()

    # Download data for each repository
    all_data = {
        'downloaded_at': datetime.now(timezone.utc).isoformat(),
        'repositories': []
    }

    for repo in repositories:
        try:
            # Get last fetch time for incremental updates
            since = state_manager.get_last_fetch_time(repo)
            if since:
                print(f"Fetching data since {since}")
            else:
                print("First fetch - downloading all data")

            repo_data = downloader.download_repo_data(repo, since)
            all_data['repositories'].append(repo_data)

            # Update state
            state_manager.update_fetch_time(repo)

        except Exception as e:
            print(f"Error processing repository {repo}: {e}")
            continue

    # Save to JSON file
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    output_file = f'github-actions-{timestamp}.json'

    print(f"\nSaving data to {output_file}...")
    with open(output_file, 'w') as f:
        json.dump(all_data, f, indent=2)
    print(f"Saved {os.path.getsize(output_file)} bytes")

    # Upload to R2
    try:
        uploader.upload_file(output_file)
        print("\n✓ Successfully uploaded to Cloudflare R2")
    except Exception as e:
        print(f"\n✗ Failed to upload to R2: {e}")
        sys.exit(1)

    # Save state
    state_manager.save_state()

    print("\n" + "=" * 60)
    print("Download and upload completed successfully!")
    print("=" * 60)


if __name__ == '__main__':
    main()
