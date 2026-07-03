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
- `codex/bin/wsl_boot_ai_stack.sh`: volitelný WSL boot wrapper, který po startu WSL zvedne Docker a Codex gateway stack.
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

Stejný runner nově umí i `smoke`, tedy auditované startup ověření. Hodí se pro zadání typu “zkus to rozběhnout a vrať výsledek”, ale bez neomezeného shellu. Nejprve se pokusí najít standardní smoke entrypoint, například `package.json` script `smoke`/`dev`/`start`, Django `manage.py runserver`, nebo zjevný FastAPI/Flask app entrypoint. Proces pustí jen v krátkém okně přes `timeout`, sleduje readiness logy typu `http://127.0.0.1`, `ready on` nebo `Uvicorn running on`, a úspěch vrátí jen když startup opravdu detekoval. Příklad:

    repo: Test2
    GATEWAY_ADMIN_WORKSPACE_ACTION Test2 smoke --timeout 900

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

`Codex Auto Tools Filter` navíc umí pro modely `codex-local-*` rozpoznat přirozené požadavky a mapovat je na několik širších capability workflow. Například “pullni ai-stack a nasaď” přepíše interně na `GATEWAY_ADMIN_DEPLOY_STACK`; “ukaž deploy status/log” přepíše na `GATEWAY_ADMIN_DEPLOY_STATUS`; preflight formulace typu “je to ready na push?”, “co blokuje push?” nebo “zkontroluj push readiness” přepíše na auditovaný `GATEWAY_ADMIN_GIT_STATUS`; publish-plan formulace typu “navrhni publish plan”, “jak publikovat release”, “co mám dělat před releasem?” nebo “jaký je další release krok?” přepíše na helper `mentor_codex_local.py publish-plan`; release-prep formulace typu “zkontroluj release readiness”, “připrav release” nebo “co blokuje release” přepíše na helper `mentor_codex_local.py release-prep`; jednoduché publish zadání typu “pushni změny do GitHubu”, “commitni a pushni” nebo `message: ...` přepíše na auditovaný `GATEWAY_ADMIN_GIT_PUSH`; bootstrap požadavky typu “vytvoř nové repository Test2 a vygeneruj ssh klíč”, “založ projekt Test2 na GitHubu” nebo “připrav workspace Test2 s deploy key” přepíše na `GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2 --restart` případně s `--github`; pokud bootstrap prompt rovnou obsahuje i follow-through cíl typu “doinstaluj co chybí”, “napiš základ aplikace”, “rozběhni to” nebo “spusť testy”, přepíše se už na širší mentor workflow `mentor_codex_local.py bootstrap-improve`, které nejdřív repo založí a zaregistruje a potom pokračuje přes auditovaný improve flow nad novým workspace; běžné repo kontroly jako git status/remote/log nebo explicitní “spusť příkaz:” v registrovaném workspace přepíše na `GATEWAY_ADMIN_RUN_WORKSPACE`; běžné developerské akce jako install/test/build/lint/verify/smoke přepíše na `GATEWAY_ADMIN_WORKSPACE_ACTION`; širší požadavky typu “ověř a pokračuj sám”, “udělej co je potřeba”, “dotáhni to” nebo “navrhni další krok” přepíše na `GATEWAY_ADMIN_WORKSPACE_AUTOPILOT`, nově typicky se třemi kroky a se sadou `install,verify,smoke,test,build,lint`. Viditelný chat tak může zůstat lidský, zatímco technická vrstva stále používá auditovatelný admin workflow.

Novější výchozí chování je o něco samostatnější: autopilot a mentor helpery už standardně počítají i s `verify` a `smoke`, ne jen s `install/test/build/lint`. U širších zadání typu “rozběhni to a dotáhni co půjde” tak codex-local nemusí zbytečně končit po prvním read-only shrnutí, ale může bezpečně zkusit ověřovací a startup krok ještě před tím, než sáhne po patch workflow.

Další drobné rozšíření autonomie je v `apply-safe` vrstvě: safe auto-apply už není omezený jen na úplně miniaturní patche ve třech souborech. Nově může auditovaně vzít až pět souborů, víc hunků a o něco větší diff, pokud pořád zůstává v bezpečném scope `docs/`, `codex/*.json|*.md`, `codex/bin/*.py|*.sh`, `codex/gateway/*.py`, `openwebui/*.js|*.css` a vybraných root configů. Smysl je jednoduchý: méně zbytečných stopů kvůli příliš úzkému whitelistu, ale pořád bez zásahu do runtime state, secrets nebo generovaných dat.

