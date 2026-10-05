# A batch runs in background and comes back as a digest

**This file is the protocol for both loops.** `pr-loop` and `issue-loop`
point here for any batch of more than one item; `batch=1` keeps each loop's
own one-at-a-time shape.

## Why

The user launches a batch, turns to something else, and wants to be
interrupted **twice**: when the proposals are ready to judge, and when the
approved work is done. Everything in between is silent. Heavy work is his to
do by hand; the batch only sorts it out of the way. A batch that stops
halfway to ask a question has failed at the one thing it is for.

## 1 · Launch, then free the chat

1. Say in one line what started, tagged with the loop:
   `PR · pr-loop · 5 in background — ti avviso col digest`, or
   `ISSUE · issue-loop · 3 in background — ti avviso col digest`.
   Every line this protocol prints starts with that tag: two loops can run
   from the same desk, and the tag is how a reader tells them apart.
2. Mark the rows (`notify.py --batch <ns> --working "in analisi"`).
3. Spawn one **read-only** analysis agent per item, in background (runtime.md,
   *Background delegation*), **at most four running at once**; the next starts
   as one finishes. The size of the batch is the queue's business, not the
   user's. Each loop says which skill the agent runs and on which model.
4. From a desk click, park the request so the next click is not held behind
   this one:

   ```sh
   python3 <PLUGIN_ROOT>/server/chatdesk.py park --repo <owner/repo> \
       --session <session-id> --request <key> --request-id <request-id> \
       "5 analisi in background"
   ```

5. End the turn. Do not poll: each agent's completion comes back to this
   chat by itself.

A detached one-shot process follows the same shape without `park`: its
digest is the report of its `needs-input` result.

## 2 · While it runs: nothing for the user

An agent coming back is a feed line (`notify.py --pr <n> "analisi pronta"`)
and at most one short chat line, never a question. A failed analysis is a
digest line, not an interruption.

## 3 · First interruption: the digest

When the last analysis is back:

- **tell the user once** — the host's push notification (runtime.md,
  *Telling the user*), plus a feed line;
- from a desk click, publish the request as `needs-input` with a one-line
  report (`digest: 3 pronte, 1 a mano, 2 niente da fare`) through
  `chatdesk.py result`: the row reads "serve una decisione" and the chat is
  free again;
- print the digest — **as text, never inside a code fence**, one line per
  item, in groups, empty groups left out:

(the fence below delimits the template — your output has no fence)

```markdown
**PR · digest pr-loop** — 6 righe

**Pronte** — decidi tu
1. #1145 — <titolo breve> · <la proposta, in una riga>
2. #1128 — <titolo breve> · <la proposta> · tocca lo stesso file di #1145

**A mano** — lavoro tuo, nessuna proposta
- #1188 — <titolo breve> · <perché è lavoro tuo, in una riga>

**Niente da fare**
- #1102 — <perché: aspetta @x dal 3 ottobre, già risolta, duplicato di #…>

**Fatto da solo**
- #1059 — <cosa, con i numeri veri dei check>
```

Then ask **once**: a host multi-select with one option per *Pronte* line, or a
typed answer (`1 vai, 2 no`, `tutte vai`). The four-line block of each loop
is not printed here: `dimmi di più su #1145` prints that one, and the others
keep waiting.

- **Pronte** — the proposal is one concrete action and the loop may execute
  it on a go.
- **A mano** — the item needs intense work or a decision only he can take: an
  open design choice, a WORKFLOW-sized issue, a PR whose review is a real
  design read. Say why in one line and offer the dedicated session (runtime.md,
  *Dedicated work*) — offer it, never start it.
- **Niente da fare** — waiting on a named person, already solved, duplicate.
- **Fatto da solo** — what the loop's own autonomous lane already did.

An unanswered digest is a normal ending: the request stays `needs-input`,
nothing proceeds, and the digest is still there when he comes back.

## 4 · Execution, in background again

The approved lines run under the loop's own parallel rule (`pr-loop`, *What
can run in parallel, and what cannot*): conflict graph, components in
parallel, one worktree per item, never more than four agents at once. Park
the request again, mark the rows, end the turn.

A question that comes up while an agent works does **not** interrupt: the
agent returns `needs-input` with the question, and it waits for the final
digest. A failure inside a sequential group aborts the rest of that group,
as the loop's rule says.

## 5 · Second interruption: the outcome

When the last agent is back: the push notification, a feed line, and the
outcome — **per item, never aggregate** — then the questions that came up,
one line each, under **Serve te**. Close the request: `chatdesk.py result`
with `done`, or `needs-input` while a *Serve te* line stands; by hand,
`notify.py --done run:<flow>` (git-desk, *Rows and requests*).
