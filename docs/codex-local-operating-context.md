# Codex-local Operating Context

Tento dokument je startovni kontext pro budouci codex-local agenty v OpenWebUI. Cilem je, aby agent dokazal navazat na praci v ai-stacku bez hadani, bez vypisovani secretu a bez obchazeni viditelneho audit chatu.

## Cil agenta

Codex-local ma byt lokalni coding agent a domaci AI operator pro tento stack. Ma umet analyzovat repozitare, delat zmeny v registrovanych workspacech, spoustet testy, instalovat projektove zavislosti, zakladat lokalni repozitare, volitelne zakladat GitHub repozitare, dokumentovat provoz a po kontrole pushovat zmeny do GitHubu.

Zakladni pravidlo: autonomie se pridava pres sirsi, auditovatelne capability scopes, ne pres novy marker pro kazdou drobnost. Workspace-run muze mit vetsi pravomoci uvnitr registrovaneho workspace, ale porad musi mit timeouty, logy, omezeni na workspace cestu a nesmi vypisovat secrets. Nepridavat neomezene Docker/Git pravo mimo spravovane runtime flow.

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
python3 codex/bin/owui_chat_turn.py \
  --model codex-local-plan-qwen14b \
  --visible-prompt-file /tmp/visible.txt \
  --prompt-file /tmp/technical.txt \
  --status-interval 3 \
  --quiet
