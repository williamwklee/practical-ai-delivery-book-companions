PUBLIC EDITION COMPANION — limits

Use the book’s Companion files appendix and the root START-HERE.txt. Historical task/page/commit identifiers below describe the recorded exercise, not access you need. Follow the local commands only after reading their stated limits.

# Keep an AI work loop within its allowance

This is the DEV-80 reference for Practical Work Delivery with Multiple AIs.
It is useful for research, presentations, documents and software: the output
changes, but every attempt still needs an owner, an allowance and a stop rule.
The complete written and visual delivery in Confluence is authoritative:
[publisher archive; this edition supplies the files locally]

## What the controller does

Before a worker starts, it records one consumed process slot. Producer,
checker, correction, retry and helper work all count. A permitted child counts
against itself, its parent and the overall campaign, once at each level.
The smallest applicable allowance wins. Only one process may be unsettled in
this reference; another admission stops rather than guessing that it finished.
Registered work IDs and operations form a fixed list. Finding a new ticket
does not approve it, and registering a new directory does not restore usage.

A cycle is explicitly started before its processes. Cycles, reviews,
corrections, retries and launches each have finite counts. A fixed deadline
survives reopening the database. The adapter receives the earliest controller,
claim and per-process deadline, with finite first-output, idle-output and total
time limits. The code detects a wall-clock rollback and stops; it is not a
trusted-clock security system. Use an ordinary stable workstation clock.

Every exhausted limit records its counter as structured data. Resumption reads
that field rather than interpreting the wording of the stop message. Tests
explicitly refuse unchanged exhausted cycle, correction, review and retry allowances.

The controller keeps the reason for a stop, partial-output references, consumed
and unsettled attempts, and the conditions for resuming. Settling or inspecting
an existing attempt can continue after a stop; those operations do not launch
another process or send another deliverable. Reconciliation records an observed
ended process and checked effects. It neither invents a successful result nor
approves a new attempt. Full recovery of remote writes belongs to DEV-81.

An ordinary local error can occur after reservation but before any process starts,
for example an output folder that already exists. The bridge then records
`abandoned_before_launch` with `process_started: false`; it keeps the consumed
slot and records the failure class. An existing output folder is a known,
potentially recoverable transient case. A permission failure stops as outside
authority; any other pre-launch exception stops as missing evidence. Those
last two classes need a recorded human decision after the cause is checked.
The record never claims that an unstarted process ended. Once a transient
cause is resolved, a charged retry is permitted only if the
same approved allowance still has room. With no room, a recorded amendment
and decision are needed. Immediately before the adapter call, a durable
`launching` boundary is recorded. After that boundary the no-launch shortcut
is forbidden: an uncertain outcome needs actual reconciliation evidence.

## Eight reasons to stop

| Case | What happens | What permits resumption |
| --- | --- | --- |
| Product quota or authentication warning | Preserve the warning and stop. | Resolve the condition, then record William's decision. No account switch or paid API fallback. |
| Optional observed usage threshold | Add reported usage; stop when the observed total reaches the chosen threshold. | A recorded limit amendment if still exhausted, then a bound resumption decision. |
| Time, cycle or process allowance | Refuse the next operation before reserving or launching it. | Record an amendment for the exhausted allowance and William's decision to resume. |
| Repeated findings | Stop when the same findings reach the stated repetition limit. | A changed plan and William's recorded decision. A different result still needs review. |
| Transient failure | A resolved temporary cause may allow a retry within unchanged limits and ownership. | A recorded resolution; the next process must be a charged retry. Exhaustion or changed policy prevents automatic resumption. |
| Missing mandatory process/result evidence | Leave the attempt unsettled; keep partial output. | Reconcile its process/effects, then record William's decision. |
| Operation outside authority | Refuse the operation or unknown work ID and preserve a stop. | Correct the request, or obtain an explicit scope amendment, then obtain resumption authority. |
| Manual stop | Record the decision and signal the active local adapter. | William's decision after checking the current state; clear the campaign STOP file only after all applicable stops are resolved. |

