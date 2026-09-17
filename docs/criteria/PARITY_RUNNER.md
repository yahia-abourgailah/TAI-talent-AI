# The parity runner: switching on the rule-change check (BR-304, BR-702)

The master record never leaves company machines, so the golden replay runs only on a self-hosted
GitHub runner inside the network. Until the runner exists, CI fails **any** change under
`src/scoring` or `src/replay` on purpose, and scoring stays frozen.

Infrastructure owns the machine. Person A owns the job (`parity` in `.github/workflows/ci.yml`).

## 1. The machine (Infrastructure)

- Linux, inside the company network, with Python 3.12 and outbound HTTPS to github.com.
- The master workbook on local disk, **outside** any git checkout, readable only by the runner's
  user (`chmod 600`).
- No inbound ports.

## 2. Register the runner (Infrastructure, with a repository admin)

Repository → Settings → Actions → Runners → **New self-hosted runner**, then on the machine:

```bash
./config.sh --url https://github.com/<owner>/<repo> --token <registration token> \
  --labels talent --name talent-parity --unattended
sudo ./svc.sh install && sudo ./svc.sh start
```

The runner must carry the label `talent`. The job asks for `[self-hosted, talent]`.

## 3. Point the job at the workbook (repository admin)

- Secret `TALENT_MASTER_PATH`: the absolute path of the workbook on the runner.
- Variable `PARITY_RUNNER_READY` = `true`.

```bash
gh secret set TALENT_MASTER_PATH --body "/srv/talent/TAI_Master.xlsx"
gh variable set PARITY_RUNNER_READY --body true
```

## 4. Prove it (Person A)

A throwaway branch that changes a weight must turn CI red, and the revert must turn it green. The
branch is never merged.

```bash
git switch dev && git pull
git switch -c chore/p6-parity-proof
# change one weight, e.g. in _score_location: `return 30, flags, signals` -> `return 25, ...`
git commit -am "chore: parity proof - change a weight (do not merge)"
git push -u origin chore/p6-parity-proof      # open a draft PR into dev: parity goes red
git revert --no-edit HEAD
git push                                      # parity goes green
```

On the red run, the job's summary shows the replay report (score differences, and tiers stored
against replayed). The `shadow-diff` artifact holds the same report and `tier_moves.html`: who
moves tier, counted from → to, with twenty sheet rows as the sample. Measured locally on
2026-09-17 by changing the near-New-Cairo weight from 30 to 25: 1,129 scores differ, 260
candidates change tier (236 P1 → P2), and the job exits 1.

Record the two run links here once done:

| Run | Result | Link |
|---|---|---|
| Weight changed | red expected | — |
| Reverted | green expected | — |

## A real rule change, later

A new criteria version is a new module under `src/scoring/rulesets/`, registered in
`scoring/versions.py`. Before any opening uses it:

```bash
python -m replay.compare --from 2026-08-04 --to <new version> --run-date <date> \
  --html artifacts/tier_moves_<new version>.html
```

Attach the page to the pull request. The criteria owner signs it.
