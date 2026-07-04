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
- `codex/bin/openwebui_codex_auto_tools_filter.py`: OpenWebUI filter pro automatické připojení toolsetů a normalizaci bezpečných codex-local intentů z běžného jazyka.
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

- `codex-local`: výchozí persistentní alias pro běžný agent loop.
- `codex-local-heavy`: volitelný heavy alias pro explicitní deep/heavy eskalaci.
- `codex-local-planner-exp`: experimentální planner alias; když není nastavený experimentální planner model, spadne zpět na default.
- Starší aliasy `codex-local-plan-qwen14b`, `codex-local-build-qwen14b`, `codex-local-plan-qwen32b` a `codex-local-build-qwen32b` zůstávají jen kvůli kompatibilitě, ale nová logika už nebere planner/build jako důvod ke změně modelu.

Výchozí runtime politika je:

- `CODEX_LOCAL_DEFAULT_MODEL=qwen2.5-coder:14b` nebo `Qwen3-14B` varianta, pokud ji máš lokálně připravenou.
- `CODEX_LOCAL_HEAVY_MODEL=qwen2.5-coder:32b` nebo ručně zvolený `Qwen3-Coder-30B-A3B`.
- `CODEX_LOCAL_MODEL_MODE=single`
- `CODEX_LOCAL_ALLOW_HEAVY_ESCALATION=false`
- `CODEX_LOCAL_STRUCTURED_OUTPUT=auto`
- `CODEX_LOCAL_STRUCTURED_BACKEND=auto`
- `CODEX_LOCAL_STRUCTURED_ATTEMPT_TIMEOUT=8`
- `CODEX_LOCAL_EXPERIMENTAL_PLANNER_MODEL=` ponechat prázdné, pokud výslovně netestuješ planner experiment.

Na RTX 4080 16 GB / 64 GB RAM je 14B praktická defaultní volba, protože drží nižší latenci i VRAM churn. Heavy model má být ruční deep mode, ne něco, co se samo přepíná každé dva turny.

## Příklady práce s codex-local agentem

Primární způsob práce je přes viditelný OpenWebUI audit chat. Instrukce mají být konkrétní, ideálně s prvním řádkem `repo: <workspace>`, aby gateway věděla, nad kterým repozitářem má agent pracovat.
Agent rozumí i běžným aliasům jako `repository:`, `repozitář:`, `repozitar:`, `projekt:` nebo `workspace:`. Pro soubory můžeš použít `soubor:`, `file:`, `path:` nebo `cesta:`.

Příklad rychlé analýzy bez editace:

    repo: ai-stack
    Prohlédni strukturu projektu a stručně řekni, jak je zapojená gateway. Nic needituj.

Příklad čtení a vysvětlení konkrétního souboru bez ručního admin markeru:

    repozitar: ai-stack
    soubor: docker-compose.yml
    Přečti docker compose a vysvětli, co dělá řádek po řádku.

Auto-tools filter to přeloží na auditované vysvětlení souboru přes gateway. Gateway soubor načte přímo z registrovaného workspace, blokuje runtime/secrets cesty jako `.env`, `codex/state/` a `codex/audit/`, a lokální model pak odpoví z reálného očíslovaného obsahu souboru.

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
    GATEWAY_ADMIN_CREATE_LOCAL_REPO Test2

Tento workflow vytvoří lokální repo pod `/mnt/c/Repositories`, inicializuje Git, přidá README, vygeneruje private key do ignorovaného `codex/state/ssh/`, vrátí public key a zaregistruje workspace. Nerestartuje stack a nic nepushuje, pokud o to uživatel výslovně nepožádá. Když je v promptu explicitně “restartni workspace/stack”, přidá se `--restart`; formulace jako “bez GitHubu” nebo “bez restartu” mají přednost před pouhou zmínkou těch slov. Když restart selže na Docker právech, odpověď má být `LOCAL_REPO_CREATE_PARTIAL`, protože repo, klíč a registrace workspace už jsou hotové a další krok je samostatný deploy/restart.

Pokud zadání obsahuje i follow-through cíl typu `initni git`, `vygeneruj ssh klíč`, `připrav GitHub remote`, `doinstaluj co chybí`, `rozběhni to` nebo `pak pushni`, mentor už to nemá chápat jako úzký one-shot create-repo krok, ale jako širší `bootstrap-improve` workflow. Prakticky to znamená: založ repo a workspace, připrav klíč, pokračuj install/test/smoke kroky a zastav se až na prvním skutečně externím checkpointu.

Typický externí checkpoint je přidání public key do GitHubu bez uloženého GitHub tokenu. V takovém případě má codex-local vrátit přesný `MANUAL_STEP_REQUIRED` styl handoff: kde je public key, co přesně přidat do GitHubu, jak potvrdit pokračování, a teprve po potvrzení zkusit další remote krok jako `push`.

Pokud uživatel výslovně řekne GitHub, například “vytvoř GitHub repository Test2”, auto-tools filter přidá `--github`. Gateway pak použije `GITHUB_TOKEN`, `GITHUB_TOKEN_FILE`, nebo ignorovaný `codex/state/github-api.token`, vytvoří GitHub repo, přidá public key jako write deploy key a nastaví lokálnímu repozitáři `origin`. Bez tokenu vrátí jasný `GITHUB_TOKEN_MISSING`; nebude tvrdit, že GitHub repo vzniklo.

GitHub token ulož lokálně bez vypsání do historie takto:

    codex/bin/store_runtime_secret.sh github-api

Obecnější explicitní akce se neposílají přes nový endpoint pro každou drobnost, ale přes širší workspace runner. Například:

    repo: Test2
    spusť příkaz: git status --short --branch

Auto-tools filter to přeloží na:

    repo: ai-stack
    GATEWAY_ADMIN_RUN_WORKSPACE Test2 --timeout 300 -- git status --short --branch

Workspace runner nyní ve výchozím stavu používá `runner=container`, tedy spouští příkaz přes `docker exec --workdir /workspace codex-opencode-<workspace> ...`. Starší host režim existuje jen jako explicitní diagnostika přes `--runner host`; běžné run/install/test/build/smoke workflow nemá tiše padat zpět na WSL host. Gateway navíc nově vrací tvrdý marker `WORKSPACE_RUN_HOST_REQUIRES_EXPLICIT_CAPABILITY` nebo `WORKSPACE_ACTION_HOST_REQUIRES_EXPLICIT_CAPABILITY`, pokud by se někdo pokusil použít host runner bez explicitního capability/admin povolení. Pokud gateway nemá přístup k Docker socketu, vrátí auditovatelnou chybu místo toho, aby příkaz spustila mimo kontejner.

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
Při `runner=container` resolver nevyžaduje, aby byly nástroje jako `npm`, `go` nebo `cargo` nainstalované ve WSL hostu; rozhoduje podle manifestů a skutečný úspěch ověří až v OpenCode kontejneru.

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

