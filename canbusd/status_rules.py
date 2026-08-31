from typing import Any, Dict, Iterable, List, Optional


def parse_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.lower().startswith("0x") else int(value)
    return int(value)


def _set_path(dst: Dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in path.split(".") if p]
    if not parts:
        return

    cur = dst
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _paths(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(path) for path in value if path not in (None, "")]
    return [str(value)]


def _set_rule_value(
    update: Dict[str, Any],
    rule: Dict[str, Any],
    value: Any,
    path_key: str = "path",
    aliases_key: str = "aliases",
) -> None:
    paths = _paths(rule.get(path_key)) + _paths(rule.get(aliases_key))
    seen = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        _set_path(update, path, value)


def _bool_default(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


class StatusRuleState:
    """Mutable decoder state owned by one daemon/profile runtime.

    Stateless rule types do not use this object. Stateful rules, such as
    ``toggle_latch``, keep their edge and latch history here so state cannot
    leak between daemon instances, vehicle profiles, or unit tests.
    """

    def __init__(self) -> None:
        self._toggle_latches: Dict[str, Dict[str, bool]] = {}

    def reset(self) -> None:
        """Discard all state after a profile/runtime lifecycle boundary."""

        self._toggle_latches.clear()

    @staticmethod
    def _toggle_key(rule: Dict[str, Any]) -> str:
        explicit = rule.get("state_key")
        if explicit not in (None, ""):
            return str(explicit)

        # Use signal identity rather than only the output path. Two independent
        # CAN signals may intentionally publish to similarly named paths, and
        # they must not share edge history by accident. Parsed rules include the
        # numeric CAN id; direct callers still get a deterministic fallback.
        identity = (
            rule.get("id", "unknown"),
            rule.get("byte", 0),
            rule.get("path", ""),
            rule.get("mask", ""),
            rule.get("true", ""),
            rule.get("false", ""),
        )
        return "|".join(str(part) for part in identity)

    def toggle_latch_value(self, rule: Dict[str, Any], active: bool) -> bool:
        # A false/inactive frame preserves the current latched value. A rising
        # active edge toggles the latched value. This is intended for decoded
        # status bits that are button/event requests rather than held states.
        key = self._toggle_key(rule)
        state = self._toggle_latches.setdefault(
            key,
            {
                "latched": _bool_default(rule.get("initial"), False),
                "previous_active": False,
            },
        )

        was_active = bool(state.get("previous_active", False))
        if active and not was_active:
            state["latched"] = not bool(state.get("latched", False))

        state["previous_active"] = bool(active)
        return bool(state.get("latched", False))


def _join_path(prefix: Optional[str], key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def parse_status_rules(items: Iterable[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}

    for item in items:
        rule = dict(item)
        cid = parse_int(rule["id"])
        rule["id"] = cid

        if "byte" in rule:
            rule["byte"] = int(rule["byte"])

        grouped.setdefault(cid, []).append(rule)

    return grouped


def _rule_byte(rule: Dict[str, Any], data: bytes, dlc: int) -> Optional[int]:
    byte_index = int(rule.get("byte", 0))
    if dlc <= byte_index:
        return None
    return data[byte_index]


def _masked_value(raw: int, rule: Dict[str, Any]) -> int:
    if "mask" in rule:
        return raw & parse_int(rule["mask"])
    return raw


def _evaluate_signed_magnitude(rule: Dict[str, Any], data: bytes, dlc: int) -> Dict[str, Any]:
    update: Dict[str, Any] = {}

    magnitude_cfg = rule.get("magnitude", {})
    sign_cfg = rule.get("sign_bit", {})

    low_byte = int(magnitude_cfg.get("low_byte", rule.get("bytes", [0, 1])[0]))
    high_byte = int(magnitude_cfg.get("high_byte", rule.get("bytes", [0, 1])[1]))

    sign_byte = int(sign_cfg.get("byte", high_byte))
    required = max(low_byte, high_byte, sign_byte)

    if dlc <= required:
        return update

    high_mask = parse_int(magnitude_cfg.get("high_mask", "0x7F"))
    sign_mask = parse_int(sign_cfg.get("mask", "0x80"))

    low = data[low_byte]
    high = data[high_byte]
    sign_raw = data[sign_byte]

    raw_full = (high << 8) | low
    magnitude = ((high & high_mask) << 8) | low

    center = rule.get("center")
    if center is not None and raw_full == parse_int(center):
        value = 0.0
    else:
        scale = float(rule.get("scale", 1))
        offset = float(rule.get("offset", 0))
        positive_when_set = sign_cfg.get("positive", "right") != "left"

        sign_is_set = bool(sign_raw & sign_mask)
        is_positive = sign_is_set if positive_when_set else not sign_is_set

        value = (magnitude * scale) + offset
        if not is_positive:
            value = -value

    if "round" in rule:
        value = round(value, int(rule["round"]))

    _set_rule_value(update, rule, value)

    if rule.get("raw_path"):
        _set_rule_value(update, rule, raw_full, "raw_path", "raw_aliases")

    if rule.get("magnitude_raw_path"):
        _set_path(update, rule["magnitude_raw_path"], magnitude)

    if rule.get("direction_path"):
        if value > 0:
            direction = sign_cfg.get("positive", "right")
        elif value < 0:
            direction = "left" if sign_cfg.get("positive", "right") == "right" else "right"
        else:
            direction = "center"
        _set_path(update, rule["direction_path"], direction)

    return update


def _scale_value(raw: int, rule: Dict[str, Any]) -> float:
    scale = float(rule.get("scale", 1))
    offset = float(rule.get("offset", 0))
    value = (raw * scale) + offset

    if "round" in rule:
        value = round(value, int(rule["round"]))

    return value


def _evaluate_u16le(rule: Dict[str, Any], data: bytes, dlc: int) -> Dict[str, Any]:
    update: Dict[str, Any] = {}

    start_byte = parse_int(rule.get("start_byte", rule.get("byte", 0)))
    high_byte = start_byte + 1

    if dlc <= high_byte:
        return update

    raw = data[start_byte] | (data[high_byte] << 8)
    value_raw = _masked_value(raw, rule)

    if rule.get("raw_path"):
        _set_rule_value(update, rule, raw, "raw_path", "raw_aliases")

    _set_rule_value(update, rule, _scale_value(value_raw, rule))
    return update



def _evaluate_uint_le(rule: Dict[str, Any], data: bytes, dlc: int) -> Dict[str, Any]:
    update: Dict[str, Any] = {}
    start_byte = parse_int(rule.get("start_byte", rule.get("byte", 0)))

    kind = rule.get("type")
    if kind == "u24le":
        length = 3
    elif kind == "u32le":
        length = 4
    else:
        length = int(rule.get("length", 1))

    if length < 1 or length > 8:
        return update

    end_byte = start_byte + length - 1
    if dlc <= end_byte:
        return update

    raw = 0
    for offset in range(length):
        raw |= data[start_byte + offset] << (8 * offset)

    value_raw = _masked_value(raw, rule)

    if rule.get("raw_path"):
        _set_rule_value(update, rule, raw, "raw_path", "raw_aliases")

    _set_rule_value(update, rule, _scale_value(value_raw, rule))
    return update


def evaluate_rule(
    rule: Dict[str, Any],
    data: bytes,
    dlc: int,
    state: Optional[StatusRuleState] = None,
) -> Dict[str, Any]:
    kind = rule.get("type", "raw")
    update: Dict[str, Any] = {}

    if kind in ("signed_magnitude", "steering_angle"):
        return _evaluate_signed_magnitude(rule, data, dlc)

    if kind == "u16le":
        return _evaluate_u16le(rule, data, dlc)

    if kind in ("u24le", "u32le", "uint_le"):
        return _evaluate_uint_le(rule, data, dlc)
    raw = _rule_byte(rule, data, dlc)
    if raw is None:
        return update

    raw_path = rule.get("raw_path")
    if raw_path:
        _set_rule_value(update, rule, raw, "raw_path", "raw_aliases")

    value_raw = _masked_value(raw, rule)

    if kind == "raw":
        _set_rule_value(update, rule, value_raw)

    elif kind == "percent":
        value = max(0, min(100, value_raw))
        _set_rule_value(update, rule, value)

    elif kind == "scaled":
        _set_rule_value(update, rule, _scale_value(value_raw, rule))

    elif kind == "bool":
        true_value = parse_int(rule["true"])
        false_value = parse_int(rule.get("false", 0)) if "false" in rule else None
        state_mode = rule.get("state") or rule.get("stateful") or rule.get("mode")

        if state_mode == "toggle_latch":
            if value_raw == true_value:
                runtime_state = state or StatusRuleState()
                _set_rule_value(update, rule, runtime_state.toggle_latch_value(rule, True))
            elif false_value is not None and value_raw == false_value:
                runtime_state = state or StatusRuleState()
                _set_rule_value(update, rule, runtime_state.toggle_latch_value(rule, False))
        elif value_raw == true_value:
            _set_rule_value(update, rule, True)
        elif false_value is not None and value_raw == false_value:
            _set_rule_value(update, rule, False)

    elif kind == "enum":
        values = {parse_int(k): v for k, v in rule.get("values", {}).items()}
        if value_raw in values:
            _set_rule_value(update, rule, values[value_raw])
        elif "default" in rule:
            _set_rule_value(update, rule, rule["default"])

    elif kind == "bitfield":
        prefix = rule.get("path")
        bools: Dict[str, bool] = {}

        for name, mask in rule.get("fields", {}).items():
            value = bool(raw & parse_int(mask))
            bools[name] = value
            _set_path(update, _join_path(prefix, name), value)

        for name, expected in rule.get("equals", {}).items():
            if isinstance(expected, dict):
                expected_value = parse_int(expected["value"])
                observed = (
                    raw & parse_int(expected["mask"])
                    if "mask" in expected
                    else raw
                )
                value = observed == expected_value
            else:
                value = raw == parse_int(expected)
            bools[name] = value
            _set_path(update, _join_path(prefix, name), value)

        if rule.get("any"):
            _set_path(update, _join_path(prefix, rule["any"]), any(bools.values()))

        if rule.get("raw"):
            raw_key = rule["raw"] if isinstance(rule["raw"], str) else "raw"
            _set_path(update, _join_path(prefix, raw_key), raw)

    return update


def evaluate_status_rules(
    rules: Iterable[Dict[str, Any]],
    data: bytes,
    dlc: int,
    state: Optional[StatusRuleState] = None,
) -> Dict[str, Any]:
    update: Dict[str, Any] = {}
    runtime_state = state or StatusRuleState()

    for rule in rules:
        partial = evaluate_rule(rule, data, dlc, runtime_state)
        _deep_merge(update, partial)

    return update


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst
