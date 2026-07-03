# AI Stack

Lokální AI stack pro OpenWebUI, Ollama a izolované Codex/OpenCode workspaces. Repozitář slouží jako verzovaná konfigurace pro domácí AI prostředí, správu coding agentů a budoucí integrace typu Home Assistant nebo analýza výdajů.

## Komponenty

- OpenWebUI: webové UI na `http://192.168.0.48:9090`; slouží jako hlavní chatové rozhraní a rodinný AI rádce.
- Ollama: lokální model server na `http://192.168.0.48:11434`; poskytuje modely jako `qwen2.5-coder:14b` a `qwen2.5-coder:32b`.
- Codex gateway: OpenAI-compatible proxy na `http://192.168.0.48:9101`; směruje požadavky do registrovaných repozitářových workspaces.
- OpenCode workspaces: izolované kontejnery pro coding agenty nad konkrétními repozitáři.
- Docker Compose: spouští OpenWebUI a související služby.
- WSL/Ubuntu: primární runtime vrstva pro Docker, gateway a helper skripty.
- Watcher: `codex/bin/watch_gateway.sh` validuje změny gateway a po úspěšné kontrole ji restartuje.

## Hlavní soubory a adresáře

- `docker-compose.yml`: definice OpenWebUI služby a persistentních volume.
- `start_docker.bat`: Windows startovací skript volaný po startu systému nebo ručně.
- `codex/bin/start_codex_stack.sh`: startuje Codex/OpenCode stack, gateway a workspaces ve WSL.
- `codex/bin/add_workspace.py`: registruje nový repozitářový workspace do `codex/workspaces.json`.
- `codex/bin/watch_gateway.sh`: hlídá změny `codex/gateway/gateway.py`, validuje syntaxi a restartuje gateway.
- `codex/bin/openwebui_codex_auto_tools_filter.py`: OpenWebUI filter pro automatické připojení toolsetů a úzké mapování bezpečných codex-local intentů.
- `codex/gateway/gateway.py`: OpenAI-compatible gateway pro modely a workspace snapshoty.
- `codex/workspaces.json`: registr workspaces, portů a resource limitů.
- `codex/opencode-default.json`: výchozí OpenCode konfigurace pro nové workspaces.
- `docs/codex-local-operating-context.md`: startovní provozní kontext pro budoucí codex-local agenty.
- `docs/codex-local-model-system-prompt.md`: verzovaný system prompt pro OpenWebUI nastavení modelů `codex-local-*`.
- `codex/audit/`: lokální provozní logy; nepatří do Gitu.
- `codex/state/`: runtime stav, hesla a home adresáře agentů; nepatří do Gitu.

## Secret management

Nikdy necommitovat secrets. Do Gitu patří pouze šablony, dokumentace a bezpečné konfigurace.

- `.env` drží lokální secrets jako `WEBUI_SECRET_KEY`; soubor má zůstat ignorovaný.
- `codex/state/` obsahuje runtime stav a secrets, například OpenCode server password nebo home adresář; adresář má zůstat ignorovaný.
- `codex/state/codex-gateway-admin.token` je lokální token pro admin endpointy gateway; generuje ho `codex/bin/start_codex_stack.sh` a nesmí být commitovaný.
- OpenWebUI API klíče, Home Assistant tokeny, bankovní/Fio tokeny a privátní SSH klíče nesmí být v tracked souborech.
- Pokud bude potřeba GitHub SSH deploy/push klíč, privátní klíč ulož pouze do ignorované cesty a veřejný klíč přidej do GitHubu jako deploy key nebo účetní SSH key.
- Pro sdílenou dokumentaci používej placeholdery typu `OPENWEBUI_API_KEY=<set locally>` místo skutečných hodnot.
- Runtime secrets ukládej helperem `codex/bin/store_runtime_secret.sh <secret-name>`. Podporované názvy jsou `openwebui-api`, `github-api` a `codex-gateway-admin`; hodnoty se uloží pod ignorované `codex/state/` s režimem `600`.

## Konfigurace modelů

Gateway zveřejňuje OpenAI-compatible aliasy pro OpenWebUI.

