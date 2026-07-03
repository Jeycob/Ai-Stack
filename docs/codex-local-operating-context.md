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

`owui_chat_turn.py` ma byt idempotentni pro bezne retry. Pro stejny viditelny i technicky prompt si dopocita stabilni turn key a kdyz v chatu najde uz rozbehnutou nedokoncenou assistant zpravu pro ten samy turn, znovu ji pouzije misto toho, aby appendnul dalsi duplicitni user prompt. To je dulezite pro levnejsi mentor workflow i cistsi audit trail.

Doporuceny vzor:

```bash
python3 codex/bin/owui_chat_turn.py \
  --model codex-local-plan-qwen14b \
  --visible-prompt-file /tmp/visible.txt \
  --prompt-file /tmp/technical.txt \
  --status-interval 3 \
  --quiet
```

Pro opakovatelny chat-level smoke nad skutecnym audit chatem je tam i
`codex/bin/owui_chat_smoke.py`. Ten obali `owui_chat_turn.py`, po jednom turnu
znovu nacte cilovy chat a overi, ze se ve viditelne historii opravdu objevil
user prompt i dokoncena assistant odpoved pro stejny `turn_key`. Je to
nejpraktičtejsi helper pro "otestuj to end to end jako user pres OpenWebUI
chat", kdyz nechces kontrolovat historii rucne.

`codex/bin/check_ai_stack.sh` umi tenhle audit-chat smoke pridat do bezneho
stack healthchecku automaticky. Pokud je k dispozici OpenWebUI API key, po
gateway smoke pusti i `owui_chat_smoke.py`. Bez key se tenhle krok jen preskoci
misto failu. Vypnout ho jde pres `SKIP_OWUI_CHAT_SMOKE=1`.

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
ai-stack a nasad", "ukaz deploy status", preflight push dotazy jako
"je to ready na push?" nebo "co blokuje push", publish-plan dotazy jako
"navrhni publish plan", "jak publikovat release" nebo "co mam delat pred releasem", release-prep dotazy jako
"zkontroluj release readiness" nebo "co blokuje release", jednoduche publish zadani jako
"pushni zmeny do GitHubu" nebo "commitni a pushni", bootstrap zadani jako
"vytvor nove repository Test2 a vygeneruj ssh klic", "zaloz projekt Test2 na GitHubu" nebo
"priprav workspace Test2 s deploy key", a u sirsich bootstrap use-casu i formulace typu
"vytvor repository Test2, doinstaluj co chybi a zkus to rozbehnout", bezne repo kontroly typu "zkontroluj git status",
developerske workflow typu "nainstaluj zavislosti" nebo "spust testy", a
explicitni "repo: X / spust prikaz: ...". Nemel by vyrabet novy marker pro
kazdou drobnost; cilem jsou sirsi capability workflow: deploy/status,
workspace-run, workspace-action, workspace-autopilot, create-repo recipe, bootstrap-improve recipe a pozdeji dalsi
profile-based schopnosti.
Cilem je, aby uzivatel nemusel znat interni `GATEWAY_ADMIN_*` markery.

Zaroven ma umet rozlisit `publish-plan`, `release-prep`, `push-check`, jednoduchy audited push a sirsi release automation
scope. Pro "navrhni publish plan" je spravny helper `mentor_codex_local.py publish-plan`; pro "zkontroluj release readiness" je spravny helper `mentor_codex_local.py release-prep`; pro "je to ready na push?" je spravna capability `GATEWAY_ADMIN_GIT_STATUS`; pro "pushni zmeny" je spravna capability `GATEWAY_ADMIN_GIT_PUSH`; pro
"vytvor release", "publish package" nebo GitHub Actions release workflow ma
radsi vratit mentor `boundary` vysvetleni a roadmap hint, nez predstirat, ze
to je jen dalsi obycejny push.

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
- `GATEWAY_ADMIN_WORKSPACE_ACTION <workspace> <install|test|build|lint|verify|smoke> [--timeout seconds] [--env KEY=VALUE] [--dry-run]`: capability-based provedeni beznych developerskych akci nad registrovanym workspace. Resolver vybere prikaz z manifestu a scriptu projektu a spusti ho auditovane pres gateway. `verify` se snazi projekt overit agenticky jako sekvenci `lint -> test -> build` s preskakovanim nepodporovanych kroku. `smoke` zkusi najit standardni startup entrypoint a pusti ho jen v kratkem auditovanem okne, aby slo bezpecne odpovedet i na use-case "zkus to rozbehnout a vrat vysledek".
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
- pouzivat `workspace-action` pro install/test/build/lint/verify/smoke,
- pro vicekrokovou praci pouzivat `mentor_codex_local.py audit` nebo
  `mentor_codex_local.py autopilot`,