Širší GitHub/release use-cases typu “vytvoř release”, “publish package” nebo “rozjeď GitHub Actions release” se naopak nepředstírají jako obyčejný push. Filter je přeloží na mentor `boundary` vysvětlení s konkrétním capability hintem, aby bylo vidět, že jednoduchý `push-check` i samotný push už umíme, ale release automation je pořád samostatná capability hranice.

Stejnou logiku teď zná i `mentor_codex_local.py`: `delegate`, `profile`, `report`, `plan` i `next-helper` rozlišují mezi `release-prep`, `push-check`, `push` a `release boundary`, takže codex-local nepůsobí nekonzistentně mezi helper vrstvou a auto-tools routováním.

`release-prep` je malý read-only orchestration helper: nejdřív udělá repo guard, workspace scan, zkontroluje `git remote -v` a `git log -5 --oneline`, a teprve pak nechá codex-local sepsat stručný release readiness verdict. Tím dostaneš mezivrstvu mezi obyčejným pushem a plným GitHub/release capability rozšířením.

`publish-plan` jde ještě o krok dál: znovu použije `release-prep` důkazy a pak nechá codex-local vrátit krátkou sekvenci 2-4 auditovaných publish kroků plus explicitní `BOUNDARY`, pokud poslední krok už vyžaduje vzdálenou release capability.

Když je v promptu víc tasků najednou, filter nově umí i lehký multi-task routing. Požadavky typu “seřaď mi tyhle body”, “udělej z toho backlog”, “co je první” nebo “vyber další krok z těchto úkolů” přepíše na helper běžící přes `GATEWAY_ADMIN_RUN_WORKSPACE`, který zavolá `mentor_codex_local.py backlog` nebo `mentor_codex_local.py dispatch`. Díky tomu si codex-local umí samo srovnat pořadí práce a v jednodušších případech rovnou vybrat a spustit nejlepší další mentor workflow.

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

Helper teď navíc používá stabilní turn key odvozený z `chat_id + model + visible prompt + technical prompt`. Když tedy retryneš stejný běžící turn, pokusí se najít už existující nedokončenou assistant zprávu a znovu ji použít místo vytváření dalšího duplicitního user promptu a dalšího `running...` statusu. Výsledek: méně spamu v audit chatu a menší riziko zbytečných opakovaných volání.

Pro skutečný chat-level E2E smoke nad audit chatem je nad tím ještě `codex/bin/owui_chat_smoke.py`. Ten obalí `owui_chat_turn.py`, pošle jeden reálný turn do OpenWebUI, znovu načte chat a zkontroluje, že se ve viditelné historii opravdu objevil user prompt i dokončená assistant odpověď pro stejný `turn_key`. To je nejbližší opakovatelný smoke helper k use-casu “otestuj to jako user přes chat okno”:

    python3 codex/bin/owui_chat_smoke.py \
      --model codex-local-plan-qwen14b \
      --visible-prompt "repo: ai-stack\nOdpovez jednim slovem: smoke-ok" \
      --prompt "repo: ai-stack\nOdpovez jednim slovem: smoke-ok" \
      --expected-substring smoke

Nad tím je teď ještě lehký scénářový runner `codex/bin/owui_chat_scenarios.py`. Ten už neposílá interní marker nebo technický admin prompt, ale běžný user-like audit chat prompt, takže ověřuje i přirozený routing přes `Codex Auto Tools Filter`. Hodí se pro levné E2E ověření, že codex-local pořád zvládá základní agentické flow přes samotný OpenWebUI chat:

    python3 codex/bin/owui_chat_scenarios.py --list
    python3 codex/bin/owui_chat_scenarios.py --dry-run --scenario git-status --scenario next-step
    python3 codex/bin/owui_chat_scenarios.py --dry-run --scenario workflow-profile-improve --scenario mentor-brief-bootstrap
    OWUI_API_KEY=... python3 codex/bin/owui_chat_scenarios.py --scenario all --json

Výchozí scénáře dnes pokrývají:
- `git-status`: přirozené “zkontroluj git status”
- `verify-project`: přirozené “ověř projekt”
- `push-readiness`: přirozené “je to ready na push?”
- `deploy-status`: přirozené “ukaž deploy status”
- `next-step`: přirozené “navrhni další krok”

