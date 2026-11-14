"""Testes adicionais de auditoria cobrindo os itens 1 a 12 da especificação."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import BASE, PROJECT_ROOT, SRC_DIR, run_cli

from onfly_report import cli
from onfly_report.models import RawRow
from onfly_report.services.report_builder import FIXED_OFFSET, ReportBuilder, format_money
from onfly_report.services.stay_calculator import calculate_room_nights


class InMemoryReservationRepository:
    """Repositório em memória para testes determinísticos da auditoria."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = [RawRow(line_number=index + 2, values={**BASE, **row}) for index, row in enumerate(rows)]

    def read(self) -> list[RawRow]:
        return self._rows


class InMemoryRateRepository:
    """Tabela de câmbio em memória para testes determinísticos da auditoria."""

    def __init__(self, rates: dict[str, Decimal] | None = None) -> None:
        self._rates = rates or {"BRL": Decimal("1.000000"), "USD": Decimal("5.420000")}

    def read(self) -> dict[str, Decimal]:
        return self._rates


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_cli_raw(args: list[str], cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "onfly_report", *args],
        cwd=str(cwd),
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_audit_item_01_rounding_half_up_cases(tmp_path: Path) -> None:
    """Item 1: arredondamento half-up em casos x.xx5 (2.675 -> 2.68 e 0.125 -> 0.13)."""
    # Arrange / Act
    direct_2675 = format_money(Decimal("2.675"))
    direct_0125 = format_money(Decimal("0.125"))
    report = run_cli(
        tmp_path,
        [
            {"reservation_id": "R-1", "hotel_id": "H01", "hotel_name": "Hotel A", "amount": "2.675"},
            {"reservation_id": "R-2", "hotel_id": "H02", "hotel_name": "Hotel B", "amount": "0.125"},
        ],
    )
    by_hotel = {item["hotel_id"]: item["total_brl"] for item in report["top_hotels"]}

    # Assert
    assert direct_2675 == "2.68"
    assert direct_0125 == "0.13"
    assert by_hotel["H01"] == "2.68"
    assert by_hotel["H02"] == "0.13"
    assert report["summary"]["total_amount_brl"] == "2.80"


def test_audit_item_02_by_month_checkin_only_confirmed_sorted() -> None:
    """Item 2: by_month usa mês do check-in, considera só CONFIRMED e ordena ascendente."""
    # Arrange
    builder = ReportBuilder(
        reservation_repository=InMemoryReservationRepository(
            [
                {
                    "reservation_id": "R-1",
                    "checkin_date": "2025-01-30",
                    "checkout_date": "2025-02-02",
                    "rooms": "1",
                    "status": "CONFIRMED",
                    "amount": "300.00",
                },
                {
                    "reservation_id": "R-2",
                    "checkin_date": "2024-12-30",
                    "checkout_date": "2025-01-02",
                    "rooms": "1",
                    "status": "CONFIRMED",
                    "amount": "150.00",
                },
                {
                    "reservation_id": "R-3",
                    "checkin_date": "2025-01-10",
                    "checkout_date": "2025-01-15",
                    "status": "CANCELLED",
                    "amount": "900.00",
                },
                {
                    "reservation_id": "R-4",
                    "checkin_date": "2025-02-10",
                    "checkout_date": "2025-02-12",
                    "status": "NO_SHOW",
                    "amount": "800.00",
                },
            ]
        ),
        rate_repository=InMemoryRateRepository(),
    )

    # Act
    report = builder.build()

    # Assert
    assert report["by_month"] == [
        {"month": "2024-12", "reservations": 1, "room_nights": 3, "total_brl": "150.00"},
        {"month": "2025-01", "reservations": 1, "room_nights": 3, "total_brl": "300.00"},
    ]


def test_audit_item_03_top_hotels_tie_break_and_limits() -> None:
    """Item 3: top_hotels ordena por (-total_brl, hotel_id), só CONFIRMED e respeita --top."""
    # Arrange
    rows = [
        {"reservation_id": "R-1", "hotel_id": "H10", "hotel_name": "Hotel Dez", "amount": "500.00"},
        {"reservation_id": "R-2", "hotel_id": "H02", "hotel_name": "Hotel Dois", "amount": "500.00"},
        {"reservation_id": "R-3", "hotel_id": "H03", "hotel_name": "Hotel Três", "amount": "200.00"},
        {
            "reservation_id": "R-4",
            "hotel_id": "H99",
            "hotel_name": "Hotel Ignorado",
            "status": "NO_SHOW",
            "amount": "9999.00",
        },
    ]

    # Act
    report_top_2 = ReportBuilder(InMemoryReservationRepository(rows), InMemoryRateRepository(), top_n=2).build()
    report_top_10 = ReportBuilder(InMemoryReservationRepository(rows), InMemoryRateRepository(), top_n=10).build()

    # Assert
    assert [h["hotel_id"] for h in report_top_2["top_hotels"]] == ["H02", "H10"]
    assert [h["hotel_id"] for h in report_top_10["top_hotels"]] == ["H02", "H10", "H03"]


