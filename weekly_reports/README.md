# Weekly agent reports

This is the reporting side of an agent-reliability engagement. Phoenix records every model-backed chat. The report is the approved test set joined to those traces, scored, then rolled up: run, then session, then week.

The client buys the report. Trace links are evidence for engineers.

## The four units

| Unit | What it is | Example | How long it lives |
| --- | --- | --- | --- |
| Test case | One question plus what "right" means | `T-014` | The whole engagement. Never renumber it. |
| Run | One send of that question | `T-014-r2` | One reply |
| Session | One sitting. Same model, prompt version, and step count | `20261017-1430-baseline` | A few hours |
| Week | Monday through Sunday | `2026-W42` | The report period |

One Phoenix project per client and environment, for the whole engagement: `sharkk-petto-staging`. Sessions are labels inside that project.

A new model, prompt version, or `stepCount` is a new session. The week total includes only the version you would ship. Other sittings stay in their own columns. A re-test is a new session id. Old rows are not edited.

## How a week moves

```
approved test set
        +
send log  (session_id, test_id, run, sent_at, reply, trace_id)
        +
Phoenix spans  (one project, metadata stamped on the chat span)
        |
        v
normalize_spans        keep tool spans, one bundle per trace
        |
        v
join                   trace id, else session_id + test_id + run
        |
        v
score                  tool, args, tool result, answer, loop
        |
        v
rollup                 session columns, week total for the ship version
        |
        v
findings + report      pattern cards, 15% review queue, HTML and CSV
```

Question text is not a join key. Two cases can share a question.

## What you produce during a session

1. Phoenix is running. One project for the engagement.
2. Fresh chat per case, so case 12 does not inherit case 11.
3. Send each approved case three times (`r1`, `r2`, `r3`).
4. Stamp the span and append the send log on every send.

```python
from reliab.instrument import SendLogger, new_session_id, stamp_span

session_id = new_session_id("baseline")          # 20261017-1430-baseline
stamp_span(
    span,
    session_id=session_id,
    test_id="T-014",
    run="r1",
    agent_version="petto-2.3",
    prompt_version="p14",
    step_count=8,
    model="gpt-4.1-mini",
)
SendLogger("send_log.jsonl", session_id).record(
    test_id="T-014",
    run="r1",
    reply=reply,
    trace_id=trace_id,
    question=question,
)
```

`examples/stamp_example.py` is the same snippet with a fake span.

A chat sent while Phoenix is off is not recoverable. It shows up as untraced and is left out of the rates. A plain "yes" that never reaches the model is also untraced. Mark that case `score_from_reply=yes` and it is scored from the reply alone.

## What you run at the end of the week

From `weekly_reports`, with `src` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "src"
python -m reliab demo --out out/2026-W42
```

That writes a Sharkk / Petto week (baseline prompt on 17 Oct, prompt fix on 18 Oct, both inside 2026-W42) and renders the report.

For a real week:

```powershell
python -m reliab export-phoenix --project sharkk-petto-staging --week 2026-W42 --out spans.jsonl
python -m reliab weekly `
  --engagement engagement.json `
  --cases test_cases.csv `
  --sessions sessions.json `
  --send-log send_log.jsonl `
  --spans spans.jsonl `
  --out out/2026-W42
```

`export-phoenix` pulls each day on its own. The Phoenix UI caps a view at 1,000 spans, and a week can pass that. Set `PHOENIX_ENDPOINT` and, if the server requires it, `PHOENIX_API_KEY`. Install the client with `pip install arize-phoenix-client` when you want the live export. The rest of the pipeline uses the standard library.

`--strict` exits non-zero when a done-when check fails.

## What comes out

| File | Who it is for |
| --- | --- |
| `report.html` | The client. Cover, score, verdict, scorecard, week table, before/after, findings, roadmap. |
| `report.md` | The same report in text. |
| `results.csv` | One row per run. The scorecard is this file, counted. |
| `week_table.csv` | The grid in long form, for the next cycle. |
| `review_queue.csv` | About 15% of ship-version answers, for a person to sign. |
| `findings.json` | Pattern cards. A finding is two or more fails of the same case and reason. |
| `untraced.csv` | Sends with no span. |
| `manifest.json` | Week, ship version, rules version, hash of the spans. |

## How a run is scored

In order:

1. **Tool.** Pass if the expected tool was called at least once. Fail if a forbidden tool appears. Fail if `needs_tool` is yes and the list is empty. Fail if a tool runs when the case needs none. A tool outside the allowed list fails. A blank allowed list means "only the expected tool."
2. **Args.** Pass if every `must_include_args` field is present and matches. Extra fields such as `lat` or `lng` do not fail. `n/a` when the expected tool never ran, and `n/a` is left out of the args rate.
3. **Tool result.** Pass if the result matches `result_expects` (default `ok=true`). A booking case can also require `needsConfirmation=true`. If this column fails, the bug is the tool or the data, not the wording.
4. **Answer.** The reply may name only things in the tool result or the question. This build uses a rule check. Replace it with a model by passing any object with a `judge(case, reply, tool_results, question)` method to `run_week`. A person signs the review queue before the grounded number is final.
5. **Loop.** The same tool three or more times in one trace is a fail, even when it was the expected tool.

Overall status fails if any scored column fails.

## Ship version

Mark `"ship": true` on the session you would actually release. Every session with that same version key (agent, model, prompt, step count) is included in the week total. A sitting whose prompt changed is a before/after column, not part of the average.

The demo week is the picture from the spec: Saturday's baseline skips tools and calls a forbidden one. Sunday's prompt fixes that. The booking tool still confirms too easily, and a profile lookup still loops. The verdict is Not ready, because the booking pattern is critical. The grounded score stays provisional until the review queue is signed.