To je záměrně levnější než plný browser E2E. Neověřuje vzhled UI, ale přímo to, že běžná lidská formulace v audit chatu projde route -> filter -> gateway -> capability -> zpět do viditelné odpovědi.

Stejnou věc jde teď spouštět i přes hlavní mentor helper, aby scénářový smoke nebyl další izolovaný nástroj bokem:

    python3 codex/bin/mentor_codex_local.py chat-scenarios ai-stack --list
    python3 codex/bin/mentor_codex_local.py chat-scenarios ai-stack --dry-run --scenario verify-project --scenario next-step

Pro rychlou kombinovanou kontrolu celé mentoring vrstvy je tam nově i:

    python3 codex/bin/mentor_codex_local.py self-check ai-stack "Navrhni dalsi krok a dotahni co pujde."

`self-check` skládá tři vrstvy do jednoho reportu:
- `mentor_scenario_runner.py` pro levný helper-orchestration smoke,
- helper-only `bootstrap-probe` pro ověření bootstrap/create-repo reasoning bez mutací,
- `chat-scenarios` pro user-like OpenWebUI audit chat flow, včetně širší autonomy/profile vrstvy,
- `check_ai_stack.sh` pro stack summary.

Když chybí OpenWebUI API key, chat scénáře se v `self-check` automaticky přepnou do `dry-run` režimu a report je označí jako `degraded` místo falešného tvrdého failu. Díky tomu jde self-check pouštět i v lokálním klonu, kde zrovna nemáš k dispozici celý běžící stack.

Když naopak chceš opravdu plný živý důkaz přes OpenWebUI chat, použij:

    python3 codex/bin/mentor_codex_local.py self-check ai-stack "Navrhni dalsi krok a dotahni co pujde." --strict-live

`--strict-live` nedovolí fallback do `dry-run`. Pokud není dostupný OpenWebUI API key, skončí hned s jasným blockerem místo “degraded” režimu.

`self-check` nově standardně obsahuje i `bootstrap-probe`: helper-only scénář nad zadáním typu “vytvoř nové repository Test2 jako React appku, doinstaluj co chybí a zkus to rozběhnout.” Tím průběžně ověřujeme, že mentor vrstva pořád umí rozpoznat a rozplánovat bootstrap-oriented use-case, aniž by během běžného self-checku opravdu zakládala nové repozitáře. Chování lze upravit přes:

    python3 codex/bin/mentor_codex_local.py self-check ai-stack \
      --bootstrap-task "Vytvor nove repository Test3 jako FastAPI appku a navrhni dalsi kroky."

    python3 codex/bin/mentor_codex_local.py self-check ai-stack --skip-bootstrap-probe

`codex/bin/check_ai_stack.sh` to teď umí použít i automaticky. Když je dostupný OpenWebUI API key přes `OWUI_API_KEY` nebo ignorovaný `codex/state/openwebui-api.key`, healthcheck po gateway smoke přidá i audit-chat smoke. Pokud key chybí, krok se jen korektně přeskočí. Vypnout ho jde přes `SKIP_OWUI_CHAT_SMOKE=1`.

Stejný healthcheck teď umí po základním audit-chat smoke spustit i lehké user-like scénáře přes `owui_chat_scenarios.py`. Výchozí sada je záměrně levná (`git-status,next-step`), aby šlo rychle ověřit, že pořád funguje i přirozený route přes filter a capability vrstvu, ne jen úzký technický smoke. Chování jde řídit přes:
- `OWUI_CHAT_SCENARIOS=git-status,deploy-status,next-step`
- `SKIP_OWUI_CHAT_SCENARIOS=1`

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

Ještě praktičtější je mód `apply-safe`: ten po recommendation a read kroku nechá codex-local navrhnout malý diff, helper ho lokálně zkontroluje proti bezpečnému scope a když projde, rovnou ho pošle přes `GATEWAY_ADMIN_APPLY_NOW`. Tím se codex-local neposouvá k neomezenému shellu, ale k širší a samostatnější řízené editaci:

    python3 codex/bin/mentor_codex_local.py apply-safe ai-stack