```

Pro opakovane mentor workflow nad auditem pouzivej helper
`codex/bin/mentor_codex_local.py`. Ten sklada visible a technical prompt za
tebe a vola `owui_chat_turn.py` pod kapotou. Je vhodny pro bezne operace typu
scan workspace, install/test/build/lint/verify, explicitni run command nebo
deploy. Rezim `audit` umi orchestrace po vice turnech: scan, verify dry-run a
nasledne reasoning navrh dalsiho kroku pres `--send-history`. Rezim
`autopilot` jde o krok dal: po `scan -> verify` necha codex-local vybrat prave
jeden dalsi bezpecny capability krok z povolene mnoziny a muze ho rovnou
spustit.

Pro admin operace pouzivej `--no-live-status`, pokud odpoved ma byt kratka a deterministicka. Pro dlouhe modelove analyzy live status zapni.

OpenWebUI API key nepredavej v prikazu. Helpery ho ctou z `OWUI_API_KEY`,
nebo bezpecneji z ignorovaneho `codex/state/openwebui-api.key`. Pro ulozeni
pouzij `codex/bin/store_openwebui_api_key.sh` nebo obecnejsi
`codex/bin/store_runtime_secret.sh openwebui-api`; helpery klic nevypisuji.
GitHub API token pro volitelne zakladani GitHub repozitaru ukladej pres
`codex/bin/store_runtime_secret.sh github-api`, ne pres shell literaly.

## Modely

- `codex-local-plan-qwen14b`: vychozi rychly model pro analyzy a mensi navrhy.
- `codex-local-build-qwen14b`: rychly model pro mensi editacni ulohy.
- `codex-local-plan-qwen32b`: pomalejsi deep mode pro slozitejsi uvazovani.
- `codex-local-build-qwen32b`: pomalejsi build/edit deep mode.

Na RTX 4080 16 GB je 14B prakticky vychozi volba. 32B muze byt pomaly, protoze cast bezi pres CPU.

OpenWebUI model settings prompt pro `codex-local-*` je verzovany v
`docs/codex-local-model-system-prompt.md`. Prompt ma model smerovat k normalni
lidske komunikaci; vykonani akci zajistuje OpenWebUI filter/tool vrstva.

`Codex Auto Tools Filter` ma rozpoznavat prirozene intenty typu "pullni
ai-stack a nasad", "ukaz deploy status", "vytvor nove repository Test2 a
vygeneruj ssh klic", bezne repo kontroly typu "zkontroluj git status",
developerske workflow typu "nainstaluj zavislosti" nebo "spust testy", a
explicitni "repo: X / spust prikaz: ...". Nemel by vyrabet novy marker pro
kazdou drobnost; cilem jsou sirsi capability workflow: deploy/status,
workspace-run, workspace-action, workspace-autopilot, create-repo recipe a pozdeji dalsi
profile-based schopnosti.
Cilem je, aby uzivatel nemusel znat interni `GATEWAY_ADMIN_*` markery.

## Bezpecnostni pravidla

- Nikdy nevypisovat API klice, tokeny, private SSH klice ani obsah `.env`.
- Nepouzivat obecny shell tool z OpenWebUI pro neomezene prikazy.
- Admin filter smi zapisovat jen whitelisted soubory.
- Pred pushem musi byt `blocked_paths` a `sensitive_paths_seen` prazdne.
- OpenWebUI nesmi mit pripojeny Docker socket bez jasneho duvodu.
- Runtime cesty `codex/state/`, `codex/audit/`, `logs/`, `.env`, `__pycache__`, `.bak-*` necommitovat.
- Pokud je potreba nova schopnost, nejdriv zvaz rozsirenou capability s jasnym profilem, misto dalsiho jednorazoveho markeru. Musi byt pojmenovana, testovana, auditovana a zdokumentovana.
- Bezny snapshot chat sam nic neprovadi primo. Pozadavky na shell, instalace, generovani klicu, GitHub repo, push nebo realne editace maji jit pres auditovany capability workflow pro konkretni workspace. Pokud capability existuje, agent ji ma pouzit; pokud chybi, ma si rict o rozsireni workspace profilu misto predstirani akce.

## Admin prikazy

Admin prikazy se posilaji pres technicky prompt, ne jako bezny viditelny text pro uzivatele.

- `GATEWAY_ADMIN_GIT_STATUS`: ukaze stav repozitare, allowed/blocked cesty a sensitive cesty.
- `GATEWAY_ADMIN_GIT_DIFF [path]`: ukaze diff jen pro whitelisted commitovatelne soubory. Bezpecne pred pushem.
- `GATEWAY_ADMIN_REPO_GUARD [workspace] [branch]`: read-only kontrola registrovaneho workspace, branch, dirty stavu a suspicious/sensitive cest bez vypisu obsahu souboru.
- `GATEWAY_ADMIN_WORKSPACE_SCAN [workspace]`: read-only scan manifestu, jazyku, package script names a navrzenych build/test prikazu bez spousteni prikazu.
- `GATEWAY_ADMIN_WORKSPACE_ACTION <workspace> <install|test|build|lint|verify> [--timeout seconds] [--env KEY=VALUE] [--dry-run]`: capability-based provedeni beznych developerskych akci nad registrovanym workspace. Resolver vybere prikaz z manifestu a scriptu projektu a spusti ho auditovane pres gateway. `verify` se snazi projekt overit agenticky jako sekvenci `lint -> test -> build` s preskakovanim nepodporovanych kroku.
- `GATEWAY_ADMIN_WORKSPACE_AUTOPILOT <workspace> [--timeout seconds] [--allow-actions install,test,build,lint] [--max-steps N] [--recommend-only] [--env KEY=VALUE]`: vyssi capability nad workspace. Nejdriv si pripravi `verify --dry-run`, z povolenych kroku vybere dalsi bezpecne capability kroky a bud je jen doporuci, nebo je rovnou provede. Po kazdem uspesnem kroku si plan prepocita a vraci `stop_reason`, aby bylo jasne, jestli skoncil kvuli limitu kroku, chybe nebo tomu, ze uz neni co delat. Pokud nenajde nic spustitelneho, vraci i `recommendation` odvozenou ze scanneru projektu, plus `patch_target`, `patch_hint`, `patch_summary` a `read_command`, aby agent neskoncil jen prazdnym "nic nejde", ale mel i smer k dalsimu patche a pripraveny dalsi read-only krok. Pro prirozene pozadavky typu "over projekt a pokracuj sam" je to preferovana cesta; bezny guardrail je `max_steps=2`.
- `GATEWAY_ADMIN_READ <path>`: precte whitelisted soubor bez cisel radku.
- `GATEWAY_ADMIN_READ_NUMBERED <path> [start] [end]`: precte whitelisted soubor s realnymi cisly radku. Pouzivat pred presnymi patchemi.
- `GATEWAY_ADMIN_APPLY_NOW`: aplikuje prilozeny unified diff na whitelisted soubory a provede validaci Python souboru.
- `GATEWAY_ADMIN_CHECK_STACK [workspace] [model]`: spusti celkovy healthcheck stacku a gateway smoke test.
- `GATEWAY_ADMIN_SMOKE [workspace]`: spusti gateway smoke test.
- `GATEWAY_ADMIN_GIT_PUSH <branch> <message>`: commitne a pushne pouze allowed cesty.
- `GATEWAY_ADMIN_SSH_KEYGEN`: vygeneruje SSH klic do ignorovane runtime cesty.
- `GATEWAY_ADMIN_CREATE_LOCAL_REPO <name> [--github] [--github-owner OWNER] [--private|--public] [--path PATH] [--port N] [--cpus N] [--memory 16g] [--default] [--restart]`: vytvori lokalni repo pod `/mnt/c/Repositories`, inicializuje Git, prida README, vygeneruje ignorovany deploy SSH klic, vrati public key a zaregistruje workspace. S `--github` pouzije `GITHUB_TOKEN`, `GITHUB_TOKEN_FILE`, nebo ignorovany `codex/state/github-api.token`, zalozi GitHub repo, prida deploy key a nastavi `origin`. Bez tokenu vraci `GITHUB_TOKEN_MISSING`.
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

## Vyssi autonomie

Preferovany smer rozvoje je mene jednorazovych markeru a vice sirsich,
auditovanych capabilities. Prakticky to znamena:

- pouzivat `workspace-run` pro read-only a explicitni prikazy uvnitr
  registrovaneho workspace,
- pouzivat `workspace-action` pro install/test/build/lint/verify,
- pro vicekrokovou praci pouzivat `mentor_codex_local.py audit` nebo
  `mentor_codex_local.py autopilot`,
- pro review-to-patch mentoring loop pouzivat `mentor_codex_local.py patch-plan`,
- pro pripravu maleho diff navrhu nad auditem pouzivat `mentor_codex_local.py apply-ready`,
- pro maly auditovany patch v bezpecnem scope pouzivat `mentor_codex_local.py apply-safe`,
- pro sirsi "dotahni to co nejdal" workflow pouzivat `mentor_codex_local.py improve`,
- pro bezne mentor orchestracni volani bez rucni volby modu pouzivat `mentor_codex_local.py delegate`,
- pro rychle rozhodnuti o sirce pravomoci pouzivat `mentor_codex_local.py profile`,
- capability rozsirovat po profilech use-casu, ne po jednotlivych vetach.

Priklad doporucovaciho autopilota:

```bash
python3 codex/bin/mentor_codex_local.py autopilot Odysseus-Lite --recommend-only
```

Priklad provedeni jednoho dalsiho bezpecneho kroku:

```bash
python3 codex/bin/mentor_codex_local.py autopilot Odysseus-Lite
```

Priklad recommendation-driven patch planning loopu:

```bash
python3 codex/bin/mentor_codex_local.py patch-plan Odysseus-Lite
```

Priklad apply-ready loopu, ktery pripravi i navrh unified diffu, ale nic neaplikuje:

```bash
python3 codex/bin/mentor_codex_local.py apply-ready Odysseus-Lite
```

Priklad apply-safe loopu, ktery po recommendation a read kroku pripravi maly diff,
lokalne overi jeho rozsah a pri splneni guardrailu ho rovnou aplikuje pres
`GATEWAY_ADMIN_APPLY_NOW`:

```bash
python3 codex/bin/mentor_codex_local.py apply-safe ai-stack
```

Priklad sirsiho improve loopu, ktery nejdriv necha bezet capability vrstvu
(`install/test/build/lint`), a kdyz to samo nestaci, prejde do read -> patch-plan
-> safe apply:

```bash
python3 codex/bin/mentor_codex_local.py improve ai-stack
```

Priklad delegacniho loopu, ktery z textu ukolu sam zvoli nejvhodnejsi orchestraci:

```bash
python3 codex/bin/mentor_codex_local.py delegate ai-stack "Fixni to a dotahni co zvladnes."
```

Priklad profilove klasifikace bez spousteni jakychkoli akci:

```bash
python3 codex/bin/mentor_codex_local.py profile ai-stack "Uprav README a aplikuj maly patch"
```

Profilove rozhodnuti vraci i:

- `confidence`: jak silne helper veri, ze zvoleny workflow odpovida zadani.
- `guardrail_summary`: kratke vysvetleni, proc je aktualni scope dostatecny a co jeste brani sirsi akci.
- `capability_id`: stabilni jmeno capability nebo roadmap smeru, ke kteremu se helper vztahuje.
- `capability_scope`: hruby scope capability, napriklad `remote_repo`, `host_runtime`, `workspace_runtime`, `workspace_capability` nebo `mentoring`.
- `capability_summary`: kratky verzovany popis capability z roadmap registry.
- `missing_capability_hint`: nejuzsi dalsi capability scope, ktery by daval smysl pridat nebo explicitne pouzit, pokud je ukol sirsi nez stavajici guardraily.

Capability registry je verzovany v `docs/codex-local-capability-roadmap.json` a slouzi jako maly zdroj pravdy pro budouci helpery, prompt tuning i OpenWebUI routovani.

`openwebui_codex_auto_tools_filter.py` uz umi nektere prirozene pozadavky, ktere jsou sirsi nez aktualni safe runtime scope, prelozit nejen na workflow, ale i na capability-roadmap stopu. Prakticky to znamena, ze u GitHub/release nebo host-runtime use-casu se v auditu objevi i `CAPABILITY_ROADMAP_ID`, `CAPABILITY_ROADMAP_SCOPE` a `CAPABILITY_ROADMAP_SUMMARY`.

Aktualni runtime profily:

- `review`: read-only analyza a dalsi krok bez spousteni.
- `capability`: auditovane capability kroky typu `run`, `install`, `test`, `build`, `lint`, `verify`, `autopilot`.
- `safe_patch`: maly patch v omezenem ai-stack safe scope.
- `runtime`: sirsi agenticky posun projektu, typicky `improve`, tedy capability first a patch az kdyz je to potreba.

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
