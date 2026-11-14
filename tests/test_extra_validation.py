"""Testes unitários (sem I/O), de bordas, falhas de domínio e contrato do CLI."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import BASE, COLUMNS, PROJECT_ROOT, SRC_DIR, run_cli

from onfly_report.exceptions import InputFileError
from onfly_report.models import RawRow
from onfly_report.repositories.csv_rate_repository import CsvRateRepository
from onfly_report.repositories.csv_reservation_repository import CsvReservationRepository
from onfly_report.services.currency_converter import convert_to_brl
from onfly_report.services.report_builder import FIXED_OFFSET, ReportBuilder, format_money
from onfly_report.services.row_parser import RowParser
from onfly_report.services.status_policy import is_billable
from onfly_report.services.stay_calculator import calculate_room_nights


class FakeReservationRepository:
    """Repositório em memória para testes unitários isolados sem I/O."""

    def __init__(self, rows: list[RawRow]) -> None:
        self._rows = rows

    def read(self) -> list[RawRow]:
        return self._rows


class FakeRateRepository:
    """Repositório de taxas de câmbio em memória para testes unitários isolados."""

    def __init__(self, rates: dict[str, Decimal]) -> None:
        self._rates = rates

    def read(self) -> dict[str, Decimal]:
        return self._rates


def _make_raw_row(line_number: int = 2, **overrides: str) -> RawRow:
    return RawRow(line_number=line_number, values={**BASE, **overrides})


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_raw_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "onfly_report", *args],
        cwd=str(PROJECT_ROOT),
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Testes unitários puros (zero I/O)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("CONFIRMED", True),
        ("CANCELLED", False),
        ("NO_SHOW", False),
        ("PENDING", False),
    ],
)
def test_is_billable_allows_only_confirmed(status: str, expected: bool) -> None:
    """Apenas o status CONFIRMED é faturável."""
    # Arrange / Act
    result = is_billable(status)

    # Assert
    assert result is expected


@pytest.mark.parametrize(
    ("checkin", "checkout", "rooms", "expected"),
    [
        (date(2025, 1, 10), date(2025, 1, 13), 1, 3),
        (date(2025, 1, 30), date(2025, 2, 2), 1, 3),
        (date(2024, 12, 30), date(2025, 1, 3), 2, 8),
        (date(2025, 2, 27), date(2025, 3, 2), 3, 9),
    ],
)
def test_calculate_room_nights_across_boundaries(
    checkin: date, checkout: date, rooms: int, expected: int
) -> None:
    """Calcula diferença não inclusiva de dias multiplicada pelos quartos, incluindo viradas."""
    # Arrange / Act
    nights = calculate_room_nights(checkin, checkout, rooms)

    # Assert
    assert nights == expected


def test_convert_to_brl_preserves_decimal_precision_and_normalizes_currency() -> None:
    """Multiplica usando Decimal exato e normaliza espaços e caixa da moeda."""
    # Arrange
    rates = {"BRL": Decimal("1.000000"), "USD": Decimal("5.421234")}

    # Act
    converted = convert_to_brl(Decimal("100.00"), " usd ", rates)

    # Assert
    assert isinstance(converted, Decimal)
    assert converted == Decimal("542.12340000")


def test_convert_to_brl_raises_input_file_error_for_unknown_currency() -> None:
    """Moeda ausente na tabela de taxas deve lançar InputFileError."""
    # Arrange
    rates = {"BRL": Decimal("1.000000")}

    # Act / Assert
    with pytest.raises(InputFileError, match="GBP"):
        convert_to_brl(Decimal("100.00"), "GBP", rates)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.005"), "0.01"),
        (Decimal("0.015"), "0.02"),
        (Decimal("270.215"), "270.22"),
        (Decimal("1234.5"), "1234.50"),
    ],
)
def test_format_money_uses_half_up_without_thousands_separator(
    value: Decimal, expected: str
) -> None:
    """Serializa dinheiro com duas casas, ROUND_HALF_UP e sem separador de milhar."""
    # Arrange / Act
    formatted = format_money(value)

    # Assert
    assert formatted == expected


def test_row_parser_normalizes_status_and_currency() -> None:
    """RowParser converte tipos e normaliza status e currency em maiúsculas."""
    # Arrange
    raw = _make_raw_row(status=" confirmed ", currency=" usd ", amount="123.45", rooms="2")

    # Act
    reservation = RowParser().parse(raw)

    # Assert
    assert reservation.status == "CONFIRMED"
    assert reservation.currency == "USD"
    assert reservation.amount == Decimal("123.45")
    assert reservation.rooms == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"checkin_date": "10/01/2025"},
        {"checkout_date": "invalid-date"},
        {"rooms": "dois"},
        {"amount": "abc"},
    ],
)
def test_row_parser_raises_input_file_error_on_malformed_fields(overrides: dict[str, str]) -> None:
    """Campos inválidos no CSV devem lançar InputFileError com o número da linha."""
    # Arrange
    raw = _make_raw_row(line_number=7, **overrides)

    # Act / Assert
    with pytest.raises(InputFileError, match="linha 7 inválida"):
        RowParser().parse(raw)


def test_report_builder_in_memory_deterministic_timestamp_and_half_up_average() -> None:
    """ReportBuilder usa timestamp determinístico e arredonda a média com ROUND_HALF_UP."""
    # Arrange
    fixed_now = datetime(2025, 7, 1, 10, 0, 0, tzinfo=FIXED_OFFSET)
    rows = [
        _make_raw_row(
            line_number=2,
            reservation_id="R-1",
            hotel_id="H01",
            hotel_name="Hotel Um",
            checkin_date="2025-01-10",
            checkout_date="2025-01-12",
            rooms="1",
            amount="2.01",
        ),
        _make_raw_row(
            line_number=3,
            reservation_id="R-2",
            hotel_id="H02",
            hotel_name="Hotel Dois",
            status="NO_SHOW",
            amount="999.00",
        ),
    ]
    builder = ReportBuilder(
        reservation_repository=FakeReservationRepository(rows),
        rate_repository=FakeRateRepository({"BRL": Decimal("1.000000")}),
        top_n=5,
        now=fixed_now,
    )

    # Act
    report = builder.build()

    # Assert: total 2.01 / 2 noites = 1.005 -> "1.01" em ROUND_HALF_UP
    assert report["report_version"] == "1.0"
    assert report["generated_at"] == "2025-07-01T10:00:00-03:00"
    assert report["summary"] == {
        "total_reservations": 2,
        "confirmed_reservations": 1,
        "cancelled_reservations": 0,
        "no_show_reservations": 1,
        "total_room_nights": 2,
        "total_amount_brl": "2.01",
        "average_nightly_rate_brl": "1.01",
    }
    assert report["by_month"] == [
        {"month": "2025-01", "reservations": 1, "room_nights": 2, "total_brl": "2.01"}
    ]
    assert report["top_hotels"] == [
        {"hotel_id": "H01", "hotel_name": "Hotel Um", "room_nights": 2, "total_brl": "2.01"}
    ]


# ---------------------------------------------------------------------------
# Testes de contrato de erro dos repositórios CSV
# ---------------------------------------------------------------------------


def test_csv_reservation_repository_missing_columns_raises_input_file_error(tmp_path: Path) -> None:
    """CSV de reservas sem colunas obrigatórias deve lançar InputFileError."""
    # Arrange
    path = tmp_path / "invalid.csv"
    path.write_text("reservation_id,hotel_id\nR-1,H01\n", encoding="utf-8")

    # Act / Assert
    with pytest.raises(InputFileError, match="colunas obrigatórias ausentes"):
        CsvReservationRepository(path).read()


@pytest.mark.parametrize(
    ("content", "expected_msg"),
    [
        ("currency\nBRL\n", "colunas obrigatórias ausentes"),
        ("currency,rate_to_brl\n", "tabela de câmbio vazia"),
        ("currency,rate_to_brl\nUSD,0\n", "taxa não positiva"),
        ("currency,rate_to_brl\nUSD,-1.5\n", "taxa não positiva"),
        ("currency,rate_to_brl\nUSD,abc\n", "taxa inválida"),
    ],
)
def test_csv_rate_repository_invalid_inputs_raise_input_file_error(
    tmp_path: Path, content: str, expected_msg: str
) -> None:
    """Tabela de câmbio vazia, incompleta ou com taxa inválida lança InputFileError."""
    # Arrange
    path = tmp_path / "rates.csv"
    path.write_text(content, encoding="utf-8")

    # Act / Assert
    with pytest.raises(InputFileError, match=expected_msg):
        CsvRateRepository(path).read()


# ---------------------------------------------------------------------------
# Testes ponta a ponta (CLI, amostra oficial e bordas)
# ---------------------------------------------------------------------------


def test_reads_more_than_200_reservations(tmp_path: Path) -> None:
    """Garante que o repositório lê todas as linhas além da 200ª reserva."""
    # Arrange
    rows = [{"reservation_id": f"R-{i:03d}", "amount": "10.00"} for i in range(1, 251)]

    # Act
    report = run_cli(tmp_path, rows)

    # Assert
    assert report["summary"]["total_reservations"] == 250
    assert report["summary"]["confirmed_reservations"] == 250
    assert report["summary"]["total_room_nights"] == 250
    assert report["summary"]["total_amount_brl"] == "2500.00"


def test_validation_sample_matches_readme_specification(tmp_path: Path) -> None:
    """Valida campo a campo a saída do CLI sobre data/exemplo-validacao.csv."""
    # Arrange
    output_path = tmp_path / "exemplo.json"

    # Act
    process = _run_raw_cli(
        [
            "--input",
            str(PROJECT_ROOT / "data" / "exemplo-validacao.csv"),
            "--rates",
            str(PROJECT_ROOT / "data" / "taxas_cambio.csv"),
            "--output",
            str(output_path),
        ]
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))

    # Assert
    assert process.returncode == 0, process.stderr
    assert report["report_version"] == "1.0"
    assert report["generated_at"].endswith("-03:00")
    assert report["summary"] == {
        "total_reservations": 12,
        "confirmed_reservations": 9,
        "cancelled_reservations": 2,
        "no_show_reservations": 1,
        "total_room_nights": 28,
        "total_amount_brl": "7566.00",
        "average_nightly_rate_brl": "270.21",
    }
    assert report["by_month"] == [
        {"month": "2025-01", "reservations": 8, "room_nights": 25, "total_brl": "6684.00"},
        {"month": "2025-02", "reservations": 1, "room_nights": 3, "total_brl": "882.00"},
    ]
    assert report["top_hotels"] == [
        {"hotel_id": "H01", "hotel_name": "Hotel Aurora Paulista", "room_nights": 3, "total_brl": "1200.00"},
        {"hotel_id": "H02", "hotel_name": "Hotel Mirante", "room_nights": 2, "total_brl": "1084.00"},
        {"hotel_id": "H12", "hotel_name": "Hotel Miami Beach", "room_nights": 1, "total_brl": "1000.00"},
        {"hotel_id": "H10", "hotel_name": "Hotel Madrid, Gran Vía", "room_nights": 4, "total_brl": "900.00"},
        {"hotel_id": "H09", "hotel_name": "Hotel Lisboa, Centro", "room_nights": 3, "total_brl": "882.00"},
    ]


def test_top_hotels_tie_breaker_orders_by_hotel_id_ascending(tmp_path: Path) -> None:
    """Em empate de total_brl, ordena por hotel_id em ordem ascendente."""
    # Arrange
    rows = [
        {"reservation_id": "R-1", "hotel_id": "H10", "hotel_name": "Hotel Dez", "amount": "500.00"},
        {"reservation_id": "R-2", "hotel_id": "H02", "hotel_name": "Hotel Dois", "amount": "500.00"},
        {"reservation_id": "R-3", "hotel_id": "H05", "hotel_name": "Hotel Cinco", "amount": "900.00"},
    ]

    # Act
    report = run_cli(tmp_path, rows, top=5)

    # Assert
    assert [hotel["hotel_id"] for hotel in report["top_hotels"]] == ["H05", "H02", "H10"]


def test_top_parameter_greater_than_hotel_count(tmp_path: Path) -> None:
    """Quando --top excede a quantidade de hotéis, retorna todos os existentes."""
    # Arrange
    rows = [
        {"reservation_id": "R-1", "hotel_id": "H01", "hotel_name": "Hotel A", "amount": "200.00"},
        {"reservation_id": "R-2", "hotel_id": "H02", "hotel_name": "Hotel B", "amount": "300.00"},
    ]

    # Act
    report = run_cli(tmp_path, rows, top=10)

    # Assert
    assert len(report["top_hotels"]) == 2
    assert [hotel["hotel_id"] for hotel in report["top_hotels"]] == ["H02", "H01"]


@pytest.mark.parametrize("bad_top", ["0", "-1", "abc"])
def test_invalid_top_arguments_exit_with_code_2(tmp_path: Path, bad_top: str) -> None:
    """Valores inválidos para --top devem retornar código de saída 2 sem gerar arquivo."""
    # Arrange
    output_path = tmp_path / "out.json"
    args = [
        "--input",
        str(PROJECT_ROOT / "data" / "exemplo-validacao.csv"),
        "--rates",
        str(PROJECT_ROOT / "data" / "taxas_cambio.csv"),
        "--output",
        str(output_path),
        "--top",
        bad_top,
    ]

    # Act
    process = _run_raw_cli(args)

    # Assert
    assert process.returncode == 2
    assert process.stderr.strip() != ""
    assert not output_path.exists()


@pytest.mark.parametrize("missing_target", ["input", "rates"])
def test_missing_input_or_rates_file_exits_with_code_2(tmp_path: Path, missing_target: str) -> None:
    """Arquivos de entrada ou câmbio inexistentes devem retornar código de saída 2."""
    # Arrange
    output_path = tmp_path / "out.json"
    input_path = (
        tmp_path / "nao-existe.csv"
        if missing_target == "input"
        else PROJECT_ROOT / "data" / "exemplo-validacao.csv"
    )
    rates_path = (
        tmp_path / "rates-nao-existe.csv"
        if missing_target == "rates"
        else PROJECT_ROOT / "data" / "taxas_cambio.csv"
    )

    # Act
    process = _run_raw_cli(
        ["--input", str(input_path), "--rates", str(rates_path), "--output", str(output_path)]
    )

    # Assert
    assert process.returncode == 2
    assert process.stderr.strip() != ""
    assert not output_path.exists()


def test_unknown_currency_in_csv_exits_with_code_2(tmp_path: Path) -> None:
    """Moeda sem taxa cadastrada no CSV deve retornar código de saída 2 sem gerar arquivo."""
    # Arrange
    input_path = tmp_path / "reservas.csv"
    rates_path = tmp_path / "rates.csv"
    output_path = tmp_path / "out.json"

    with input_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({**BASE, "currency": "GBP", "amount": "100.00"})
    rates_path.write_text("currency,rate_to_brl\nBRL,1.000000\n", encoding="utf-8")

    # Act
    process = _run_raw_cli(
        ["--input", str(input_path), "--rates", str(rates_path), "--output", str(output_path)]
    )

    # Assert
    assert process.returncode == 2
    assert "GBP" in process.stderr
    assert not output_path.exists()


def test_no_confirmed_reservations_returns_zero_average_and_empty_lists(tmp_path: Path) -> None:
    """Sem reservas CONFIRMED, a média é '0.00' e as listas agregadas ficam vazias."""
    # Arrange
    rows = [
        {"reservation_id": "R-1", "status": "CANCELLED", "amount": "400.00"},
        {"reservation_id": "R-2", "status": "NO_SHOW", "amount": "250.00"},
    ]

    # Act
    report = run_cli(tmp_path, rows)

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


def test_year_boundary_and_checkin_month_attribution(tmp_path: Path) -> None:
    """Virada de ano calcula noites corretas e atribui a reserva ao mês do check-in."""
    # Arrange
    rows = [
        {
            "reservation_id": "R-1",
            "checkin_date": "2024-12-30",
            "checkout_date": "2025-01-03",
            "rooms": "2",
            "amount": "800.00",
        }
    ]

    # Act
    report = run_cli(tmp_path, rows)

    # Assert
    assert report["summary"]["total_room_nights"] == 8
    assert report["by_month"] == [
        {"month": "2024-12", "reservations": 1, "room_nights": 8, "total_brl": "800.00"}
    ]


def test_rounding_half_up_and_exact_sum_before_rounding(tmp_path: Path) -> None:
    """Soma valores exatos em Decimal e arredonda apenas na serialização com ROUND_HALF_UP."""
    # Arrange
    # 0.005 arredonda para 0.01 em ROUND_HALF_UP (em ROUND_HALF_EVEN seria 0.00).
    # 0.004 + 0.004 = 0.008 -> "0.01" (se arredondasse antes, daria "0.00").
    rows = [
        {"reservation_id": "R-1", "hotel_id": "H01", "hotel_name": "Hotel 1", "amount": "0.005"},
        {"reservation_id": "R-2", "hotel_id": "H02", "hotel_name": "Hotel 2", "amount": "0.004"},
        {"reservation_id": "R-3", "hotel_id": "H02", "hotel_name": "Hotel 2", "amount": "0.004"},
    ]

    # Act
    report = run_cli(tmp_path, rows)
    totals_by_hotel = {h["hotel_id"]: h["total_brl"] for h in report["top_hotels"]}

    # Assert
    assert totals_by_hotel["H01"] == "0.01"
    assert totals_by_hotel["H02"] == "0.01"
    assert report["summary"]["total_amount_brl"] == "0.01"


def test_csv_with_utf8_bom_and_quoted_commas(tmp_path: Path) -> None:
    """Lê CSVs com UTF-8 BOM e nomes de hotéis com vírgula entre aspas."""
    # Arrange
    input_path = tmp_path / "reservas_bom.csv"
    rates_path = tmp_path / "rates_bom.csv"
    output_path = tmp_path / "report.json"

    with input_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow({**BASE, "hotel_name": "Hotel Praça, Centro", "amount": "1234.50"})

    rates_path.write_text("currency,rate_to_brl\nBRL,1.000000\n", encoding="utf-8-sig")

    # Act
    process = _run_raw_cli(
        ["--input", str(input_path), "--rates", str(rates_path), "--output", str(output_path)]
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))

    # Assert
    assert process.returncode == 0, process.stderr
    assert report["top_hotels"][0]["hotel_name"] == "Hotel Praça, Centro"
    assert report["summary"]["total_amount_brl"] == "1234.50"
