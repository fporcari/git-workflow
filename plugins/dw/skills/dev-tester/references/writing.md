# Writing a collaudo

The reader is the person who reported the problem, or the one who will test the fix
on the real data. They know the application, not the code. Everything in the page is
written for them, in their language.

## Answer (per case)

- One to three short paragraphs. First sentence: what now works, in the words of the
  report ("the card refund now matches its credit note"), not the name of the change.
- Then what they will see differently: labels, amounts, where a posting now lands.
- Then what does NOT fix itself: existing data that needs an action after the deploy,
  and which action. Omit this sentence only when nothing is left to do.
- Two separate causes → a two-item list, one cause each.
- No function, table or file names unless the tester sees them in the application.

## Steps

- One action per step, on the real record the report names: document, number, date,
  amount. "Open invoice 1062 of 08/09/26", never "open an invoice".
- UI labels exactly as the application shows them. Read them from the code (button
  and tab labels) instead of paraphrasing; a label you could not find → describe
  the place and say so in the step. Italian pages quote labels in «».
- Order: read-only checks first, then the steps that change data. When the fix
  changes existing data, step 1 records the before-state (screenshot).
- A refusal the fix introduces or keeps is a step of its own, with the refusal as
  the expected result, placed before the step that does the real operation.
- Mark `modifies_data: true` on every step that writes: linking, confirming,
  recalculating, importing, and a refusal test that would write if it failed. The
  page flags them and tells the tester to stop after a KO.
- Five to nine steps per case. More → the case is two cases.

## Expected result

- What the tester can see on screen, with the numbers: "total 107.91", "the two
  credits of 15/06 stay free (3,500.00)". One result per step.
- Amounts and dates in the reader's locale format.
- Never "works correctly" or "as expected": say what appears.

## Watch out for

Draw each item from a source, never from general caution:

- PR "Not verified" → "this is the first check on real data" for that part.
- PR limits → the refusals that are expected, so a KO is not reported for them.
- PR out of scope → what stays as it is and must not be fixed by hand.
- Preconditions → was it already handled by hand or with a workaround? Stop and ask.
- Duplicates and idempotency → what running a step twice, or after a workaround, does.
- Facts inferred from the code and not checked on the data → say so plainly.
- A side remark from the report that is not the cause → one item at most.

## Open questions and scope

- Unknown deploy date or test environment → one open question each, never guessed.
- Only the cases asked for. A related problem found on the way → a reference row
  (`extra_references`), not a new case.
- Never put credentials, tokens, internal hostnames, or personal data beyond the
  names the report itself carries.
