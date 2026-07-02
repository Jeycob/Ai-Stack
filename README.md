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
- `codex/gateway/gateway.py`: OpenAI-compatible gateway pro modely a workspace snapshoty.
- `codex/workspaces.json`: registr workspaces, portů a resource limitů.
- `codex/opencode-default.json`: výchozí OpenCode konfigurace pro nové workspaces.
- `codex/audit/`: lokální provozní logy; nepatří do Gitu.
- `codex/state/`: runtime stav, hesla a home adresáře agentů; nepatří do Gitu.

## Secret management

Nikdy necommitovat secrets. Do Gitu patří pouze šablony, dokumentace a bezpečné konfigurace.

- `.env` drží lokální secrets jako `WEBUI_SECRET_KEY`; soubor má zůstat ignorovaný.
- `codex/state/` obsahuje runtime stav a secrets, například OpenCode server password nebo home adresář; adresář má zůstat ignorovaný.
- OpenWebUI API klíče, Home Assistant tokeny, bankovní/Fio tokeny a privátní SSH klíče nesmí být v tracked souborech.
- Pokud bude potřeba GitHub SSH deploy/push klíč, privátní klíč ulož pouze do ignorované cesty a veřejný klíč přidej do GitHubu jako deploy key nebo účetní SSH key.
- Pro sdílenou dokumentaci používej placeholdery typu `OPENWEBUI_API_KEY=<set locally>` místo skutečných hodnot.

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

    OWUI_API_KEY=<set locally> python3 codex/bin/owui_chat_turn.py --model codex-local-plan-qwen14b --prompt-file /tmp/prompt.txt --status-interval 3 --quiet

Pro admin nebo patch operace používej oddělený viditelný a technický prompt. Viditelný prompt je lidský popis práce pro audit chat; technický prompt může obsahovat interní gateway/admin marker a diff, ale do viditelné historie se nezapisuje:

    OWUI_API_KEY=<set locally> python3 codex/bin/owui_chat_turn.py --model codex-local-plan-qwen14b --visible-prompt-file /tmp/visible.txt --prompt-file /tmp/technical.txt --status-interval 3 --quiet

Technické markery typu `GATEWAY_ADMIN_APPLY_NOW` jsou interní bezpečnostní protokol pro whitelisted zápis souborů. V běžném OpenWebUI chatu mají být schované za helperem a viditelné jen jako lidské shrnutí práce, status a výsledek.

Gateway podporuje skutečné streaming SSE pro běžné modelové odpovědi: při `stream=true` proxyuje chunkované odpovědi z Ollamy průběžně do OpenWebUI. Admin a patch odpovědi zůstávají pevné, aby se bezpečnostní flow nechovalo nedeterministicky.

Praktická pravidla pro zadávání úloh:

- Napiš, zda agent smí editovat soubory, nebo má jen analyzovat.
- U editací omez rozsah: například `měň jen README.md` nebo `měň jen codex/gateway/gateway.py`.
- Pro rychlou práci používej `codex-local-plan-qwen14b` a `codex-local-build-qwen14b`; 32B nech pro složitější analýzy.
- Do promptů ani souborů nevkládej secrets; používej placeholdery typu `OWUI_API_KEY=<set locally>`.
- Před pushem vždy zkontroluj `GATEWAY_ADMIN_GIT_STATUS` a ujisti se, že `blocked_paths` i `sensitive_paths_seen` jsou `(none)`.

## Provozní příkazy

- Start z Windows: `C:\Repositories\ai-stack\start_docker.bat`.
- Ruční start Codex stacku ve WSL: `sudo /mnt/c/Repositories/ai-stack/codex/bin/start_codex_stack.sh`.
- Přidání workspace: `python3 codex/bin/add_workspace.py <name> <path> --port <port>`.
- Kontrola gateway: `curl http://127.0.0.1:9101/health` ve WSL nebo `curl http://192.168.0.48:9101/health` z LAN.
- Smoke test gateway: `python3 codex/bin/codex_gateway_smoke.py --base-url http://192.168.0.48:9101 --workspace ai-stack`.
- Celkový healthcheck lokálního stacku ve WSL: `bash codex/bin/check_ai_stack.sh`; pro LAN kontrolu nastav `OPENWEBUI_URL=http://192.168.0.48:9090 CODEX_GATEWAY_URL=http://192.168.0.48:9101`.
- Dry-run synchronizace OpenWebUI funkce z verzovaného zdroje: `OWUI_API_KEY=<set locally> python3 codex/bin/sync_openwebui_function.py --dry-run`.
- Aplikace synchronizace OpenWebUI funkce po review: `OWUI_API_KEY=<set locally> python3 codex/bin/sync_openwebui_function.py`.
- Bezpečné mapování OpenWebUI endpointů bez mutačních metod: `OWUI_API_KEY=<set locally> python3 codex/bin/discover_openwebui_endpoints.py --path /api/config --path /api/v1/functions/list`.
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