- `codex-local-plan-qwen14b`: rychlý výchozí model pro běžné repo dotazy.
- `codex-local-build-qwen14b`: rychlý výchozí model pro menší úpravy a patche.
- `codex-local-plan-qwen32b`: pomalejší, silnější varianta pro složitější analýzy.
- `codex-local-build-qwen32b`: pomalejší, silnější varianta pro složitější build/edit požadavky.

Na RTX 4080 16 GB je 14B praktičtější výchozí volba, protože se vejde do VRAM. 32B může běžet částečně přes CPU a je vhodnější jako ruční deep mode.

## Příklady práce s codex-local agentem

Primární způsob práce je přes viditelný OpenWebUI audit chat. Instrukce mají být konkrétní, ideálně s prvním řádkem `repo: <workspace>`, aby gateway věděla, nad kterým repozitářem má agent pracovat.

Příklad rychlé analýzy bez editace:

    repo: ai-stack
    Prohlédni strukturu projektu a stručně řekni, jak je zapojená gateway. Nic needituj.

Příklad práce nad jiným registrovaným repozitářem:

    repo: Odysseus-Lite
    Najdi hlavní runtime soubory, popiš architekturu a navrhni další testy. Nic needituj.

Příklad bezpečné změny souborů:

    repo: ai-stack
    Uprav README.md a doplň sekci s provozními příklady. Měň jen README.md a po změně ukaž git status.

Příklad explicitního admin statusu:

    repo: ai-stack
    GATEWAY_ADMIN_GIT_STATUS

Příklad bezpečného diffu jen pro whitelisted soubory:

    repo: ai-stack
    GATEWAY_ADMIN_GIT_DIFF

Příklad read-only guard kontroly registrovaného workspace:

    repo: ai-stack
    GATEWAY_ADMIN_REPO_GUARD ai-stack main

Příklad rychlého read-only scanu workspace:

    repo: ai-stack
    GATEWAY_ADMIN_WORKSPACE_SCAN ai-stack

Jak je myšlená autonomie: modely `codex-local-*` nemají mít neomezený shell v normální chat cestě, ale nemají ani zbytečně končit u “jsem read-only”. Umí číst snapshot workspace, vysvětlovat, navrhovat patch a hlavně používat širší auditované capability workflow pro daný workspace. Rizikovější akce jako shell, instalace balíčků, generování SSH klíčů, vytváření GitHub repozitářů, push a reálné editace souborů tedy nemají být předstírané; mají jít přes capability vrstvu a být viditelné v audit chatu.

Příklad vytvoření nového lokálního repozitáře, deploy SSH klíče a workspace:

    Vytvoř nové repository Test2 a vygeneruj mi ssh klíč pro něj.

Auto-tools filter tento přirozený prompt přeloží na:

    repo: ai-stack
    GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2 --restart

Tento workflow vytvoří lokální repo pod `/mnt/c/Repositories`, inicializuje Git, přidá README, vygeneruje private key do ignorovaného `codex/state/ssh/`, vrátí public key a zaregistruje workspace.

Pokud uživatel výslovně řekne GitHub, například “vytvoř GitHub repository Test2”, auto-tools filter přidá `--github`. Gateway pak použije `GITHUB_TOKEN`, `GITHUB_TOKEN_FILE`, nebo ignorovaný `codex/state/github-api.token`, vytvoří GitHub repo, přidá public key jako write deploy key a nastaví lokálnímu repozitáři `origin`. Bez tokenu vrátí jasný `GITHUB_TOKEN_MISSING`; nebude tvrdit, že GitHub repo vzniklo.

GitHub token ulož lokálně bez vypsání do historie takto:

    codex/bin/store_runtime_secret.sh github-api

Obecnější explicitní akce se neposílají přes nový endpoint pro každou drobnost, ale přes širší workspace runner. Například:

    repo: Test2
    spusť příkaz: git status --short --branch

Auto-tools filter to přeloží na:

    repo: ai-stack
    GATEWAY_ADMIN_RUN_WORKSPACE Test2 --timeout 300 -- git status --short --branch

U nejběžnějších read-only repo akcí není nutné psát ani `spusť příkaz:`. Například `repo: Test2` a “zkontroluj git status” se přeloží na stejný workspace runner s `git status --short --branch`; “ukaž git remote” na `git remote -v`; “ukaž poslední commity” na `git log -5 --oneline`.

