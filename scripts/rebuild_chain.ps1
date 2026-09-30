# Full rebuild of the example records after a state-builder change, then the v0 files, checks,
# QA reports, data report, and upload of the v0 files to the private HF dataset repo.
# Launch detached:
#   Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','D:\Fine-tune\scripts\rebuild_chain.ps1'
# Progress: .scratch\rebuild_chain.log ; completion marker: .scratch\rebuild_done.txt

$ErrorActionPreference = "Continue"
Set-Location D:\Fine-tune
$log = ".scratch\rebuild_chain.log"
"START $(Get-Date)" | Out-File -Encoding utf8 $log
$tok = ".scratch/tok/qwen3.5-2b/tokenizer.json"

function Run($step, $extras, $argsList) {
    "== $step $(Get-Date)" | Out-File -Encoding utf8 -Append $log
    & uv run --python 3.12 @extras python @argsList 2>&1 | ForEach-Object { $_.ToString() } | Out-File -Encoding utf8 -Append $log
}

$ex = @("--with", "pydantic", "--with", "tokenizers", "--with", "numpy", "--with", "scikit-learn", "--with", "huggingface_hub")
Run "build swe_agent" $ex @("scripts/build_examples.py", "--in", "data/unified/swe_agent.jsonl", "--out", "data/examples/swe_agent", "--tokenizer", $tok, "--workers", "3", "--max-train-traj", "15000")
Run "build openhands" $ex @("scripts/build_examples.py", "--in", "data/unified/openhands.jsonl", "--out", "data/examples/openhands", "--tokenizer", $tok, "--workers", "3", "--max-train-traj", "12000")
Run "make_v0_split" $ex @("scripts/make_v0_split.py", "--train-per-dataset", "25000", "--dev-per-dataset", "5000")
Run "check_v0" $ex @("scripts/check_v0.py", "--examples", "3")
Run "label_qa swe_agent" $ex @("scripts/label_qa.py", "--in", "data/examples/swe_agent.dev.jsonl", "--out", "docs/label_qa_swe_agent.md", "--n", "25", "--max-records", "60000")
Run "label_qa openhands" $ex @("scripts/label_qa.py", "--in", "data/examples/openhands.dev.jsonl", "--out", "docs/label_qa_openhands.md", "--n", "25", "--max-records", "40000")
Run "report_data" $ex @("scripts/report_data.py")
Run "upload v0" $ex @("-c", "import time; from huggingface_hub import HfApi
api = HfApi()
for i in range(8):
    try:
        api.upload_folder(folder_path='data/v0', repo_id='azlanmalikai/agent-compass-data', repo_type='dataset', allow_patterns=['*.jsonl', 'stats.json'], commit_message='v0 rebuilt: padded lines collapsed, think steps hidden, strict prefix filter'); print('uploaded'); break
    except Exception as e:
        print('retry', i, type(e).__name__); time.sleep(15)
")

"DONE $(Get-Date)" | Out-File -Encoding utf8 .scratch\rebuild_done.txt
"DONE $(Get-Date)" | Out-File -Encoding utf8 -Append $log
