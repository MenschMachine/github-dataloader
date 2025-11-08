# GitHub Actions Data Downloader

A Python script that downloads GitHub Actions workflow data from your repositories and uploads it to Cloudflare R2 storage.

## Features

- Download workflow runs, jobs, and artifacts from GitHub Actions
- Incremental fetching (only new data since last run)
- Automatic retry logic with exponential backoff
- Configurable repository list
- Saves data locally before uploading
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

1. Log in to Cloudflare Dashboard
2. Go to R2 Object Storage
3. Create a bucket
4. Create API tokens with read/write permissions
5. Note your Account ID from the R2 overview page

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
4. Save data to `github-actions-{timestamp}.json` in the specified output directory
5. Upload the JSON file to your R2 bucket (unless `--local-only` is specified)
6. Update `last_fetch_state.json` for incremental fetching

### Command-Line Options

- `--local-only`: Save data locally only, skip uploading to R2. When this flag is used, R2 environment variables are not required.
- `--output-dir <directory>`: Directory to save downloaded JSON files (default: current directory). The directory will be created if it doesn't exist.

## Data Structure

The output JSON contains:

```json
{
  "downloaded_at": "2024-01-01T12:00:00Z",
  "repositories": [
    {
      "repository": "owner/repo",
      "fetched_at": "2024-01-01T12:00:00Z",
      "workflow_runs": [
        {
          "id": 123456,
          "name": "CI",
          "status": "completed",
          "conclusion": "success",
          "created_at": "2024-01-01T10:00:00Z",
          "jobs": [...],
          "artifacts": [...]
        }
      ]
    }
  ]
}
```

## Incremental Fetching

The script maintains state in `last_fetch_state.json` to track when each repository was last fetched. On subsequent runs, it only fetches data created after the last fetch time.

To force a full re-fetch, delete `last_fetch_state.json`.

## Error Handling

- Network errors: Automatic retry with exponential backoff (4 attempts)
- API rate limits: Built-in delays between requests
- Failed uploads: Retries with exponential backoff
- Missing config: Creates example config file

## Files Generated

- `github-actions-{timestamp}.json` - Downloaded data
- `last_fetch_state.json` - State for incremental fetching
- `config.json` - Repository configuration (you create this)

## Security

**Important**: Never commit sensitive files to version control!

The `.gitignore` file already protects:
- `.env` (contains your credentials)
- `config.json` (contains your repositories)
- `github-actions-*.json` (contains downloaded data)
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

Verify your R2 credentials:
- Check Account ID
- Verify API token permissions
- Ensure bucket name is correct

## License

MIT
