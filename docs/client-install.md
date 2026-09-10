# Installing on a client machine, images only

The client's laptop gets Docker, a compose file, an `.env`, and the three
images. No git, no Node, no Python, no source checkout, no build. Setup over
AnyDesk is realistic: everything below is copy-paste into one terminal.

---

## Read this first: images are not a code lock

`docker save` and a private registry are packaging, not licensing. The images
contain the code, in readable form:

- the **worker** image ships `workers/src/*.py` as plain Python
- the **api** image ships compiled JavaScript with its comments intact, and
  the TypeScript beside it
- the **web** bundle is JavaScript the browser downloads anyway

Anyone with the image can `docker cp` all of it out in one command. Shipping
images means the client cannot easily *build* or *modify* your system, and
never has to see your git history — worth having, and genuinely simpler for
them. It does not mean they cannot read it. If that matters, the answer is a
contract, or hosting it yourself and selling access, not a packaging trick.

The same goes for **your API keys**: whatever you put in the client's `.env` is
readable by anyone at that keyboard, and every model call bills to it. Either
accept that, issue the client their own key, or run the thing on your own
server instead.

## Two ways to move the images

| | Private registry (recommended) | `docker save` tarballs |
|---|---|---|
| Client needs internet | yes, to pull | no |
| Client needs a credential | yes, a pull-only token | no |
| First install | pulls a few GB | you transfer a few GB |
| **Every later update** | **only changed layers, usually seconds** | the full few GB again |
| Setup on your side | a GitHub/Docker Hub account | nothing |

The updates row is the one that decides it. With tarballs, a one-line fix
means transferring the whole PaddleOCR layer through AnyDesk's file transfer
again. GHCR gives unlimited private images on a free personal account, and you
already have GitHub.

Use tarballs only when the client's machine genuinely cannot reach a registry.

## On YOUR machine: build the bundle

```bash
cd ~/cdip
./deploy/release.sh 1.0.0 --push ghcr.io/harshilthakkar5
# or, offline:
./deploy/release.sh 1.0.0 --save
```

It builds all three images for `linux/amd64`, pushes or saves them, and writes
`deploy/dist/`:

```
deploy/dist/
  docker-compose.yml     the image-only stack — no build, no source
  .env.template          generated from the repo's own template
  README.txt             the five steps, tailored to how you shipped
  cdip-*.tar.gz          only with --save
```

`deploy/dist/` is that whole handover. Nothing else leaves your machine.

**Architecture matters.** The script builds `linux/amd64` because that is what
a normal Windows laptop is. If the client has an ARM machine (a Snapdragon
Windows laptop, an Apple Silicon Mac), build with
`CDIP_PLATFORM=linux/arm64` — otherwise the containers fail with an "exec
format error" that names nothing useful.

### For the registry route, first

```bash
# a token with write:packages, on your machine only
echo $GITHUB_TOKEN | docker login ghcr.io -u harshilthakkar5 --password-stdin
```

Then make a SECOND token with **`read:packages` only** — that is the one the
client machine gets. Never give them the token that can push.

After the first push the packages default to private. Leave them private; the
client's read-only token is what grants access.

## On the CLIENT machine, over AnyDesk

### 1. Docker Desktop

Install it from docker.com. On Windows it installs WSL2 itself and asks for a
reboot. Turn on **Settings → General → Start Docker Desktop when you sign in**,
or the app will be down after every reboot.

### 2. Memory for the VM

On a 32 GB laptop, create `C:\Users\<them>\.wslconfig`:

```ini
[wsl2]
memory=24GB
processors=8
swap=8GB
```

Then `wsl --shutdown` in PowerShell and reopen Docker Desktop. This is the
ceiling everything runs inside — the container limits in the `.env` sum to
about 16 GB, so the VM must be comfortably above that.

Keep the `.env`'s server-sized block (the `*_MEM_LIMIT` and `*_CONCURRENCY`
values that are NOT commented out). The laptop preset further down that file is
for a 16 GB machine and will only make a 32 GB one slower.

