PUBLIC EDITION COMPANION — software

Use the book’s Companion files appendix and the root START-HERE.txt. Historical task/page/commit identifiers below describe the recorded exercise, not access you need. Follow the local commands only after reading their stated limits.

# A small calculator you can specify and check

Authoritative instructions, source, actual results, findings and visuals:
[publisher archive; this edition supplies the files locally]

This is one software example beside a research brief and an editable presentation. You do not need to be a software engineer to state the expected result, reject a misleading percentage, or ask for a usable error message. Running this particular companion needs Python 3.9 or later and a terminal. It uses the standard library, no network, no model API key and no third-party Python packages.

## Try the checked result

Download and extract the complete bundle into a new exercise folder. Open a terminal there and run:

    python3 corrected/compare_amounts.py 100 80

The example treats both amounts as millimetres, but the calculator is unit-neutral: use the same unit for both inputs. The result is one JSON object. Its numeric fields are decimal strings rounded to two fractional digits using half-up rounding. You should see amount 100.00, baseline 80.00, difference 20.00, percent_above 25.00 and percent_of 125.00, plus the explicit warning that a comparison establishes no trend, cause or forecast. These are fictional rainfall inputs. The program does arithmetic; it is not a climate analysis.

Check the result yourself: subtract 80 from 100 to get 20. Divide that extra 20 by the baseline 80 and multiply by 100 to get 25 percent above. Dividing the whole 100 by 80 gives 125 percent of the baseline. Those phrases mean different things.

## Make the stop visible

Run once with a missing baseline:

    python3 corrected/compare_amounts.py 100

The program must print one helpful STOP line to the error stream, return exit 2 and emit no result to the normal output stream. It must not guess the missing value or show a Python traceback. Supply the intended baseline explicitly and run the valid command above once. This corrects an input, not an AI retry loop. Keep both results under the same work-unit record and original limits. Zero is a valid amount, but a zero baseline cannot be used for this percentage comparison.

The contract accepts only ordinary ASCII decimals: an optional leading plus, digits and an optional decimal point. It rejects negative inputs, surrounding spaces, exponent notation, NaN, infinity, more than 32 characters, amounts above 100000 and baselines outside greater-than-zero through 100000. These are deliberately small operational limits for the exercise, not scientific rainfall limits. Percentages can be negative when the amount is below baseline; inputs themselves cannot be negative. Very small valid baselines can produce very large percentages. That arithmetic alone does not establish a useful real-world interpretation.

## Run the independent checks

    python3 test_compare.py corrected/compare_amounts.py

The fixed acceptance examples cover a larger, equal, smaller and zero amount; thirds and half-up rounding; alternate accepted decimal spelling; upper and longest-small-baseline boundaries; and 17 invalid-input cases. They launch separate short-lived local Python processes, each with a five-second timeout. No AI is called by the tests. A successful suite is evidence for these cases, not a proof of every possible input or platform.

Rounding can hide a very small difference. A negative value that rounds to zero may appear as -0.00; the contract permits that sign. The review also notes that the explanatory en dash in one STOP message can display differently under an ASCII locale. These are disclosed behavior choices, not failed acceptance checks. If you maintain the code, keep its five numeric formatting calls inside the high-precision decimal context; moving them outside can break very large valid ratios.

The retained seed used binary floating-point input, confused percent above with percent of, and lacked the required error handling. The raw seed test log and the actual Opus findings remain historical. The producer corrected the complete source; the host inspected it before execution, then ran the independent fixed expectations. The reviewer received the exact source, same contract and attributed actual test evidence. Agreements and test passes have distinct meanings: one challenges the reasoning, the other checks observed program behavior.

## Reuse the delivery method

State who will use the output, accepted inputs, expected examples and stop behavior before asking an AI to write it. Keep the unit small enough to check independently. Give a second AI the full request and exact candidate; retain its actual findings, including false positives. Correct supported defects, rerun affected checks and request another review under unchanged limits.

For this recorded example, Codex writes shared records and actual Claude Code Opus High reviews supplied material using normal consumer CLI sign-ins. Each AI's shared-tool identity remains distinct; a relayed report is attributed to Claude but published under Codex's account. The private GitHub connection is shared and its attribution limits are explicit. The included host composition is a record of the bounded producer workspace, not a standalone unattended dispatcher. Its qualified controller and adapter are upstream dependencies. Do not replay the completed Jira task or copy its operational database to create a new allowance.

The editable comparison-flow.drawio and its exported PNG explain the stop path. Edit the native source and re-export when the flow changes, then update the canonical page, attachment and separate inventory. Exact companion files are derived from the canonical Confluence page/version in manifest.json. Current source, inputs, outputs, findings and lessons are fully readable there; GitHub is the durable file companion. Tokens and list-cost observations are diagnostics, not an invoice, quota balance or provider cap.

PUBLIC EDITION SELECTION: This folder supplies the files named in the root inventory. Producer-workspace orchestration scripts, private service receipts and complete historical review bindings remain in the publisher archive; they are not required to open or check this example. References above to a complete original bundle/history describe that archived delivery, not an assertion that every archival file is included here. The root manifest is the current file inventory. The source report for the deck is ../research/report.md; private Confluence links in slide notes are optional historical provenance. Public source links and fictional source inputs remain available in source-pack.json. No recorded operational allowance may be replayed.
