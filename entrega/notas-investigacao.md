# Notas de Investigação — Desafio Onfly (`onfly_report`)

## 1. Linha de base (Fase 1)
- Ambiente virtual criado com Python `3.11.6` (`.venv`) e dependências instaladas via `requirements-dev.txt` (`pytest==8.3.4`, `ruff==0.8.4`).
- `git status`: `fatal: not a git repository (or any of the parent directories): .git` (o diretório não é um repositório Git; hashes SHA-256 dos arquivos originais em `tests/` e `data/` foram registrados para comprovar integridade).
- Resultado inicial de `pytest -q`: **5 falhas em 5 testes** (`5 failed in 1.55s`), falhando em:
  1. `tests/test_currency.py::test_currency_conversion_to_brl` (`'118.45' != '642.00'`)
  2. `tests/test_nights.py::test_nights_across_month_boundary` (`-28 != 3`)
  3. `tests/test_regression_report.py::test_regression_report_on_sample` (`total_amount_brl: '1018.45' != '1542.00'`, `average_nightly_rate_brl: '203.69' != '308.40'`)
  4. `tests/test_status_rules.py::test_status_rules_exclude_cancelled_and_no_show` (`'120.00' != '100.00'`)
  5. `tests/test_top_hotels.py::test_top_hotels_are_ranked_descending` (`['H04', 'H03', 'H02'] != ['H01', 'H02', 'H03']`)

---

## 2. Defeitos identificados no código

### Defeito 1 — Política de status inclui `NO_SHOW` como faturável
- **Arquivo e trecho**: `src/onfly_report/services/status_policy.py` (linhas 6–8):
  ```python
  def is_billable(status: str) -> bool:
      """Indica se a reserva entra nos totais financeiros e de noites."""
      return status != "CANCELLED"
  ```
- **Regra violada**: Status — *"Só `CONFIRMED` é faturável. `CANCELLED` e `NO_SHOW` contam em `total_reservations` e nas contagens por status, mas não entram em noites, valores, `by_month` nem `top_hotels`."*
- **Comportamento observado vs. esperado**: Em `tests/test_status_rules.py` e em `data/exemplo-validacao.csv`, reservas `NO_SHOW` são somadas em `total_amount_brl`, `total_room_nights`, `by_month` e `top_hotels` (ex.: na amostra, `by_month` de `2025-01` soma 9 reservas em vez de 8). Esperado: apenas `status == "CONFIRMED"`.
- **Causa raiz**: A função `is_billable` verifica `status != "CANCELLED"` em vez de `status == "CONFIRMED"`, tratando reservas `NO_SHOW` como faturáveis.

### Defeito 2 — Cálculo de noites usa apenas `.day` em vez da diferença entre datas
- **Arquivo e trecho**: `src/onfly_report/services/stay_calculator.py` (linhas 8–10):
  ```python
  def calculate_room_nights(checkin: date, checkout: date, rooms: int) -> int:
      """Calcula noites de quarto: diferença de dias (não inclusiva) vezes quartos."""
      return (checkout.day - checkin.day) * rooms
  ```
- **Regra violada**: Noites — `room_nights = (checkout_date - checkin_date).days * rooms` (diferença não inclusiva, inclusive na virada de mês/ano).
- **Comportamento observado vs. esperado**: Para `2025-01-30` → `2025-02-02` (1 quarto), calcula `(2 - 30) * 1 = -28` noites; na amostra `exemplo-validacao.csv`, resulta em `total_room_nights = -33` e `average_nightly_rate_brl = "0.00"` (pois `total_nights <= 0`). Esperado: `3` noites no teste unitário e `28` na amostra.
- **Causa raiz**: A função `calculate_room_nights` subtrai apenas o atributo `.day` das datas em vez de calcular a diferença em dias entre os objetos `date` completos (`(checkout - checkin).days`).

### Defeito 3 — Conversão cambial invertida e uso de `float` em dinheiro
- **Arquivo e trecho**: `src/onfly_report/services/currency_converter.py` (linhas 8–11):
  ```python
  def convert_to_brl(amount: Decimal, currency: str, rates: dict[str, Decimal]) -> Decimal:
      """Converte um valor na moeda informada para BRL usando a taxa da tabela."""
      rate = rates[currency]
      return Decimal(str(float(amount) / float(rate)))
  ```
