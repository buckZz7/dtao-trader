"""Comprehensive subnet code quality assessment.

Goes beyond commit count to evaluate actual code quality:
1. Lines of real code (excluding boilerplate, lockfiles, generated files)
2. Template divergence (how different from the bittensor subnet template)
3. Code complexity (real logic vs config/scripts)
4. Test coverage (test files exist, test ratio)
5. Documentation quality (README length, docstrings)
6. Language breakdown (protocol code vs boilerplate)
7. Architecture signals (neuron.py, validator logic, miner logic, custom modules)
"""
import json, os, subprocess, tempfile, shutil
from datetime import datetime, timezone

# File patterns to EXCLUDE from real code counting
EXCLUDE_PATTERNS = [
    '.git/', '.venv/', 'node_modules/', '__pycache__/', 
    '*.lock', '*.lockb', 'package-lock.json', 'yarn.lock',
    '.env', '.env.example', '.gitignore',
    '*.pyc', '*.pyo', '*.egg-info/',
    'Dockerfile', 'docker-compose.yml',
    '*.md', '*.rst', '*.txt',  # docs counted separately
    '*.png', '*.jpg', '*.jpeg', '*.gif', '*.svg', '*.ico',
    '*.zip', '*.tar', '*.gz',
    '.github/', '.vscode/', '.idea/',
]

# File extensions that count as real code
CODE_EXTENSIONS = {
    '.py', '.rs', '.ts', '.tsx', '.js', '.jsx', 
    '.go', '.java', '.kt', '.swift',
    '.c', '.cpp', '.h', '.hpp',
    '.sol', '.move',
}

# Template files (bittensor subnet template signatures)
TEMPLATE_FILES = [
    'neurons/miner.py', 'neurons/validator.py',
    'template/miner.py', 'template/validator.py',
]

def should_count_file(filepath):
    """Check if a file should be counted as real code."""
    for pattern in EXCLUDE_PATTERNS:
        if pattern.replace('*', '') in filepath:
            return False
        if filepath.endswith(pattern.replace('*', '')):
            return False
    
    _, ext = os.path.splitext(filepath)
    return ext in CODE_EXTENSIONS

def count_lines_of_code(repo_path):
    """Count real lines of code, excluding boilerplate."""
    total_lines = 0
    file_count = 0
    file_breakdown = {}
    
    for root, dirs, files in os.walk(repo_path):
        # Skip excluded directories
        if '.git' in root or '.venv' in root or 'node_modules' in root:
            continue
        if '__pycache__' in root or '.egg-info' in root:
            continue
        
        for f in files:
            filepath = os.path.join(root, f)
            rel_path = os.path.relpath(filepath, repo_path)
            
            if not should_count_file(rel_path):
                continue
            
            try:
                with open(filepath, 'r', errors='ignore') as fh:
                    lines = sum(1 for _ in fh)
                total_lines += lines
                file_count += 1
                
                ext = os.path.splitext(f)[1]
                if ext not in file_breakdown:
                    file_breakdown[ext] = {'files': 0, 'lines': 0}
                file_breakdown[ext]['files'] += 1
                file_breakdown[ext]['lines'] += lines
            except:
                pass
    
    return {
        'total_loc': total_lines,
        'file_count': file_count,
        'by_language': file_breakdown,
    }

def assess_tests(repo_path):
    """Check for test files and test coverage."""
    test_files = 0
    test_lines = 0
    total_code_lines = 0
    
    for root, dirs, files in os.walk(repo_path):
        if '.git' in root or '.venv' in root or 'node_modules' in root:
            continue
        
        for f in files:
            filepath = os.path.join(root, f)
            _, ext = os.path.splitext(f)
            
            if ext not in CODE_EXTENSIONS:
                continue
            
            try:
                with open(filepath, 'r', errors='ignore') as fh:
                    lines = sum(1 for _ in fh)
                total_code_lines += lines
                
                if 'test' in f.lower() or '/tests/' in filepath.lower() or '/test/' in filepath.lower():
                    test_files += 1
                    test_lines += lines
            except:
                pass
    
    test_ratio = test_lines / total_code_lines if total_code_lines > 0 else 0
    
    return {
        'test_files': test_files,
        'test_lines': test_lines,
        'test_ratio': round(test_ratio, 3),
        'has_tests': test_files > 0,
    }