Pro nejbližší chování typu “dělej to jako Codex a dotáhni co zvládneš” je tam nový mód `improve`: nejdřív nechá doběhnout capability vrstvu (`install/test/build/lint` podle projektu), a teprve když už další krok není čistě spustitelný, přepne se do recommendation -> read -> patch-plan -> safe apply workflow:

    python3 codex/bin/mentor_codex_local.py improve ai-stack

Když nechceš ručně přemýšlet, který mód zvolit, použij `delegate`: helper podle textu úkolu sám vybere vhodnou orchestrace vrstvu (`audit`, `autopilot`, `apply-safe`, `improve`, případně `run`) a tu pak spustí přes audit chat:

    python3 codex/bin/mentor_codex_local.py delegate ai-stack "Fixni to a dotáhni co zvládneš."

Když chceš jen rychle zjistit, jakou šířku pravomocí by helper pro úkol zvolil, použij `profile`. Vrátí `runtime_profile`, vybraný workflow a důvod, ale nic nespouští:

    python3 codex/bin/mentor_codex_local.py profile ai-stack "Uprav README a aplikuj malý patch"

`profile` a `delegate` navíc nově vrací i `confidence`, `guardrail_summary` a `missing_capability_hint`, takže je hned vidět, proč helper zůstal v review scope, kdy mu stačí capability runner, kdy už je rozumné přejít do safe patch nebo širšího `improve` flow, a jaká přesná capability vrstva ještě chybí, pokud je úkol širší než současné guardraily. Zároveň už helper není zbytečně úzký u přímých capability úkolů: požadavky typu `spusť testy`, `nainstaluj závislosti`, `ověř projekt`, `vytvoř repository Test2` nebo `pullni ai-stack a nasaď` umí klasifikovat rovnou na auditované workflow `action`, `create-repo` nebo `deploy`, místo aby končil jen obecným `audit`.

Ještě důležitější posun je, že širší lidské zadání už nemusí padat rovnou do `autopilot`. Formulace typu `repo: ai-stack` + `Fixni to a dotáhni co zvládneš.`, `Udělej co je potřeba.`, `Proveď to jako Codex.` nebo `Vyber workflow a proveď.` teď filter přeloží na `mentor_codex_local.py delegate`. Teprve ten pak vybere správný runtime workflow, takže broad orchestrace jde přes mentora, ne přes jeden natvrdo zvolený capability krok.

Stejný princip nově platí i pro repo bootstrap. Jednoduché “vytvoř repository Test2” pořád jde nejkratší cestou přes `create-repo`, ale širší zadání typu “vytvoř repository Test2, doinstaluj co chybí, napiš základ a zkus to rozběhnout” už neskončí jen jedním bootstrap markerem. Filter ho pošle do `bootstrap-improve`, který udělá sekvenci `create-repo -> bootstrap-dispatch -> improve` a tím dává agentovi víc samostatnosti bez toho, aby musel dostat neomezený shell.

To samé platí i pro přirozenější wording. Router dnes chytá i formulace jako `udělej maximum`, `vezmi si to celé`, `postarej se o to sám`, `připrav starter`, `napiš základ appky` nebo `pokračuj sám` a podle kontextu je pošle buď do `delegate`, nebo rovnou do `bootstrap-improve` / `workspace-autopilot`. Díky tomu model nemusí být tak závislý na přesných frázích nebo ručních interních markerech.

Bootstrap-improve navíc nově nese i lehký `solution_profile` a `starter_hint`. Když tedy zadání zní třeba “vytvoř repository Test2 ve FastAPI”, “založ React appku” nebo “udělej Three.js starter”, mentor si tenhle stackový záměr uloží do execution briefu a posune ho dál do improve flow. Není to ještě plný framework-specific scaffolder, ale agent už díky tomu nepokračuje naslepo bez technologického směru.

K tomu se teď přidává i `public_stack` a `public_stack_rationale`: mentor pro běžné stacky rovnou doporučí osvědčené veřejné balíčky a tooling, třeba `fastapi, uvicorn, pydantic-settings, pytest, httpx` pro FastAPI nebo `vite, react, typescript, vitest, @testing-library/react` pro React. Cíl je jednoduchý: codex-local má víc reuseovat etablované moduly a méně vyrábět vlastní boilerplate.

Poslední vrstva jsou `scaffold_recipe`, `scaffold_files` a `scaffold_loop`. Tady už mentor pro známé stacky neposílá jen obecné doporučení, ale i konkrétní první bootstrap recipe, klíčové soubory a doporučený ověřovací sled. Například pro React dnes vrátí audited scaffold token `codex_scaffold_react_app`, očekávané soubory `package.json, src/main.tsx, src/App.tsx, src/App.test.tsx, vite.config.ts, tsconfig.json` a loop `install -> test or lint -> dev server smoke -> build`.

