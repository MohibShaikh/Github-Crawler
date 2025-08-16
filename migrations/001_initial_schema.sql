-- Initial schema for GitHub repository crawler
-- Designed for efficient updates and future extensibility

-- Core repositories table
CREATE TABLE IF NOT EXISTS repositories (
    id SERIAL PRIMARY KEY,
    repo_id BIGINT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    owner_login TEXT NOT NULL,
    owner_type TEXT NOT NULL, -- 'User' or 'Organization'
    stars_count INTEGER NOT NULL DEFAULT 0,
    forks_count INTEGER NOT NULL DEFAULT 0,
    watchers_count INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    description TEXT,
    is_private BOOLEAN NOT NULL DEFAULT false,
    is_fork BOOLEAN NOT NULL DEFAULT false,
    is_archived BOOLEAN NOT NULL DEFAULT false,
    is_disabled BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    pushed_at TIMESTAMP WITH TIME ZONE,
    crawled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for efficient querying and updates
CREATE INDEX IF NOT EXISTS idx_repositories_repo_id ON repositories(repo_id);
CREATE INDEX IF NOT EXISTS idx_repositories_owner_login ON repositories(owner_login);
CREATE INDEX IF NOT EXISTS idx_repositories_stars_count ON repositories(stars_count);
CREATE INDEX IF NOT EXISTS idx_repositories_language ON repositories(language);
CREATE INDEX IF NOT EXISTS idx_repositories_crawled_at ON repositories(crawled_at);
CREATE INDEX IF NOT EXISTS idx_repositories_last_modified ON repositories(last_modified);
CREATE INDEX IF NOT EXISTS idx_repositories_updated_at ON repositories(updated_at);

-- Future extensibility: Issues table
CREATE TABLE IF NOT EXISTS issues (
    id SERIAL PRIMARY KEY,
    issue_id BIGINT UNIQUE NOT NULL,
    repo_id BIGINT NOT NULL REFERENCES repositories(repo_id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    state TEXT NOT NULL, -- 'open', 'closed'
    author_login TEXT NOT NULL,
    assignee_logins TEXT[], -- Array of assignee usernames
    label_names TEXT[], -- Array of label names
    milestone_title TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE,
    crawled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_issues_issue_id ON issues(issue_id);
CREATE INDEX IF NOT EXISTS idx_issues_repo_id ON issues(repo_id);
CREATE INDEX IF NOT EXISTS idx_issues_state ON issues(state);
CREATE INDEX IF NOT EXISTS idx_issues_author_login ON issues(author_login);
CREATE INDEX IF NOT EXISTS idx_issues_updated_at ON issues(updated_at);
CREATE INDEX IF NOT EXISTS idx_issues_crawled_at ON issues(crawled_at);

-- Future extensibility: Pull Requests table
CREATE TABLE IF NOT EXISTS pull_requests (
    id SERIAL PRIMARY KEY,
    pr_id BIGINT UNIQUE NOT NULL,
    repo_id BIGINT NOT NULL REFERENCES repositories(repo_id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    state TEXT NOT NULL, -- 'open', 'closed', 'merged'
    author_login TEXT NOT NULL,
    assignee_logins TEXT[],
    reviewer_logins TEXT[],
    label_names TEXT[],
    milestone_title TEXT,
    head_ref TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    mergeable BOOLEAN,
    merged BOOLEAN NOT NULL DEFAULT false,
    merged_by_login TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE,
    merged_at TIMESTAMP WITH TIME ZONE,
    crawled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pull_requests_pr_id ON pull_requests(pr_id);
CREATE INDEX IF NOT EXISTS idx_pull_requests_repo_id ON pull_requests(repo_id);
CREATE INDEX IF NOT EXISTS idx_pull_requests_state ON pull_requests(state);
CREATE INDEX IF NOT EXISTS idx_pull_requests_author_login ON pull_requests(author_login);
CREATE INDEX IF NOT EXISTS idx_pull_requests_updated_at ON pull_requests(updated_at);
CREATE INDEX IF NOT EXISTS idx_pull_requests_crawled_at ON pull_requests(crawled_at);

-- Future extensibility: Comments table (supports both issues and PRs)
CREATE TABLE IF NOT EXISTS comments (
    id SERIAL PRIMARY KEY,
    comment_id BIGINT UNIQUE NOT NULL,
    repo_id BIGINT NOT NULL REFERENCES repositories(repo_id) ON DELETE CASCADE,
    issue_id BIGINT REFERENCES issues(issue_id) ON DELETE CASCADE,
    pr_id BIGINT REFERENCES pull_requests(pr_id) ON DELETE CASCADE,
    comment_type TEXT NOT NULL, -- 'issue', 'pr', 'review'
    author_login TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    crawled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Ensure comment belongs to either issue or PR, not both
    CONSTRAINT check_comment_parent CHECK (
        (issue_id IS NOT NULL AND pr_id IS NULL) OR 
        (issue_id IS NULL AND pr_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_comments_comment_id ON comments(comment_id);
CREATE INDEX IF NOT EXISTS idx_comments_repo_id ON comments(repo_id);
CREATE INDEX IF NOT EXISTS idx_comments_issue_id ON comments(issue_id);
CREATE INDEX IF NOT EXISTS idx_comments_pr_id ON comments(pr_id);
CREATE INDEX IF NOT EXISTS idx_comments_author_login ON comments(author_login);
CREATE INDEX IF NOT EXISTS idx_comments_updated_at ON comments(updated_at);
CREATE INDEX IF NOT EXISTS idx_comments_crawled_at ON comments(crawled_at);

-- Future extensibility: CI Checks table
CREATE TABLE IF NOT EXISTS ci_checks (
    id SERIAL PRIMARY KEY,
    check_id BIGINT UNIQUE NOT NULL,
    repo_id BIGINT NOT NULL REFERENCES repositories(repo_id) ON DELETE CASCADE,
    pr_id BIGINT REFERENCES pull_requests(pr_id) ON DELETE CASCADE,
    check_suite_id BIGINT,
    name TEXT NOT NULL,
    status TEXT NOT NULL, -- 'queued', 'in_progress', 'completed'
    conclusion TEXT, -- 'success', 'failure', 'neutral', 'cancelled', 'timed_out', 'action_required'
    head_sha TEXT NOT NULL,
    external_id TEXT,
    url TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    crawled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ci_checks_check_id ON ci_checks(check_id);
CREATE INDEX IF NOT EXISTS idx_ci_checks_repo_id ON ci_checks(repo_id);
CREATE INDEX IF NOT EXISTS idx_ci_checks_pr_id ON ci_checks(pr_id);
CREATE INDEX IF NOT EXISTS idx_ci_checks_status ON ci_checks(status);
CREATE INDEX IF NOT EXISTS idx_ci_checks_conclusion ON ci_checks(conclusion);
CREATE INDEX IF NOT EXISTS idx_ci_checks_updated_at ON ci_checks(updated_at);

-- Crawl job tracking for efficient incremental updates
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id SERIAL PRIMARY KEY,
    job_type TEXT NOT NULL, -- 'repositories', 'issues', 'pull_requests', 'comments', 'ci_checks'
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    status TEXT NOT NULL DEFAULT 'running', -- 'running', 'completed', 'failed'
    records_processed INTEGER DEFAULT 0,
    records_created INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    last_cursor TEXT, -- For pagination continuation
    error_message TEXT,
    metadata JSONB -- Flexible field for job-specific data
);

CREATE INDEX IF NOT EXISTS idx_crawl_jobs_job_type ON crawl_jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_started_at ON crawl_jobs(started_at);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status);

-- Rate limiting tracking
CREATE TABLE IF NOT EXISTS rate_limit_status (
    id SERIAL PRIMARY KEY,
    api_type TEXT NOT NULL, -- 'graphql', 'rest'
    limit_type TEXT NOT NULL, -- 'primary', 'secondary'
    remaining INTEGER NOT NULL,
    reset_at TIMESTAMP WITH TIME ZONE NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_status_api_type ON rate_limit_status(api_type);
CREATE INDEX IF NOT EXISTS idx_rate_limit_status_recorded_at ON rate_limit_status(recorded_at);
