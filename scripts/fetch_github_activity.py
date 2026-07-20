#!/usr/bin/env python3
"""Fetch GitHub repo activity for all emission-enabled Bittensor subnets."""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

SUBNETS_FILE = "/opt/data/dtao-trader/data/emission_enabled_subnets.json"
OUTPUT_FILE = "/opt/data/dtao-trader/data/github_activity.json"

TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not TOKEN:
    # Try sourcing the env script
    print("WARNING: GITHUB_TOKEN not set in env", file=sys.stderr)

API = "https://api.github.com"

def api_get(path, raw_url=None):
    url = raw_url or (API + path)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dtao-trader",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers)
    except Exception as e:
        return 0, str(e), {}

def parse_owner_repo(url):
    """Parse a github URL to extract owner/repo. Handle edge cases."""
    if not url or "github.com" not in url:
        return None, None, None
    # Strip trailing slash
    u = url.rstrip("/")
    # Strip /tree/<branch> suffix
    u = re.sub(r"/tree/[^/]+(/.*)?$", "", u)
    # Strip /blob/ suffix
    u = re.sub(r"/blob/[^/]+(/.*)?$", "", u)
    # Org repositories listing page — cannot resolve directly
    if "/orgs/" in u and "/repositories" in u:
        org = u.split("/orgs/")[1].split("/")[0]
        return None, None, ("org", org)
    # Standard owner/repo path
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)", u)
    if not m:
        return None, None, None
    owner = m.group(1)
    repo = m.group(2)
    # Remove trailing .git
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo, None

def resolve_org_repo(org):
    """For org URLs, list the org's repos and pick the most likely subnet repo.
    We pick the most recently pushed repo as a heuristic, but try to find one
    with 'subnet' in the name first."""
    status, body, _ = api_get(f"/orgs/{org}/repos?per_page=100&sort=pushed&direction=desc")
    if status != 200:
        return None
    repos = json.loads(body)
    if not isinstance(repos, list) or not repos:
        return None
    # Prefer repos whose name contains subnet/network/sn
    candidates = [r for r in repos if re.search(r"subnet|network|sn[-_]?|validator|miner", r.get("name",""), re.I)]
    if candidates:
        # pick the most recently pushed among candidates
        candidates.sort(key=lambda r: r.get("pushed_at",""), reverse=True)
        return candidates[0]["name"]
    # Otherwise pick the most recently pushed
    return repos[0]["name"]

def get_total_commits(owner, repo):
    """Get total contributor count (proxy for contributors) and commit count is tricky.
    We'll count commits in last 30d and 7d via the commits endpoint with pagination
    using Link header. For speed, cap at per_page=100 and count pages."""
    return None

def count_commits(owner, repo, since_iso, until_iso=None):
    """Count commits since given ISO timestamp by paginating /repos/.../commits.
    Returns count and last commit date."""
    count = 0
    last_commit_date = None
    page = 1
    per_page = 100
    while True:
        params = {"since": since_iso, "per_page": per_page, "page": page}
        if until_iso:
            params["until"] = until_iso
        qs = urllib.parse.urlencode(params)
        status, body, headers = api_get(f"/repos/{owner}/{repo}/commits?{qs}")
        if status == 404 or status == 403:
            return None, None, status
        if status != 200:
            return None, None, status
        try:
            commits = json.loads(body)
        except Exception:
            return None, None, status
        if not isinstance(commits, list) or len(commits) == 0:
            break
        count += len(commits)
        # last_commit_date is the most recent; first item on page 1 is most recent
        if page == 1 and commits:
            d = commits[0].get("commit", {}).get("committer", {}).get("date")
            if d:
                last_commit_date = d
        if len(commits) < per_page:
            break
        page += 1
        if page > 40:  # cap at 4000 commits to avoid runaway
            break
    return count, last_commit_date, 200

def get_contributors_count(owner, repo):
    """Count contributors via /repos/{owner}/{repo}/contributors with anon=1,
    paginating. Cap at 500 to limit time."""
    count = 0
    page = 1
    per_page = 100
    while page <= 6:
        qs = urllib.parse.urlencode({"per_page": per_page, "page": page, "anon": "1"})
        status, body, _ = api_get(f"/repos/{owner}/{repo}/contributors?{qs}")
        if status != 200:
            if page == 1:
                return None
            break
        try:
            lst = json.loads(body)
        except Exception:
            break
        if not isinstance(lst, list) or len(lst) == 0:
            break
        count += len(lst)
        if len(lst) < per_page:
            break
        page += 1
    return count

