#!/usr/bin/env python3
"""Compose final concept scores for batch 2 (18 subnets).

Scoring rubric (1-10 each):
  NECESSITY:  1 = business with token, 10 = needs miners/validators
  TAM:        1 = tiny niche, 10 = massive market
  MOAT:       1 = easily cloneable, 10 = strong network effects/IP
  EXECUTION:  1 = vaporware, 10 = production-grade

concept_score = avg(4 dims) * 10  -> 0-100
verdict: Real network / Borderline / Business with token
  - avg >= 6.5 -> Real network
  - avg >= 4.5 -> Borderline
  - else       -> Business with token
"""
import json

# netuid -> assessment dict
DATA = [
  # SN24 Quasar — decentralized pretraining of Quasar MoE LLM (10T+ tokens target).
  # Miners train fragments, validators verify, orchestrator merges. Real distributed training.
  # Strong necessity (true distributed training is the decentralized ML ideal), big TAM (LLM training
  # is massive), strong moat (10T-token run + signed fragment protocol is hard to replicate),
  # execution is substantial (34.7k LOC, real orchestrator/validator/miner code, S3 checkpoint flow).
  {
    "netuid": 24, "name": "Quasar",
    "summary": "Decentralized LLM pretraining where miners train tensor fragments of the Quasar MoE model, validators verify, and an orchestrator merges accepted fragments into a live checkpoint.",
    "scores": {"necessity": 9, "tam": 9, "moat": 8, "execution": 8},
  },
  # SN124 Swarm — drone RL arena. Miners train drone pilots, validators run them in
  # procedurally-generated worlds (search&rescue, swarm SAR, interceptor, autopilot).
  # Real necessity (distributed RL training across many pilots), large TAM (drone/robotics market),
  # good moat (simulation engine + 5 missions + 1100 worlds/week), excellent execution
  # (72.7k LOC, 117 test files, real leaderboard).
  {
    "netuid": 124, "name": "Swarm",
    "summary": "Open drone-pilot RL arena where miners train models that fly missions (search & rescue, swarm coordination, interception) in thousands of fresh procedural worlds each week.",
    "scores": {"necessity": 8, "tam": 7, "moat": 7, "execution": 9},
  },
  # SN63 Enigma — bounty-based challenge subnet for breaking crypto (RSA) / post-quantum hardening.
  # Treasury wallet (on-chain smart contract) funds prizes; validators verify solutions & vote.
  # High necessity (decentralized security research bounties), decent TAM (security research),
  # strong moat (treasury contract + partner network Terra Quantum/BlueQubit), good execution
  # (30.9k LOC incl. Solidity treasury contracts, full miner/validator/treasury tooling).
  {
    "netuid": 63, "name": "Enigma",
    "summary": "Bounty-based challenge subnet that funds on-chain prize pools (via a governor smart contract) for breaking cryptographic systems like RSA, with validators verifying and voting on payouts.",
    "scores": {"necessity": 8, "tam": 6, "moat": 8, "execution": 8},
  },
  # SN81 deprecated — placeholder/deprecated subnet (github.com/deprecated/deprecated).
  {
    "netuid": 81, "name": "deprecated",
    "summary": "Deprecated/placeholder subnet — no real GitHub repository exists; the listed URL is a placeholder.",
    "scores": {"necessity": 1, "tam": 1, "moat": 1, "execution": 1},
  },
  # SN126 Poker44 — bot detection for online poker. Miners return bot-risk predictions on hand chunks,
  # validators score against canonical eval data from the platform. "Security infrastructure, not a
  # poker room." Decent necessity (distributed model competition), moderate TAM (online poker security
  # niche is real but not huge), weak-to-moderate moat (eval data is centralized by Poker44 platform),
  # decent execution (7k LOC, real validator/miner/synapse code, 10 test files).
  {
    "netuid": 126, "name": "Poker44",
    "summary": "Poker bot-detection subnet where miners score bot-risk predictions on hand-behavior chunks and validators benchmark them against canonical evaluation data from the Poker44 platform.",
    "scores": {"necessity": 6, "tam": 4, "moat": 5, "execution": 6},
  },
  # SN79 MVTRX — decentralized market research + AI training for trading. C++ matching engine
  # simulation (τaos), GenTRX distributed training of an order-book generative model, and a
  # forthcoming live exchange. High necessity (distributed sim + gradient training), large TAM
  # (trading/market data), strong moat (custom C++ L3 orderbook engine + GenTRX gradient protocol),
  # excellent execution (89.9k LOC incl. 27k C++, 66 test files, real Grafana dashboards).
  {
    "netuid": 79, "name": "MVTRX",
    "summary": "Decentralized market-research subnet combining a C++ limit-orderbook trading simulator (τaos) where miners run trading agents, with distributed gradient training (GenTRX) of a shared order-book generative model.",
    "scores": {"necessity": 8, "tam": 8, "moat": 8, "execution": 9},
  },
  # SN100 BASE — multi-challenge orchestration platform. Routes miner traffic to independent
  # challenge subnets, aggregates weights, validators submit on-chain. Master/validator/worker plane
  # with miner-funded GPU workers + ExecutionProof anti-collusion. High necessity (meta-orchestration
  # for many challenges), large TAM (broader than any single challenge), strong moat (worker plane +
  # proof tiers + proxy deny rules), excellent execution (103.5k LOC, 172 test files, full Compose
  # operator path, CI badge).
  {
    "netuid": 100, "name": "BASE",
    "summary": "Multi-challenge orchestration subnet that runs independent challenge subnets under one validator network, with a master coordinator, miner-funded GPU worker plane, and signed execution proofs.",
    "scores": {"necessity": 7, "tam": 8, "moat": 8, "execution": 9},
  },
  # SN49 Nepher Robotics — robotics tournament platform. Miners submit trained policies; validators
  # score them in isolated Isaac Lab GPU sandbox containers. Strong necessity (distributed robotics
  # policy training is a genuine ML frontier), large TAM (robotics), good moat (sandbox isolation +
  # Isaac Lab integration + tournament API), decent execution (7.8k LOC, real sandbox/validator/miner
  # code, 5 test files). Lower LOC but the architecture is real and the sandbox security model is
  # substantive.
  {
    "netuid": 49, "name": "Nepher Robotics",
    "summary": "Decentralized robotics tournament where miners submit trained policies and validators score them inside isolated Isaac Lab GPU sandbox containers running on NVIDIA hardware.",
    "scores": {"necessity": 8, "tam": 7, "moat": 7, "execution": 6},
  },
  # SN46 Zipcode / RESI — real-estate price prediction. Miners train ONNX models for US residential
  # property prices, validators evaluate against fresh ground-truth sales (30-day commit-reveal
  # separation). Moderate necessity (could be done centrally, but commit-reveal + competition is a
  # genuine use), large TAM (real estate is the biggest asset class), decent moat (commit-reveal +
  # winner-takes-all threshold pioneer mechanism + HF model hosting), excellent execution
  # (31.7k LOC, 49 test files, real miner CLI, validator scoring).
  {
    "netuid": 46, "name": "Zipcode",
    "summary": "Real-estate price prediction subnet where miners train ONNX models for US residential property prices and validators evaluate them against never-before-seen sales data via a 30-day commit-reveal mechanism.",
    "scores": {"necessity": 6, "tam": 8, "moat": 6, "execution": 8},
  },
  # SN94 BitSota — decentralized ML algorithm evolution via genetic programming. Miners evolve ML
  # algorithms on a CIFAR-10 pipeline, validators run autoresearch replay validation. Has pool mining,
  # desktop GUI, research-agent path. Moderate necessity (distributed AutoML evolution is a real
  # research angle but still speculative), moderate TAM (ML research tooling), weak moat (CIFAR-10
  # eval is easily cloned, novelty is in the research framing), decent execution (32k LOC, 9 test
  # files, GUI/desktop app, real miner/validator/pool code).
  {
    "netuid": 94, "name": "BitSota",
    "summary": "Decentralized AutoML network where miners evolve ML algorithms using genetic programming on a fixed CIFAR-10 pipeline and validators run autoresearch replay validation, with direct and pool mining modes.",
    "scores": {"necessity": 6, "tam": 5, "moat": 4, "execution": 6},
  },
  # SN39 deprecated — placeholder (same github.com/deprecated/deprecated).
  {
    "netuid": 39, "name": "deprecated",
    "summary": "Deprecated/placeholder subnet — no real GitHub repository exists; the listed URL is a placeholder.",
    "scores": {"necessity": 1, "tam": 1, "moat": 1, "execution": 1},
  },
  # SN82 Compelle — adversarial persuasion debate subnet. Miners commit written debate strategies
  # on-chain; validators pair miners in Pro/Con LLM debates, score via Elo, set weights. Low-moderate
  # necessity (LLM debate could be run centrally; decentralization adds minor value), small TAM
  # (debate/persuasion niche), weak moat (Chutes-hosted LLMs + standard Elo, easily cloned),
  # weak execution (3.6k LOC, 6 Python files, 0 tests, validator-only repo).
  {
    "netuid": 82, "name": "Compelle",
    "summary": "Adversarial-persuasion subnet where miners commit debate strategies on-chain and validators pair them in Pro/Con LLM debates scored by Elo, using Chutes-hosted inference.",
    "scores": {"necessity": 3, "tam": 3, "moat": 3, "execution": 4},
  },
  # SN18 Zeus — environmental forecasting. Miners predict ERA5 climate variables (temperature, wind,
  # solar radiation) on the global grid; validators issue short- and long-range challenges with
  # on-chain commit-reveal verification. Strong necessity (distributed forecasting competition +
  # commit-reveal is a genuine decentralized-ML use), large TAM (weather/climate forecasting is
  # massive), good moat (ERA5 data pipeline + commit-reveal + SQLite validator state), decent
  # execution (7.9k LOC, real miner/validator/commit-reveal, but 0 tests).
  {
    "netuid": 18, "name": "Zeus",
    "summary": "Environmental forecasting subnet where miners predict ERA5 climate variables (temperature, wind, solar radiation) on the global grid and validators verify predictions via on-chain commit-reveal.",
    "scores": {"necessity": 8, "tam": 7, "moat": 6, "execution": 6},
  },
  # SN121 sundae_bar — generalist commercial AI agent benchmarking. Miners submit open-source
  # generalist agents, validators benchmark them via the Agent Eval Test Suite (AETS), winner takes
  # all. sundae_bar deploys the winner commercially. Low necessity (agent benchmarking is a business
  # that does not inherently need decentralization; the "revenue-backed emissions" framing is a
  # business-with-token pattern), moderate TAM (enterprise AI agents), weak moat (AETS + Letta are
  # off-the-shelf), very weak execution (README-only repo — 0 LOC of code, just assets/).
  {
    "netuid": 121, "name": "sundae_bar",
    "summary": "Generalist AI agent benchmarking subnet where miners submit open-source agents and validators score them via an Agent Eval Test Suite, with the winner deployed commercially by the sundae_bar enterprise platform.",
    "scores": {"necessity": 3, "tam": 6, "moat": 3, "execution": 1},
  },
  # SN85 Vidaio — video processing (upscaling + compression) via miners, validators benchmark with
  # VMAF/PieAPP. Moderate necessity (distributed video processing could be done by cloud but the
  # benchmarking competition is genuine), large TAM (video processing/streaming), weak moat
  # (upscaling/compression models are commoditized; VMAF is standard), decent execution (34.4k LOC,
  # 4 test files, real miner/validator with Modal workers, Redis, ffmpeg).
  {
    "netuid": 85, "name": "Vidaio",
    "summary": "Video processing subnet where miners perform AI-driven upscaling and compression and validators benchmark outputs using VMAF and PieAPP metrics on synthetic and organic queries.",
    "scores": {"necessity": 5, "tam": 7, "moat": 4, "execution": 7},
  },
  # SN41 Almanac / Sportstensor — decentralized sports prediction. Miners trade on Almanac (which
  # routes Polymarket CLOB orders) and earn based on prediction accuracy/ROI; validators run a
  # two-phase optimization scoring. Moderate necessity (prediction-market signal aggregation is a
  # plausible decentralization use, but it's ultimately a trading business), moderate TAM (sports
  # betting/prediction markets), weak moat (depends on Polymarket infra; scoring is replicable),
  # weak execution (9.7k LOC, 11 code files, 0 tests, flat structure).
  {
    "netuid": 41, "name": "Almanac",
    "summary": "Decentralized sports-prediction network where miners trade on Almanac (routing Polymarket CLOB orders) and validators reward them via a two-phase optimization on ROI and trading volume.",
    "scores": {"necessity": 5, "tam": 5, "moat": 4, "execution": 4},
  },
  # SN33 ReadyAI / Conversation Genome Project — low-cost structured data pipeline. Miners tag
  # structured data from raw inputs using LLMs (GPT-4o etc.), validators create ground truth and
  # score via cosine distance. Moderate necessity (distributed data annotation is a genuine use but
  # centralized annotation services exist), moderate TAM (data prep for AI is large), weak moat
  # (relies on OpenAI API; cosine-distance scoring is trivial to clone), decent execution
  # (16.8k LOC, 45 test files, real miner/validator/conversationgenome code, Docker, CI).
  {
    "netuid": 33, "name": "ReadyAI",
    "summary": "Structured-data pipeline where miners use LLMs to tag raw data into structured outputs and validators score submissions by cosine distance against validator-created ground truth.",
    "scores": {"necessity": 5, "tam": 6, "moat": 4, "execution": 7},
  },
  # SN50 Synth — synthetic data generation. Miners generate synthetic price/data simulations,
  # validators score via CRPS (continuous ranked probability score). The repo references a whitepaper
  # and API. Moderate necessity (synthetic data generation has decentralization value for diversity,
  # but could be centralized), large TAM (synthetic data for ML is a growing market), weak-moderate
  # moat (CRPS scoring + BigTable storage is replicable), decent execution (17.3k LOC, 21 test files,
  # real miner/validator/base code, alembic migrations, BigTable storage).
  {
    "netuid": 50, "name": "Synth",
    "summary": "Synthetic-data subnet where miners generate data simulations and validators score them via continuous ranked probability score (CRPS), with BigTable-backed prediction storage.",
    "scores": {"necessity": 5, "tam": 7, "moat": 5, "execution": 7},
  },
]

