# GitHub Actions Data Downloader

A Python script that downloads the last 20 workflow runs from your repositories and creates a current state aggregate file, optionally uploading it to Cloudflare R2 storage.

## Features

- Fetches last 20 workflow runs per repository (configurable)
- Always gets fresh data with current status (no caching)
- Automatic retry logic with exponential backoff
- Configurable repository list with glob pattern support
- Creates a single aggregate file with current state grouped by repository
- Optional upload to Cloudflare R2 bucket
- Local-only mode for downloading without cloud upload

## Prerequisites

- Python 3.7+
- GitHub Personal Access Token with `repo` and `actions:read` scopes
- Cloudflare R2 account with access credentials (optional, only required if uploading to R2)

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create configuration file:
```bash
cp config.example.json config.json
```

3. Edit `config.json` with your repositories:
```json
{
  "repositories": [
    "username/repo1",
    "username/repo2"
  ]
}
```

## Configuration

### Repository Patterns

The `repositories` field in `config.json` supports both exact repository names and glob patterns:

**Exact repository names:**
```json
{
  "repositories": [
    "myorg/backend-api",
    "myorg/frontend-web"
  ]
}
```

**Glob patterns:**
```json
{
  "repositories": [
    "myorg/backend-*",
    "myorg/*-service",
    "myorg/app-*-prod"
  ]
}
```

**Mixed (exact names and patterns):**
```json
{
  "repositories": [
    "myorg/critical-app",
    "myorg/backend-*",
    "myorg/*-service"
  ]
}
```

Glob pattern syntax:
- `*` - Matches any characters (e.g., `myorg/app-*` matches `myorg/app-web`, `myorg/app-api`)
- `?` - Matches a single character (e.g., `myorg/app-?` matches `myorg/app-1`, `myorg/app-a`)
- `[abc]` - Matches any character in brackets (e.g., `myorg/app-[123]` matches `myorg/app-1`, `myorg/app-2`, `myorg/app-3`)

The script will automatically fetch all repositories from the organization and match them against your patterns.

### Exclusion Patterns

You can exclude specific repositories using the optional `exclude` field in `config.json`. This is useful when a glob pattern matches too many repositories and you want to exclude certain ones.

**Excluding specific repositories:**
```json
{
  "repositories": [
    "myorg/backend-*"
  ],
  "exclude": [
    "myorg/backend-test",
    "myorg/backend-deprecated"
  ]
}
```

**Excluding with glob patterns:**
```json
{
  "repositories": [
    "myorg/*"
  ],
  "exclude": [
    "myorg/*-test",
    "myorg/*-deprecated",
    "myorg/temp-*"
  ]
}
```

**Complete example:**
```json
{
  "repositories": [
    "myorg/critical-app",
    "myorg/backend-*",
    "myorg/*-service"
  ],
  "exclude": [
    "myorg/backend-test",
    "myorg/*-deprecated",
    "myorg/temp-*"
  ]
}
```

The exclusion patterns are applied after repository patterns are expanded. Both exact names and glob patterns are supported in the `exclude` field.

### Environment Variables

You can set environment variables either by exporting them in your shell or by creating a `.env` file in the project directory.

**Option 1: Using a .env file (recommended)**

Copy the example file and fill in your credentials:
```bash
cp .env.example .env
```

Then edit `.env` with your values:
```
GITHUB_TOKEN=your_github_personal_access_token
R2_ACCESS_KEY_ID=your_r2_access_key_id
R2_SECRET_ACCESS_KEY=your_r2_secret_access_key
R2_ACCOUNT_ID=your_cloudflare_account_id
R2_BUCKET_NAME=your_r2_bucket_name
```

**Option 2: Export in shell**

**Required for all modes:**
```bash
export GITHUB_TOKEN="your_github_personal_access_token"
```

**Required only for R2 upload (not needed with `--local-only` flag):**
```bash
export R2_ACCESS_KEY_ID="your_r2_access_key_id"
export R2_SECRET_ACCESS_KEY="your_r2_secret_access_key"
export R2_ACCOUNT_ID="your_cloudflare_account_id"
export R2_BUCKET_NAME="your_r2_bucket_name"
```

### GitHub Token

Create a Personal Access Token at: https://github.com/settings/tokens

Required scopes:
- `repo` (for private repositories)
- `actions:read` (for reading Actions data)

### Cloudflare R2

**IMPORTANT:** This script requires **R2 Access Keys**, not Cloudflare API Tokens.

1. Log in to Cloudflare Dashboard
2. Go to R2 Object Storage
3. Create a bucket
4. **Generate R2 Access Keys** (not API tokens):
   - In R2 dashboard, go to "Manage R2 API Tokens"
   - Click "Create API Token"
   - Select permissions (read & write for the bucket)
   - This will generate:
     - **Access Key ID** (32 characters) → Use for `R2_ACCESS_KEY_ID`
     - **Secret Access Key** (43 characters) → Use for `R2_SECRET_ACCESS_KEY`
   - **Note:** Regular Cloudflare API tokens (40+ characters) will NOT work with this script
5. Note your Account ID from the R2 overview page → Use for `R2_ACCOUNT_ID`

The script uses boto3's S3-compatible interface, which requires R2 Access Keys, not general Cloudflare API tokens.

## Usage

### Download and Upload to R2 (default)

```bash
python github_actions_downloader.py
```

### Download to Local Only (skip R2 upload)

```bash
python github_actions_downloader.py --local-only
```

### Save to Custom Directory

