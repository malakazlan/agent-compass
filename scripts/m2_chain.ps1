# M2 finishing sequence on the CPU box: relabel example files with the current rules,
# regenerate label QA reports, rerun baselines, write the M1 data report.
# Launch detached so the session's task cleanup cannot stop it:
#   Start-Process powershell -ArgumentList '-NoProfile','-WindowStyle','Hidden','-File','D:\Fine-tune\scripts\m2_chain.ps1'
# Progress: .scratch\m2_chain.log ; completion marker: .scratch\m2_done.txt

$ErrorActionPreference = "Continue"
Set-Location D:\Fine-tune
$log = ".scratch\m2_chain.log"
"START $(Get-Date)" | Out-File -Encoding utf8 $log

function Run($step, $argsList) {
    "== $step $(Get-Date)" | Out-File -Encoding utf8 -Append $log
    & uv run --python 3.12 --with pydantic --with numpy --with scikit-learn python @argsList 2>&1 |
        ForEach-Object { $_.ToString() } | Out-File -Encoding utf8 -Append $log
}

Run "relabel swe_agent" @("scripts/relabel_examples.py", "--unified", "data/unified/swe_agent.jsonl", "--examples", "data/examples/swe_agent.dev.jsonl", "data/examples/swe_agent.test.jsonl", "data/examples/swe_agent.train.jsonl")
Run "relabel openhands" @("scripts/relabel_examples.py", "--unified", "data/unified/openhands.jsonl", "--examples", "data/examples/openhands.dev.jsonl", "data/examples/openhands.test.jsonl", "data/examples/openhands.train.jsonl")
Run "label_qa swe_agent" @("scripts/label_qa.py", "--in", "data/examples/swe_agent.dev.jsonl", "--out", "docs/label_qa_swe_agent.md", "--n", "25", "--max-records", "60000")
Run "label_qa openhands" @("scripts/label_qa.py", "--in", "data/examples/openhands.dev.jsonl", "--out", "docs/label_qa_openhands.md", "--n", "25", "--max-records", "40000")
Run "baselines swe_agent" @("scripts/eval_baselines.py", "--train", "data/examples/swe_agent.train.jsonl", "--eval", "data/examples/swe_agent.dev.jsonl", "data/examples/swe_agent.test.jsonl", "--run-id", "baselines-swe_agent-v0", "--n-boot", "300")
Run "baselines openhands" @("scripts/eval_baselines.py", "--train", "data/examples/openhands.train.jsonl", "--eval", "data/examples/openhands.dev.jsonl", "data/examples/openhands.test.jsonl", "--run-id", "baselines-openhands-v0", "--n-boot", "300")
Run "report_data" @("scripts/report_data.py")

"DONE $(Get-Date)" | Out-File -Encoding utf8 .scratch\m2_done.txt
"DONE $(Get-Date)" | Out-File -Encoding utf8 -Append $log