def verdict(avg):
    if avg >= 6.5:
        return "Real network"
    if avg >= 4.5:
        return "Borderline"
    return "Business with token"

results = []
for d in DATA:
    s = d["scores"]
    avg = (s["necessity"] + s["tam"] + s["moat"] + s["execution"]) / 4
    results.append({
        "netuid": d["netuid"],
        "name": d["name"],
        "summary": d["summary"],
        "scores": s,
        "concept_score": round(avg * 10, 1),
        "verdict": verdict(avg),
    })

# Sort by netuid for stable output
results.sort(key=lambda r: r["netuid"])

out_path = "/opt/data/dtao-trader/data/concept_scores_batch2.json"
with open(out_path, "w") as f:
    json.dump(results, f, indent=2)

print(f"Wrote {len(results)} entries to {out_path}\n")
print(f"{'SN':>4} {'Name':<18} {'Nec':>4} {'TAM':>4} {'Moat':>5} {'Exec':>5} {'Score':>6}  Verdict")
print("-" * 70)
for r in results:
    s = r["scores"]
    print(f"SN{r['netuid']:>3} {r['name']:<18} {s['necessity']:>4} {s['tam']:>4} {s['moat']:>5} {s['execution']:>5} {r['concept_score']:>6.1f}  {r['verdict']}")
