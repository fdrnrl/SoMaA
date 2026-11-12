"""CSV разделов 7.2/7.4 ТЗ: фиксированные поля, UTF-8, без сторонних библиотек."""

import csv
import io
from datetime import datetime
from decimal import Decimal

from dashboard.data import ANOMALY_COLUMNS, MSK, SCHOOL_COLUMNS

TIME_COLUMNS = frozenset(("detected_at", "calculated_at"))


def _timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Время CSV должно содержать часовой пояс.")
    return value.astimezone(MSK).isoformat()


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, float | Decimal):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("CSV не поддерживает бесконечность и NaN.")
        return format(number, "f").replace(".", ",")
    if isinstance(value, list | tuple):
        return ",".join(_cell(item) for item in value)
    if isinstance(value, str | int):
        return str(value)
    raise TypeError("Неподдерживаемый тип значения CSV.")


def csv_bytes(rows, columns):
    """Сохраняет порядок строк/полей и коды; пустой набор содержит заголовок."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        try:
            values = [row[name] for name in columns]
        except KeyError:
            raise ValueError("В строке CSV отсутствует обязательное поле.") from None
        writer.writerow([
            _cell(_timestamp(value) if name in TIME_COLUMNS and value is not None else value)
            for name, value in zip(columns, values, strict=True)
        ])
    return output.getvalue().encode("utf-8")


def anomalies_csv(rows):
    return csv_bytes(rows, ANOMALY_COLUMNS)


def schools_csv(rows):
    return csv_bytes(rows, SCHOOL_COLUMNS)