def test_audit_item_04_status_counts_before_filtering() -> None:
    """Item 4: total_reservations e contagens por status são computados antes de filtrar CONFIRMED."""
    # Arrange
    builder = ReportBuilder(
        reservation_repository=InMemoryReservationRepository(
            [
                {"reservation_id": "R-1", "status": "CONFIRMED", "amount": "100.00"},
                {"reservation_id": "R-2", "status": "CANCELLED", "amount": "200.00"},
                {"reservation_id": "R-3", "status": "CANCELLED", "amount": "300.00"},
                {"reservation_id": "R-4", "status": "NO_SHOW", "amount": "400.00"},
            ]
        ),
        rate_repository=InMemoryRateRepository(),
    )

    # Act
    summary = builder.build()["summary"]

    # Assert
    assert summary["total_reservations"] == 4
    assert summary["confirmed_reservations"] == 1
    assert summary["cancelled_reservations"] == 2
    assert summary["no_show_reservations"] == 1
    assert summary["total_amount_brl"] == "100.00"


@pytest.mark.parametrize(
    ("checkin", "checkout", "rooms", "expected"),
    [
        (date(2025, 1, 30), date(2025, 2, 2), 2, 6),
        (date(2024, 12, 30), date(2025, 1, 2), 1, 3),
        (date(2025, 1, 10), date(2025, 1, 13), 1, 3),
    ],
)
def test_audit_item_05_room_nights_edge_cases(
    checkin: date, checkout: date, rooms: int, expected: int
) -> None:
    """Item 5: noites de quarto em virada de mês, virada de ano e mesmo mês."""
    # Arrange / Act
    nights = calculate_room_nights(checkin, checkout, rooms)

    # Assert
    assert nights == expected


def test_audit_item_06_average_nightly_rate_exact_decimal_and_zero_nights() -> None:
    """Item 6: média calculada com Decimal exato e '0.00' quando total_room_nights == 0."""
    # Arrange
    builder_exact = ReportBuilder(
        reservation_repository=InMemoryReservationRepository(
            [
                {
                    "reservation_id": "R-1",
                    "checkin_date": "2025-01-10",
                    "checkout_date": "2025-01-12",
                    "currency": "USD",
                    "amount": "1.005",
                }
            ]
        ),
        rate_repository=InMemoryRateRepository({"USD": Decimal("2.000000")}),
    )
    builder_zero = ReportBuilder(
        reservation_repository=InMemoryReservationRepository([]),
        rate_repository=InMemoryRateRepository(),
    )

    # Act
    # 1.005 * 2 = 2.01; 2.01 / 2 noites = 1.005 -> "1.01" (ROUND_HALF_UP)
    report_exact = builder_exact.build()
    report_zero = builder_zero.build()

    # Assert
    assert report_exact["summary"]["total_amount_brl"] == "2.01"
    assert report_exact["summary"]["average_nightly_rate_brl"] == "1.01"
    assert report_zero["summary"]["average_nightly_rate_brl"] == "0.00"


def test_audit_item_07_report_without_confirmed_reservations() -> None:
    """Item 7: relatório sem nenhuma reserva CONFIRMED zera totais e retorna listas vazias."""
    # Arrange
    builder = ReportBuilder(
        reservation_repository=InMemoryReservationRepository(
            [
                {"reservation_id": "R-1", "status": "CANCELLED", "amount": "500.00"},
                {"reservation_id": "R-2", "status": "NO_SHOW", "amount": "300.00"},
            ]
        ),
        rate_repository=InMemoryRateRepository(),
    )

    # Act
    report = builder.build()

    # Assert
    assert report["summary"] == {
        "total_reservations": 2,
        "confirmed_reservations": 0,
        "cancelled_reservations": 1,
        "no_show_reservations": 1,
        "total_room_nights": 0,
        "total_amount_brl": "0.00",
        "average_nightly_rate_brl": "0.00",
    }
    assert report["by_month"] == []
    assert report["top_hotels"] == []


def test_audit_item_08_generated_at_iso8601_fixed_offset() -> None:
    """Item 8: generated_at usa formato ISO 8601 com offset fixo -03:00."""
    # Arrange
    fixed_now = datetime(2025, 7, 1, 10, 0, 0, tzinfo=FIXED_OFFSET)
    builder_fixed = ReportBuilder(
        reservation_repository=InMemoryReservationRepository([]),
        rate_repository=InMemoryRateRepository(),
        now=fixed_now,
    )
    builder_runtime = ReportBuilder(
        reservation_repository=InMemoryReservationRepository([]),
        rate_repository=InMemoryRateRepository(),
    )

    # Act
    generated_fixed = builder_fixed.build()["generated_at"]
    generated_runtime = builder_runtime.build()["generated_at"]
    parsed_runtime = datetime.fromisoformat(generated_runtime)

    # Assert
    assert generated_fixed == "2025-07-01T10:00:00-03:00"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-03:00", generated_runtime)
    assert parsed_runtime.utcoffset() == timezone(FIXED_OFFSET.utcoffset(None)).utcoffset(None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1234.50"), "1234.50"),
        (Decimal("0.00"), "0.00"),
        (Decimal("1000000.00"), "1000000.00"),
        (Decimal("1E+6"), "1000000.00"),
    ],
)
def test_audit_item_09_money_format_no_thousands_or_scientific_notation(
    value: Decimal, expected: str
) -> None:
    """Item 9: dinheiro é string com 2 casas, sem separador de milhar nem notação científica."""
    # Arrange / Act
    formatted = format_money(value)

    # Assert
    assert formatted == expected
    assert "," not in formatted
    assert "E" not in formatted.upper()