- pro nove repo/workspace use-casy, kde bootstrap hned prechazi do dalsi prace,
  pouzivat `mentor_codex_local.py bootstrap-improve`,
- pro review-to-patch mentoring loop pouzivat `mentor_codex_local.py patch-plan`,
- pro pripravu maleho diff navrhu nad auditem pouzivat `mentor_codex_local.py apply-ready`,
- pro maly auditovany patch v bezpecnem scope pouzivat `mentor_codex_local.py apply-safe`,
- pro sirsi "dotahni to co nejdal" workflow pouzivat `mentor_codex_local.py improve`,
- pro bezne mentor orchestracni volani bez rucni volby modu pouzivat `mentor_codex_local.py delegate`,
- pro rychle rozhodnuti o sirce pravomoci pouzivat `mentor_codex_local.py profile`,
- pro kompaktní mentoring souhrn nad jednim taskem pouzivat `mentor_codex_local.py report`,
- pro konkretni starter/bootstrap recipe nad novym projektem pouzivat `mentor_codex_local.py scaffold-plan`,
- pro kratky sequenced mentoring plan nad jednim taskem pouzivat `mentor_codex_local.py plan`,
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

Priklad sirsiho repo bootstrapu, ktery nejdriv repo zalozi a zaregistruje a pak
nad nim rovnou pokracuje pres `improve`:

```bash
python3 codex/bin/mentor_codex_local.py bootstrap-improve ai-stack "Vytvor nove repository Test2, doinstaluj co chybi a zkus to rozbehnout."
```

Kdyz je v zadani rovnou i technologicky zamer, napriklad React, Next.js,
FastAPI, Three.js nebo OpenGL, helper si ho ulozi jako `solution_profile` a
`starter_hint` do execution briefu. Tim se dalsi improve krok neodpojuje od
puvodniho stackoveho zameru a codex-local nemusi po bootstrapu improvizovat
uplne od nuly.

Pro bezne stacky helper navic umi doplnit i `public_stack` a
`public_stack_rationale`, tedy doporucenou sadu verejnych knihoven a
toolingu, ktere maji byt preferovane pred vlastnim boilerplatem. Tohle je
dulezite pro cely smer projektu: codex-local ma byt vic orchestrace a reuse,
min vlastni framework.

Pro nejbeznejsi stacky uz helper umi i `scaffold_recipe`, `scaffold_files` a
`scaffold_loop`. To znamena, ze execution brief muze obsahovat nejen "tohle je
asi FastAPI" nebo "tohle vypada jako React", ale i prvni konkretni bootstrap
krok, seznam klicovych souboru a doporuceny smoke/test/build sled. Presne tohle
ma lokalni modelu pomahat, aby se choval vic jako vedena implementace a min
jako neurcite improvizovani.

Kdyz tenhle mezikrok chceme explicitne a bez dalsi exekuce, je na to helper
`mentor_codex_local.py scaffold-plan`. Ten vraci prave starter recipe,
ocekavane soubory a verifikacni sled pred tim, nez se pusti `bootstrap-improve`
nebo jiny vykonavajici workflow.

