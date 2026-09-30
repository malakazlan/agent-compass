# Pre-launch checks on v0 (rebuilt 2026-09-30)

Output of `scripts/check_v0.py` on the rebuilt files (padding collapsed, think steps hidden, strict prefix filter). Examples were read by hand; see the session notes in docs/label_qa_findings.md.

## Balance
- train: 49987 records, pass rate 32.5%  per dataset: SWE-rebench-openhands-trajectories 25002 @ 48.2%, SWE-agent-trajectories 24985 @ 16.9%
- dev: 9999 records, pass rate 29.2%  per dataset: SWE-rebench-openhands-trajectories 5000 @ 42.9%, SWE-agent-trajectories 4999 @ 15.4%
- test: 64124 records, pass rate 35.5%  per dataset: SWE-agent-trajectories 33230 @ 19.1%, SWE-rebench-openhands-trajectories 30894 @ 53.1%

## Leakage
- train: prefix == full trajectory: 0 (one-step trajectories: 0); last visible action is submit/finish: 0; harness-pattern hits: 1338; confident-phrase hits by label: {False: 2158, True: 2392}; pytest-summary hits by label: {True: 8519, False: 8013}
- dev: prefix == full trajectory: 0 (one-step trajectories: 0); last visible action is submit/finish: 0; harness-pattern hits: 307; confident-phrase hits by label: {False: 404, True: 404}; pytest-summary hits by label: {False: 1759, True: 1332}
- test: prefix == full trajectory: 0 (one-step trajectories: 0); last visible action is submit/finish: 0; harness-pattern hits: 1297; confident-phrase hits by label: {False: 2398, True: 3487}; pytest-summary hits by label: {True: 11869, False: 9555}

## Repo / task overlap
- train vs test: shared repos 0, shared tasks 0  (1517 / 223 repos)
- train vs dev: shared repos 0, shared tasks 0  (1517 / 187 repos)
- dev vs test: shared repos 0, shared tasks 0  (187 / 223 repos)