Příklad vysvětlení souboru přes gateway:

    repo: ai-stack
    GATEWAY_ADMIN_EXPLAIN_FILE ai-stack docker-compose.yml 1 400 -- vysvětli co dělá řádek po řádku

Příklad vestavěného smoke testu gateway:

    repo: ai-stack
    GATEWAY_ADMIN_SMOKE ai-stack

Příklad celkového healthchecku z OpenWebUI admin filteru:

    repo: ai-stack
    GATEWAY_ADMIN_CHECK_STACK ai-stack codex-local

Gateway `/health` nově vrací i `runtime_repo_root`, `runtime_commit` a `runtime_fingerprint`. To je důležité hlavně pro helpery a CI: když check běží ze stejného checkoutu jako live runtime, vynucuje ostrý fingerprint match a odhalí skutečný stale proces. Když check běží z jiného klonu stejného commitu, nesmí spadnout falešným `CODEX_LOCAL_RUNTIME_SPLIT_BRAIN`; místo toho se porovnává commit a případný drift vrací samostatný marker `CODEX_LOCAL_RUNTIME_CLONE_DRIFT`.

Příklad reálného spuštění příkazu v registrovaném workspace přes gateway admin endpoint:

    repo: ai-stack
    GATEWAY_ADMIN_RUN_WORKSPACE ai-stack -- git status --short --branch

Příklad příkazu s delším timeoutem:

    repo: ai-stack
    GATEWAY_ADMIN_RUN_WORKSPACE ai-stack --timeout 120 -- python3 -m py_compile codex/gateway/gateway.py

Příklad capability-based akce nad workspace:

    repo: ai-stack
    GATEWAY_ADMIN_WORKSPACE_ACTION ai-stack lint --dry-run

Příklad veřejného web dotazu bez ručního markeru:

    kdo ma dneska svatek? stahni mi to z seznam.cz

Auto-tools filter ho přepíše na auditovaný `GATEWAY_ADMIN_WEB_ANSWER https://www.seznam.cz/ -- ...`. Gateway stáhne jen veřejný HTTP/HTTPS zdroj přes omezený GET, blokuje lokální a privátní adresy, nepoužívá cookies ani secrets, z HTML vytáhne čitelný text a lokální model odpoví pouze z načteného zdroje. Pro čisté stažení textu bez otázky existuje `GATEWAY_ADMIN_WEB_FETCH <url>`.

Stejná capability funguje i v přirozenějším slovosledu jako `stahni z seznam.cz kdo ma dneska svatek` nebo `podivej se na https://example.com a stahni mi text`. Filter z takového promptu nejdřív vytáhne veřejnou URL a potom se pokusí očistit samotný dotaz, aby se dál neposílalo celé technické zadání typu `stáhni mi to z ...`, ale už jen věcná otázka.

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

Deploy skript nejdřív provede `git pull --ff-only`, ověří Python soubory a až potom restartuje Codex stack a OpenWebUI. Pokud je runtime checkout špinavý jen kvůli lokálnímu `codex/workspaces.json`, skript ho před pull dočasně stashne a po aktualizaci zase vrátí, takže běžné lokální workspace registrace už neblokují nasazení nového commitu. Po restartu čeká na gateway, OpenWebUI root a `/static/loader.js`, aby krátký náběh služby nevypadal jako chyba. Nově si URL pro OpenWebUI nebere slepě jen z `127.0.0.1:9090`, ale přes sdílený helper `codex/bin/openwebui_runtime.py`, který zkusí explicitní env, `docker-compose.yml` `WEBUI_URL`, odvození z `CODEX_GATEWAY_PUBLIC_URL` a až potom lokální fallbacky. Tím se nerozbíjí readiness jen proto, že localhost v konkrétním WSL/Windows setupu zrovna neodpovídá stejně jako LAN endpoint. `codex/bin/start_codex_stack.sh` už také nepoužívá natvrdo `/mnt/c/Repositories/ai-stack`; repo root bere z aktuálního checkoutu skriptu nebo z `AI_STACK_REPO_ROOT`, takže restart z nového klonu neskončí tichým nasazením starého checkoutu. Stejně tak už nečeká jediného uživatele `sklenik`, ale resolve-ne runtime uživatele z `AI_USER`, `SUDO_USER`, aktuálního `$USER`, vlastníka checkoutu a až pak z `id -un`. Pokud `sudo` vyžaduje heslo, pokusí se o restart přes WSL root interop (`wsl.exe -d Ubuntu -u root`), stejně jako Windows startovací `.bat` skript. Když selže i to, vypíše ruční fallback. Pokud má k dispozici `OWUI_API_KEY` nebo ignorovaný soubor `codex/state/openwebui-api.key`, po restartu také sesynchronizuje OpenWebUI admin filter a auto-tools filter funkci. Stejný reconcile krok spouští i `start_docker.bat`, takže běžný Windows autostart nenechá OpenWebUI tiše s vypnutými nebo stale codex-local funkcemi. Tento kontrakt hlídá i `codex/bin/start_codex_stack_smoke.py`.

Admin odpovědi drží hlavní stav nahoře a dlouhé části jako `output`, `tail` nebo `log_tail` balí do rozbalovacích `<details>` bloků, aby chat zůstal čitelný. OpenWebUI raw HTML v Markdownu escapuje, proto `openwebui/loader.js` tyto doslovné bloky po vykreslení zprávy převádí na skutečné lokální dropdowny.

`Codex Auto Tools Filter` je teď schválně tenká vrstva: pro modely `codex-local-*` už nemá být hlavní mozek. Běžný přirozený prompt se standardně přepíše na jediný technický vstup `GATEWAY_ADMIN_AGENT_LOOP <workspace> -- <původní task>` a teprve gateway si přes LLM zvolí workflow jako `review`, `edit`, `action`, `run`, `bootstrap`, `web_answer`, `web_fetch` nebo `deploy`. Prakticky to znamená, že formulace typu “pullni ai-stack a nasaď”, “stáhni mi to z seznam.cz”, “vytvoř repository Test2”, “přidej WebGL soubor s koulí a spusť to” nebo “přečti docker-compose a vysvětli ho” už nemají být mapované v OpenWebUI přes spoustu zvláštních větví, ale mají projít jedním capability-first agent loopem. Ruční route pravidla zůstávají jen jako nouzový fallback pro explicitní admin markery a kompatibilitu se staršími helpery.

Planner uvnitř gateway je nově výslovně `LLM-first`. To znamená, že `admin_agent_loop()` se nejdřív vždy pokusí získat plán od modelu a teprve když planner call selže nebo vrátí nepoužitelný výstup, přejde na malý bounded fallback. V audit výstupu je to vidět přes `planner_source=llm|fallback`, takže už se dá snadno poznat, jestli systém opravdu rozhodoval přes model, nebo jen zachraňoval běh bez něj.

