"""Deliberately defective teaching seed. Do not use as the final calculator."""
import json
import sys

amount = float(sys.argv[1])
baseline = float(sys.argv[2])
print(json.dumps({
    "amount": f"{amount:.2f}",
    "baseline": f"{baseline:.2f}",
    "difference": f"{amount - baseline:.2f}",
    "percent_above": f"{amount / baseline * 100:.2f}",
    "percent_of": f"{amount / baseline * 100:.2f}",
    "notice": "Comparison only; no trend, cause or forecast is established."
}))
