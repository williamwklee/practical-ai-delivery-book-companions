PUBLIC EDITION COMPANION — pickup

Use the book’s Companion files appendix and the root START-HERE.txt. Historical task/page/commit identifiers below describe the recorded exercise, not access you need. Follow the local commands only after reading their stated limits.

# Bounded ticket pickup — DEV-79

Full readable explanation, source appendix, diagram and audits:
[publisher archive; this edition supplies the files locally]
Batch: [publisher archive; this edition supplies the files locally]

This component extends the existing DEV-78 consumer CLI runner. It adds a fixed approved queue, persistent scan and claim counters, single ownership and a single publication slot. It does not grant William’s approval or acceptance.

## Reproduce the local demonstration

Python 3.9 or later with its standard library is enough; no model API key or extra Python package is required for these deterministic checks. From this folder:

```sh
python3 -m unittest -v test_pickup test_bridge test_snapshot
python3 qualify.py --output my-local-qualification
```

The output folder must be new. `qualify.py` explicitly creates a fictional queue, starts two actual Python contenders, stages the already qualified DEV-78 result through `runner_bridge.py`, writes one local file and verifies its bytes. It launches no new AI and performs no Jira, Confluence or GitHub writes. `accepted-result.json` means the coordinator selected that result; it does not mean William accepted the work. Keep the generated SQLite file private. It contains live claim tokens for that local demonstration and is not a distributable source file.

The four JSON files in `reference-evidence` are copies of the accepted DEV-78 live-pass evidence, not a newly run model conversation. Original public process evidence and original sources are retained at the DEV-78 Confluence page 75663367, version 7, and companion commit ab7b8578029e1fd50a9a79cb3136d542ff910ecc, runner/pdca/evidence/live-pass. The bridge recomputes the request/candidate bindings and the fictional-room source checks. It trusts the coordinator’s evidence directory: it cannot authenticate arbitrary worker-supplied files or prove that a claimed reviewer process ran.

## Trusted coordinator contract

All cooperating workers use one canonical SQLite database on one local machine. `Queue.create` refuses an existing path. No new folder, copied database or new worker is allowed to recreate an operational queue or replenish its allowance. Queue creation is a trusted operator action after real authority and finite limits have been recorded. The operator supplies a fixed approved ticket list, unique approval IDs, exact request/definition hashes, expiry and explicit publisher identity. No automatic ticket creation or unlimited discovery is provided.

Workers must not have direct publication credentials or writable access to the coordinator’s state in an operational deployment. This library is a coordinator implementation, not an operating-system access-control service. The qualification processes deliberately share access for contention testing. It does not protect against a hostile same-user process editing SQLite, forging snapshots or calling trusted APIs, another coordinator with a copied database, or outside writers using shared service credentials. Those are deployment boundaries, not enforced identity checks.

Call `reserve_scan` before each connector read, even if that read may fail. A failed or abandoned read consumes its reserved scan; call `discard_scan` to record a known failure. Use the existing authenticated Jira MCP connector; no REST token or new model login is introduced. Request summary, description, status, issuetype, parent and issuelinks. `jira_snapshot.normalize` fails closed if any required field or prerequisite status is missing. It hashes the title, full description, type, parent and prerequisite key set, so removing a blocker or changing the definition invalidates an older approval. Record the approval hash using the same normalizer. Status changes do not change the approved definition.

Pass the time the actual read completed. Do not restamp old data as a fresh read. An ownership value is taken from the coordinator’s verified claim/comment record, never from Jira’s assignee field alone. Approved and Rework Required Tasks with all prerequisite tickets Done are eligible. To Do, container, blocked, owned, stale and unknown work are rejected. This minimal reference treats Rejected/Duplicate prerequisites conservatively as unresolved until the operator reconciles their links and authority.

`claim` consumes one scan and, if eligible, the one-use authority inside BEGIN IMMEDIATE. The unique ticket row and random owner token fence other cooperating workers. It orders candidates by the recorded priority and key. Jira read followed by transition remains non-atomic. After the local claim, the trusted coordinator records its owner/claim reference on Jira, transitions to In Progress and obtains a new complete read. Only `confirm_board_claim` admits model work. If that write/read fails or another owner intervenes, stop; do not launch. `validate_launch` must precede each model process and its returned expiry must bound the adapter timeout. Use the existing persistent RunLedger and DEV-78 LiveLauncher for actual model launches; this component is not a second model budget controller.

## Publication and interrupted writes

The trusted adapter stages only an exact request/result pair with a bound independent review and deterministic checks. The bridge accepts only READY_FOR_PUBLICATION, rechecks source facts and rejects modified/stopped results. The public `stage_result` API is trusted coordinator plumbing, not authentication of a worker’s supplied PASS flag.

Before the external write, the designated coordinator publisher obtains a fresh Jira read and calls `begin_publication`. One transaction consumes the publication slot and marks it unsettled. Only that returned dispatch is permitted to write the exact payload. A second contender, expired worker, wrong publisher or changed status/definition is refused. External writes are not part of the SQLite transaction; service read/write races with outside actors and provider retries are not solved by this local component. The external adapter must disable blind retries and preserve the service’s request/version receipts. No exactly-once remote effect is claimed.

Read back the saved result and use `settle_publication` with its exact hash and durable receipt. That settlement can record an already observed effect after a stop or deadline, but authorises no new write. A crash before or after the service write leaves an unsettled record and blocks another attempt. Never delete it or retry automatically. DEV-81 owns reconciliation/recovery; DEV-82 owns actual end-to-end connector integration and its failure tests. A new approved queue is not a workaround for unsettled state.

## Evidence and diagram

The final test log contains 42 passing unittest methods. Local qualification uses real competing processes and a local file sink. The live DEV-80 read was a separate negative eligibility probe: DEV-79 was unfinished, so no claim or Jira write occurred. That probe preceded the later board-read timestamp and full-definition normalizer additions; it demonstrates the unchanged dependency guard, not live qualification of those added paths. The final tests cover those additions using explicit fixtures.

BOOK-VIS-007 is an illustration, not a screenshot. Open pickup.drawio in local draw.io Desktop to edit the eight separate objects and five connectors. Export a new PNG, inspect it, update every recorded Confluence occurrence and its exact source/export/page/attachment version in inventory 75759742. Retain the separate native source. The current PNG was exported by draw.io Desktop 31.4.5 and visually inspected by the host; browser/assistive-technology behavior was not inspected.

SQLite transaction behavior: https://www.sqlite.org/lang_transaction.html (checked 28 September 2026). Only one write transaction is active at a time; BEGIN IMMEDIATE requests it before the read/claim decision. A busy database may reject the operation; this caller makes no unbounded retry.

PUBLIC EDITION SELECTION: The root COMPANION-FILES.json inventory is the current list of supplied reader files. References to original manifests, complete process receipts, export receipts or test-run logs describe the publisher’s historical archive unless those files are listed in the current inventory. All dependencies for the documented offline commands are supplied. Use the root START-HERE.txt download and restore route; no private project access is required.