Nad tím je teď i samostatný helper `scaffold-plan`, který z bootstrap-oriented zadání vytáhne právě tenhle konkrétní starter plán bez spuštění jakékoli akce. Je to levný mezikrok mezi čistým profilem a plným `bootstrap-improve`: nejdřív si můžeš nechat vypsat scaffold recipe, očekávané soubory a verifikační sled, a teprve potom pustit samotný bootstrap nebo improve workflow.

Další vrstva je `bootstrap-dispatch`. Ten už z téhož zadání vezme konkrétní scaffold recipe, přeloží ho do spustitelného guardrailed runneru přes `run_check.py` a umí vrátit nebo rovnou provést první bootstrap command v nově založeném workspace. Prakticky to znamená, že codex-local už nemusí po bootstrapu znovu “hádat”, co je první rozumný instalační nebo scaffold krok; dostane ho jako explicitní capability.

Nově navíc `bootstrap-dispatch` nekončí jen u prvního scaffold commandu. Ze `scaffold_loop` si odvodí další kandidátní capability kroky (`install`, `smoke`, `verify`, `build`, `test`, `lint`), zohlední co už pokryl samotný recipe, a po úspěšném bootstrapu zkusí najít krátkou podporovanou posloupnost dalších workspace akcí přes existující `workspace_action.py`. Tím se další kroky opírají o reuse už hotových capability resolverů místo nové bespoke logiky. Sekvence zůstává krátká a guardrailed; při prvním failu se zastaví.

Vedle samotného provedení má teď `bootstrap-dispatch --execute` i uzavřený outcome summary. Do výstupu zapisuje `BOOTSTRAP_DISPATCH_FINAL_STATUS`, `BOOTSTRAP_DISPATCH_EXECUTED_SEQUENCE`, `BOOTSTRAP_DISPATCH_SUCCESSFUL_ACTIONS`, `BOOTSTRAP_DISPATCH_FAILED_ACTIONS`, `BOOTSTRAP_DISPATCH_STOP_REASON` a `BOOTSTRAP_DISPATCH_NEXT_RECOMMENDATION`. Díky tomu už další helper nebo improve flow nemusí jen hádat, co se v bootstrap sekvenci povedlo, ale dostane stručný auditovaný handoff.

Na to teď navazuje i `improve`. Když dostane mentor context z bootstrap handoffu, umí si podle něj přeladit `allow-actions` pro autopilot: po `followup_completed` nezkouší znovu úspěšné kroky a soustředí se na další nejbližší bezpečný posun, zatímco po `followup_partial` začne od selhané capability akce a teprve potom pokračuje dál. Tím se improve fáze chová víc jako skutečný agentický follow-up a méně jako statická šablona.

Stejnou sadu capability akcí teď důsledně používá i runtime gateway i mentor helpery: `install, verify, smoke, test, build, lint`. Není to už tak, že by `verify` nebo `smoke` byly jen doporučené v promptu, ale chyběly v nižší vrstvě. Praktický dopad je širší autonomie bez ručního rozšiřování whitelistu pro každou běžnou ověřovací akci zvlášť.

Zároveň už `bootstrap-dispatch` nerozbíhá nesmyslné pseudo-commandy. Pokud je `scaffold_recipe` jen popisný plán a ne skutečně spustitelný shell command, helper to označí jako `SCAFFOLD_RECIPE_MODE=manual` a vrátí jasný blocker s dalším krokem místo toho, aby se snažil pustit text typu “use CMake scaffold plus glfw...”.

První takhle dříve blokované profily už jsme rovnou posunuli do reálné capability vrstvy: `opengl-native`, `fastapi-service`, `node-service`, `react-app`, `threejs-app` a nově i `electron-app` mají dedikované audited scaffoldery v `codex/bin/`. `bootstrap-dispatch` je umí přeložit do skutečného guardrailed runneru přes `run_check.py`, takže tyhle startery už nejsou jen roadmap poznámka, ale opravdový první bootstrap krok.