Podobně existuje širší workflow i pro běžné developerské akce. Například:

    repo: Test2
    nainstaluj závislosti

nebo:

    repo: Test2
    spusť testy

Auto-tools filter to přeloží na:

    repo: ai-stack
    GATEWAY_ADMIN_WORKSPACE_ACTION Test2 install --timeout 1800

nebo:

    repo: ai-stack
    GATEWAY_ADMIN_WORKSPACE_ACTION Test2 test --timeout 1800

Resolver se dívá na manifesty workspace a volí přirozený příkaz pro daný stack, například `npm install`, `python -m pip install -r requirements.txt`, `cargo test`, `go test ./...` nebo `./gradlew test`. Když vhodný příkaz nenašel, vrátí auditovatelný `unsupported` výsledek místo předstírání úspěchu.

Ještě výš je capability `verify`, která se chová víc agenticky: pokusí se z workspace odvodit a v pořadí provést `lint -> test -> build`, přeskakuje nepodporované kroky a vrací stručný audit celého ověření. Příklad:

    repo: Test2
    ověř projekt

Nad tím je ještě capability `autopilot`, která se chová víc jako lokální řízený agent: nejdřív udělá `verify --dry-run`, z dostupných capability kroků vybere další bezpečné kroky a podle limitu je buď jen doporučí, nebo je rovnou provede. Typicky běží s limitem jednoho kroku, ale pro přirozené “pokračuj sám” workflow může bezpečně udělat krátký chain do `max_steps=2`. Po každém úspěšném kroku si plán znovu přepočítá a vrací i `stop_reason`, aby bylo jasné, proč skončil. Když nenajde nic spustitelného, vrátí místo prázdného failu i konkrétní `recommendation`, plus `patch_target`, `patch_hint`, `patch_summary` a hotový `read_command`, tedy kde by se typicky upravovalo, co v projektu chybí a jaký další read krok má smysl udělat před patchem. Příklad doporučení:

    repo: Test2
    navrhni další krok a zatím nic nespouštěj

Příklad autonomnějšího pokračování:

    repo: Test2
    ověř projekt a pokračuj sám

Příklad čtení whitelisted souboru:

    repo: ai-stack
    GATEWAY_ADMIN_READ codex/gateway/gateway.py

Příklad čtení whitelisted souboru s reálnými čísly řádků pro přesnější patche:

    repo: ai-stack
    GATEWAY_ADMIN_READ_NUMBERED README.md 68 84

Příklad vestavěného smoke testu gateway:

    repo: ai-stack
    GATEWAY_ADMIN_SMOKE ai-stack

Příklad celkového healthchecku z OpenWebUI admin filteru:

    repo: ai-stack
    GATEWAY_ADMIN_CHECK_STACK ai-stack codex-local-plan-qwen14b

Příklad reálného spuštění příkazu v registrovaném workspace přes gateway admin endpoint:

    repo: ai-stack
    GATEWAY_ADMIN_RUN_WORKSPACE ai-stack -- git status --short --branch

Příklad příkazu s delším timeoutem:

    repo: ai-stack
    GATEWAY_ADMIN_RUN_WORKSPACE ai-stack --timeout 120 -- python3 -m py_compile codex/gateway/gateway.py

Příklad capability-based akce nad workspace:

    repo: ai-stack
    GATEWAY_ADMIN_WORKSPACE_ACTION ai-stack lint --dry-run

Příklad vyššího ověřovacího workflow:

    repo: ai-stack
    GATEWAY_ADMIN_WORKSPACE_ACTION ai-stack verify --dry-run

Příklad registrace nového workspace přes gateway admin endpoint:

    repo: ai-stack
    GATEWAY_ADMIN_ADD_WORKSPACE NewRepo /mnt/c/Repositories/NewRepo --port 4099

Příklad natažení poslední verze `ai-stack` z Gitu a nasazení stacku:

    repo: ai-stack
    GATEWAY_ADMIN_DEPLOY_STACK

Protože nasazení restartuje i gateway, běží asynchronně. Stav sleduj dalším dotazem:

    repo: ai-stack
    GATEWAY_ADMIN_DEPLOY_STATUS

