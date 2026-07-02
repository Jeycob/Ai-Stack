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

## Provozní příkazy

- Start z Windows: `C:\Repositories\ai-stack\start_docker.bat`.
- Ruční start Codex stacku ve WSL: `sudo /mnt/c/Repositories/ai-stack/codex/bin/start_codex_stack.sh`.
- Přidání workspace: `python3 codex/bin/add_workspace.py <name> <path> --port <port>`.
- Kontrola gateway: `curl http://127.0.0.1:9101/health` ve WSL nebo `curl http://192.168.0.48:9101/health` z LAN.
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
- Cílový vzdálený repozitář: `github.com/Jeycob/Ai-Stack`.