Pro 3D use-case je důležité hlavně to, že `threejs-app` už není jen kombinace “vite + npm install three”, ale skutečný audited starter `codex/bin/scaffold_threejs_app.py`. Ten vytvoří malou Three.js scénu, `vite.config.ts`, TypeScript config a smoke-friendly `dev`/`smoke` skripty, takže codex-local může na zadání typu “udělej mi 3D web appku” navázat konkrétním bootstrap krokem místo obecného doporučení.

Stejným způsobem je teď pokrytý i `electron-app`. `codex/bin/scaffold_electron_app.py` drží Electron tenký a používá veřejný stack `electron + vite + typescript` místo nové vlastní infrastruktury. Starter vytvoří `main.js`, `preload.js`, renderer entry, `vite.config.ts` a jednoduchý audited smoke skript, takže i desktop use-case má konkrétní guardrailed bootstrap cestu.

Stejným směrem je teď posunutý i `fastapi-service`. Místo pouhého `pip install ...` recipe má vlastní scaffolder `codex/bin/scaffold_fastapi_service.py`, který vytvoří minimální kostru `app/main.py`, `app/config.py`, `tests/test_health.py`, `requirements.txt` a doplní provozní poznámky do README. Díky tomu bootstrap neznamená jen nainstalované balíčky, ale i skutečný běžitelný starter, na který už může navázat `smoke`, `verify` nebo `pytest`.

Stejný upgrade teď dostal i `node-service`: nový `codex/bin/scaffold_node_service.py` vytvoří `package.json` se skripty `build`, `test`, `smoke`, plus `src/app.ts`, `src/index.ts`, `tests/health.test.ts` a `tsconfig.json`. To je důležité hlavně pro auditované follow-up kroky, protože `workspace_action.py` pak už nad takovým starterem umí přirozeně použít `npm install`, `npm test`, `npm run build` i `npm run smoke`.

Kvůli tokenům má mentor nově i lehčí `compact execution brief` větev. Používá se tam, kde jde hlavně o orchestration handoff mezi helpery a není potřeba znovu posílat celý reasoning blok. Zachovává jen minimum: `workspace`, `workflow`, případně `solution_profile`, `public_stack`, `scaffold_recipe`, `scaffold_loop`, `next_scope_hint` a `next_helper`.

Stabilní capability ID a jejich stručný roadmap popis jsou verzované v `docs/codex-local-capability-roadmap.json`. Helper je používá pro `capability_id`, `capability_scope` a `capability_summary`, takže další rozšiřování už nemusí být jen volný text v promptu.

`Codex Auto Tools Filter` teď navíc umí pro některé přirozené požadavky, které jsou širší než dnešní safe runtime scope, propsat do audit stopy i capability-roadmap doporučení. Typicky u GitHub/release nebo host-runtime úkolů zapíše `CAPABILITY_ROADMAP_ID`, `CAPABILITY_ROADMAP_SCOPE` a `CAPABILITY_ROADMAP_SUMMARY`, místo aby jen mlčky selhal nebo přehnaně rozšířil runtime.

Pro levné mentoring rozhodnutí nad jedním úkolem je tam i `report`: vrátí workflow, runtime profile, capability metadata, guardrail summary, missing capability hint, doporučený další helper command a rovnou i návrh viditelného audit chat promptu:

    python3 codex/bin/mentor_codex_local.py report ai-stack "Fixni to a dotáhni co zvládneš."

Když chceš ještě o krok praktičtější dohled nad delším úkolem, použij `plan`: vrátí krátký sequenced mentor plán 2–4 kroků nad daným taskem, typicky kombinaci `report`, `audit`, `improve`, `deploy-status` nebo capability review:

    python3 codex/bin/mentor_codex_local.py plan ai-stack "Fixni to a dotáhni co zvládneš."

Když naopak potřebuješ co nejlevnější mentor kontext pro další modelový krok, použij `brief`: vrátí malý execution brief se zvoleným workflow, guardraily, next helperem a jednou krátkou formulací cíle. To je vhodné jako úsporná vrstva mezi plannerem a samotnou exekucí v OpenWebUI:

    python3 codex/bin/mentor_codex_local.py brief ai-stack "Fixni to a dotáhni co zvládneš."

`delegate` teď tenhle brief nejen vypíše, ale při orchestrace dalšího workflow ho automaticky přilepí i do dalších promptů jako `Mentor brief:` ve visible vrstvě a `MENTOR_EXECUTION_BRIEF` v technické vrstvě. Díky tomu se vybraný task neztratí mezi helper módy a codex-local dostane v každém dalším kroku malý, stabilní mentor payload místo toho, aby se spoléhal jen na volnou historii chatu.