def get_last_commit_date(owner, repo):
    """Quick fetch of 1 most recent commit to get last commit date."""
    status, body, _ = api_get(f"/repos/{owner}/{repo}/commits?per_page=1")
    if status != 200 or not body:
        return None
    try:
        c = json.loads(body)
        if isinstance(c, list) and c:
            return c[0].get("commit", {}).get("committer", {}).get("date")
    except Exception:
        pass
    return None

def process_subnet(entry):
    netuid = entry.get("netuid")
    name = entry.get("name")
    github = entry.get("github")
    result = {
        "netuid": netuid,
        "name": name,
        "github": github,
        "stars": None,
        "open_issues": None,
        "commits_7d": None,
        "commits_30d": None,
        "last_commit": None,
        "last_push": None,
        "contributors": None,
    }
    owner, repo, special = parse_owner_repo(github)
    if special and special[0] == "org":
        org = special[1]
        r = resolve_org_repo(org)
        if r is None:
            print(f"  [netuid={netuid}] org {org}: no repos found, skipping", file=sys.stderr)
            result["github"] = f"{github} (unresolved org)"
            return result
        owner, repo = org, r
        print(f"  [netuid={netuid}] org {org} resolved to {owner}/{repo}", file=sys.stderr)
    if not owner or not repo:
        print(f"  [netuid={netuid}] could not parse {github}", file=sys.stderr)
        return result

    # Repo info
    status, body, _ = api_get(f"/repos/{owner}/{repo}")
    if status == 404:
        print(f"  [netuid={netuid}] {owner}/{repo} 404, skipping", file=sys.stderr)
        return result
    if status != 200:
        print(f"  [netuid={netuid}] {owner}/{repo} repo info status={status}", file=sys.stderr)
        return result
    try:
        info = json.loads(body)
    except Exception:
        return result
    result["stars"] = info.get("stargazers_count")
    result["open_issues"] = info.get("open_issues_count")
    result["last_push"] = info.get("pushed_at")

    now = datetime.now(timezone.utc)
    since_30 = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Commits in last 30 days (also gives last_commit date)
    c30, last_commit, cstatus = count_commits(owner, repo, since_30)
    if c30 is not None:
        result["commits_30d"] = c30
        result["last_commit"] = last_commit or get_last_commit_date(owner, repo)
    else:
        # fallback: try just getting last commit date
        result["last_commit"] = get_last_commit_date(owner, repo)

    # Commits in last 7 days
    c7, _, _ = count_commits(owner, repo, since_7)
    if c7 is not None:
        result["commits_7d"] = c7

    # Contributors
    contribs = get_contributors_count(owner, repo)
    if contribs is not None:
        result["contributors"] = contribs

    print(f"  [netuid={netuid}] {owner}/{repo}: stars={result['stars']} "
          f"commits_30d={result['commits_30d']} commits_7d={result['commits_7d']} "
          f"issues={result['open_issues']} contrib={result['contributors']}",
          file=sys.stderr)
    return result

def main():
    with open(SUBNETS_FILE) as f:
        subnets = json.load(f)
    print(f"Processing {len(subnets)} subnets", file=sys.stderr)
    results = []
    for i, entry in enumerate(subnets, 1):
        print(f"[{i}/{len(subnets)}] netuid={entry.get('netuid')} {entry.get('name')}",
              file=sys.stderr)
        r = process_subnet(entry)
        results.append(r)
        # Be polite to the API
        time.sleep(0.2)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} results to {OUTPUT_FILE}", file=sys.stderr)

    # Summary: top 20 by commits_30d
    valid = [r for r in results if r.get("commits_30d") is not None]
    valid.sort(key=lambda r: r["commits_30d"], reverse=True)
    print("\n" + "="*90)
    print(f"TOP 20 SUBNETS BY COMMITS IN LAST 30 DAYS (of {len(valid)} with data)")
    print("="*90)
    print(f"{'netuid':>6}  {'name':<28} {'commits_30d':>11} {'commits_7d':>10} {'stars':>6} {'issues':>6} {'contrib':>7}  last_commit")
    print("-"*110)
    for r in valid[:20]:
        print(f"{r['netuid']:>6}  {(r['name'] or '')[:28]:<28} "
              f"{r['commits_30d']:>11} {r['commits_7d']:>10} "
              f"{r['stars'] if r['stars'] is not None else '-':>6} "
              f"{r['open_issues'] if r['open_issues'] is not None else '-':>6} "
              f"{r['contributors'] if r['contributors'] is not None else '-':>7}  "
              f"{(r['last_commit'] or '-')[:10]}")
    skipped = [r for r in results if r.get("commits_30d") is None]
    print(f"\nSkipped/no-commit-data: {len(skipped)}")
    for r in skipped:
        print(f"  netuid={r['netuid']} {r['name']} -> {r['github']}")

if __name__ == "__main__":
    main()