Ještě důležitější je, že planner už nemá vracet rovnou hotový workflow jen podle keywordů. Nejprve vytváří `TaskSpec`, tedy strukturovaný popis významu úlohy: aktuální workspace, skutečný cíl uživatele, jestli jde o nové repo nebo práci v existujícím workspace, cílový remote, požadovaný end-state, potřebné capability, chybějící vstupy, rizikovost a recovery plán. Deterministický kód pak z `TaskSpec` teprve mapuje capability jako `review`, `workspace_search`, `edit`, `action`, `run`, `bootstrap`, `workspace_git_publish`, `ssh_key_create`, `ssh_key_show_public`, `web_answer`, `web_fetch` nebo `deploy`.

Mezi `TaskSpec` a validací je nově samostatná canonical capability vrstva. LLM může vrátit starší nebo přirozenější alias jako `workspace_ssh_key_create`, `workspace_ssh_key_show_public`, `workspace_review` nebo `read_only_review`, ale gateway ho před validací převede na canonical form `ssh_key_create`, `ssh_key_show_public` nebo `review`. Registry je potom zdroj pravdy pro to, co je implementované; alias už nesmí skončit jako falešný `missing_capabilities`. Když TaskSpec obsahuje zároveň vytvoření SSH klíče i výpis public key, vyhrává `ssh_key_show_public`, protože tahle capability klíč idempotentně vytvoří, pokud chybí, a rovnou vrátí public key/path.

Meta dotazy jsou řešené deterministicky, ne přes obecný repo review: `workspace_context_set`, `workspace_context_status`, `capability_catalog_show` a `agent_runtime_status` vrací aktuální workspace, známé workspaces, capability katalog nebo runtime stav. Dotaz typu “prohledej repo a hledej capability” jde do `workspace_search`, což je bounded `rg` přes workspace s ignorováním runtime/secrets adresářů; pokud by search capability chyběla nebo neměla query, gateway vrátí konkrétní `NEEDS_ATTENTION` místo halucinovaného shrnutí.

Model se přitom standardně nemění. Planner, executor, reviewer i recovery používají stejný default runtime model; liší se jen prompt rolí. Gateway si interně drží role:

- `planner`: vrací jen `TaskSpec` JSON,
- `executor`: vybere a spustí nejbližší bezpečný capability krok,
- `reviewer`: porovná výsledek s cílovým stavem,
- `recovery`: určí root cause, next safe action a případný `MANUAL_STEP_REQUIRED`.

Pro `TaskSpec` a podobné tool JSON se nově preferuje structured-output vrstva v režimu `auto`. Když backend umí `response_format` nebo podobný structured backend, gateway ho použije. Protože některé OpenAI-compatible backendy umí při nepodporovaném `json_schema` místo rychlé chyby dlouho viset, structured pokus je schválně krátký (`CODEX_LOCAL_STRUCTURED_ATTEMPT_TIMEOUT`, výchozí 8 s). Když selže, gateway si to pro běžící proces zapamatuje, další planner volání už structured backend nepokouší a spadne na běžný JSON výstup plus jeden repair retry. Tím pádem `llguidance` nebo Granite planner nejsou povinné runtime závislosti, jen volitelné zlepšení.

Od commitu `7cb415e` a navazujících změn je navíc `TaskSpec` plán považovaný za capability-locked. Prakticky to znamená, že když LLM planner zvolí například `workspace_git_publish`, `workspace_action:verify` nebo read-only `review`, následná normalizační vrstva už ten workflow nesmí znovu přepsat jen proto, že se v promptu objevují slova jako `repo`, `ssh`, `github`, `push`, `oprav` nebo `spusť`. Keyword heuristiky zůstávají jen jako bounded fallback pro situaci, kdy planner selže nebo capability úplně chybí.

Stejný princip se teď propisuje i do `autopilot` capability. Dřív autopilot jen vzal první podporovanou akci z priority pořadí `install -> verify -> smoke -> test -> build -> lint`. Nově mezi auditovanými kandidáty zkouší bounded LLM next-step planner, který dostane `user_task`, `desired_end_state`, už provedené kroky, `verify_steps` a seznam povolených kandidátů. Smí vybrat jen akci z tohoto seznamu; když vrátí neplatnou akci nebo selže, gateway spadne zpět na deterministic fallback. Tím se zlepšuje praktičnost bez toho, aby se rozšířil runtime scope mimo existující capability hranice.

Praktický dopad je hlavně u Git/GitHub úloh. Prompt typu:

    repo: TestCode
    initni git repo a pushni sem git@github.com:owner/repo.git

už nemá spadnout do bootstrapu nového repa jen proto, že obsahuje slova `repo`, `git` nebo `ssh`. Pokud workspace `TestCode` existuje, `TaskSpec` to má pochopit jako práci uvnitř existujícího workspace a zvolit capability `workspace_git_publish`: případně `git init`, nastavení `origin`, commit změn, push branch `main` přes workspace SSH key a při auth blockerech vrácení `MANUAL_STEP_REQUIRED` s public key a přesným dalším krokem místo generic rady.

TaskSpec také prochází validací capability až po canonicalizaci aliasů. Když model požádá o skutečně neznámou nebo zatím neimplementovanou capability, gateway vrátí `AGENT_LOOP_NEEDS_ATTENTION` s `missing_capabilities` a recovery odkazem na `docs/codex-local-capability-roadmap.json`. Nesmí si vybrat “něco podobného”, protože to je přesně cesta k tomu, že agent udělá jinou věc než uživatel chtěl.

Novější vrstva nad tím je `GATEWAY_ADMIN_AGENT_LOOP`: intent-first orchestrace pro běžný codex-local chat. Tady už router nemá být „hlavní mozek“. Tenká OpenWebUI vrstva pouze předá workspace a původní task do gateway, a samotný loop udělá sled `intent/plán -> policy check -> capability execution -> observation -> recovery/verify -> report`.

Prakticky to znamená:

- `nic needituj` nebo jiný explicitní read-only požadavek jde do `review`,
- `kde teď jsi`, `přepni se do workspace Test2` nebo `jaké máš capability` jde do meta capability,
- `prohledej repo` jde do `workspace_search` a vrací konkrétní match řádky,
- bezpečná editace jde do `edit` a může si sama přidat `run_after=verify|test|build|smoke`,
- `ověř projekt`, `spusť build`, `nainstaluj závislosti` apod. jdou do `action`,
- širší “udělej co je potřeba / pokračuj sám / rozběhni to” jde do `autopilot`,
- bootstrap typu “vytvoř repo, initni git, vygeneruj ssh klíč, doinstaluj co chybí” jde do `bootstrap`,
- veřejný webový dotaz jde do `web_answer` nebo `web_fetch`,
- ai-stack self-update/restart prompt jde do `deploy`.