An unchanged finding is a fingerprint of the findings for the same unit.
The bridge calls the trusted `findings_classifier(final_text, process_record)`
after the actual review finishes. It must derive a consistent fingerprint from
that output. An explicit `None` means the coordinator verified that there are
no findings; it resets the repetition count. It must never mean unknown or
unparsed. With no classifier, a successful checker result stops as missing
classification evidence rather than silently clearing the count. Its complete
process is retained as settled, with a missing-evidence stop requiring actual
classification and William's resumption decision; it is not accepted as a clean
review. A classifier
exception leaves an uncertain attempt requiring reconciliation. The controller
cannot establish whether a model's prose represents actual progress or whether
a supplied classifier interpreted it correctly. Supplying and qualifying that
interpretation in the live workflow remains DEV-82 work.

## Approval is a real decision, not a string

`register_authority` is a trusted operator interface. Before calling it, the
coordinator must read the genuine external record, check William's authenticated
identity and explicit instruction, and bind the decision to this exact scope,
old policy, new policy or stop, and expiry. A reference or a name is not proof
of authentication. Ordinary workers must not call the registrar. Our fixture
references explicitly say they are fictional and give no service authority.

The registered grant is one-use. A resumption grant binds the exact stop ID,
resolution-evidence text and changed plan, including `None` when absent.
Changing even the evidence text requires a matching new grant. An amendment must match its exact payload and
the current policy fingerprint. The append-only event stores the old and new
limits, deadline difference, authority reference, time, unit and all existing
attempt IDs/states plus cumulative consumption. An amendment does not alter a
running attempt's deadline, reset counters, clear stops or enlarge another
scope. The overall cap still applies when a unit cap is increased. Reopening
or changing the attempt's name cannot bypass these records.

Automatic resumption is restricted to a documented transient failure with an
ended process or a proven pre-launch abandonment, no unsettled attempt, the same coordinator and unchanged policy
at every applicable level. The next attempt is explicitly a retry. A retry of
a checker also consumes a review slot; a retry of a correction also consumes a
correction slot. A transient stop must name a failed work-unit attempt; the
campaign as a whole cannot receive a retry entitlement. Campaign-wide manual
stops can resume under their bound human authority without creating one.
There is no automatic retry loop or sleep loop in this library.

## Run the local evidence

Use Python 3.9 or later on the macOS reference workstation. The controller uses
only the Python standard library. The unchanged process adapter uses POSIX
process groups and `ps`; these process checks have not been qualified on Windows.

Use the root START-HERE.txt to restore the companion files. Open a terminal in the restored limits folder and run:

    python3 -m unittest -v test_controller test_bridge
    python3 qualify.py --output my-local-evidence

The output folder must be new. This command explicitly creates fictional
fixtures; it is never a command for recreating a stopped operational campaign.
Its eight case groups preserve stops, failed admissions, decisions, amendments,
resumption and final closure. `after_resume` is the snapshot after the resumed
demonstration; `final_stopped` additionally includes the final campaign-closing
stop. It also starts one actual Python fixture through
the existing adapter, then refuses a second process before creating its run
folder. None of those processes calls an AI or writes to Jira or Confluence.
Synthetic token fields belong to fixture evidence, not actual model accounting.

Tests separately exercise actual timeout, manual cancellation, partial output
without a terminal event, and a complete result with missing optional usage.
The last case is deliberately different from the earlier adapter's conservative
`success` flag: the bridge rechecks the mandatory process, binding, model, final
bytes and terminal fields, and permits absent optional usage without inventing
zero measured usage. The original adapter record remains unchanged and visible.

## Fit this into the existing workflow

1. The existing DEV-79 queue checks the current Jira status, native prerequisites,
   work approval and exclusive claim. A Task must be authorised and claimable;
   an Epic is never a worker's job. William retains human acceptance.
2. The trusted coordinator opens the one registered controller database using
   its fixed configured path. All permitted unit and child IDs were registered
   at the original campaign admission. It validates the queue claim before
   every call and passes that claim's expiry to `run_guarded`.
3. The same producer/reviewer launcher boundary used by DEV-78 calls
   `run_guarded` with the existing consumer CLI profile, normal sign-in, exact
   prompt and current context. Reserve first; start the native process once;
   preserve the process and result receipts. The bridge performs no login,
   model selection, ticket scan or service write.
4. Supply the trusted post-result classifier for checker calls. It interprets
   the actual independent review and source checks before settlement. Record
   a stable findings fingerprint, and start the next authorised cycle only if all limits permit.
   A stopped controller cannot confer publication authority. The queue still
   owns result binding and publication; DEV-82 owns the integrated live exercise.