Kdyz uz nechceme jen plan, ale i prvni realny bootstrap krok, je na to
`mentor_codex_local.py bootstrap-dispatch`. Ten vezme inferovany scaffold
recipe, prelozi ho do guardrailed `run_check.py` commandu a umi ho rovnou
spustit v nove vytvorenem workspace. `bootstrap-improve` ho pouziva automaticky
mezi `create-repo` a `improve`, takze lokalni agent neskonci jen u zalozeni
repozitare, ale zkusi i prvni starter/bootstrap command.

Od ted ten helper navic umi z `scaffold_loop` odvodit i dalsi capability kroky.
Nejdřív si pripravi follow-up kandidaty jako `install`, `verify`, `build`,
`test` nebo `lint`, zohledni co uz recipe samo provedlo, a po uspesnem
bootstrapu zkusi pres existujici `workspace_action.py --dry-run` najit kratkou
realne podporovanou posloupnost workspace akci. Sekvence je zamerne kratka a
guardrailed, a pri prvnim failu se zastavi. Tohle je porad reuse stavajici
capability vrstvy, ne dalsi paralelni executor.

Kvuli cene promptu je vhodne rozlisovat plny a compact mentor brief. Plny
execution brief je porad dobry pro debugging, roadmap vysvetleni a hlubsi audit.
Compact brief je vhodny pro dalsi orchestration handoff mezi helpery: drzi jen
minimum kontextu nutne pro pokracovani v dalsim capability kroku a neposila
znovu cely reasoning blok.

Pro levne lokalni E2E overeni mentor vrstvy bez volani ziveho OpenWebUI chatu
je vhodny `codex/bin/mentor_scenario_runner.py`. Ten umi dva rezimy:

- single-task: bere jeden lidsky task a retezi nad nim helpery `profile`,
  `brief`, `next-helper`, `plan` a podle workflow doplni jeste
  `bootstrap-dispatch` nebo `delegate --dry-run`
- multi-task: kdyz dostane vice tasku pres opakovane `--task`, `--task-file`
  nebo stdin, otestuje lehkou prioritizacni vrstvu `backlog -> top -> dispatch
  --recommend-only`

Vysledek je kompaktní scenarovy report nad helper orchestration vrstvou. Je to
dobre pro opakovatelnou validaci klasifikace, handoff logiky i toho, ze si
agent umi sam zvolit dalsi task bez rucniho skladani helperu, ale neni to
nahrada za zivy audit chat nebo gateway smoke test.

Priklad multi-task scenare:

```bash
python3 codex/bin/mentor_scenario_runner.py ai-stack \
  --task "Fixni to a dotahni co zvladnes." \
  --task "Uprav README a aplikuj maly patch" \
  --task "Vytvor release a pushni to na GitHub"
```

Priklad explicitniho scaffold planu:

```bash
python3 codex/bin/mentor_codex_local.py scaffold-plan ai-stack "Vytvor nove repository Test2 jako React appku, doinstaluj co chybi a zkus to rozbehnout."
```

Priklad explicitniho bootstrap dispatch kroku:

```bash
python3 codex/bin/mentor_codex_local.py bootstrap-dispatch ai-stack "Vytvor nove repository Test2 jako React appku, doinstaluj co chybi a zkus to rozbehnout." --execute
```

Priklad profilove klasifikace bez spousteni jakychkoli akci:

```bash
python3 codex/bin/mentor_codex_local.py profile ai-stack "Uprav README a aplikuj maly patch"
```

Priklad mentor reportu, ktery vrati workflow, capability metadata, guardraily i doporuceny dalsi helper krok:

```bash
python3 codex/bin/mentor_codex_local.py report ai-stack "Fixni to a dotahni co zvladnes."
```

Priklad kratkeho execution briefu, ktery je vhodny jako levna mezivrstva mezi planovanim a dalsi modelovou exekuci:

```bash
python3 codex/bin/mentor_codex_local.py brief ai-stack "Fixni to a dotahni co zvladnes."
```

