"""Compare an amount with a positive baseline using decimal arithmetic."""
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP, localcontext

DECIMAL_PATTERN = re.compile(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
CENT = Decimal("0.01")
NOTICE = "Comparison only; no trend, cause or forecast is established."


def parse_value(raw, name, positive=False):
    if not 1 <= len(raw) <= 32:
        raise ValueError(f"{name} must contain 1–32 characters.")
    if DECIMAL_PATTERN.fullmatch(raw) is None:
        raise ValueError(
            f"{name} must use ASCII decimal digits, an optional decimal point "
            "and an optional leading plus; no whitespace or exponent."
        )
    value = Decimal(raw)
    if not 0 <= value <= 100000 or (positive and value == 0):
        bounds = "greater than 0 and at most 100000" if positive else "0 through 100000"
        raise ValueError(f"{name} must be {bounds}.")
    return value


def stop(reason):
    print(f"STOP: {reason}", file=sys.stderr)
    return 2


def decimal_string(value):
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def main():
    if len(sys.argv) != 3:
        return stop("Expected exactly two arguments: amount baseline, in the same units.")
    try:
        amount = parse_value(sys.argv[1], "amount")
        baseline = parse_value(sys.argv[2], "baseline", positive=True)
    except ValueError as error:
        return stop(str(error))

    with localcontext() as context:
        # Inputs have at most 31 fractional places. Percentages have at most
        # 39 integer digits; 120 digits also safely resolve cent-rounding ties.
        context.prec = 120
        context.rounding = ROUND_HALF_UP
        difference = amount - baseline
        percent_above = difference * 100 / baseline
        percent_of = amount * 100 / baseline
        result = {
            "amount": decimal_string(amount),
            "baseline": decimal_string(baseline),
            "difference": decimal_string(difference),
            "percent_above": decimal_string(percent_above),
            "percent_of": decimal_string(percent_of),
            "notice": NOTICE,
        }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
