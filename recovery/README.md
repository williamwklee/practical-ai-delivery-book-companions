PUBLIC EDITION COMPANION — recovery

Use the book’s Companion files appendix and the root START-HERE.txt. Historical task/page/commit identifiers below describe the recorded exercise, not access you need. Follow the local commands only after reading their stated limits.

# Recover work without delivering it twice

This is the DEV-81 executable reference for Practical Work Delivery with Multiple
AIs. It supports research notes, presentations, reports and software equally.
The complete written and visual delivery is authoritative in Confluence:
[publisher archive; this edition supplies the files locally]

Imagine that an AI finishes a presentation and saves the file. The connection
fails before the ticket records the result. Starting again could create a
second presentation, use more allowance and lose the first review's findings.
Recovery begins by asking what actually happened, using the durable records.
It does not begin by asking the AI to repeat the task.

## The three questions at restart

1. Is the previous process still running, or is its outcome unknown? Keep its
   slot consumed. Do not start another process until the original outcome is
   reconciled. A missing process ID is not evidence that all its children ended.
2. Is the intended output already present and does it match the reviewed
   candidate? If it matches, finish the missing record. Do not deliver again.
   If it differs, preserve both the observed evidence and original candidate.
3. Does a new action remain authorised? Check the current work status, owner,
   definition, dependencies, claim expiry, stop state and remaining allowance.
   A new folder, conversation or ticket does not answer that question.

The same rule applies when the output is a slide deck, a research comparison or
a code change. A recovery log is a durable account of what is known, what is
uncertain and the next permitted action. It is not a new approval.

## What stays durable

The existing DEV-79 queue contains approval consumption, claim ownership and
the once-only publication reservation. This reference adds recovery tables to
that same registered SQLite store. It does not create a third campaign database.
The original DEV-80 budget ledger remains at its registered path and retains
all used and unsettled model attempts. There is one added recovery journal;
the two pre-existing queue and budget files are preserved, not merged or reset.
They are not claimed to form one distributed atomic transaction.

Before publication, checkpoint the original request, each candidate and each
review as exact bytes with hashes. Checkpoints append instead of replacing the
previous findings. A restored context is useful only after every stored hash
matches. A candidate's context must also match the queue's independently
verified publication proof before it can be prepared for delivery.

The work ID, request, current definition, reviewer result, output hash and
destination remain bound together. The coordinator, not a worker's claim of
PASS, supplies the verified proof. An unchanged destination matters: searching
for a similarly named file or creating another page is not reconciliation.

## A process that survives its coordinator

`supervisor.launch` reserves the original budget slot, writes the request and
launch binding, then runs the existing bounded CLI adapter in a separate
process. The adapter keeps its elapsed, first-output, idle-output, cancellation
and output-size guards if the coordinator exits. A process receipt records its
PID and observed birth time. The wrapper uses ordinary consumer CLI profiles;
it performs no login, account switch or model API call of its own.

After restart, `recover_result` opens the same attempt directory and budget
record. A matching live adapter returns `still_running`. An ended adapter must
also have its original valid terminal, exit, process-group, prompt, unit and
result-hash evidence. Only then may its original attempt settle. Calling the
inspection again returns `already_settled` without charging or launching again.
An absent optional usage field remains unknown; it does not require API billing.

The unavoidable launch-to-receipt crash window remains uncertain. A missing
receipt or missing final adapter record requires manual process/effect
inspection; the code never infers that no child exists from a dead parent PID.
A valid failed CLI result keeps its partial files and a stop. The existing
controller's reconciliation and bound resumption decision are used; no result
is promoted to a successful review merely because a process ended.

For a checker, supply a trusted post-result findings classifier. Explicit
`None` means the completed review was verified to have no findings. Missing
classification stops. Semantic interpretation is a coordinator responsibility,
not something a process hash can prove.

## When a file was saved but its record was not

`prepare_file` binds one fixed canonical path to the reviewed bytes before the
queue reserves publication. `publish_file` creates the file exclusively: it
will not overwrite an existing path. It flushes the file before settlement.
If the process or record update fails after the write, the publication stays
uncertain in the original queue.

`inspect_file` reads the existing destination. Matching bytes settle the
original publication and finish the record; repeated inspection is harmless.
This observation is permitted after a claim expires because it causes no new
output. A partial file, changed file, symlink or unavailable read does not grant
a retry. A conflicting file is preserved for a human decision.

An absent local file can be created through `resume_file` only with a current
claim and fresh status/ownership checks. The finite recovery-write counter is
consumed before the attempt. A permission failure or manual stop requires a
bound human decision after the cause is resolved. A failed or interrupted
retry still consumes its slot. This is a local exclusive-create mechanism,
not a claim that every remote service offers the same property.

## When the shared service lost the reply

A remote service can save the result even when the client receives an error.
Do not treat a timeout as proof that nothing happened. The supplied
`prepare_service`, `service_intent` and `observe_service` methods handle an
existing object identified by a stable ID, with an atomic version precondition.
They return a write contract for the trusted shared-tool connector; they do not
contain an HTTP client, authentication token or model API route.

The operator must first qualify the connector's conditional-update behavior.
The contract names that profile, exact target, prior version, payload hash and
operation marker. The connector must enforce that prior version on the actual
write and preserve the marker in its readable delivery record. For Confluence,
the integrated coordinator can use its normal tool's version/snapshot guard;
DEV-82 proves the actual connected path. This unit's outage service is synthetic.