Stejný brief už jde nově vyžádat i přirozeně přes OpenWebUI chat. Požadavky typu `repo: ai-stack` + `Dej mi krátký mentor brief pro: Fixni to a dotáhni co zvládneš.` nebo `Jaký brief má dostat model pro tenhle task...` filter přeloží na `mentor_codex_local.py brief` přes auditovaný `GATEWAY_ADMIN_RUN_WORKSPACE`.

Když chceš ještě menší odpověď a jde ti jen o další praktický krok, použij `next-helper`: vrátí pouze nejlepší další helper command, důvod a malý execution brief.

    python3 codex/bin/mentor_codex_local.py next-helper ai-stack "Fixni to a dotáhni co zvládneš."

Pro levné lokální “as-if user” ověření celé mentoring vrstvy je tam i `mentor_scenario_runner.py`. Ten nevolá živý OpenWebUI chat, ale řetězí helpery jako `profile -> brief -> next-helper -> plan` a podle workflow přidá ještě vhodný krok jako `bootstrap-dispatch` nebo `delegate --dry-run`. Výsledkem je kompaktní scénářový report nad helper orchestration vrstvou:

    python3 codex/bin/mentor_scenario_runner.py ai-stack "Vytvoř nové repository Test2 jako React appku, doinstaluj co chybí a zkus to rozběhnout."

Stejný runner teď umí i multi-task autonomii. Když dostane víc úkolů přes opakované `--task`, `--task-file` nebo stdin, nehraje si už jen na single-step mentora, ale ověří i lehký scheduler flow `backlog -> top -> dispatch --recommend-only`:

    python3 codex/bin/mentor_scenario_runner.py ai-stack \
      --task "Fixni to a dotáhni co zvládneš." \
      --task "Uprav README a aplikuj malý patch" \
      --task "Vytvoř release a pushni to na GitHub"

Je to levná E2E vrstva pro lokální validaci mentora. Živý důkaz proti skutečnému běžícímu codex-local stacku pořád zůstává `owui_chat_turn.py`, gateway smoke a OpenWebUI audit chat.

Když naopak potřebuješ vysvětlit, proč helper nezvolil širší akci a co ho brzdí, použij `boundary`: vrátí guardrail summary, capability scope, missing capability hint a další doporučený helper krok.

    python3 codex/bin/mentor_codex_local.py boundary ai-stack "Vytvoř release a pushni to na GitHub"

Když už máš víc úkolů najednou a chceš, aby si helper sám srovnal pořadí a šířku pravomocí, použij `backlog`. Nad každým taskem udělá stejnou klasifikaci jako `profile/report/plan`, ale vrátí prioritizovanou frontu s `NEXT_HELPER`, `PLAN_CMD` a připraveným audit chat promptem:

    python3 codex/bin/mentor_codex_local.py backlog ai-stack \
      --task "Fixni to a dotáhni co zvládneš." \
      --task "Uprav README a aplikuj malý patch" \
      --task "Vytvoř release a pushni to na GitHub"

Stejný backlog můžeš helperu poslat i přes stdin nebo soubor po řádcích:

    printf '%s\n' \
      "Ověř projekt a pokračuj sám." \
      "Uprav README a aplikuj malý patch" \
      "Nainstaluj systémový balík a restartuj service" \
      | python3 codex/bin/mentor_codex_local.py backlog ai-stack

Když nechceš jen backlog, ale rovnou “vezmi nejlepší další úkol a spusť správný mentor flow”, použij `dispatch`. Ten nejdřív vypíše backlog, potom vybere top položku a předá ji do `delegate`. S `--recommend-only` skončí jen doporučením bez exekuce:

    python3 codex/bin/mentor_codex_local.py dispatch ai-stack \
      --tasks "Fixni to a dotáhni co zvládneš." \
      --tasks "Uprav README a aplikuj malý patch" \
      --tasks "Vytvoř release a pushni to na GitHub" \
      --recommend-only

Když chceš ještě levnější variantu jen pro “co je první a proč”, použij `top`: vrátí pouze top task, důvod, next helper a execution brief bez celého backlog dumpu:

    python3 codex/bin/mentor_codex_local.py top ai-stack \
      --tasks "Fixni to a dotáhni co zvládneš." \
      --tasks "Uprav README a aplikuj malý patch" \
      --tasks "Vytvoř release a pushni to na GitHub"