Priklad ultra-levkeho helper navadeni, kdy chceme jen dalsi konkretni command bez sirsiho planu:

```bash
python3 codex/bin/mentor_codex_local.py next-helper ai-stack "Fixni to a dotahni co zvladnes."
```

Priklad guardrail/capability-boundary vysvetleni, kdy chceme vedet proc helper nevolil sirsi akci:

```bash
python3 codex/bin/mentor_codex_local.py boundary ai-stack "Vytvor release a pushni to na GitHub"
```

Priklad mentor planu, ktery z jednoho tasku vrati kratkou 2-4 krokovou posloupnost:

```bash
python3 codex/bin/mentor_codex_local.py plan ai-stack "Fixni to a dotahni co zvladnes."
```

Priklad backlogu nad vice tasky, kdy helper sam srovna poradi a u kazde polozky vrati workflow, capability metadata, dalsi helper command i pripraveny audit prompt:

```bash
python3 codex/bin/mentor_codex_local.py backlog ai-stack \
  --task "Fixni to a dotahni co zvladnes." \
  --task "Uprav README a aplikuj maly patch" \
  --task "Vytvor release a pushni to na GitHub"
```

Backlog umi cist tasky i ze stdin nebo z newline-delimited souboru pres `--task-file`, takze je vhodny jako uzsi scheduler vrstva nad vice prirozenymi zadani.

Priklad dispatch vrstvy nad vice tasky: helper nejdriv postavi backlog, vybere nejvyssi prioritu a pak ji preda do standardniho `delegate` flow. S `--recommend-only` se chova jako planner bez exekuce:

```bash
python3 codex/bin/mentor_codex_local.py dispatch ai-stack \
  --tasks "Fixni to a dotahni co zvladnes." \
  --tasks "Uprav README a aplikuj maly patch" \
  --tasks "Vytvor release a pushni to na GitHub" \
  --recommend-only
```

Prave tahle `recommend-only` varianta je vhodna i pro prirozene chat dotazy typu `Co ma delat jako prvni?`, `Ktery ukol je prvni?`, `Jaky je top task?` nebo `Jen doporuc prvni krok bez spusteni`.

Pro jeste levnejsi use-case je tam i `top`, ktery nevraci cely backlog, ale jen top task, jeho reason, next helper a execution brief:

```bash
python3 codex/bin/mentor_codex_local.py top ai-stack \
  --tasks "Fixni to a dotahni co zvladnes." \
  --tasks "Uprav README a aplikuj maly patch" \
  --tasks "Vytvor release a pushni to na GitHub"
```

Profilove rozhodnuti vraci i:

- `confidence`: jak silne helper veri, ze zvoleny workflow odpovida zadani.
- `guardrail_summary`: kratke vysvetleni, proc je aktualni scope dostatecny a co jeste brani sirsi akci.
- `capability_id`: stabilni jmeno capability nebo roadmap smeru, ke kteremu se helper vztahuje.
- `capability_scope`: hruby scope capability, napriklad `remote_repo`, `host_runtime`, `workspace_runtime`, `workspace_capability` nebo `mentoring`.
- `capability_summary`: kratky verzovany popis capability z roadmap registry.
- `missing_capability_hint`: nejuzsi dalsi capability scope, ktery by daval smysl pridat nebo explicitne pouzit, pokud je ukol sirsi nez stavajici guardraily.

Report navic vraci:

- `MENTOR_REPORT_NEXT_HELPER`: doporuceny dalsi helper command.
- `MENTOR_REPORT_AUDIT_CHAT_PROMPT`: navrh viditelneho promptu pro OpenWebUI audit chat.
- `MENTOR_REPORT_EXECUTION_BRIEF`: kratky nizkonakladovy mentor brief pro dalsi modelovy krok.

Brief vraci:

- `MENTOR_BRIEF_NEXT_HELPER`: doporuceny dalsi helper command.
- `MENTOR_BRIEF_EXECUTION_BRIEF`: minimalisticky cil, guardraily a dalsi krok pripraveny pro dalsi model.