Tím se snižuje závislost na křehkých regex routách v OpenWebUI filtrech. Kód už nemá hádat celý workflow z každé fráze; má hlavně zvalidovat policy, spustit auditovanou capability a při selhání vrátit konkrétní recovery krok.

Nově jsou základní workspace capability akce a jejich přirozené jazykové spouštěče centralizované v `docs/codex-local-capability-roadmap.json` pod `workspace_actions`. To znamená, že `Codex Auto Tools Filter`, `Codex Gateway Admin Filter` i `mentor_codex_local.py` čtou stejný registry pro akce jako `install`, `test`, `build`, `lint`, `verify` a `smoke`, včetně timeoutů, runneru a synonym typu “stáhni co je potřeba”, “doinstaluj co chybí” nebo “pusť to”.

Stejný registry teď používá i gateway autopilot planner. Ten už nevybírá další capability krok jen z úzkého `verify + install` heuristického flow, ale dělá dry-run přes celé povolené action set, řídí se `autopilot_priority` a při failu vrací i recovery směr (`recommendation`, `patch_target`, `patch_hint`, `read_command`) odvozený z registry a scanneru workspace. Prakticky to znamená méně falešných „nic nejde“ stavů a lepší navázání do `mentor_codex_local.py improve`.

`mentor_codex_local.py improve` teď navíc umí malý `diagnose -> fix -> verify` recovery loop. Když autopilot vrátí patch guidance, helper:
1. provede `read_command`,
2. vyžádá si minimální patch plan,
3. nechá model navrhnout přesně jeden malý unified diff,
4. diff lokálně zvaliduje proti safe scope,
5. auditovaně ho aplikuje,
6. hned potom spustí ještě jeden bezpečný capability verify krok.

Tenhle loop může kontrolovaně proběhnout víckrát přes `--recovery-cycles`, takže agent nekončí po prvním patchi, pokud follow-up verify vrátí další konkrétní blocker.

Protože některé instalace OpenWebUI umí odmítnout aktualizaci sekundárního auto-tools filtru, kritické přirozené routy jsou zároveň implementované i v aktivním `Codex Gateway Admin Filter`. Ten umí bez explicitních `GATEWAY_ADMIN_*` markerů zachytit například `repozitar: ai-stack / soubor: docker-compose.yml / vysvětli řádek po řádku`, `repozitar: Test2 / vytvoř mi ssh klíč pro github`, `repozitar: Test2 / spusť testy`, `repozitar: Test2 / přidej webgl soubor s koulí` nebo `kdo má dnes svátek? stáhni mi to z seznam.cz`.

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

    python3 codex/bin/owui_chat_turn.py --model codex-local --prompt-file /tmp/prompt.txt --status-interval 3 --quiet

Helper teď navíc používá stabilní turn key odvozený z `chat_id + model + visible prompt + technical prompt`. Když tedy retryneš stejný běžící turn, pokusí se najít už existující nedokončenou assistant zprávu a znovu ji použít místo vytváření dalšího duplicitního user promptu a dalšího `running...` statusu. Výsledek: méně spamu v audit chatu a menší riziko zbytečných opakovaných volání.

Pro follow-up turny v tom samém viditelném chatu helper nově automaticky zapíná i posílání chat chainu do `/api/chat/completions`, takže navazující prompty typu `vrat mi public key` nebo `pokračuj` neztratí workspace/context jen proto, že caller zapomněl přidat `--send-history`. První one-shot turn zůstává bez historie; když chceš auto chování vypnout i u follow-upu, použij `--no-send-history`.

Další důležitá vrstva je follow nad background joby. Když odpověď z OpenWebUI/gateway vrátí `WORKSPACE_RUN_SCHEDULED` nebo `STACK_DEPLOY_SCHEDULED`, `owui_chat_turn.py` už neskončí jen u “scheduled”. Ve výchozím stavu si vezme `job_id` a přes skryté follow-up completion requesty průběžně polluje `GATEWAY_ADMIN_RUN_WORKSPACE_STATUS` nebo `GATEWAY_ADMIN_DEPLOY_STATUS`, zatímco ve viditelné assistant zprávě aktualizuje stručný průběžný stav. Tím se audit chat chová víc jako skutečný agentický turn, který se snaží počkat na výsledek background capability jobu, ne jen jako fronta scheduler requestů. Když tenhle follow nechceš, vypni ho přepínačem `--no-follow-scheduled`.

Pro skutečný chat-level E2E smoke nad audit chatem je nad tím ještě `codex/bin/owui_chat_smoke.py`. Ten obalí `owui_chat_turn.py`, pošle jeden reálný turn do OpenWebUI, znovu načte chat a zkontroluje, že se ve viditelné historii opravdu objevil user prompt i dokončená assistant odpověď pro stejný `turn_key`. To je nejbližší opakovatelný smoke helper k use-casu “otestuj to jako user přes chat okno”:

    python3 codex/bin/owui_chat_smoke.py \
      --model codex-local \
      --visible-prompt "repo: ai-stack\nOdpovez jednim slovem: smoke-ok" \
      --prompt "repo: ai-stack\nOdpovez jednim slovem: smoke-ok" \
      --expected-substring smoke

Nad tím je teď ještě lehký scénářový runner `codex/bin/owui_chat_scenarios.py`. Ten už neposílá interní marker nebo technický admin prompt, ale běžný user-like audit chat prompt, takže ověřuje i přirozený routing přes `Codex Auto Tools Filter`. Hodí se pro levné E2E ověření, že codex-local pořád zvládá základní agentické flow přes samotný OpenWebUI chat:

    python3 codex/bin/owui_chat_scenarios.py --list
    python3 codex/bin/owui_chat_scenarios.py --dry-run --scenario agent-review --scenario verify-project
    python3 codex/bin/owui_chat_scenarios.py --dry-run --scenario explicit-agent-loop
    OWUI_API_KEY=... python3 codex/bin/owui_chat_scenarios.py --scenario all --json

Výchozí scénáře dnes pokrývají:
- `agent-review`: read-only review přes intent-first agent loop
- `verify-project`: přirozené “ověř projekt”
- `explicit-agent-loop`: kontrola, že `GATEWAY_ADMIN_AGENT_LOOP` zachytí capability vrstva, ne plain model

Pro mutační a kontextové regresní testy jsou za `--include-mutating` ještě scénáře jako:
- `bootstrap-followthrough`: bootstrap + “stáhni co je třeba / pusť to”
- `safe-edit-verify`: malý edit + verify
- `bootstrap-ssh-public-key`: skutečný multi-turn follow-up `vytvoř repo -> vytvoř SSH key -> vrať public key`, který hlídá, že se mezi tahy neztratí workspace context a agent nespadne do plain LLM nebo review fallbacku

