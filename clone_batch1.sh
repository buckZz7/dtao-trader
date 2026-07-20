#!/bin/bash
source /opt/data/skills/github/github-auth/scripts/gh-env.sh

OUT=/tmp/repos_batch1
mkdir -p $OUT
cd $OUT

clone_repo() {
  local key=$1
  local owner=$2
  local repo=$3
  local dir="$OUT/$key"
  if [ -d "$dir/.git" ]; then
    echo "EXISTS $key"
    return
  fi
  echo "CLONING $key -> $owner/$repo"
  git clone --depth 1 "https://github.com/$owner/$repo.git" "$dir" 2>&1 | tail -2
}

clone_repo 64_chutes chutesai chutes
clone_repo 120_affine AffineFoundation affine
clone_repo 4_targon manifold-inc targon
clone_repo 107_minos minos-protocol minos_subnet
clone_repo 97_albedo unarbos albedo
clone_repo 15_oro ORO-AI oro
clone_repo 68_nova metanova-labs nova
clone_repo 75_hippius thenervelab thebrain
clone_repo 96_verathos verathos-ai verathos
clone_repo 114_soma DendriteHQ SOMA
clone_repo 38_chronollm chronollm sn38
clone_repo 34_bitmind BitMind-AI bitmind-subnet
clone_repo 67_harnyx harnyx harnyx
clone_repo 93_bitcast bitcast-network bitcast
clone_repo 19_blockmachine taostat blockmachine
clone_repo 102_connito Connito-AI Connito

# SN3 deprecated - skip
# SN105 orgs URL - skip

echo "DONE"
ls -la $OUT