```bash
python github_actions_downloader.py --output-dir ./data
```

### Fetch More/Fewer Runs

```bash
python github_actions_downloader.py --run-count 50
```

### Fetch Detailed Job and Artifact Info

```bash
python github_actions_downloader.py --fetch-details
```

By default, the script only fetches basic run information (status, conclusion, etc.) which is very fast (1 API call per repository).

Use `--fetch-details` to also fetch jobs and artifacts for each run. This is much slower (2-3 additional API calls per run).

**Performance comparison:**
- Without `--fetch-details`: ~1 API call per repository
- With `--fetch-details`: ~40-60 API calls per repository (for 20 runs)

### Clear All Files from R2 Bucket

```bash
python github_actions_downloader.py --clear
```

This will:
1. List all files in the R2 bucket
2. Ask for confirmation ("yes")
3. Delete all files from the bucket
4. Exit without downloading anything

**Warning:** This is a destructive operation and cannot be undone!

### Combine Options

```bash
python github_actions_downloader.py --local-only --output-dir ./github-data --run-count 30
```

The script will:
1. Read repositories from `config.json`
2. Fetch the last N workflow runs for each repository (default: 20)
3. For each workflow run, fetch associated jobs and artifacts
4. Create a single aggregate file: `current-state.json` with all data grouped by repository
5. Upload the aggregate to your R2 bucket (unless `--local-only` is specified)

### Command-Line Options

- `--local-only`: Save data locally only, skip uploading to R2. When this flag is used, R2 environment variables are not required.
- `--output-dir <directory>`: Directory to save the aggregate file (default: current directory). The directory will be created if it doesn't exist.
- `--run-count <number>`: Number of recent workflow runs to fetch per repository (default: 20).
- `--fetch-details`: Fetch detailed job and artifact info for each run (default: off). Much slower but provides jobs_count and artifacts_count in the aggregate.
- `--clear`: Delete all files from the R2 bucket and exit. Requires confirmation. Does not download any data.

## Data Structure

### Current State Aggregate

The script creates a single file: `current-state.json`

This file contains:
- Current state of all repositories
- Last N workflow runs per repository
- Run details including status, conclusion, jobs count, artifacts count
- Grouped and organized by repository

### Aggregate File Format

`current-state.json`:
```json
{
  "updated_at": "2025-11-17T10:00:00Z",
  "repository_count": 5,
  "total_runs": 100,
  "repositories": [
    {
      "name": "owner/repo1",
      "fetched_at": "2025-11-17T10:00:00Z",
      "run_count": 20,
      "workflow_runs": [
        {
          "run_id": 123456,
          "name": "CI",
          "status": "completed",
          "conclusion": "success",
          "created_at": "2025-11-17T09:00:00Z",
          "updated_at": "2025-11-17T09:15:00Z",
          "head_branch": "main",
          "head_sha": "abc1234",
          "event": "push",
          "run_number": 42,
          "html_url": "https://github.com/owner/repo1/actions/runs/123456",
          "jobs_count": 3,
          "artifacts_count": 1
        }
      ]
    }
  ]
}
```

## How it Works

### Always Fresh Data

Unlike traditional incremental fetching systems, this script:
- **Always fetches the last N runs** (no time-based filtering)
- **Re-downloads everything every time** (captures status updates)
- **No state tracking or caching** (always reflects current reality)

This means:
- ✅ Captures in-progress builds that complete
- ✅ Captures re-runs and their updated status
- ✅ Captures status changes (queued → in_progress → completed)
- ✅ Simple and predictable behavior
- ✅ Perfect for hourly cron jobs

### Use Case: Hourly Updates

Run this script every hour via cron to:
1. Get current status of all builds across all repos
2. See which builds are in progress
3. Track re-runs and their outcomes
4. Monitor build health in real-time

Example cron entry (runs every hour):
```bash
0 * * * * cd /path/to/github-dataloader && python github_actions_downloader.py
```

## Error Handling

- Network errors: Automatic retry with exponential backoff (4 attempts)
- API rate limits: Built-in delays between requests
- Failed uploads: Retries with exponential backoff
- Missing config: Creates example config file

## Files Generated

- `current-state.json` - Single aggregate file with current state of all repositories
- `config.json` - Repository configuration (you create this)

## Security

**Important**: Never commit sensitive files to version control!

The `.gitignore` file already protects:
- `.env` (contains your credentials)
- `config.json` (contains your repositories)
- `current-state.json` (contains workflow run data)

The `.env.example` file is safe to commit as it only contains empty variable names.

## Troubleshooting

### Rate Limiting

GitHub API has a rate limit of 5000 requests/hour for authenticated users. The script includes delays to respect this limit.

With 20 runs per repo and ~2 API calls per run (jobs + artifacts), you can process approximately:
- ~100 repositories per hour

If you have many repositories or need more runs per repo, consider:
- Reducing `--run-count`
- Running less frequently
- Filtering repositories in config.json

### Authentication Errors

Ensure your GitHub token has the correct scopes:
```bash
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user
```

### R2 Upload Errors

**"Credential access key has length X, should be 32" error:**
- This means you're using a Cloudflare API token instead of R2 Access Keys
- Solution: Generate proper R2 Access Keys as described in the Cloudflare R2 section above
- The Access Key ID must be exactly 32 characters

**Other R2 authentication issues:**
- Check Account ID is correct
- Verify R2 Access Keys have read/write permissions for your bucket
- Ensure bucket name is correct
- Make sure you're using Access Keys, not API tokens

## License

MIT