@pytest.mark.parametrize(
    "args",
    [
        ["--input", "data/exemplo-validacao.csv", "--rates", "data/taxas_cambio.csv", "--top", "0"],
        ["--input", "data/exemplo-validacao.csv", "--rates", "data/taxas_cambio.csv", "--top", "-1"],
        ["--input", "data/exemplo-validacao.csv", "--rates", "data/taxas_cambio.csv", "--top", "abc"],
        ["--input", "data/nao-existe.csv", "--rates", "data/taxas_cambio.csv"],
        ["--input", "data/exemplo-validacao.csv", "--rates", "data/nao-existe.csv"],
        ["--rates", "data/taxas_cambio.csv"],
        ["--input", "data/exemplo-validacao.csv"],
    ],
)
def test_audit_item_10_cli_invalid_inputs_and_args_exit_2(args: list[str]) -> None:
    """Item 10: argumentos inválidos, obrigatórios ausentes e arquivos inexistentes retornam 2."""
    # Arrange / Act
    process = _run_cli_raw(args)

    # Assert
    assert process.returncode == 2


def test_audit_item_10_cli_unexpected_error_exits_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Item 10: erro inesperado durante a geração do relatório retorna código de saída 1."""
    # Arrange
    def _boom(self: ReportBuilder) -> dict:
        raise RuntimeError("falha inesperada simulada")

    monkeypatch.setattr(ReportBuilder, "build", _boom)

    # Act
    exit_code = cli.main(
        [
            "--input",
            str(PROJECT_ROOT / "data" / "exemplo-validacao.csv"),
            "--rates",
            str(PROJECT_ROOT / "data" / "taxas_cambio.csv"),
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    # Assert
    assert exit_code == 1


def test_audit_item_10_cli_default_output_creates_directory(tmp_path: Path) -> None:
    """Item 10: --output padrão é output/report.json e o CLI cria o diretório."""
    # Arrange
    expected_output = tmp_path / "output" / "report.json"

    # Act
    process = _run_cli_raw(
        [
            "--input",
            str(PROJECT_ROOT / "data" / "exemplo-validacao.csv"),
            "--rates",
            str(PROJECT_ROOT / "data" / "taxas_cambio.csv"),
        ],
        cwd=tmp_path,
    )

    # Assert
    assert process.returncode == 0, process.stderr
    assert expected_output.is_file()
    assert json.loads(expected_output.read_text(encoding="utf-8"))["report_version"] == "1.0"


def test_audit_item_11_original_csv_files_have_no_utf8_bom() -> None:
    """Item 11: os CSVs originais do repositório não possuem BOM UTF-8 (ef bb bf)."""
    # Arrange
    reservas_path = PROJECT_ROOT / "data" / "reservas_hoteis.csv"
    rates_path = PROJECT_ROOT / "data" / "taxas_cambio.csv"

    # Act
    reservas_head = reservas_path.read_bytes()[:3]
    rates_head = rates_path.read_bytes()[:3]

    # Assert
    assert reservas_head != b"\xef\xbb\xbf"
    assert rates_head != b"\xef\xbb\xbf"
    assert reservas_head == b"res"
    assert rates_head == b"cur"


def test_audit_item_12_hotel_names_with_comma_and_accents_in_json(tmp_path: Path) -> None:
    """Item 12: nomes com vírgula e acentos são preservados legíveis (ensure_ascii=False)."""
    # Arrange
    output_path = tmp_path / "report.json"

    # Act
    process = _run_cli_raw(
        [
            "--input",
            str(PROJECT_ROOT / "data" / "exemplo-validacao.csv"),
            "--rates",
            str(PROJECT_ROOT / "data" / "taxas_cambio.csv"),
            "--output",
            str(output_path),
        ]
    )
    raw_json = output_path.read_text(encoding="utf-8")
    report = json.loads(raw_json)
    names = [hotel["hotel_name"] for hotel in report["top_hotels"]]

    # Assert
    assert process.returncode == 0, process.stderr
    assert "Hotel Madrid, Gran Vía" in names
    assert "Hotel Lisboa, Centro" in names
    assert "Hotel Madrid, Gran Vía" in raw_json
    assert "\\u00ed" not in raw_json