I tohle už jde přirozeně z OpenWebUI chatu: když uživatel napíše více bodů a otázku typu `Co má dělat jako první?`, `Který úkol je první?`, `Jaký je top task?` nebo `Proč je to první?`, filter to přeloží na lehký `mentor_codex_local.py top`. Když místo toho napíše `Jen doporuč první krok bez spuštění`, stále se použije `dispatch --recommend-only`.

Podobně i pro single-task otázky typu `Jaký helper mám spustit dál pro ...?`, `Co mám pustit dál pro ...?`, `Co opravit jako první pro ...?`, `Jaký je další safe patch krok pro ...?` nebo `next helper for ...` filter nově routuje na `mentor_codex_local.py next-helper`.

U multi-task bug/patch prioritizace se route také rozšířil za čistě obecné “top task” fráze. Formulace jako `Který bug má nejvyšší prioritu?`, `Jaký bug je první?`, `Vyber další safe patch krok` nebo `Seřaď bugy podle priority` teď padají do stejných lehkých mentor vrstev `top`, `dispatch` nebo `backlog`, místo aby končily v neurčitém auditu.

Stejně tak už jde přirozeně vyžádat i guardrail vysvětlení: formulace typu `Proč to nejde pro ...?`, `Jaké guardraily platí pro ...?`, `Jaká capability chybí pro ...?` nebo `Why can't it do this for ...?` filter přeloží na `mentor_codex_local.py boundary`.

Podobně už jde přirozeně routovat i levná mentor analytika nad jedním taskem:

- `Udělej code review`, `Review kódu` nebo `Najdi rizika` -> `mentor_codex_local.py review`
- `Jaký workflow bys zvolil pro ...?` nebo `Jaký runtime profile bys zvolil pro ...?` -> `mentor_codex_local.py profile`
- `Udělej mentor report pro ...` nebo `Shrň workflow pro ...` -> `mentor_codex_local.py report`
- `Připrav krátký plán pro ...` nebo `Jaký plán bys zvolil pro ...?` -> `mentor_codex_local.py plan`
- `Najdi bug a navrhni opravu`, `Fix plan` nebo `Plán opravy` -> také `mentor_codex_local.py plan`, ale jako levný bridge z review do opravy

Tohle je důležitý směr celé autonomie: méně jednorázových whitelist markerů a víc širších auditovaných capability scope. V praxi to znamená, že codex-local má být samostatnější hlavně u standardních workflow, která už máme pojmenovaná a ohraničená, ne přes neomezený shell.

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
- Volitelný autostart při každém startu WSL: v `/etc/wsl.conf` nastav `[boot] command = /mnt/c/Repositories/ai-stack/codex/bin/wsl_boot_ai_stack.sh --background`. Skript loguje do `codex/audit/wsl-boot-ai-stack.log`.
- Přidání workspace: `python3 codex/bin/add_workspace.py <name> <path> --port <port>`.
- Kontrola gateway: `curl http://127.0.0.1:9101/health` ve WSL nebo `curl http://192.168.0.48:9101/health` z LAN.
- Smoke test gateway: `python3 codex/bin/codex_gateway_smoke.py --base-url http://192.168.0.48:9101 --workspace ai-stack`.
- Celkový healthcheck lokálního stacku ve WSL: `bash codex/bin/check_ai_stack.sh`; pro LAN kontrolu nastav `OPENWEBUI_URL=http://192.168.0.48:9090 CODEX_GATEWAY_URL=http://192.168.0.48:9101`. Pokud je dostupný OpenWebUI API key, skript nově zahrne i audit-chat smoke přes `owui_chat_smoke.py`.
- `GATEWAY_ADMIN_CHECK_STACK` už v OpenWebUI nevrací celý syrový healthcheck log. Gateway filter pouští `check_ai_stack.sh` v `CHECK_AI_STACK_SUMMARY_ONLY=1` režimu a vrací stručný verdict se souhrnem checků. Dlouhé admin výstupy jako deploy tail, workspace output nebo smoke logy se nově vrací jako kompaktní preview bloky místo doslovného HTML `<details>`, protože tenhle renderer je v běžném OpenWebUI chatu nevykresloval spolehlivě.
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
