#!/usr/bin/env python3
"""Clone repos for batch 2 concept assessment using parallel threads."""
import json, os, subprocess, shutil, concurrent.futures, time

os.makedirs('/tmp/concept_repos', exist_ok=True)
for d in os.listdir('/tmp/concept_repos'):
    p = os.path.join('/tmp/concept_repos', d)
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)

GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')

# Skip the 2 "deprecated" placeholder ones (netuid 81, 39)
REPOS = [
    (24,  "Quasar",         "SILX-LABS",  "QUASAR-SUBNET"),
    (124, "Swarm",         "swarm-subnet", "swarm"),
    (63,  "Enigma",        "qbittensor-labs", "enigma"),
    (126, "Poker44",       "Poker44",    "Poker44-subnet"),
    (79,  "MVTRX",         "taos-im",    "sn-79"),
    (100, "BASE",          "BaseIntelligence", "base"),
    (49,  "Nepher Robotics","nepher-ai", "nepher-subnet"),
    (46,  "Zipcode",       "resi-labs-ai", "RESI-models"),
    (94,  "pending...",    "AlveusLabs", "SN94-BitSota"),
    (82,  "Compelle",      "compelle",   "compelle-validator"),
    (18,  "Zeus",          "Orpheus-AI", "Zeus"),
    (121, "sundae_bar",    "sundae-bar", "bittensor-subnet"),
    (85,  "Vidaio",        "vidaio-subnet", "vidaio-subnet"),
    (41,  "Almanac",       "sportstensor", "sn41"),
    (33,  "ReadyAI",       "afterpartyai", "bittensor-conversation-genome-project"),
    (50,  "Synth",         "mode-network", "synth-subnet"),
]

def clone_one(item):
    netuid, name, owner, repo = item
    target = f"/tmp/concept_repos/sn{netuid}_{repo}"
    url = f"https://{GH_TOKEN}@github.com/{owner}/{repo}.git" if GH_TOKEN else f"https://github.com/{owner}/{repo}.git"
    try:
        r = subprocess.run(
            ['git', 'clone', '--depth', '1', url, target],
            capture_output=True, timeout=90, text=True
        )
        if r.returncode != 0:
            return (netuid, name, owner, repo, 'clone_failed', r.stderr[:300])
        file_count = sum(len(files) for _, _, files in os.walk(target))
        return (netuid, name, owner, repo, 'ok', f'{file_count} files')
    except subprocess.TimeoutExpired:
        return (netuid, name, owner, repo, 'timeout', 'clone exceeded 90s')
    except Exception as e:
        return (netuid, name, owner, repo, 'error', str(e)[:200])

print(f"Cloning {len(REPOS)} repos in parallel (max 8 workers)...")
start = time.time()
results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(clone_one, REPOS):
        results.append(r)
        print(f"  SN{r[0]:>3} {r[1]:<20} {r[4]:<14} {r[5]}")
print(f"\nDone in {time.time()-start:.1f}s")
ok = sum(1 for r in results if r[4] == 'ok')
print(f"OK: {ok}/{len(results)}")