Deploy skript nejdřív provede `git pull --ff-only`, ověří Python soubory a až potom restartuje Codex stack a OpenWebUI. Po restartu čeká na gateway, OpenWebUI root a `/static/loader.js`, aby krátký náběh služby nevypadal jako chyba. Pokud `sudo` vyžaduje heslo, pokusí se o restart přes WSL root interop (`wsl.exe -d Ubuntu -u root`), stejně jako Windows startovací `.bat` skript. Když selže i to, vypíše ruční fallback. Pokud má k dispozici `OWUI_API_KEY` nebo ignorovaný soubor `codex/state/openwebui-api.key`, po restartu také sesynchronizuje OpenWebUI admin filter a auto-tools filter funkci.

Admin odpovědi drží hlavní stav nahoře a dlouhé části jako `output`, `tail` nebo `log_tail` balí do rozbalovacích `<details>` bloků, aby chat zůstal čitelný. OpenWebUI raw HTML v Markdownu escapuje, proto `openwebui/loader.js` tyto doslovné bloky po vykreslení zprávy převádí na skutečné lokální dropdowny.

`Codex Auto Tools Filter` navíc umí pro modely `codex-local-*` rozpoznat přirozené požadavky a mapovat je na několik širších capability workflow. Například “pullni ai-stack a nasaď” přepíše interně na `GATEWAY_ADMIN_DEPLOY_STACK`; “ukaž deploy status/log” přepíše na `GATEWAY_ADMIN_DEPLOY_STATUS`; “vytvoř nové repository Test2 a vygeneruj ssh klíč” přepíše na `GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2 --restart`; běžné repo kontroly jako git status/remote/log nebo explicitní “spusť příkaz:” v registrovaném workspace přepíše na `GATEWAY_ADMIN_RUN_WORKSPACE`; běžné developerské akce jako install/test/build/lint/verify přepíše na `GATEWAY_ADMIN_WORKSPACE_ACTION`; širší požadavky typu “ověř a pokračuj sám”, “udělej co je potřeba”, “dotáhni to” nebo “navrhni další krok” přepíše na `GATEWAY_ADMIN_WORKSPACE_AUTOPILOT`, obvykle s limitem dvou kroků. Viditelný chat tak může zůstat lidský, zatímco technická vrstva stále používá auditovatelný admin workflow.

System prompt pro stránku nastavení modelu v OpenWebUI je verzovaný v `docs/codex-local-model-system-prompt.md`. Jeho úloha je naučit model mluvit lidsky a nepodsouvat uživateli interní markery; skutečné provedení akcí má stále zajišťovat filter/tool vrstva.

Příklad aplikace konkrétního patche:

    repo: ai-stack
    GATEWAY_ADMIN_APPLY_NOW
    diff --git a/README.md b/README.md
    --- a/README.md
    +++ b/README.md
    @@ ...

Příklad pushnutí povolených změn:

    repo: ai-stack
    GATEWAY_ADMIN_GIT_PUSH main Update ai-stack documentation

Při dlouhých operacích používej helper `codex/bin/owui_chat_turn.py`; ten zapíše instrukci do OpenWebUI chatu hned, založí běžící assistant zprávu a průběžně ji aktualizuje:

    python3 codex/bin/owui_chat_turn.py --model codex-local-plan-qwen14b --prompt-file /tmp/prompt.txt --status-interval 3 --quiet

Pro admin nebo patch operace používej oddělený viditelný a technický prompt. Viditelný prompt je lidský popis práce pro audit chat; technický prompt může obsahovat interní gateway/admin marker a diff, ale do viditelné historie se nezapisuje:

    python3 codex/bin/owui_chat_turn.py --model codex-local-plan-qwen14b --visible-prompt-file /tmp/visible.txt --prompt-file /tmp/technical.txt --status-interval 3 --quiet

Pro běžné mentorování codex-local bez ručního skládání promptů je nad tím ještě malý orchestrátor:

    python3 codex/bin/mentor_codex_local.py action Odysseus-Lite test

Nebo například:

    python3 codex/bin/mentor_codex_local.py scan Odysseus-Lite
    python3 codex/bin/mentor_codex_local.py run ai-stack -- git status --short --branch
    python3 codex/bin/mentor_codex_local.py deploy