- **Regra violada**: Câmbio e Exatidão — *"Converta com `amount * rate_to_brl`"* e *"Use `Decimal`, nunca `float`, no dinheiro."*
- **Comportamento observado vs. esperado**: `100.00 USD` com taxa `5.420000` gera `Decimal('18.450184501845017')` em vez de `Decimal('542.00000000')` (`100.00 BRL + 100.00 USD = "118.45"` em vez de `"642.00"`). Além disso, introduz imprecisão de ponto flutuante binário (`float`).
- **Causa raiz**: A função `convert_to_brl` converte os operandos para `float` e divide o valor pela taxa de câmbio em vez de multiplicar diretamente os valores `Decimal` (`amount * rate`).

### Defeito 4 — Ranking `top_hotels` ordenado em ordem crescente
- **Arquivo e trecho**: `src/onfly_report/services/report_builder.py` (linha 91):
  ```python
  ranked_hotels = sorted(hotels.items(), key=lambda item: (item[1]["amount"], item[0]))
  ```
- **Regra violada**: Ordenação — *"`top_hotels`: `total_brl` decrescente; empate por `hotel_id` ascendente."*
- **Comportamento observado vs. esperado**: Em `tests/test_top_hotels.py`, com valores `1000, 900, 800, 700` e `--top 3`, retorna `['H04', 'H03', 'H02']` (os menores valores primeiro). Esperado: `['H01', 'H02', 'H03']`.
- **Causa raiz**: A ordenação de `ranked_hotels` em `ReportBuilder.build` utiliza `item[1]["amount"]` positivo na chave do `sorted`, classificando os hotéis em ordem crescente de valor em vez de decrescente (`-item[1]["amount"]`).

### Defeito 5 — Leitura de reservas truncada em 200 linhas
- **Arquivo e trecho**: `src/onfly_report/repositories/csv_reservation_repository.py` (linhas 44–49):
  ```python
  rows: list[RawRow] = []
  for values in reader:
      rows.append(RawRow(line_number=reader.line_num, values=values))
      if len(rows) == 200:
          break
  return rows
  ```
- **Regra violada**: Totais / Ordem do processamento — *"`total_reservations` = número de reservas no arquivo"*.
- **Comportamento observado vs. esperado**: `CsvReservationRepository.read()` retorna apenas 200 linhas ao ler `data/reservas_hoteis.csv` (que possui 300 reservas de dados além do cabeçalho), descartando as 100 reservas finais. Esperado: ler todas as 300 reservas do arquivo.
- **Causa raiz**: O método `CsvReservationRepository.read` interrompe prematuramente o laço de leitura ao atingir 200 registros devido a uma condição `if len(rows) == 200: break`.

### Achado adicional de robustez (UTF-8 BOM em CSVs e moeda desconhecida/normalização)
- **Arquivos e trechos**:
  - `src/onfly_report/repositories/csv_reservation_repository.py` (linha 38) e `src/onfly_report/repositories/csv_rate_repository.py` (linha 23) abrem os arquivos com `encoding="utf-8"`.
  - `src/onfly_report/services/currency_converter.py` (linha 10) e `src/onfly_report/services/row_parser.py` (linhas 33–34): `CsvRateRepository` normaliza moedas com `.strip().upper()`, mas `RowParser` fazia apenas `.strip()` e `convert_to_brl` acessava `rates[currency]` diretamente, levantando `KeyError` (código de saída `1`) em vez de `InputFileError` (código `2`) caso surgisse moeda ausente na tabela de câmbio ou em minúsculas.
- **Comportamento observado e correção**:
  - Ao testar empiricamente um CSV com BOM UTF-8 (`\xef\xbb\xbf`), `csv.DictReader` preserva `\ufeff` no nome da primeira coluna (`\ufeffreservation_id` / `\ufeffcurrency`), causando `InputFileError: colunas obrigatórias ausentes`. Usar `encoding="utf-8-sig"` remove o BOM automaticamente quando presente e mantém leitura idêntica quando ausente.
  - Em `RowParser`, normalizar `status` e `currency` com `.strip().upper()`, e em `convert_to_brl`, validar a existência da moeda normalizada em `rates` lançando `InputFileError` quando ausente (preservando o contrato de saída `2` do CLI).

---