def assess_documentation(repo_path):
    """Check README and docstring quality."""
    readme_lines = 0
    readme_exists = False
    
    for f in os.listdir(repo_path):
        if f.lower().startswith('readme'):
            readme_exists = True
            try:
                with open(os.path.join(repo_path, f), 'r', errors='ignore') as fh:
                    readme_lines = sum(1 for _ in fh)
            except:
                pass
    
    # Check for docstrings in Python files
    docstring_lines = 0
    total_py_lines = 0
    
    for root, dirs, files in os.walk(repo_path):
        if '.git' in root or '.venv' in root:
            continue
        for f in files:
            if f.endswith('.py'):
                try:
                    with open(os.path.join(root, f), 'r', errors='ignore') as fh:
                        content = fh.read()
                        total_py_lines += len(content.splitlines())
                        # Count lines inside docstrings
                        in_docstring = False
                        for line in content.splitlines():
                            if '"""' in line or "'''" in line:
                                docstring_lines += 1
                                if in_docstring:
                                    in_docstring = False
                                else:
                                    in_docstring = True
                            elif in_docstring:
                                docstring_lines += 1
                except:
                    pass
    
    return {
        'readme_exists': readme_exists,
        'readme_lines': readme_lines,
        'docstring_lines': docstring_lines,
        'docstring_ratio': round(docstring_lines / total_py_lines, 3) if total_py_lines > 0 else 0,
    }

def assess_architecture(repo_path):
    """Check for real subnet architecture (not just template)."""
    signals = {
        'has_neurons': False,
        'has_validator': False,
        'has_miner': False,
        'has_custom_modules': False,
        'has_api': False,
        'has_database': False,
        'has_ml_models': False,
        'has_benchmark': False,
        'custom_files': 0,
    }
    
    all_files = []
    for root, dirs, files in os.walk(repo_path):
        if '.git' in root or '.venv' in root or 'node_modules' in root:
            continue
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo_path)
            all_files.append(rel)
    
    for f in all_files:
        fl = f.lower()
        if 'neuron' in fl: signals['has_neurons'] = True
        if 'validator' in fl: signals['has_validator'] = True
        if 'miner' in fl: signals['has_miner'] = True
        if any(x in fl for x in ['api/', 'server/', 'service/', 'core/', 'protocol/']):
            signals['has_custom_modules'] = True
        if any(x in fl for x in ['api.py', 'server.py', 'endpoint', 'route']):
            signals['has_api'] = True
        if any(x in fl for x in ['db', 'database', 'model.py', 'schema', 'migration']):
            signals['has_database'] = True
        if any(x in fl for x in ['model', 'train', 'inference', 'ml/', 'ai/']):
            signals['has_ml_models'] = True
        if any(x in fl for x in ['benchmark', 'eval', 'score']):
            signals['has_benchmark'] = True
    
    # Count non-template files (files not in standard template paths)
    template_paths = ['neurons/', 'template/', 'setup.py', 'requirements.txt', '.env.example']
    custom = [f for f in all_files if not any(f.startswith(t) or f == t for t in template_paths)]
    signals['custom_files'] = len([f for f in custom if should_count_file(f)])
    
    return signals

def assess_quality_score(loc, tests, docs, arch, commits_30d):
    """Compute a quality score 0-100 based on all factors."""
    score = 0
    
    # Code volume (max 25 points)
    # 500+ lines = real project, 2000+ = substantial
    loc_score = min(25, (loc['total_loc'] / 2000) * 25)
    score += loc_score
    
    # Tests (max 20 points)
    if tests['has_tests']:
        test_score = min(20, tests['test_ratio'] * 100)
        score += test_score
    
    # Documentation (max 15 points)
    doc_score = 0
    if docs['readme_exists']:
        doc_score += min(5, docs['readme_lines'] / 50)
    doc_score += min(10, docs['docstring_ratio'] * 50)
    score += doc_score
    
    # Architecture (max 25 points)
    arch_score = 0
    if arch['has_neurons']: arch_score += 3
    if arch['has_validator']: arch_score += 3
    if arch['has_miner']: arch_score += 3
    if arch['has_custom_modules']: arch_score += 5
    if arch['has_api']: arch_score += 3
    if arch['has_database']: arch_score += 3
    if arch['has_ml_models']: arch_score += 3
    if arch['has_benchmark']: arch_score += 2
    arch_score = min(25, arch_score)
    score += arch_score
    
    # Activity (max 15 points)
    # Commits show ongoing work but capped — 760 commits isn't 10x better than 76
    activity_score = min(15, (commits_30d or 0) / 30 * 15)
    score += activity_score
    
    return round(score, 1)