To je záměrně levnější než plný browser E2E. Neověřuje vzhled UI, ale přímo to, že běžná lidská formulace v audit chatu projde route -> filter -> gateway -> capability -> zpět do viditelné odpovědi.

Pro nested helper flow je navíc k dispozici čistě offline regression smoke:

    python3 codex/bin/owui_chat_turn_stateless_smoke.py

Ten nevolá živé OpenWebUI, ale importuje `owui_chat_turn.py` a ověří, že `--stateless` režim sahá pouze na `/api/chat/completions` a vůbec nečte ani nepřepisuje `/api/v1/chats/...`. Je to pojistka proti návratu re-entrant deadlocku, kdy helper spuštěný přes OpenWebUI zkoušel znovu mutovat stejný audit chat uvnitř jednoho requestu.

Podobně je tu i `codex/bin/gateway_recovery_smoke.py`. Ten bez živého Dockeru nebo OpenWebUI ověří, že `workspace_action_failure_recommendation()` umí z dat v `docs/codex-local-capability-roadmap.json` odvodit konkrétnější patch guidance pro časté fail signatury jako `missing script: test`, `vite: not found`, `missing script: dev` nebo neplatnou Python dependency při install kroku. Je to malý guard proti návratu k příliš obecnému “zkontroluj manifest” recovery textu.

`codex/bin/codex_gateway_smoke.py` už nekontroluje jen `/health`, `/v1/models` a obecné chat endpointy. Nově explicitně ověřuje i to, že přirozený `codex-local` prompt typu read-only review vrátí `AGENT_LOOP_*` odpověď s `workflow=review`, takže gateway smoke umí zachytit regresi zpět do plain LLM režimu i bez browserového E2E.

Vedle toho je tu i `codex/bin/gateway_runtime_health_smoke.py`. Ten čistě offline hlídá kontrakt nového `/health` payloadu: že ready stav vrací `codex_local_ready=true`, `capability_mode=agent-first`, `natural_codex_local_route=agent_loop`, `runtime_commit` a admin token readiness, a že ne-ready stav vrací konkrétní `readiness_issues` místo neurčitého “něco je špatně”. Je to pojistka proti tomu, aby se runtime observability časem nerozpadla a stack znovu nezačal působit zdravě i ve chvíli, kdy capability-first cesta ve skutečnosti není připravená.

Nová vrstva nad tím je `agent_self_improve`. Je to auditovaná rutina pro případy, kdy se codex-local v OpenWebUI zachová špatně nebo kdy má vzniknout nová menší capability. Běží jako bezpečný self-improving junior programátor nad existující architekturou: `collect_context -> reproduce -> reason -> propose_patch -> generate_unified_diff -> apply_guarded_patch -> verify -> e2e -> report`. Vezme chat transcript nebo chat URL, uloží redigovaný artifact do unikátního adresáře `codex/audit/self-improve/<timestamp>-<hash>-pid.../`, určí typ failu, vytvoří regression scenario, spustí route/capability reprodukci, připraví patch proposal, vygeneruje auditovaný unified diff draft a podle režimu ověří, připraví deploy nebo E2E. Spuštění bez živého OpenWebUI:

    python3 codex/bin/agent_self_improve.py \
      --workspace ai-stack \
      --transcript-file /tmp/fail-transcript.json \
      --expected-behavior "workflow=ssh_key_show_public" \
      --mode verify \
      --dry-run

Pro návrh nové capability slouží režim `capability_develop`:

    python3 codex/bin/agent_self_improve.py \
      --workspace ai-stack \
      --mode capability_develop \
      --target-capability-name workspace_profile \
      --feature-request "Add bounded workspace profiling capability." \
      --dry-run

`target_capability_name` je součást TaskSpec kontraktu. Planner tak nemusí schovávat název nové capability do volného textu; gateway ho předá do self-improve helperu a helper podle něj vytvoří `generated-unified.diff`. Ten je omezený na povolené cesty, typicky `docs/`, `codex/bin/`, `codex/gateway/` a kořenové provozní soubory, a ještě před apply musí projít `git apply --check`. Capability draft už netvoří jen roadmap poznámku, ale i strojově validovatelný smoke kontrakt `docs/capability-drafts/<capability>.smoke.json`, který generic recovery smoke ověří proti roadmapě, canonical aliasům a očekávaným draft pathům. Defaultní OpenWebUI agent loop spouští self-improve jako `dry_run=True`; skutečný apply má jít přes auditovaný admin/CLI krok po senior review.

Přes gateway existuje stejná capability jako `agent_self_improve` a přímý endpoint `/v1/admin/agent/self-improve`. CLI cesta:

    python3 codex/bin/gateway_admin.py self-improve --workspace ai-stack --chat-url http://192.168.0.48:9090/c/<id>

Rutina je silná, ale není neomezený shell. Nikdy nevypisuje tokeny, `.env` ani private SSH key, patch aplikuje jen z explicitně dodaného patch souboru, nejdřív kontroluje povolené cesty a `git apply --check`, a nikdy nenasazuje po selhaných smoke testech. Před živým E2E/deploy navíc povinně běží `gateway_runtime_fingerprint_check.py`; pokud live `/health` nehlásí stejný source epoch/fingerprint jako checkout, self-improve vrátí recovery místo falešného OK. Typické failure patterns jako `kde ted jsi?`, `jake mas capability?`, `vytvor tam ssh klic a vypis mi public`, bounded repo search, `max_cycles`, patch proposal, capability-development artifact, runtime gate a unikátní paralelní artefakty jsou pokryté `codex/bin/agent_self_improve_smoke.py`.

`agent_capability_develop` je samostatná capability v katalogu. Nemá přidávat další prompt-specific keyword router; má vytvořit TaskSpec/acceptance criteria, registry změny, executor/workflow scope, roadmap/docs, testovací plán a unified diff draft. Codex-local tím může odpracovat rutinní průzkum, návrh testů, patch proposal, diff draft, smoke běhy a recovery report, zatímco senior Codex drží finální architekturu, bezpečnostní hranice a review aplikovaného diffu.

`workspace_search` preferuje `rg`, ale není na něm tvrdě závislé. Pokud runtime kontejner `rg` nemá, capability použije bounded Python fallback se stejnými skip pravidly pro `.git`, `codex/state`, `codex/audit`, logy, dependency a build adresáře. Deploy smoke tak nesmí selhat jen proto, že konkrétní runtime image nemá ripgrep.

Stejná recovery metadata už dnes nenesou jen patch guidance, ale i retry záměr: gateway k failnutému kroku vrací `retry_action`, `retry_runner` a `retry_timeout`. Díky tomu `mentor_codex_local.py improve` po úspěšném safe patchi neudělá jen obecné `verify`, ale zkusí znovu právě ten capability krok, který předtím selhal, typicky `test`, `smoke`, `build` nebo `install`. Když ten retry uspěje, helper ještě jednou krátce přepočítá autopilot a zkusí další bezpečný krok, takže recovery loop nekončí hned v momentě, kdy se odblokuje první symptom.

