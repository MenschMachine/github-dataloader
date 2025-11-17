# GitHub Actions Data Downloader

A Python script that downloads GitHub Actions workflow data from your repositories and uploads it to Cloudflare R2 storage.

## Features

- Download workflow runs, jobs, and artifacts from GitHub Actions
- Incremental fetching (only new data since last run)
- Automatic retry logic with exponential backoff
- Configurable repository list with glob pattern support
- Saves data locally before uploading
- Optional upload to Cloudflare R2 bucket
- Local-only mode for downloading without cloud upload
- Automatic time-based aggregations (daily, weekly, monthly, yearly)

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

### Combine Options

```bash
python github_actions_downloader.py --local-only --output-dir ./github-data
```

The script will:
1. Read repositories from `config.json`
2. Fetch workflow runs since the last fetch (or all data if first run)
3. For each workflow run, fetch associated jobs and artifacts
4. Save each workflow run to a separate file: `run-{repo}-{run_id}.json`
5. Create a metadata file: `metadata-{timestamp}.json` that references all runs
6. Upload all files to your R2 bucket under `{timestamp}/` (unless `--local-only` is specified)
7. Update `last_fetch_state.json` for incremental fetching

### Command-Line Options

- `--local-only`: Save data locally only, skip uploading to R2. When this flag is used, R2 environment variables are not required.
- `--output-dir <directory>`: Directory to save downloaded JSON files (default: current directory). The directory will be created if it doesn't exist.

## Data Structure

### File Organization

The script creates multiple files per fetch:

1. **Individual workflow run files**: `run-{repository}-{run_id}.json`
   - Contains complete data for a single workflow run
   - Includes jobs and artifacts
   - One file per workflow run

2. **Metadata file**: `metadata-{timestamp}.json`
   - Index of all workflow runs in this fetch
   - References individual run files
   - Contains summary information

3. **Aggregation files**: `agg-{period_type}-{period}.json`
   - Automatically generated for each time period
   - Groups workflow runs by day, week, month, and year
   - Examples: `agg-daily-2024-01-15.json`, `agg-monthly-2024-01.json`
   - Makes it easy to query runs by time period

### Metadata File Format

`metadata-{timestamp}.json`:
```json
{
  "downloaded_at": "2024-01-01T12:00:00Z",
  "timestamp": "20240101_120000",
  "repositories": [
    {
      "repository": "owner/repo",
      "fetched_at": "2024-01-01T12:00:00Z",
      "workflow_run_count": 5,
      "workflow_runs": [
        {
          "run_id": 123456,
          "filename": "run-owner-repo-123456.json",
          "name": "CI",
          "status": "completed",
          "conclusion": "success",
          "created_at": "2024-01-01T10:00:00Z",
          "updated_at": "2024-01-01T10:15:00Z"
        }
      ]
    }
  ]
}
```

### Individual Run File Format

`run-{repository}-{run_id}.json`:
```json
{
  "id": 123456,
  "name": "CI",
  "status": "completed",
  "conclusion": "success",
  "created_at": "2024-01-01T10:00:00Z",
  "jobs": [...],
  "artifacts": [...],
  ... (all GitHub Actions workflow run data)
}
```

### Aggregation File Format

`agg-{period_type}-{period}.json`:
```json
{
  "period_type": "daily",
  "period": "2024-01-15",
  "run_count": 12,
  "workflow_runs": [
    {
      "repository": "owner/repo",
      "run_id": 123456,
      "filename": "run-owner-repo-123456.json",
      "name": "CI",
      "status": "completed",
      "conclusion": "success",
      "created_at": "2024-01-15T10:00:00Z",
      "updated_at": "2024-01-15T10:15:00Z"
    }
  ]
}
```

Aggregation types:
- **Daily**: `agg-daily-YYYY-MM-DD.json` - Groups runs by day
- **Weekly**: `agg-weekly-YYYY-Www.json` - Groups runs by week
- **Monthly**: `agg-monthly-YYYY-MM.json` - Groups runs by month
- **Yearly**: `agg-yearly-YYYY.json` - Groups runs by year

## Incremental Fetching

The script maintains state in `last_fetch_state.json` to track when each repository was last fetched. On subsequent runs, it only fetches data created after the last fetch time.

To force a full re-fetch, delete `last_fetch_state.json`.

## Error Handling

- Network errors: Automatic retry with exponential backoff (4 attempts)
- API rate limits: Built-in delays between requests
- Failed uploads: Retries with exponential backoff
- Missing config: Creates example config file

## Files Generated

- `run-{repository}-{run_id}.json` - Individual workflow run data (one per run)
- `metadata-{timestamp}.json` - Index file referencing all workflow runs
- `agg-{period_type}-{period}.json` - Aggregation files grouped by time period
- `last_fetch_state.json` - State for incremental fetching
- `config.json` - Repository configuration (you create this)

## Security

**Important**: Never commit sensitive files to version control!

The `.gitignore` file already protects:
- `.env` (contains your credentials)
- `config.json` (contains your repositories)
- `run-*.json` (individual workflow run files)
- `metadata-*.json` (metadata index files)
- `agg-*.json` (aggregation files)
- `last_fetch_state.json` (contains state)

The `.env.example` file is safe to commit as it only contains empty variable names.

## Troubleshooting

### Rate Limiting

GitHub API has a rate limit of 5000 requests/hour for authenticated users. The script includes delays to respect this limit.

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