def clone_and_assess(netuid, name, github_url, commits_30d=0):
    """Clone a repo and run full assessment."""
    # Parse owner/repo from URL
    url = github_url.replace('/tree/main', '').replace('/tree/master', '').rstrip('/')
    if 'orgs/' in url:
        return None  # Skip org URLs
    
    parts = url.rstrip('/').split('/')
    if len(parts) < 2:
        return None
    owner = parts[-2]
    repo = parts[-1].replace('.git', '')
    
    # Clone to temp dir
    tmpdir = tempfile.mkdtemp()
    repo_path = os.path.join(tmpdir, repo)
    
    try:
        # Shallow clone, last commit only
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', f'https://github.com/{owner}/{repo}.git', repo_path],
            capture_output=True, timeout=60, text=True
        )
        if result.returncode != 0:
            return {'netuid': netuid, 'name': name, 'github': github_url, 'error': 'clone failed'}
        
        # Run assessments
        loc = count_lines_of_code(repo_path)
        tests = assess_tests(repo_path)
        docs = assess_documentation(repo_path)
        arch = assess_architecture(repo_path)
        quality_score = assess_quality_score(loc, tests, docs, arch, commits_30d)
        
        return {
            'netuid': netuid,
            'name': name,
            'github': github_url,
            'loc': loc['total_loc'],
            'file_count': loc['file_count'],
            'by_language': {k: v['lines'] for k, v in loc['by_language'].items()},
            'tests': tests,
            'docs': docs,
            'architecture': arch,
            'quality_score': quality_score,
            'commits_30d': commits_30d,
        }
    except Exception as e:
        return {'netuid': netuid, 'name': name, 'github': github_url, 'error': str(e)[:100]}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == '__main__':
    import sys
    
    # Load subnet list
    with open('data/emission_enabled_subnets.json') as f:
        subnets = json.load(f)
    
    # Load GitHub activity for commit counts
    with open('data/github_activity.json') as f:
        gh_activity = json.load(f)
    commit_map = {g['netuid']: g.get('commits_30d', 0) for g in gh_activity}
    
    # Filter to undervalued + active subnets (the ones worth assessing)
    with open('data/valuation_analysis.json') as f:
        valuation = json.load(f)
    
    # Assess all emission-enabled subnets (or a subset)
    if '--all' in sys.argv:
        to_assess = subnets
    else:
        # Focus on undervalued + near-equilibrium subnets
        to_assess = []
        for v in valuation:
            if v['distance_pct'] < -20:  # Undervalued
                sn = next((s for s in subnets if s['netuid'] == v['netuid']), None)
                if sn:
                    to_assess.append(sn)
    
    print(f"Assessing {len(to_assess)} subnets...")
    
    results = []
    for i, sn in enumerate(to_assess):
        netuid = sn['netuid']
        name = sn['name']
        github = sn['github']
        commits = commit_map.get(netuid, 0)
        
        print(f"  [{i+1}/{len(to_assess)}] SN{netuid} ({name})...", end=' ', flush=True)
        
        if not github or 'orgs/' in github or 'deprecated' in github:
            print("skip (no valid repo)")
            continue
        
        result = clone_and_assess(netuid, name, github, commits)
        if result:
            results.append(result)
            if 'error' in result:
                print(f"error: {result['error']}")
            else:
                print(f"LOC: {result['loc']}, Quality: {result['quality_score']}/100")
        else:
            print("skip")
    
    # Save
    with open('data/code_quality.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    results_valid = [r for r in results if 'error' not in r]
    results_valid.sort(key=lambda x: x['quality_score'], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"CODE QUALITY ASSESSMENT ({len(results_valid)} subnets)")
    print(f"{'='*80}")
    print(f"\n{'SN':>4} {'Name':>15} {'LOC':>7} {'Files':>6} {'Tests':>6} {'DocStr':>7} {'Arch':>6} {'Score':>7}")
    print("-" * 65)
    for r in results_valid:
        arch_signals = sum(1 for v in r['architecture'].values() if v is True) + (1 if r['architecture'].get('custom_files', 0) > 5 else 0)
        print(f"  SN{r['netuid']:3d} {r['name']:>15} {r['loc']:>7} {r['file_count']:>6} {r['tests']['test_files']:>6} {r['docs']['docstring_lines']:>7} {arch_signals:>6} {r['quality_score']:>6.1f}")