Na to je navázaný i malý offline guard `codex/bin/mentor_recovery_followup_smoke.py`. Ten ověří, že mentor helper po patchi opravdu skládá `GATEWAY_ADMIN_WORKSPACE_ACTION <workspace> <retry_action> ...` a jen při chybějícím retry hintu spadne zpět na generický jednokrokový autopilot verify.

Vedle toho je ještě `codex/bin/mentor_improve_outcome_smoke.py`. Ten hlídá, že `improve` loop neoznačuje výsledek příliš optimisticky: rozlišuje `completed`, `capability_progress`, `blocked`, `recovered_to_new_blocker` a `recovery_limit_reached` podle `stop_reason`, patch guidance a recovery markerů. Cílem je, aby helper po krátkém chainu uměl říct, jestli je opravdu hotovo, nebo jestli se jen posunul k dalšímu přesnému blockeru.

Ještě jedna levná pojistka je `codex/bin/gateway_admin_run_workspace_smoke.py`. Ten neklepe na živou gateway, ale importuje `openwebui_gateway_admin_filter.py` a ověří, že starší nebo ručně vložené nested helper commandy dostanou před spuštěním bezpečný stateless tvar. Prakticky hlídá dvě věci:
- `python3 codex/bin/mentor_codex_local.py delegate ...` dostane automaticky `--stateless-turns`,
- `python3 codex/bin/owui_chat_turn.py ...` dostane automaticky `--stateless`.

To je důležité jako druhá obranná linie proti rekurzi `OpenWebUI chat -> helper -> stejný chat`, i kdyby někde zůstal starý builder bez stateless flagu.

Na stejný problém teď míří i `codex/bin/gateway_background_env_smoke.py`. Ten už neleze do filtru, ale přímo do runtime vrstvy `admin_run_workspace(background=True)`. Ověřuje, že background gateway job předá child procesu i `OWUI_STATELESS=1` a další forwarded env. To chrání scénář, kdy route už helper správně pošle jako background run, ale child proces by bez env propagation znovu spadl do viditelného `/api/v1/chats/...` flow a zacyklil se na stejném OpenWebUI requestu.

Stejnou věc jde teď spouštět i přes hlavní mentor helper, aby scénářový smoke nebyl další izolovaný nástroj bokem:

    python3 codex/bin/mentor_codex_local.py chat-scenarios ai-stack --list
    python3 codex/bin/mentor_codex_local.py chat-scenarios ai-stack --dry-run --scenario verify-project --scenario next-step

Pro rychlou kombinovanou kontrolu celé mentoring vrstvy je tam nově i:

    python3 codex/bin/mentor_codex_local.py self-check ai-stack "Navrhni dalsi krok a dotahni co pujde."

`self-check` skládá několik levných kontrol do jednoho reportu:
- `mentor_scenario_runner.py` pro levný helper-orchestration smoke,
- helper-only `bootstrap-probe` pro ověření bootstrap/create-repo reasoning bez mutací,
- `filter_route_smoke.py` pro offline ověření, že přirozené OpenWebUI prompty routeují na správné admin/capability workflow,
- `owui_chat_turn_stateless_smoke.py` pro regression guard nad nested OpenWebUI helper flow,
- `mentor_recovery_followup_smoke.py` pro regression guard nad retry-after-patch loopem v `improve`,
- `mentor_improve_outcome_smoke.py` pro regression guard nad klasifikací hotovo/progress/blocker v `improve`,
- `gateway_admin_run_workspace_smoke.py` pro regression guard nad automatickou stateless normalizací starších helper commandů,
- `gateway_background_env_smoke.py` pro regression guard nad předáním `OWUI_STATELESS` do background workspace jobů,
- `chat-scenarios` pro user-like OpenWebUI audit chat flow, včetně širší autonomy/profile vrstvy,
- `check_ai_stack.sh` pro stack summary.

`filter_route_smoke.py` je záměrně offline: importuje `Codex Auto Tools Filter` přímo a ověřuje například, že `Navrhni další krok.` zůstane recommend-only autopilot, zatímco `Navrhni další krok a dotáhni co půjde.` se přepíše na mentor `delegate`. Když chybí OpenWebUI API key, chat scénáře se v `self-check` automaticky přepnou do `dry-run` režimu a report je označí jako `degraded` místo falešného tvrdého failu. Díky tomu jde self-check pouštět i v lokálním klonu, kde zrovna nemáš k dispozici celý běžící stack.

`check_ai_stack.sh` nad tím teď skládá obě úrovně důkazů. Nejdřív pustí levné offline guardy jako `filter_route_smoke.py`, `workspace_context_regression_smoke.py` a `gateway_recovery_smoke.py`, takže se regres v routování, udržení workspace kontextu nebo recovery logice chytí i bez běžícího OpenWebUI stacku. Teprve potom přidává runtime probe jako `codex_gateway_smoke.py`, audit chat smoke, scénáře a reconcile check. Tím se problém typu “codex-local zase tiše spadl do plain LLM režimu” nebo “follow-up prompt ztratil workspace a spadl zpět do ai-stack” dá zachytit jak při obyčejném review v klonu, tak po skutečném restartu nebo deployi.

Stejný princip teď hlídá i samotný helper `codex/bin/owui_chat_turn.py`. Pro modely `codex-local-*` před každým visible i stateless completion requestem udělá preflight přes gateway `/health` a přes `reconcile_openwebui_functions.py --check-only`. Když gateway není `codex_local_ready`, chybí admin token, jsou OpenWebUI filtry inactive/stale nebo běžící runtime neodpovídá lokálnímu repu (`runtime_fingerprint` mismatch), helper prompt do `/api/chat/completions` vůbec nepošle. Místo tichého pádu do plain LLM odpovědi vrátí rovnou marker jako `GATEWAY_ADMIN_TOKEN_MISSING`, `CODEX_LOCAL_FILTER_INACTIVE`, `CODEX_LOCAL_FILTER_STALE`, `CODEX_LOCAL_RUNTIME_SPLIT_BRAIN` nebo `CODEX_LOCAL_GATEWAY_UNAVAILABLE` s konkrétním recovery krokem.

Když naopak chceš opravdu plný živý důkaz přes OpenWebUI chat, použij:

    python3 codex/bin/mentor_codex_local.py self-check ai-stack "Navrhni dalsi krok a dotahni co pujde." --strict-live

`--strict-live` nedovolí fallback do `dry-run`. Pokud není dostupný OpenWebUI API key, skončí hned s jasným blockerem místo “degraded” režimu.

