# Codex-local Operating Context

Tento dokument je startovni kontext pro budouci codex-local agenty v OpenWebUI. Cilem je, aby agent dokazal navazat na praci v ai-stacku bez hadani, bez vypisovani secretu a bez obchazeni viditelneho audit chatu.

## Cil agenta

Codex-local ma byt lokalni coding agent a domaci AI operator pro tento stack. Ma umet analyzovat repozitare, navrhovat zmeny, pripravovat whitelisted patche, spoustet bezpecne healthchecky, dokumentovat provoz a po kontrole pushovat zmeny do GitHubu.

Zakladni pravidlo: autonomie se pridava postupne a jen pres konkretni, uzke, auditovatelne nastroje. Nepridavat obecny shell ani neomezene Docker/Git pravo do OpenWebUI.

## Architektura

- OpenWebUI bezi na `http://192.168.0.48:9090` a slouzi jako viditelne UI i audit chat.
- Ollama bezi na `http://192.168.0.48:11434` a poskytuje lokalni modely.
- Codex gateway bezi na `http://192.168.0.48:9101` a vystavuje OpenAI-compatible model aliasy pro OpenWebUI.
- OpenCode workspaces bezi jako izolovane kontejnery nad registrovanymi repozitari.
- `ai-stack` je verzovany zdroj pravdy pro konfigurace, helpery, dokumentaci a OpenWebUI admin filter.
- Runtime stav, secrets, logy a private key material jsou ignorovane a nepatri do Gitu.

## Viditelny audit chat

Primarni audit chat je:

`http://192.168.0.48:9090/c/57529037-84b9-42e1-8bae-9eab35b601bd`

Prace s repozitari ma jit pres `codex/bin/owui_chat_turn.py`. Helper zapise lidsky citelnou instrukci do OpenWebUI historie a technicky prompt muze poslat oddelene. Diky tomu uzivatel vidi zamer, prubeh a vysledek, ale nevidi interni admin payloady nebo dlouhe diffy, pokud to neni potreba.

Doporuceny vzor:

```bash
OWUI_API_KEY=<set locally> python3 codex/bin/owui_chat_turn.py \
  --model codex-local-plan-qwen14b \
  --visible-prompt-file /tmp/visible.txt \
  --prompt-file /tmp/technical.txt \
  --status-interval 3 \
  --quiet
```

Pro admin operace pouzivej `--no-live-status`, pokud odpoved ma byt kratka a deterministicka. Pro dlouhe modelove analyzy live status zapni.

## Modely

- `codex-local-plan-qwen14b`: vychozi rychly model pro analyzy a mensi navrhy.
- `codex-local-build-qwen14b`: rychly model pro mensi editacni ulohy.
- `codex-local-plan-qwen32b`: pomalejsi deep mode pro slozitejsi uvazovani.
- `codex-local-build-qwen32b`: pomalejsi build/edit deep mode.

Na RTX 4080 16 GB je 14B prakticky vychozi volba. 32B muze byt pomaly, protoze cast bezi pres CPU.

## Bezpecnostni pravidla

- Nikdy nevypisovat API klice, tokeny, private SSH klice ani obsah `.env`.
- Nepouzivat obecny shell tool z OpenWebUI pro neomezene prikazy.
- Admin filter smi zapisovat jen whitelisted soubory.
- Pred pushem musi byt `blocked_paths` a `sensitive_paths_seen` prazdne.
- OpenWebUI nesmi mit pripojeny Docker socket bez jasneho duvodu.
- Runtime cesty `codex/state/`, `codex/audit/`, `logs/`, `.env`, `__pycache__`, `.bak-*` necommitovat.
- Pokud je potreba novy nastroj, ma byt uzky, pojmenovany, testovany a zdokumentovany.
- Bezny chat je read-only. Pozadavky na shell, instalace, generovani klicu, GitHub repo, push nebo realne editace musi bud vratit vysvetleni, nebo jit pres explicitni whitelisted admin/tool workflow.

## Admin prikazy

Admin prikazy se posilaji pres technicky prompt, ne jako bezny viditelny text pro uzivatele.

- `GATEWAY_ADMIN_GIT_STATUS`: ukaze stav repozitare, allowed/blocked cesty a sensitive cesty.
- `GATEWAY_ADMIN_GIT_DIFF [path]`: ukaze diff jen pro whitelisted commitovatelne soubory. Bezpecne pred pushem.
- `GATEWAY_ADMIN_REPO_GUARD [workspace] [branch]`: read-only kontrola registrovaneho workspace, branch, dirty stavu a suspicious/sensitive cest bez vypisu obsahu souboru.
- `GATEWAY_ADMIN_WORKSPACE_SCAN [workspace]`: read-only scan manifestu, jazyku, package script names a navrzenych build/test prikazu bez spousteni prikazu.
- `GATEWAY_ADMIN_READ <path>`: precte whitelisted soubor bez cisel radku.
- `GATEWAY_ADMIN_READ_NUMBERED <path> [start] [end]`: precte whitelisted soubor s realnymi cisly radku. Pouzivat pred presnymi patchemi.
- `GATEWAY_ADMIN_APPLY_NOW`: aplikuje prilozeny unified diff na whitelisted soubory a provede validaci Python souboru.
- `GATEWAY_ADMIN_CHECK_STACK [workspace] [model]`: spusti celkovy healthcheck stacku a gateway smoke test.
- `GATEWAY_ADMIN_SMOKE [workspace]`: spusti gateway smoke test.
- `GATEWAY_ADMIN_GIT_PUSH <branch> <message>`: commitne a pushne pouze allowed cesty.
- `GATEWAY_ADMIN_SSH_KEYGEN`: vygeneruje SSH klic do ignorovane runtime cesty.
- `GATEWAY_ADMIN_GIT_UNTRACK_IGNORED`: pomuze odstranit ignorovane runtime soubory z indexu, pokud se tam omylem dostaly.

## Workflow zmeny

1. Zacni viditelnou zpravu v audit chatu: co chces zmenit a proc.
2. Ziskej kontext pres `GATEWAY_ADMIN_READ_NUMBERED`, ne pres hadani line numberu.
3. Nech lokalni model navrhnout zmenu nebo patch, ale over faktickou spravnost.
4. Aplikuj jen maly whitelisted patch pres `GATEWAY_ADMIN_APPLY_NOW`.
5. Spust `GATEWAY_ADMIN_GIT_DIFF` a zkontroluj, ze diff odpovida zameru.
6. Spust `GATEWAY_ADMIN_CHECK_STACK ai-stack codex-local-plan-qwen14b`.
7. Spust `GATEWAY_ADMIN_GIT_STATUS` a over `(none)` u blocked/sensitive cest.
8. Pushni pres `GATEWAY_ADMIN_GIT_PUSH main <message>`.
9. Po pushi zkontroluj cisty status.

## Pro nove nastroje

Novy nastroj ma splnit:

- uzky ucel a jasny nazev,
- zadne vypisovani secretu,
- validace vstupu,
- timeouty a retry/backoff u sitovych volani,
- dokumentace v README nebo v tomto dokumentu,
- test pres OpenWebUI audit chat,
- commit a push do `Jeycob/Ai-Stack`.

## Aktualni priorita rozvoje

1. Zlepsovat spolehlivost viditelneho OpenWebUI workflow.
2. Pridavat bezpecne read-only a review nastroje pred zapisovymi nastroji.
3. Rozsirovat workspace schopnosti pro realne programovaci use-cases.
4. Pripravovat budouci integrace Home Assistant a read-only financnich dat s duslednym secret managementem.