`mentor_codex_local.py` vytváří lidský visible prompt do audit chatu a vedle něj technický prompt pro gateway admin workflow, takže Codex může co nejvíc práce offloadnout na codex-local bez ručního psaní interních markerů.

Pro vyšší orchestrace je tam i mód `audit`, který udělá tři audit chat turny za sebou:
1. `workspace scan`
2. `verify --dry-run`
3. shrnutí a návrh jednoho dalšího kroku od codex-local nad předchozí historií

Příklad:

    python3 codex/bin/mentor_codex_local.py audit Odysseus-Lite

Ještě praktičtější je mód `autopilot`, který po `scan -> verify` nechá codex-local vybrat právě jeden další bezpečný capability krok z povolené množiny a může ho rovnou spustit:

    python3 codex/bin/mentor_codex_local.py autopilot Odysseus-Lite

Pouze pro doporučení bez spuštění:

    python3 codex/bin/mentor_codex_local.py autopilot Odysseus-Lite --recommend-only

Pro recommendation-driven mentoring loop je tam i mód `patch-plan`: helper nejdřív vyžádá autopilot recommendation, když dostane `read_command`, sám provede follow-up read přes audit chat a pak nechá codex-local navrhnout minimální patch plan:

    python3 codex/bin/mentor_codex_local.py patch-plan Odysseus-Lite

Ještě o krok dál jde mód `apply-ready`: ten po recommendation a případném read kroku nechá codex-local navrhnout i malý unified diff, ale diff zatím jen vrátí do auditu a nic neaplikuje:

    python3 codex/bin/mentor_codex_local.py apply-ready Odysseus-Lite

OpenWebUI helpery čtou API key nejdřív z `OWUI_API_KEY` a potom z ignorovaného souboru `codex/state/openwebui-api.key` nebo z cesty v `OWUI_API_KEY_FILE`. Preferovaný způsob uložení bez vypsání klíče do shell historie je:

    codex/bin/store_openwebui_api_key.sh

Obecnější varianta pro runtime secrets je:

    codex/bin/store_runtime_secret.sh openwebui-api

Nebo přes stdin z lokálního tajného zdroje; nepiš reálný key jako literál do shell historie:

    codex/bin/store_openwebui_api_key.sh < /path/to/local/openwebui-api.key

Technické markery typu `GATEWAY_ADMIN_APPLY_NOW` jsou interní bezpečnostní protokol pro whitelisted zápis souborů. V běžném OpenWebUI chatu mají být schované za helperem a viditelné jen jako lidské shrnutí práce, status a výsledek.

Gateway podporuje skutečné streaming SSE pro běžné modelové odpovědi: při `stream=true` proxyuje chunkované odpovědi z Ollamy průběžně do OpenWebUI. Admin a patch odpovědi zůstávají pevné, aby se bezpečnostní flow nechovalo nedeterministicky.

### OpenWebUI live refresh workaround

Pokud je otevřený chat měněný externě přes helper nebo admin API, samotný OpenWebUI frontend ho ne vždy hned překreslí. Proto je v `openwebui/loader.js` lehký polling hook, který na stránce `/c/<chatId>` sleduje změnu `history.currentId` a při změně provede synchronizační reload.

Hook se projeví až po recreate služby `open-webui`, protože je přes `docker-compose.yml` mountovaný do obou runtime cest `/app/backend/open_webui/static/loader.js` a `/app/build/static/loader.js`.

Prakticky to znamená:

- stack startuj z Windows přes `C:\Repositories\ai-stack\start_docker.bat`,
- po startu rychle ověř `curl -sS http://127.0.0.1:9090/static/loader.js | wc -c`,
- potom zkontroluj `curl -sS http://127.0.0.1:9101/health` a `curl -sS http://127.0.0.1:9101/v1/workspaces`.

Praktická pravidla pro zadávání úloh:

- Napiš, zda agent smí editovat soubory, nebo má jen analyzovat.
- U editací omez rozsah: například `měň jen README.md` nebo `měň jen codex/gateway/gateway.py`.
- Pro rychlou práci používej `codex-local-plan-qwen14b` a `codex-local-build-qwen14b`; 32B nech pro složitější analýzy.
- Do promptů ani verzovaných souborů nevkládej secrets; OpenWebUI API key ukládej do ignorovaného `codex/state/openwebui-api.key`.
- Před pushem vždy zkontroluj `GATEWAY_ADMIN_GIT_STATUS` a ujisti se, že `blocked_paths` i `sensitive_paths_seen` jsou `(none)`.