Next-helper vraci:

- `MENTOR_NEXT_HELPER_COMMAND`: nejvhodnejsi dalsi helper command.
- `MENTOR_NEXT_HELPER_REASON`: proc je tenhle helper dalsi spravna volba.
- `MENTOR_NEXT_HELPER_EXECUTION_BRIEF`: nejmensi mentor payload pro dalsi krok.

Boundary vraci:

- `MENTOR_BOUNDARY_GUARDRAIL_SUMMARY`: proc dnesni scope staci nebo nestaci.
- `MENTOR_BOUNDARY_CAPABILITY_SCOPE`: na jaky capability scope task narazi.
- `MENTOR_BOUNDARY_MISSING_CAPABILITY_HINT`: jaky dalsi capability scope by daval smysl pridat nebo explicitne pouzit.
- `MENTOR_BOUNDARY_NEXT_HELPER`: jaky helper ma smysl pustit dal misto slepeho rozsirovani pravomoci.

Review vraci stejny execution brief pattern, ale s workflow `review`; je urceny pro senior read-only pass nad riziky, regreseni a chybejicimi testy pred dalsim capability nebo patch krokem.

`delegate` navic tenhle execution brief nese dal i do dalsich helper promptu. Prakticky to znamena, ze pri prechodu z `dispatch` nebo `delegate` do `audit`/`autopilot`/`improve` uz dalsi modelovy krok nedostane jen obecny workflow prompt, ale i maly stabilni kontext ve visible casti (`Mentor brief:`) a v technicke casti (`MENTOR_EXECUTION_BRIEF`).

Stejny princip plati i pro sirsi capability flow: helper nema zustavat zbytecne uzky u tasku, ktere uz umi auditovane provest. Proto dnes umi klasifikovat i prime capability pozadavky typu `spust testy`, `nainstaluj zavislosti`, `over projekt`, `vytvor repository Test2` nebo `pullni ai-stack a nasad` rovnou na workflow `action`, `create-repo` nebo `deploy`, misto toho aby vsechno shazoval do obecneho `audit`.

Pro opravdu siroka lidska zadani je preferovana dalsi vrstva `delegate`: pokud prompt zni treba `Fixni to a dotahni co zvladnes`, `Udelej co je potreba`, `Proved to jako Codex` nebo `Vyber workflow a proved`, filter nema zbrkle vybrat jediny capability runner. Ma to prelozit na `mentor_codex_local.py delegate`, ktery teprve rozhodne, jestli je spravne `action`, `deploy`, `create-repo`, `autopilot`, `apply-safe` nebo `improve`.

`openwebui_codex_auto_tools_filter.py` umi tenhle use-case uz i prirozene routovat z chatu: kdyz uzivatel napise `repo: <workspace>` a pozadavek typu `Dej mi kratky mentor brief pro ...`, `Jaky brief ma dostat model pro ...` nebo `execution brief`, filter to prelozi na `mentor_codex_local.py brief` pres `GATEWAY_ADMIN_RUN_WORKSPACE`.

Stejne tak uz umi prirozene routovat i dalsi levne mentor vrstvy:

- `Udelej code review`, `Review kodu` nebo `Najdi rizika` -> `mentor_codex_local.py review`
- `Jaky workflow bys zvolil pro ...?` nebo `Jaky runtime profile bys zvolil pro ...?` -> `mentor_codex_local.py profile`
- `Udelej mentor report pro ...` nebo `Shrn workflow pro ...` -> `mentor_codex_local.py report`
- `Priprav kratky plan pro ...` nebo `Jaky plan bys zvolil pro ...?` -> `mentor_codex_local.py plan`
- `Najdi bug a navrhni opravu`, `Fix plan` nebo `Plan opravy` -> take `mentor_codex_local.py plan`, ale jako levny bridge mezi review a opravou

Plan navic vraci:

- `PLAN_STEP_<N>_LABEL`: typ dalsiho kroku.
- `PLAN_STEP_<N>_VALUE`: konkretni helper command nebo capability review krok.
- `PLAN_STEP_COUNT`: pocet kroku v navrhu.
- `MENTOR_PLAN_EXECUTION_BRIEF`: stejny kratky mentor brief, ale uz vedle vicekrokoveho planu.

Backlog navic vraci:

- `MENTOR_BACKLOG_COUNT`: pocet tasku ve fronte.
- `MENTOR_BACKLOG_TOP_TASK`: task s nejvyssi prioritou.
- `BACKLOG_ITEM_<N>_PRIORITY`: jednoduche poradi pro dalsi automatizaci.
- `BACKLOG_ITEM_<N>_NEXT_HELPER`: konkretni helper command pro dalsi krok.
- `BACKLOG_ITEM_<N>_PLAN_CMD`: pripraveny detailni `plan` command pro danou polozku.
- `BACKLOG_ITEM_<N>_AUDIT_CHAT_PROMPT`: viditelny prompt pripraveny do OpenWebUI auditu.

Dispatch navic vraci:

- `MENTOR_DISPATCH_SELECTED_TASK`: vybrany top task.
- `MENTOR_DISPATCH_SELECTED_WORKFLOW`: workflow, ktere se nad nim ma spustit.
- `MENTOR_DISPATCH_SELECTED_NEXT_HELPER`: helper command odpovidajici vybrane polozce.
- `MENTOR_DISPATCH_SELECTED_EXECUTION_BRIEF`: kratky vykonovy brief pro top task.
- `MENTOR_DISPATCH_MODE`: `recommend-only` nebo `execute`.

Top navic vraci:

- `MENTOR_TOP_TASK`: aktualne nejvyse prioritni task.
- `MENTOR_TOP_REASON`: proc byl vybran prave tenhle task.
- `MENTOR_TOP_NEXT_HELPER`: jaky helper by mel nasledovat.
- `MENTOR_TOP_EXECUTION_BRIEF`: nejmensi prenositelny mentor payload pro dalsi krok.

Capability registry je verzovany v `docs/codex-local-capability-roadmap.json` a slouzi jako maly zdroj pravdy pro budouci helpery, prompt tuning i OpenWebUI routovani.

`openwebui_codex_auto_tools_filter.py` uz umi nektere prirozene pozadavky, ktere jsou sirsi nez aktualni safe runtime scope, prelozit nejen na workflow, ale i na capability-roadmap stopu. Prakticky to znamena, ze u GitHub/release nebo host-runtime use-casu se v auditu objevi i `CAPABILITY_ROADMAP_ID`, `CAPABILITY_ROADMAP_SCOPE` a `CAPABILITY_ROADMAP_SUMMARY`. Zaroven umi ze single-task nebo vice-task promptu vyrobit `profile`, `report`, `plan`, `brief`, `next-helper`, `boundary`, `backlog`, `dispatch` nebo `top` helper call, takze codex-local dostane i lehkou prioritizacni a mentor vrstvu bez rucniho skladani admin markeru. Nove se to vztahuje i na patch-priority formulace jako `Co opravit jako prvni?`, `Jaky je dalsi safe patch krok?`, `Ktery bug ma nejvyssi prioritu?` nebo `Serad bugy podle priority`.

Aktualni runtime profily:

- `review`: read-only analyza a dalsi krok bez spousteni.
- `capability`: auditovane capability kroky typu `run`, `install`, `test`, `build`, `lint`, `verify`, `autopilot`.
- `safe_patch`: maly patch v omezenem ai-stack safe scope.
- `runtime`: sirsi agenticky posun projektu, typicky `improve`, tedy capability first a patch az kdyz je to potreba.

Aktualni capability-roadmap registry nově rozlisuje i:

- `workspace_repo_bootstrap`: bootstrap noveho repo/workspace vcetne SSH klice a pripadneho GitHub remote.
- `stack_deploy`: auditovany ai-stack deploy flow pro pull, restart a smoke checky.

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