After a lost reply, inspect the fixed target. The expected next version with
the matching marker and payload hash settles the existing publication. An
unknown read or changed content stops. If an authoritative fresh read still
shows the original version, a bounded conditional retry may be admitted with
the same precondition. If the earlier request arrives late, only one of the
two requests may win that version. The fixture actually exercises that race
ordering and records one saved effect and one version conflict.

This does not support create-by-title, blind append, email/send operations, or
an unqualified connector's promise that retries are safe. If a later version
or conflicting content is observed, preserve it and obtain a human decision;
do not overwrite it or invent a successful original delivery. There is no
universal exactly-once remote-write guarantee.

## Ownership and decisions survive restart

An expired claim prevents a new write even if the AI has the old token. A
trusted registrar can register a real, externally verified decision for the
exact claim-renewal payload and current state. `renew_claim` keeps the same
claim, owner and approval, limits the lease to the existing approved window,
rotates the token, and records the old and new expiry. The previous token is
invalid. Neither the consumed claim nor model allowance is refunded.

Grants are one-use and bind the operation, unit, exact payload, current state
and expiry. A reference string does not authenticate William. The registrar
must verify his actual instruction and identity before calling. Fictional
decision records in tests are explicitly labelled and confer no service rights.
A changed owner, definition, scope or exhausted approved window is not repaired
by this narrow lease operation.

Automatic continuation is appropriate only when standing authority already
covers the action, the cause is resolved, the state is known, ownership remains
valid, and the original finite allowance has room. Manual/substantive stops
and scope or limit increases require the applicable recorded decision.
For this book-production batch, William explicitly directed completion without
intermediate acceptance pauses after audit alignment and verified publication.
That standing batch instruction is preserved in Jira. Recovery must honour
existing authority rather than repeatedly asking for the same approval.

## Six reproducible cases

| Case | Evidence retained | Permitted result |
| --- | --- | --- |
| CLI exit | Original consumed attempt, failed process receipt and partial files | Clear the stop under a fictional registered decision; close the fixture without another launch |
| Service outage | Fixed target/version contract, unknown-read refusal, conditional retry | Exactly one simulated version change; the delayed request conflicts |
| Lost context | Every original request/candidate/review checkpoint and its hash | Restore the latest checked result without losing prior unresolved findings |
| Expired ownership | Expired-token refusal, exact decision and lease amendment | Same claim, rotated token, no refunded approval or model slot |
| File before record | Saved bytes, missing settlement and original publication token | Finish the record after expiry without a second file write |
| Replayed authority/budget | Completed original claim, zero active claims, three consumed model slots and reopen refusals | Approval replay refuses with authority_consumed; a fourth model launch refuses with launches_exhausted |

Use Python 3.9 or later on the qualified macOS workstation. The wrapper reuses
POSIX process groups and `ps`; these process semantics have not been qualified
on Windows. Follow the root START-HERE.txt, open a terminal in the restored recovery folder and run:

    python3 -m unittest -v test_recovery test_supervisor
    python3 qualify.py --output my-new-fictional-recovery-evidence

The qualification command deliberately creates isolated fictional cases. It is
never an instruction to replace an operational campaign. There is no loop that
keeps retrying until success. Each case is finite and closes its fixture state.

The CLI-exit case proves stop reconciliation and a fictional decision to clear
it; it does not exercise a resumed launch. The separate
`test_supervisor.test_cli_exit_failure_retains_partial_evidence_and_requires_manual_resolution`
test retains the failed adapter record, partial files and active missing-evidence
stop before any decision. No real William approval is implied by the fixture.

The tests separately exercise a real coordinator exit, surviving local work,
missing process receipts, corrupted context, partial files, changed status and
ownership, conditional-write races, permission stops, one-use grants and finite
retry exhaustion. Their Python workers and simulated service calls are not AI
calls or real Jira/Confluence writes. Actual independent Opus audit processes
are counted separately in the book's execution ledger.

## Boundaries and maintenance

This is one trusted coordinator, not a hostile-worker or distributed security
boundary. Ordinary workers must not hold the journal, registrar or publisher's
write authority. Protect the registered stores and credentials using the
workstation's access controls. Direct database edits, old-backup restoration
or a replacement campaign can defeat local counters; their prevention is an
administrative deployment obligation. This code rejects copied registered
paths and repeat installation but does not provision OS isolation.

Process-interruption recovery is exercised. Power-loss durability, browser
rendering, assistive-technology behavior, distributed writers and real service
outage behavior are not measured here. A local process ending does not prove
remote provider work stopped immediately. Native token/list-cost fields are
diagnostics, not invoices, remaining subscription quota or a hard provider cap.

Confluence holds the full text, source, evidence, diagram and unedited audits.
GitHub provides exact companion files. Jira carries the work unit and native
dependencies. Each AI retains its real Jira/Confluence identity; a Codex-relayed
Opus report is not a Claude service write. GitHub identity may be shared with
its stated attribution limits. Claude reviews; Codex writes and publishes.
No extra reviewer write permission or new login is required.

BOOK-VIS-009 is an editable draw.io illustration, not a missing screenshot.
Revise its native objects and connectors, export the PNG locally, inspect it,
replace every registered Confluence occurrence and update the visual inventory.
The complete integrated consumer-CLI/shared-tool exercise remains DEV-82.

Lesson: uncertainty is a recorded state. Keeping it prevents an apparently
helpful restart from repeating work, losing findings or silently restoring an
already consumed allowance.

PUBLIC EDITION SELECTION: The root COMPANION-FILES.json inventory is the current list of supplied reader files. References to original manifests, complete process receipts, export receipts or test-run logs describe the publisher’s historical archive unless those files are listed in the current inventory. All dependencies for the documented offline commands are supplied. Use the root START-HERE.txt download and restore route; no private project access is required.
