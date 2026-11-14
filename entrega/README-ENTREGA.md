# Entrega — Desafio Técnico Onfly (`onfly_report`)

## Defeitos corrigidos

1. **Filtro de status faturável (`src/onfly_report/services/status_policy.py`)**: `is_billable` aceitava qualquer status diferente de `"CANCELLED"`, incluindo reservas `"NO_SHOW"` nos totais faturáveis, `by_month` e `top_hotels`, em vez de aceitar apenas `"CONFIRMED"`.
2. **Cálculo de noites de quarto (`src/onfly_report/services/stay_calculator.py`)**: `calculate_room_nights` subtraía apenas o dia do mês (`checkout.day - checkin.day`), gerando noites negativas ou incorretas em viradas de mês/ano.
3. **Conversão cambial e uso de `float` (`src/onfly_report/services/currency_converter.py`)**: `convert_to_brl` convertia os valores `Decimal` para `float` e dividia o valor pela taxa de câmbio em vez de multiplicar em `Decimal` (`amount * rate`).
4. **Ordenação do ranking `top_hotels` (`src/onfly_report/services/report_builder.py`)**: `ranked_hotels` ordenava por `item[1]["amount"]` em ordem crescente, exibindo os hotéis de menor faturamento primeiro em vez de maior faturamento com desempate por `hotel_id` ascendente.
5. **Truncamento da leitura do CSV em 200 registros (`src/onfly_report/repositories/csv_reservation_repository.py`)**: `CsvReservationRepository.read` interrompia o laço de leitura ao atingir 200 linhas (`if len(rows) == 200: break`), descartando as 100 reservas finais de `data/reservas_hoteis.csv`.

*Defesas preventivas adicionadas (não eram defeitos nos dados originais, conforme verificado na auditoria):*
- Suporte a UTF-8 com ou sem BOM (`encoding="utf-8-sig"`) em `csv_reservation_repository.py` e `csv_rate_repository.py` (confirmado via `head -c3 ... | xxd` que os CSVs originais não possuem BOM `ef bb bf`).
- Normalização (`strip().upper()`) de `status` e `currency` em `row_parser.py` e lançamento de `InputFileError` (saída `2`) em `convert_to_brl` caso surja moeda ausente na tabela de taxas.

## Causa raiz

1. A função `is_billable` verificava `status != "CANCELLED"` em vez de `status == "CONFIRMED"`, tratando reservas `NO_SHOW` como faturáveis.
2. A função `calculate_room_nights` subtraía apenas o atributo `.day` das datas em vez de calcular a diferença em dias entre os objetos `date` completos (`(checkout - checkin).days`).
3. A função `convert_to_brl` convertia os operandos para `float` e dividia o valor pela taxa de câmbio em vez de multiplicar diretamente os valores `Decimal` (`amount * rate`).
4. A ordenação de `ranked_hotels` em `ReportBuilder.build` utilizava `item[1]["amount"]` positivo na chave do `sorted`, classificando os hotéis em ordem crescente de valor em vez de decrescente (`-item[1]["amount"]`).
5. O método `CsvReservationRepository.read` interrompia prematuramente o laço de leitura ao atingir 200 registros devido a uma condição `if len(rows) == 200: break`.

## Como investiguei

1. **Setup e linha de base**: Criei o ambiente virtual `.venv` com Python 3.11.6 (`py -3.11 -m venv .venv`) e instalei `requirements-dev.txt` (`pytest==8.3.4`, `ruff==0.8.4`). Executei `pytest -q`, registrando as 5 falhas iniciais (`5 failed in 1.55s`), além dos hashes SHA-256 originais de `tests/` e `data/` (o diretório não possuía `.git`, confirmado via `git status`).
2. **Diagnóstico contra o `README.md`**: Li integralmente o `README.md`, todos os arquivos em `src/onfly_report/` e todos os testes em `tests/`. Cruzei cada regra de negócio e contrato do CLI com o código-fonte e reproduzi cada divergência em `data/exemplo-validacao.csv` e `data/reservas_hoteis.csv`, documentando tudo em `entrega/notas-investigacao.md`.
3. **Correção incremental**: Corrigi um defeito por vez (`status_policy.py`, `stay_calculator.py`, `currency_converter.py`, `report_builder.py`, `csv_reservation_repository.py`), executando `pytest -q` após cada correção até os 5 testes originais passarem, e adicionei defesas preventivas de BOM UTF-8 e moeda ausente/normalizada.
4. **Auditoria completa dos 12 pontos de contrato e testes adicionais**: Verifiquei empiricamente os 12 itens da auditoria (arredondamento `ROUND_HALF_UP` em `2.675`/`0.125`, `by_month` por check-in e ordenado, `top_hotels` com desempate, contagens antes do filtro, noites em virada de mês/ano, média exata e divisão por zero, relatório sem `CONFIRMED`, `generated_at` com `-03:00`, formato sem milhar/notação científica, códigos de saída `0`/`2`/`1` e `--output` padrão, ausência de BOM nos CSVs originais via `head -c3 | xxd`, e preservação de vírgulas/acentos no JSON). Criei `tests/test_extra_validation.py` e `tests/test_extra_auditoria.py`, executei `ruff check src tests` e gerei `output/report.json` e `output/exemplo.json`.

## Como validar

No PowerShell (Windows), a partir da raiz do projeto:

```powershell
# 1. Executar toda a suíte de testes (fornecidos + extras)
.\.venv\Scripts\pytest -q

# 2. Verificar lint com ruff
.\.venv\Scripts\ruff check src tests

# 3. Gerar o relatório principal
$env:PYTHONPATH = "src"
.\.venv\Scripts\python -m onfly_report `
  --input data/reservas_hoteis.csv `
  --rates data/taxas_cambio.csv `
  --output output/report.json

# 4. Gerar o relatório da amostra de validação do README
.\.venv\Scripts\python -m onfly_report `
  --input data/exemplo-validacao.csv `
  --rates data/taxas_cambio.csv `
  --output output/exemplo.json
```

Em Bash/Linux (com o venv ativo):

```bash
pytest -q
PYTHONPATH=src python -m onfly_report --input data/reservas_hoteis.csv --rates data/taxas_cambio.csv --output output/report.json
PYTHONPATH=src python -m onfly_report --input data/exemplo-validacao.csv --rates data/taxas_cambio.csv --output output/exemplo.json
```

## Resultado do pytest

```text
......................................................................   [100%]
70 passed in 7.74s
```
