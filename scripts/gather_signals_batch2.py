#!/usr/bin/env python3
"""Gather execution signals for batch 2 concept assessment."""
import os, json

REPOS_DIR = '/tmp/concept_repos'
CODE_EXT = {'.py', '.rs', '.ts', '.tsx', '.js', '.jsx', '.go', '.c', '.cpp', '.h', '.sol'}

EXCLUDE_DIRS = {'.git', '.venv', 'node_modules', '__pycache__', '.egg-info', '.github', '.vscode', '.idea'}

def gather(repo_path):
    total_loc = 0
    py_loc = 0
    file_count = 0
    lang = {}
    key_files = []
    has_tests = False
    test_files = 0
    top_level = []
    subdirs = []

    try:
        top_level = sorted(os.listdir(repo_path))
    except Exception:
        pass

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), repo_path)
            ext = os.path.splitext(f)[1]
            if ext in CODE_EXT:
                try:
                    with open(os.path.join(root, f), 'r', errors='ignore') as fh:
                        lines = sum(1 for _ in fh)
                    total_loc += lines
                    file_count += 1
                    lang[ext] = lang.get(ext, 0) + lines
                    if ext == '.py':
                        py_loc += lines
                except Exception:
                    pass
            if 'test' in f.lower() and ext in CODE_EXT:
                has_tests = True
                test_files += 1
            # Key bittensor architecture files
            for k in ['neuron', 'validator', 'miner', 'base/neuron', 'template']:
                if k in rel.lower() and ext in CODE_EXT:
                    key_files.append(rel)

    # Get subdirs (top level only)
    try:
        subdirs = [d for d in os.listdir(repo_path) if os.path.isdir(os.path.join(repo_path, d)) and d not in EXCLUDE_DIRS]
    except Exception:
        pass

    return {
        'total_loc': total_loc,
        'py_loc': py_loc,
        'file_count': file_count,
        'languages': lang,
        'has_tests': has_tests,
        'test_files': test_files,
        'key_arch_files': key_files[:10],
        'top_level_files': [f for f in top_level if not f.startswith('.')][:25],
        'subdirs': sorted(subdirs)[:20],
    }

results = {}
for d in sorted(os.listdir(REPOS_DIR)):
    p = os.path.join(REPOS_DIR, d)
    if not os.path.isdir(p):
        continue
    # parse netuid
    try:
        netuid = int(d.split('_')[0].replace('sn', ''))
    except Exception:
        continue
    info = gather(p)
    results[netuid] = info

print(json.dumps(results, indent=2))