5. Use `manual_stop` to persist the stop and create the campaign's STOP file.
   The adapter observes that file and terminates its local process group.
   After authorised resumption and reconciliation, inspect all active stops
   before removing that file; removing the file alone does not resume the ledger.

This delivery qualifies the controller and its direct existing-adapter bridge.
It does not claim that the complete DEV-78/79/80/81 workflow has run live; that
is DEV-82's assigned work. No existing dispatcher is armed. Consumer CLI
sign-ins remain the model route; no model API key or API billing account is used.
Codex writes and publishes; Claude reads and audits. Each AI's real Jira and
Confluence identity still needs its own verified attribution. GitHub may use
a shared identity with its stated limits. The bridge does not test, replace or
require a new login for Claude's working interactive MCP connection.

## Boundaries that affect the result

This is one trusted local coordinator and one SQLite file, not a distributed
security boundary. BEGIN IMMEDIATE serialises its local decisions. Cooperative
callers get persistent counters; a hostile process with database write access
can bypass them. Keep operational state, authority registration, owner secrets
and shared-tool credentials outside ordinary workers' write access. This
library does not provision that OS isolation or authenticate a Jira user.

The file records its canonical path and refuses a copied file at a new path.
Bootstrap refuses an existing file. Operational callers must always open the
same registered file. Deleting it, creating a fresh campaign elsewhere, restoring
an old backup or editing SQLite can defeat local state; administrative protection
and recovery of that registry are deployment obligations, not guarantees here.
The audit fixtures intentionally have distinct stores; they are not restarts.

Optional usage is explicitly known or unknown. An observed threshold can be
crossed by the process already running, and absent telemetry may conceal usage.
It is not a hard provider token/dollar cap, invoice, or remaining subscription
quota. CLI launches are not hidden provider requests. A local timeout does not
prove remote processing stopped immediately. A missed cancellation-file write
leaves the persisted stop in place and the finite adapter timeout in force.

No real quota was exhausted. No browser rendering, screen-reader behavior,
distributed workers, hostile-worker separation or integrated live service run
was measured in this unit. The qualification includes zero fresh AI processes;
actual independent delivery audits are separately counted in the shared ledger.

One campaign uses one registered store. All handles to that same store derive
the same STOP path from its SQLite filename. `Controller.stop` records state
only; `adapter_bridge.manual_stop` also writes the signal file, outside the
database transaction. The eight-group manual-stop case exercises persistence
and resumption. The separate actual-subprocess test exercises signalling and
local termination. New stop decisions get distinct IDs and must each be
resolved; acknowledgement of an already recorded manual cancellation is an
event, not an extra decision. Transient resumption names its exact failed
attempt rather than selecting whichever attempt happens to be latest.

Claude's four reported usage fields and Codex's input-plus-output fields have
different provider definitions; their observed thresholds are not interchangeable.
Unknown usage remains unknown. Test-result elapsed time is the parent process's
wall clock, including startup and teardown. The unittest log reports its own
test-run duration; both labelled measurements are retained.

## Editable illustration

BOOK-VIS-008 is a generated diagram, not a screenshot or missing image.
Edit the separate objects and connectors in `limits.drawio`, export `limits.png`
with local draw.io, inspect it, replace every registered Confluence occurrence,
and record exact source/PNG/attachment/page versions in the visual inventory.
The Confluence text, code, visual and evidence are the authoritative delivery;
these companion files and their archive provide reproducible sources.

The reused `upstream/adapter.py` and `upstream/fixture_worker.py` are byte-equal
to `runner/pdca/` at GitHub commit ab7b8578029e1fd50a9a79cb3136d542ff910ecc,
the accepted DEV-78 delivery. The historical upstream-proof.json readback is retained in the publisher archive. The public edition’s root manifest identifies the supplied source bytes.

PUBLIC EDITION SELECTION: The root COMPANION-FILES.json inventory is the current list of supplied reader files. References to original manifests, complete process receipts, export receipts or test-run logs describe the publisher’s historical archive unless those files are listed in the current inventory. All dependencies for the documented offline commands are supplied. Use the root START-HERE.txt download and restore route; no private project access is required.