`self-check` nově standardně obsahuje i `bootstrap-probe`: helper-only scénář nad zadáním typu “vytvoř nové repository Test2 jako React appku, doinstaluj co chybí a zkus to rozběhnout.” Tím průběžně ověřujeme, že mentor vrstva pořád umí rozpoznat a rozplánovat bootstrap-oriented use-case, aniž by během běžného self-checku opravdu zakládala nové repozitáře. Chování lze upravit přes:

    python3 codex/bin/mentor_codex_local.py self-check ai-stack \
      --bootstrap-task "Vytvor nove repository Test3 jako FastAPI appku a navrhni dalsi kroky."

    python3 codex/bin/mentor_codex_local.py self-check ai-stack --skip-bootstrap-probe

`codex/bin/check_ai_stack.sh` to teď umí použít i automaticky. Když je dostupný OpenWebUI API key přes `OWUI_API_KEY` nebo ignorovaný `codex/state/openwebui-api.key`, healthcheck po gateway smoke přidá i audit-chat smoke. Pokud key chybí, krok se jen korektně přeskočí. Vypnout ho jde přes `SKIP_OWUI_CHAT_SMOKE=1`.

Stejný healthcheck teď umí po základním audit-chat smoke spustit i lehké user-like scénáře přes `owui_chat_scenarios.py`. Výchozí sada je záměrně levná (`agent-review,verify-project`), aby šlo rychle ověřit, že pořád funguje i přirozený route přes filter a capability vrstvu, ne jen úzký technický smoke. Chování jde řídit přes:
- `OWUI_CHAT_SCENARIOS=agent-review,explicit-agent-loop,verify-project`
- `SKIP_OWUI_CHAT_SCENARIOS=1`

Pro admin nebo patch operace používej oddělený viditelný a technický prompt. Viditelný prompt je lidský popis práce pro audit chat; technický prompt může obsahovat interní gateway/admin marker a diff, ale do viditelné historie se nezapisuje:

    python3 codex/bin/owui_chat_turn.py --model codex-local --visible-prompt-file /tmp/visible.txt --prompt-file /tmp/technical.txt --status-interval 3 --quiet

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

Pro nejbližší chování typu “dělej to jako Codex a dotáhni co zvládneš” je tam nový mód `improve`: nejdřív nechá doběhnout capability vrstvu (`install/test/build/lint` podle projektu), a teprve když už další krok není čistě spustitelný, přepne se do recommendation -> read -> patch-plan -> safe apply workflow. Když patch projde, helper se teď nepokouší jen o generické verify, ale přednostně znovu spustí právě ten původně selhaný capability krok podle gateway recovery metadata:

    python3 codex/bin/mentor_codex_local.py improve ai-stack

Když nechceš ručně přemýšlet, který mód zvolit, použij `delegate`: helper podle textu úkolu sám vybere vhodnou orchestrace vrstvu (`audit`, `autopilot`, `apply-safe`, `improve`, případně `run`) a tu pak spustí přes audit chat:

    python3 codex/bin/mentor_codex_local.py delegate ai-stack "Fixni to a dotáhni co zvládneš."

Když chceš jen rychle zjistit, jakou šířku pravomocí by helper pro úkol zvolil, použij `profile`. Vrátí `runtime_profile`, vybraný workflow a důvod, ale nic nespouští:

    python3 codex/bin/mentor_codex_local.py profile ai-stack "Uprav README a aplikuj malý patch"

`profile` a `delegate` navíc nově vrací i `confidence`, `guardrail_summary` a `missing_capability_hint`, takže je hned vidět, proč helper zůstal v review scope, kdy mu stačí capability runner, kdy už je rozumné přejít do safe patch nebo širšího `improve` flow, a jaká přesná capability vrstva ještě chybí, pokud je úkol širší než současné guardraily. Zároveň už helper není zbytečně úzký u přímých capability úkolů: požadavky typu `spusť testy`, `nainstaluj závislosti`, `ověř projekt`, `vytvoř repository Test2` nebo `pullni ai-stack a nasaď` umí klasifikovat rovnou na auditované workflow `action`, `create-repo` nebo `deploy`, místo aby končil jen obecným `audit`.

U follow-through zadání má `profile` stejné pravidlo jako člověk: čisté `jen navrhni`, `pouze analyzuj` nebo `nic needituj` zůstává read-only `audit`, ale kombinace typu `navrhni další krok a dotáhni co půjde`, `udělej maximum`, `postarej se o to` nebo `pokračuj sám` se bere jako explicitní požadavek na `improve`. Díky tomu self-check a běžné mentorování netvrdí, že uživatel chtěl jen analýzu, když ve stejné větě žádá i provedení.

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
- Pro rychlou práci používej `codex-local`; `codex-local-heavy` pouštěj jen při explicitním deep/heavy požadavku nebo ručním přepnutí.
- Do promptů ani verzovaných souborů nevkládej secrets; OpenWebUI API key ukládej do ignorovaného `codex/state/openwebui-api.key`.
- Před pushem vždy zkontroluj `GATEWAY_ADMIN_GIT_STATUS` a ujisti se, že `blocked_paths` i `sensitive_paths_seen` jsou `(none)`.

## Provozní příkazy