## Provozní příkazy

- Start z Windows: `C:\Repositories\ai-stack\start_docker.bat`.
- Ruční start Codex stacku ve WSL: `sudo /mnt/c/Repositories/ai-stack/codex/bin/start_codex_stack.sh`.
- Přidání workspace: `python3 codex/bin/add_workspace.py <name> <path> --port <port>`.
- Kontrola gateway: `curl http://127.0.0.1:9101/health` ve WSL nebo `curl http://192.168.0.48:9101/health` z LAN.
- Smoke test gateway: `python3 codex/bin/codex_gateway_smoke.py --base-url http://192.168.0.48:9101 --workspace ai-stack`.
- Celkový healthcheck lokálního stacku ve WSL: `bash codex/bin/check_ai_stack.sh`; pro LAN kontrolu nastav `OPENWEBUI_URL=http://192.168.0.48:9090 CODEX_GATEWAY_URL=http://192.168.0.48:9101`.
- Spuštění kontrolního příkazu v registrovaném workspace přes gateway: `curl -sS http://127.0.0.1:9101/v1/admin/workspace/run -H "Content-Type: application/json" -d '{"workspace":"ai-stack","timeout":30,"command":["git","status","--short","--branch"]}'`.
- Dry-run synchronizace OpenWebUI funkce z verzovaného zdroje: `python3 codex/bin/sync_openwebui_function.py --dry-run`.
- Aplikace synchronizace OpenWebUI funkce po review: `python3 codex/bin/sync_openwebui_function.py`.
- Uložení GitHub API tokenu pro volitelné zakládání GitHub repozitářů: `codex/bin/store_runtime_secret.sh github-api`.
- Bezpečné mapování OpenWebUI endpointů bez mutačních metod: `OWUI_API_KEY_FILE=codex/state/openwebui-api.key python3 codex/bin/discover_openwebui_endpoints.py --path /api/config --path /api/v1/functions/list`.
- Seznam modelů: `curl http://192.168.0.48:9101/v1/models`.
- Seznam workspaces: `curl http://192.168.0.48:9101/v1/workspaces`.

## Bezpečnostní pravidla

- OpenWebUI nesmí mít připojený `/var/run/docker.sock`, pokud k tomu není explicitní důvod a izolace.
- Workspaces musí být explicitně registrované a izolované.
- Změny `codex/gateway/gateway.py` mají projít Python syntaktickou validací a watcherem.
- Admin patch flow v OpenWebUI smí zapisovat jen whitelisted soubory.
- Síťové helpery mají používat retry/backoff, aby krátké výpadky portproxy nebo restartů nevytvářely falešné chyby.

## Plánované integrace

- Home Assistant: sběr dat z domácích čidel, automatizace a lokální AI poradce nad stavem domácnosti.
- Fio/bankovní data: read-only analýza výdajů s důrazem na tokeny mimo Git a minimální oprávnění.
- Rodinný AI management: znalostní báze, plánování, úkoly a bezpečně oddělené osobní údaje.
- GitHub `Jeycob/Ai-Stack`: verzování důležitých konfigurací a dokumentace bez runtime secrets.

## Git workflow

- Udržuj důležité konfigurace a dokumentaci v tomto repozitáři.
- Runtime stav, audit logy, hesla a privátní klíče nech v `.gitignore`.
- Před pushem zkontroluj `git status` a ujisti se, že se necommitují secrets.
- Před commitem/pushem spusť gateway smoke test a pushuj jen whitelisted zdrojové soubory; `.env`, `codex/state/`, `codex/audit/`, logy ani privátní klíče do commitu nepatří.
- Assistant v OpenWebUI smí navrhovat diffy, ale odpověď modelu sama nesmí zapisovat do repozitáře; zápis má probíhat pouze přes explicitní skrytý helper/admin payload a whitelisted filtr.
- Cílový vzdálený repozitář: `github.com/Jeycob/Ai-Stack`.