### 3. Turn off sleep

Settings → System → Power → **Never**, on the plugged-in profile. A machine
that suspends is a machine whose app is "broken" every morning.

### 4. Copy the bundle over

AnyDesk's file transfer, or a USB stick, into `C:\cdip`. With `--save` this is
the several-GB part; with the registry route it is three small files.

### 5. Fill in the `.env`

Rename `.env.template` to `.env` and edit these:

```bash
LOCAL_S3_PUBLIC_ENDPOINT=http://localhost:9000   # or http://<their-LAN-IP>:9000
APP_URL=http://localhost                         # must match the above host
POSTGRES_PASSWORD=<long random>
LOCAL_S3_SECRET=<long random>
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
```

`LOCAL_S3_PUBLIC_ENDPOINT` is the only one with a real decision behind it.
Uploads go from the browser **straight** to object storage with a presigned
URL, and a presigned URL is signed against the host it will be requested on:

- **only this laptop uses the app** → `http://localhost:9000`
- **other machines in their office use it too** → `http://<this laptop's LAN
  IP>:9000`, and open ports 80 and 9000 in Windows Firewall (see
  `docs/local-server-deployment.md` §1)

Get it wrong and every upload fails with `SignatureDoesNotMatch` — in the
browser, with nothing in the server logs.

### 6. Start it

In PowerShell (Docker Desktop puts `docker` on the Windows PATH, so no WSL
terminal is needed):

```powershell
cd C:\cdip

# only with --save: load the images first
Get-ChildItem *.tar.gz | ForEach-Object { docker load -i $_.FullName }

# only with the registry: the pull-only token
docker login ghcr.io -u harshilthakkar5

docker compose up -d
```

Migrations run by themselves — the compose file has a one-shot `migrate`
service that the API and worker wait on, so there is no second command and no
way to start the app against an un-migrated database.

Watch it come up:

```powershell
docker compose ps
docker compose logs -f api
```

Then open the address from `APP_URL` and register the client's account.

### 7. Check it landed right

```powershell
curl.exe http://localhost/api/health
```

(`curl.exe`, not `curl` — in Windows PowerShell the bare name is an alias for
`Invoke-WebRequest`, which prints an object rather than the JSON.)

```json
{"status":"ok","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"},
 "storage":{"backend":"local","bucket":"cdip-local"}}
```

## Shipping an update later

On your machine:

```bash
./deploy/release.sh 1.1.0 --push ghcr.io/harshilthakkar5
```

On theirs — two lines, and Docker pulls only the layers that changed:

```powershell
notepad .env          # CDIP_TAG=1.1.0
docker compose up -d
```

The `migrate` service runs any new migrations before the API restarts. Their
data is in named Docker volumes, untouched by the image swap.

**Keep the old tag.** Rolling back is `CDIP_TAG=1.0.0` and `up -d` again —
unless a migration ran, which is one-way. Take a database dump before an update
that carries one:

```powershell
docker compose exec -T postgres pg_dump -U cdip cdip > backup.sql
```

## Their backups

Two things, and the drawings are the smaller one:

```powershell
docker compose exec -T postgres pg_dump -U cdip cdip > C:\backups\cdip.sql
docker run --rm -v cdip_minio_data:/d -v C:/backups:/b alpine tar czf /b/storage.tar.gz -C /d .
```

Postgres is what matters: PDFs can be re-uploaded and vectors recomputed, but
the portion structure, the approved summaries and the chat history cannot.

By default the drawings live in a named volume (`cdip_minio_data`), which needs
no path decision and has no permission surprises. If they would rather see the
files, set `STORAGE_DATA_DIR=/c/cdip-data` in the `.env` — it works, and it is
slower, because that path crosses the Windows filesystem bridge.

## What to hand over, and what not to

**Hand over:** `docker-compose.yml`, the `.env` you filled in, the image
tarballs (if `--save`), and a read-only registry token (if not).

**Do not hand over:** the token that can push to your registry, your `.env.local`
from your own machine, or the repository.
