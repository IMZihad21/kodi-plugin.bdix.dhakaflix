"""Validation helpers for persisted add-on settings."""


def parse_integer_setting(
    value: object,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if value is None or value == "":
        parsed = default
    elif isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{name} must be an integer")
    else:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be an integer") from error

    if parsed < minimum or (maximum is not None and parsed > maximum):
        if maximum is None:
            range_message = f"at least {minimum}"
        else:
            range_message = f"between {minimum} and {maximum}"
        raise ValueError(f"{name} must be {range_message}")
    return parsed