## 3. Verificação dos 13 pontos obrigatórios (Fase 2.4)

1. **`float` em dinheiro ou na média**: Confirmado `float(` em `currency_converter.py:11` (defeito 3). Na média (`report_builder.py:77-79`), já usa `total_amount / Decimal(total_nights)` sem `float`.
2. **Arredondamento (`ROUND_HALF_UP`)**: Em `report_builder.py:20-22`, `format_money` já usa `value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`. Validado empiricamente: `format_money(Decimal("0.005")) == "0.01"` e `format_money(Decimal("0.015")) == "0.02"`. Soma exata ocorre antes da serialização.
3. **Cálculo de noites**: Defeito 2 confirmado em `stay_calculator.py:10` (`checkout.day - checkin.day`).
4. **Filtro de status e ordem das operações**: Em `report_builder.py:42-49`, a ordem está correta (`ler → parse → contar status → filtrar is_billable → agregar`), porém `is_billable` em `status_policy.py:8` está incorreto (defeito 1).
5. **Câmbio (multiplicação vs. divisão, moeda)**: Defeito 3 confirmado em `currency_converter.py:11` (divisão em vez de multiplicação). `CsvRateRepository` normaliza moedas com `.strip().upper()`; `RowParser` usa `.strip()` em `currency`.
6. **`by_month` pelo mês do check-in e ordenação**: Em `report_builder.py:68, 88`, usa `row.checkin.strftime("%Y-%m")` e `sorted(monthly.items())` (ordem ascendente por `"YYYY-MM"`). Correto.
7. **`top_hotels` ordenação, desempate e `--top`**: Defeito 4 confirmado em `report_builder.py:91` (ordena crescente em vez de decrescente); desempate por `item[0]` (`hotel_id` ascendente) e slice `[: self._top_n]` já estão corretos.
8. **Parsing do CSV (vírgula entre aspas, UTF-8/BOM, espaços)**: `csv.DictReader` trata corretamente nomes entre aspas com vírgula (ex.: `"Hotel Lisboa, Centro"`, `"Hotel Madrid, Gran Vía"`); `RowParser` e `CsvRateRepository` aplicam `.strip()`. Porém ambos repositórios usam `encoding="utf-8"` em vez de `encoding="utf-8-sig"`, falhando quando há UTF-8 BOM.
9. **Parsing de datas**: `date.fromisoformat(...)` em `row_parser.py:30-31` lê datas civis `YYYY-MM-DD` sem timezone e levanta `ValueError` (convertido em `InputFileError`) em formatos inválidos. Correto.
10. **Média (divisão por zero e arredondamento)**: `report_builder.py:77-79` trata `total_nights > 0` retornando `Decimal("0")` (`"0.00"`) quando não há noites, e arredonda apenas via `format_money` na serialização. Correto.
11. **`generated_at` com `-03:00`**: `FIXED_OFFSET = timezone(timedelta(hours=-3))` e `now.isoformat(timespec="seconds")` em `report_builder.py:17, 102, 106` produz formato `YYYY-MM-DDTHH:MM:SS-03:00`. Correto.
12. **Validação de `--top` e arquivos inexistentes; códigos de saída**: Testado empiricamente via `subprocess` — `--top 0`, `--top -1`, `--top abc`, `--input` inexistente e `--rates` inexistente retornam todos código `2`; erros inesperados retornam `1`; sucesso retorna `0`.
13. **Serialização**: `format_money` gera strings com 2 casas decimais sem separador de milhar (`format_money(Decimal("1234.5")) == "1234.50"`). Correto.


---

## 4. Auditoria Final (Itens 1 a 12)