- Start z Windows: `C:\Repositories\ai-stack\start_docker.bat`.
- Ruční start Codex stacku ve WSL: `sudo /mnt/c/Repositories/ai-stack/codex/bin/start_codex_stack.sh`.
- Pokud ruční start skončí před `Codex stack OK`, skript vypíše `GATEWAY_START_FAILED`, stav procesu gateway a posledních 80 řádků `codex/audit/gateway.log`, aby byl vidět import/runtime error bez ručního lovení v logu.
- Volitelný autostart při každém startu WSL: v `/etc/wsl.conf` nastav `[boot] command = bash /mnt/c/Repositories/ai-stack/codex/bin/wsl_boot_ai_stack.sh --background`. Skript loguje do `codex/audit/wsl-boot-ai-stack.log`.
- Přidání workspace: `python3 codex/bin/add_workspace.py <name> <path> --port <port>`.
- Kontrola gateway: `curl http://127.0.0.1:9101/health` ve WSL nebo `curl http://192.168.0.48:9101/health` z LAN. Endpoint nově vrací i host-side probe `openwebui.root` a `openwebui.loader`, plus readiness signály `codex_local_ready`, `capability_mode=agent-first`, `natural_codex_local_route=agent_loop`, `runtime_commit`, `runtime_fingerprint` a stav `gateway_admin` tokenu. Je tak hned vidět rozdíl mezi “gateway běží” a “stack je opravdu připravený jako capability-first coding agent”.
- Kandidátní OpenWebUI URL, které runtime použije pro health/deploy/check flow, vypíše `python3 codex/bin/openwebui_runtime.py`. Helper čte explicitní env (`OPENWEBUI_URL`, `OPENWEBUI_PUBLIC_URL`, `WEBUI_URL`), `docker-compose.yml` `WEBUI_URL`, umí odvodit `:9090` z `CODEX_GATEWAY_PUBLIC_URL` a teprve nakonec přidává `127.0.0.1` a `localhost`.
- Smoke test gateway: `python3 codex/bin/codex_gateway_smoke.py --base-url http://192.168.0.48:9101 --workspace ai-stack`.
- Celkový healthcheck lokálního stacku ve WSL: `bash codex/bin/check_ai_stack.sh`; pro LAN kontrolu nastav `OPENWEBUI_URL=http://192.168.0.48:9090 CODEX_GATEWAY_URL=http://192.168.0.48:9101`. Skript kontroluje i přítomnost WSL boot wrapperu a konfiguraci `/etc/wsl.conf`; chybějící autostart config je jen `SKIP`, protože primární start může dál řešit Windows Task Scheduler. Pokud je dostupný OpenWebUI API key, skript kontroluje required funkce přes reconciler a explicitní `GATEWAY_ADMIN_AGENT_LOOP` smoke posílá stateless přes `/api/chat/completions`, aby ověřil filtry bez mutace viditelného chatu. Nově přidává i runtime fingerprint check přes `codex/bin/gateway_runtime_fingerprint_check.py` a transcript-level `codex/bin/workspace_context_regression_smoke.py`, takže umí chytit jak split-brain stav „repo už je nové, ale běžící gateway proces je ještě starý“, tak regresi typu „follow-up prompt ztratil workspace a utekl do ai-stack“. Viditelné audit-chat smoke/scénáře přes `owui_chat_smoke.py` zůstávají dostupné, ale deploy je defaultně přeskakuje pomocí `SKIP_OWUI_CHAT_SMOKE=1 SKIP_OWUI_CHAT_SCENARIOS=1`, protože po restartu OpenWebUI nechceme dlouhým UI pollingem držet deploy ve stavu `running`. Pro levný endpoint-only healthcheck bez modelového smoke použij `SKIP_GATEWAY_SMOKE=1 SKIP_OWUI_CHAT_SMOKE=1 SKIP_OWUI_CHAT_SCENARIOS=1`.
- `GATEWAY_ADMIN_CHECK_STACK` už v OpenWebUI nevrací celý syrový healthcheck log. Gateway filter pouští `check_ai_stack.sh` v `CHECK_AI_STACK_SUMMARY_ONLY=1` režimu a vrací stručný verdict se souhrnem checků. Dlouhé admin výstupy jako deploy tail, workspace output nebo smoke logy se nově vrací jako kompaktní preview bloky místo doslovného HTML `<details>`, protože tenhle renderer je v běžném OpenWebUI chatu nevykresloval spolehlivě.
- Spuštění kontrolního příkazu v registrovaném workspace přes gateway: `curl -sS http://127.0.0.1:9101/v1/admin/workspace/run -H "Content-Type: application/json" -d '{"workspace":"ai-stack","timeout":30,"command":["git","status","--short","--branch"]}'`.
- Dlouhé mentor/OpenWebUI helper běhy se z admin filtru nespouští synchronně. Když `GATEWAY_ADMIN_RUN_WORKSPACE` obsahuje `mentor_codex_local.py` nebo `owui_chat_turn.py`, filtr ho pošle gateway jako background job a v chatu hned vrátí PID a log cestu, aby request nezamrzal v OpenWebUI UI.
- Aby se při těchto nested helper bězích neroztočila rekurze `OpenWebUI chat -> mentor helper -> owui_chat_turn.py -> stejný OpenWebUI chat`, používá helper vrstva explicitní `--stateless-turns`, který se při volání child `owui_chat_turn.py` překládá na `--stateless`. Takový vnitřní krok pak nepíše zpět do `/api/v1/chats/<id>`, ale jde přímo přes jednorázové `/api/chat/completions`, takže se nesnaží editovat právě ten chat, který ho spustil.
- Stejné pravidlo teď platí napříč mentor helper commandy, ne jen pro `delegate`: `brief`, `review`, `boundary`, `profile`, `report`, `plan`, `next-helper`, `publish-plan`, `release-prep`, `bootstrap-dispatch`, `bootstrap-improve` a multi-task helpery už se skládají ve stejném stateless tvaru. Tím se snižuje riziko, že by se některý “levný” analytický helper choval jinak než zbytek.
- Sjednocený stateless tvar se nepropisuje jen do live routování z OpenWebUI filtru, ale i do textových výstupů mentora jako `MENTOR_BRIEF_NEXT_HELPER`, `MENTOR_NEXT_HELPER_COMMAND`, `PLAN_STEP_*`, `BACKLOG_ITEM_*_PLAN_CMD` a `BACKLOG_ITEM_*_REPORT_CMD`. Díky tomu i copy/paste workflow z audit chatu nebo z lokálních helper výstupů používá stejný bezpečný command shape jako runtime router.
- Čistě read-only architektonické prompty typu `repo: ai-stack` + `Nic needituj. Řekni blockery autonomie...` už se nesnaží routovat do self-delegace přes `mentor_codex_local.py delegate`. Jdou přes `GATEWAY_ADMIN_AGENT_LOOP`, kde policy vynutí `workflow=review` a odpověď vznikne bez editace.
- Background workspace run teď vrací i `job_id`. Stav jde kdykoli zkontrolovat přes `GATEWAY_ADMIN_RUN_WORKSPACE_STATUS <job_id>` nebo přirozeně dotazem typu `repo: Test2` + `stav jobu`. Gateway vrátí `running`, poslední `tail` a pokud už doběhl `run_check.py`, i `exit_code`, `duration_ms` a parsovaný `result`.
- Přímý fallback pro deploy přes gateway admin endpoint: `python3 codex/bin/gateway_admin.py --base-url http://127.0.0.1:9101 deploy` na runtime hostu, nebo z jiné stanice s `CODEX_GATEWAY_ADMIN_TOKEN` / `codex/state/codex-gateway-admin.token`. Stav zjistíš přes `python3 codex/bin/gateway_admin.py --base-url http://127.0.0.1:9101 deploy-status`.
- Dry-run reconcile všech required OpenWebUI funkcí: `python3 codex/bin/reconcile_openwebui_functions.py --dry-run`.
- Tvrdá kontrola runtime stavu funkcí: `python3 codex/bin/reconcile_openwebui_functions.py --check-only`.
- Aplikace reconcile po review: `python3 codex/bin/reconcile_openwebui_functions.py`.
- Reconciler záměrně nevynucuje jen shodu obsahu, ale i `is_active=true`, `is_global=true` a hash runtime zdroje včetně embedded capability roadmapy. Aktivaci provádí přes OpenWebUI toggle endpointy, ne pouze přes update payload, protože některé verze `is_active` v update requestu ignorují. Tím deploy odhalí i stav, kdy je nový filtr v repozitáři, ale OpenWebUI ho v model pipeline reálně nepoužívá.
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
