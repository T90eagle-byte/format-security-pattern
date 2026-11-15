DECISION_LABELS = {
    "ACCEPT": "Принято",
    "QUARANTINE": "Карантин",
    "REJECT": "Отклонено",
}

REASON_LABELS = {
    "FORMAT_MISMATCH": "Несоответствие форматов",
    "METADATA_MISMATCH": "Несоответствие служебных данных",
    "FORMAT_NOT_DETECTED": "Формат не определён",
    "INVALID_JSON": "Некорректная структура JSON",
    "INVALID_XML": "Некорректная структура XML",
    "INVALID_CSV": "Некорректная структура CSV",
    "UNSUPPORTED_FORMAT": "Неподдерживаемый формат",
    "EMPTY_FILE": "Пустой файл",
}

VALIDATION_STATUS_LABELS = {
    "VALID": "Корректна",
    "INVALID": "Некорректна",
    "UNSUPPORTED": "Не поддерживается",
}

PRESENTATION_LABELS = {
    "decision": DECISION_LABELS,
    "reason": REASON_LABELS,
    "validation_status": VALIDATION_STATUS_LABELS,
}
