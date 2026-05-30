"""Soul YAML Spec v1.0 — standardized soul definition format."""

# Example soul YAML:
#
# id: themis
# name: Assistant
# archetype: 预见型架构合伙人
# thinking_framework: |
#   风险先于机会，先问"什么会错"再问"什么会对"。
#   决策前三问：基准数据？最坏结果？谁会反对？
# communication_style: 苏格拉底式提问，平和沉稳，用词精准
# values:
#   - 先测量再相信
#   - 风险先于机会
#   - Consult reviewer before major decisions
# veto_conditions:
#   - 不自行修改JSON配置文件
#   - 不自行重启Gateway
#   - 不执行未经确认的删除操作
# tools_whitelist:
#   - read
#   - write
#   - exec
#   - web_search

# ── Validation rules ──

SOUL_SPEC_V1_FIELDS = {
    "id": {"type": str, "required": True, "max_length": 64},
    "name": {"type": str, "required": True, "max_length": 128},
    "archetype": {"type": str, "required": False, "max_length": 200},
    "thinking_framework": {"type": str, "required": True, "max_length": 8000},
    "communication_style": {"type": str, "required": False, "max_length": 500},
    "values": {"type": list, "required": False, "item_type": str, "max_items": 10},
    "veto_conditions": {"type": list, "required": False, "item_type": str, "max_items": 10},
    "tools_whitelist": {"type": list, "required": False, "item_type": str, "max_items": 20},
}


def validate_soul_yaml(data: dict) -> tuple[list[str], list[str]]:
    """Validate a soul YAML dict against spec v1.0.
    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    for field_name, spec in SOUL_SPEC_V1_FIELDS.items():
        value = data.get(field_name)

        if spec.get("required") and (value is None or value == ""):
            errors.append(f"Missing required field: {field_name}")
            continue

        if value is None:
            continue

        expected_type = spec["type"]
        if not isinstance(value, expected_type):
            errors.append(f"Field '{field_name}' must be {expected_type.__name__}, got {type(value).__name__}")
            continue

        if isinstance(value, str) and "max_length" in spec:
            if len(value) > spec["max_length"]:
                warnings.append(f"Field '{field_name}' exceeds {spec['max_length']} chars ({len(value)})")

        if isinstance(value, list):
            if "max_items" in spec and len(value) > spec["max_items"]:
                warnings.append(f"Field '{field_name}' has {len(value)} items (max {spec['max_items']})")
            if "item_type" in spec:
                for i, item in enumerate(value):
                    if not isinstance(item, spec["item_type"]):
                        errors.append(f"Field '{field_name}[{i}]' must be {spec['item_type'].__name__}")

    # Quality warnings
    tf = data.get("thinking_framework", "")
    if isinstance(tf, str) and len(tf) < 50:
        warnings.append("thinking_framework is very short (<50 chars), consider adding more guidance")
    if isinstance(tf, str) and len(tf) > 4000:
        warnings.append(f"thinking_framework is long ({len(tf)} chars), consider compressing for token budget")

    if not data.get("values"):
        warnings.append("No values defined — agent has no behavioral guardrails")
    if not data.get("veto_conditions"):
        warnings.append("No veto_conditions — agent has no hard prohibitions")

    return errors, warnings


def soul_to_yaml_dict(soul) -> dict:
    """Convert a Soul dataclass to YAML-compatible dict."""
    return {
        "id": soul.id,
        "name": soul.name,
        "archetype": soul.archetype,
        "thinking_framework": soul.thinking_framework,
        "communication_style": soul.communication_style,
        "values": soul.values,
        "veto_conditions": soul.veto_conditions,
        "tools_whitelist": soul.tools_whitelist,
    }


def estimate_soul_tokens(soul_data: dict) -> int:
    """Estimate compiled soul token count."""
    text_parts = []
    for key in ["name", "archetype", "thinking_framework", "communication_style"]:
        if v := soul_data.get(key):
            text_parts.append(v)
    for key in ["values", "veto_conditions"]:
        for item in soul_data.get(key, []):
            text_parts.append(item)
    full = " ".join(text_parts)
    cjk = sum(1 for c in full if "\u4e00" <= c <= "\u9fff")
    other = len(full) - cjk
    return int(cjk / 2 + other / 4)
