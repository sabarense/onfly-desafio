# Declaração de Uso de IA

## Ferramenta e modelo usados
- **Ferramenta**: Antigravity CLI
- **Orquestrador / Modelo**: Orchestrator (Argon), com subagente de revisão `backend-reviewer`
- **Nível de raciocínio**: Padrão da ferramenta (Medium)

## O que a IA fez
- Executou o setup do ambiente virtual Python 3.11.6 (`.venv`), instalou `requirements-dev.txt` e registrou a linha de base de testes (`5 failed in 1.55s`) e os hashes SHA-256 originais de `tests/` e `data/`.
- Leu a especificação (`README.md`), todo o código em `src/onfly_report/` e todos os testes em `tests/`, diagnosticando os defeitos no código do CLI e registrando os achados em `entrega/notas-investigacao.md`.
- Aplicou correções incrementais em:
  - `src/onfly_report/services/status_policy.py` (restringiu `is_billable` a `status == "CONFIRMED"`);
  - `src/onfly_report/services/stay_calculator.py` (corrigiu o cálculo de noites para `(checkout - checkin).days * rooms`);
  - `src/onfly_report/services/currency_converter.py` (substituiu divisão em `float` por multiplicação exata em `Decimal`, normalizando moeda e lançando `InputFileError` para moeda sem taxa);
  - `src/onfly_report/services/row_parser.py` (normalizou `status` e `currency` com `.strip().upper()`);
  - `src/onfly_report/services/report_builder.py` (corrigiu a ordenação de `top_hotels` para `(-item[1]["amount"], item[0])`);
  - `src/onfly_report/repositories/csv_reservation_repository.py` e `src/onfly_report/repositories/csv_rate_repository.py` (removeu o limite artificial de 200 linhas e adotou `encoding="utf-8-sig"`).
- Adicionou a suíte de testes extras em `tests/test_extra_validation.py` (testes unitários sem I/O com fakes em memória, validação de `InputFileError`, bordas de arredondamento `ROUND_HALF_UP`, virada de ano, UTF-8 BOM e códigos de saída do CLI), mantendo os testes originais e os arquivos em `data/` inalterados.
- Executou o CLI sobre `data/reservas_hoteis.csv` (`output/report.json`) e `data/exemplo-validacao.csv` (`output/exemplo.json`), validou a amostra campo a campo contra o `README.md`, rodou `pytest -q` (`45 passed in 5.20s`) e `ruff check src tests` (`All checks passed!`), submeteu a revisão ao subagente `backend-reviewer` e redigiu os documentos em `entrega/`.

## O que eu (o candidato) fiz

- Li a especificação (README) e defini a abordagem: delegar a investigação
  e a correção a um agente de IA orquestrado, com restrições explícitas
  (não alterar tests/ nem data/, usar Decimal, não fazer push).
- Elaborei os prompts de execução, com apoio do Claude (ver sessao-02),
  e escolhi o orquestrador e o modelo (Antigravity CLI / Argon).
- Revisei o relatório final do agente e identifiquei que a amostra de
  validação cobria só parte dos cenários; por isso pedi uma segunda
  rodada de auditoria (arredondamento half-up, desempate do top_hotels,
  by_month pelo check-in, BOM, códigos de saída).
- Rodei `pytest -q` por conta própria: 70 testes passando.
- Comparei o CLI com a amostra de validação campo a campo.
- Li o diff completo e conferi que tests/ e data/ originais
  não foram alterados.
- Publiquei o repositório e exportei as sessões.