- Item 1: OK (Todos os 4 pontos de serialização monetária em `src/onfly_report/services/report_builder.py:86,97,113,114` chamam `format_money`, que executa `str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))` nas linhas 16–22, sem `round()` nem `float`; comprovado com `format_money(Decimal("2.675")) == "2.68"` e `format_money(Decimal("0.125")) == "0.13"`)
- Item 2: OK (`by_month` em `src/onfly_report/services/report_builder.py:49,61,68-71,81-89` itera somente sobre `confirmed`, agrupa por `row.checkin.strftime("%Y-%m")` — mês do check-in, não do check-out — e ordena com `sorted(monthly.items())` em ordem ascendente de `month`, conforme evidenciado no caso `2025-01-30` → `2025-02-02` contado em `2025-01` e `2024-12-30` → `2025-01-02` contado em `2024-12` antes de `2025-01`)
- Item 3: OK (CORRIGIDO na Fase 3: `src/onfly_report/services/report_builder.py:91-100` ordena `hotels` — já filtrado apenas para `CONFIRMED` — com `key=lambda item: (-item[1]["amount"], item[0])` e aplica `[: self._top_n]`; empate entre `H10` (`300.00`) e `H02` (`300.00`) resultou em `["H02", "H10", "H03"]`, e com `--top 10` e apenas 3 hotéis retornou 3 itens)
- Item 4: OK (CORRIGIDO na Fase 3 o truncamento em 200 linhas em `csv_reservation_repository.py`: em `src/onfly_report/services/report_builder.py:42-49,108-111`, `len(reservations)` e `status_counts` são calculados sobre todas as reservas antes de `confirmed = [row for row in reservations if is_billable(row.status)]`; no caso com 3 `CONFIRMED`, 1 `CANCELLED` e 1 `NO_SHOW`, o resumo reportou `total_reservations=5, confirmed_reservations=3, cancelled_reservations=1, no_show_reservations=1`)
- Item 5: OK (CORRIGIDO na Fase 3: `src/onfly_report/services/stay_calculator.py:8-10` calcula `(checkout - checkin).days * rooms`; evidência executada: `(a) 2025-01-30 -> 2025-02-02, 2 quartos = 6`, `(b) 2024-12-30 -> 2025-01-02, 1 quarto = 3`, `(c) 2025-01-10 -> 2025-01-13, 1 quarto = 3`)
- Item 6: OK (`src/onfly_report/services/report_builder.py:77-79,114` calcula `total_amount / Decimal(total_nights) if total_nights > 0 else Decimal("0")` e arredonda apenas via `format_money(average_nightly_rate)`; com `total_room_nights=0` retorna `"0.00"` sem `ZeroDivisionError`, e com `total_amount="700.00"` / `total_room_nights=8` retorna `"87.50"`)
- Item 7: OK (Com apenas `CANCELLED` e `NO_SHOW`, `ReportBuilder.build()` retornou `total_room_nights=0`, `total_amount_brl="0.00"`, `average_nightly_rate_brl="0.00"`, `by_month=[]` e `top_hotels=[]`)
- Item 8: OK (`src/onfly_report/services/report_builder.py:17,102,106` define `FIXED_OFFSET = timezone(timedelta(hours=-3))`, `now = self._now or datetime.now(FIXED_OFFSET)` e `now.isoformat(timespec="seconds")`, gerando timestamp ISO 8601 com offset fixo `-03:00`, ex.: `"2026-11-14T14:52:12-03:00"`)
- Item 9: OK (`format_money` em `src/onfly_report/services/report_builder.py:20-22` usa `str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))`; evidência: `Decimal("1234.50") -> "1234.50"`, `Decimal("0.00") -> "0.00"`, `Decimal("1000000.00") -> "1000000.00"`, sem separador de milhar e sem notação científica)
- Item 10: OK (`src/onfly_report/cli.py:24-27,36-37,45-61`; evidência executada: `--top 0 -> exit=2`, `--top -1 -> exit=2`, `--top abc -> exit=2`, `--input inexistente -> exit=2`, `--rates inexistente -> exit=2`, sem `--input -> exit=2`, sem `--rates -> exit=2`, `RuntimeError` simulado -> `exit=1`, execução sem `--output` em diretório limpo criou `output/report.json` e retornou `exit=0`)
- Item 11: OK (`head -c3 data/reservas_hoteis.csv | xxd` imprimiu `00000000: 7265 73  res` e `head -c3 data/taxas_cambio.csv | xxd` imprimiu `00000000: 6375 72  cur`, comprovando ausência de BOM UTF-8 nos arquivos originais e confirmando que `encoding="utf-8-sig"` é defesa preventiva)
- Item 12: OK (`csv.DictReader` preserva vírgulas entre aspas e `src/onfly_report/cli.py:56` grava JSON com `ensure_ascii=False`; `"Hotel Madrid, Gran Vía"` e `"Hotel Lisboa, Centro"` saem íntegros e acentuados no JSON)
